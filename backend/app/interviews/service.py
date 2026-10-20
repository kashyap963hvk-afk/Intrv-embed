"""Interview templates and sessions: create, prepare in the background (CV → plan → reference packs), invite the
candidate, consent, integrity events, report and the human decision."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import composer
from app.agents.runtime import AgentDeps
from app.audit.service import ClientMeta, record
from app.auth.principal import Principal
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.core.jobs import JobDispatcher
from app.core.logging import get_logger
from app.cv import service as cv
from app.db.deps import after_commit
from app.db.models import (
    Document,
    IntegrityEvent,
    InterviewSession,
    InterviewTemplate,
    InterviewTurn,
    Organization,
    QuestionItem,
    QuestionSet,
    Role,
    RolePack,
    Scorecard,
    SessionStatus,
    Speaker,
    TemplateVisibility,
)
from app.interviews import integrity
from app.interviews.engine import FINALIZE_JOB, LLM_FAILURES, SessionRef, owner_session
from app.interviews.persona import PERSONA_VARIABLES, lint_persona, render_persona
from app.interviews.planning import context_packs, plan_interview, practical_availability, with_practical
from app.interviews.schemas import InterviewPlan, InterviewSettings
from app.knowledge.acl import readable, space_permissions
from app.knowledge.events import publish
from app.knowledge.storage import ObjectStorage
from app.orgs.policy import org_policy
from app.practical.service import submissions as practical_submissions
from app.rolepacks.schema import RolePackSpec

log = get_logger("app.interviews")

PREPARE_JOB = "interviews.prepare"
CONSENT_VERSION = "2026-10"
INVITE_HOURS = 168


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- templates -----------------------------------------------------------------------------------------------


def require_template_edit(principal: Principal, template: InterviewTemplate) -> None:
    if template.owner_user_id is None and principal.role != Role.OWNER:
        raise ForbiddenError("Only owners can change organization templates.")


async def get_template(db: AsyncSession, template_id: uuid.UUID) -> InterviewTemplate:
    template = await db.get(InterviewTemplate, template_id)  # RLS: mine or org-shared
    if template is None:
        raise NotFoundError("Template not found.")
    return template


async def list_templates(db: AsyncSession) -> list[InterviewTemplate]:
    return list(await db.scalars(select(InterviewTemplate).order_by(InterviewTemplate.name)))


async def save_template(
    db: AsyncSession,
    principal: Principal,
    template: InterviewTemplate | None,
    fields: dict[str, Any],
    meta: ClientMeta,
) -> InterviewTemplate:
    """Create or patch a template. On a patch an explicit null clears the role pack or resets the variant to the
    pack's default; other null fields are left as they are."""
    if "settings" in fields and fields["settings"] is not None:
        fields["settings"] = InterviewSettings.model_validate(fields["settings"]).model_dump(mode="json")
    if "role_pack_id" in fields and fields["role_pack_id"] is None:
        fields["role_variant"] = None
    pack_id = fields.get("role_pack_id", template.role_pack_id if template else None)
    if pack_id is not None and ("role_pack_id" in fields or "role_variant" in fields):
        pack = await db.get(RolePack, pack_id)
        if pack is None:
            raise NotFoundError("Role pack not found.")
        try:
            RolePackSpec.model_validate(pack.spec).role(
                fields.get("role_variant", template.role_variant if template else None)
            )
        except ValueError as exc:
            raise AppError(str(exc)) from exc
    shared = fields.pop("shared", None)
    if shared and principal.role != Role.OWNER:
        raise ForbiddenError("Only owners can share templates with the organization.")
    if template is None:
        template = InterviewTemplate(
            id=uuid.uuid4(),
            org_id=principal.org_id,
            owner_user_id=None if shared else principal.user_id,
            visibility=TemplateVisibility.ORG if shared else TemplateVisibility.PRIVATE,
            created_by=principal.user_id,
            name=fields.pop("name"),
            settings=fields.pop("settings", None) or InterviewSettings().model_dump(mode="json"),
        )
        db.add(template)
        action = "template.create"
    else:
        require_template_edit(principal, template)
        action = "template.update"
        if shared is not None and shared != (template.owner_user_id is None):
            if principal.role != Role.OWNER:
                raise ForbiddenError("Only owners can change who a template is shared with.")
            template.owner_user_id = None if shared else principal.user_id
            template.visibility = TemplateVisibility.ORG if shared else TemplateVisibility.PRIVATE
    for key, value in fields.items():
        if value is not None or key in {"role_pack_id", "role_variant"}:
            setattr(template, key, value)
    await db.flush()
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action=action,
        meta=meta,
        target_type="interview_template",
        target_id=template.id,
        detail={"name": template.name, "shared": template.owner_user_id is None},
    )
    return template


