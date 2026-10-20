"""Pipecat processors that connect a voice call to the interview engine.

InterviewBridge sits between turn detection and TTS:
- user turns (finalised transcripts between UserStarted/StoppedSpeaking) become one answer, sent after a short grace
  period so a candidate who pauses mid-answer is not cut off;
- the engine's reply (turn.start / turn.delta / turn.end, whole sentences) becomes LLMFullResponseStart / LLMText /
  LLMFullResponseEnd frames for TTS;
- barge-in mutes the rest of the reply; what was actually heard is reported back (`spoken`), and if the
  candidate heard nothing the next answer is marked as a continuation so the engine merges both parts;
- silence after a question: "take your time" (7 s), an engine rephrase (20 s), an offer to move on (45 s);
- a monologue longer than `ramble_secs` gets a soft cut-in at the next pause;
- engine state, captions and errors go to the client as RTVI server messages.

SpokenTracker sits after the output transport, where a TTSTextFrame arrives only once its sentence was played.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    EndTaskFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

from intrv_voice.engine_link import Link

log = logging.getLogger("intrv_voice")

TAKE_YOUR_TIME = "Take your time."
OFFER_TO_MOVE_ON = "If you prefer, we can move on to a different question. Just let me know."
CUT_IN = "Let me stop you there for a moment."
WELCOME_BACK = "Welcome back."
CLIENT_FRAMES = {"state", "accepted", "transcript", "error", "ended", "turn.end"}


@dataclass
class Playback:
    """Sentences confirmed as played, per engine turn (filled by SpokenTracker)."""

    current: int | None = None
    spoken: dict[int, list[str]] = field(default_factory=dict)

    def begin(self, seq: int) -> None:
        self.current = seq
        self.spoken.setdefault(seq, [])

    def heard(self, seq: int) -> str:
        return " ".join(self.spoken.get(seq, []))


class SpokenTracker(FrameProcessor):
    def __init__(self, playback: Playback, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._playback = playback

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and self._playback.current is not None:
            self._playback.spoken[self._playback.current].append(frame.text.strip())
        await self.push_frame(frame, direction)


class InterviewBridge(FrameProcessor):
    def __init__(
        self,
        link: Link,
        playback: Playback,
        *,
        grace_secs: float = 0.6,
        silence_prompts_secs: tuple[float, float, float] = (7.0, 20.0, 45.0),
        ramble_secs: float = 120.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._link = link
        self._playback = playback
        self._grace = grace_secs
        self._silence = silence_prompts_secs
        self._ramble = ramble_secs
        self._buffer: list[str] = []
        self._send_task: asyncio.Task[None] | None = None
        self._timer: asyncio.Task[None] | None = None
        self._queued: str = ""
        self._reply_seq: int | None = None
        self._reply_open = False
        self._report_due: int | None = None
        self._muted: set[int] = set()
        self._awaiting = False
        self._unheard = False
        self._user_since: float | None = None
        self.sent: list[dict[str, Any]] = []  # answers sent (observability and tests)

    # --- frames from the pipeline ------------------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                self._buffer.append(frame.text.strip())
        elif isinstance(frame, VADUserStartedSpeakingFrame | UserStartedSpeakingFrame):
            await self._stop_timer()
            await self._cancel_send()
            self._user_since = self._user_since or time.monotonic()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if self._user_since is not None and time.monotonic() - self._user_since > self._ramble:
                await self.push_frame(TTSSpeakFrame(CUT_IN, append_to_context=False))
                self._schedule_send(0.0)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._schedule_send(self._grace)
        elif isinstance(frame, InterruptionFrame):
            await self._interrupted()
        elif isinstance(frame, BotStoppedSpeakingFrame) and direction == FrameDirection.UPSTREAM:
            await self._finished_speaking()
        elif isinstance(frame, EndFrame | CancelFrame):
            await self._stop_timer()
            await self._cancel_send()
        await self.push_frame(frame, direction)

    def _schedule_send(self, delay: float) -> None:
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()
        self._send_task = self.create_task(self._send_after(delay), "answer-grace")

    async def _cancel_send(self) -> None:
        if self._send_task is not None and not self._send_task.done():
            await self.cancel_task(self._send_task)
        self._send_task = None

    async def _send_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        text = " ".join(self._buffer).strip()
        self._buffer.clear()
        self._user_since = None
        if not text:
            return
        if self._reply_open:  # the engine is still answering the previous turn: send right after it ends
            self._queued = f"{self._queued} {text}".strip()
            return
        await self._send_answer(text)

    async def _send_answer(self, text: str) -> None:
        message = {
            "type": "answer",
            "text": text,
            "client_id": uuid.uuid4().hex,
            "meta": {"continuation": self._unheard},
        }
        self._unheard, self._awaiting = False, False
        self.sent.append(message)
        await self._link.send(message)

    async def _interrupted(self) -> None:
        seq = self._reply_seq
        if seq is None or seq in self._muted:
            return
        self._muted.add(seq)
        self._playback.current = None
        if not self._reply_open:  # the reply was complete and partly played
            await self._report(seq, interrupted=True)

    async def _finished_speaking(self) -> None:
        seq = self._report_due
        if seq is not None and not self._reply_open and seq not in self._muted:
            await self._report(seq, interrupted=False)
            if self._awaiting:
                await self._arm_timer()

    async def _report(self, seq: int, *, interrupted: bool) -> None:
        heard = self._playback.heard(seq)
        self._report_due = None
        if interrupted and not heard:
            self._unheard = True
        await self._link.send({"type": "spoken", "seq": seq, "text": heard, "interrupted": interrupted})

    # --- silence prompts ---------------------------------------------------------------------------------------

    async def _arm_timer(self) -> None:
        if self._timer is None or self._timer.done():
            self._timer = self.create_task(self._silence_prompts(), "silence-prompts")

    async def _stop_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            await self.cancel_task(self._timer)
        self._timer = None

    async def _silence_prompts(self) -> None:
        take_time, rephrase, move_on = self._silence
        await asyncio.sleep(take_time)
        await self.push_frame(TTSSpeakFrame(TAKE_YOUR_TIME, append_to_context=False))
        await asyncio.sleep(rephrase - take_time)
        await self._link.send({"type": "nudge"})
        await asyncio.sleep(move_on - rephrase)
        await self.push_frame(TTSSpeakFrame(OFFER_TO_MOVE_ON, append_to_context=False))

    # --- frames from the engine --------------------------------------------------------------------------------

    async def begin(self, first_frame: dict[str, Any] | None) -> None:
        """Called once the client is connected: resume or start the interview in voice mode."""
        if first_frame is not None:
            await self.on_engine_frame(first_frame)
        await self._link.send({"type": "mode", "mode": "VOICE"})
        if (first_frame or {}).get("status") == "PLANNED":
            await self._link.send({"type": "start"})

    async def on_engine_frame(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "turn.start":
            seq = int(frame["seq"])
            self._reply_seq, self._reply_open = seq, True
            self._playback.begin(seq)
            await self._stop_timer()
            await self.push_frame(LLMFullResponseStartFrame())
        elif kind == "turn.delta":
            if int(frame["seq"]) not in self._muted:
                await self.push_frame(LLMTextFrame(str(frame.get("text", ""))))
        elif kind == "turn.end":
            await self._reply_ended(frame)
        elif kind == "transcript":
            await self._resume(frame)
        elif kind in {"ended", "link.closed"}:
            await self._client({"type": "ended", "reason": frame.get("reason", "closed")})
            await self.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)  # drains audio, then ends the call
            return
        if kind in CLIENT_FRAMES:
            await self._client(frame)

    async def _reply_ended(self, frame: dict[str, Any]) -> None:
        seq = int(frame["seq"])
        self._reply_open = False
        self._awaiting = bool(frame.get("awaiting"))
        if seq in self._muted:
            await self._report(seq, interrupted=True)
        else:
            await self.push_frame(LLMFullResponseEndFrame())
            self._report_due = seq
        if self._queued:
            text, self._queued = self._queued, ""
            await self._send_answer(text)

    async def _resume(self, frame: dict[str, Any]) -> None:
        turns = frame.get("turns") or []
        if turns and turns[-1].get("speaker") == "INTERVIEWER":
            self._awaiting = True
            await self.push_frame(TTSSpeakFrame(f"{WELCOME_BACK} {turns[-1]['text']}", append_to_context=False))

    async def _client(self, data: dict[str, Any]) -> None:
        await self.push_frame(RTVIServerMessageFrame(data=data))
