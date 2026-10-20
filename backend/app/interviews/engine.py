"""Live text interview engine (ARCHITECTURE §13).

The interview is an explicit state machine persisted in `interview_sessions.state` (ADR 0007): every candidate
answer is one transaction that stores the answer, the interviewer's reply and the new state together, so a dropped
connection or a crashed worker never leaves a half-applied turn. The client simply resends an answer that was not
acknowledged.

Per turn: integrity signals for the answer → controller (single streamed call with a `<control>` block, or a
separate structured call) → deterministic policy (time, depth, hints, prompt attacks, integrity probes) → the
interviewer's reply, streamed sentence by sentence through `SentenceGate` (one question, word limit, no praise).

Self-healing: when no model answers, the controller falls back to heuristics and the interviewer falls back to the
plan's own questions, so an interview never stalls; a reply without a question gets the planned one appended;
single-call mode switches itself to two calls after repeated malformed control blocks.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import composer
from app.agents.runtime import AgentDeps, llm, org_name
from app.core.jobs import JobDispatcher
from app.core.logging import get_logger
from app.db.models import (
    AgentRole,
    IntegrityEvent,
    InterviewSession,
    InterviewTurn,
    Organization,
    QualityTier,
    SessionStatus,
    Speaker,
    User,
)
from app.db.tenant import TenantContext, set_tenant_context
from app.interviews import integrity, policy, speech
from app.interviews.persona import render_persona
from app.interviews.planning import fallback_plan, structure_for
from app.interviews.schemas import InterviewPlan, InterviewSettings, PlanTopic, TurnControl
from app.llm.service import PolicyViolationError
from app.llm.types import CallCtx, LLMUnavailableError, Message, ProviderError, StructuredOutputError
from app.orgs.policy import OrgPolicy, org_policy
from app.rag.risk import injection_matches
from app.rolepacks.schema import RolePackSpec, RoleVariant

log = get_logger("app.interviews")

GRADE_JOB = "grade.topic"
FINALIZE_JOB = "grade.finalize"
LLM_FAILURES = (LLMUnavailableError, StructuredOutputError, PolicyViolationError, ProviderError, TimeoutError)
LOCK_MS = 180_000
HISTORY_TURNS = 16
MAX_ANSWER_CHARS = 6000
RESUME_GRACE_S = 10
SINGLE_CALL_FAILURE_LIMIT = 2
GOOD_TIERS = {QualityTier.GOOD, QualityTier.EXCELLENT}
BROWSER_SOURCES = {"BROWSER", "SCREEN", "CAMERA"}

Emit = Callable[[dict[str, Any]], Awaitable[None]]

OPENING = (
    "Hi{name}, I'm {interviewer}, an AI interviewer working with {company}. Over the next {minutes} minutes we'll talk "
    "about your experience and some {role} topics, and a person on the hiring team will review the conversation "
    "afterwards. Take your time with each answer, and ask me if anything is unclear."
)
NEXT_TOPIC_DIRECTIVE = (
    "Move on to a new topic. Bridge from the previous topic in a few neutral words, then ask this question in your "
    "own words, keeping its meaning and any concrete details: {question}"
)
GENERIC_PROBE = "Could you walk me through a concrete example of that from your own work, step by step?"
FALLBACK_WRAP_UP = "We're nearly out of time. Do you have any questions for me about the role or the team?"
FALLBACK_CLOSE = (
    "Thank you for your time today. The hiring team will review our conversation and get in touch about next steps. "
    "Goodbye."
)
FALLBACK_CANDIDATE_ANSWER = (
    "That's a good question for the recruiter, and I'll pass it on. Is there anything else you'd like to ask?"
)
REPHRASE_DIRECTIVE = (
    "The candidate has been silent for a while. Do not move on. Rephrase your last question in simpler, narrower "
    "words, or offer a concrete starting point, in at most two short sentences ending with one question."
)
MODES = ("TEXT", "VOICE")
REMINDER_DIRECTIVE = 'Before anything else, say this neutral reminder in one sentence: "{reminder}" Then: '

_DONT_KNOW = re.compile(
    r"\b(i\s*(do\s*n[o']?t|dont)\s*know|not sure|no idea|never (used|worked with|done)|can'?t remember|"
    r"i'?m not familiar|not familiar with)\b",
    re.IGNORECASE,
)
_CLARIFY = re.compile(
    r"^(what do you mean|could you (clarify|repeat|rephrase)|can you (clarify|repeat|rephrase)|sorry,? what)",
    re.IGNORECASE,
)
_META = re.compile(
    r"\b(what('?s| is) my score|how (am i|did i) do(ing)?|tell me the (answer|rubric)|"
    r"what('?s| is) the (correct|right) answer)\b",
    re.IGNORECASE,
)
_COMPATIBLE = {
    "probe": {"probe", "challenge"},
    "challenge": {"challenge", "probe"},
    "hint": {"hint"},
    "clarify": {"clarify"},
    "decline": {"clarify"},
    "steer": {"clarify"},
}
SHORT_ANSWER_WORDS = 12
CLARIFY_MAX_WORDS = 15
DONT_KNOW_MAX_WORDS = 40


def now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class SessionRef:
    id: uuid.UUID
    org_id: uuid.UUID
    owner_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class EngineDeps:
    agent: AgentDeps
    session_factory: async_sessionmaker[AsyncSession]
    jobs: JobDispatcher


class EngineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class Outcome:
    """What the interviewer must do after the policy decision, and the planned text if no model can say it."""

    directive: str
    fallback: str
    needs_question: bool = True
    closing: bool = False
    asked: str = ""
    jobs: list[tuple[str, dict[str, str]]] = field(default_factory=list)


def heuristic_control(answer: str) -> TurnControl:
    """Classify an answer without a model (used when the controller is unavailable)."""
    stripped = answer.strip()
    words = len(stripped.split())
    if injection_matches(stripped):
        return TurnControl(answer_quality="injection_attempt", next_action="probe")
    if _META.search(stripped):
        return TurnControl(answer_quality="meta_request", next_action="clarify")
    if _CLARIFY.search(stripped) or (words < CLARIFY_MAX_WORDS and stripped.endswith("?")):
        return TurnControl(answer_quality="asks_clarification", next_action="clarify")
    if _DONT_KNOW.search(stripped) and words < DONT_KNOW_MAX_WORDS:
        return TurnControl(answer_quality="dont_know", next_action="hint")
    if words < SHORT_ANSWER_WORDS:
        return TurnControl(answer_quality="shallow", next_action="probe")
    return TurnControl(answer_quality="adequate", next_action="probe")


def compatible(decision: policy.Decision, control: TurnControl) -> bool:
    """May the single-call reply that the model already started be used as is?"""
    return (
        decision.move == "stay"
        and decision.integrity == "none"
        and control.next_action in _COMPATIBLE.get(decision.action, set())
    )


# --- locking -------------------------------------------------------------------------------------------------

_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_local_locks: dict[uuid.UUID, asyncio.Lock] = {}


@contextlib.asynccontextmanager
async def session_lock(valkey: Redis | None, session_id: uuid.UUID) -> AsyncIterator[bool]:
    """One turn at a time per interview, across API replicas. Falls back to a process lock if Valkey is down."""
    key, token = f"interview:lock:{session_id}", uuid.uuid4().hex
    acquired: bool | None = None
    if valkey is not None:
        try:
            acquired = bool(await valkey.set(key, token, nx=True, px=LOCK_MS))
        except RedisError as exc:
            log.warning("interviews.lock_fallback", error=str(exc))
    if acquired is None:
        local = _local_locks.setdefault(session_id, asyncio.Lock())
        if local.locked():
            yield False
            return
        async with local:
            yield True
        return
    try:
        yield acquired
    finally:
        if acquired and valkey is not None:
            with contextlib.suppress(RedisError):
                await valkey.eval(_RELEASE, 1, key, token)  # type: ignore[misc]


@contextlib.asynccontextmanager
async def owner_session(
    factory: async_sessionmaker[AsyncSession], org_id: uuid.UUID, owner_user_id: uuid.UUID
) -> AsyncIterator[AsyncSession]:
    """The engine acts for the interview's creator: their tenant context, model routes and data policy."""
    async with factory() as db:
        await set_tenant_context(db, TenantContext(org_id=org_id, user_id=owner_user_id))
        user = await db.get(User, owner_user_id)
        if user is None:
            raise EngineError("not_found", "This interview is no longer available.")
        await set_tenant_context(db, TenantContext(org_id=org_id, user_id=owner_user_id, role=user.role.value))
        yield db


