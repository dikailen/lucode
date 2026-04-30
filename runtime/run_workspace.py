import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4


class RunWorkspace:
    """Temporary storage for multi-agent worker outputs."""

    def __init__(self, project_root: Path):
        self.root = (project_root / ".agent_runs" / self._new_run_id()).resolve()
        self.project_root = project_root.resolve()

    @staticmethod
    def _new_run_id() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]

    def create(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=False)
        return self.root

    def write_task_output(self, task_id: str, title: str, content: str) -> Path:
        safe_task_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in task_id)
        path = self.root / f"{safe_task_id}.md"
        path.write_text(
            f"# {title}\n\n"
            f"任务 ID：{task_id}\n\n"
            "## 输出\n\n"
            f"{content}\n",
            encoding="utf-8",
        )
        return path

    def cleanup(self) -> None:
        resolved = self.root.resolve()
        expected_parent = (self.project_root / ".agent_runs").resolve()
        if not resolved.is_relative_to(expected_parent):
            raise RuntimeError(f"Refusing to clean unexpected path: {resolved}")
        if resolved.exists():
            shutil.rmtree(resolved)
        if expected_parent.exists() and not any(expected_parent.iterdir()):
            expected_parent.rmdir()
