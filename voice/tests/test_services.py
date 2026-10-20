"""HTTP TTS client: streams PCM from the TTS container in 100 ms frames; a dead container is an error frame, not
a crash. STT selection follows the settings."""

from __future__ import annotations

import json

import httpx
import pytest
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame

from intrv_voice.config import VoiceSettings
from intrv_voice.services import CHUNK_BYTES, HttpTTSService, make_stt


async def collect(service: HttpTTSService, text: str) -> list[object]:
    return [frame async for frame in service.run_tts(text, "ctx-1")]


async def test_http_tts_streams_pcm_frames() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=bytes(CHUNK_BYTES * 3 + 100))

    service = HttpTTSService(
        "http://tts:8880", voice="af_heart", speed=1.1, http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    frames = await collect(service, "Hello there.")
    audio = [f for f in frames if isinstance(f, TTSAudioRawFrame)]
    assert sum(len(f.audio) for f in audio) == CHUNK_BYTES * 3 + 100
    assert all(f.sample_rate == 24_000 for f in audio)
    assert seen == [{"text": "Hello there.", "voice": "af_heart", "speed": 1.1}]


async def test_http_tts_failure_is_an_error_frame() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "loading"})

    service = HttpTTSService(
        "http://tts:8880", voice="af_heart", http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    frames = await collect(service, "Hello.")
    assert len(frames) == 1
    assert isinstance(frames[0], ErrorFrame)


def test_stt_backend_needs_its_credentials() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        make_stt(VoiceSettings(stt_backend="groq"))
    with pytest.raises(ValueError, match="STT_BASE_URL"):
        make_stt(VoiceSettings(stt_backend="openai_compatible"))
    groq = make_stt(VoiceSettings(stt_backend="groq", groq_api_key="gsk_test"))
    assert type(groq).__name__ == "GroqSTTService"
