"""CV intelligence API: analyse a CV document into a CandidateProfile and match it against a job description."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select

from app.agents.schemas import GapMatrix
from app.auth.deps import ActiveMember, csrf_protect
from app.auth.principal import Principal
from app.core.errors import NotFoundError
from app.cv import service
from app.db.deps import DbSession
from app.db.models import CandidateProfile
from app.knowledge.deps import ResourcesDep
from app.rolepacks import service as packs

router = APIRouter(prefix="/api/cv", tags=["cv"], dependencies=[Depends(csrf_protect)])


class AnalyzeIn(BaseModel):
    document_id: uuid.UUID
    force: bool = False


class ContactOut(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    links: list[str] = []


class CandidateProfileOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_version: int
    structured: dict[str, Any]
    blind_summary: str
    domain_tags: list[str]
    evidence_index: dict[str, str]
    contact: ContactOut
    created_at: datetime


class MatchIn(BaseModel):
    profile_id: uuid.UUID
    jd_document_id: uuid.UUID
    role_pack_id: uuid.UUID


async def _out(resources: ResourcesDep, db: DbSession, profile: CandidateProfile) -> CandidateProfileOut:
    pii = await service.decrypt_pii(resources.agent_deps, db, profile)
    return CandidateProfileOut(
        id=profile.id,
        document_id=profile.document_id,
        document_version=profile.document_version,
        structured=profile.structured,
        blind_summary=profile.blind_summary,
        domain_tags=list(profile.domain_tags or []),
        evidence_index=service.evidence_index(profile.structured),
        contact=ContactOut.model_validate(pii),
        created_at=profile.created_at,
    )


async def _profile(db: DbSession, principal: Principal, profile_id: uuid.UUID) -> CandidateProfile:
    profile = await db.get(CandidateProfile, profile_id)  # RLS: own profiles only
    if profile is None or profile.owner_user_id != principal.user_id:
        raise NotFoundError("Candidate profile not found.")
    return profile


@router.post(
    "/analyze", response_model=CandidateProfileOut, summary="Extract a structured, blind-scorable profile from a CV"
)
async def analyze(
    body: AnalyzeIn, principal: ActiveMember, db: DbSession, resources: ResourcesDep
) -> CandidateProfileOut:
    profile = await service.analyze_cv(
        resources.agent_deps,
        db,
        org_id=principal.org_id,
        user_id=principal.user_id,
        document_id=body.document_id,
        force=body.force,
    )
    return await _out(resources, db, profile)


@router.get("/profiles", response_model=list[CandidateProfileOut])
async def list_profiles(
    principal: ActiveMember, db: DbSession, resources: ResourcesDep, document_id: uuid.UUID | None = None
) -> list[CandidateProfileOut]:
    query = select(CandidateProfile).where(CandidateProfile.owner_user_id == principal.user_id)
    if document_id is not None:
        query = query.where(CandidateProfile.document_id == document_id)
    profiles = await db.scalars(query.order_by(CandidateProfile.created_at.desc()).limit(100))
    return [await _out(resources, db, p) for p in profiles]


@router.get("/profiles/{profile_id}", response_model=CandidateProfileOut)
async def get_profile(
    profile_id: uuid.UUID, principal: ActiveMember, db: DbSession, resources: ResourcesDep
) -> CandidateProfileOut:
    return await _out(resources, db, await _profile(db, principal, profile_id))


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: uuid.UUID, principal: ActiveMember, db: DbSession) -> None:
    await db.delete(await _profile(db, principal, profile_id))


@router.post("/match-jd", response_model=GapMatrix, summary="Gap matrix: job requirements vs CV evidence")
async def match_jd(body: MatchIn, principal: ActiveMember, db: DbSession, resources: ResourcesDep) -> GapMatrix:
    profile = await _profile(db, principal, body.profile_id)
    spec = packs.spec_of(await packs.get_pack(db, body.role_pack_id))
    return await service.match_jd(
        resources.agent_deps,
        db,
        org_id=principal.org_id,
        user_id=principal.user_id,
        profile=profile,
        jd_document_id=body.jd_document_id,
        competency_ids=[c.id for c in spec.competencies],
    )