@contextlib.asynccontextmanager
async def _closing(stream: AsyncIterator[str]) -> AsyncIterator[AsyncIterator[str]]:
    """Close the upstream model stream when we stop early (one question reached, or the turn changed)."""
    try:
        yield stream
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


# --- turn context --------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Turn:
    """Everything one turn needs, loaded once."""

    session: InterviewSession
    state: dict[str, Any]
    plan: InterviewPlan
    structure: list[dict[str, Any]]
    org: str
    rules: OrgPolicy
    history: list[InterviewTurn]
    now: datetime
    state_before: dict[str, Any] | None = None  # voice: lets an answer that continues after barge-in be merged

    @property
    def topic(self) -> PlanTopic | None:
        index = int(self.state.get("topic_index", 0))
        return self.plan.topics[index] if 0 <= index < len(self.plan.topics) else None

    @property
    def mode(self) -> str:
        return "VOICE" if self.session.mode == "VOICE" else "TEXT"

    @property
    def settings(self) -> dict[str, Any]:
        return self.session.settings

    def section_minutes(self, section: str) -> float:
        return next((float(s["minutes"]) for s in self.structure if s["section"] == section), 5.0)

    def timing(self) -> policy.Timing:
        started = _parse_time(self.state.get("started_at")) or self.now
        section_started = _parse_time(self.state.get("section_started_at")) or started
        paused = float(self.state.get("paused_s", 0))
        return policy.Timing(
            total_elapsed_min=max(0.0, ((self.now - started).total_seconds() - paused) / 60),
            total_minutes=float(self.settings.get("duration_minutes", 45)),
            section_elapsed_min=max(0.0, (self.now - section_started).total_seconds() / 60),
            section_minutes=self.section_minutes(str(self.state.get("section", "TECHNICAL"))),
        )

    def context_pack(self) -> list[dict[str, str]]:
        topic = self.topic
        packs: dict[str, list[dict[str, str]]] = self.session.plan.get("context_packs", {})
        return packs.get(topic.id, []) if topic else []

    def context_text(self) -> str:
        pack = self.context_pack()
        if not pack:
            return "(no reference documents)"
        return "\n".join(f'<snippet document_id="{p["document_id"]}">\n{p["text"]}\n</snippet>' for p in pack)

    def last_question(self) -> str:
        return next((t.text for t in reversed(self.history) if t.speaker == Speaker.INTERVIEWER), "")

    def call(self, role: AgentRole) -> CallCtx:
        return CallCtx(
            org_id=self.session.org_id,
            user_id=self.session.owner_user_id,
            role=role,
            session_id=self.session.id,
            contains_pii=True,
        )

    def system(self, task: composer.Layer) -> str:
        persona = self.session.persona
        topic = self.topic
        candidate = self.session.plan.get("candidate", {})
        covered = [t.title for t in self.plan.topics if t.id in self.state.get("closed_topics", [])]
        layers: list[composer.Layer] = [
            (
                "org_policy",
                {
                    "interview_tone": self.rules.interview_tone or "Professional, friendly and direct.",
                    "forbidden_topics": ", ".join(self.rules.forbidden_topics) or "(none beyond the platform rules)",
                    "language": self.settings.get("language", "English"),
                },
                {"company_facts": self.rules.company_facts or "(none provided)"},
            ),
            (
                "interviewer_persona",
                {
                    "interviewer_name": persona.get("interviewer_name", "Alex"),
                    "role_title": persona.get("role_title", "engineering"),
                    "company": self.org,
                    "recent_acknowledgements": ", ".join(self.state.get("recent_acks", [])) or "(none)",
                },
                {"persona_style": persona.get("style", "")},
            ),
            (
                "interview_context",
                {
                    "role_title": persona.get("role_title", "engineering"),
                    "seniority": self.settings.get("seniority", "MID"),
                    "mode": self.mode.lower(),
                    "section": self.state.get("section", ""),
                    "covered_topics": ", ".join(covered) or "(none yet)",
                    "topic_title": topic.title if topic else "(candidate questions)",
                },
                {
                    "candidate_summary": {
                        "summary": candidate.get("summary", ""),
                        "claims": candidate.get("evidence", {}),
                    },
                    "asked_questions": self.state.get("asked", [])[-12:] or "(none)",
                    "topic_question": topic.seed_question if topic else "",
                    "context_pack": self.context_text(),
                },
            ),
            task,
        ]
        return composer.layered_system(org_name=self.org, layers=layers)

    def history_messages(self) -> list[Message]:
        messages: list[Message] = []
        for turn in self.history[-HISTORY_TURNS:]:
            if turn.speaker == Speaker.INTERVIEWER:
                messages.append({"role": "assistant", "content": turn.text})
            elif turn.speaker == Speaker.CANDIDATE:
                messages.append({"role": "user", "content": f"<candidate>{composer.neutralize(turn.text)}</candidate>"})
        if not messages or messages[0]["role"] == "assistant":
            messages.insert(0, {"role": "user", "content": "(The interview begins.)"})
        return messages


