"""`make voice-bench`: per-stage latency of the voice pipeline on this machine (p50 / p95).

    uv run python -m intrv_voice.bench --rounds 5 [--stt configured] [--tts http]

Stages (what the candidate perceives is "total"):
  end of turn   speech stops → turn judged complete (Silero VAD + Smart Turn v3, plus STT finalising)
  grace         turn complete → answer sent (configurable pause, protects candidates who pause mid-answer)
  engine        answer sent → first sentence from the engine (scripted here: --think-ms; real LLMs vary)
  tts + play    first sentence → first audio leaving the worker
  total         speech stops → interviewer starts speaking
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from intrv_voice.config import VoiceSettings
from intrv_voice.offline import FakeTTS, ScriptedEngine, Timeline, read_wav, run_offline, silence, summary
from intrv_voice.services import HttpTTSService, make_stt

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "answer.wav"


def pair(starts: list[float], ends: list[float]) -> list[float]:
    """For each end, the time since the latest start before it."""
    out = []
    for end in ends:
        before = [s for s in starts if s <= end]
        if before:
            out.append(end - before[-1])
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--stt", choices=["scripted", "configured"], default="scripted")
    parser.add_argument("--tts", choices=["fake", "http"], default="fake")
    parser.add_argument("--think-ms", type=int, default=300)
    parser.add_argument("--audio", type=Path, default=FIXTURE)
    args = parser.parse_args()
    settings = VoiceSettings(silence_prompts_secs=(600.0, 601.0, 602.0))
    answer = read_wav(args.audio)
    schedule = [silence(0.5)]
    for _ in range(args.rounds):
        schedule += [answer, silence(6.0)]
    engine = ScriptedEngine(["Okay.", "How did you measure that on the bench?"], think_s=args.think_ms / 1000)
    timeline = Timeline()
    stt = make_stt(settings) if args.stt == "configured" else None
    tts = HttpTTSService(settings.tts_url, voice=settings.tts_voice) if args.tts == "http" else FakeTTS(0.25)
    await run_offline(settings, schedule, engine, stt=stt, tts=tts, timeline=timeline, timeout_s=args.rounds * 40 + 30)
    sent = engine.answered_at
    print(f"voice pipeline latency, {args.rounds} rounds (stt={args.stt}, tts={args.tts}, engine={args.think_ms} ms)")
    print(f"  end of turn  {summary(pair(timeline.speech_end, timeline.turn_end))}")
    print(f"  grace        {summary(pair(timeline.turn_end, sent))}")
    print(f"  engine       {summary(pair(sent, engine.first_delta_at))}")
    print(f"  tts + play   {summary(pair(engine.first_delta_at, timeline.bot_start))}")
    print(f"  total        {summary(pair(timeline.speech_end, timeline.bot_start))}")


if __name__ == "__main__":
    asyncio.run(main())
