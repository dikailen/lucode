from __future__ import annotations

from typing import Any

from runtime.events.model import ExecutionEvent


class ExecutionEventBus:
    """In-memory execution event recorder used by the runtime UI layer."""

    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []

    def emit(self, event_type: str, message: str = "", **kwargs: Any) -> ExecutionEvent:
        event = ExecutionEvent(event_type=str(event_type or ""), message=str(message or ""), **kwargs)
        self._events.append(event)
        return event

    def snapshot(self) -> list[ExecutionEvent]:
        return list(self._events)
