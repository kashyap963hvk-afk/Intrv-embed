"""The production image installs only runtime dependencies (`uv sync --no-dev`). Every module in `app` and
`workers` must import without the dev group, or the API crashes at start-up in production only."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import settings_values

ROOT = Path(__file__).resolve().parents[2]
DEV_ONLY = ("mypy_boto3_s3", "mypy", "pytest", "respx", "testcontainers", "docker", "psycopg")

SCRIPT = f"""
import importlib, importlib.abc, pkgutil, sys

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {DEV_ONLY!r}:
            raise ModuleNotFoundError(f"dev-only module imported at runtime: {{name}}")
        return None

sys.meta_path.insert(0, Block())
for package in ("app", "workers"):
    root = importlib.import_module(package)
    for info in pkgutil.walk_packages(root.__path__, prefix=package + "."):
        importlib.import_module(info.name)
print("ok")
"""


def test_app_imports_without_dev_dependencies() -> None:
    env = {**os.environ, **{key.upper(): str(value) for key, value in settings_values().items()}}
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180, check=False
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().endswith("ok")
