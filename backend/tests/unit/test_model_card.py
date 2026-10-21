"""docs/MODEL_CARD.md is generated from the code; this fails while the committed card is stale."""

from __future__ import annotations

from pathlib import Path

from app.compliance import model_card

ROOT = Path(__file__).resolve().parents[2]


def test_model_card_is_current() -> None:
    expected = model_card.render(str(ROOT / "seed" / "role_packs"))
    committed = (ROOT.parent / "docs" / "MODEL_CARD.md").read_text(encoding="utf-8")
    assert committed == expected, "docs/MODEL_CARD.md is stale: run `python -m app.cli model-card`"


def test_model_card_states_the_limits() -> None:
    card = model_card.render(str(ROOT / "seed" / "role_packs"))
    assert "advisory" in card
    assert "No emotion" in card
    assert "embedded-systems" in card
    assert "`platform_integrity` | " in card
