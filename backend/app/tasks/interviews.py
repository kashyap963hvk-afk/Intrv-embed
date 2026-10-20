"""Celery tasks for interviews: preparation (default queue), shadow grading and the scorecard (grade queue), and the
self-healing scan (beat)."""

from __future__ import annotations

from celery import current_app, shared_task


def _args(org_id: str, user_id: str, session_id: str, **extra: str) -> dict[str, str]:
    return {"org_id": org_id, "user_id": user_id, "session_id": session_id, **extra}


@shared_task(name="interviews.prepare", acks_late=True, max_retries=0, soft_time_limit=1500, time_limit=1600)
def prepare_interview(org_id: str, user_id: str, session_id: str) -> None:
    from app.interviews.service import run_prepare  # noqa: PLC0415 - keep the worker import graph light
    from workers.runtime import agent_deps, ingest_deps, run  # noqa: PLC0415

    run(run_prepare(agent_deps(), ingest_deps().session_factory, _args(org_id, user_id, session_id)))


@shared_task(name="grade.topic", acks_late=True, max_retries=0, soft_time_limit=600, time_limit=660)
def grade_topic(org_id: str, user_id: str, session_id: str, topic_id: str) -> None:
    from app.interviews.grading import run_grade_topic  # noqa: PLC0415
    from workers.runtime import agent_deps, ingest_deps, run  # noqa: PLC0415

    run(
        run_grade_topic(
            agent_deps(), ingest_deps().session_factory, _args(org_id, user_id, session_id, topic_id=topic_id)
        )
    )


@shared_task(name="grade.finalize", acks_late=True, max_retries=0, soft_time_limit=1800, time_limit=1900)
def finalize_interview(org_id: str, user_id: str, session_id: str) -> None:
    from app.interviews.grading import run_finalize  # noqa: PLC0415
    from workers.runtime import agent_deps, ingest_deps, run  # noqa: PLC0415

    run(run_finalize(agent_deps(), ingest_deps().session_factory, _args(org_id, user_id, session_id)))


@shared_task(name="system.heal", acks_late=False, max_retries=0, soft_time_limit=240, time_limit=270)
def heal_stuck_work() -> dict[str, int]:
    """Repair work that crashed or restarted workers left behind (see app.healing)."""
    from app.core.jobs import CeleryJobs  # noqa: PLC0415
    from app.healing import heal  # noqa: PLC0415
    from app.interviews.engine import EngineDeps, InterviewEngine  # noqa: PLC0415
    from app.knowledge.dispatch import CeleryDispatcher  # noqa: PLC0415
    from workers.runtime import agent_deps, ingest_deps, run  # noqa: PLC0415

    factory = ingest_deps().session_factory
    jobs = CeleryJobs(current_app)
    engine = InterviewEngine(EngineDeps(agent_deps(), factory, jobs))
    return run(heal(factory, jobs=jobs, ingest=CeleryDispatcher(current_app), engine=engine))
