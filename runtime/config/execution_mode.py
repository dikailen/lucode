from __future__ import annotations

import re


EXECUTION_MODES = {"solo", "serial", "full"}
DEFAULT_EXECUTION_MODE = "solo"


def normalize_execution_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in EXECUTION_MODES:
        return mode
    return DEFAULT_EXECUTION_MODE


def explicit_execution_mode_for_input(user_input: str) -> str:
    """Return a per-turn mode only when the user explicitly asks for one."""

    text = str(user_input or "").strip().lower()
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text)
    mode_word = "\u6a21\u5f0f"
    use_words = "\u7528|\u4f7f\u7528|\u5207\u5230|\u5207\u6362\u5230|\u4ee5|\u6309"
    for mode in ("full", "serial", "solo"):
        if re.search(rf"(^|\s|/){mode}\s*({mode_word}|mode)(\b|$)", normalized):
            return mode
        if re.search(rf"({use_words}|run with|use)\s*{mode}\b", normalized):
            return mode
        if re.search(rf"\b{mode}\s+(mode|{mode_word})\b", normalized):
            return mode
    return ""


def should_use_solo_mode(user_input: str, execution_mode: str) -> bool:
    return normalize_execution_mode(execution_mode) == "solo"


def runtime_route_for_input(user_input: str, execution_mode: str) -> str:
    mode = normalize_execution_mode(execution_mode)
    if mode == "solo":
        return "solo"
    return "serial"


def execution_mode_label_zh(mode: str) -> str:
    labels = {
        "solo": "\u5355\u6a21\u578b\u5de5\u5177 Agent",
        "serial": "\u591a Agent \u4e32\u884c",
        "full": "\u9ad8\u7ea7\u5e76\u884c\u591a Agent",
    }
    return labels.get(normalize_execution_mode(mode), labels[DEFAULT_EXECUTION_MODE])
