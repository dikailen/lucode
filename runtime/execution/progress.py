from __future__ import annotations

import sys

from runtime.execution.pipeline import PipelineRunState
from runtime.ui.output_controller import OutputPhase
from runtime.ui.capabilities import detect_dynamic_ui_capability, normalize_dynamic_ui_mode
from runtime.ui.progress import render_runtime_statusline, render_task_status_board


def _print_progress_snapshot(run_state: PipelineRunState, mode: str, attempt: int, active: str = "") -> None:
    controller = getattr(run_state, "output_controller", None)
    rich_configured = _should_use_rich_live()
    dynamic_allowed = True
    if controller is not None and hasattr(controller, "can_render_dynamic"):
        dynamic_allowed = bool(controller.can_render_dynamic())
    use_rich_live = dynamic_allowed and rich_configured
    if _is_terminal_output_phase(controller):
        _stop_rich_live(run_state)
        if rich_configured:
            return
    if controller is not None and hasattr(controller, "can_print_persistent") and not controller.can_print_persistent():
        _pause_rich_live(run_state, reason="persistent output blocked")
        return
    if not dynamic_allowed:
        _pause_rich_live(run_state, reason="dynamic output blocked")
    elif use_rich_live:
        if _refresh_rich_live(run_state, mode=mode, attempt=attempt, active=active):
            return
        rendered = _render_plain_progress_snapshot(run_state, mode=mode, attempt=attempt, active=active)
        if rendered:
            _safe_print(rendered)
        return

    rendered = _render_progress_snapshot(run_state, mode=mode, attempt=attempt, active=active)
    if not rendered:
        return
    _safe_print(rendered)


def _render_progress_snapshot(run_state: PipelineRunState, mode: str, attempt: int, active: str = "") -> str:
    controller = getattr(run_state, "output_controller", None)
    if controller is not None and hasattr(controller, "can_print_persistent") and not controller.can_print_persistent():
        return ""
    dynamic_allowed = True
    if controller is not None and hasattr(controller, "can_render_dynamic"):
        dynamic_allowed = bool(controller.can_render_dynamic())
    if dynamic_allowed and _should_use_rich_live():
        try:
            from runtime.ui.rich_live import render_rich_live_snapshot

            return render_rich_live_snapshot(run_state, mode=mode, attempt=attempt, active=active)
        except Exception:
            pass
    return "\n".join(
        _plain_progress_lines(run_state, mode=mode, attempt=attempt, active=active)
    )


def _render_plain_progress_snapshot(run_state: PipelineRunState, mode: str, attempt: int, active: str = "") -> str:
    return "\n".join(
        _plain_progress_lines(run_state, mode=mode, attempt=attempt, active=active)
    )


def _plain_progress_lines(run_state: PipelineRunState, mode: str, attempt: int, active: str = "") -> list[str]:
    return [
        render_task_status_board(run_state, mode=mode, attempt=attempt, include_events=True),
        render_runtime_statusline(mode, active=active),
    ]


def _should_use_rich_live() -> bool:
    dynamic_mode = normalize_dynamic_ui_mode()
    if dynamic_mode == "off":
        return False
    if dynamic_mode == "on":
        return True
    capability = detect_dynamic_ui_capability()
    return bool(capability.enabled)


def _rich_runtime_for(run_state: PipelineRunState):
    runtime = getattr(run_state, "_rich_live_runtime", None)
    if runtime is None:
        from runtime.ui.rich_live_runtime import RichLiveRuntime

        runtime = RichLiveRuntime(enabled=True)
        setattr(run_state, "_rich_live_runtime", runtime)
    return runtime


def _refresh_rich_live(run_state: PipelineRunState, *, mode: str, attempt: int, active: str = "") -> bool:
    try:
        return bool(_rich_runtime_for(run_state).refresh(run_state, mode=mode, attempt=attempt, active=active))
    except Exception:
        return False


def _pause_rich_live(run_state: PipelineRunState, *, reason: str = "") -> None:
    runtime = getattr(run_state, "_rich_live_runtime", None)
    if runtime is not None and hasattr(runtime, "pause"):
        runtime.pause(reason)


def _stop_rich_live(run_state: PipelineRunState) -> None:
    runtime = getattr(run_state, "_rich_live_runtime", None)
    if runtime is not None and hasattr(runtime, "stop"):
        runtime.stop()


def _is_terminal_output_phase(controller) -> bool:
    if controller is None or not hasattr(controller, "snapshot"):
        return False
    try:
        phase = controller.snapshot().phase
    except Exception:
        return False
    return phase in {OutputPhase.COMPLETED, OutputPhase.FAILED}


def _safe_print(value: str) -> None:
    text = str(value)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except LookupError:
        safe_text = text
    print(safe_text)
