from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass
class ExecutionContext:
    request: Any
    model_registry: Any
    mcp_manager: Any
    hooks: Any
    run_agent: Callable
    settings: Any


class ExecutionStrategy(Protocol):
    mode_name: str

    async def execute(self, context: ExecutionContext) -> str:
        ...
