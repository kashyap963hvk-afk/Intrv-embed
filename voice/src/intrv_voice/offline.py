"""Run the voice pipeline without WebRTC: recorded audio in, paced audio out, a scripted or real engine.

Used by the tests and by `make voice-bench`. Audio is fed and played in real time, so timings are realistic.
"""

from __future__ import annotations

import asyncio
import statistics
import time
import wave
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TextAggregationMode, TTSService
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.time import time_now_iso8601
from pipecat.workers.runner import WorkerRunner

from intrv_voice.bridge import InterviewBridge
from intrv_voice.config import VoiceSettings
from intrv_voice.pipeline import IN_RATE, OUT_RATE, build_pipeline


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (IN_RATE, 1, 2):
            raise ValueError(f"{path} must be 16 kHz mono 16-bit")
        return wav.readframes(wav.getnframes())


def silence(seconds: float) -> bytes:
    return bytes(int(IN_RATE * seconds) * 2)


class WavSource(FrameProcessor):
    """Pushes 20 ms audio frames in real time, like a microphone."""

    def __init__(self, schedule: list[bytes], done: asyncio.Event, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._schedule, self._done = schedule, done
        self._task: asyncio.Task[None] | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._task = self.create_task(self._feed(), "wav")
        elif isinstance(frame, EndFrame | CancelFrame) and self._task:
            await self.cancel_task(self._task)

    async def _feed(self) -> None:
        start, sent, step = time.monotonic(), 0, 640
        for block in self._schedule:
            for offset in range(0, len(block), step):
                frame = InputAudioRawFrame(audio=block[offset : offset + step], sample_rate=IN_RATE, num_channels=1)
                await self.push_frame(frame)
                sent += 1
                delay = start + sent * 0.02 - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
        self._done.set()


class ScriptedSTT(SegmentedSTTService):
    """Returns `part1`, `part2`, … per speech segment (STT quality is not under test)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(settings=STTSettings(model=None, language=None), ttfs_p99_latency=0.3, **kwargs)
        self.count = 0

    @property
    def wants_wav_segments(self) -> bool:
        return False

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        self.count += 1
        yield TranscriptionFrame(f"part{self.count}", "", time_now_iso8601())


class FakeTTS(TTSService):
    """Silence of a realistic duration per word."""

    def __init__(self, seconds_per_word: float = 0.3, **kwargs: Any) -> None:
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            text_aggregation_mode=TextAggregationMode.TOKEN,
            max_consecutive_zero_audio_contexts=0,
            settings=TTSSettings(model=None, voice=None, language=None),
            **kwargs,
        )
        self._seconds_per_word = seconds_per_word
        self.spoken_requests: list[str] = []

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        self.spoken_requests.append(text)
        await self.stop_ttfb_metrics()
        samples = int(self.sample_rate * self._seconds_per_word * max(1, len(text.split())))
        audio, step = bytes(samples * 2), self.sample_rate // 10 * 2
        for offset in range(0, len(audio), step):
            yield TTSAudioRawFrame(audio[offset : offset + step], self.sample_rate, 1, context_id=context_id)


class PacedOutput(BaseOutputTransport):
    """Plays audio in real time, so playback confirmations and barge-in behave as on a real call."""

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: Any) -> bool:
        await asyncio.sleep(len(frame.audio) / (frame.sample_rate * 2 * frame.num_channels))
        return True


class ScriptedEngine:
    """Stands in for the engine socket: answers each answer (and nudge) with whole sentences after `think_s`."""

    def __init__(self, sentences: list[str], *, think_s: float = 0.2) -> None:
        self.sentences = sentences
        self.think_s = think_s
        self.sent: list[dict[str, Any]] = []
        self.first_delta_at: list[float] = []
        self.answered_at: list[float] = []
        self.bridge: InterviewBridge | None = None
        self.seq = 1
        self._tasks: set[asyncio.Task[None]] = set()

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        if message["type"] in {"answer", "nudge"}:
            self.answered_at.append(time.monotonic())
            reply = ["Let me put it another way. What did you build?"] if message["type"] == "nudge" else self.sentences
            self._tasks.add(asyncio.create_task(self._reply(reply)))

    async def _reply(self, sentences: list[str]) -> None:
        assert self.bridge is not None
        self.seq += 2
        await self.bridge.on_engine_frame({"type": "turn.start", "seq": self.seq, "speaker": "INTERVIEWER"})
        for index, sentence in enumerate(sentences):
            await asyncio.sleep(self.think_s)
            if index == 0:
                self.first_delta_at.append(time.monotonic())
            await self.bridge.on_engine_frame({"type": "turn.delta", "seq": self.seq, "text": sentence})
        end = {"type": "turn.end", "seq": self.seq, "text": " ".join(sentences), "awaiting": True}
        await self.bridge.on_engine_frame(end)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m["type"] == kind]


class Timeline(BaseObserver):
    """Wall-clock times of the frames that bound each latency stage."""

    def __init__(self) -> None:
        super().__init__()
        self.speech_end: list[float] = []
        self.turn_end: list[float] = []
        self.bot_start: list[float] = []
        self._seen: set[int] = set()

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if id(frame) in self._seen or data.direction != FrameDirection.DOWNSTREAM:
            return
        self._seen.add(id(frame))
        now = time.monotonic()
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self.speech_end.append(now)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self.turn_end.append(now)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self.bot_start.append(now)


async def run_offline(
    settings: VoiceSettings,
    schedule: list[bytes],
    engine: ScriptedEngine,
    *,
    stt: Any = None,
    tts: Any = None,
    timeline: Timeline | None = None,
    timeout_s: float = 120,
) -> InterviewBridge:
    done = asyncio.Event()
    pipeline, bridge = build_pipeline(
        settings,
        engine,
        source=WavSource(schedule, done),
        stt=stt or ScriptedSTT(),
        tts=tts or FakeTTS(),
        sink=PacedOutput(TransportParams(audio_out_enabled=True)),
    )
    engine.bridge = bridge
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=IN_RATE, audio_out_sample_rate=OUT_RATE, enable_metrics=True),
        observers=[timeline] if timeline else None,
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    async def stop() -> None:
        await done.wait()
        await worker.stop_when_done()

    try:
        await asyncio.wait_for(asyncio.gather(runner.run(), stop()), timeout=timeout_s)
    finally:
        await engine.close()
    return bridge


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def summary(values: list[float]) -> str:
    if not values:
        return "n/a"
    ms = [v * 1000 for v in values]
    return f"p50 {statistics.median(ms):6.0f} ms   p95 {percentile(ms, 0.95):6.0f} ms   (n={len(ms)})"
