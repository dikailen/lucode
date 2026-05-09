from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from planning.planner_schema import PlannedTask


class PatchProposalLedger:
    """Append-only ledger for planned file edits and task outcomes."""

    def __init__(self, project_root: Path, ledger_dir: Path | None = None):
        self.project_root = project_root.resolve()
        self.ledger_dir = (ledger_dir or self.project_root / ".agent_quarantine").resolve()
        self.ledger_path = self.ledger_dir / "patch_proposals.jsonl"

    def record_proposal(self, task: PlannedTask, note: str = "") -> dict[str, Any]:
        entry = {
            "timestamp": _now(),
            "kind": "patch_proposal",
            "task_id": task.id,
            "title": task.title,
            "skill_id": task.skill_id,
            "model": task.model,
            "mcp": list(task.mcp),
            "read_set": list(task.read_set),
            "write_intent": list(task.write_intent),
            "expected_sha256": self._expected_hashes(task.write_intent),
            "status": "proposed",
            "note": _preview(note),
        }
        self._append(entry)
        return entry

    def record_task_status(self, task_id: str, status: str, output: str = "") -> dict[str, Any]:
        entry = {
            "timestamp": _now(),
            "kind": "task_status",
            "task_id": task_id,
            "status": status,
            "output_preview": _preview(output),
        }
        self._append(entry)
        return entry

    def _expected_hashes(self, paths: list[str]) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for raw_path in paths:
            resolved = self._resolve_intent(raw_path)
            if resolved and resolved.is_file():
                hashes[_relative(self.project_root, resolved)] = _sha256_file(resolved)
        return hashes

    def _resolve_intent(self, raw_path: str) -> Path | None:
        if not raw_path or not str(raw_path).strip():
            return None
        candidate = Path(str(raw_path).strip())
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if not resolved.is_relative_to(self.project_root):
            return None
        return resolved

    def _append(self, entry: dict[str, Any]) -> None:
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _relative(project_root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(project_root)).replace("\\", "/")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preview(value: str, limit: int = 800) -> str:
    value = str(value or "")
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated {len(value) - limit} chars]"
