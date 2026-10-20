"""Interviews API: templates, sessions (create → prepare → invite → live → report), the candidate's join and
consent endpoints, and the interview WebSocket."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from app.audit.service import ClientMeta
from app.auth.cookies import ACCESS_COOKIE
from app.auth.deps import ActiveMember, SettingsDep, csrf_protect
from app.core.errors import AppError, ConflictError
from app.core.logging import get_logger
from app.core.ratelimit import enforce_rate_limit
from app.core.resources import Resources
from app.core.security import decode_access_token
from app.db.deps import DbSession
from app.db.models import InterviewSession, InterviewTemplate, RolePack, SessionStatus, UserStatus
from app.db.models import User as UserModel
from app.interviews import export, media, service
from app.interviews.engine import Emit, EngineError, SessionRef, owner_session, public_state
from app.interviews.integrity import BrowserEvent
from app.interviews.persona import lint_persona
from app.interviews.schemas import InterviewPlan, InterviewSettings
from app.knowledge.deps import ResourcesDep
from app.practical import service as practical
from app.practical.service import PracticalDeps
from app.rolepacks.schema import RolePackSpec

log = get_logger("app.interviews")

templates_router = APIRouter(prefix="/api/templates", tags=["interviews"], dependencies=[Depends(csrf_protect)])
router = APIRouter(prefix="/api/interviews", tags=["interviews"], dependencies=[Depends(csrf_protect)])
join_router = APIRouter(prefix="/api/join", tags=["candidate"], dependencies=[Depends(csrf_protect)])
ws_router = APIRouter()

Seniority = Literal["JUNIOR", "MID", "SENIOR", "STAFF"]
MAX_FRAME_CHARS = 20_000


def _meta(request: Request) -> ClientMeta:
    return ClientMeta.from_request(request)


Meta = Annotated[ClientMeta, Depends(_meta)]


# --- templates -----------------------------------------------------------------------------------------------


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role_pack_id: uuid.UUID | None = None
    role_variant: str | None = Field(default=None, max_length=64)
    persona_prompt: str = Field(default="", max_length=6000)
    settings: InterviewSettings = Field(default_factory=InterviewSettings)
    interviewer_name: str | None = Field(default=None, max_length=60)
    shared: bool = Field(default=False, description="Share with the whole organization (owners only)")


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role_pack_id: uuid.UUID | None = None
    role_variant: str | None = Field(default=None, max_length=64)
    persona_prompt: str | None = Field(default=None, max_length=6000)
    settings: InterviewSettings | None = None
    interviewer_name: str | None = Field(default=None, max_length=60)


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    shared: bool
    mine: bool
    role_pack_id: uuid.UUID | None
    role_variant: str | None
    persona_prompt: str
    settings: dict[str, Any]
    warnings: list[str]
    updated_at: datetime


class PromptLayer(BaseModel):
    layer: str
    name: str
    editable: bool
    text: str


class PromptPreview(BaseModel):
    layers: list[PromptLayer]
    warnings: list[str]
    variables: list[str]


class SimulateIn(BaseModel):
    answers: list[str] = Field(
        default_factory=lambda: [
            "In my last project I owned that part: I chose the approach, implemented it and tested it myself.",
            "We just used the standard approach for it and it worked fine.",
            "I'm not sure, I haven't worked with that.",
        ],
        min_length=1,
        max_length=5,
    )


class SimulatedTurn(BaseModel):
    candidate: str
    answer_quality: str
    action: str
    interviewer: str


class SimulateOut(BaseModel):
    opening_question: str
    turns: list[SimulatedTurn]


def _template_out(template: InterviewTemplate, user_id: uuid.UUID) -> TemplateOut:
    return TemplateOut(
        id=template.id,
        name=template.name,
        shared=template.owner_user_id is None,
        mine=user_id in (template.owner_user_id, template.created_by),
        role_pack_id=template.role_pack_id,
        role_variant=template.role_variant,
        persona_prompt=template.persona_prompt,
        settings=template.settings,
        warnings=lint_persona(template.persona_prompt),
        updated_at=template.updated_at,
    )


def _template_fields(body: TemplateIn | TemplatePatch) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    name = fields.pop("interviewer_name", None)
    if "settings" in fields or name is not None:
        settings = dict(fields.get("settings") or {})
        if name is not None:
            settings["interviewer_name"] = name
        fields["settings"] = settings
    return fields


@templates_router.get("", response_model=list[TemplateOut])
async def list_templates(principal: ActiveMember, db: DbSession) -> list[TemplateOut]:
    return [_template_out(t, principal.user_id) for t in await service.list_templates(db)]


@templates_router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(body: TemplateIn, principal: ActiveMember, db: DbSession, meta: Meta) -> TemplateOut:
    template = await service.save_template(db, principal, None, _template_fields(body), meta)
    return _template_out(template, principal.user_id)


@templates_router.get("/{template_id}", response_model=TemplateOut)
async def get_template(template_id: uuid.UUID, principal: ActiveMember, db: DbSession) -> TemplateOut:
    return _template_out(await service.get_template(db, template_id), principal.user_id)


@templates_router.patch("/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID, body: TemplatePatch, principal: ActiveMember, db: DbSession, meta: Meta
) -> TemplateOut:
    template = await service.get_template(db, template_id)
    fields = _template_fields(body)
    if "settings" in fields:
        fields["settings"] = {**template.settings, **fields["settings"]}
    template = await service.save_template(db, principal, template, fields, meta)
    return _template_out(template, principal.user_id)


@templates_router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: uuid.UUID, principal: ActiveMember, db: DbSession) -> None:
    template = await service.get_template(db, template_id)
    service.require_template_edit(principal, template)
    await db.delete(template)


@templates_router.post("/{template_id}/preview-prompt", response_model=PromptPreview)
async def preview_prompt(template_id: uuid.UUID, principal: ActiveMember, db: DbSession) -> PromptPreview:
    preview = await service.preview_prompt(db, principal, await service.get_template(db, template_id))
    return PromptPreview.model_validate(preview)


@templates_router.post(
    "/{template_id}/simulate", response_model=SimulateOut, summary="Run a few turns with a scripted candidate"
)
async def simulate_template(
    template_id: uuid.UUID, body: SimulateIn, principal: ActiveMember, db: DbSession, resources: ResourcesDep
) -> SimulateOut:
    template = await service.get_template(db, template_id)
    pack = await db.get(RolePack, template.role_pack_id) if template.role_pack_id else None
    if pack is None:
        raise AppError("Choose a role pack for this template first.")
    spec = RolePackSpec.model_validate(pack.spec)
    result = await resources.interviews.simulate(
        db,
        org_id=principal.org_id,
        user_id=principal.user_id,
        spec=spec,
        variant=spec.role(template.role_variant),
        persona_prompt=template.persona_prompt,
        settings=template.settings,
        answers=body.answers,
    )
    return SimulateOut.model_validate(result)


# --- sessions ------------------------------------------------------------------------------------------------


class CreateInterviewIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    candidate_label: str | None = Field(default=None, max_length=120, description="How the interviewer greets them")
    template_id: uuid.UUID | None = None
    role_pack_id: uuid.UUID | None = None
    role_variant: str | None = Field(default=None, max_length=64)
    cv_document_id: uuid.UUID | None = None
    jd_document_id: uuid.UUID | None = None
    question_set_id: uuid.UUID | None = None
    mode: Literal["TEXT", "VOICE"] = Field(default="TEXT", description="Candidates can switch to text at any time")
    seniority: Seniority | None = None
    space_ids: list[uuid.UUID] | None = Field(default=None, description="Reference material for the interviewer")
    settings: dict[str, Any] | None = Field(default=None, description="Overrides of the template's settings")


class InterviewOut(BaseModel):
    id: uuid.UUID
    title: str
    candidate_label: str
    status: SessionStatus
    progress: str
    error: str | None
    mode: str
    role: str
    role_pack_id: uuid.UUID | None
    template_id: uuid.UUID | None
    cv_document_id: uuid.UUID | None
    question_set_id: uuid.UUID | None
    consent_given: bool
    invite_expires_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    scorecard_status: str | None = None
    recommendation: str | None = None
    human_decision: str | None = None


class InterviewDetail(InterviewOut):
    settings: dict[str, Any]
    persona: dict[str, Any]
    plan: dict[str, Any]
    live: dict[str, Any]


class PlanIn(BaseModel):
    topics: InterviewPlan


class InterviewInviteIn(BaseModel):
    expires_in_hours: int = Field(default=service.INVITE_HOURS, ge=1, le=24 * 30)


class InterviewInviteOut(BaseModel):
    url: str
    expires_at: datetime


class DecisionIn(BaseModel):
    decision: Literal["advance", "hold", "reject"]
    notes: str = Field(default="", max_length=4000)


def _out(session: InterviewSession, scorecard: Any = None) -> InterviewOut:
    return InterviewOut(
        id=session.id,
        title=session.title,
        candidate_label=session.candidate_label,
        status=session.status,
        progress=session.progress,
        error=session.error,
        mode=session.mode,
        role=session.persona.get("role_title", ""),
        role_pack_id=session.role_pack_id,
        template_id=session.template_id,
        cv_document_id=session.cv_document_id,
        question_set_id=session.question_set_id,
        consent_given=bool((session.consent or {}).get("accepted")),
        invite_expires_at=session.invite_expires_at,
        started_at=session.started_at,
        ended_at=session.ended_at,
        created_at=session.created_at,
        scorecard_status=scorecard.status.value if scorecard else None,
        recommendation=scorecard.recommendation if scorecard else None,
        human_decision=scorecard.human_decision if scorecard else None,
    )


def _detail(session: InterviewSession) -> InterviewDetail:
    plan = {k: v for k, v in session.plan.items() if k != "context_packs"}
    plan["context_documents"] = sorted(
        {p["title"] for pack in session.plan.get("context_packs", {}).values() for p in pack}
    )
    return InterviewDetail(
        **_out(session).model_dump(),
        settings=session.settings,
        persona=session.persona,
        plan=plan,
        live=public_state(session),
    )


@router.get("", response_model=list[InterviewOut])
async def list_interviews(_member: ActiveMember, db: DbSession) -> list[InterviewOut]:
    sessions = await service.list_sessions(db)
    cards = await service.scorecards_for(db, [s.id for s in sessions])
    return [_out(s, cards.get(s.id)) for s in sessions]


@router.post("", response_model=InterviewDetail, status_code=status.HTTP_202_ACCEPTED)
async def create_interview(
    body: CreateInterviewIn, principal: ActiveMember, db: DbSession, resources: ResourcesDep
) -> InterviewDetail:
    if body.settings:
        try:
            InterviewSettings.model_validate(body.settings)
        except ValidationError as exc:
            raise AppError("Invalid interview settings.", extra={"problems": [e["msg"] for e in exc.errors()]}) from exc
    session = await service.create_session(
        db, resources.jobs, resources.valkey, principal, body.model_dump(exclude_unset=True)
    )
    return _detail(session)


@router.get("/{session_id}", response_model=InterviewDetail)
async def get_interview(session_id: uuid.UUID, _member: ActiveMember, db: DbSession) -> InterviewDetail:
    return _detail(await service.get_session(db, session_id))


@router.patch("/{session_id}/plan", response_model=InterviewDetail)
async def update_plan(session_id: uuid.UUID, body: PlanIn, _member: ActiveMember, db: DbSession) -> InterviewDetail:
    session = await service.get_session(db, session_id)
    service.update_plan(session, body.topics)
    await db.flush()
    return _detail(session)


@router.post("/{session_id}/prepare", response_model=InterviewDetail, status_code=status.HTTP_202_ACCEPTED)
async def prepare_again(
    session_id: uuid.UUID, _member: ActiveMember, db: DbSession, resources: ResourcesDep
) -> InterviewDetail:
    session = await service.retry_prepare(db, resources.jobs, await service.get_session(db, session_id))
    return _detail(session)


@router.post(
    "/{session_id}/invite", response_model=InterviewInviteOut, summary="Create (or rotate) the candidate's link"
)
async def invite(
    session_id: uuid.UUID, body: InterviewInviteIn, _member: ActiveMember, db: DbSession, settings: SettingsDep
) -> InterviewInviteOut:
    url, expires = service.create_invite(await service.get_session(db, session_id), settings, body.expires_in_hours)
    return InterviewInviteOut(url=url, expires_at=expires)


@router.post("/{session_id}/end", response_model=InterviewDetail)
async def end_interview(
    session_id: uuid.UUID, principal: ActiveMember, db: DbSession, resources: ResourcesDep
) -> InterviewDetail:
    session = await service.get_session(db, session_id)
    if session.status == SessionStatus.LIVE:
        ref = SessionRef(session.id, session.org_id, session.owner_user_id)
        await db.commit()
        try:
            await resources.interviews.end(ref, reason="ended by the interviewer")
        except EngineError as exc:
            raise ConflictError(exc.message) from exc
        await db.refresh(session)
    elif session.status in {SessionStatus.PLANNED, SessionStatus.PREPARING, SessionStatus.FAILED}:
        session.status = SessionStatus.ABORTED
        session.invite_token_hash = None
        await db.flush()
    return _detail(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_interview(session_id: uuid.UUID, _member: ActiveMember, db: DbSession) -> None:
    session = await service.get_session(db, session_id)
    if session.status == SessionStatus.LIVE:
        raise ConflictError("End the interview before deleting it.")
    await db.delete(session)


@router.get("/{session_id}/report")
async def get_report(
    session_id: uuid.UUID, _member: ActiveMember, db: DbSession, resources: ResourcesDep
) -> dict[str, Any]:
    return await service.report(db, await service.get_session(db, session_id), resources.storage)


@router.post("/{session_id}/regrade", status_code=status.HTTP_202_ACCEPTED, summary="Grade the interview again")
async def regrade(session_id: uuid.UUID, _member: ActiveMember, db: DbSession, resources: ResourcesDep) -> None:
    session = await service.get_session(db, session_id)
    if session.status != SessionStatus.COMPLETED:
        raise ConflictError("Only a finished interview can be graded.")
    service.enqueue_finalize(db, resources.jobs, session)


@router.post("/{session_id}/decision")
async def record_decision(
    session_id: uuid.UUID, body: DecisionIn, principal: ActiveMember, db: DbSession, meta: Meta
) -> dict[str, Any]:
    session = await service.get_session(db, session_id)
    card = await service.record_decision(db, principal, session, body.decision, body.notes, meta)
    return {"human_decision": card.human_decision, "reviewed_at": card.reviewed_at}


@router.get("/{session_id}/export", summary="Download the report (JSON or PDF)")
async def export_report(
    session_id: uuid.UUID,
    _member: ActiveMember,
    db: DbSession,
    resources: ResourcesDep,
    format: Annotated[Literal["json", "pdf"], Query()] = "pdf",
) -> Response:
    report = await service.report(db, await service.get_session(db, session_id), resources.storage)
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", report["title"]).strip("-")[:80] or "interview"
    if format == "json":
        body, media = json.dumps(report, indent=1, default=str).encode(), "application/json"
    else:
        body, media = export.to_pdf(report), "application/pdf"
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}.{format}"'}
    )


# --- candidate (invite link) ---------------------------------------------------------------------------------


class ConsentIn(BaseModel):
    accepted: Literal[True]
    camera: bool = False


class IntegrityIn(BaseModel):
    events: list[BrowserEvent] = Field(max_length=50)


@join_router.get("/{token}", summary="What the candidate sees before joining")
async def join_info(token: str, db: DbSession, resources: ResourcesDep) -> dict[str, Any]:
    ref = await service.resolve_invite(db, token)
    return await service.join_info(resources.session_factory, ref)


@join_router.post("/{token}/consent")
async def consent(token: str, body: ConsentIn, db: DbSession, resources: ResourcesDep, meta: Meta) -> dict[str, Any]:
    ref = await service.resolve_invite(db, token)
    given = await service.give_consent(resources.session_factory, ref, camera=body.camera, meta=meta)
    return {"accepted": True, "camera": given["camera"], "at": given["at"]}


@join_router.get("/{token}/voice", summary="Voice room: the voice service's offer URL and ICE servers")
async def voice(token: str, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    ref = await service.resolve_invite(db, token)
    return media.voice_info(settings, ref.id)


class CodeIn(BaseModel):
    code: str = Field(max_length=40_000)


class SubmitIn(CodeIn):
    explanation: str = Field(default="", max_length=6000)


def _practical(resources: Resources) -> PracticalDeps:
    return PracticalDeps(resources.agent_deps, resources.session_factory, resources.storage, resources.piston)


def _as_problem(exc: EngineError) -> AppError:
    return ConflictError(exc.message) if exc.code in {"no_task", "not_live", "unavailable"} else AppError(exc.message)


@join_router.get("/{token}/practical", summary="The current practical task (code, diagram or datasheet)")
async def practical_task(token: str, db: DbSession, resources: ResourcesDep) -> dict[str, Any]:
    ref = await service.resolve_invite(db, token)
    try:
        return await practical.current_task(_practical(resources), ref)
    except EngineError as exc:
        raise _as_problem(exc) from exc


@join_router.post("/{token}/practical/run", summary="Compile and run against the task's tests")
async def practical_run(token: str, body: CodeIn, db: DbSession, resources: ResourcesDep) -> dict[str, Any]:
    ref = await service.resolve_invite(db, token)
    await enforce_rate_limit(resources.valkey, f"practical-run:{ref.id}", limit=60, window_s=3600)
    try:
        return await practical.run_code(_practical(resources), ref, body.code)
    except EngineError as exc:
        raise _as_problem(exc) from exc


@join_router.post("/{token}/practical/submit", summary="Submit code and explanation for review")
async def practical_submit(token: str, body: SubmitIn, db: DbSession, resources: ResourcesDep) -> dict[str, Any]:
    ref = await service.resolve_invite(db, token)
    await enforce_rate_limit(resources.valkey, f"practical-submit:{ref.id}", limit=10, window_s=3600)
    try:
        return await practical.submit(_practical(resources), ref, body.code, body.explanation)
    except EngineError as exc:
        raise _as_problem(exc) from exc


@join_router.post("/{token}/whiteboard", summary="Submit a diagram (PNG/JPEG/WebP, up to 3 MB)")
async def whiteboard(token: str, file: UploadFile, db: DbSession, resources: ResourcesDep) -> dict[str, Any]:
    ref = await service.resolve_invite(db, token)
    await enforce_rate_limit(resources.valkey, f"whiteboard:{ref.id}", limit=10, window_s=3600)
    image = await file.read(practical.MAX_IMAGE_BYTES + 1)
    try:
        return await practical.whiteboard(_practical(resources), ref, image, file.content_type or "")
    except EngineError as exc:
        raise _as_problem(exc) from exc


@join_router.post("/{token}/snapshot", summary="STRICT mode: a low-resolution camera frame for the reviewer")
async def snapshot(token: str, file: UploadFile, db: DbSession, resources: ResourcesDep) -> dict[str, bool]:
    ref = await service.resolve_invite(db, token)
    await enforce_rate_limit(resources.valkey, f"snapshot:{ref.id}", limit=120, window_s=3600)
    try:
        stored = await practical.snapshot(_practical(resources), ref, await file.read(practical.MAX_SNAPSHOT_BYTES + 1))
    except EngineError as exc:
        raise _as_problem(exc) from exc
    return {"stored": stored}


@join_router.post("/{token}/integrity", summary="Browser integrity signals (batched)")
async def integrity_events(token: str, body: IntegrityIn, db: DbSession, resources: ResourcesDep) -> dict[str, int]:
    ref = await service.resolve_invite(db, token)
    return {"stored": await service.candidate_events(resources.session_factory, ref, body.events)}


# --- WebSocket -----------------------------------------------------------------------------------------------


async def _authenticate(websocket: WebSocket, session_id: uuid.UUID, token: str | None) -> tuple[SessionRef, bool]:
    """Candidate: invite token. Interviewer preview: the access cookie of the session's creator."""
    resources: Resources = websocket.app.state.resources
    if token:
        async with resources.session_factory() as db:
            ref = await service.resolve_invite(db, token)
        if ref.id != session_id:
            raise EngineError("forbidden", "This link is for a different interview.")
        return ref, False
    origin = websocket.headers.get("origin")
    settings = websocket.app.state.settings
    if origin not in settings.cors_allowed_origins:
        raise EngineError("forbidden", "Origin not allowed.")
    cookie = websocket.cookies.get(ACCESS_COOKIE)
    claims = decode_access_token(settings, cookie) if cookie else None
    if claims is None:
        raise EngineError("unauthorized", "Sign in again.")
    async with owner_session(resources.session_factory, claims.org_id, claims.user_id) as db:
        user = await db.get(UserModel, claims.user_id)
        session = await db.get(InterviewSession, session_id)  # RLS: only the creator sees it
        if user is None or user.status != UserStatus.ACTIVE or session is None:
            raise EngineError("forbidden", "You cannot open this interview.")
        return SessionRef(session.id, session.org_id, session.owner_user_id), True


