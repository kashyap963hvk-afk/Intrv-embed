"""Practical rounds during a live interview (ARCHITECTURE §14.3 and §14.4): the current task, running code against
its tests, submitting code with an explanation (reviewed by the Code Reviewer agent), whiteboard diagrams read by
a vision model, and STRICT-mode camera snapshots for the human reviewer.

Everything is reached with the candidate's invite token and runs in the interview creator's tenant context.
"""

from __future__ import annotations

import base64
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import composer
from app.agents.runtime import AgentDeps, llm, org_name
from app.core.logging import get_logger
from app.db.models import AgentRole, IntegrityEvent, InterviewSession, PracticalSubmission, SessionStatus
from app.interviews.engine import LLM_FAILURES, EngineError, SessionRef, owner_session
from app.interviews.schemas import InterviewPlan, PlanTopic
from app.knowledge.storage import ObjectStorage
from app.llm.types import CallCtx
from app.practical.piston import PistonClient, PistonUnavailableError, RunReport
from app.practical.review import review_submission
from app.privacy.service import session_prefix
from app.rolepacks.schema import PracticalTask, RolePackSpec, TaskKind

log = get_logger("app.practical")

CODE_KINDS = {TaskKind.DEBUG_SNIPPET, TaskKind.WRITE_CODE}
MAX_CODE_CHARS = 40_000
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 300 * 1024
VISION_UNAVAILABLE = "Diagram review needs a vision model; the interviewer will discuss your diagram with you instead."


@dataclass(frozen=True, slots=True)
class PracticalDeps:
    agent: AgentDeps
    session_factory: async_sessionmaker[AsyncSession]
    storage: ObjectStorage
    piston: PistonClient | None


@dataclass(frozen=True, slots=True)
class Current:
    session: InterviewSession
    topic: PlanTopic
    task: PracticalTask


def _current(session: InterviewSession) -> Current:
    if session.status != SessionStatus.LIVE:
        raise EngineError("not_live", "The interview is not running.")
    plan = InterviewPlan.model_validate({"topics": session.plan.get("topics", [])})
    index = int(session.state.get("topic_index", 0))
    topic = plan.topics[index] if index < len(plan.topics) and session.state.get("phase") == "topic" else None
    if topic is None or topic.section != "PRACTICAL" or not topic.task_id:
        raise EngineError("no_task", "There is no practical task right now.")
    task = next(
        (t for t in RolePackSpec.model_validate(session.pack_snapshot).practical_tasks if t.id == topic.task_id), None
    )
    if task is None:
        raise EngineError("no_task", "There is no practical task right now.")
    return Current(session, topic, task)


def task_view(current: Current) -> dict[str, Any]:
    task = current.task
    context = current.session.plan.get("practical", {}).get(task.id, {})
    return {
        "task_id": task.id,
        "kind": task.kind.value,
        "language": task.language,
        "prompt": task.prompt,
        "starter_code": task.starter_code,
        "test_count": len(task.tests),
        "can_run": task.kind in CODE_KINDS,
        "reference": context.get("reference", []),  # datasheet excerpt for DATASHEET_READING
    }


async def current_task(deps: PracticalDeps, ref: SessionRef) -> dict[str, Any]:
    async with owner_session(deps.session_factory, ref.org_id, ref.owner_user_id) as db:
        session = await db.get(InterviewSession, ref.id)
        if session is None:
            raise EngineError("not_found", "This interview does not exist.")
        return task_view(_current(session))


async def _check(deps: PracticalDeps, task: PracticalTask, code: str) -> RunReport:
    if deps.piston is None:
        raise EngineError("unavailable", "Code can't be run on this server; explain your solution instead.")
    try:
        return await deps.piston.check(task, code[:MAX_CODE_CHARS])
    except PistonUnavailableError as exc:
        raise EngineError("unavailable", str(exc)) from exc


async def run_code(deps: PracticalDeps, ref: SessionRef, code: str) -> dict[str, Any]:
    async with owner_session(deps.session_factory, ref.org_id, ref.owner_user_id) as db:
        session = await db.get(InterviewSession, ref.id)
        if session is None:
            raise EngineError("not_found", "This interview does not exist.")
        current = _current(session)
    if current.task.kind not in CODE_KINDS:
        raise EngineError("invalid", "This task has no code to run.")
    report = await _check(deps, current.task, code)
    return report.as_json()


def _ctx(session: InterviewSession, role: AgentRole) -> CallCtx:
    return CallCtx(
        org_id=session.org_id, user_id=session.owner_user_id, role=role, session_id=session.id, contains_pii=True
    )


async def submit(deps: PracticalDeps, ref: SessionRef, code: str, explanation: str) -> dict[str, Any]:
    """Store the submission with test results and the reviewer's verified findings; the candidate then talks it
    through in the interview (the frontend sends the explanation as an answer with this submission id)."""
    async with owner_session(deps.session_factory, ref.org_id, ref.owner_user_id) as db:
        session = await db.get(InterviewSession, ref.id)
        if session is None:
            raise EngineError("not_found", "This interview does not exist.")
        current = _current(session)
        report: RunReport | None = None
        if current.task.kind in CODE_KINDS and deps.piston is not None and code.strip():
            try:
                report = await deps.piston.check(current.task, code[:MAX_CODE_CHARS])
            except PistonUnavailableError:
                report = None
        review: dict[str, Any] | None = None
        try:
            found = await review_submission(
                deps.agent,
                db,
                _ctx(session, AgentRole.GRADER),
                org_name=await org_name(db, session.org_id),
                task=current.task,
                code=code[:MAX_CODE_CHARS],
                explanation=explanation[:6000],
                report=report,
            )
            review = found.model_dump()
        except LLM_FAILURES as exc:
            log.warning("practical.review_deferred", session_id=str(session.id), error=str(exc))
        row = PracticalSubmission(
            org_id=session.org_id,
            owner_user_id=session.owner_user_id,
            session_id=session.id,
            topic_id=current.topic.id,
            task_id=current.task.id,
            kind=current.task.kind.value,
            code=code[:MAX_CODE_CHARS],
            explanation=explanation[:6000],
            results=report.as_json() if report else {},
            review=review,
        )
        db.add(row)
        await db.commit()
        return {"submission_id": str(row.id), "results": row.results, "reviewed": review is not None}


