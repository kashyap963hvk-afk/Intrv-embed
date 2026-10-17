"""Organization-wide policy (stored in organizations.settings["policy"])."""

from __future__ import annotations

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


def org_policy(org: Organization) -> OrgPolicy:
    raw = (org.settings or {}).get("policy", {})
    return OrgPolicy.model_validate(raw if isinstance(raw, dict) else {})


def with_policy(org: Organization, policy: OrgPolicy) -> dict[str, object]:
    return {**(org.settings or {}), "policy": policy.model_dump(mode="json")}