def public_state(session: InterviewSession, now: datetime | None = None) -> dict[str, Any]:
    """What the candidate's screen may show: never the plan, topics or any assessment."""
    state = session.state or {}
    count = len(session.plan.get("topics", []))
    started = _parse_time(state.get("started_at"))
    elapsed = 0.0
    if started is not None:
        end = session.ended_at or now or now_utc()
        elapsed = max(0.0, ((end - started).total_seconds() - float(state.get("paused_s", 0))) / 60)
    return {
        "status": session.status.value,
        "phase": state.get("phase", "pending"),
        "section": state.get("section"),
        "topic_number": min(int(state.get("topic_index", 0)) + 1, count) if count else 0,
        "topic_count": count,
        "elapsed_minutes": round(elapsed, 1),
        "total_minutes": session.settings.get("duration_minutes", 45),
        "awaiting_answer": bool(state.get("awaiting")),
        "mode": session.mode,
    }


# --- the engine ----------------------------------------------------------------------------------------------


class InterviewEngine:
    def __init__(self, deps: EngineDeps) -> None:
        self.deps = deps
        self._background: set[asyncio.Task[None]] = set()

    # Background jobs never block the conversation (inline mode runs them in-process).
    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _submit(self, name: str, args: dict[str, str]) -> None:
        try:
            await self.deps.jobs.submit(name, args)
        except Exception:
            # The self-healing scan re-dispatches grading that never ran; the interview itself goes on.
            log.exception("interviews.job_submit_failed", job=name, session_id=args.get("session_id"))

    async def drain(self) -> None:
        """Wait for background jobs (tests and graceful shutdown)."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    def _jobs_after(self, ref: SessionRef, jobs: list[tuple[str, dict[str, str]]]) -> None:
        for name, extra in jobs:
            args = {"org_id": str(ref.org_id), "user_id": str(ref.owner_user_id), "session_id": str(ref.id), **extra}
            self._spawn(self._submit(name, args))

    async def _load(self, db: AsyncSession, ref: SessionRef) -> Turn:
        session = await db.get(InterviewSession, ref.id)
        if session is None or session.org_id != ref.org_id:
            raise EngineError("not_found", "This interview does not exist.")
        org = await db.get(Organization, session.org_id)
        history = list(
            await db.scalars(
                select(InterviewTurn).where(InterviewTurn.session_id == session.id).order_by(InterviewTurn.seq)
            )
        )
        plan = InterviewPlan.model_validate({"topics": session.plan.get("topics", [])})
        return Turn(
            session=session,
            state=dict(session.state or {}),
            plan=plan,
            structure=list(session.plan.get("structure", [])),
            org=org.name if org else await org_name(db, session.org_id),
            rules=org_policy(org) if org else OrgPolicy(),
            history=history,
            now=now_utc(),
        )

    @staticmethod
    def _add_turn(db: AsyncSession, turn: Turn, speaker: Speaker, text: str, **extra: Any) -> InterviewTurn:
        seq = int(turn.state.get("seq", 0)) + 1
        turn.state["seq"] = seq
        topic = turn.topic
        row = InterviewTurn(
            org_id=turn.session.org_id,
            owner_user_id=turn.session.owner_user_id,
            session_id=turn.session.id,
            seq=seq,
            speaker=speaker,
            text=text,
            section=str(turn.state.get("section", "")) or None,
            topic_id=topic.id if topic and turn.state.get("phase") == "topic" else None,
            **extra,
        )
        db.add(row)
        turn.history.append(row)
        return row

    @staticmethod
    def _save_state(turn: Turn) -> None:
        turn.session.state = dict(turn.state)

    # --- start / resume ------------------------------------------------------------------------------------

    async def hello(self, ref: SessionRef, emit: Emit) -> None:
        """First frame after connecting: the state, plus the transcript when rejoining a live interview."""
        async with owner_session(self.deps.session_factory, ref.org_id, ref.owner_user_id) as db:
            turn = await self._load(db, ref)
            if turn.session.status == SessionStatus.LIVE:
                await self._resume(db, turn, emit)
            else:
                await emit({"type": "state", **public_state(turn.session, turn.now)})

    async def start(self, ref: SessionRef, emit: Emit, *, as_owner: bool = False) -> None:
        async with owner_session(self.deps.session_factory, ref.org_id, ref.owner_user_id) as db:
            turn = await self._load(db, ref)
            session = turn.session
            if session.status == SessionStatus.LIVE:
                await self._resume(db, turn, emit)
                return
            if session.status != SessionStatus.PLANNED:
                raise EngineError("not_available", _unavailable_message(session.status))
            if not as_owner and not (session.consent or {}).get("accepted"):
                raise EngineError("consent_required", "Please read and accept the consent notice first.")
            await self._open(db, turn, emit, as_owner=as_owner)

    async def _resume(self, db: AsyncSession, turn: Turn, emit: Emit) -> None:
        disconnected = _parse_time(turn.state.pop("disconnected_at", None))
        if disconnected is not None:
            gap = (turn.now - disconnected).total_seconds()
            if gap > RESUME_GRACE_S:  # the clock does not run while the candidate is disconnected
                turn.state["paused_s"] = float(turn.state.get("paused_s", 0)) + gap
            self._save_state(turn)
            await db.commit()
        await emit(
            {
                "type": "transcript",
                "turns": [
                    {"seq": t.seq, "speaker": t.speaker.value, "text": t.text}
                    for t in turn.history
                    if t.speaker != Speaker.SYSTEM
                ],
                "last_client_id": next(
                    ((t.meta or {}).get("client_id") for t in reversed(turn.history) if t.speaker == Speaker.CANDIDATE),
                    None,
                ),
            }
        )
        await emit({"type": "state", **public_state(turn.session, turn.now)})

    async def _turn_mode(self, db: AsyncSession, turn: Turn) -> str:
        configured = str(turn.settings.get("turn_mode", "auto"))
        if configured != "auto":
            return configured
        try:
            chain = await llm(self.deps.agent, db).resolve_chain(turn.call(AgentRole.INTERVIEWER))
        except LLM_FAILURES:
            return "two_call"
        return "single_call" if chain and chain[0].quality_tier in GOOD_TIERS else "two_call"

    async def _open(self, db: AsyncSession, turn: Turn, emit: Emit, *, as_owner: bool) -> None:
        session = turn.session
        first = turn.plan.topics[0]
        now_iso = turn.now.isoformat()
        turn.state.update(
            {
                "phase": "topic",
                "topic_index": 0,
                "section": first.section,
                "started_at": now_iso,
                "section_started_at": now_iso,
                "follow_ups": 0,
                "hints": 0,
                "closed_topics": [],
                "asked": [first.seed_question],
                "recent_acks": [],
                "reminded_sections": [],
                "single_call_failures": 0,
                "paused_s": 0.0,
                "turn_mode": await self._turn_mode(db, turn),
                "preview": as_owner,
            }
        )
        label = (session.candidate_label or "").split(" ")[0].strip()
        opening = OPENING.format(
            name=f" {label}" if label and not as_owner else "",
            interviewer=session.persona.get("interviewer_name", "Alex"),
            company=turn.org,
            minutes=session.settings.get("duration_minutes", 45),
            role=session.persona.get("role_title", "technical"),
        )
        text = f"{opening} {first.seed_question}"
        session.status, session.started_at = SessionStatus.LIVE, turn.now
        await emit({"type": "turn.start", "seq": int(turn.state.get("seq", 0)) + 1, "speaker": "INTERVIEWER"})
        row = self._add_turn(db, turn, Speaker.INTERVIEWER, text, control=None, meta={"action": "opening"})
        await emit({"type": "turn.delta", "seq": row.seq, "text": text})
        turn.state.update(awaiting=True, asked_at=turn.now.isoformat())
        self._save_state(turn)
        await db.commit()
        await emit({"type": "turn.end", "seq": row.seq, "text": text, "awaiting": True})
        await emit({"type": "state", **public_state(session, turn.now)})

    # --- answers -------------------------------------------------------------------------------------------

    async def answer(
        self, ref: SessionRef, text: str, meta: dict[str, Any], emit: Emit, *, client_id: str | None = None
    ) -> None:
        async with session_lock(self.deps.agent.valkey, ref.id) as acquired:
            if not acquired:
                raise EngineError("busy", "Your previous answer is still being processed.")
            async with owner_session(self.deps.session_factory, ref.org_id, ref.owner_user_id) as db:
                turn = await self._load(db, ref)
                if turn.session.status != SessionStatus.LIVE:
                    raise EngineError("not_live", _unavailable_message(turn.session.status))
                if client_id and _already_answered(turn, client_id):
                    await self._resume(db, turn, emit)  # duplicate send after a reconnect
                    return
                if meta.get("continuation") is True and turn.session.mode == "VOICE":
                    text = await self._merge_unheard(db, turn, text)
                if not turn.state.get("awaiting"):
                    raise EngineError("not_awaiting", "Please wait for the interviewer's question.")
                jobs = await self._answer(db, turn, text.strip()[:MAX_ANSWER_CHARS], meta, emit, client_id)
        self._jobs_after(ref, jobs)

    async def _merge_unheard(self, db: AsyncSession, turn: Turn, text: str) -> str:
        """Voice: the candidate paused, we answered, they kept talking over a reply they never heard. Undo that
        exchange and treat both parts as one answer."""
        if len(turn.history) < 2:  # noqa: PLR2004
            return text
        reply, previous = turn.history[-1], turn.history[-2]
        meta = reply.meta or {}
        if (
            reply.speaker != Speaker.INTERVIEWER
            or previous.speaker != Speaker.CANDIDATE
            or not meta.get("interrupted")
            or meta.get("spoken_text")
            or not isinstance(meta.get("state_before"), dict)
        ):
            return text
        turn.state = dict(meta["state_before"])
        await db.delete(reply)
        await db.delete(previous)
        await db.flush()
        turn.history = turn.history[:-2]
        return f"{previous.text} {text}"[:MAX_ANSWER_CHARS]

    async def _answer(
        self,
        db: AsyncSession,
        turn: Turn,
        text: str,
        meta: dict[str, Any],
        emit: Emit,
        client_id: str | None,
    ) -> list[tuple[str, dict[str, str]]]:
        session = turn.session
        turn.state_before = copy.deepcopy(turn.state)
        asked_at = _parse_time(turn.state.get("asked_at"))
        delay = (turn.now - asked_at).total_seconds() if asked_at else None
        clean_meta = _client_meta(meta, client_id)
        answer_row = self._add_turn(db, turn, Speaker.CANDIDATE, text, meta=clean_meta)
        await emit({"type": "accepted", "seq": answer_row.seq, "client_id": client_id})
        signals, probe_signals, reminder = await self._integrity(db, turn, answer_row, delay)

        timing = turn.timing()
        started = time.monotonic()
        control: TurnControl | None = None
        decision: policy.Decision | None = None
        streamed: tuple[str, dict[str, Any]] | None = None
        control_source = "policy"
        reply_seq = int(turn.state.get("seq", 0)) + 1

        def decide(found: TurnControl) -> policy.Decision:
            return policy.decide(
                found,
                turn.state,
                turn.settings,
                timing,
                answer=text,
                seniority=str(turn.settings.get("seniority", "MID")),
                has_next_topic=int(turn.state.get("topic_index", 0)) + 1 < len(turn.plan.topics),
                integrity_signals=probe_signals,
            )

        if turn.state.get("phase") == "candidate_questions":
            control = TurnControl(answer_quality="adequate", next_action="answer_candidate_question")
        elif turn.state.get("turn_mode") == "single_call" and reminder is None:
            control, decision, streamed = await self._single_call(db, turn, reply_seq, emit, decide)
            control_source = "single_call" if control else "single_call_failed"
        if control is None:
            control, control_source = await self._control(db, turn, text)
        if control.answer_quality == "injection_attempt" and not any(s["kind"] == "injection_attempt" for s in signals):
            await integrity.record_answer_signals(
                db, session, answer_row.seq, [{"kind": "injection_attempt", "severity": "high", "detail": {}}]
            )
        decision = decision or decide(control)
        controller_ms = round((time.monotonic() - started) * 1000)
        answer_row.control = control.model_dump()

        plan = self._apply(turn, decision)
        if decision.integrity == "probe" or reminder is not None:
            await integrity.mark_addressed(db, session.id)
        if streamed is None:
            directive = (REMINDER_DIRECTIVE.format(reminder=reminder) if reminder else "") + plan.directive
            streamed = await self._speak(
                db,
                turn,
                reply_seq,
                emit,
                directive=directive,
                acknowledgement=self._acknowledgement(turn, control, decision),
                fallback=plan.fallback,
                needs_question=plan.needs_question,
            )
        latency = {**streamed[1], "controller_ms": controller_ms, "control_source": control_source}
        await self._complete(db, turn, plan, decision, streamed[0], latency, emit)
        return plan.jobs

    async def _complete(
        self,
        db: AsyncSession,
        turn: Turn,
        plan: Outcome,
        decision: policy.Decision,
        reply: str,
        latency: dict[str, Any],
        emit: Emit,
    ) -> None:
        """Store the reply and the new state in the same transaction as the answer, then tell the client."""
        session = turn.session
        if turn.state.get("phase") == "topic" and plan.asked:
            turn.state["asked"] = [*turn.state.get("asked", []), plan.asked][-40:]
        if not plan.asked and plan.needs_question:
            turn.state["asked"] = [*turn.state.get("asked", []), reply][-40:]
        row = self._add_turn(
            db,
            turn,
            Speaker.INTERVIEWER,
            reply,
            latency=latency,
            meta={
                "action": decision.action,
                "move": decision.move,
                "integrity": decision.integrity,
                **({"state_before": turn.state_before} if session.mode == "VOICE" and turn.state_before else {}),
            },
        )
        if plan.closing:
            session.status, session.ended_at = SessionStatus.COMPLETED, turn.now
            turn.state.update(phase="closed", awaiting=False)
        else:
            turn.state.update(awaiting=True, asked_at=now_utc().isoformat())
        self._save_state(turn)
        await db.commit()
        await emit({"type": "turn.end", "seq": row.seq, "text": reply, "awaiting": not plan.closing})
        await emit({"type": "state", **public_state(session)})
        if plan.closing:
            await emit({"type": "ended", "reason": "completed"})

    async def _integrity(
        self, db: AsyncSession, turn: Turn, answer: InterviewTurn, delay: float | None
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Answer-level signals for this answer, plus what is still unaddressed (for probes and reminders)."""
        references = [p["text"] for p in turn.context_pack()]
        signals = integrity.answer_signals(
            answer.text, answer.meta or {}, references=references, response_delay_s=delay
        )
        await integrity.record_answer_signals(db, turn.session, answer.seq, signals)
        await db.flush()
        pending = await integrity.unaddressed(db, turn.session.id)
        probe_signals = sum(
            1
            for e in pending
            if e.kind != "injection_attempt"
            and integrity.SEVERITY_RANK.get(e.severity, 0) >= integrity.SEVERITY_RANK["medium"]
        )
        return signals, probe_signals, self._reminder_due(turn, pending)

    def _reminder_due(self, turn: Turn, pending: list[IntegrityEvent]) -> str | None:
        if turn.settings.get("integrity_mode", "STANDARD") == "OFF" or turn.state.get("phase") != "topic":
            return None
        section = str(turn.state.get("section", ""))
        if section in turn.state.get("reminded_sections", []):
            return None
        browser = [
            e
            for e in pending
            if e.source in BROWSER_SOURCES
            and integrity.SEVERITY_RANK.get(e.severity, 0) >= integrity.SEVERITY_RANK["medium"]
        ]
        if not browser:
            return None
        turn.state["reminded_sections"] = [*turn.state.get("reminded_sections", []), section]
        return turn.rules.integrity_reminder

    def _acknowledgement(self, turn: Turn, control: TurnControl, decision: policy.Decision) -> str:
        if control.acknowledgement == "none" or decision.action in {"stay_in_role", "decline", "clarify", "steer"}:
            return "Do not start with an acknowledgement."
        ack = speech.next_acknowledgement(turn.state.get("recent_acks", []))
        turn.state["recent_acks"] = [*turn.state.get("recent_acks", []), ack][-speech.ACK_MEMORY :]
        return f'Start with this short neutral acknowledgement: "{ack}"'

    # --- controller ----------------------------------------------------------------------------------------

    async def _control(self, db: AsyncSession, turn: Turn, answer: str) -> tuple[TurnControl, str]:
        topic = turn.topic
        if topic is None:
            return heuristic_control(answer), "heuristic"
        messages = composer.task_messages(
            "controller",
            org_name=turn.org,
            trusted={
                "topic_title": topic.title,
                "expected_signals": "; ".join(topic.expected_signals) or "(none listed)",
                "red_flags": "; ".join(topic.red_flags) or "(none listed)",
                "follow_up_count": turn.state.get("follow_ups", 0),
                "follow_up_depth": turn.settings.get("follow_up_depth", 3),
                "hints_allowed": "yes" if turn.settings.get("hints_allowed", True) else "no",
                "seniority": turn.settings.get("seniority", "MID"),
            },
            untrusted={
                "last_question": turn.last_question() or topic.seed_question,
                "context_pack": turn.context_text(),
                "last_answer": answer,
            },
        )
        try:
            control = await llm(self.deps.agent, db).complete_json(
                turn.call(AgentRole.CONTROLLER), messages, TurnControl, temperature=0.0, max_tokens=400
            )
        except LLM_FAILURES as exc:
            log.warning("interviews.controller_fallback", session_id=str(turn.session.id), error=str(exc))
            return heuristic_control(answer), "heuristic"
        if injection_matches(answer) and control.answer_quality != "injection_attempt":
            control.answer_quality = "injection_attempt"  # code double-checks what the model may have missed
        return control, "controller"

    async def _single_call(
        self, db: AsyncSession, turn: Turn, seq: int, emit: Emit, decide: Callable[[TurnControl], policy.Decision]
    ) -> tuple[TurnControl | None, policy.Decision | None, tuple[str, dict[str, Any]] | None]:
        topic = turn.topic
        if topic is None:
            return None, None, None
        timing = turn.timing()
        system = turn.system(
            (
                "turn_single_call",
                {
                    "topic_title": topic.title,
                    "topic_competencies": ", ".join(topic.competencies),
                    "expected_signals": "; ".join(topic.expected_signals) or "(none listed)",
                    "red_flags": "; ".join(topic.red_flags) or "(none listed)",
                    "probe_angles": "; ".join(topic.probe_angles) or "(use your judgement)",
                    "follow_up_count": turn.state.get("follow_ups", 0),
                    "follow_up_depth": turn.settings.get("follow_up_depth", 3),
                    "hints_allowed": "yes" if turn.settings.get("hints_allowed", True) else "no",
                    "section_elapsed": round(timing.section_elapsed_min, 1),
                    "section_minutes": timing.section_minutes,
                    "total_elapsed": round(timing.total_elapsed_min, 1),
                    "total_minutes": timing.total_minutes,
                    "directive": "Decide next_action with the guide above and reply accordingly.",
                },
                {},
            )
        )
        messages = [{"role": "system", "content": system}, *turn.history_messages()]
        splitter = speech.ControlSplitter()
        gate = speech.SentenceGate(speech.WORD_LIMIT[turn.mode])
        decision: policy.Decision | None = None
        started, first_ms = time.monotonic(), None
        try:
            stream = llm(self.deps.agent, db).stream(
                turn.call(AgentRole.INTERVIEWER), messages, temperature=0.5, max_tokens=700
            )
            async with _closing(stream) as deltas:
                async for delta in deltas:
                    out = splitter.feed(delta)
                    if splitter.failed:
                        break
                    if splitter.control is None:
                        continue
                    if decision is None:
                        decision = decide(splitter.control)
                        if not compatible(decision, splitter.control):
                            return splitter.control, decision, None
                        await emit({"type": "turn.start", "seq": seq, "speaker": "INTERVIEWER"})
                    for sentence in gate.feed(out):
                        first_ms = first_ms or round((time.monotonic() - started) * 1000)
                        await emit({"type": "turn.delta", "seq": seq, "text": sentence})
                    if gate.done:
                        break
        except LLM_FAILURES as exc:
            log.warning("interviews.single_call_failed", session_id=str(turn.session.id), error=str(exc))
            if decision is None:
                return None, None, None
        if splitter.control is None or decision is None:
            failures = int(turn.state.get("single_call_failures", 0)) + 1
            turn.state["single_call_failures"] = failures
            if failures >= SINGLE_CALL_FAILURE_LIMIT:
                turn.state["turn_mode"] = "two_call"  # self-healing: this model cannot keep the format
                log.info("interviews.switched_to_two_call", session_id=str(turn.session.id))
            return None, None, None
        return splitter.control, decision, await self._finish_single(turn, decision, gate, seq, emit, started, first_ms)

    async def _finish_single(
        self,
        turn: Turn,
        decision: policy.Decision,
        gate: speech.SentenceGate,
        seq: int,
        emit: Emit,
        started: float,
        first_ms: int | None,
    ) -> tuple[str, dict[str, Any]]:
        for sentence in gate.flush():
            await emit({"type": "turn.delta", "seq": seq, "text": sentence})
        text = gate.text
        fallback = self._probe_fallback(turn)
        if not text:
            text = fallback
            await emit({"type": "turn.delta", "seq": seq, "text": text})
        elif "?" not in text:
            text = f"{text} {fallback}"
            await emit({"type": "turn.delta", "seq": seq, "text": fallback})
        self._count(turn, decision)
        return text, {
            "ttft_ms": first_ms,
            "total_ms": round((time.monotonic() - started) * 1000),
            "mode": "single_call",
        }

    # --- policy outcome ------------------------------------------------------------------------------------

    @staticmethod
    def _count(turn: Turn, decision: policy.Decision) -> None:
        if decision.counts_as_follow_up:
            turn.state["follow_ups"] = int(turn.state.get("follow_ups", 0)) + 1
        if decision.uses_hint:
            turn.state["hints"] = int(turn.state.get("hints", 0)) + 1

    def _probe_fallback(self, turn: Turn) -> str:
        topic = turn.topic
        asked = set(turn.state.get("asked", []))
        angles = [a for a in (topic.probe_angles if topic else []) if a not in asked]
        return angles[0] if angles else GENERIC_PROBE

    def _close_topic(self, turn: Turn, jobs: list[tuple[str, dict[str, str]]]) -> None:
        topic = turn.topic
        if topic is None or topic.id in turn.state.get("closed_topics", []):
            return
        turn.state["closed_topics"] = [*turn.state.get("closed_topics", []), topic.id]
        jobs.append((GRADE_JOB, {"topic_id": topic.id}))

    def _next_topic(self, turn: Turn, *, by_section: bool) -> PlanTopic | None:
        index = int(turn.state.get("topic_index", 0))
        current = turn.plan.topics[index] if index < len(turn.plan.topics) else None
        nxt = index + 1
        if by_section and current is not None:
            while nxt < len(turn.plan.topics) and turn.plan.topics[nxt].section == current.section:
                nxt += 1
        if nxt >= len(turn.plan.topics):
            return None
        topic = turn.plan.topics[nxt]
        turn.state.update(topic_index=nxt, follow_ups=0, hints=0)
        if topic.section != turn.state.get("section"):
            turn.state.update(section=topic.section, section_started_at=turn.now.isoformat())
        return topic

    def _apply(self, turn: Turn, decision: policy.Decision) -> Outcome:
        jobs: list[tuple[str, dict[str, str]]] = []
        has_questions_section = any(s["section"] == "CANDIDATE_QUESTIONS" for s in turn.structure)
        move = decision.move
        if move in {"next_topic", "next_section"}:
            self._close_topic(turn, jobs)
            topic = self._next_topic(turn, by_section=move == "next_section")
            if topic is not None:
                return Outcome(
                    NEXT_TOPIC_DIRECTIVE.format(question=topic.seed_question),
                    topic.seed_question,
                    asked=topic.seed_question,
                    jobs=jobs,
                )
            move = "candidate_questions"
        if move == "candidate_questions":
            self._close_topic(turn, jobs)
            if has_questions_section and turn.state.get("phase") == "topic":
                turn.state.update(
                    phase="candidate_questions",
                    section="CANDIDATE_QUESTIONS",
                    section_started_at=turn.now.isoformat(),
                    candidate_question_turns=0,
                )
                return Outcome(policy.WRAP_UP_DIRECTIVE, FALLBACK_WRAP_UP, jobs=jobs)
            move = "close"
        if move == "close":
            self._close_topic(turn, jobs)
            turn.state.update(section="CLOSE")
            jobs.append((FINALIZE_JOB, {}))
            return Outcome(policy.CLOSE_DIRECTIVE, FALLBACK_CLOSE, needs_question=False, closing=True, jobs=jobs)
        if decision.action == "answer_candidate_question":
            turn.state["candidate_question_turns"] = int(turn.state.get("candidate_question_turns", 0)) + 1
            return Outcome(decision.directive, FALLBACK_CANDIDATE_ANSWER, jobs=jobs)
        self._count(turn, decision)
        topic = turn.topic
        fallback = self._probe_fallback(turn)
        if decision.action in {"stay_in_role", "decline", "clarify", "steer"} and topic is not None:
            fallback = topic.seed_question if not turn.last_question() else turn.last_question()
        return Outcome(decision.directive, fallback, jobs=jobs)

    # --- speaking ------------------------------------------------------------------------------------------

    async def _speak(
        self,
        db: AsyncSession,
        turn: Turn,
        seq: int,
        emit: Emit,
        *,
        directive: str,
        acknowledgement: str,
        fallback: str,
        needs_question: bool,
    ) -> tuple[str, dict[str, Any]]:
        system = turn.system(("interviewer_turn", {"acknowledgement": acknowledgement, "directive": directive}, {}))
        messages = [{"role": "system", "content": system}, *turn.history_messages()]
        gate = speech.SentenceGate(speech.WORD_LIMIT[turn.mode] if needs_question else 120)
        started, first_ms = time.monotonic(), None
        source = "model"
        await emit({"type": "turn.start", "seq": seq, "speaker": "INTERVIEWER"})
        try:
            stream = llm(self.deps.agent, db).stream(
                turn.call(AgentRole.INTERVIEWER), messages, temperature=0.6, max_tokens=400
            )
            async with _closing(stream) as deltas:
                async for delta in deltas:
                    for sentence in gate.feed(delta):
                        first_ms = first_ms or round((time.monotonic() - started) * 1000)
                        await emit({"type": "turn.delta", "seq": seq, "text": sentence})
                    if gate.done:
                        break
            for sentence in gate.flush():
                await emit({"type": "turn.delta", "seq": seq, "text": sentence})
        except LLM_FAILURES as exc:
            log.warning("interviews.interviewer_fallback", session_id=str(turn.session.id), error=str(exc))
            source = "fallback"
        text = gate.text
        if not text:
            text, source = fallback, "fallback"
            await emit({"type": "turn.delta", "seq": seq, "text": text})
        elif needs_question and "?" not in text:
            text = f"{text} {fallback}"  # one-question rule: the reply must end with the planned question
            await emit({"type": "turn.delta", "seq": seq, "text": fallback})
        return text, {
            "ttft_ms": first_ms,
            "total_ms": round((time.monotonic() - started) * 1000),
            "mode": "two_call",
            "source": source,
        }

    # --- ending --------------------------------------------------------------------------------------------

    async def end(self, ref: SessionRef, *, reason: str, emit: Emit | None = None) -> bool:
        """End early (candidate left, interviewer stopped it, or the session was abandoned). Grades what exists."""
        jobs: list[tuple[str, dict[str, str]]] = []
        async with session_lock(self.deps.agent.valkey, ref.id) as acquired:
            if not acquired:
                raise EngineError("busy", "A turn is in progress; try again in a moment.")
            async with owner_session(self.deps.session_factory, ref.org_id, ref.owner_user_id) as db:
                turn = await self._load(db, ref)
                if turn.session.status != SessionStatus.LIVE:
                    return False
                if turn.state.get("phase") == "topic" and turn.topic is not None:
                    self._close_topic(turn, jobs)
                self._add_turn(db, turn, Speaker.SYSTEM, f"Interview ended ({reason}).", meta={"reason": reason})
                turn.state.update(phase="closed", awaiting=False, ended_reason=reason)
                turn.session.status, turn.session.ended_at = SessionStatus.COMPLETED, turn.now
                self._save_state(turn)
                await db.commit()
        jobs.append((FINALIZE_JOB, {}))
        self._jobs_after(ref, jobs)
        if emit is not None:
            await emit({"type": "ended", "reason": reason})
        return True

    async def simulate(
        self,
        db: AsyncSession,
        *,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        spec: RolePackSpec,
        variant: RoleVariant,
        persona_prompt: str,
        settings: dict[str, Any],
        answers: list[str],
    ) -> dict[str, Any]:
        """Template test drive: real controller, policy and interviewer on the pack's first technical topics,
        with scripted answers. Nothing is stored except model usage."""
        parsed = InterviewSettings.model_validate(settings)
        frozen = {**parsed.model_dump(mode="json"), "seniority": "MID", "turn_mode": "two_call"}
        structure = structure_for(parsed, variant)
        fallback = fallback_plan(spec, variant, structure, None, [])
        topics = [t for t in fallback.topics if t.section == "TECHNICAL"][:2] or fallback.topics[:2]
        for index, topic in enumerate(topics, start=1):
            topic.id = f"t{index}"
        org = await db.get(Organization, org_id)
        now = now_utc()
        session = InterviewSession(
            id=uuid.uuid4(),
            org_id=org_id,
            owner_user_id=user_id,
            title="Simulation",
            mode="TEXT",
            status=SessionStatus.LIVE,
            settings=frozen,
            persona={
                "interviewer_name": settings.get("interviewer_name") or spec.persona_defaults.interviewer_name,
                "style": render_persona(
                    persona_prompt or spec.persona_defaults.style,
                    {"role_title": variant.name, "company": org.name if org else "", "seniority": "MID"},
                ),
                "role_title": variant.name,
            },
            plan={
                "topics": [t.model_dump(mode="json") for t in topics],
                "structure": structure,
                "context_packs": {},
                "candidate": {"summary": SIMULATION_CANDIDATE, "evidence": {}},
            },
        )
        first = topics[0]
        turn = Turn(
            session=session,
            state={
                "phase": "topic",
                "topic_index": 0,
                "section": first.section,
                "started_at": now.isoformat(),
                "section_started_at": now.isoformat(),
                "asked": [first.seed_question],
                "closed_topics": [],
            },
            plan=InterviewPlan(topics=topics),
            structure=structure,
            org=org.name if org else "the organization",
            rules=org_policy(org) if org else OrgPolicy(),
            history=[InterviewTurn(seq=1, speaker=Speaker.INTERVIEWER, text=first.seed_question)],
            now=now,
        )
        results = []
        for answer in answers:
            turn.history.append(InterviewTurn(seq=len(turn.history) + 1, speaker=Speaker.CANDIDATE, text=answer))
            control, _source = await self._control(db, turn, answer)
            decision = policy.decide(
                control,
                turn.state,
                frozen,
                turn.timing(),
                answer=answer,
                seniority="MID",
                has_next_topic=int(turn.state.get("topic_index", 0)) + 1 < len(turn.plan.topics),
                integrity_signals=0,
            )
            outcome = self._apply(turn, decision)
            reply, _latency = await self._speak(
                db,
                turn,
                0,
                _ignore,
                directive=outcome.directive,
                acknowledgement=self._acknowledgement(turn, control, decision),
                fallback=outcome.fallback,
                needs_question=outcome.needs_question,
            )
            turn.history.append(InterviewTurn(seq=len(turn.history) + 1, speaker=Speaker.INTERVIEWER, text=reply))
            results.append(
                {
                    "candidate": answer,
                    "answer_quality": control.answer_quality,
                    "action": decision.action,
                    "interviewer": reply,
                }
            )
            if outcome.closing:
                break
        return {"opening_question": first.seed_question, "turns": results}

    # --- voice ------------------------------------------------------------------------------------------

    async def set_mode(self, ref: SessionRef, mode: str, emit: Emit) -> None:
        """Voice ↔ text in the same session ("Switch to text" keeps the transcript and the clock)."""
        if mode not in MODES:
            raise EngineError("invalid", "Unknown interview mode.")
        async with owner_session(self.deps.session_factory, ref.org_id, ref.owner_user_id) as db:
            session = await db.get(InterviewSession, ref.id)
            if session is None or session.status not in {SessionStatus.PLANNED, SessionStatus.LIVE}:
                raise EngineError("not_available", "This interview is not open.")
            session.mode = mode
            await db.commit()
            await emit({"type": "state", **public_state(session)})

    async def record_spoken(self, ref: SessionRef, seq: int, text: str, *, interrupted: bool) -> None:
        """What the candidate actually heard: whole sentences played before any barge-in (voice only)."""
        async with owner_session(self.deps.session_factory, ref.org_id, ref.owner_user_id) as db:
            turn = await db.scalar(
                select(InterviewTurn).where(InterviewTurn.session_id == ref.id, InterviewTurn.seq == seq)
            )
            if turn is None or turn.speaker != Speaker.INTERVIEWER:
                return
            turn.meta = {**(turn.meta or {}), "spoken_text": text[:4000], "interrupted": bool(interrupted)}
            await db.commit()

    async def nudge(self, ref: SessionRef, emit: Emit) -> None:
        """After a long silence (voice): the interviewer rephrases or narrows its question; nothing is graded."""
        async with session_lock(self.deps.agent.valkey, ref.id) as acquired:
            if not acquired:
                return
            async with owner_session(self.deps.session_factory, ref.org_id, ref.owner_user_id) as db:
                turn = await self._load(db, ref)
                if turn.session.status != SessionStatus.LIVE or not turn.state.get("awaiting"):
                    return
                if turn.state.get("phase") != "topic":
                    return
                seq = int(turn.state.get("seq", 0)) + 1
                last = turn.last_question()
                reply, latency = await self._speak(
                    db,
                    turn,
                    seq,
                    emit,
                    directive=REPHRASE_DIRECTIVE,
                    acknowledgement="Do not start with an acknowledgement.",
                    fallback=f"Let me put it another way. {last}" if last else GENERIC_PROBE,
                    needs_question=True,
                )
                row = self._add_turn(db, turn, Speaker.INTERVIEWER, reply, latency=latency, meta={"action": "rephrase"})
                self._save_state(turn)
                await db.commit()
                await emit({"type": "turn.end", "seq": row.seq, "text": reply, "awaiting": True})

    def on_disconnect(self, ref: SessionRef) -> None:
        """Called when the socket closes; recorded off the socket's task so a server-side cancel cannot cut it."""
        self._spawn(self.disconnected(ref))

    async def disconnected(self, ref: SessionRef) -> None:
        """Remember when the candidate dropped so the clock pauses until they are back."""
        async with owner_session(self.deps.session_factory, ref.org_id, ref.owner_user_id) as db:
            session = await db.get(InterviewSession, ref.id)
            if session is None or session.status != SessionStatus.LIVE:
                return
            session.state = {**session.state, "disconnected_at": now_utc().isoformat()}
            await db.commit()


