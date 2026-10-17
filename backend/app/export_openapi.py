"""Write the OpenAPI schema to a file (or stdout) without needing any running services.

Usage: python -m app.export_openapi [output_path]
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.main import create_app

# The schema does not depend on connection settings; these values only satisfy validation.
_SCHEMA_ONLY_SETTINGS = {
    "app_env": "dev",
    "database_url": "postgresql+asyncpg://schema:schema@localhost:5432/schema",
    "valkey_url": "redis://localhost:6379/0",
    "qdrant_url": "http://localhost:6333",
    "s3_bucket": "schema",
    "master_key_base64": base64.b64encode(b"\0" * 32).decode(),
    "jwt_secret": "schema-export-only-" + "x" * 32,
}


def build_schema() -> dict[str, Any]:
    settings = Settings.model_validate(_SCHEMA_ONLY_SETTINGS)
    return create_app(settings).openapi()


def render_schema() -> str:
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    rendered = render_schema()
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
