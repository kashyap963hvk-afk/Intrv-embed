"""CV intelligence: CandidateProfile extraction (contact details split off and encrypted), an evidence index the
other agents must cite, and the JD gap matrix."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import composer
from app.agents.runtime import AgentDeps, llm, org_name
from app.agents.schemas import ExtractedProfile, GapMatrix
from app.core.errors import AppError, NotFoundError
from app.db.models import (
    AgentRole,
    CandidateProfile,
    ChunkKind,
    DocStatus,
    Document,
    DocumentChunk,
    RolePack,
)
from app.llm.types import CallCtx

MAX_CV_CHARS = 60_000
MAX_JD_CHARS = 30_000


class DocumentNotReadyError(AppError):
    status_code = 409
    slug = "document-not-ready"
    title = "The document is not ready yet"


def pii_purpose(profile_id: uuid.UUID) -> str:
    return f"candidate-pii:{profile_id}"


async def document_text(db: AsyncSession, document_id: uuid.UUID, limit: int) -> tuple[Document, str]:
    """The parsed text of a READY document the caller can read (hidden CV text was already excluded at ingest)."""
    doc = await db.get(Document, document_id)  # RLS: only readable documents exist
    if doc is None:
        raise NotFoundError("Document not found.")
    if doc.status != DocStatus.READY:
        raise DocumentNotReadyError(f"'{doc.title}' is {doc.status.value.lower()}; wait until it is ready.")
    parents = await db.scalars(
        select(DocumentChunk.text)
        .where(DocumentChunk.document_id == document_id, DocumentChunk.kind == ChunkKind.PARENT)
        .order_by(DocumentChunk.ord)
    )
    text = "\n\n".join(parents)
    if not text.strip():
        raise AppError(f"No text could be read from '{doc.title}'.")
    return doc, text[:limit]


async def allowed_domain_tags(db: AsyncSession) -> list[str]:
    tags: set[str] = set()
    for pack_tags in await db.scalars(select(RolePack.domain_tags).where(RolePack.archived.is_(False))):
        tags.update(pack_tags or [])
    return sorted(tags)


def evidence_index(structured: dict[str, Any]) -> dict[str, str]:
    """Stable ids for every citable CV fact, e.g. "projects[1].claims[0]" → the claim text."""
    index: dict[str, str] = {}
    for i, role in enumerate(structured.get("roles", [])):
        span = " - ".join(p for p in (role.get("start"), role.get("end")) if p)
        index[f"roles[{i}]"] = (
            f"{role.get('title', '')} at {role.get('company', '')} {f'({span})' if span else ''}".strip()
        )
        for j, achievement in enumerate(role.get("achievements", [])):
            index[f"roles[{i}].achievements[{j}]"] = achievement
    for i, project in enumerate(structured.get("projects", [])):
        index[f"projects[{i}]"] = f"Project {project.get('name', '')}: {', '.join(project.get('tech', []))}".strip()
        for j, claim in enumerate(project.get("claims", [])):
            index[f"projects[{i}].claims[{j}]"] = claim
        for j, metric in enumerate(project.get("metrics", [])):
            index[f"projects[{i}].metrics[{j}]"] = metric
    for i, skill in enumerate(structured.get("skills", [])):
        index[f"skills[{i}]"] = skill.get("name", "")
    for i, claim in enumerate(structured.get("vague_claims", [])):
        index[f"vague_claims[{i}]"] = f"{claim.get('text', '')} (vague: {claim.get('why_vague', '')})"
    for i, cert in enumerate(structured.get("certifications", [])):
        index[f"certifications[{i}]"] = cert
    return index


def _scrub(text: str, extracted: ExtractedProfile) -> str:
    """Remove the name and contact details from free text (belt and braces for the blind summary)."""
    values = [extracted.contact.email, extracted.contact.phone, *extracted.contact.links]
    if extracted.name:
        values += [extracted.name, *[part for part in extracted.name.split() if len(part) > 2]]  # noqa: PLR2004
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        text = re.sub(re.escape(value), "[candidate]", text, flags=re.IGNORECASE)
    return re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)


async def analyze_cv(
    deps: AgentDeps,
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    force: bool = False,
) -> CandidateProfile:
    doc, text = await document_text(db, document_id, MAX_CV_CHARS)
    if not force:
        existing = await db.scalar(
            select(CandidateProfile)
            .where(
                CandidateProfile.document_id == document_id,
                CandidateProfile.document_version == doc.version,
                CandidateProfile.owner_user_id == user_id,
            )
            .order_by(CandidateProfile.created_at.desc())
        )
        if existing is not None:
            return existing
    tags = await allowed_domain_tags(db)
    messages = composer.task_messages(
        "cv_extract",
        org_name=await org_name(db, org_id),
        trusted={"allowed_domain_tags": ", ".join(tags) or "(none)"},
        untrusted={"cv_markdown": text},
    )
    ctx = CallCtx(org_id=org_id, user_id=user_id, role=AgentRole.EXTRACTOR, contains_pii=True)
    service = llm(deps, db)
    extracted = await service.complete_json(ctx, messages, ExtractedProfile, temperature=0.0)
    structured = extracted.model_dump(mode="json", exclude={"name", "contact", "blind_summary"}, by_alias=True)
    structured["domain_tags"] = [t for t in extracted.domain_tags if t in tags]
    profile = CandidateProfile(
        id=uuid.uuid4(),
        org_id=org_id,
        owner_user_id=user_id,
        document_id=document_id,
        document_version=doc.version,
        structured=structured,
        blind_summary=_scrub(extracted.blind_summary, extracted),
        domain_tags=structured["domain_tags"],
    )
    pii = {"name": extracted.name, **extracted.contact.model_dump()}
    profile.pii_encrypted = await deps.keyring.encrypt(db, org_id, json.dumps(pii), purpose=pii_purpose(profile.id))
    db.add(profile)
    await db.flush()
    return profile


async def decrypt_pii(deps: AgentDeps, db: AsyncSession, profile: CandidateProfile) -> dict[str, Any]:
    if profile.pii_encrypted is None:
        return {}
    data = json.loads(
        await deps.keyring.decrypt(db, profile.org_id, profile.pii_encrypted, purpose=pii_purpose(profile.id))
    )
    return data if isinstance(data, dict) else {}


async def match_jd(
    deps: AgentDeps,
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    profile: CandidateProfile,
    jd_document_id: uuid.UUID,
    competency_ids: list[str],
) -> GapMatrix:
    _, jd_text = await document_text(db, jd_document_id, MAX_JD_CHARS)
    index = evidence_index(profile.structured)
    messages = composer.task_messages(
        "jd_match",
        org_name=await org_name(db, org_id),
        trusted={"competency_ids": ", ".join(competency_ids)},
        untrusted={"cv_evidence_index": index, "jd_markdown": jd_text},
    )
    ctx = CallCtx(org_id=org_id, user_id=user_id, role=AgentRole.EXTRACTOR, contains_pii=True)
    matrix = await llm(deps, db).complete_json(ctx, messages, GapMatrix, temperature=0.0)
    for requirement in matrix.requirements:
        requirement.evidence_refs = [ref for ref in requirement.evidence_refs if ref in index]
        if requirement.competency_id not in competency_ids:
            requirement.competency_id = None
    return matrix