@ws_router.websocket("/ws/interview/{session_id}")
async def interview_socket(websocket: WebSocket, session_id: uuid.UUID, token: str | None = None) -> None:
    resources: Resources = websocket.app.state.resources
    engine = resources.interviews
    try:
        ref, as_owner = await _authenticate(websocket, session_id, token)
    except (EngineError, AppError) as exc:
        await websocket.close(code=4403, reason=getattr(exc, "message", None) or str(exc)[:120])
        return
    await websocket.accept()

    async def emit(frame: dict[str, Any]) -> None:
        await websocket.send_json(frame)

    async def safely(action: Awaitable[None]) -> None:
        try:
            await action
        except EngineError as exc:
            await emit({"type": "error", "code": exc.code, "message": exc.message})
        except AppError as exc:
            await emit({"type": "error", "code": "invalid", "message": exc.detail or str(exc)})

    await safely(engine.hello(ref, emit))
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > MAX_FRAME_CHARS:
                await emit({"type": "error", "code": "too_large", "message": "That message is too long."})
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                message = None
            if not isinstance(message, dict):
                await emit({"type": "error", "code": "invalid", "message": "Messages must be JSON objects."})
                continue
            await safely(_handle(resources, ref, message, emit, as_owner=as_owner))
    except WebSocketDisconnect:
        engine.on_disconnect(ref)


