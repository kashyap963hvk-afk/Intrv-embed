"""Prompt files → messages (CLAUDE.md rule 6).

Prompts live in `agents/prompts/*.md` with a `<!-- prompt-version: N -->` header. Every agent call is composed as
L0 platform rules (never user-editable) followed by the task prompt. Values are substituted for `{{name}}`
placeholders; untrusted values (CVs, documents, candidate speech) have any of our wrapper tags neutralised, so
text inside a CV cannot close `<candidate_cv>` and pose as instructions.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.llm.types import Message

PROMPTS_DIR = Path(__file__).parent / "prompts"
_VERSION = re.compile(r"<!--\s*prompt-version:\s*(\d+)\s*-->\s*")
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_WRAPPER_TAGS = re.compile(r"<(/?)\s*(documents|candidate_cv|candidate|control|system|instructions?)\b", re.IGNORECASE)
L0 = "platform_integrity"


class PromptError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: int
    template: str

    @property
    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.template))


@functools.cache
def load(name: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"unknown prompt {name!r}")
    raw = path.read_text(encoding="utf-8")
    match = _VERSION.match(raw)
    if match is None:
        raise PromptError(f"prompt {name!r} has no prompt-version header")
    return Prompt(name, int(match.group(1)), raw[match.end() :].strip())


def neutralize(text: str) -> str:
    """Untrusted text may mention our tags but never open or close them."""
    return _WRAPPER_TAGS.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", text)


def as_data(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)


def render(name: str, *, trusted: dict[str, Any] | None = None, untrusted: dict[str, Any] | None = None) -> str:
    prompt = load(name)
    values = {k: as_data(v) for k, v in (trusted or {}).items()}
    values.update({k: neutralize(as_data(v)) for k, v in (untrusted or {}).items()})
    missing = prompt.placeholders - set(values)
    if missing:
        raise PromptError(f"prompt {name!r} is missing values for: {', '.join(sorted(missing))}")
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], prompt.template)


def versions(*names: str) -> dict[str, int]:
    return {name: load(name).version for name in (L0, *names)}


def task_messages(
    name: str,
    *,
    org_name: str,
    trusted: dict[str, Any] | None = None,
    untrusted: dict[str, Any] | None = None,
    user: str = "Produce the output now.",
) -> list[Message]:
    """System = L0 + task prompt; the user turn only asks for the result (data stays inside the wrappers)."""
    system = f"{render(L0, trusted={'org_name': org_name})}\n\n{render(name, trusted=trusted, untrusted=untrusted)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


Layer = tuple[str, dict[str, Any], dict[str, Any]]  # (prompt name, trusted values, untrusted values)


def layered_system(*, org_name: str, layers: list[Layer]) -> str:
    """L0 platform rules first, then each layer in the given order (L1 org policy … L4 directive)."""
    parts = [render(L0, trusted={"org_name": org_name})]
    parts += [render(name, trusted=trusted, untrusted=untrusted) for name, trusted, untrusted in layers]
    return "\n\n".join(parts)
