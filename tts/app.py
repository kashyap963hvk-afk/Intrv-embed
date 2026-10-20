"""Kokoro TTS over HTTP: POST /tts {text, voice, speed} → raw 24 kHz 16-bit mono PCM (audio/L16).

Model files (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) are baked into the image at build time; nothing is
downloaded at runtime. The model is warmed up at start, because the first synthesis on CPU takes seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

MODEL_DIR = os.environ.get("KOKORO_MODEL_DIR", "/models")
SAMPLE_RATE = 24_000
MAX_CHARS = 1_000


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    voice: str = Field(default="af_heart", max_length=40)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    lang: str = Field(default="en-us", max_length=10)


class Engine:
    def __init__(self) -> None:
        from kokoro_onnx import Kokoro

        self.kokoro = Kokoro(f"{MODEL_DIR}/kokoro-v1.0.onnx", f"{MODEL_DIR}/voices-v1.0.bin")
        self.voices = set(self.kokoro.get_voices())
        self.lock = asyncio.Lock()  # one synthesis at a time per process; scale with replicas

    def synthesize(self, request: SpeakIn) -> bytes:
        samples, rate = self.kokoro.create(request.text, voice=request.voice, speed=request.speed, lang=request.lang)
        if rate != SAMPLE_RATE:
            raise RuntimeError(f"unexpected sample rate {rate}")
        return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


state: dict[str, Engine] = {}


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    engine = await asyncio.to_thread(Engine)
    await asyncio.to_thread(engine.synthesize, SpeakIn(text="Hello."))  # warm-up
    state["engine"] = engine
    yield


app = FastAPI(title="Intrv-embed TTS", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok" if "engine" in state else "loading"}


@app.get("/voices")
async def voices() -> list[str]:
    return sorted(state["engine"].voices)


@app.post("/tts")
async def tts(body: SpeakIn) -> Response:
    engine = state.get("engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="loading")
    if body.voice not in engine.voices:
        raise HTTPException(status_code=422, detail=f"unknown voice {body.voice}")
    async with engine.lock:
        audio = await asyncio.to_thread(engine.synthesize, body)
    return Response(audio, media_type=f"audio/L16;rate={SAMPLE_RATE};channels=1")
