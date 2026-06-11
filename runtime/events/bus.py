from __future__ import annotations

from collections.abc import Callable
from typing import Any

from runtime.events.model import ExecutionEvent


class ExecutionEventBus:
    """In-memory execution event recorder used by the runtime UI layer."""

    def __init__(self) -> None:
        self._events: list[ExecutionEvent] = []
        self._subscribers: list[Callable[[ExecutionEvent], None]] = []

    def subscribe(self, callback: Callable[[ExecutionEvent], None]):
        """Subscribe to newly emitted events and return an unsubscribe callback."""

        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def emit(self, event_type: str, message: str = "", **kwargs: Any) -> ExecutionEvent:
        event = ExecutionEvent(event_type=str(event_type or ""), message=str(message or ""), **kwargs)
        self._events.append(event)
        for callback in list(self._subscribers):
            try:
                callback(event)
            except Exception:
                pass
        return event

    def snapshot(self) -> list[ExecutionEvent]:
        return list(self._events)