async def preview_prompt(db: AsyncSession, principal: Principal, template: InterviewTemplate) -> dict[str, Any]:
    """The composed interviewer prompt for this template with sample values. L0 is shown read-only."""
    org = await db.get(Organization, principal.org_id)
    pack = await db.get(RolePack, template.role_pack_id) if template.role_pack_id else None
    spec = RolePackSpec.model_validate(pack.spec) if pack else None
    variant = spec.role(template.role_variant) if spec else None
    org_title = org.name if org else "the organization"
    values = {
        "role_title": variant.name if variant else "Embedded Software Engineer",
        "candidate_first_name": "Sam",
        "company": org_title,
        "seniority": "MID",
    }
    style = render_persona(template.persona_prompt or (spec.persona_defaults.style if spec else ""), values)
    policy = org_policy(org) if org else None
    layers = [
        {
            "layer": "L0",
            "name": "Platform rules",
            "editable": False,
            "text": composer.render(composer.L0, trusted={"org_name": org_title}),
        },
        {
            "layer": "L1",
            "name": "Organization policy",
            "editable": False,
            "text": composer.render(
                "org_policy",
                trusted={
                    "interview_tone": (policy.interview_tone if policy else "") or "Professional, friendly and direct.",
                    "forbidden_topics": ", ".join(policy.forbidden_topics if policy else []) or "(none)",
                    "language": template.settings.get("language", "English"),
                },
                untrusted={"company_facts": (policy.company_facts if policy else "") or "(none provided)"},
            ),
        },
        {
            "layer": "L2",
            "name": "Persona (this template)",
            "editable": True,
            "text": composer.render(
                "interviewer_persona",
                trusted={
                    "interviewer_name": template.settings.get("interviewer_name")
                    or (spec.persona_defaults.interviewer_name if spec else "Alex"),
                    "role_title": values["role_title"],
                    "company": org_title,
                    "recent_acknowledgements": "(none)",
                },
                untrusted={"persona_style": style},
            ),
        },
        {
            "layer": "L3",
            "name": "Session context",
            "editable": False,
            "text": "Filled in per interview: blind CV summary, plan, current topic and reference snippets.",
        },
        {
            "layer": "L4",
            "name": "Turn directive",
            "editable": False,
            "text": "Filled in per turn by the controller and the deterministic policy.",
        },
    ]
    return {"layers": layers, "warnings": lint_persona(template.persona_prompt), "variables": list(PERSONA_VARIABLES)}


# --- sessions ------------------------------------------------------------------------------------------------


async def publish_status(valkey: Redis | None, session: InterviewSession) -> None:
    if valkey is None:
        return
    await publish(
        valkey,
        session.org_id,
        {
            "type": "interview.status",
            "user_id": str(session.owner_user_id),
            "session_id": str(session.id),
            "status": session.status.value,
            "progress": session.progress,
        },
    )


