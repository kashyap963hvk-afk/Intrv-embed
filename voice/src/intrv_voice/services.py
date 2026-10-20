"""Speech services: an HTTP TTS client for the Kokoro container, and the STT backends.

Kokoro (kokoro-onnx → phonemizer / espeak-ng, GPL) and Piper (GPL) never run inside this process; they live in
their own container (`tts/`), reached over HTTP. faster-whisper (MIT) runs in-process with one model shared by all
calls in the worker; Groq or any OpenAI-compatible Whisper endpoint are the hosted alternatives.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TextAggregationMode, TTSService
from pipecat.transcriptions.language import Language

from intrv_voice.config import VoiceSettings

TTS_SAMPLE_RATE = 24_000
CHUNK_BYTES = 4_800  # 100 ms of 24 kHz s16le


class HttpTTSService(TTSService):
    """POST {text, voice, speed} to the TTS container; it answers raw 24 kHz 16-bit mono PCM."""

    def __init__(
        self, base_url: str, *, voice: str, speed: float = 1.0, http: httpx.AsyncClient | None = None, **kwargs: Any
    ) -> None:
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            text_aggregation_mode=TextAggregationMode.TOKEN,  # the engine already sends whole sentences
            max_consecutive_zero_audio_contexts=0,  # frequent barge-ins must not write the service off
            sample_rate=TTS_SAMPLE_RATE,
            settings=TTSSettings(model=None, voice=voice, language=None),
            **kwargs,
        )
        self._url = base_url.rstrip("/") + "/tts"
        self._voice = voice
        self._speed = speed
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0))

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        body = {"text": text, "voice": self._voice, "speed": self._speed}
        try:
            async with self._http.stream("POST", self._url, json=body) as response:
                response.raise_for_status()
                first = True
                async for chunk in response.aiter_bytes(CHUNK_BYTES):
                    if first:
                        await self.stop_ttfb_metrics()
                        first = False
                    yield TTSAudioRawFrame(chunk, TTS_SAMPLE_RATE, 1, context_id=context_id)
        except httpx.HTTPError as exc:
            yield ErrorFrame(f"TTS service unavailable: {exc}")

    async def cleanup(self) -> None:
        await super().cleanup()
        await self._http.aclose()


_whisper_lock = threading.Lock()
_whisper_models: dict[tuple[str, str, str], Any] = {}


def _shared_whisper_class() -> type[STTService]:
    from faster_whisper import WhisperModel
    from pipecat.services.whisper.stt import WhisperSTTService

    class SharedWhisperSTTService(WhisperSTTService):
        """One loaded model per worker process instead of one per call."""

        def _load(self) -> None:
            key = (str(getattr(self._settings, "model", "")), self._device, self._compute_type)
            with _whisper_lock:
                if key not in _whisper_models:
                    _whisper_models[key] = WhisperModel(key[0], device=key[1], compute_type=key[2])
            self._model = _whisper_models[key]

    return SharedWhisperSTTService


def make_stt(settings: VoiceSettings) -> STTService:
    if settings.stt_backend == "groq":
        from pipecat.services.groq.stt import GroqSTTService

        if settings.groq_api_key is None:
            raise ValueError("STT_BACKEND=groq needs GROQ_API_KEY")
        return GroqSTTService(
            api_key=settings.groq_api_key.get_secret_value(),
            settings=GroqSTTService.Settings(model="whisper-large-v3-turbo", language=Language(settings.stt_language)),
        )
    if settings.stt_backend == "openai_compatible":
        from pipecat.services.openai.stt import OpenAISTTService

        if not settings.stt_base_url:
            raise ValueError("STT_BACKEND=openai_compatible needs STT_BASE_URL")
        key = settings.stt_api_key.get_secret_value() if settings.stt_api_key else "not-needed"
        return OpenAISTTService(
            api_key=key,
            base_url=settings.stt_base_url,
            settings=OpenAISTTService.Settings(model=settings.stt_model, language=Language(settings.stt_language)),
        )
    whisper = _shared_whisper_class()
    return whisper(
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        settings=whisper.Settings(model=settings.whisper_model, language=Language(settings.stt_language)),  # type: ignore[attr-defined]
    )
