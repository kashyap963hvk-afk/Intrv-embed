"""Code Reviewer agent: compares a submission with the task's known bugs and test results. Quotes are checked
against what the candidate actually wrote; the reviewer never sees the candidate's identity."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import composer
from app.agents.runtime import AgentDeps, llm
from app.llm.types import CallCtx
from app.practical.piston import RunReport
from app.rolepacks.schema import PracticalTask

_SPACE = re.compile(r"\s+")


class FoundBug(BaseModel):
    bug: int = Field(ge=1)
    quote: str = Field(min_length=2, max_length=600)


class CodeReview(BaseModel):
    found: list[FoundBug] = Field(default_factory=list)
    missed: list[int] = Field(default_factory=list)
    new_bugs: list[str] = Field(default_factory=list, max_length=10)
    fix_correct: bool | None = None
    reasoning: Literal["low", "medium", "high"] = "medium"
    summary: str = Field(default="", max_length=800)


def _norm(text: str) -> str:
    return _SPACE.sub(" ", text).strip().lower()


def verify(review: CodeReview, task: PracticalTask, code: str, explanation: str) -> CodeReview:
    """Keep only found-bug claims whose quote is really in the submission; recompute missed from that."""
    haystack = _norm(f"{code}\n{explanation}")
    total = len(task.known_bugs)
    found = [f for f in review.found if 1 <= f.bug <= total and _norm(f.quote.strip("\"'`")) in haystack]
    unique = {f.bug: f for f in found}
    missed = [n for n in range(1, total + 1) if n not in unique]
    return review.model_copy(update={"found": list(unique.values()), "missed": missed})


async def review_submission(
    deps: AgentDeps,
    db: AsyncSession,
    ctx: CallCtx,
    *,
    org_name: str,
    task: PracticalTask,
    code: str,
    explanation: str,
    report: RunReport | None,
) -> CodeReview:
    tests = (
        "\n".join(
            f"{'PASS' if t.passed else 'FAIL'} stdin={t.stdin!r} expected={t.expected!r} got={t.actual!r}"
            for t in report.tests
        )
        if report and report.tests
        else "(no automated tests for this task)"
    )
    messages = composer.task_messages(
        "code_reviewer",
        org_name=org_name,
        trusted={
            "task_id": task.id,
            "task_kind": task.kind.value,
            "known_bugs": "\n".join(f"{i}. {bug}" for i, bug in enumerate(task.known_bugs, start=1)) or "(none)",
        },
        untrusted={
            "task_prompt": task.prompt,
            "original_code": task.starter_code or "(none)",
            "diagnostics": "\n".join([*(report.errors if report else []), *(report.warnings if report else [])])
            or "(none)",
            "test_results": tests,
            "candidate_code": code or "(no code)",
            "explanation": explanation or "(no explanation)",
        },
    )
    review = await llm(deps, db).complete_json(ctx, messages, CodeReview, temperature=0.0)
    return verify(review, task, code, explanation)
