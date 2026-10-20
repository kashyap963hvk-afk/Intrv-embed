"""Optional system-tray UI (`pip install intrv-bridge[tray]`, then `intrv-bridge tray`).

Shows whether the tunnel is connected and offers Quit; the bridge itself runs on a background event loop.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from intrv_bridge.catalog import ModelEntry
from intrv_bridge.tunnel import Bridge


def _icon(color: str) -> Any:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((8, 8, 56, 56), fill=color)
    return image


def run_tray(server_url: str, token: str, models: Callable[[], Awaitable[list[ModelEntry]]]) -> None:
    import pystray

    loop = asyncio.new_event_loop()
    bridge = Bridge(server_url, token, models)
    worker = threading.Thread(target=lambda: loop.run_until_complete(bridge.run_forever()), daemon=True)

    def status(_item: Any) -> str:
        return "Connected" if bridge.connected.is_set() else "Connecting…"

    def quit_app(icon: Any, _item: Any) -> None:
        icon.stop()
        loop.call_soon_threadsafe(loop.stop)

    icon = pystray.Icon(
        "intrv-bridge",
        _icon("#16a34a"),
        "Intrv-embed bridge",
        menu=pystray.Menu(pystray.MenuItem(status, None, enabled=False), pystray.MenuItem("Quit", quit_app)),
    )
    worker.start()
    icon.run()
