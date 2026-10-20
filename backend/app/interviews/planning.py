"""Interview preparation: section structure, the planner agent (with a deterministic fallback so an interview can
always start), and grounded context packs per technical topic."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import composer
from app.agents.runtime import AgentDeps, llm, org_name, search
from app.core.logging import get_logger
from app.db.models import AgentRole, QuestionItem
from app.interviews.schemas import InterviewPlan, InterviewSettings, PlanTopic
from app.llm.types import CallCtx, LLMUnavailableError, StructuredOutputError
from app.questions.plan import is_behavioral
from app.rolepacks.schema import RolePackSpec, RoleVariant

log = get_logger("app.interviews")

TOPIC_SECTIONS = ("CV_WALKTHROUGH", "TECHNICAL", "BEHAVIORAL")
SECTION_ORDER = ("OPENING", "CV_WALKTHROUGH", "TECHNICAL", "PRACTICAL", "BEHAVIORAL", "CANDIDATE_QUESTIONS", "CLOSE")
PRACTICAL_SEED = (
    "Let's do a short practical exercise now. The task is on the Practical tab: take your time, run it as often as "
    "you like, and when you submit, walk me through what you found and why. Shall we start?"
)
MINUTES_PER_TOPIC = {"CV_WALKTHROUGH": 5.0, "TECHNICAL": 6.0, "BEHAVIORAL": 4.0}
DEFAULT_STRUCTURE = [
    {"section": "OPENING", "minutes": 2},
    {"section": "CV_WALKTHROUGH", "minutes": 6},
    {"section": "TECHNICAL", "minutes": 25},
    {"section": "BEHAVIORAL", "minutes": 6},
    {"section": "CANDIDATE_QUESTIONS", "minutes": 4},
    {"section": "CLOSE", "minutes": 2},
]
CONTEXT_CHARS = 2500
# Reference packs never include other candidates' CVs or job descriptions.
REFERENCE_DOC_TYPES = ("KNOWLEDGE", "DATASHEET", "CODE", "RUBRIC", "QUESTION_BANK", "OTHER")


def structure_for(settings: InterviewSettings, role: RoleVariant) -> list[dict[str, Any]]:
    """Sections with minutes scaled to the interview's duration. Without practical tasks, PRACTICAL time goes to
    TECHNICAL."""
    raw: list[dict[str, Any]] = (
        list(settings.structure)
        if settings.structure
        else [s.model_dump(mode="json") for s in role.default_structure] or list(DEFAULT_STRUCTURE)
    )
    practical = bool(settings.practical_tasks)
    merged: dict[str, float] = {}
    for step in raw:
        section = str(step["section"])
        if section == "PRACTICAL" and not practical:
            section = "TECHNICAL"
        merged[section] = merged.get(section, 0.0) + float(step["minutes"])
    if practical and "PRACTICAL" not in merged:
        merged["PRACTICAL"] = 12.0 * len(settings.practical_tasks)
    total = sum(merged.values()) or 1.0
    scale = settings.duration_minutes / total
    return [{"section": s, "minutes": round(merged[s] * scale, 1)} for s in SECTION_ORDER if s in merged]


def _topic_count(structure: list[dict[str, Any]], section: str) -> int:
    minutes = next((float(s["minutes"]) for s in structure if s["section"] == section), 0.0)
    return max(1, round(minutes / MINUTES_PER_TOPIC[section])) if minutes > 0 else 0


def fallback_plan(
    spec: RolePackSpec,
    role: RoleVariant,
    structure: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    items: list[dict[str, Any]],
) -> InterviewPlan:
    """A complete plan without any model call: question-set items first, then the role pack's question bank."""
    topics: list[PlanTopic] = []
    projects = (profile or {}).get("projects", [])
    roles = (profile or {}).get("roles", [])
    if _topic_count(structure, "CV_WALKTHROUGH"):
        anchor = (
            f"the {projects[0].get('name')} project"
            if projects
            else f"your work as {roles[0].get('title')} at {roles[0].get('company')}"
            if roles
            else "the project you are proudest of"
        )
        topics.append(
            PlanTopic(
                title="CV walkthrough",
                section="CV_WALKTHROUGH",
                competencies=[max(role.weights, key=role.weights.__getitem__)],
                source={"type": "cv_claim", "ref": "projects[0]" if projects else "roles[0]" if roles else ""},
                seed_question=f"Could you walk me through {anchor}, and what you personally owned in it?",
                probe_angles=[
                    "What exactly did you build yourself?",
                    "What was the hardest problem?",
                    "What would you change now?",
                ],
                expected_signals=["clear personal ownership", "specific technical decisions", "measurable outcome"],
                planned_minutes=float(next(s["minutes"] for s in structure if s["section"] == "CV_WALKTHROUGH")),
            )
        )
    technical_needed = _topic_count(structure, "TECHNICAL")
    used: set[str] = set()
    for item in items:
        if technical_needed <= 0:
            break
        if item.get("category") == "behavioral" or item["text"] in used:
            continue
        used.add(item["text"])
        technical_needed -= 1
        topics.append(
            PlanTopic(
                title=item["text"][:80],
                section="TECHNICAL",
                competencies=[item.get("competency", "")],
                source={"type": "question_set", "ref": ",".join(item.get("evidence_refs", [])[:2])},
                rationale=item.get("rationale", ""),
                seed_question=item["text"],
                probe_angles=item.get("follow_ups", []),
                expected_signals=item.get("expected_signals", []),
                red_flags=item.get("red_flags", []),
            )
        )
    by_weight = sorted((c for c in role.weights if not is_behavioral(spec, c)), key=lambda c: -role.weights[c])
    for competency_id in by_weight:
        if technical_needed <= 0:
            break
        competency = spec.competency(competency_id)
        seed = next((q for q in (competency.seed_questions if competency else []) if q.text not in used), None)
        if competency is None or seed is None:
            continue
        used.add(seed.text)
        technical_needed -= 1
        topics.append(
            PlanTopic(
                title=competency.name,
                section="TECHNICAL",
                competencies=[competency_id],
                source={"type": "rubric", "ref": competency_id},
                seed_question=seed.text,
                probe_angles=seed.follow_ups or competency.probe_ladder,
                expected_signals=seed.expected_signals or competency.signals[:6],
                red_flags=competency.red_flags[:5],
            )
        )
    for behavioral in spec.behavioral[: _topic_count(structure, "BEHAVIORAL")]:
        owner = spec.competency(behavioral.competency_id)
        topics.append(
            PlanTopic(
                title=owner.name if owner else "Behavioral",
                section="BEHAVIORAL",
                competencies=[behavioral.competency_id],
                source={"type": "rubric", "ref": behavioral.competency_id},
                seed_question=behavioral.question,
                probe_angles=["What did you personally do?", "What was the result?", "What would you do differently?"],
                expected_signals=behavioral.star_signals,
            )
        )
    return InterviewPlan(topics=topics)


