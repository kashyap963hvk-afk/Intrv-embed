"""Turning model text into validated structured data."""

from __future__ import annotations

import json
import re
from typing import Any

import json_repair
from pydantic import BaseModel, ValidationError

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> blocks some reasoning models emit before the answer."""
    return _THINK.sub("", text).strip()


def extract_json_text(text: str) -> str:
    cleaned = strip_reasoning(text)
    fenced = _FENCE.match(cleaned)
    if fenced:
        return fenced.group(1)
    start = min((i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0), default=-1)
    return cleaned[start:] if start > 0 else cleaned


def parse_model[T: BaseModel](text: str, schema: type[T]) -> T:
    """Parse (and if needed repair) JSON, then validate. Raises ValueError with a readable reason."""
    candidate = extract_json_text(text)
    try:
        data: Any = json.loads(candidate)
    except json.JSONDecodeError:
        data = json_repair.loads(candidate)
        if data in ("", None):
            raise ValueError("the output contained no JSON") from None
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in exc.errors()[:8])
        raise ValueError(f"the JSON did not match the schema: {problems}") from exc


def schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object and nothing else (no prose, no code fences). "
        f"It must validate against this JSON Schema:\n{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
    )


def response_format_for(schema: type[BaseModel], capabilities: dict[str, Any]) -> dict[str, Any] | None:
    if capabilities.get("json_schema"):
        return {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
        }
    if capabilities.get("json_object"):
        return {"type": "json_object"}
    return None
