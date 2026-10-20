"""Voice worker HTTP API: WebRTC signalling for the candidate's voice room.

POST  /voice/{session_id}/offer   SDP offer → answer (Authorization: Bearer <invite token>)
PATCH /voice/{session_id}/offer   trickled ICE candidates for that connection
GET   /healthz

The invite token is checked by the interview engine itself: the worker opens the engine socket with it before
answering the offer, so an invalid or expired link never gets a media connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from intrv_voice.config import VoiceSettings
from intrv_voice.engine_link import EngineLink, LinkRefusedError
from intrv_voice.pipeline import IN_RATE, OUT_RATE, build_pipeline
from intrv_voice.services import HttpTTSService, make_stt

log = logging.getLogger("intrv_voice")


def server_ice_servers(settings: VoiceSettings) -> list[IceServer]:
    servers = [IceServer(urls=url) for url in settings.stun_urls]
    if settings.turn_urls and settings.turn_username and settings.turn_credential:
        servers += [
            IceServer(urls=url, username=settings.turn_username, credential=settings.turn_credential.get_secret_value())
            for url in settings.turn_urls
        ]
    return servers


class Calls:
    """Live calls: pc_id → session, so ICE patches and renegotiations stay bound to their interview."""

    def __init__(self) -> None:
        self.owner: dict[str, uuid.UUID] = {}
        self.tasks: set[asyncio.Task[None]] = set()


async def run_call(settings: VoiceSettings, connection: SmallWebRTCConnection, link: EngineLink) -> None:
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=IN_RATE,
            audio_out_sample_rate=OUT_RATE,
        ),
    )
    pipeline, bridge = build_pipeline(
        settings,
        link,
        source=transport.input(),
        stt=make_stt(settings),
        tts=HttpTTSService(settings.tts_url, voice=settings.tts_voice, speed=settings.tts_speed),
        sink=transport.output(),
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=IN_RATE, audio_out_sample_rate=OUT_RATE, enable_metrics=True, enable_usage_metrics=True
        ),
        idle_timeout_secs=None,  # the engine owns timing; silence prompts live in the bridge
    )

    @transport.event_handler("on_client_connected")
    async def connected(_transport: Any, _connection: Any) -> None:
        link.listen(bridge.on_engine_frame)
        await bridge.begin(link.first_frame)

    @transport.event_handler("on_client_disconnected")
    async def disconnected(_transport: Any, _connection: Any) -> None:
        await worker.cancel(reason="client disconnected")

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    try:
        await runner.run()
    finally:
        await link.close()


def create_app(settings: VoiceSettings | None = None) -> FastAPI:
    settings = settings or VoiceSettings()
    handler = SmallWebRTCRequestHandler(ice_servers=server_ice_servers(settings))
    calls = Calls()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await handler.close()
        for task in list(calls.tasks):
            task.cancel()

    app = FastAPI(title="Intrv-embed voice", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["POST", "PATCH", "GET"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def token_of(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Open the interview from your invitation link.")
        return authorization.removeprefix("Bearer ").strip()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/voice/{session_id}/offer")
    async def offer(
        session_id: uuid.UUID, request: Request, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        token = token_of(authorization)
        body = SmallWebRTCRequest.from_dict(await request.json())
        if body.pc_id:  # renegotiation of an existing call
            if calls.owner.get(body.pc_id) != session_id:
                raise HTTPException(status_code=403, detail="This connection belongs to another interview.")
            renegotiated = await handler.handle_web_request(request=body, webrtc_connection_callback=_never)
            if renegotiated is None:
                raise HTTPException(status_code=409, detail="The connection could not be renegotiated.")
            return dict(renegotiated)
        link = EngineLink(settings.backend_ws_url, session_id, token)
        try:
            first = await link.connect()
        except LinkRefusedError as exc:
            raise HTTPException(status_code=403, detail="This interview link is not valid or has expired.") from exc
        if first.get("status") not in {None, "PLANNED", "LIVE"} and first.get("type") != "transcript":
            await link.close()
            raise HTTPException(status_code=409, detail="This interview is not open.")

        async def on_connection(connection: SmallWebRTCConnection) -> None:
            task = asyncio.create_task(run_call(settings, connection, link))
            calls.tasks.add(task)
            task.add_done_callback(calls.tasks.discard)

        try:
            answer = await handler.handle_web_request(request=body, webrtc_connection_callback=on_connection)
        except Exception:
            await link.close()
            raise
        if answer is None:
            await link.close()
            raise HTTPException(status_code=409, detail="The connection could not be established.")
        calls.owner[str(answer["pc_id"])] = session_id
        return dict(answer)

    @app.patch("/voice/{session_id}/offer")
    async def candidates(
        session_id: uuid.UUID, request: Request, authorization: str | None = Header(default=None)
    ) -> dict[str, str]:
        token_of(authorization)
        body = await request.json()
        pc_id = str(body.get("pc_id") or body.get("pcId") or "")
        if calls.owner.get(pc_id) != session_id:
            raise HTTPException(status_code=404, detail="Unknown connection.")
        items = [
            IceCandidate(
                candidate=c.get("candidate", ""),
                sdp_mid=c.get("sdp_mid") or c.get("sdpMid") or "",
                sdp_mline_index=int(c.get("sdp_mline_index", c.get("sdpMLineIndex", 0)) or 0),
            )
            for c in body.get("candidates", [])
        ]
        await handler.handle_patch_request(SmallWebRTCPatchRequest(pc_id=pc_id, candidates=items))
        return {"status": "success"}

    return app


async def _never(_connection: SmallWebRTCConnection) -> None:
    """Renegotiations reuse the existing call; the handler does not call back for them."""