def normalise_plan(
    plan: InterviewPlan, spec: RolePackSpec, role: RoleVariant, structure: list[dict[str, Any]]
) -> InterviewPlan:
    # Topics only target competencies this role variant weights; others would never count towards the score.
    known = {c.id for c in spec.competencies if role.weights.get(c.id, 0) > 0}
    sections = {s["section"] for s in structure}
    topics: list[PlanTopic] = []
    for topic in plan.topics:
        if topic.section == "PRACTICAL":
            continue  # practical topics come from the role pack's tasks, never from the planner
        section = topic.section
        if section not in sections:
            continue
        competencies = [c for c in topic.competencies if c in known] or [
            max(role.weights, key=role.weights.__getitem__)
        ]
        topics.append(topic.model_copy(update={"section": section, "competencies": competencies}))
    topics.sort(key=lambda t: SECTION_ORDER.index(t.section))  # stable: keeps the planner's order inside a section
    for index, topic in enumerate(topics, start=1):
        topic.id = f"t{index}"
    return InterviewPlan(topics=topics) if topics else plan


def with_practical(
    plan: InterviewPlan,
    spec: RolePackSpec,
    role: RoleVariant,
    settings: InterviewSettings,
    structure: list[dict[str, Any]],
    available: set[str],
) -> InterviewPlan:
    """Insert one PRACTICAL topic per chosen task (those that can run here), in section order, and renumber."""
    minutes = next((float(s["minutes"]) for s in structure if s["section"] == "PRACTICAL"), 0.0)
    chosen = [t for t in spec.practical_tasks if t.id in settings.practical_tasks and t.id in available]
    topics = [t for t in plan.topics if t.section != "PRACTICAL"]
    for task in chosen:
        topics.append(
            PlanTopic(
                title=f"Practical: {task.id.replace('_', ' ')}",
                section="PRACTICAL",
                competencies=[c for c in task.rubric_competencies if role.weights.get(c, 0) > 0]
                or [max(role.weights, key=role.weights.__getitem__)],
                source={"type": "rubric", "ref": task.id},
                seed_question=PRACTICAL_SEED,
                probe_angles=[
                    "What was the first thing you noticed when you read the code?",
                    "How would you confirm that on real hardware?",
                    "What would you change if this ran on a multi-core MCU?",
                ],
                expected_signals=task.known_bugs[:6],
                planned_minutes=max(1.0, minutes / max(1, len(chosen))),
                task_id=task.id,
            )
        )
    topics.sort(key=lambda t: SECTION_ORDER.index(t.section))
    for index, topic in enumerate(topics, start=1):
        topic.id = f"t{index}"
    return InterviewPlan(topics=topics)


