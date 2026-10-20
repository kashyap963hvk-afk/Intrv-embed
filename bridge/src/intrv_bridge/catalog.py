"""What this PC can serve: .gguf files in the folders the user added (run with llama-server), and models of a
local Ollama / LM Studio / vLLM server that is already running."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("intrv_bridge")

GGUF_MAGIC = b"GGUF"
LOCAL_SERVERS = {
    "ollama": "http://127.0.0.1:11434/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "vllm": "http://127.0.0.1:8000/v1",
}

Endpoint = Callable[[], Awaitable[tuple[str, str]]]  # → (OpenAI-compatible base URL, model name to send)


@dataclass(slots=True)
class ModelEntry:
    id: str
    name: str
    source: str  # gguf | ollama | lmstudio | vllm
    endpoint: Endpoint
    path: Path | None = None
    size_bytes: int | None = None
    quant: str | None = None
    context_length: int | None = None
    arch: str | None = None
    removable: bool = False

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "name": self.name, "source": self.source}
        for key in ("size_bytes", "quant", "context_length", "arch"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


@dataclass(slots=True)
class GgufInfo:
    arch: str | None = None
    context_length: int | None = None
    quant: str | None = None
    name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def is_gguf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == GGUF_MAGIC
    except OSError:
        return False


def read_gguf_info(path: Path) -> GgufInfo:
    """Metadata via the `gguf` package (llama.cpp's reader). Missing fields stay None."""
    from gguf import GGUFReader  # local import: the reader memory-maps the file, only when scanning

    reader = GGUFReader(str(path), "r")

    def text(key: str) -> str | None:
        item = reader.fields.get(key)
        if item is None or not item.data:
            return None
        return bytes(item.parts[item.data[0]]).decode("utf-8", errors="replace")

    def number(key: str) -> int | None:
        item = reader.fields.get(key)
        return int(item.parts[item.data[0]][0]) if item is not None and item.data else None

    arch = text("general.architecture")
    file_type = number("general.file_type")
    return GgufInfo(
        arch=arch,
        context_length=number(f"{arch}.context_length") if arch else None,
        quant=_FILE_TYPES.get(file_type) if file_type is not None else None,
        name=text("general.name"),
    )


# llama.cpp `general.file_type` values for the common quantisations.
_FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
               12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
               32: "BF16"}  # fmt: skip


def model_id(path: Path) -> str:
    """Stable across rescans and drive letters: file name + size + a hash of the first megabyte."""
    with path.open("rb") as handle:
        head = hashlib.sha256(handle.read(1 << 20)).hexdigest()[:12]
    return f"gguf:{path.stem}:{head}"


def scan_folders(
    folders: list[Path], endpoint_for: Callable[[Path, bool], Endpoint], removable: Callable[[Path], bool]
) -> list[ModelEntry]:
    entries: list[ModelEntry] = []
    seen: set[Path] = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.gguf")):
            real = path.resolve()
            if real in seen or not real.is_file() or not is_gguf(real):
                continue
            if "-of-" in real.stem and not real.stem.endswith("00001-of-" + real.stem.split("-of-")[-1]):
                continue  # split models: only the first shard is loadable
            seen.add(real)
            try:
                info = read_gguf_info(real)
            except Exception:  # a corrupt or exotic file must not stop the scan
                log.warning("could not read GGUF metadata from %s", real)
                info = GgufInfo()
            on_removable = removable(real)
            entries.append(
                ModelEntry(
                    id=model_id(real),
                    name=info.name or real.stem,
                    source="gguf",
                    endpoint=endpoint_for(real, on_removable),
                    path=real,
                    size_bytes=real.stat().st_size,
                    quant=info.quant,
                    context_length=info.context_length,
                    arch=info.arch,
                    removable=on_removable,
                )
            )
    return entries


async def detect_servers(http: httpx.AsyncClient) -> list[ModelEntry]:
    """Models of local OpenAI-compatible servers that are already running."""
    entries: list[ModelEntry] = []
    for source, base in LOCAL_SERVERS.items():
        try:
            response = await http.get(f"{base}/models", timeout=2)
            response.raise_for_status()
            names = [m["id"] for m in response.json().get("data", []) if isinstance(m.get("id"), str)]
        except (httpx.HTTPError, ValueError, KeyError, AttributeError):
            continue
        for name in names:

            async def endpoint(base: str = base, name: str = name) -> tuple[str, str]:
                return base, name

            entries.append(ModelEntry(id=f"{source}:{name}", name=name, source=source, endpoint=endpoint))
    return entries