async def _handle(
    resources: Resources, ref: SessionRef, message: dict[str, Any], emit: Emit, *, as_owner: bool
) -> None:
    engine = resources.interviews
    kind = message.get("type")
    if kind == "ping":
        await emit({"type": "pong"})
    elif kind == "start":
        await engine.start(ref, emit, as_owner=as_owner)
    elif kind == "answer" and isinstance(message.get("text"), str) and message["text"].strip():
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
        client_id = message.get("client_id") if isinstance(message.get("client_id"), str) else None
        await engine.answer(ref, message["text"], meta or {}, emit, client_id=client_id)
    elif kind == "integrity" and isinstance(message.get("events"), list):
        try:
            events = [BrowserEvent.model_validate(e) for e in message["events"][:50]]
        except ValidationError:
            await emit({"type": "error", "code": "invalid", "message": "Unknown integrity event."})
            return
        await service.candidate_events(resources.session_factory, ref, events)
    elif kind == "end":
        await engine.end(ref, reason="ended by the candidate", emit=emit)
    elif kind == "mode" and isinstance(message.get("mode"), str):
        await engine.set_mode(ref, message["mode"], emit)
    elif kind == "spoken" and isinstance(message.get("seq"), int) and isinstance(message.get("text"), str):
        await engine.record_spoken(ref, message["seq"], message["text"], interrupted=bool(message.get("interrupted")))
    elif kind == "nudge":
        await engine.nudge(ref, emit)
    else:
        await emit({"type": "error", "code": "invalid", "message": "Unknown message."})
