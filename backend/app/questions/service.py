"""Question sets: create (generation runs in the background with live progress), read, edit, regenerate one item."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import composer
from app.agents.runtime import AgentDeps, llm, org_name, search, snippets
from app.agents.schemas import GeneratedQuestions
from app.auth.principal import Principal
from app.core.errors import AppError, NotFoundError
from app.core.jobs import JobDispatcher
from app.core.logging import get_logger
from app.cv import service as cv
from app.db.deps import after_commit
from app.db.models import (
    AgentRole,
    CandidateProfile,
    DocType,
    Document,
    QuestionItem,
    QuestionSet,
    RolePack,
    SetStatus,
    User,
)
from app.db.tenant import TenantContext, set_tenant_context
from app.knowledge.events import publish
from app.llm.types import CallCtx, LLMUnavailableError, StructuredOutputError
from app.questions.graph import QUESTION_GRAPH, GenContext
from app.rolepacks.schema import RolePackSpec

log = get_logger("app.questions")

JOB = "questions.generate"


async def publish_progress(valkey: Redis | None, qset: QuestionSet, progress: str) -> None:
    if valkey is None:
        return
    await publish(
        valkey,
        qset.org_id,
        {
            "type": "question_set.status",
            "user_id": str(qset.owner_user_id),  # delivered only to the set's owner
            "set_id": str(qset.id),
            "status": qset.status.value,
            "progress": progress,
        },
    )


async def create_set(
    db: AsyncSession,
    jobs: JobDispatcher,
    principal: Principal,
    *,
    title: str | None,
    cv_document_id: uuid.UUID,
    jd_document_id: uuid.UUID | None,
    role_pack_id: uuid.UUID,
    role_variant: str | None,
    params: dict[str, Any],
) -> QuestionSet:
    cv_doc = await db.get(Document, cv_document_id)  # RLS: must be readable
    if cv_doc is None:
        raise NotFoundError("CV not found.")
    if jd_document_id is not None and await db.get(Document, jd_document_id) is None:
        raise NotFoundError("Job description not found.")
    pack = await db.get(RolePack, role_pack_id)
    if pack is None or pack.archived:
        raise NotFoundError("Role pack not found.")
    spec = RolePackSpec.model_validate(pack.spec)
    try:
        variant = spec.role(role_variant)
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    qset = QuestionSet(
        id=uuid.uuid4(),
        org_id=principal.org_id,
        owner_user_id=principal.user_id,
        title=(title or f"{cv_doc.title} · {variant.name}")[:200],
        cv_document_id=cv_document_id,
        jd_document_id=jd_document_id,
        role_pack_id=pack.id,
        role_pack_version=pack.version,
        role_variant=variant.id,
        params=params,
        status=SetStatus.GENERATING,
        progress="Queued",
    )
    db.add(qset)
    await db.flush()
    args = {"org_id": str(principal.org_id), "user_id": str(principal.user_id), "set_id": str(qset.id)}

    async def enqueue() -> None:
        await jobs.submit(JOB, args)

    after_commit(db, enqueue)
    return qset


async def _generate(
    deps: AgentDeps,
    session_factory: async_sessionmaker[AsyncSession],
    db: AsyncSession,
    qset: QuestionSet,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
) -> None:
    async def progress(message: str) -> None:
        qset.progress = message[:200]
        await publish_progress(deps.valkey, qset, message)

    if qset.cv_document_id is None:
        raise AppError("The CV was deleted.")
    await progress("Reading the CV")
    profile = await cv.analyze_cv(deps, db, org_id=org_id, user_id=user_id, document_id=qset.cv_document_id)
    qset.candidate_profile_id = profile.id
    await db.commit()
    pack = await db.get(RolePack, qset.role_pack_id) if qset.role_pack_id else None
    if pack is None:
        raise AppError("The role pack was deleted.")
    spec = RolePackSpec.model_validate(pack.spec)
    gap: dict[str, Any] | None = None
    if qset.jd_document_id is not None:
        await progress("Comparing the CV with the job description")
        matrix = await cv.match_jd(
            deps,
            db,
            org_id=org_id,
            user_id=user_id,
            profile=profile,
            jd_document_id=qset.jd_document_id,
            competency_ids=[c.id for c in spec.competencies],
        )
        gap = matrix.model_dump()
    params = qset.params
    state = await QUESTION_GRAPH.ainvoke(
        {
            "spec": spec.model_dump(mode="json"),
            "variant_id": qset.role_variant,
            "seniority": params.get("seniority") or profile.structured.get("seniority_estimate") or "MID",
            "count": int(params.get("count", 20)),
            "difficulty": params.get("difficulty"),
            "mix": params.get("mix"),
            "space_ids": params.get("space_ids"),
            "profile": profile.structured,
            "index": cv.evidence_index(profile.structured),
            "gap": gap,
        },
        context=GenContext(deps, session_factory, org_id, user_id, role, progress),
    )
    await db.execute(delete(QuestionItem).where(QuestionItem.set_id == qset.id))
    for position, item in enumerate(state["final"]):
        db.add(QuestionItem(org_id=org_id, owner_user_id=user_id, set_id=qset.id, ord=position, data=item))
    qset.analysis = {
        "gap_matrix": gap,
        "plan": state["slots"],
        "critic": state.get("critic_summary", {}),
        "notes": state.get("notes", []),
        "prompt_versions": composer.versions("cv_extract", "jd_match", "question_gen", "question_critic"),
    }
    qset.status, qset.error = SetStatus.READY, None
    await progress(f"{len(state['final'])} questions ready")
    await db.commit()


async def run_generation(
    deps: AgentDeps, session_factory: async_sessionmaker[AsyncSession], args: Mapping[str, str]
) -> None:
    """Background job: CV analysis (cached) → JD gap matrix → graph → items. Failures mark the set FAILED."""
    org_id, user_id, set_id = uuid.UUID(args["org_id"]), uuid.UUID(args["user_id"]), uuid.UUID(args["set_id"])
    async with session_factory() as db:
        await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id))
        user = await db.get(User, user_id)
        if user is None:
            return
        role = user.role.value
        await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id, role=role))
        qset = await db.get(QuestionSet, set_id)
        if qset is None or qset.status != SetStatus.GENERATING:
            return
        try:
            await _generate(deps, session_factory, db, qset, org_id=org_id, user_id=user_id, role=role)
        except Exception as exc:
            await db.rollback()
            failed = await db.get(QuestionSet, set_id)
            if failed is None:
                return
            friendly = exc.detail if isinstance(exc, AppError) and exc.detail else str(exc)
            if not isinstance(exc, AppError | LLMUnavailableError | StructuredOutputError):
                log.exception("questions.generation_crashed", set_id=str(set_id))
                friendly = f"Generation failed unexpectedly ({type(exc).__name__}). Try again."
            failed.status, failed.error, failed.progress = SetStatus.FAILED, friendly[:2000], "Failed"
            await db.commit()
            await publish_progress(deps.valkey, failed, "Failed")


async def get_set(db: AsyncSession, set_id: uuid.UUID) -> QuestionSet:
    qset = await db.get(QuestionSet, set_id)  # RLS: owner only
    if qset is None:
        raise NotFoundError("Question set not found.")
    return qset


async def items_of(db: AsyncSession, set_id: uuid.UUID) -> list[QuestionItem]:
    return list(await db.scalars(select(QuestionItem).where(QuestionItem.set_id == set_id).order_by(QuestionItem.ord)))


async def item_counts(db: AsyncSession, set_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(QuestionItem.set_id, func.count()).where(QuestionItem.set_id.in_(set_ids)).group_by(QuestionItem.set_id)
    )
    return {set_id: int(n) for set_id, n in rows.all()}


async def get_item(db: AsyncSession, qset: QuestionSet, item_id: uuid.UUID) -> QuestionItem:
    item = await db.get(QuestionItem, item_id)
    if item is None or item.set_id != qset.id:
        raise NotFoundError("Question not found.")
    return item


async def reorder(db: AsyncSession, qset: QuestionSet, ordered_ids: list[uuid.UUID]) -> None:
    items = {i.id: i for i in await items_of(db, qset.id)}
    if set(ordered_ids) != set(items):
        raise AppError("Send every question id of the set exactly once.")
    for position, item_id in enumerate(ordered_ids):
        items[item_id].ord = position


async def regenerate_item(
    deps: AgentDeps, db: AsyncSession, principal: Principal, qset: QuestionSet, item: QuestionItem, hint: str | None
) -> QuestionItem:
    """One fresh question for the same competency, category and difficulty, avoiding the rest of the set."""
    if item.pinned:
        raise AppError("Unpin the question before regenerating it.")
    profile = await db.get(CandidateProfile, qset.candidate_profile_id) if qset.candidate_profile_id else None
    pack = await db.get(RolePack, qset.role_pack_id) if qset.role_pack_id else None
    if profile is None or pack is None:
        raise AppError("The CV profile or role pack of this set is no longer available.")
    spec = RolePackSpec.model_validate(pack.spec)
    data = item.data
    competency = spec.competency(data["competency"])
    others = [i.data["text"] for i in await items_of(db, qset.id) if i.id != item.id]
    references = await search(
        deps,
        db,
        org_id=principal.org_id,
        user_id=principal.user_id,
        query=f"{competency.name if competency else data['competency']} {data['text']}",
        k=4,
    )
    index = cv.evidence_index(profile.structured)
    messages = composer.task_messages(
        "question_gen",
        org_name=await org_name(db, principal.org_id),
        trusted={
            "count": 1,
            "role_variant_name": spec.role(qset.role_variant).name,
            "seniority": qset.params.get("seniority") or profile.structured.get("seniority_estimate") or "MID",
            "slots": f"1. competency={data['competency']} category={data['category']} difficulty={data['difficulty']}",
        },
        untrusted={
            "cv_evidence_index": index,
            "competency_specs": {"competency": data["competency"], "avoid_repeating": others, "user_hint": hint or ""},
            "jd_gap_matrix": (qset.analysis or {}).get("gap_matrix") or "(none)",
            "reference_snippets": snippets(references),
        },
    )
    call = CallCtx(org_id=principal.org_id, user_id=principal.user_id, role=AgentRole.QUESTION_GEN, contains_pii=True)
    result = await llm(deps, db).complete_json(call, messages, GeneratedQuestions, temperature=0.7)
    if not result.questions:
        raise AppError("The model returned no question; try again.")
    fresh = result.questions[0]
    fresh.competency, fresh.category = data["competency"], data["category"]
    fresh.evidence_refs = [r for r in fresh.evidence_refs if r in index]
    fresh.citations = [c for c in fresh.citations if c in {str(r.document_id) for r in references}]
    item.data = {**fresh.model_dump(), "source": "generated", "regenerated": True}
    await db.flush()
    return item


async def cv_documents(db: AsyncSession) -> list[Document]:
    return list(
        await db.scalars(select(Document).where(Document.doc_type == DocType.CV).order_by(Document.created_at.desc()))
    )