async def plan_interview(
    deps: AgentDeps,
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    spec: RolePackSpec,
    role: RoleVariant,
    settings: InterviewSettings,
    seniority: str,
    profile: dict[str, Any] | None,
    evidence_index: dict[str, str],
    gap: dict[str, Any] | None,
    question_items: list[QuestionItem],
) -> tuple[InterviewPlan, list[dict[str, Any]], str]:
    """Returns (plan, structure, source) where source is "planner" or "fallback"."""
    structure = structure_for(settings, role)
    items = [i.data for i in question_items]
    competencies = [
        {
            "id": c.id,
            "name": c.name,
            "weight": role.weights.get(c.id, 0),
            "signals": c.signals[:8],
            "red_flags": c.red_flags[:5],
        }
        for c in spec.competencies
        if c.id in role.weights
    ]
    messages = composer.task_messages(
        "planner",
        org_name=await org_name(db, org_id),
        trusted={
            "total_minutes": settings.duration_minutes,
            "role_variant_name": role.name,
            "seniority": seniority,
            "structure": ", ".join(f"{s['section']} {s['minutes']} min" for s in structure),
        },
        untrusted={
            "candidate_profile_json": {
                "evidence": evidence_index,
                "vague_claims": (profile or {}).get("vague_claims", []),
            },
            "jd_gap_matrix": gap or "(no job description)",
            "role_pack_competencies": competencies,
            "question_set_items": [
                {k: i.get(k) for k in ("text", "competency", "category", "rationale")} for i in items
            ]
            or "(none)",
            "reference_snippets": "(retrieved per topic after planning)",
        },
    )
    ctx = CallCtx(org_id=org_id, user_id=user_id, role=AgentRole.PLANNER, contains_pii=True)
    try:
        planned = normalise_plan(
            await llm(deps, db).complete_json(ctx, messages, InterviewPlan, temperature=0.3), spec, role, structure
        )
    except (LLMUnavailableError, StructuredOutputError) as exc:
        log.warning("interviews.planner_fallback", error=str(exc))
        planned = None
    if planned is not None and any(t.section == "TECHNICAL" for t in planned.topics):
        return planned, structure, "planner"
    # Self-healing: the interview still starts with a sound plan built from the question set and role pack.
    plan = normalise_plan(fallback_plan(spec, role, structure, profile, items), spec, role, structure)
    return plan, structure, "fallback"


async def context_packs(
    deps: AgentDeps,
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    plan: InterviewPlan,
    space_ids: list[uuid.UUID],
) -> dict[str, list[dict[str, str]]]:
    """Reference snippets per technical topic, retrieved once at preparation (the session's ACL snapshot)."""
    packs: dict[str, list[dict[str, str]]] = {}
    for topic in plan.topics:
        if topic.section != "TECHNICAL" or not space_ids:
            continue
        chunks = await search(
            deps,
            db,
            org_id=org_id,
            user_id=user_id,
            query=f"{topic.title}. {' '.join(topic.expected_signals[:6])}",
            space_ids=space_ids,
            k=3,
            doc_types=REFERENCE_DOC_TYPES,
        )
        budget, pack = CONTEXT_CHARS, []
        for chunk in chunks:
            text = chunk.text[: max(0, budget)]
            if not text:
                break
            budget -= len(text)
            pack.append({"document_id": str(chunk.document_id), "title": chunk.citation.title, "text": text})
        packs[topic.id] = pack
    return packs


async def practical_availability(
    deps: AgentDeps,
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    spec: RolePackSpec,
    settings: InterviewSettings,
    space_ids: list[uuid.UUID],
    code_runner: bool,
) -> tuple[set[str], dict[str, Any], list[str]]:
    """Which chosen tasks can run here: code needs the runner, datasheet reading needs a datasheet the session
    may read. Returns (task ids, per-task context, notes for the interviewer's progress line)."""
    available: set[str] = set()
    context: dict[str, Any] = {}
    notes: list[str] = []
    for task in spec.practical_tasks:
        if task.id not in settings.practical_tasks:
            continue
        if task.kind.value in {"DEBUG_SNIPPET", "WRITE_CODE"} and not code_runner:
            notes.append(f"{task.id}: skipped (no code runner configured)")
            continue
        if task.kind.value == "DATASHEET_READING":
            chunks = (
                await search(
                    deps,
                    db,
                    org_id=org_id,
                    user_id=user_id,
                    query=task.prompt,
                    space_ids=space_ids,
                    k=2,
                    doc_types=("DATASHEET",),
                )
                if space_ids
                else []
            )
            if not chunks:
                notes.append(f"{task.id}: skipped (no datasheet in the reference spaces)")
                continue
            context[task.id] = {
                "reference": [
                    {"document_id": str(c.document_id), "title": c.citation.title, "text": c.text[:CONTEXT_CHARS]}
                    for c in chunks
                ]
            }
        available.add(task.id)
    return available, context, notes