SIMULATION_CANDIDATE = "(simulation: no CV; answer only from the transcript)"


async def _ignore(_frame: dict[str, Any]) -> None:
    return None


def _already_answered(turn: Turn, client_id: str) -> bool:
    return any(t.speaker == Speaker.CANDIDATE and (t.meta or {}).get("client_id") == client_id for t in turn.history)


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_META_KEYS = {"typing_ms": (int, float), "paste_chars": (int, float), "paste_count": (int,), "edits": (int,)}


def _client_meta(meta: dict[str, Any], client_id: str | None) -> dict[str, Any]:
    """Only known numeric fields from the browser are stored."""
    clean: dict[str, Any] = {
        key: meta[key]
        for key, types in _META_KEYS.items()
        if isinstance(meta.get(key), types) and not isinstance(meta.get(key), bool) and 0 <= meta[key] < 10**9
    }
    if client_id:
        clean["client_id"] = client_id[:64]
    submission = meta.get("submission_id")
    if isinstance(submission, str) and _UUID.fullmatch(submission):
        clean["submission_id"] = submission  # practical work this answer talks about
    return clean


def _unavailable_message(status: SessionStatus) -> str:
    return {
        SessionStatus.PREPARING: "The interview is still being prepared. Please try again in a few minutes.",
        SessionStatus.PLANNED: "The interview has not started yet.",
        SessionStatus.COMPLETED: "This interview has ended. Thank you for your time.",
        SessionStatus.ABORTED: "This interview was cancelled.",
        SessionStatus.FAILED: "This interview could not be prepared. Please contact the recruiter.",
    }.get(status, "This interview is not available.")
