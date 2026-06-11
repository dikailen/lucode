from __future__ import annotations

from typing import Any

try:
    from PySide6.QtCore import QObject, QTimer, Signal
except ModuleNotFoundError:
    QObject = object  # type: ignore[assignment]
    QTimer = None  # type: ignore[assignment]

    class Signal:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            del args, kwargs


class DeltaCoalescer:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self._template: dict[str, Any] = {}

    def push(self, event: dict[str, Any]) -> None:
        text = str(event.get("text") or event.get("message") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if not text:
            text = str(payload.get("text") or "")
        if not text:
            return
        if not self._parts:
            self._template = dict(event)
        self._parts.append(text)

    def flush(self) -> dict[str, Any] | None:
        if not self._parts:
            return None
        event = dict(self._template)
        text = "".join(self._parts)
        payload = dict(event.get("payload") if isinstance(event.get("payload"), dict) else {})
        payload["text"] = text
        event["event_type"] = "AgentMessageDelta"
        event["message"] = text
        event["text"] = text
        event["payload"] = payload
        self._parts = []
        self._template = {}
        return event

    def has_pending(self) -> bool:
        return bool(self._parts)


def coalesce_events_for_test(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coalescer = DeltaCoalescer()
    out: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("event_type") or "") == "AgentMessageDelta":
            coalescer.push(event)
            continue
        flushed = coalescer.flush()
        if flushed is not None:
            out.append(flushed)
        out.append(event)
    flushed = coalescer.flush()
    if flushed is not None:
        out.append(flushed)
    return out


class EventBridge(QObject):
    event_received = Signal(dict)

    def __init__(self, *, flush_interval_ms: int = 40, parent: QObject | None = None):
        if QTimer is None:
            raise RuntimeError('Lucode GUI dependencies are missing. Install with: pip install -e ".[gui]"')
        super().__init__(parent)
        self._coalescer = DeltaCoalescer()
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, int(flush_interval_ms or 40)))
        self._timer.timeout.connect(self.flush)

    def on_bus_event(self, event) -> None:
        try:
            event_dict = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            if str(event_dict.get("event_type") or "") == "AgentMessageDelta":
                self._coalescer.push(event_dict)
                if not self._timer.isActive():
                    self._timer.start()
                return
            self.flush()
            self.event_received.emit(event_dict)
        except Exception:
            return

    def flush(self) -> None:
        event = self._coalescer.flush()
        if event is not None:
            self.event_received.emit(event)
        if not self._coalescer.has_pending() and self._timer.isActive():
            self._timer.stop()
