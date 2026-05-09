from __future__ import annotations


EXECUTION_MODES = {"solo", "serial", "full"}
DEFAULT_EXECUTION_MODE = "solo"


def normalize_execution_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in EXECUTION_MODES:
        return mode
    return DEFAULT_EXECUTION_MODE


def should_use_solo_mode(user_input: str, execution_mode: str) -> bool:
    return normalize_execution_mode(execution_mode) == "solo"


def runtime_route_for_input(user_input: str, execution_mode: str) -> str:
    mode = normalize_execution_mode(execution_mode)
    if mode == "solo":
        return "solo"
    return "serial"


def execution_mode_label_zh(mode: str) -> str:
    labels = {
        "solo": "单模型工具 Agent",
        "serial": "多 Agent 串行",
        "full": "高级并行多 Agent",
    }
    return labels.get(normalize_execution_mode(mode), labels[DEFAULT_EXECUTION_MODE])
