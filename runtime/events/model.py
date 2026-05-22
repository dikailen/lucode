from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ExecutionEvent:
    """Small serializable event for execution observability."""

    event_type: str
    message: str = ""
    mode: str = ""
    agent: str = ""
    task_id: str = ""
    status: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.timestamp,
            "event_type": self.event_type,
            "message": self.message,
            "mode": self.mode,
            "agent": self.agent,
            "task_id": self.task_id,
            "status": self.status,
            "payload": dict(self.payload),
        }
