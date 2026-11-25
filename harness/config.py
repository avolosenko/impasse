"""Configuration loading: config.yaml deep-merged with config.local.yaml."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict: `override` merged into `base` recursively (lists replaced)."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    def __init__(self, data: dict[str, Any], root: Path = REPO_ROOT):
        self.data = data
        self.root = root

    @classmethod
    def load(cls, root: Path = REPO_ROOT, local: bool = True) -> "Config":
        data = yaml.safe_load((root / "config.yaml").read_text()) or {}
        local_path = root / "config.local.yaml"
        if local and local_path.exists():
            data = deep_merge(data, yaml.safe_load(local_path.read_text()) or {})
        return cls(data, root)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    # --- servers and roles -------------------------------------------------

    def server(self, name: str) -> dict:
        try:
            return self.data["servers"][name]
        except KeyError:
            raise KeyError(f"unknown server '{name}' (config.yaml servers:)") from None

    def server_url(self, name: str) -> str:
        s = self.server(name)
        return f"http://{s['host']}:{s['port']}"

    def endpoint_for_role(self, role: str) -> str:
        try:
            server_name = self.data["roles"][role]
        except KeyError:
            raise KeyError(f"no server bound to role '{role}' (config.yaml roles:)") from None
        return self.server_url(server_name)

    # --- models ------------------------------------------------------------

    def model(self, name: str) -> dict:
        try:
            return self.data["models"][name]
        except KeyError:
            known = ", ".join(self.data.get("models", {}))
            raise KeyError(f"unknown model '{name}'; known: {known}") from None

    def gguf_path(self, model_name: str) -> Path:
        key = self.model(model_name)["gguf"]
        paths = self.data.get("gguf_paths") or {}
        if key not in paths:
            raise KeyError(
                f"no GGUF path for '{key}': set gguf_paths.{key} in config.local.yaml"
            )
        return Path(paths[key]).expanduser()

    def llama_server_bin(self) -> Path:
        b = self.data.get("llama_server_bin")
        if not b:
            raise KeyError("llama_server_bin is not set in config.local.yaml")
        return Path(b).expanduser()
