from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm.json_output import extract_json_text, parse_model, response_format_for, strip_reasoning


class Plan(BaseModel):
    topics: list[str]
    minutes: int


def test_strips_reasoning_and_fences() -> None:
    assert strip_reasoning('<think>hmm\nlong</think> {"a": 1}') == '{"a": 1}'
    assert extract_json_text('```json\n{"topics": [], "minutes": 3}\n```') == '{"topics": [], "minutes": 3}'
    assert extract_json_text('Sure! Here it is: {"x": 1}') == '{"x": 1}'


def test_parse_valid_and_repairs_common_mistakes() -> None:
    assert parse_model('{"topics": ["RTOS"], "minutes": 5}', Plan).minutes == 5
    # trailing comma + single quotes: repaired
    assert parse_model("{'topics': ['DMA', 'I2C',], 'minutes': 7,}", Plan).topics == ["DMA", "I2C"]


def test_schema_violations_explained() -> None:
    with pytest.raises(ValueError, match="minutes"):
        parse_model('{"topics": ["x"]}', Plan)
    with pytest.raises(ValueError, match="no JSON"):
        parse_model("I cannot help with that.", Plan)


def test_response_format_ladder() -> None:
    assert response_format_for(Plan, {"json_schema": True})["type"] == "json_schema"  # type: ignore[index]
    assert response_format_for(Plan, {"json_object": True}) == {"type": "json_object"}
    assert response_format_for(Plan, {}) is None
