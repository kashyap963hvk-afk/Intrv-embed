#!/usr/bin/env python3
"""Create .env from .env.example, replacing __GENERATE_<NAME>__ tokens with random secrets.

Tokens ending in _32B__ become base64 of 32 random bytes (encryption keys); all others become
URL-safe random strings, so they can be embedded in connection URLs without escaping.
Refuses to overwrite an existing .env unless --force is given.
"""

from __future__ import annotations

import argparse
import base64
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"__GENERATE_([A-Z0-9_]+?)__")


def _value(name: str) -> str:
    if name.endswith("_32B"):
        return base64.b64encode(secrets.token_bytes(32)).decode()
    return secrets.token_hex(24)


def render(template: str) -> str:
    values: dict[str, str] = {}

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            values[name] = _value(name)
        return values[name]

    return TOKEN.sub(substitute, template)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="overwrite an existing .env")
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    args = parser.parse_args()

    if args.output.exists() and not args.force:
        print(f"{args.output} already exists; use --force to regenerate (this rotates all secrets).", file=sys.stderr)
        return 1
    args.output.write_text(render((ROOT / ".env.example").read_text(encoding="utf-8")), encoding="utf-8")
    args.output.chmod(0o600)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
