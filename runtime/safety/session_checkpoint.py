from __future__ import annotations

import subprocess
from pathlib import Path

from runtime.safety.checkpoint import Checkpoint, RollbackResult, create_checkpoint, rollback_checkpoint


class SessionCheckpointManager:
    """Keep a single user-facing rollback point for the latest executed turn."""

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self._active_checkpoint: Checkpoint | None = None
        self._active_dirty_paths: list[str] = []
        self._last_checkpoint: Checkpoint | None = None
        self._last_changed_paths: list[str] = []

    def begin_turn(self) -> Checkpoint:
        checkpoint = create_checkpoint(self.project_root)
        self._active_checkpoint = checkpoint
        self._active_dirty_paths = list(checkpoint.dirty_paths)
        return checkpoint

    def complete_turn(self) -> None:
        if self._active_checkpoint is None:
            return

        changed_paths = _git_status_paths(self.project_root)
        baseline_dirty = set(self._active_dirty_paths)
        scoped_paths = [path for path in changed_paths if path not in baseline_dirty]
        checkpoint = self._active_checkpoint

        if scoped_paths and checkpoint.git_available and checkpoint.head_commit:
            self._last_checkpoint = Checkpoint(
                project_root=checkpoint.project_root,
                git_available=True,
                is_dirty=checkpoint.is_dirty,
                head_commit=checkpoint.head_commit,
                can_rollback=True,
                mode="scoped_patch_rollback",
                message="已记录最近一轮可回滚 checkpoint。",
                scoped_paths=scoped_paths,
                dirty_paths=list(self._active_dirty_paths),
            )
            self._last_changed_paths = scoped_paths
        else:
            self._last_checkpoint = None
            self._last_changed_paths = []

        self._active_checkpoint = None
        self._active_dirty_paths = []

    def rollback_last_turn(self) -> RollbackResult:
        if self._last_checkpoint is None:
            return RollbackResult(False, "当前没有可回滚的最近一轮改动。")

        result = rollback_checkpoint(self._last_checkpoint)
        if result.rolled_back:
            self._last_checkpoint = None
            self._last_changed_paths = []
        return result

    def render_status(self) -> str:
        if self._last_checkpoint is None:
            return "最近一轮回滚点：没有可回滚的改动"
        paths = ", ".join(self._last_changed_paths[:8])
        if len(self._last_changed_paths) > 8:
            paths += f"，其余 {len(self._last_changed_paths) - 8} 个已省略"
        return f"最近一轮回滚点：可回滚；涉及 {paths or '未记录路径'}"


def _git_status_paths(project_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=project_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=20,
            shell=False,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        raw = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1].strip()
        raw = raw.strip('"').replace("\\", "/")
        if raw and raw not in paths:
            paths.append(raw)
    return paths
