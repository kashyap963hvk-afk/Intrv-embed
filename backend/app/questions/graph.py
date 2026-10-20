"""question_gen_graph (ARCHITECTURE §12): plan → per-competency grounded generation (parallel) → critic → dedupe →
top-up from the role pack's question bank.

Each node opens its own tenant-scoped session (parallel branches never share one), and every model output is
re-checked in code: competencies and categories are forced to the planned slot, evidence references must exist
in the CV evidence index, and citations must be documents that were actually retrieved.
"""

from __future__ import annotations

import operator
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import composer
from app.agents.runtime import AgentDeps, llm, org_name, search, snippets
from app.agents.schemas import CriticResult, GeneratedQuestion, GeneratedQuestions
from app.core.logging import get_logger
from app.db.models import AgentRole
from app.db.tenant import TenantContext, set_tenant_context
from app.llm.types import CallCtx, LLMUnavailableError, StructuredOutputError
from app.questions.plan import Slot, is_behavioral, plan_slots
from app.rolepacks.schema import Competency, RolePackSpec

log = get_logger("app.questions")

DUPLICATE_SIMILARITY = 0.7
_WORD = re.compile(r"[a-z0-9_]+")

Progress = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class GenContext:
    deps: AgentDeps
    session_factory: async_sessionmaker[AsyncSession]
    org_id: uuid.UUID
    user_id: uuid.UUID
    role_name: str  # the user's role (tenant context)
    progress: Progress


class GenState(TypedDict, total=False):
    spec: dict[str, Any]
    variant_id: str
    seniority: str
    count: int
    difficulty: int | None
    mix: dict[str, float] | None
    space_ids: list[str] | None
    profile: dict[str, Any]
    index: dict[str, str]
    gap: dict[str, Any] | None
    slots: list[dict[str, Any]]
    batch: dict[str, Any]
    generated: Annotated[list[dict[str, Any]], operator.add]
    notes: Annotated[list[str], operator.add]
    final: list[dict[str, Any]]
    critic_summary: dict[str, int]


async def _session(ctx: GenContext) -> AsyncSession:
    db = ctx.session_factory()
    await set_tenant_context(db, TenantContext(org_id=ctx.org_id, user_id=ctx.user_id, role=ctx.role_name))
    return db


def _competency_brief(competency: Competency) -> dict[str, Any]:
    return {
        "id": competency.id,
        "name": competency.name,
        "description": competency.description,
        "anchors": competency.anchors,
        "signals": competency.signals,
        "red_flags": competency.red_flags,
        "probe_ladder": competency.probe_ladder,
        "example_questions": [q.text for q in competency.seed_questions],
    }


# --- nodes ---------------------------------------------------------------------------------------------------


async def plan(state: GenState, runtime: Runtime[GenContext]) -> GenState:
    spec = RolePackSpec.model_validate(state["spec"])
    slots = plan_slots(
        spec,
        spec.role(state["variant_id"]),
        count=state["count"],
        seniority=state["seniority"],
        difficulty=state.get("difficulty"),
        mix=state.get("mix"),
        profile=state["profile"],
        gap=state.get("gap"),
    )
    await runtime.context.progress(
        f"Planned {len(slots)} questions across {len({s.competency for s in slots})} competencies"
    )
    return {"slots": [s.as_json() for s in slots]}


def fan_out(state: GenState) -> list[Send]:
    by_competency: dict[str, list[dict[str, Any]]] = {}
    for position, slot in enumerate(state["slots"]):
        by_competency.setdefault(slot["competency"], []).append({**slot, "position": position})
    shared = {k: state.get(k) for k in ("spec", "variant_id", "seniority", "index", "gap", "space_ids", "profile")}
    return [Send("generate", {**shared, "batch": {"competency": c, "slots": s}}) for c, s in by_competency.items()]


