"""llama-server lifecycle for .gguf files, and removable-drive detection.

- One llama-server per loaded model on a free 127.0.0.1 port; never bound to other interfaces.
- `--no-mmap` when the file is on a removable drive: otherwise generation page-faults from USB and stutters.
- Idle models are unloaded after `idle_unload_s`.
- The binary is either a path the user gave, one on PATH, or a llama.cpp release the bridge downloaded and
  verified against the SHA-256 published with that release (see `download_llama_server`).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import platform
import shutil
import socket
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil

log = logging.getLogger("intrv_bridge")

START_TIMEOUT_S = 600.0  # a 7B model from USB 2.0 takes ~2.5 minutes to read
RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"


def is_removable(path: Path) -> bool:
    """True when the file lives on a removable drive (USB stick, SD card)."""
    real = str(path.resolve())
    best: psutil._common.sdiskpart | None = None
    for part in psutil.disk_partitions(all=False):
        if real.startswith(part.mountpoint) and (best is None or len(part.mountpoint) > len(best.mountpoint)):
            best = part
    if best is None:
        return False
    options = best.opts.lower()
    mountpoint = str(best.mountpoint)
    if "removable" in str(options):  # Windows
        return True
    if platform.system() == "Darwin":
        return mountpoint.startswith("/Volumes/")
    return mountpoint.startswith(("/media/", "/run/media/", "/mnt/usb"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@dataclass(slots=True)
class _Loaded:
    process: asyncio.subprocess.Process
    port: int
    last_used: float = field(default_factory=time.monotonic)


class LlamaRuntime:
    def __init__(self, binary: str | None, *, idle_unload_s: int = 900, gpu_layers: str = "auto") -> None:
        self.binary = binary or shutil.which("llama-server") or "llama-server"
        self.idle_unload_s = idle_unload_s
        self.gpu_layers = gpu_layers
        self._loaded: dict[Path, _Loaded] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None

    def command(self, path: Path, port: int, *, removable: bool, context: int | None = None) -> list[str]:
        cmd = [self.binary, "-m", str(path), "--host", "127.0.0.1", "--port", str(port), "--jinja"]
        cmd += ["-c", str(min(context or 8192, 32768))]
        if self.gpu_layers != "auto":
            cmd += ["-ngl", self.gpu_layers]
        if removable:
            cmd.append("--no-mmap")
        return cmd

    def is_loaded(self, path: Path) -> bool:
        loaded = self._loaded.get(path)
        return loaded is not None and loaded.process.returncode is None

    async def base_url(self, path: Path, *, removable: bool, context: int | None = None) -> str:
        async with self._lock:
            loaded = self._loaded.get(path)
            if loaded is None or loaded.process.returncode is not None:
                loaded = await self._start(path, removable=removable, context=context)
                self._loaded[path] = loaded
                if self._reaper is None or self._reaper.done():
                    self._reaper = asyncio.create_task(self._reap())
            loaded.last_used = time.monotonic()
            return f"http://127.0.0.1:{loaded.port}/v1"

    async def _start(self, path: Path, *, removable: bool, context: int | None) -> _Loaded:
        port = _free_port()
        process = await asyncio.create_subprocess_exec(
            *self.command(path, port, removable=removable, context=context),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        deadline = time.monotonic() + START_TIMEOUT_S
        async with httpx.AsyncClient(timeout=3) as http:
            while time.monotonic() < deadline:
                if process.returncode is not None:
                    raise RuntimeError(f"llama-server exited while loading {path.name} (code {process.returncode})")
                with contextlib.suppress(httpx.HTTPError):
                    if (await http.get(f"http://127.0.0.1:{port}/health")).status_code == 200:
                        log.info("loaded %s on port %s", path.name, port)
                        return _Loaded(process=process, port=port)
                await asyncio.sleep(0.5)
        process.terminate()
        raise RuntimeError(f"{path.name} did not finish loading in time")

    async def _reap(self) -> None:
        while self._loaded:
            await asyncio.sleep(30)
            cutoff = time.monotonic() - self.idle_unload_s
            async with self._lock:
                for path, loaded in list(self._loaded.items()):
                    if loaded.last_used < cutoff or loaded.process.returncode is not None:
                        await _stop(loaded.process)
                        del self._loaded[path]
                        log.info("unloaded %s (idle)", path.name)

    async def close(self) -> None:
        async with self._lock:
            for loaded in self._loaded.values():
                await _stop(loaded.process)
            self._loaded.clear()
        if self._reaper is not None:
            self._reaper.cancel()


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()


def release_asset_stem() -> str:
    """The llama.cpp release asset for this machine (CPU builds; GPU users point to their own binary)."""
    system, machine = platform.system(), platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    if system == "Windows":
        return f"bin-win-cpu-{arch}"
    if system == "Darwin":
        return f"bin-macos-{arch}"
    return f"bin-ubuntu-{arch}"


ARCHIVE_TYPES = (".zip", ".tar.gz")


def pick_asset(assets: list[dict[str, object]], stem: str) -> dict[str, object] | None:
    for extension in ARCHIVE_TYPES:
        for asset in assets:
            if str(asset.get("name", "")).endswith(stem + extension):
                return asset
    return None


async def download_llama_server(target_dir: Path, http: httpx.AsyncClient) -> Path:
    """Download the latest llama.cpp release for this OS, verify its SHA-256 digest from the release metadata,
    and unpack it. Refuses assets without a published digest."""
    release = (await http.get(RELEASES_API, timeout=30)).raise_for_status().json()
    stem = release_asset_stem()
    asset = pick_asset(release.get("assets", []), stem)
    if asset is None:
        raise RuntimeError(f"no llama.cpp release asset for {stem}; install llama-server yourself")
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:"):
        raise RuntimeError("the release asset has no SHA-256 digest; refusing to run an unverified binary")
    await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
    archive = target_dir / str(asset["name"])
    sha = hashlib.sha256()
    url = str(asset["browser_download_url"])
    async with http.stream("GET", url, follow_redirects=True, timeout=600) as response:
        response.raise_for_status()
        with archive.open("wb") as handle:
            async for block in response.aiter_bytes():
                sha.update(block)
                handle.write(block)
    if sha.hexdigest() != digest.removeprefix("sha256:"):
        archive.unlink(missing_ok=True)
        raise RuntimeError("downloaded llama.cpp archive failed the SHA-256 check")
    return await asyncio.to_thread(_unpack, archive, target_dir)


def _unpack(archive: Path, target_dir: Path) -> Path:
    root = target_dir.resolve()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                if not (root / member).resolve().is_relative_to(root):
                    raise RuntimeError("archive contains a path outside the target folder")
            bundle.extractall(root)  # noqa: S202 - every member was checked above
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(root, filter="data")  # refuses absolute paths, `..`, links out and devices
    archive.unlink(missing_ok=True)
    name = "llama-server.exe" if platform.system() == "Windows" else "llama-server"
    binary = next(target_dir.rglob(name), None)
    if binary is None:
        raise RuntimeError("llama-server not found in the release archive")
    binary.chmod(0o755)
    return binary
