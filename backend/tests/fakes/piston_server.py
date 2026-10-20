"""A Piston-compatible test server (POST /api/v2/execute) that really compiles and runs C with the host's gcc.

Tests only: it has no sandboxing. Production uses the Piston container (no network, cgroup limits).
"""

from __future__ import annotations

import asyncio
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI


async def _run(cmd: list[str], cwd: str, stdin: str, timeout_s: float) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin.encode()), timeout=timeout_s)
        signal = None
    except TimeoutError:
        process.kill()
        out, err = await process.communicate()
        signal = "SIGKILL"
    stdout, stderr = out.decode(errors="replace"), err.decode(errors="replace")
    return {"stdout": stdout, "stderr": stderr, "output": stdout + stderr, "code": process.returncode, "signal": signal}


def create_app() -> FastAPI:
    app = FastAPI()
    app.state.requests = []

    @app.post("/api/v2/execute")
    async def execute(body: dict[str, Any]) -> dict[str, Any]:
        app.state.requests.append(body)
        if body["language"] != "c":
            return {"message": f"runtime {body['language']} is unknown"}
        with tempfile.TemporaryDirectory() as work:
            source = body["files"][0]
            await asyncio.to_thread(Path(work, source["name"]).write_text, source["content"])
            compiled = await _run(["gcc", "-std=c11", "-O2", "-o", "main", source["name"], "-lm"], work, "", 30)
            result: dict[str, Any] = {"language": "c", "version": "13.3.0", "compile": compiled}
            if compiled["code"] != 0:
                result["run"] = {"stdout": "", "stderr": "", "output": "", "code": None, "signal": None}
                return result
            result["run"] = await _run(["./main"], work, body.get("stdin", ""), body.get("run_timeout", 3000) / 1000)
            return result

    return app


@contextmanager
def running_piston() -> Iterator[tuple[str, FastAPI]]:
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
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
