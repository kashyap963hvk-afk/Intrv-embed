"""Provider presets loaded from presets.yaml (data, not code)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.db.models import ProviderKind

PRESETS_FILE = Path(__file__).with_name("presets.yaml")


class PresetSupports(BaseModel):
    stream: bool = True
    json_schema: bool = False
    json_object: bool = False
    tools: bool = False
    vision: bool = False


class Preset(BaseModel):
    key: str
    display_name: str
    kind: ProviderKind
    base_url: str
    requires_api_key: bool
    docs_url: str
    signup_url: str | None = None
    free_tier: bool
    trains_on_data: bool | None
    notes: str | None = None
    default_rpm: int | None = None
    default_rpd: int | None = None
    supports: PresetSupports = Field(default_factory=PresetSupports)
    model_hints: list[str] = Field(default_factory=list)


@lru_cache
def load_presets() -> dict[str, Preset]:
    raw = yaml.safe_load(PRESETS_FILE.read_text(encoding="utf-8"))
    presets = [Preset.model_validate(item) for item in raw]
    keys = [p.key for p in presets]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate preset keys in presets.yaml")
    return {p.key: p for p in presets}


def get_preset(key: str | None) -> Preset | None:
    return load_presets().get(key) if key else None
