"""GGUF_MANAGED models (self-host mode): the backend runs llama.cpp's `llama-server` for a `.gguf` file.

CLAUDE.md rule 3: user-supplied paths are never opened unless they resolve (symlinks followed) inside one of
`GGUF_ALLOWED_DIRS`. Each model runs as one child process on a free 127.0.0.1 port, is health-checked before
use, and is stopped after `GGUF_IDLE_UNLOAD_S` without requests. If the process dies, the next request starts
it again.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import httpx2

from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.providers.openai_compat import OpenAICompatibleProvider
from app.llm.types import ChatChunk, ChatRequest, ProviderError

log = get_logger("app.llm.gguf")

GGUF_MAGIC = b"GGUF"
START_TIMEOUT_S = 300.0  # large models from slow disks take minutes to load
DEFAULT_CONTEXT = 8192


class GgufPathError(Exception):
    pass


def resolve_model_path(raw: str, allowed_dirs: list[str]) -> Path:
    """The real path of an allowed .gguf file, or GgufPathError. Relative paths, `..` and symlinks that leave
    the allowed directories are all refused."""
    if not allowed_dirs:
        raise GgufPathError("Local GGUF models are disabled on this server (GGUF_ALLOWED_DIRS is empty).")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise GgufPathError("Give the full path of the .gguf file.")
    try:
        real = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GgufPathError("That file does not exist.") from exc
    roots = [Path(d).resolve(strict=False) for d in allowed_dirs]
    if not any(real.is_relative_to(root) for root in roots):
        raise GgufPathError("That file is outside the folders allowed for local models.")
    if real.suffix.lower() != ".gguf" or not real.is_file():
        raise GgufPathError("Only .gguf model files can be used.")
    with real.open("rb") as handle:
        if handle.read(4) != GGUF_MAGIC:
            raise GgufPathError("That file is not a GGUF model.")
    return real


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@dataclass(slots=True)
class _Server:
    process: asyncio.subprocess.Process
    port: int
    last_used: float = field(default_factory=time.monotonic)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


class GgufRuntime:
    """One per process; serialises start-up so concurrent calls share one server per model."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(5.0))
        self._servers: dict[Path, _Server] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    def command(self, path: Path, port: int, context: int = DEFAULT_CONTEXT) -> list[str]:
        binary = shutil.which(self.settings.llama_server_bin) or self.settings.llama_server_bin
        return [binary, "-m", str(path), "--host", "127.0.0.1", "--port", str(port), "-c", str(context), "--jinja"]

    async def endpoint(self, raw_path: str) -> str:
        path = resolve_model_path(raw_path, self.settings.gguf_allowed_dirs)
        async with self._lock:
            server = self._servers.get(path)
            if server is not None and server.process.returncode is None:
                server.last_used = time.monotonic()
                return server.base_url
            server = await self._start(path)
            self._servers[path] = server
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.create_task(self._reap_idle())
            return server.base_url

    async def _start(self, path: Path) -> _Server:
        port = _free_port()
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command(path, port),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise GgufPathError(
                f"llama-server was not found ({self.settings.llama_server_bin}); install llama.cpp or set "
                "LLAMA_SERVER_BIN."
            ) from exc
        server = _Server(process=process, port=port)
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if process.returncode is not None:
                raise GgufPathError(f"llama-server exited while loading the model (code {process.returncode}).")
            with contextlib.suppress(httpx.HTTPError):
                if (await self._http.get(f"http://127.0.0.1:{port}/health")).status_code == 200:  # noqa: PLR2004
                    log.info("gguf.started", model=path.name, port=port)
                    return server
            await asyncio.sleep(0.5)
        await self._stop(server)
        raise GgufPathError("llama-server did not become ready in time.")

    async def _stop(self, server: _Server) -> None:
        if server.process.returncode is None:
            server.process.terminate()
            try:
                await asyncio.wait_for(server.process.wait(), timeout=10)
            except TimeoutError:
                server.process.kill()
                await server.process.wait()

    async def _reap_idle(self) -> None:
        while self._servers:
            await asyncio.sleep(30)
            cutoff = time.monotonic() - self.settings.gguf_idle_unload_s
            async with self._lock:
                for path, server in list(self._servers.items()):
                    if server.last_used < cutoff or server.process.returncode is not None:
                        await self._stop(server)
                        del self._servers[path]
                        log.info("gguf.unloaded", model=path.name)

    async def close(self) -> None:
        async with self._lock:
            for server in self._servers.values():
                await self._stop(server)
            self._servers.clear()
        if self._reaper is not None:
            self._reaper.cancel()
        await self._http.aclose()


class GgufProvider:
    """OpenAI-compatible calls to the llama-server this process runs for the profile's model file."""

    def __init__(self, runtime: GgufRuntime, path: str, http_client: httpx2.AsyncClient) -> None:
        self._runtime = runtime
        self._path = path
        self._http = http_client

    async def _provider(self) -> OpenAICompatibleProvider:
        try:
            base_url = await self._runtime.endpoint(self._path)
        except GgufPathError as exc:
            raise ProviderError("not_found", str(exc)) from exc
        return OpenAICompatibleProvider(base_url=base_url, api_key=None, http_client=self._http)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        provider = await self._provider()
        async for chunk in provider.stream(request):
            yield chunk

    async def list_models(self) -> list[str]:
        return await (await self._provider()).list_models()
