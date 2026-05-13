from __future__ import annotations

import os


def runtime_verbose_enabled() -> bool:
    raw = str(os.environ.get("LUCODE_VERBOSE_RUNTIME") or os.environ.get("AGENTS_VERBOSE_RUNTIME") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "debug", "verbose"}


def runtime_logo_enabled() -> bool:
    raw = str(os.environ.get("LUCODE_NO_LOGO") or os.environ.get("AGENTS_NO_LOGO") or "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def turn_timeout_seconds() -> float | None:
    raw = str(os.environ.get("AGENTS_TURN_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None
