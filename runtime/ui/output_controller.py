from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum


class OutputPhase(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    RUNNING = "running"
    APPROVAL_WAITING = "approval_waiting"
    INTERACTIVE_INPUT = "interactive_input"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class OutputControllerState:
    phase: OutputPhase = OutputPhase.IDLE
    mode: str = ""
    route: str = ""
    active_task_id: str = ""
    dynamic_allowed: bool = False
    persistent_allowed: bool = True
    reason: str = ""

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "phase": self.phase.value,
            "mode": self.mode,
            "route": self.route,
            "active_task_id": self.active_task_id,
            "dynamic_allowed": self.dynamic_allowed,
            "persistent_allowed": self.persistent_allowed,
            "reason": self.reason,
        }


class OutputController:
    """Small output-state gate shared by plain and future dynamic renderers."""

    def __init__(self, *, mode: str = "", route: str = "") -> None:
        self._mode = str(mode or "")
        self._route = str(route or "")
        self._phase = OutputPhase.IDLE
        self._active_task_id = ""
        self._reason = ""
        self._phase_stack: list[OutputControllerState] = []

    def snapshot(self) -> OutputControllerState:
        dynamic_allowed, persistent_allowed = _permissions_for_phase(self._phase)
        return OutputControllerState(
            phase=self._phase,
            mode=self._mode,
            route=self._route,
            active_task_id=self._active_task_id,
            dynamic_allowed=dynamic_allowed,
            persistent_allowed=persistent_allowed,
            reason=self._reason,
        )

    def configure(self, *, mode: str | None = None, route: str | None = None) -> None:
        if mode is not None:
            self._mode = str(mode or "")
        if route is not None:
            self._route = str(route or "")

    def can_render_dynamic(self) -> bool:
        return self.snapshot().dynamic_allowed

    def can_print_persistent(self) -> bool:
        return self.snapshot().persistent_allowed

    def push_phase(self, phase: OutputPhase | str, *, reason: str = "", task_id: str | None = None) -> int:
        self._phase_stack.append(self.snapshot())
        self._set(_coerce_phase(phase), reason=reason, task_id=self._active_task_id if task_id is None else task_id)
        return len(self._phase_stack)

    def restore(self, token: int | None) -> None:
        if not self._phase_stack:
            return
        if token is not None and token < 1:
            return
        snapshot = self._phase_stack.pop()
        if self._phase in {OutputPhase.COMPLETED, OutputPhase.FAILED}:
            return
        self._phase = snapshot.phase
        self._mode = snapshot.mode
        self._route = snapshot.route
        self._active_task_id = snapshot.active_task_id
        self._reason = snapshot.reason

    @contextmanager
    def temporary_phase(self, phase: OutputPhase | str, *, reason: str = "", task_id: str | None = None):
        token = self.push_phase(phase, reason=reason, task_id=task_id)
        try:
            yield
        finally:
            self.restore(token)

    def enter_idle(self, reason: str = "") -> None:
        self._set(OutputPhase.IDLE, reason=reason, task_id="")

    def enter_planning(self, reason: str = "") -> None:
        self._set(OutputPhase.PLANNING, reason=reason, task_id="")

    def enter_running(self, *, task_id: str = "", reason: str = "") -> None:
        self._set(OutputPhase.RUNNING, reason=reason, task_id=task_id or self._active_task_id)

    def enter_approval_waiting(self, reason: str = "") -> None:
        self._set(OutputPhase.APPROVAL_WAITING, reason=reason, task_id=self._active_task_id)

    def enter_interactive_input(self, reason: str = "") -> None:
        self._set(OutputPhase.INTERACTIVE_INPUT, reason=reason, task_id=self._active_task_id)

    def enter_finalizing(self, reason: str = "") -> None:
        self._set(OutputPhase.FINALIZING, reason=reason, task_id="")

    def enter_completed(self, reason: str = "") -> None:
        self._set(OutputPhase.COMPLETED, reason=reason, task_id="")

    def enter_failed(self, reason: str = "") -> None:
        self._set(OutputPhase.FAILED, reason=reason, task_id=self._active_task_id)

    def _set(self, phase: OutputPhase, *, reason: str = "", task_id: str = "") -> None:
        self._phase = phase
        self._reason = str(reason or "")
        self._active_task_id = str(task_id or "")


def _permissions_for_phase(phase: OutputPhase) -> tuple[bool, bool]:
    if phase in {OutputPhase.PLANNING, OutputPhase.RUNNING, OutputPhase.FINALIZING}:
        return True, True
    if phase in {OutputPhase.APPROVAL_WAITING, OutputPhase.INTERACTIVE_INPUT}:
        return False, False
    return False, True


def _coerce_phase(phase: OutputPhase | str) -> OutputPhase:
    if isinstance(phase, OutputPhase):
        return phase
    return OutputPhase(str(phase or "idle"))