async def create_session(
    db: AsyncSession,
    jobs: JobDispatcher,
    valkey: Redis | None,
    principal: Principal,
    body: dict[str, Any],
) -> InterviewSession:
    template = await get_template(db, body["template_id"]) if body.get("template_id") else None
    pack_id = body.get("role_pack_id") or (template.role_pack_id if template else None)
    pack = await db.get(RolePack, pack_id) if pack_id else None
    if pack is None or pack.archived:
        raise NotFoundError("Choose a role pack (or a template that has one).")
    spec = RolePackSpec.model_validate(pack.spec)
    try:
        variant = spec.role(body.get("role_variant") or (template.role_variant if template else None))
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    cv_doc = await db.get(Document, body["cv_document_id"]) if body.get("cv_document_id") else None
    if body.get("cv_document_id") and cv_doc is None:
        raise NotFoundError("CV not found.")
    if body.get("jd_document_id") and await db.get(Document, body["jd_document_id"]) is None:
        raise NotFoundError("Job description not found.")
    if body.get("question_set_id") and await db.get(QuestionSet, body["question_set_id"]) is None:
        raise NotFoundError("Question set not found.")
    org = await db.get(Organization, principal.org_id)
    policy = org_policy(org) if org else None
    merged: dict[str, Any] = {"integrity_mode": policy.integrity_mode if policy else "STANDARD"}
    merged |= template.settings if template else {}
    merged |= {k: v for k, v in (body.get("settings") or {}).items() if v is not None}
    settings = InterviewSettings.model_validate(merged).model_dump(mode="json")
    settings["seniority"] = body.get("seniority")
    settings["interviewer_name"] = merged.get("interviewer_name")
    allowed = readable(await space_permissions(db, valkey, org_id=principal.org_id, user_id=principal.user_id))
    requested = body.get("space_ids")
    spaces = sorted(str(s) for s in (allowed & set(requested) if requested is not None else allowed))
    values = {
        "role_title": variant.name,
        "candidate_first_name": (body.get("candidate_label") or "").split(" ")[0],
        "company": org.name if org else "",
        "seniority": body.get("seniority") or "",
    }
    session = InterviewSession(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        owner_user_id=principal.user_id,
        title=(body.get("title") or f"{body.get('candidate_label') or 'Candidate'} · {variant.name}")[:200],
        candidate_label=(body.get("candidate_label") or "")[:120],
        template_id=template.id if template else None,
        role_pack_id=pack.id,
        role_pack_version=pack.version,
        role_variant=variant.id,
        cv_document_id=cv_doc.id if cv_doc else None,
        jd_document_id=body.get("jd_document_id"),
        question_set_id=body.get("question_set_id"),
        mode=body.get("mode") or "TEXT",
        status=SessionStatus.PREPARING,
        progress="Queued",
        settings=settings,
        persona={
            "interviewer_name": settings.get("interviewer_name") or spec.persona_defaults.interviewer_name,
            "style": render_persona(
                (template.persona_prompt if template else "") or spec.persona_defaults.style, values
            ),
            "role_title": variant.name,
        },
        pack_snapshot=spec.model_dump(mode="json"),
        acl_snapshot=spaces,
        state={"phase": "pending", "seq": 0},
    )
    db.add(session)
    await db.flush()
    _enqueue_prepare(db, jobs, session)
    return session


def _enqueue_prepare(db: AsyncSession, jobs: JobDispatcher, session: InterviewSession) -> None:
    args = {"org_id": str(session.org_id), "user_id": str(session.owner_user_id), "session_id": str(session.id)}

    async def enqueue() -> None:
        await jobs.submit(PREPARE_JOB, args)

    after_commit(db, enqueue)


async def retry_prepare(db: AsyncSession, jobs: JobDispatcher, session: InterviewSession) -> InterviewSession:
    if session.status not in {SessionStatus.FAILED, SessionStatus.PLANNED}:
        raise ConflictError("Only an interview that failed to prepare, or has not started yet, can be re-planned.")
    session.status, session.error, session.progress = SessionStatus.PREPARING, None, "Queued"
    session.state = {"phase": "pending", "seq": 0}
    await db.flush()
    _enqueue_prepare(db, jobs, session)
    return session


