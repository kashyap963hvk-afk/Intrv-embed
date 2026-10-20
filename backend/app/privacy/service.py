"""Candidate privacy (ARCHITECTURE §15): find a subject's data, export it, erase it everywhere, and purge data
past the organization's retention period.

Erasure covers Postgres rows (sessions with their turns, grades, scorecards and integrity events; question sets;
CV documents with their chunks and candidate profiles), the S3 objects under each document's and session's
prefix, and the Qdrant points of each document. Every row is deleted inside its owner's tenant context; the
owner-level search uses `app_candidate_index()`, which only returns ids and labels of the caller's organization.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime import AgentDeps
from app.core.logging import get_logger
from app.cv import service as cv
from app.db.models import (
    CandidateProfile,
    DocStatus,
    Document,
    IntegrityEvent,
    InterviewSession,
    InterviewTurn,
    QuestionItem,
    QuestionSet,
    Scorecard,
)
from app.interviews.engine import EngineError, owner_session
from app.knowledge.storage import ObjectStorage, document_prefix
from app.rag.store import VectorStore

log = get_logger("app.privacy")

PURGE_BATCH = 500
ERASE_ORDER = {"session": 0, "question_set": 1, "cv": 2, "profile": 3}


@dataclass(frozen=True, slots=True)
class Item:
    kind: str  # session | profile | cv | question_set
    id: uuid.UUID
    owner_user_id: uuid.UUID
    label: str = ""
    document_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None

    def out(self) -> dict[str, str | None]:
        return {"kind": self.kind, "id": str(self.id), "owner_user_id": str(self.owner_user_id), "label": self.label}


@dataclass(frozen=True, slots=True)
class PrivacyDeps:
    agent: AgentDeps
    session_factory: async_sessionmaker[AsyncSession]
    storage: ObjectStorage
    store: VectorStore


def session_prefix(org_id: uuid.UUID, session_id: uuid.UUID) -> str:
    """Where interview media (audio, whiteboard images, snapshots) is stored."""
    return f"org/{org_id}/sessions/{session_id}/"


async def candidate_index(db: AsyncSession) -> list[Item]:
    rows = await db.execute(
        sql_text("SELECT kind, id, owner_user_id, label, document_id, profile_id FROM app_candidate_index()")
    )
    return [Item(r.kind, r.id, r.owner_user_id, r.label or "", r.document_id, r.profile_id) for r in rows]


async def _profile_email(deps: PrivacyDeps, org_id: uuid.UUID, item: Item) -> str:
    try:
        async with owner_session(deps.session_factory, org_id, item.owner_user_id) as db:
            profile = await db.get(CandidateProfile, item.id)
            if profile is None:
                return ""
            pii = await cv.decrypt_pii(deps.agent, db, profile)  # {"name", "email", "phone", "links"}
            return str(pii.get("email") or "").strip().lower()
    except EngineError:
        return ""


async def find_subject(
    deps: PrivacyDeps,
    org_id: uuid.UUID,
    index: list[Item],
    *,
    email: str | None = None,
    session_ids: Iterable[uuid.UUID] = (),
    profile_ids: Iterable[uuid.UUID] = (),
    document_ids: Iterable[uuid.UUID] = (),
) -> list[Item]:
    """Everything linked to the given selectors: a CV pulls in its profile, sessions and question sets."""
    sessions, profiles, documents = set(session_ids), set(profile_ids), set(document_ids)
    if email:
        wanted = email.strip().lower()
        for item in index:
            if item.kind == "profile" and await _profile_email(deps, org_id, item) == wanted:
                profiles.add(item.id)
    for item in index:  # a profile belongs to one CV document; a selected session brings its CV along
        if item.kind == "profile" and item.id in profiles and item.document_id:
            documents.add(item.document_id)
        if item.kind == "session" and item.id in sessions and item.document_id:
            documents.add(item.document_id)
    for item in index:
        if item.kind == "profile" and item.document_id in documents:
            profiles.add(item.id)
    selected = []
    for item in index:
        linked = item.document_id in documents or (item.profile_id is not None and item.profile_id in profiles)
        if (
            (item.kind == "session" and (item.id in sessions or linked))
            or (item.kind == "question_set" and linked)
            or (item.kind == "cv" and item.id in documents)
            or (item.kind == "profile" and item.id in profiles)
        ):
            selected.append(item)
    return selected


async def _erase_one(deps: PrivacyDeps, org_id: uuid.UUID, item: Item) -> bool:
    try:
        async with owner_session(deps.session_factory, org_id, item.owner_user_id) as db:
            if item.kind == "session":
                row: Any = await db.get(InterviewSession, item.id)
            elif item.kind == "question_set":
                row = await db.get(QuestionSet, item.id)
            elif item.kind == "profile":
                row = await db.get(CandidateProfile, item.id)
            else:
                row = await db.get(Document, item.id)
            if row is None:
                return False
            space_id = row.space_id if isinstance(row, Document) else None
            if isinstance(row, Document):
                row.status = DocStatus.DELETING
                await db.flush()
            await db.delete(row)  # FK cascades: turns, grades, scorecards, events, items, chunks, profiles
            await db.commit()
    except EngineError:
        return False
    if item.kind == "session":
        await deps.storage.delete_prefix(session_prefix(org_id, item.id))
    if space_id is not None:
        await deps.store.delete_document(org_id, item.id)
        await deps.storage.delete_prefix(document_prefix(org_id, space_id, item.id))
    return True


async def erase(deps: PrivacyDeps, org_id: uuid.UUID, items: list[Item]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in sorted(items, key=lambda i: ERASE_ORDER.get(i.kind, 9)):
        if await _erase_one(deps, org_id, item):
            counts[item.kind] = counts.get(item.kind, 0) + 1
    return counts


async def export(deps: PrivacyDeps, org_id: uuid.UUID, items: list[Item]) -> dict[str, Any]:
    """Subject access export: everything held about the candidate, decrypted, as JSON-ready data."""
    bundle: dict[str, Any] = {"profiles": [], "documents": [], "interviews": [], "question_sets": []}
    for item in items:
        try:
            async with owner_session(deps.session_factory, org_id, item.owner_user_id) as db:
                await _export_one(deps, db, item, bundle)
        except EngineError:
            continue
    return bundle


async def _export_one(deps: PrivacyDeps, db: AsyncSession, item: Item, bundle: dict[str, Any]) -> None:
    if item.kind == "profile":
        profile = await db.get(CandidateProfile, item.id)
        if profile is not None:
            bundle["profiles"].append(
                {
                    "id": str(profile.id),
                    "created_at": profile.created_at.isoformat(),
                    "contact_and_name": await cv.decrypt_pii(deps.agent, db, profile),
                    "profile": profile.structured,
                    "summary": profile.blind_summary,
                }
            )
    elif item.kind == "cv":
        doc = await db.get(Document, item.id)
        if doc is not None:
            bundle["documents"].append(
                {
                    "id": str(doc.id),
                    "title": doc.title,
                    "filename": doc.filename,
                    "created_at": doc.created_at.isoformat(),
                }
            )
    elif item.kind == "session":
        session = await db.get(InterviewSession, item.id)
        if session is not None:
            bundle["interviews"].append(await _session_export(db, session))
    elif item.kind == "question_set":
        qset = await db.get(QuestionSet, item.id)
        if qset is not None:
            items = await db.scalars(
                select(QuestionItem).where(QuestionItem.set_id == qset.id).order_by(QuestionItem.ord)
            )
            bundle["question_sets"].append(
                {"id": str(qset.id), "title": qset.title, "questions": [i.data.get("text") for i in items]}
            )


async def _session_export(db: AsyncSession, session: InterviewSession) -> dict[str, Any]:
    turns = await db.scalars(
        select(InterviewTurn).where(InterviewTurn.session_id == session.id).order_by(InterviewTurn.seq)
    )
    card = await db.scalar(select(Scorecard).where(Scorecard.session_id == session.id))
    events = await db.scalars(select(IntegrityEvent).where(IntegrityEvent.session_id == session.id))
    return {
        "id": str(session.id),
        "title": session.title,
        "candidate_label": session.candidate_label,
        "status": session.status.value,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "consent": session.consent,
        "transcript": [{"seq": t.seq, "speaker": t.speaker.value, "text": t.text} for t in turns],
        "assessment": None
        if card is None
        else {
            "label": "Advisory assessment; the decision was made by a person.",
            "data": card.data,
            "human_decision": card.human_decision,
        },
        "integrity_signals": [
            {"at": e.created_at.isoformat(), "kind": e.kind, "severity": e.severity, "detail": e.detail} for e in events
        ],
    }


async def purge_expired(deps: PrivacyDeps) -> dict[str, int]:
    """Nightly: erase candidate data older than each organization's retention period."""
    async with deps.session_factory() as db:
        rows = list(
            await db.execute(
                sql_text("SELECT kind, id, org_id, owner_user_id FROM app_expired_candidate_data(:n)"),
                {"n": PURGE_BATCH},
            )
        )
    counts: dict[str, int] = {}
    for row in rows:
        if await _erase_one(deps, row.org_id, Item(row.kind, row.id, row.owner_user_id)):
            counts[row.kind] = counts.get(row.kind, 0) + 1
    if counts:
        log.info("privacy.purged", **counts)
    return counts
