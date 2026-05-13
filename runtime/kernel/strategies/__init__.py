from __future__ import annotations

from runtime.config.execution_mode import normalize_execution_mode, runtime_route_for_input
from runtime.kernel.strategies.base import ExecutionContext, ExecutionStrategy


def create_execution_strategy(*, routing_input: str, execution_mode: str) -> ExecutionStrategy:
    route = runtime_route_for_input(routing_input, execution_mode)
    mode = "solo" if route == "solo" else normalize_execution_mode(execution_mode)

    if mode == "full":
        from runtime.kernel.strategies.full import FullStrategy

        return FullStrategy()
    if mode == "serial":
        from runtime.kernel.strategies.serial import SerialStrategy

        return SerialStrategy()

    from runtime.kernel.strategies.solo import SoloStrategy

    return SoloStrategy()


__all__ = ["ExecutionContext", "ExecutionStrategy", "create_execution_strategy"]
