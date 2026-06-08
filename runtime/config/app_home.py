from __future__ import annotations

import os
import sys
from pathlib import Path


def get_app_home() -> Path:
    """Return the installed Lucode resource root for source and frozen builds."""

    env_value = os.environ.get("LUCODE_APP_HOME")
    if env_value:
        return Path(env_value).expanduser().resolve()
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        internal_dir = executable_dir / "_internal"
        if (internal_dir / "catalogs").is_dir() and (internal_dir / "skills").is_dir():
            return internal_dir
        return executable_dir
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    return get_app_home().joinpath(*parts)
