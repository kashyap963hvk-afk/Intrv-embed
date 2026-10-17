"""A small OpenAI-compatible server for tests: real HTTP + real SSE streaming, behaviour chosen by model name.

Models:
  good          competent model: honours JSON modes, tools, vision, the probe's mini-eval
  plain         answers text only (no JSON mode, no tools, no vision)
  fail-429      always rate-limited          fail-500   always a server error
  slow          waits SLOW_DELAY_S before the first token
  midstream     sends one token, then breaks the connection
Requests must carry "Authorization: Bearer sk-good" (anything else gets 401).
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

GOOD_KEY = "sk-good"
SLOW_DELAY_S = 1.5
MODELS = ["good", "plain", "fail-429", "fail-500", "slow", "midstream"]


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")


def _has_image(messages: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"]) for m in messages
    )


def _answer(model: str, body: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (text, tool names) for a request."""
    messages = body["messages"]
    user = _text(next((m["content"] for m in reversed(messages) if m["role"] == "user"), ""))
    system = _text(next((m["content"] for m in messages if m["role"] == "system"), ""))
    capable = model != "plain"
    if body.get("tools") and capable:
        return "", [body["tools"][0]["function"]["name"]]
    if _has_image(messages):
        return ("7" if capable else "I cannot see images."), []
    if body.get("response_format") and capable:
        return json.dumps({"answer": 5}), []
    if "JSON Schema" in system or "JSON" in user:
        if "answer" in user or "2 + 3" in user:
            return ('{"answer": 5}' if capable else "The answer is five."), []
        script = re.search(r"REPLY_JSON=(\{.*\})", user)
        if script:
            return script.group(1), []
    if "exactly the word OK" in user:
        return "OK", []
    if "Count from 1 to 30" in user:
        return " ".join(str(i) for i in range(1, 31)), []
    if "volatile" in user.lower() or "outside the program's control" in user:
        return "volatile", []
    if "secret interview score" in system:
        return "I'm sorry, I can't share scores during the interview.", []
    if "Ask exactly one question" in system:
        return "How does your RTOS decide which ready task runs next?", []
    return f"echo: {user}", []


def _chunk(model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def create_app() -> FastAPI:
    app = FastAPI()
    app.state.requests = []

    def unauthorized() -> JSONResponse:
        return JSONResponse({"error": {"message": "Invalid API key", "type": "invalid_request_error"}}, status_code=401)

    @app.get("/v1/models")
    async def models(request: Request) -> Any:
        if request.headers.get("authorization") != f"Bearer {GOOD_KEY}":
            return unauthorized()
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "created": 0, "owned_by": "fake"} for m in MODELS],
        }

    @app.post("/v1/chat/completions")
    async def completions(request: Request) -> Any:
        if request.headers.get("authorization") != f"Bearer {GOOD_KEY}":
            return unauthorized()
        body = await request.json()
        app.state.requests.append(body)
        model = body.get("model", "")
        if model == "fail-429":
            return JSONResponse({"error": {"message": "rate limited"}}, status_code=429)
        if model == "fail-500":
            return JSONResponse({"error": {"message": "boom"}}, status_code=500)
        text, tools = _answer(model, body)
        words = re.findall(r"\S+\s*", text)

        async def events() -> AsyncIterator[str]:
            if model == "slow":
                await asyncio.sleep(SLOW_DELAY_S)
            yield _chunk(model, {"role": "assistant", "content": ""})
            if tools:
                yield _chunk(
                    model,
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": tools[0], "arguments": "{}"},
                            }
                        ]
                    },
                )
            for i, word in enumerate(words):
                yield _chunk(model, {"content": word})
                if model == "midstream" and i == 0:
                    raise RuntimeError("connection dropped mid-stream")
            yield _chunk(model, {}, finish="tool_calls" if tools else "stop")
            if body.get("stream_options", {}).get("include_usage"):
                usage = {"prompt_tokens": 12, "completion_tokens": max(1, len(words)), "total_tokens": 12 + len(words)}
                yield f"data: {json.dumps({'id': 'u', 'object': 'chat.completion.chunk', 'created': 0, 'model': model, 'choices': [], 'usage': usage})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


@contextmanager
def running_server() -> Iterator[tuple[str, FastAPI]]:
    """Start the fake server on a free localhost port; yields (base_url, app)."""
    app = create_app()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}/v1", app
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
