from __future__ import annotations

from pathlib import Path

from app.export_openapi import render_schema

SNAPSHOT = Path(__file__).resolve().parents[2] / "openapi.json"


def test_openapi_snapshot_is_current() -> None:
    """The committed schema drives frontend type generation; it must match the code."""
    assert SNAPSHOT.exists(), "backend/openapi.json missing: run `make openapi`"
    assert SNAPSHOT.read_text(encoding="utf-8") == render_schema(), (
        "backend/openapi.json is stale: run `make openapi` and commit the result"
    )
