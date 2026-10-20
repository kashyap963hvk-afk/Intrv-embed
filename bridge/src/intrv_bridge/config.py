"""Bridge settings, stored in the user's config folder (the device token is readable by this OS user only)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP = "intrv-bridge"


def config_path() -> Path:
    return Path(os.environ.get("INTRV_BRIDGE_CONFIG") or Path(user_config_dir(APP)) / "config.json")


def data_dir() -> Path:
    return Path(user_data_dir(APP))


@dataclass
class BridgeConfig:
    server_url: str = ""
    device_id: str = ""
    token: str = ""
    model_folders: list[str] = field(default_factory=list)
    llama_server: str = ""
    idle_unload_s: int = 900
    gpu_layers: str = "auto"

    @classmethod
    def load(cls) -> BridgeConfig:
        path = config_path()
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> Path:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
        return path
