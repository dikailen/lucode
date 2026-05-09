from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp_servers.core.operation_log import _redact_text
from runtime.execution.pipeline import PipelineRunState


class FlywheelStore:
    """Small local experience store for project lessons and pipeline summaries."""

    def __init__(self, project_root: Path, cache_dir: Path | None = None):
        self.project_root = project_root.resolve()
        self.cache_dir = (cache_dir or self.project_root / ".agent_cache").resolve()
        self.path = self.cache_dir / "flywheel_memory.jsonl"

    def append_entry(
        self,
        *,
        kind: str,
        summary: str,
        tags: list[str] | None = None,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id": self._new_id(),
            "time": datetime.now().isoformat(timespec="seconds"),
            "project_root": str(self.project_root),
            "kind": kind,
            "source": source,
            "summary": _redact_text(summary),
            "tags": sorted(set(tags or [])),
            "metadata": self._redact_metadata(metadata or {}),
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def record_pipeline_state(self, state: PipelineRunState) -> dict[str, Any]:
        completed = sum(1 for task in state.tasks if task.status == "completed")
        failed = sum(1 for task in state.tasks if task.status == "failed")
        skills = sorted({task.skill_id for task in state.tasks if task.skill_id})
        mcp_ids = sorted({mcp_id for task in state.tasks for mcp_id in task.mcp})
        summary = (
            f"Pipeline {state.route_type}: {completed} completed, {failed} failed. "
            f"Reason: {state.reason}. Skills: {', '.join(skills) or 'none'}."
        )
        return self.append_entry(
            kind="pipeline_summary",
            source="pipeline",
            summary=summary,
            tags=[state.route_type, *skills, *mcp_ids],
            metadata={
                "route_type": state.route_type,
                "task_count": len(state.tasks),
                "completed": completed,
                "failed": failed,
            },
        )

    def record_failure_case(
        self,
        *,
        user_request: str,
        attempt_count: int,
        models_used: list[str],
        files_touched: list[str],
        failure_reasons: list[str],
        rollback_status: str,
        lesson: str,
    ) -> dict[str, Any]:
        summary = (
            f"Failure after {attempt_count} attempts. "
            f"Rollback: {rollback_status}. Lesson: {lesson}"
        )
        return self.append_entry(
            kind="failure_case",
            source="repair_loop",
            summary=summary,
            tags=["failure", "repair_loop", rollback_status],
            metadata={
                "user_request": user_request,
                "attempt_count": attempt_count,
                "models_used": models_used,
                "files_touched": files_touched,
                "failure_reasons": failure_reasons,
                "rollback_status": rollback_status,
                "lesson": lesson,
            },
        )

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        terms = [item for item in query.lower().split() if item]
        entries = self.load_entries()
        scored = []
        for entry in entries:
            haystack = " ".join(
                [
                    str(entry.get("kind", "")),
                    str(entry.get("summary", "")),
                    " ".join(entry.get("tags", [])),
                ]
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda item: (item[0], item[1].get("time", "")), reverse=True)
        return [entry for _, entry in scored[: max(1, int(limit or 5))]]

    def load_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    @staticmethod
    def _new_id() -> str:
        return datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid4().hex[:8]

    @staticmethod
    def _redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        redacted = {}
        for key, value in metadata.items():
            if isinstance(value, str):
                redacted[key] = _redact_text(value)
            elif isinstance(value, dict):
                redacted[key] = FlywheelStore._redact_metadata(value)
            elif isinstance(value, list):
                redacted[key] = [_redact_text(item) if isinstance(item, str) else item for item in value]
            else:
                redacted[key] = value
        return redacted
