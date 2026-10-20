"""Self-healing scan (Celery beat, every few minutes).

Background work can be lost when a worker is killed or restarted mid-task (deploys, OOM, a host reboot). The scan
finds what was left behind and repairs it inside the owner's tenant context:

- interview preparation that never finished → dispatched again (after 3 attempts: FAILED with a retry hint);
- live interviews nobody has touched for longer than their duration + 1 hour → ended, graded with what exists;
- finished interviews without a report, or a report stuck in PENDING → grading dispatched again;
- question sets stuck in GENERATING → FAILED with a retry hint (the owner retries with one click);
- documents stuck in processing for 2 hours → processing dispatched again (after 3 attempts: FAILED).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.jobs import JobDispatcher
from app.core.logging import get_logger
from app.db.models import (
    DocStatus,
    Document,
    InterviewSession,
    QuestionSet,
    Scorecard,
    ScorecardStatus,
    SessionStatus,
    SetStatus,
)
from app.interviews.engine import FINALIZE_JOB, EngineError, InterviewEngine, SessionRef, owner_session
from app.interviews.service import PREPARE_JOB
from app.knowledge.dispatch import IngestDispatcher

log = get_logger("app.healing")

BATCH = 50
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class Stuck:
    kind: str
    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID


async def find_stuck(factory: async_sessionmaker[AsyncSession], limit: int = BATCH) -> list[Stuck]:
    async with factory() as db:
        rows = await db.execute(text("SELECT kind, id, org_id, user_id FROM app_stuck_work(:n)"), {"n": limit})
        return [Stuck(r.kind, r.id, r.org_id, r.user_id) for r in rows]


def _bump(meta: dict[str, object] | None) -> tuple[dict[str, object], int]:
    current = dict(meta or {})
    raw = current.get("heal_attempts", 0)
    attempts = (raw if isinstance(raw, int) else 0) + 1
    current["heal_attempts"] = attempts
    return current, attempts


def _job_args(item: Stuck) -> dict[str, str]:
    return {"org_id": str(item.org_id), "user_id": str(item.user_id), "session_id": str(item.id)}


async def _heal_prepare(db: AsyncSession, item: Stuck, jobs: JobDispatcher) -> str:
    session = await db.get(InterviewSession, item.id)
    if session is None or session.status != SessionStatus.PREPARING:
        return "skipped"
    state, attempts = _bump(session.state)
    if attempts > MAX_ATTEMPTS:
        session.status, session.progress = SessionStatus.FAILED, "Failed"
        session.error = "Preparation did not finish after several attempts. Use 'Prepare again' to retry."
        await db.commit()
        return "failed"
    session.state = state
    await db.commit()
    await jobs.submit(PREPARE_JOB, _job_args(item))
    return "redispatched"


async def _heal_finalize(db: AsyncSession, item: Stuck, jobs: JobDispatcher) -> str:
    card = await db.scalar(select(Scorecard).where(Scorecard.session_id == item.id))
    if card is not None:
        data, attempts = _bump(card.data)
        if attempts > MAX_ATTEMPTS:
            card.status = ScorecardStatus.FAILED
            card.error = "Grading did not finish after several attempts. Use 'Grade again' to retry."
            await db.commit()
            return "failed"
        card.data = data
        await db.commit()
    await jobs.submit(FINALIZE_JOB, _job_args(item))
    return "redispatched"


async def _heal_question_set(db: AsyncSession, item: Stuck) -> str:
    qset = await db.get(QuestionSet, item.id)
    if qset is None or qset.status != SetStatus.GENERATING:
        return "skipped"
    qset.status, qset.progress = SetStatus.FAILED, "Failed"
    qset.error = "Generation did not finish (a worker restarted). Use Retry to generate the set again."
    await db.commit()
    return "failed"


async def _heal_document(db: AsyncSession, item: Stuck, ingest: IngestDispatcher) -> str:
    doc = await db.get(Document, item.id)
    if doc is None or doc.status not in {DocStatus.PENDING, DocStatus.PARSING, DocStatus.EMBEDDING}:
        return "skipped"
    meta, attempts = _bump(doc.parse_meta)
    if attempts > MAX_ATTEMPTS:
        doc.status = DocStatus.FAILED
        doc.error = "Processing did not finish after several attempts. Use Reindex to try again."
        await db.commit()
        return "failed"
    doc.parse_meta = meta
    await db.commit()
    await ingest.dispatch(org_id=item.org_id, user_id=item.user_id, document_id=item.id)
    return "redispatched"


async def heal(
    factory: async_sessionmaker[AsyncSession],
    *,
    jobs: JobDispatcher,
    ingest: IngestDispatcher,
    engine: InterviewEngine,
) -> dict[str, int]:
    """One scan. Every item is isolated: one failure never stops the others."""
    counts: dict[str, int] = {}
    for item in await find_stuck(factory):
        outcome = "error"
        try:
            if item.kind == "abandoned":
                ended = await engine.end(SessionRef(item.id, item.org_id, item.user_id), reason="abandoned")
                outcome = "closed" if ended else "skipped"
            else:
                async with owner_session(factory, item.org_id, item.user_id) as db:
                    if item.kind == "prepare":
                        outcome = await _heal_prepare(db, item, jobs)
                    elif item.kind == "finalize":
                        outcome = await _heal_finalize(db, item, jobs)
                    elif item.kind == "question_set":
                        outcome = await _heal_question_set(db, item)
                    elif item.kind == "document":
                        outcome = await _heal_document(db, item, ingest)
        except EngineError as exc:
            outcome = "skipped"
            log.info("healing.skipped", kind=item.kind, id=str(item.id), reason=exc.code)
        except Exception:
            log.exception("healing.item_failed", kind=item.kind, id=str(item.id))
        key = f"{item.kind}.{outcome}"
        counts[key] = counts.get(key, 0) + 1
    await engine.drain()
    if counts:
        log.info("healing.done", **counts)
    return counts
