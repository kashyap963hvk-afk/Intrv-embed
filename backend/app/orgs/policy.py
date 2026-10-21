"""Organization-wide policy (stored in organizations.settings["policy"])."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Organization, ProviderKind

DEFAULT_ALLOWED_KINDS = [
    ProviderKind.OPENAI_COMPATIBLE,
    ProviderKind.SERVER_LOCAL,
    ProviderKind.BRIDGE,
    ProviderKind.GGUF_MANAGED,
]


class OrgPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    allowed_provider_kinds: list[ProviderKind] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_KINDS))
    allowed_preset_keys: list[str] | None = Field(
        default=None, description="Restrict cloud providers to these presets; null allows all."
    )
    block_training_providers_for_pii: bool = Field(
        default=True,
        description="Never send candidate data (CVs, transcripts) to providers that may train on it or whose "
        "policy is unknown.",
    )
    allow_member_llm_profiles: bool = Field(
        default=True, description="Members may add their own models and API keys (subject to their permissions)."
    )
    # --- Interviews (prompt layer L1 and integrity defaults; ARCHITECTURE §13.2, §15.1) ----------------------
    interview_tone: str = Field(
        default="", max_length=1000, description="Org-wide tone guidance for the AI interviewer (L1)."
    )
    company_facts: str = Field(
        default="",
        max_length=4000,
        description="Facts the interviewer may share when candidates ask about the company or role.",
    )
    forbidden_topics: list[str] = Field(default_factory=list, max_length=30)
    integrity_mode: Literal["OFF", "STANDARD", "STRICT"] = Field(
        default="STANDARD", description="Default interview integrity monitoring (templates may override)."
    )
    integrity_reminder: str = Field(
        default="Please keep this window in focus and answer from your own experience.",
        max_length=300,
        description="Neutral reminder the interviewer may give once per section when signals appear.",
    )
    consent_contact: str = Field(
        default="", max_length=300, description="Who candidates contact about their data or to request a human."
    )
    retention_days: int = Field(default=180, ge=7, le=3650)
    # --- Sign-in ----------------------------------------------------------------------------------------------
    member_sign_in: Literal["password", "google"] = Field(
        default="password",
        description="How members sign in. 'google': members use Sign in with Google through their invitation; "
        "only owners may use a password.",
    )


def org_policy(org: Organization) -> OrgPolicy:
    raw = (org.settings or {}).get("policy", {})
    return OrgPolicy.model_validate(raw if isinstance(raw, dict) else {})


def with_policy(org: Organization, policy: OrgPolicy) -> dict[str, object]:
    return {**(org.settings or {}), "policy": policy.model_dump(mode="json")}
