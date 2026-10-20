"""One call = one Pipecat pipeline:

transport.input → VAD (Silero, 0.2 s) → STT → user turns (VAD start, Smart Turn v3 stop; barge-in on)
  → InterviewBridge (↔ engine) → TTS → transport.output → SpokenTracker
"""

from __future__ import annotations

from typing import Any

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from intrv_voice.bridge import InterviewBridge, Playback, SpokenTracker
from intrv_voice.config import VoiceSettings
from intrv_voice.engine_link import Link

IN_RATE = 16_000
OUT_RATE = 24_000


def build_pipeline(
    settings: VoiceSettings,
    link: Link,
    *,
    source: Any,
    stt: Any,
    tts: Any,
    sink: Any,
) -> tuple[Pipeline, InterviewBridge]:
    playback = Playback()
    bridge = InterviewBridge(
        link,
        playback,
        grace_secs=settings.answer_grace_secs,
        silence_prompts_secs=settings.silence_prompts_secs,
        ramble_secs=settings.ramble_secs,
    )
    pipeline = Pipeline(
        [
            source,
            VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=settings.vad_stop_secs))),
            stt,
            UserTurnProcessor(
                user_turn_strategies=UserTurnStrategies(
                    start=[VADUserTurnStartStrategy()],
                    stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())],
                )
            ),
            bridge,
            tts,
            sink,
            SpokenTracker(playback),
        ]
    )
    return pipeline, bridge
