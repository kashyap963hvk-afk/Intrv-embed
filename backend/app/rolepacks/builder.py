"""Role Builder agent: a plain-language role description (optionally grounded in KB spaces) → a draft Role Pack.

The draft is validated; weights are normalised when the model's numbers do not quite sum to 1, and any remaining
problems are returned with the draft so the user can fix them in the editor instead of losing the work.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import json_repair
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import composer
from app.agents.runtime import AgentDeps, llm, org_name, search, snippets
from app.db.models import AgentRole
from app.llm.json_output import extract_json_text, schema_instruction
from app.llm.types import CallCtx
from app.rolepacks.schema import RolePackSpec, SpecProblem, validate_spec


class Draft(BaseModel):
    spec: dict[str, Any]
    valid: bool
    problems: list[SpecProblem]


def normalise(data: dict[str, Any]) -> dict[str, Any]:
    """Small, safe repairs: weights rescaled to sum to 1 and anchors keyed 1..4."""
    for role in data.get("roles", []) or []:
        weights = role.get("weights")
        if (
            isinstance(weights, dict)
            and weights
            and all(isinstance(v, int | float) and v > 0 for v in weights.values())
        ):
            total = float(sum(weights.values()))
            role["weights"] = {k: round(v / total, 4) for k, v in weights.items()}
    for competency in data.get("competencies", []) or []:
        anchors = competency.get("anchors")
        if isinstance(anchors, dict):
            competency["anchors"] = {int(k): v for k, v in anchors.items() if str(k).isdigit()}
        elif isinstance(anchors, list) and len(anchors) == 4:  # noqa: PLR2004 - four anchored levels
            competency["anchors"] = {i + 1: str(v) for i, v in enumerate(anchors)}
    return data


async def build_draft(
    deps: AgentDeps,
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    description: str,
    space_ids: list[uuid.UUID] | None,
) -> Draft:
    references = await search(deps, db, org_id=org_id, user_id=user_id, query=description, space_ids=space_ids, k=6)
    messages = composer.task_messages(
        "role_builder",
        org_name=await org_name(db, org_id),
        trusted={"role_description": description.replace("\n", " ")[:1500]},
        untrusted={"reference_snippets": snippets(references)},
        user=schema_instruction(RolePackSpec),
    )
    service = llm(deps, db)
    ctx = CallCtx(org_id=org_id, user_id=user_id, role=AgentRole.ROLE_BUILDER)
    data: dict[str, Any] = {}
    problems: list[SpecProblem] = []
    for _ in range(2):
        completion = await service.complete(ctx, messages, temperature=0.3, response_format={"type": "json_object"})
        parsed = json_repair.loads(extract_json_text(completion.text))
        data = normalise(parsed if isinstance(parsed, dict) else {})
        spec, problems = validate_spec(data)
        if spec is not None:
            return Draft(spec=spec.model_dump(mode="json"), valid=True, problems=[])
        fixes = "\n".join(f"- {p.path}: {p.message}" for p in problems[:15])
        messages = [
            *messages,
            {"role": "assistant", "content": json.dumps(data)[:12000]},
            {"role": "user", "content": f"Fix these problems and return the complete corrected JSON:\n{fixes}"},
        ]
    return Draft(spec=data, valid=False, problems=problems)


class PackDiff(BaseModel):
    added_competencies: list[str]
    removed_competencies: list[str]
    changed_competencies: list[str]
    added_roles: list[str]
    removed_roles: list[str]
    weight_changes: dict[str, dict[str, tuple[float | None, float | None]]]


def diff(base: RolePackSpec, new: RolePackSpec) -> PackDiff:
    old_c = {c.id: c for c in base.competencies}
    new_c = {c.id: c for c in new.competencies}
    old_r = {r.id: r for r in base.roles}
    new_r = {r.id: r for r in new.roles}
    weights: dict[str, dict[str, tuple[float | None, float | None]]] = {}
    for role_id in old_r.keys() & new_r.keys():
        before, after = old_r[role_id].weights, new_r[role_id].weights
        changed = {
            k: (before.get(k), after.get(k))
            for k in before.keys() | after.keys()
            if abs((before.get(k) or 0.0) - (after.get(k) or 0.0)) > 1e-9  # noqa: PLR2004
        }
        if changed:
            weights[role_id] = changed
    return PackDiff(
        added_competencies=sorted(new_c.keys() - old_c.keys()),
        removed_competencies=sorted(old_c.keys() - new_c.keys()),
        changed_competencies=sorted(k for k in old_c.keys() & new_c.keys() if old_c[k] != new_c[k]),
        added_roles=sorted(new_r.keys() - old_r.keys()),
        removed_roles=sorted(old_r.keys() - new_r.keys()),
        weight_changes=weights,
    )