async def _prepare(deps: AgentDeps, db: AsyncSession, session: InterviewSession) -> None:
    async def progress(message: str) -> None:
        session.progress = message[:200]
        await db.commit()
        await publish_status(deps.valkey, session)

    org_id, user_id = session.org_id, session.owner_user_id
    spec = RolePackSpec.model_validate(session.pack_snapshot)
    variant = spec.role(session.role_variant)
    profile = None
    gap: dict[str, Any] | None = None
    if session.cv_document_id is not None:
        await progress("Reading the CV")
        profile = await cv.analyze_cv(deps, db, org_id=org_id, user_id=user_id, document_id=session.cv_document_id)
        session.candidate_profile_id = profile.id
        if session.jd_document_id is not None:
            await progress("Comparing the CV with the job description")
            matrix = await cv.match_jd(
                deps,
                db,
                org_id=org_id,
                user_id=user_id,
                profile=profile,
                jd_document_id=session.jd_document_id,
                competency_ids=[c.id for c in spec.competencies],
            )
            gap = matrix.model_dump()
    items: list[QuestionItem] = []
    if session.question_set_id is not None:
        items = list(
            await db.scalars(
                select(QuestionItem).where(QuestionItem.set_id == session.question_set_id).order_by(QuestionItem.ord)
            )
        )
    await progress("Planning the interview")
    settings = InterviewSettings.model_validate(session.settings)
    seniority = session.settings.get("seniority") or (profile.structured.get("seniority_estimate") if profile else None)
    seniority = seniority or "MID"
    index = cv.evidence_index(profile.structured) if profile else {}
    plan, structure, source = await plan_interview(
        deps,
        db,
        org_id=org_id,
        user_id=user_id,
        spec=spec,
        role=variant,
        settings=settings,
        seniority=seniority,
        profile=profile.structured if profile else None,
        evidence_index=index,
        gap=gap,
        question_items=items,
    )
    available, practical, notes = await practical_availability(
        deps,
        db,
        org_id=org_id,
        user_id=user_id,
        spec=spec,
        settings=settings,
        space_ids=[uuid.UUID(s) for s in session.acl_snapshot],
        code_runner=bool(deps.settings.piston_url),
    )
    if settings.practical_tasks:
        plan = with_practical(plan, spec, variant, settings, structure, available)
    await progress("Gathering reference material")
    packs = await context_packs(
        deps,
        db,
        org_id=org_id,
        user_id=user_id,
        plan=plan,
        space_ids=[uuid.UUID(s) for s in session.acl_snapshot],
    )
    session.settings = {**session.settings, "seniority": seniority}
    session.plan = {
        "topics": [t.model_dump(mode="json") for t in plan.topics],
        "structure": structure,
        "source": source,
        "context_packs": packs,
        "candidate": {"summary": profile.blind_summary if profile else "", "evidence": index},
        "practical": practical,
        "notes": notes,
        "gap_matrix": gap,
        "prompt_versions": composer.versions("planner", "cv_extract", "jd_match"),
    }
    session.state = {"phase": "pending", "seq": 0}
    session.status, session.error = SessionStatus.PLANNED, None
    await progress("Ready" if source == "planner" else "Ready (built from the role pack; the planner was unavailable)")


async def run_prepare(
    deps: AgentDeps, session_factory: async_sessionmaker[AsyncSession], args: Mapping[str, str]
) -> None:
    """Background job. Any failure marks the session FAILED with a readable reason (retry from the UI)."""
    org_id, owner, session_id = uuid.UUID(args["org_id"]), uuid.UUID(args["user_id"]), uuid.UUID(args["session_id"])
    async with owner_session(session_factory, org_id, owner) as db:
        session = await db.get(InterviewSession, session_id)
        if session is None or session.status != SessionStatus.PREPARING:
            return
        try:
            await _prepare(deps, db, session)
        except Exception as exc:
            await db.rollback()
            failed = await db.get(InterviewSession, session_id)
            if failed is None:
                return
            friendly = exc.detail if isinstance(exc, AppError) and exc.detail else str(exc)
            if not isinstance(exc, (AppError, *LLM_FAILURES)):
                log.exception("interviews.prepare_crashed", session_id=str(session_id))
                friendly = f"Preparation failed unexpectedly ({type(exc).__name__}). Try again."
            failed.status, failed.error, failed.progress = SessionStatus.FAILED, friendly[:2000], "Failed"
            await db.commit()
            await publish_status(deps.valkey, failed)


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> InterviewSession:
    session = await db.get(InterviewSession, session_id)  # RLS: creator only
    if session is None:
        raise NotFoundError("Interview not found.")
    return session


async def list_sessions(db: AsyncSession) -> list[InterviewSession]:
    return list(await db.scalars(select(InterviewSession).order_by(InterviewSession.created_at.desc()).limit(200)))


def update_plan(session: InterviewSession, plan: InterviewPlan) -> None:
    if session.status != SessionStatus.PLANNED:
        raise ConflictError("The plan can only be edited before the interview starts.")
    topics = []
    for index, topic in enumerate(plan.topics, start=1):
        topic.id = topic.id or f"t{index}"
        topics.append(topic.model_dump(mode="json"))
    if len({t["id"] for t in topics}) != len(topics):
        raise AppError("Topic ids must be unique.")
    session.plan = {**session.plan, "topics": topics}


def create_invite(session: InterviewSession, settings: Settings, hours: int) -> tuple[str, datetime]:
    if session.status not in {SessionStatus.PLANNED, SessionStatus.LIVE}:
        raise ConflictError("Invite the candidate once the interview is planned.")
    token = secrets.token_urlsafe(32)
    session.invite_token_hash = hash_token(token)
    session.invite_expires_at = datetime.now(UTC) + timedelta(hours=hours)
    return f"{settings.public_app_url.rstrip('/')}/join/{token}", session.invite_expires_at


