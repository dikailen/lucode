from __future__ import annotations

from runtime.execution.pipeline import PipelineRunState
from runtime.ui.progress import render_runtime_statusline, render_task_status_board


def _print_progress_snapshot(run_state: PipelineRunState, mode: str, attempt: int, active: str = "") -> None:
    print(render_task_status_board(run_state, mode=mode, attempt=attempt))
    print(render_runtime_statusline(mode, active=active))
