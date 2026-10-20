"""Interview report export (PDF via reportlab). The JSON export is the report itself."""

from __future__ import annotations

import io
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

INTEGRITY_NOTE = "Integrity signals are informational. They are not proof and were not used in scoring."


def _bullets(values: list[Any], style: Any) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(escape(str(v)), style), leftIndent=10) for v in values], bulletType="bullet"
    )


def to_pdf(report: dict[str, Any]) -> bytes:
    styles = getSampleStyleSheet()
    body, small, h2, h3 = styles["BodyText"], styles["Italic"], styles["Heading2"], styles["Heading3"]
    card = report.get("scorecard") or {}
    data = card.get("data") or {}
    narrative = data.get("narrative") or {}
    story: list[Any] = [
        Paragraph(escape(report["title"]), styles["Title"]),
        Paragraph(escape(f"{report.get('role', '')} · {report.get('ended_at') or report.get('status')}"), small),
        Spacer(1, 4 * mm),
        Paragraph(f"<b>{escape(data.get('label', 'Advisory: requires a human decision.'))}</b>", body),
        Spacer(1, 4 * mm),
    ]
    if narrative:
        story += [
            Paragraph("Summary", h2),
            Paragraph(escape(narrative.get("summary", "")), body),
            Paragraph(
                escape(
                    f"AI recommendation: {narrative.get('recommendation', '')} "
                    f"({narrative.get('recommendation_reason', '')})"
                ),
                body,
            ),
        ]
        if card.get("human_decision"):
            story.append(
                Paragraph(escape(f"Human decision: {card['human_decision']}. {card.get('decision_notes') or ''}"), body)
            )
    if data.get("competencies"):
        story.append(Paragraph("Competencies", h2))
        for competency in data["competencies"]:
            score = competency["score"] if competency["assessed"] else "not assessed"
            flag = " (needs human review)" if competency.get("needs_human_review") else ""
            story.append(Paragraph(escape(f"{competency['name']}: {score}{flag}"), h3))
            quotes = [f"“{e['quote']}” (turn {e['turn_seq']})" for e in competency.get("evidence", [])]
            if quotes:
                story.append(_bullets(quotes, body))
    for label, key in (
        ("Strengths", "strengths"),
        ("Concerns", "concerns"),
        ("CV claims verified", "claims_verified"),
        ("CV claims contradicted", "claims_contradicted"),
        ("CV claims untested", "claims_untested"),
        ("Focus for the next round", "next_round_focus"),
    ):
        if narrative.get(key):
            story += [Paragraph(label, h2), _bullets(narrative[key], body)]
    if narrative.get("knowledge_gaps"):
        story += [
            Paragraph("Knowledge gaps", h2),
            _bullets([f"{g['topic']}: {g['explanation']}" for g in narrative["knowledge_gaps"]], body),
        ]
    integrity = report.get("integrity") or {}
    story += [
        Paragraph("Integrity signals", h2),
        Paragraph(escape(INTEGRITY_NOTE), small),
        Paragraph(escape(f"Overall: {integrity.get('rollup', 'nothing notable')}"), body),
    ]
    counts = integrity.get("counts") or {}
    if counts:
        story.append(_bullets([f"{kind}: {count}" for kind, count in sorted(counts.items())], body))
    story += [Spacer(1, 4 * mm), Paragraph("Transcript", h2)]
    for turn in report.get("transcript", []):
        story.append(Paragraph(f"<b>{escape(turn['speaker'].title())}:</b> {escape(turn['text'])}", body))
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, title=report["title"], leftMargin=18 * mm, rightMargin=18 * mm).build(story)
    return buffer.getvalue()
