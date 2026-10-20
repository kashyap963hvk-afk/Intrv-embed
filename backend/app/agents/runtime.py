"""What an agent call needs: an LLM service bound to the caller's tenant session, plus grounded retrieval."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.keyring import Keyring
from app.db.models import Organization
from app.knowledge.acl import readable, space_permissions
from app.llm.gateway import LLMGateway
from app.llm.service import LLMService
from app.rag.engine import RagEngine
from app.rag.retrieve import RetrievalRequest, RetrievedChunk


@dataclass(frozen=True, slots=True)
class AgentDeps:
    settings: Settings
    keyring: Keyring
    gateway: LLMGateway
    rag: RagEngine
    valkey: Redis | None


def llm(deps: AgentDeps, db: AsyncSession) -> LLMService:
    return LLMService(db, settings=deps.settings, keyring=deps.keyring, gateway=deps.gateway)


async def org_name(db: AsyncSession, org_id: uuid.UUID) -> str:
    org = await db.get(Organization, org_id)
    return org.name if org else "the organization"


async def search(
    deps: AgentDeps,
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    space_ids: Sequence[uuid.UUID] | None = None,
    k: int = 4,
    doc_types: Sequence[str] | None = None,
) -> list[RetrievedChunk]:
    """Retrieval limited to what this user may read (and, if given, to the chosen spaces)."""
    allowed = readable(await space_permissions(db, deps.valkey, org_id=org_id, user_id=user_id))
    spaces = sorted(allowed if space_ids is None else allowed & set(space_ids))
    if not spaces:
        return []
    retriever = await deps.rag.retriever()
    result = await retriever.retrieve(
        db,
        org_id,
        RetrievalRequest(query=query, space_ids=spaces, final=k, doc_types=doc_types, token_budget=1500),
    )
    return result.chunks


def snippets(chunks: Sequence[RetrievedChunk], limit_chars: int = 1200) -> str:
    """Reference snippets for prompts, each tagged with its document id for citations."""
    if not chunks:
        return "(no reference documents)"
    parts = []
    for chunk in chunks:
        where = " > ".join([chunk.citation.title, *chunk.citation.section_path])
        parts.append(
            f'<snippet document_id="{chunk.document_id}" source="{where}">\n{chunk.text[:limit_chars]}\n</snippet>'
        )
    return "\n".join(parts)
