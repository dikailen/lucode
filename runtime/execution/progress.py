from __future__ import annotations

import sys

from runtime.execution.pipeline import PipelineRunState
from runtime.ui.progress import render_runtime_statusline, render_task_status_board


def _print_progress_snapshot(run_state: PipelineRunState, mode: str, attempt: int, active: str = "") -> None:
    _safe_print(render_task_status_board(run_state, mode=mode, attempt=attempt, include_events=True))
    _safe_print(render_runtime_statusline(mode, active=active))


def _safe_print(value: str) -> None:
    text = str(value)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except LookupError:
        safe_text = text
    print(safe_text)
