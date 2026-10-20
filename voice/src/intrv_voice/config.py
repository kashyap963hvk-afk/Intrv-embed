"""Voice worker settings (environment variables)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    backend_ws_url: str = "ws://backend:8000"  # the interview engine's WebSocket base (internal network)
    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    stun_urls: list[str] = Field(default_factory=lambda: ["stun:stun.l.google.com:19302"])
    turn_urls: list[str] = Field(default_factory=list)
    turn_username: str | None = None
    turn_credential: SecretStr | None = None

    stt_backend: Literal["faster_whisper", "groq", "openai_compatible"] = "faster_whisper"
    whisper_model: str = "/models/whisper"  # a CTranslate2 directory baked into the image (no hub download)
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    groq_api_key: SecretStr | None = None
    stt_base_url: str | None = None
    stt_api_key: SecretStr | None = None
    stt_model: str = "whisper-1"
    stt_language: str = "en"

    tts_url: str = "http://tts:8880"  # the Kokoro container (separate because of its GPL dependencies)
    tts_voice: str = "af_heart"
    tts_speed: float = 1.0

    vad_stop_secs: float = 0.2
    answer_grace_secs: float = 0.6  # wait this long after a turn ends before sending, in case the candidate goes on
    silence_prompts_secs: tuple[float, float, float] = (7.0, 20.0, 45.0)
    ramble_secs: float = 120.0
