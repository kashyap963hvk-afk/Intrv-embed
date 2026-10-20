"""`intrv-bridge` command line.

intrv-bridge pair https://api.example.com 123456 --name "My laptop"
intrv-bridge add-folder E:\\models          # a pendrive works; it is re-scanned on every start
intrv-bridge scan                          # list models found
intrv-bridge run                           # connect and serve (Ctrl+C to stop)
intrv-bridge install-llama-server          # download a verified llama.cpp build for this OS
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from intrv_bridge import __version__
from intrv_bridge.catalog import Endpoint, ModelEntry, detect_servers, scan_folders
from intrv_bridge.config import BridgeConfig, data_dir
from intrv_bridge.runtime import LlamaRuntime, download_llama_server, is_removable
from intrv_bridge.tunnel import Bridge, Revoked

log = logging.getLogger("intrv_bridge")


def _pair(args: argparse.Namespace) -> int:
    response = httpx.post(
        f"{args.server.rstrip('/')}/api/bridge/claim",
        json={"code": args.code, "name": args.name or platform.node(), "platform": platform.platform()[:60]},
        timeout=20,
    )
    if response.status_code != 201:
        print(f"Pairing failed: {response.json().get('detail', response.text)}", file=sys.stderr)
        return 1
    config = BridgeConfig.load()
    body = response.json()
    config.server_url, config.device_id, config.token = args.server.rstrip("/"), body["device_id"], body["token"]
    path = config.save()
    print(f"Paired as {body['device_id']}. Settings saved to {path}. Now run: intrv-bridge run")
    return 0


def _add_folder(args: argparse.Namespace) -> int:
    config = BridgeConfig.load()
    folder = str(Path(args.folder).expanduser().resolve())
    if folder not in config.model_folders:
        config.model_folders.append(folder)
        config.save()
    print(f"Model folders: {', '.join(config.model_folders)}")
    return 0


def _catalog(
    config: BridgeConfig, runtime: LlamaRuntime, http: httpx.AsyncClient
) -> Callable[[], Awaitable[list[ModelEntry]]]:
    async def models() -> list[ModelEntry]:
        def endpoint_for(path: Path, removable: bool) -> Endpoint:
            async def endpoint() -> tuple[str, str]:
                return await runtime.base_url(path, removable=removable), path.stem

            return endpoint

        found = scan_folders([Path(f) for f in config.model_folders], endpoint_for, is_removable)
        return found + await detect_servers(http)

    return models


async def _scan(config: BridgeConfig) -> int:
    runtime = LlamaRuntime(config.llama_server or None)
    async with httpx.AsyncClient() as http:
        models = await _catalog(config, runtime, http)()
    if not models:
        print("No models found. Add a folder with .gguf files (intrv-bridge add-folder) or start Ollama.")
    for model in models:
        size = f"{model.size_bytes / 1e9:.1f} GB" if model.size_bytes else ""
        usb = " (removable drive: loaded with --no-mmap)" if model.removable else ""
        print(f"- {model.name} [{model.source}] {model.quant or ''} {size}{usb}")
    return 0


async def _run(config: BridgeConfig) -> int:
    if not config.token:
        print("Not paired yet. In the web app open AI models → Use a model on my PC, then run intrv-bridge pair.")
        return 1
    runtime = LlamaRuntime(
        config.llama_server or None, idle_unload_s=config.idle_unload_s, gpu_layers=config.gpu_layers
    )
    async with httpx.AsyncClient() as http:
        bridge = Bridge(config.server_url, config.token, _catalog(config, runtime, http))
        print(f"intrv-bridge {__version__}: connecting to {config.server_url} (Ctrl+C to stop)")
        try:
            await bridge.run_forever()
        except Revoked:
            return 2
        finally:
            await bridge.close()
            await runtime.close()
    return 0


async def _install(config: BridgeConfig) -> int:
    async with httpx.AsyncClient(follow_redirects=True) as http:
        binary = await download_llama_server(data_dir() / "llama.cpp", http)
    config.llama_server = str(binary)
    config.save()
    print(f"Installed {binary}")
    return 0


def _tray(config: BridgeConfig) -> int:
    try:
        from intrv_bridge.tray import run_tray
    except ImportError:
        print("The tray UI needs: pip install 'intrv-bridge[tray]'", file=sys.stderr)
        return 1
    if not config.token:
        print("Not paired yet: run intrv-bridge pair first.", file=sys.stderr)
        return 1
    runtime = LlamaRuntime(
        config.llama_server or None, idle_unload_s=config.idle_unload_s, gpu_layers=config.gpu_layers
    )
    http = httpx.AsyncClient()
    run_tray(config.server_url, config.token, _catalog(config, runtime, http))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="intrv-bridge", description="Serve local models to Intrv-embed.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair", help="pair this PC with a 6-digit code from the web app")
    pair.add_argument("server", help="backend URL, e.g. https://api.example.com")
    pair.add_argument("code")
    pair.add_argument("--name", default="")
    folder = sub.add_parser("add-folder", help="add a folder (or pendrive) with .gguf files")
    folder.add_argument("folder")
    sub.add_parser("scan", help="list the models this PC can serve")
    sub.add_parser("run", help="connect and serve")
    sub.add_parser("install-llama-server", help="download a verified llama.cpp build")
    sub.add_parser("tray", help="run with a system-tray icon (needs the [tray] extra)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")
    config = BridgeConfig.load()
    match args.command:
        case "pair":
            return _pair(args)
        case "add-folder":
            return _add_folder(args)
        case "scan":
            return asyncio.run(_scan(config))
        case "install-llama-server":
            return asyncio.run(_install(config))
        case "tray":
            return _tray(config)
        case _:
            try:
                return asyncio.run(_run(config))
            except KeyboardInterrupt:
                return 0


if __name__ == "__main__":
    sys.exit(main())