async def resolve_invite(db: AsyncSession, token: str) -> SessionRef:
    if not token or len(token) > 200:  # noqa: PLR2004
        raise NotFoundError("This interview link is not valid or has expired.")
    row = (
        await db.execute(
            sql_text("SELECT id, org_id, owner_user_id FROM app_session_by_invite(:h)"), {"h": hash_token(token)}
        )
    ).first()
    if row is None:
        raise NotFoundError("This interview link is not valid or has expired.")
    return SessionRef(id=row[0], org_id=row[1], owner_user_id=row[2])


def monitoring_notice(session: InterviewSession) -> list[str]:
    """Exactly what is monitored, shown on the consent screen (ADR 0005)."""
    mode = session.settings.get("integrity_mode", "STANDARD")
    camera = session.settings.get("camera_monitoring", "off")
    items = ["Your typed answers and the interviewer's questions are recorded as a transcript."]
    if mode != "OFF":
        items += [
            "Whether this window loses focus or leaves full screen, and copy/paste activity (sizes and timing only, "
            "never the clipboard contents).",
            "Answer timing and typing speed.",
        ]
    if camera != "off":
        items.append(
            "With your permission, your camera is analysed on your own device only, to count faces and estimate "
            "whether you are looking at the screen. No video or face data leaves your device."
        )
    items.append("No emotion, facial expression, identity, age, gender or ethnicity analysis is ever performed.")
    items.append("Signals are shown to a human reviewer; they never decide the outcome on their own.")
    return items


async def join_info(factory: async_sessionmaker[AsyncSession], ref: SessionRef) -> dict[str, Any]:
    async with owner_session(factory, ref.org_id, ref.owner_user_id) as db:
        session = await db.get(InterviewSession, ref.id)
        org = await db.get(Organization, ref.org_id)
        if session is None or org is None:
            raise NotFoundError("This interview link is not valid or has expired.")
        policy = org_policy(org)
        return {
            "session_id": str(session.id),
            "title": session.persona.get("role_title", ""),
            "organization": org.name,
            "interviewer_name": session.persona.get("interviewer_name", "Alex"),
            "duration_minutes": session.settings.get("duration_minutes", 45),
            "mode": session.mode,
            "status": session.status.value,
            "integrity_mode": session.settings.get("integrity_mode", "STANDARD"),
            "camera_monitoring": session.settings.get("camera_monitoring", "off"),
            "monitoring": monitoring_notice(session),
            "retention_days": policy.retention_days,
            "contact": policy.consent_contact or "the recruiter who sent you this link",
            "consent_version": CONSENT_VERSION,
            "consent_given": bool((session.consent or {}).get("accepted")),
            "human_alternative": "You may ask for a human interviewer instead; contact "
            + (policy.consent_contact or "the recruiter")
            + ".",
        }


async def give_consent(
    factory: async_sessionmaker[AsyncSession], ref: SessionRef, *, camera: bool, meta: ClientMeta
) -> dict[str, Any]:
    async with owner_session(factory, ref.org_id, ref.owner_user_id) as db:
        session = await db.get(InterviewSession, ref.id)
        if session is None:
            raise NotFoundError("This interview link is not valid or has expired.")
        if session.status not in {SessionStatus.PLANNED, SessionStatus.LIVE}:
            raise ConflictError("This interview is not open.")
        org = await db.get(Organization, ref.org_id)
        contact = org_policy(org).consent_contact if org else ""
        if session.settings.get("camera_monitoring") == "required" and not camera:
            raise AppError(
                "This interview requires the camera check. If you cannot use a camera, contact "
                f"{contact or 'the recruiter'} to arrange an alternative."
            )
        session.consent = {
            "accepted": True,
            "at": datetime.now(UTC).isoformat(),
            "ip": meta.ip,
            "user_agent": (meta.user_agent or "")[:300],
            "policy_version": CONSENT_VERSION,
            "camera": bool(camera) and session.settings.get("camera_monitoring", "off") != "off",
            "monitoring": monitoring_notice(session),
        }
        await record(
            db,
            org_id=session.org_id,
            actor_id=None,
            action="interview.consent",
            meta=meta,
            target_type="interview_session",
            target_id=session.id,
            detail={"policy_version": CONSENT_VERSION, "camera": session.consent["camera"]},
        )
        await db.commit()
        return session.consent


