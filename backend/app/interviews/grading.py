"""Shadow grading and the scorecard (ARCHITECTURE §13.5 to 13.6).

Graders run blind (no name, no demographics, no integrity data) at low temperature. Every score must cite verbatim
candidate quotes: quotes are checked against the stored transcript in code, wrong turn numbers are corrected, and a
grade with no verifiable quote is retried once and otherwise recorded as "not assessed". Finalisation re-grades each
topic until `grading_runs` runs exist, takes the median, flags spreads of two points or more for human review, and
writes the narrative. Scores, evidence and integrity are filled in by code; the model only writes prose.

Self-healing: finalisation grades any topic whose background grade never ran, and when no model is available the
scorecard falls back to a narrative built from the scores, so a finished interview always gets a report.
"""

from __future__ import annotations

import re
import statistics
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import composer
from app.agents.runtime import AgentDeps, llm, org_name
from app.core.logging import get_logger
from app.db.models import (
    AgentRole,
    IntegrityEvent,
    InterviewSession,
    InterviewTurn,
    Scorecard,
    ScorecardStatus,
    SessionStatus,
    Speaker,
    TopicGrade,
)
from app.interviews import integrity
from app.interviews.engine import LLM_FAILURES, owner_session
from app.interviews.schemas import CompetencyGrade, InterviewPlan, PlanTopic, ScorecardNarrative, TopicGradeResult
from app.knowledge.events import publish
from app.llm.types import CallCtx
from app.rolepacks.schema import RolePackSpec

log = get_logger("app.interviews")

ADVISORY = "Advisory: requires a human decision."
VARIANCE_POINTS = 2.0
EVIDENCE_IN_REPORT = 2
_SPACE = re.compile(r"\s+")
_QUOTE_TRIM = "\"' .\u2026\u201c\u201d\u2018\u2019"
RECOMMENDATION_BANDS = ((3.0, "strong_no"), (4.5, "no"), (5.5, "lean_no"), (6.5, "lean_yes"), (8.0, "yes"))


class GradingUnavailableError(Exception):
    def __init__(self, failures: list[str]) -> None:
        super().__init__("No model could grade this interview: " + "; ".join(failures[:3]))


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text.lower()).strip()


def verify_evidence(grade: CompetencyGrade, candidate_turns: Mapping[int, str]) -> CompetencyGrade:
    """Keep only quotes that really occur in a candidate turn; fix the turn number when the quote is elsewhere."""
    kept = []
    for evidence in grade.evidence:
        quote = _norm(evidence.quote.strip(_QUOTE_TRIM).replace("...", " ").replace("…", " "))
        if len(quote) < 3:  # noqa: PLR2004
            continue
        fragments = [f.strip() for f in re.split(r"\s{2,}", quote) if f.strip()] or [quote]
        seq = evidence.turn_seq
        if seq not in candidate_turns or not all(f in _norm(candidate_turns[seq]) for f in fragments):
            seq = next((s for s, text in candidate_turns.items() if all(f in _norm(text) for f in fragments)), -1)
        if seq >= 0:
            kept.append(evidence.model_copy(update={"turn_seq": seq}))
    return grade.model_copy(update={"evidence": kept})


def _transcript(turns: list[InterviewTurn]) -> str:
    return "\n".join(f"[turn {t.seq}] {t.speaker.value}: {t.text}" for t in turns if t.speaker != Speaker.SYSTEM)


