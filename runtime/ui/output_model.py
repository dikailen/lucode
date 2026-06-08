from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OutputItemKind(str, Enum):
    PROGRESS = "progress"
    TOOL = "tool"
    WORKER = "worker"
    LEAD_REVIEW = "lead_review"
    SUPERVISOR = "supervisor"
    INTERACTIVE_PANEL = "interactive_panel"
    TRANSIENT_HINT = "transient_hint"
    OPERATION_RESULT = "operation_result"
    DIAGNOSTIC = "diagnostic"


class OutputVisibility(str, Enum):
    PERSISTENT = "persistent"
    TRANSIENT = "transient"


@dataclass
class OutputItem:
    kind: OutputItemKind
    visibility: OutputVisibility
    summary: str
    title: str = ""
    body: str = ""
    source: str = ""
    event_type: str = ""
    task_id: str = ""
    status: str = ""
    agent: str = ""
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "visibility": self.visibility.value,
            "summary": self.summary,
            "title": self.title,
            "body": self.body,
            "source": self.source,
            "event_type": self.event_type,
            "task_id": self.task_id,
            "status": self.status,
            "agent": self.agent,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


@dataclass
class OutputViewModel:
    items: list[OutputItem] = field(default_factory=list)
    mode: str = ""
    route: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def persistent_items(self) -> list[OutputItem]:
        return [item for item in self.items if item.visibility == OutputVisibility.PERSISTENT]

    @property
    def transient_items(self) -> list[OutputItem]:
        return [item for item in self.items if item.visibility == OutputVisibility.TRANSIENT]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "route": self.route,
            "items": [item.to_dict() for item in self.items],
            "metadata": dict(self.metadata),
        }