async def candidate_events(
    factory: async_sessionmaker[AsyncSession], ref: SessionRef, events: list[integrity.BrowserEvent]
) -> int:
    async with owner_session(factory, ref.org_id, ref.owner_user_id) as db:
        session = await db.get(InterviewSession, ref.id)
        if session is None or session.status != SessionStatus.LIVE:
            return 0
        if not (session.consent or {}).get("camera"):
            events = [e for e in events if not e.kind.startswith("camera_")]
        stored = await integrity.record_browser_events(db, session, events, int(session.state.get("seq", 0)) or None)
        await db.commit()
        return stored


# --- report --------------------------------------------------------------------------------------------------


async def transcript(db: AsyncSession, session_id: uuid.UUID) -> list[InterviewTurn]:
    return list(
        await db.scalars(
            select(InterviewTurn).where(InterviewTurn.session_id == session_id).order_by(InterviewTurn.seq)
        )
    )


async def report(db: AsyncSession, session: InterviewSession, storage: ObjectStorage | None = None) -> dict[str, Any]:
    scorecard = await db.scalar(select(Scorecard).where(Scorecard.session_id == session.id))
    events = list(
        await db.scalars(
            select(IntegrityEvent).where(IntegrityEvent.session_id == session.id).order_by(IntegrityEvent.created_at)
        )
    )
    turns = await transcript(db, session.id)
    return {
        "session_id": str(session.id),
        "title": session.title,
        "candidate_label": session.candidate_label,
        "role": session.persona.get("role_title", ""),
        "status": session.status.value,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "scorecard": None
        if scorecard is None
        else {
            "status": scorecard.status.value,
            "error": scorecard.error,
            "data": scorecard.data,
            "variance_flags": scorecard.variance_flags,
            "recommendation": scorecard.recommendation,
            "human_decision": scorecard.human_decision,
            "decision_notes": scorecard.decision_notes,
            "reviewed_at": scorecard.reviewed_at.isoformat() if scorecard.reviewed_at else None,
        },
        "integrity": {
            **integrity.summarise(events),
            "events": [
                {
                    "at": e.created_at.isoformat(),
                    "kind": e.kind,
                    "severity": e.severity,
                    "source": e.source,
                    "turn_seq": e.turn_seq,
                    "duration_ms": e.duration_ms,
                    "detail": e.detail,
                }
                for e in events
            ],
        },
        "transcript": [
            {
                "seq": t.seq,
                "speaker": t.speaker.value,
                "text": t.text,
                "section": t.section,
                "topic_id": t.topic_id,
                "at": t.created_at.isoformat(),
                "latency": t.latency if t.speaker == Speaker.INTERVIEWER else None,
            }
            for t in turns
        ],
        "plan": {"topics": session.plan.get("topics", []), "source": session.plan.get("source")},
        "practical": await practical_submissions(db, storage, session.id) if storage else [],
        "snapshots": [
            await storage.presign_get(e.detail["key"], filename="snapshot.jpg", expires_s=600)
            for e in events
            if storage and e.kind == "snapshot" and isinstance(e.detail.get("key"), str)
        ],
    }


async def record_decision(
    db: AsyncSession, principal: Principal, session: InterviewSession, decision: str, notes: str, meta: ClientMeta
) -> Scorecard:
    scorecard = await db.scalar(select(Scorecard).where(Scorecard.session_id == session.id))
    if scorecard is None:
        raise ConflictError("The report is not ready yet.")
    scorecard.human_decision, scorecard.decision_notes = decision, notes
    scorecard.reviewer_id, scorecard.reviewed_at = principal.user_id, datetime.now(UTC)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="interview.decision",
        meta=meta,
        target_type="interview_session",
        target_id=session.id,
        detail={"decision": decision, "ai_recommendation": scorecard.recommendation},
    )
    await db.flush()
    return scorecard


async def scorecards_for(db: AsyncSession, session_ids: list[uuid.UUID]) -> dict[uuid.UUID, Scorecard]:
    if not session_ids:
        return {}
    rows = await db.scalars(select(Scorecard).where(Scorecard.session_id.in_(session_ids)))
    return {card.session_id: card for card in rows}


def enqueue_finalize(db: AsyncSession, jobs: JobDispatcher, session: InterviewSession) -> None:
    args = {"org_id": str(session.org_id), "user_id": str(session.owner_user_id), "session_id": str(session.id)}

    async def enqueue() -> None:
        await jobs.submit(FINALIZE_JOB, args)

    after_commit(db, enqueue)
