"""Celery task: generate a question set in the background (progress is published live to its owner)."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="questions.generate", acks_late=True, max_retries=0, soft_time_limit=1500, time_limit=1600)
def generate_question_set(org_id: str, user_id: str, set_id: str) -> None:
    from app.questions.service import run_generation  # noqa: PLC0415 - keep the worker import graph light
    from workers.runtime import agent_deps, ingest_deps, run  # noqa: PLC0415

    run(
        run_generation(
            agent_deps(), ingest_deps().session_factory, {"org_id": org_id, "user_id": user_id, "set_id": set_id}
        )
    )
