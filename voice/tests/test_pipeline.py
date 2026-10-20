"""Offline voice pipeline tests (no WebRTC, no network): real Silero VAD and Smart Turn v3 on recorded WAV
fixtures, a scripted STT, a fake TTS that produces real-time audio, a paced output transport and a scripted
engine. Covers answering, playback confirmation, barge-in, the continuation hand-off and silence prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from intrv_voice.bridge import TAKE_YOUR_TIME
from intrv_voice.config import VoiceSettings
from intrv_voice.offline import FakeTTS, ScriptedEngine, read_wav, run_offline, silence

FIXTURES = Path(__file__).parent / "fixtures"


def pcm(name: str) -> bytes:
    return read_wav(FIXTURES / name)


async def run(schedule: list[bytes], engine: ScriptedEngine, tts: FakeTTS, **overrides: Any) -> None:
    await run_offline(VoiceSettings(**overrides), schedule, engine, tts=tts, timeout_s=90)


def kinds(engine: ScriptedEngine, kind: str) -> list[dict[str, Any]]:
    return engine.of(kind)


async def test_answer_is_sent_once_reply_played_and_silence_prompts() -> None:
    engine = ScriptedEngine(["Thanks.", "How did you measure the latency?"])
    tts = FakeTTS(seconds_per_word=0.05)
    await run(
        [silence(0.5), pcm("answer.wav"), silence(9.0)],
        engine,
        tts,
        silence_prompts_secs=(1.5, 3.0, 4.5),
    )
    answers = kinds(engine, "answer")
    assert len(answers) == 1  # pauses inside the answer did not split it
    assert answers[0]["text"].startswith("part1")
    assert answers[0]["meta"] == {"continuation": False}
    spoken = kinds(engine, "spoken")
    assert spoken[0]["interrupted"] is False
    assert spoken[0]["text"] == "Thanks. How did you measure the latency?"  # confirmed after playback
    assert TAKE_YOUR_TIME in tts.spoken_requests  # 1.5 s of silence after the question
    assert kinds(engine, "nudge")  # then the engine is asked to rephrase


async def test_barge_in_mutes_the_reply_and_reports_what_was_heard() -> None:
    long_reply = [
        "Thanks for that.",
        "Before we go on I would like to understand the timing of the interrupt service routine in detail.",
        "Please walk me through how you measured the worst case latency on the bench.",
    ]
    engine = ScriptedEngine(long_reply)
    tts = FakeTTS(seconds_per_word=0.3)
    await run(
        [silence(0.5), pcm("answer.wav"), silence(1.6), pcm("bargein.wav"), silence(4.0)],
        engine,
        tts,
        silence_prompts_secs=(30.0, 31.0, 32.0),
    )
    spoken = kinds(engine, "spoken")
    assert spoken[0]["interrupted"] is True
    assert "walk me through" not in spoken[0]["text"]  # the unplayed sentence was never confirmed
    answers = kinds(engine, "answer")
    assert len(answers) == 2
    # Nothing heard → the second answer continues the first; something heard → a normal new answer.
    assert answers[1]["meta"]["continuation"] is (spoken[0]["text"] == "")