async def whiteboard(deps: PracticalDeps, ref: SessionRef, image: bytes, content_type: str) -> dict[str, Any]:
    if len(image) > MAX_IMAGE_BYTES or content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise EngineError("invalid", "Upload a PNG, JPEG or WebP image of up to 3 MB.")
    async with owner_session(deps.session_factory, ref.org_id, ref.owner_user_id) as db:
        session = await db.get(InterviewSession, ref.id)
        if session is None:
            raise EngineError("not_found", "This interview does not exist.")
        current = _current(session)
        key = f"{session_prefix(session.org_id, session.id)}whiteboard/{uuid.uuid4().hex}"
        await deps.storage.put(key, io.BytesIO(image), content_type)
        description = await _describe(deps, db, session, current.task, image, content_type)
        row = PracticalSubmission(
            org_id=session.org_id,
            owner_user_id=session.owner_user_id,
            session_id=session.id,
            topic_id=current.topic.id,
            task_id=current.task.id,
            kind="WHITEBOARD",
            attachment_key=key,
            review={"description": description} if description else None,
        )
        db.add(row)
        await db.commit()
        return {
            "submission_id": str(row.id),
            "vision_available": description is not None,
            "message": None if description else VISION_UNAVAILABLE,
        }


async def _describe(
    deps: PracticalDeps, db: AsyncSession, session: InterviewSession, task: PracticalTask, image: bytes, mime: str
) -> str | None:
    """The Whiteboard Reader (vision role). No vision model → None, and the interview simply continues."""
    system = composer.task_messages(
        "whiteboard_reader", org_name=await org_name(db, session.org_id), untrusted={"task_prompt": task.prompt}
    )[0]
    user = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe the attached diagram."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(image).decode()}"}},
        ],
    }
    ctx = _ctx(session, AgentRole.VISION)
    service = llm(deps.agent, db)
    try:
        chain = [p for p in await service.resolve_chain(ctx) if p.capabilities.get("vision")]  # probed models only
        if not chain:
            return None
        completion = await deps.agent.gateway.complete(
            chain, [system, user], ctx, on_usage=service.record_usage, temperature=0.0
        )
    except LLM_FAILURES as exc:
        log.info("practical.vision_unavailable", session_id=str(session.id), error=str(exc))
        return None
    return completion.text.strip()[:4000] or None


async def snapshot(deps: PracticalDeps, ref: SessionRef, image: bytes) -> bool:
    """STRICT mode with camera consent only: a low-resolution frame for the human reviewer (never analysed)."""
    if len(image) > MAX_SNAPSHOT_BYTES or not image.startswith(b"\xff\xd8"):
        raise EngineError("invalid", "Snapshots are small JPEG images.")
    async with owner_session(deps.session_factory, ref.org_id, ref.owner_user_id) as db:
        session = await db.get(InterviewSession, ref.id)
        if (
            session is None
            or session.status != SessionStatus.LIVE
            or session.settings.get("integrity_mode") != "STRICT"
            or not (session.consent or {}).get("camera")
        ):
            return False
        key = f"{session_prefix(session.org_id, session.id)}snapshots/{datetime.now(UTC):%Y%m%dT%H%M%S%f}.jpg"
        await deps.storage.put(key, io.BytesIO(image), "image/jpeg")
        db.add(
            IntegrityEvent(
                org_id=session.org_id,
                owner_user_id=session.owner_user_id,
                session_id=session.id,
                kind="snapshot",
                severity="info",
                source="CAMERA",
                turn_seq=int(session.state.get("seq", 0)) or None,
                detail={"key": key},
            )
        )
        await db.commit()
        return True


async def submissions(db: AsyncSession, storage: ObjectStorage, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await db.scalars(
        select(PracticalSubmission)
        .where(PracticalSubmission.session_id == session_id)
        .order_by(PracticalSubmission.created_at)
    )
    out = []
    for row in rows:
        image = (
            await storage.presign_get(row.attachment_key, filename="diagram", expires_s=600)
            if row.attachment_key
            else None
        )
        out.append(
            {
                "id": str(row.id),
                "topic_id": row.topic_id,
                "task_id": row.task_id,
                "kind": row.kind,
                "code": row.code,
                "explanation": row.explanation,
                "results": row.results,
                "review": row.review,
                "image_url": image,
                "at": row.created_at.isoformat(),
            }
        )
    return out


def grader_lines(rows: list[PracticalSubmission]) -> list[str]:
    """What graders see of practical work: verified facts, never integrity data."""
    lines = []
    for row in rows:
        results = row.results or {}
        tests = f"tests {results.get('passed', 0)}/{results.get('total', 0)}" if results.get("total") else "no tests"
        review = row.review or {}
        found = ", ".join(str(f.get("bug")) for f in review.get("found", [])) or "none"
        lines.append(
            f"[practical submission for {row.task_id}] {tests}; compiler warnings: {len(results.get('warnings', []))}; "
            f"known bugs found: {found}; missed: {review.get('missed', [])}; new bugs: {review.get('new_bugs', [])}; "
            f"reviewer: {review.get('summary') or review.get('description') or 'not reviewed'}"
        )
    return lines