def _seed_fallback(competency: Competency | None, slots: list[dict[str, Any]], used: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for slot in slots:
        seed = next((q for q in (competency.seed_questions if competency else []) if q.text not in used), None)
        if seed is None:
            continue
        used.add(seed.text)
        out.append(
            {
                "position": slot.get("position", 0),
                **GeneratedQuestion(
                    text=seed.text,
                    competency=slot["competency"],
                    category=slot["category"],
                    difficulty=seed.difficulty,
                    rationale="From the role pack's question bank (no CV-specific variant was generated).",
                    expected_signals=seed.expected_signals,
                    follow_ups=seed.follow_ups,
                ).model_dump(),
                "source": "role_pack",
            }
        )
    return out


async def generate(state: GenState, runtime: Runtime[GenContext]) -> GenState:
    ctx = runtime.context
    spec = RolePackSpec.model_validate(state["spec"])
    batch = state["batch"]
    competency = spec.competency(batch["competency"])
    slots: list[dict[str, Any]] = batch["slots"]
    index: dict[str, str] = state["index"]
    behavioral = is_behavioral(spec, batch["competency"])
    db = await _session(ctx)
    try:
        signals = " ".join((competency.signals if competency else [])[:6])
        query = f"{competency.name if competency else batch['competency']}: {signals}"
        chosen = state.get("space_ids")
        references = (
            []
            if behavioral
            else await search(
                ctx.deps,
                db,
                org_id=ctx.org_id,
                user_id=ctx.user_id,
                query=query,
                space_ids=[uuid.UUID(s) for s in chosen] if chosen else None,
                k=4,
            )
        )
        gap_rows = [
            r for r in (state.get("gap") or {}).get("requirements", []) if r.get("competency_id") == batch["competency"]
        ]
        slot_lines = "\n".join(
            f"{i + 1}. competency={s['competency']} category={s['category']} difficulty={s['difficulty']}"
            for i, s in enumerate(slots)
        )
        messages = composer.task_messages(
            "question_gen",
            org_name=await org_name(db, ctx.org_id),
            trusted={
                "count": len(slots),
                "role_variant_name": spec.role(state["variant_id"]).name,
                "seniority": state["seniority"],
                "slots": slot_lines,
            },
            untrusted={
                "cv_evidence_index": index,
                "competency_specs": _competency_brief(competency) if competency else batch["competency"],
                "jd_gap_matrix": {"requirements": gap_rows}
                if gap_rows
                else "(no job description gaps for this competency)",
                "reference_snippets": snippets(references),
            },
        )
        call = CallCtx(org_id=ctx.org_id, user_id=ctx.user_id, role=AgentRole.QUESTION_GEN, contains_pii=True)
        retrieved = {str(c.document_id) for c in references}
        items: list[dict[str, Any]] = []
        notes: list[str] = []
        try:
            result = await llm(ctx.deps, db).complete_json(call, messages, GeneratedQuestions, temperature=0.4)
            for slot, question in zip(slots, result.questions, strict=False):
                question.competency, question.category = slot["competency"], slot["category"]
                question.evidence_refs = [r for r in question.evidence_refs if r in index]
                question.citations = [c for c in question.citations if c in retrieved]
                items.append({**question.model_dump(), "source": "generated", "position": slot["position"]})
        except (LLMUnavailableError, StructuredOutputError) as exc:
            notes.append(f"{batch['competency']}: generation failed ({exc}); used the role pack's questions")
            log.warning("questions.generate_failed", competency=batch["competency"], error=str(exc))
        await db.commit()
    finally:
        await db.close()
    if len(items) < len(slots):
        items += _seed_fallback(competency, slots[len(items) :], {i["text"] for i in items})
    await ctx.progress(f"Wrote questions for {competency.name if competency else batch['competency']}")
    return {"generated": items, "notes": notes}


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def similarity(a: str, b: str) -> float:
    left, right = _words(a), _words(b)
    return len(left & right) / max(1, len(left | right))


def dedupe(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    for item in items:
        if any(similarity(item["text"], other["text"]) >= DUPLICATE_SIMILARITY for other in kept):
            continue
        kept.append(item)
    return kept, len(items) - len(kept)


async def critic(state: GenState, runtime: Runtime[GenContext]) -> GenState:
    ctx = runtime.context
    # Parallel branches finish in any order; review and present questions in plan order.
    items = sorted(state.get("generated", []), key=lambda item: item.get("position", 0))
    summary = {"kept": 0, "rewritten": 0, "dropped": 0, "duplicates": 0, "topped_up": 0}
    notes: list[str] = []
    await ctx.progress("Reviewing questions")
    if items:
        db = await _session(ctx)
        try:
            messages = composer.task_messages(
                "question_critic",
                org_name=await org_name(db, ctx.org_id),
                untrusted={
                    "questions_json": [{"index": i, **q} for i, q in enumerate(items)],
                    "cv_evidence_index": state["index"],
                    "reference_snippets": "(see citations on each question)",
                },
            )
            call = CallCtx(org_id=ctx.org_id, user_id=ctx.user_id, role=AgentRole.QUESTION_GEN, contains_pii=True)
            verdicts = await llm(ctx.deps, db).complete_json(call, messages, CriticResult, temperature=0.0)
            by_index = {v.index: v for v in verdicts.verdicts if 0 <= v.index < len(items)}
            reviewed: list[dict[str, Any]] = []
            for i, item in enumerate(items):
                verdict = by_index.get(i)
                if verdict is None or verdict.action == "keep":
                    reviewed.append(item)
                    summary["kept"] += 1
                elif verdict.action == "drop":
                    summary["dropped"] += 1
                elif verdict.rewritten is not None:
                    rewritten = verdict.rewritten.model_dump()
                    rewritten.update(competency=item["competency"], category=item["category"])
                    rewritten["evidence_refs"] = [r for r in rewritten["evidence_refs"] if r in state["index"]]
                    rewritten["citations"] = [c for c in rewritten["citations"] if c in item.get("citations", [])]
                    reviewed.append({**rewritten, "source": item.get("source", "generated"), "critic": verdict.reason})
                    summary["rewritten"] += 1
                else:
                    reviewed.append(item)
                    summary["kept"] += 1
            items = reviewed
            await db.commit()
        except (LLMUnavailableError, StructuredOutputError) as exc:
            notes.append(f"critic skipped: {exc}")
        finally:
            await db.close()
    items, summary["duplicates"] = dedupe(items)

    # Top up from the question bank if the critic or dedupe left the set short.
    spec = RolePackSpec.model_validate(state["spec"])
    missing = state["count"] - len(items)
    if missing > 0:
        used = {i["text"] for i in items}
        planned = [Slot(**s) for s in state["slots"]]
        for slot in planned:
            if missing <= 0:
                break
            extra = _seed_fallback(spec.competency(slot.competency), [{**slot.as_json(), "position": 10_000}], used)
            if extra:
                items += extra
                missing -= 1
                summary["topped_up"] += 1
    final = [{k: v for k, v in item.items() if k != "position"} for item in items[: state["count"]]]
    return {"final": final, "critic_summary": summary, "notes": notes}


def build_graph() -> Any:
    graph = StateGraph(GenState, context_schema=GenContext)
    graph.add_node("plan", plan)
    graph.add_node("generate", generate)
    graph.add_node("critic", critic)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", fan_out, ["generate"])
    graph.add_edge("generate", "critic")
    graph.add_edge("critic", END)
    return graph.compile()


QUESTION_GRAPH = build_graph()
