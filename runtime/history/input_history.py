from __future__ import annotations

import os
from pathlib import Path


def input_history_dir(env=None) -> Path:
    """Return the user-level Lucode input history directory."""

    env = os.environ if env is None else env
    raw_home = str(env.get("LUCODE_HOME") or "").strip()
    if raw_home:
        return Path(raw_home).expanduser() / "input"
    return Path.home() / ".lucode" / "input"


def main_input_history_path(env=None) -> Path:
    return input_history_dir(env) / "main_history.txt"


def ensure_main_input_history_path(env=None) -> Path:
    path = main_input_history_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