async def grade_topic(
    deps: AgentDeps,
    db: AsyncSession,
    session: InterviewSession,
    topic: PlanTopic,
    *,
    run_no: int,
    temperature: float,
) -> list[TopicGrade]:
    """Grade one topic once (one row per competency). Existing rows for this run are replaced."""
    spec = RolePackSpec.model_validate(session.pack_snapshot)
    turns = list(
        await db.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.session_id == session.id, InterviewTurn.topic_id == topic.id)
            .order_by(InterviewTurn.seq)
        )
    )
    answers = {t.seq: t.text for t in turns if t.speaker == Speaker.CANDIDATE}
    await db.execute(
        delete(TopicGrade).where(
            TopicGrade.session_id == session.id, TopicGrade.topic_id == topic.id, TopicGrade.run_no == run_no
        )
    )
    competencies = [c for c in topic.competencies if spec.competency(c) is not None]
    results: dict[str, CompetencyGrade] = {}
    model: str | None = None
    if answers and competencies:
        results, model = await _grade_with_retry(
            deps, db, session, topic, spec, turns, answers, competencies, temperature
        )
    rows = []
    for competency in competencies:
        grade = results.get(competency)
        assessed = grade is not None and bool(grade.evidence)
        row = TopicGrade(
            org_id=session.org_id,
            owner_user_id=session.owner_user_id,
            session_id=session.id,
            topic_id=topic.id,
            competency=competency,
            run_no=run_no,
            anchor_level=grade.anchor_level if assessed and grade else None,
            score=grade.score if assessed and grade else None,
            evidence=[e.model_dump() for e in grade.evidence] if assessed and grade else [],
            detail=(
                {
                    "misconceptions": grade.misconceptions,
                    "verified_claims": grade.verified_claims,
                    "unverified_claims": grade.unverified_claims,
                }
                if assessed and grade
                else {"not_assessed": "no answers" if not answers else "no verifiable evidence"}
            ),
            confidence=grade.confidence if assessed and grade else "low",
            model=model,
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


async def _grade_with_retry(
    deps: AgentDeps,
    db: AsyncSession,
    session: InterviewSession,
    topic: PlanTopic,
    spec: RolePackSpec,
    turns: list[InterviewTurn],
    answers: dict[int, str],
    competencies: list[str],
    temperature: float,
) -> tuple[dict[str, CompetencyGrade], str | None]:
    anchors = {c: spec.competency(c).anchors for c in competencies if spec.competency(c)}  # type: ignore[union-attr]
    packs: dict[str, list[dict[str, str]]] = session.plan.get("context_packs", {})
    messages = composer.task_messages(
        "grader",
        org_name=await org_name(db, session.org_id),
        trusted={"topic_competencies": ", ".join(competencies), "anchors": anchors},
        untrusted={
            "context_pack": "\n".join(p["text"] for p in packs.get(topic.id, [])) or "(none)",
            "blind_summary": session.plan.get("candidate", {}).get("summary", "") or "(no CV)",
            "transcript_slice": _transcript(turns),
        },
    )
    ctx = CallCtx(
        org_id=session.org_id,
        user_id=session.owner_user_id,
        role=AgentRole.GRADER,
        session_id=session.id,
        contains_pii=True,
    )
    service = llm(deps, db)
    results: dict[str, CompetencyGrade] = {}
    for attempt in range(2):
        result = await service.complete_json(ctx, messages, TopicGradeResult, temperature=temperature)
        for grade in result.grades:
            if grade.competency in competencies and grade.competency not in results:
                checked = verify_evidence(grade, answers)
                if checked.evidence:
                    results[grade.competency] = checked
        if len(results) == len(competencies) or attempt == 1:
            break
        missing = ", ".join(c for c in competencies if c not in results)
        messages = [
            *messages,
            {
                "role": "user",
                "content": f"Grades for {missing} had no verbatim quote from a CANDIDATE turn. Grade every "
                "competency again; each evidence quote must be copied exactly from a candidate turn.",
            },
        ]
    profile = next(iter(await service.resolve_chain(ctx)), None)
    return results, profile.model if profile else None


def plan_of(session: InterviewSession) -> InterviewPlan:
    return InterviewPlan.model_validate({"topics": session.plan.get("topics", [])})


async def topics_with_answers(db: AsyncSession, session: InterviewSession) -> list[PlanTopic]:
    answered = set(
        await db.scalars(
            select(InterviewTurn.topic_id).where(
                InterviewTurn.session_id == session.id,
                InterviewTurn.speaker == Speaker.CANDIDATE,
                InterviewTurn.topic_id.is_not(None),
            )
        )
    )
    return [t for t in plan_of(session).topics if t.id in answered]


async def run_grade_topic(
    deps: AgentDeps, session_factory: async_sessionmaker[AsyncSession], args: Mapping[str, str]
) -> None:
    """Background job when a topic closes (first run, temperature 0.1). Failures are healed by finalisation."""
    org_id, owner, session_id = uuid.UUID(args["org_id"]), uuid.UUID(args["user_id"]), uuid.UUID(args["session_id"])
    async with owner_session(session_factory, org_id, owner) as db:
        session = await db.get(InterviewSession, session_id)
        topic = next((t for t in plan_of(session).topics if t.id == args["topic_id"]), None) if session else None
        if session is None or topic is None:
            return
        try:
            await grade_topic(deps, db, session, topic, run_no=0, temperature=0.1)
            await db.commit()
        except LLM_FAILURES as exc:
            await db.rollback()
            log.warning("interviews.grade_deferred", session_id=str(session_id), topic=args["topic_id"], error=str(exc))


def _median_by_competency(grades: list[TopicGrade]) -> dict[str, dict[str, Any]]:
    """Per competency: the median over runs within each topic, then the mean over topics."""
    by_topic: dict[tuple[str, str], list[TopicGrade]] = {}
    for grade in grades:
        by_topic.setdefault((grade.competency, grade.topic_id), []).append(grade)
    out: dict[str, dict[str, Any]] = {}
    for (competency, topic_id), runs in by_topic.items():
        scored = [g for g in runs if g.score is not None]
        entry = out.setdefault(
            competency, {"topic_scores": [], "levels": [], "runs": [], "evidence": [], "spread": 0.0, "topics": []}
        )
        entry["topics"].append(topic_id)
        if not scored:
            continue
        scores = [float(g.score) for g in scored if g.score is not None]
        entry["topic_scores"].append(statistics.median(scores))
        entry["levels"].append(statistics.median([g.anchor_level or 1 for g in scored]))
        entry["runs"].append({"topic_id": topic_id, "scores": scores})
        entry["spread"] = max(entry["spread"], max(scores) - min(scores))
        best = min(scored, key=lambda g: abs(float(g.score or 0) - statistics.median(scores)))
        entry["evidence"] += best.evidence[:EVIDENCE_IN_REPORT]
        entry.setdefault("misconceptions", []).extend(best.detail.get("misconceptions", []))
    return out


def recommendation_for(score: float | None) -> str:
    if score is None:
        return "lean_no"
    return next((label for limit, label in RECOMMENDATION_BANDS if score < limit), "strong_yes")


def build_scores(session: InterviewSession, grades: list[TopicGrade]) -> dict[str, Any]:
    spec = RolePackSpec.model_validate(session.pack_snapshot)
    weights = spec.role(session.role_variant).weights
    medians = _median_by_competency(grades)
    competencies = []
    flags: list[str] = []
    weighted, weight_total = 0.0, 0.0
    for competency_id, entry in medians.items():
        competency = spec.competency(competency_id)
        assessed = bool(entry["topic_scores"])
        score = round(statistics.fmean(entry["topic_scores"]), 1) if assessed else None
        needs_review = entry["spread"] >= VARIANCE_POINTS
        if needs_review:
            flags.append(competency_id)
        weight = float(weights.get(competency_id, 0.0))
        if score is not None and weight > 0:
            weighted += score * weight
            weight_total += weight
        competencies.append(
            {
                "id": competency_id,
                "name": competency.name if competency else competency_id,
                "weight": weight,
                "assessed": assessed,
                "score": score,
                "anchor_level": round(statistics.fmean(entry["levels"])) if entry["levels"] else None,
                "anchor": competency.anchors.get(round(statistics.fmean(entry["levels"])))
                if competency and entry["levels"]
                else None,
                "evidence": entry["evidence"][: EVIDENCE_IN_REPORT * 2],
                "misconceptions": entry.get("misconceptions", [])[:5],
                "runs": entry["runs"],
                "spread": round(entry["spread"], 1),
                "needs_human_review": needs_review,
            }
        )
    competencies.sort(key=lambda c: -float(c["weight"]))
    overall = round(weighted / weight_total, 1) if weight_total else None
    covered = sum(float(weights.get(c["id"], 0)) for c in competencies if c["assessed"])
    return {
        "overall_score": overall,
        "weight_covered": round(covered / (sum(weights.values()) or 1), 2),
        "competencies": competencies,
        "variance_flags": flags,
    }


def fallback_narrative(scores: dict[str, Any]) -> ScorecardNarrative:
    assessed = [c for c in scores["competencies"] if c["assessed"]]
    strengths = [f"{c['name']}: scored {c['score']}/10" for c in assessed if (c["score"] or 0) >= 7][:5]  # noqa: PLR2004
    concerns = [f"{c['name']}: scored {c['score']}/10" for c in assessed if (c["score"] or 0) < 5][:5]  # noqa: PLR2004
    untested = [c["name"] for c in scores["competencies"] if not c["assessed"]]
    overall = scores["overall_score"]
    return ScorecardNarrative(
        summary=(
            f"Automatic summary (no model was available to write one). Weighted score {overall}/10 across "
            f"{len(assessed)} assessed competencies."
            if overall is not None
            else "Automatic summary: there was not enough evidence to score this interview."
        ),
        claims_untested=[f"{name} was not assessed" for name in untested][:10],
        strengths=strengths,
        concerns=concerns,
        next_round_focus=[c["name"] for c in sorted(assessed, key=lambda c: c["score"] or 0)[:3]] + untested[:2],
        recommendation=recommendation_for(overall),
        recommendation_reason="Derived from the weighted competency scores only.",
    )


async def _narrative(
    deps: AgentDeps, db: AsyncSession, session: InterviewSession, scores: dict[str, Any], notes: list[str]
) -> tuple[ScorecardNarrative, str]:
    candidate = session.plan.get("candidate", {})
    graded = [
        {k: c[k] for k in ("id", "name", "score", "anchor_level", "evidence", "misconceptions")}
        for c in scores["competencies"]
    ]
    messages = composer.task_messages(
        "scorecard",
        org_name=await org_name(db, session.org_id),
        trusted={
            "role_title": session.persona.get("role_title", ""),
            "seniority": session.settings.get("seniority", "MID"),
            "variance_flags": ", ".join(scores["variance_flags"]) or "(none)",
        },
        untrusted={
            "graded_competencies": graded,
            "grader_notes": notes or "(none)",
            "reference_snippets": "(see the grades)",
            "cv_claims": candidate.get("evidence", {}) or "(no CV)",
        },
    )
    ctx = CallCtx(
        org_id=session.org_id,
        user_id=session.owner_user_id,
        role=AgentRole.GRADER,
        session_id=session.id,
        contains_pii=True,
    )
    try:
        return await llm(deps, db).complete_json(ctx, messages, ScorecardNarrative, temperature=0.2), "model"
    except LLM_FAILURES as exc:
        log.warning("interviews.scorecard_fallback", session_id=str(session.id), error=str(exc))
        return fallback_narrative(scores), "fallback"


async def _publish(deps: AgentDeps, session: InterviewSession, status: str) -> None:
    if deps.valkey is None:
        return
    await publish(
        deps.valkey,
        session.org_id,
        {
            "type": "interview.report",
            "user_id": str(session.owner_user_id),
            "session_id": str(session.id),
            "status": status,
        },
    )


async def finalize(deps: AgentDeps, db: AsyncSession, session: InterviewSession) -> Scorecard:
    runs = int(session.settings.get("grading_runs", 3))
    scorecard = await db.scalar(select(Scorecard).where(Scorecard.session_id == session.id))
    if scorecard is None:
        scorecard = Scorecard(
            org_id=session.org_id,
            owner_user_id=session.owner_user_id,
            session_id=session.id,
            status=ScorecardStatus.PENDING,
        )
        db.add(scorecard)
    scorecard.status, scorecard.error = ScorecardStatus.PENDING, None
    await db.commit()

    topics = await topics_with_answers(db, session)
    existing = list(await db.scalars(select(TopicGrade).where(TopicGrade.session_id == session.id)))
    have = {(g.topic_id, g.run_no) for g in existing}
    failures: list[str] = []
    for topic in topics:
        for run in range(runs):
            if (topic.id, run) in have:
                continue
            try:
                await grade_topic(deps, db, session, topic, run_no=run, temperature=0.1 if run == 0 else 0.3)
                await db.commit()
            except LLM_FAILURES as exc:
                await db.rollback()
                await db.refresh(session)
                await db.refresh(scorecard)
                failures.append(f"{topic.title}: {exc}")
                break  # later runs of this topic would fail the same way
    topic_ids = {t.id for t in topics}
    latest: dict[tuple[str, str, int], TopicGrade] = {}
    for g in await db.scalars(
        select(TopicGrade).where(TopicGrade.session_id == session.id).order_by(TopicGrade.created_at)
    ):
        if g.topic_id in topic_ids:  # a background run racing finalisation may leave a duplicate: keep the latest
            latest[(g.topic_id, g.competency, g.run_no)] = g
    grades = list(latest.values())
    if failures and not any(g.score is not None for g in grades):
        raise GradingUnavailableError(failures)
    scores = build_scores(session, grades)
    notes = [
        str((t.control or {}).get("note_for_grader"))
        for t in await db.scalars(
            select(InterviewTurn).where(
                InterviewTurn.session_id == session.id, InterviewTurn.speaker == Speaker.CANDIDATE
            )
        )
        if (t.control or {}).get("note_for_grader")
    ][:40]
    narrative, source = await _narrative(deps, db, session, scores, notes)
    events = list(await db.scalars(select(IntegrityEvent).where(IntegrityEvent.session_id == session.id)))
    scorecard.data = {
        "label": ADVISORY,
        **scores,
        "score_based_recommendation": recommendation_for(scores["overall_score"]),
        "narrative": narrative.model_dump(),
        "narrative_source": source,
        "integrity": integrity.summarise(events),  # informational; graders never saw it
        "topics": [
            {"id": t.id, "title": t.title, "section": t.section, "competencies": t.competencies} for t in topics
        ],
        "grading_problems": failures[:10],
        "prompt_versions": composer.versions("grader", "scorecard", "controller", "turn_single_call"),
    }
    scorecard.variance_flags = scores["variance_flags"]
    scorecard.recommendation = narrative.recommendation
    scorecard.status = ScorecardStatus.READY
    await db.commit()
    return scorecard


async def run_finalize(
    deps: AgentDeps, session_factory: async_sessionmaker[AsyncSession], args: Mapping[str, str]
) -> None:
    org_id, owner, session_id = uuid.UUID(args["org_id"]), uuid.UUID(args["user_id"]), uuid.UUID(args["session_id"])
    async with owner_session(session_factory, org_id, owner) as db:
        session = await db.get(InterviewSession, session_id)
        if session is None or session.status != SessionStatus.COMPLETED:
            return
        try:
            await finalize(deps, db, session)
            await _publish(deps, session, "READY")
        except Exception as exc:
            await db.rollback()
            if not isinstance(exc, (*LLM_FAILURES, GradingUnavailableError)):
                log.exception("interviews.finalize_crashed", session_id=str(session_id))
            scorecard = await db.scalar(select(Scorecard).where(Scorecard.session_id == session_id))
            if scorecard is not None:
                scorecard.status = ScorecardStatus.FAILED
                scorecard.error = (
                    f"Grading could not finish ({type(exc).__name__}). Use 'Grade again' on the report page."
                )
                await db.commit()
            refreshed = await db.get(InterviewSession, session_id)
            if refreshed is not None:
                await _publish(deps, refreshed, "FAILED")
