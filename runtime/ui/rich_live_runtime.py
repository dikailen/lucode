from __future__ import annotations

from typing import Any, Callable

from runtime.ui.rich_live import build_rich_live_renderable
from runtime.ui.terminal_owner import reset_dynamic_owner, set_dynamic_owner


LiveFactory = Callable[..., Any]
ConsoleFactory = Callable[..., Any]


class RichLiveRuntime:
    """Owns a transient Rich Live panel for one pipeline run."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        live_factory: LiveFactory | None = None,
        console_factory: ConsoleFactory | None = None,
        refresh_per_second: int = 8,
    ) -> None:
        self.enabled = bool(enabled)
        self._live_factory = live_factory
        self._console_factory = console_factory
        self._refresh_per_second = int(refresh_per_second or 8)
        self._live = None
        self._owner_token = None
        self._frame_index = 0
        self._paused = False

    @property
    def active(self) -> bool:
        return self._live is not None

    def start(self, run_state, *, mode: str, attempt: int, active: str = "") -> bool:
        if not self.enabled:
            return False
        if self._live is not None:
            return self.refresh(run_state, mode=mode, attempt=attempt, active=active)
        renderable = build_rich_live_renderable(
            run_state,
            mode=mode,
            attempt=attempt,
            active=active,
            frame_index=self._frame_index,
        )
        try:
            factory = self._resolve_live_factory()
            live = factory(
                renderable,
                refresh_per_second=self._refresh_per_second,
                transient=True,
                console=self._create_console(),
            )
            live.start(refresh=True)
        except Exception:
            self.enabled = False
            self._live = None
            self._clear_terminal_owner()
            return False
        self._live = live
        self._claim_terminal_owner()
        self._frame_index += 1
        self._paused = False
        return True

    def refresh(self, run_state, *, mode: str, attempt: int, active: str = "") -> bool:
        if not self.enabled:
            return False
        if self._live is None:
            return self.start(run_state, mode=mode, attempt=attempt, active=active)
        renderable = build_rich_live_renderable(
            run_state,
            mode=mode,
            attempt=attempt,
            active=active,
            frame_index=self._frame_index,
        )
        try:
            self._live.update(renderable)
        except Exception:
            self.stop()
            self.enabled = False
            return False
        self._frame_index += 1
        self._paused = False
        return True

    def pause(self, reason: str = "") -> None:
        del reason
        if self._live is None:
            self._paused = True
            self._frame_index = 0
            return
        self.stop()
        self._paused = True

    def resume(self, run_state, *, mode: str, attempt: int, active: str = "") -> bool:
        if not self.enabled:
            return False
        return self.start(run_state, mode=mode, attempt=attempt, active=active)

    def stop(self, final_behavior: str = "clear") -> None:
        del final_behavior
        live = self._live
        self._live = None
        self._clear_terminal_owner()
        self._frame_index = 0
        if live is None:
            return
        try:
            live.stop()
        except Exception:
            return

    def _claim_terminal_owner(self) -> None:
        if self._owner_token is None:
            self._owner_token = set_dynamic_owner("rich_live")

    def _clear_terminal_owner(self) -> None:
        token = self._owner_token
        self._owner_token = None
        if token is None:
            return
        try:
            reset_dynamic_owner(token)
        except Exception:
            return

    def _resolve_live_factory(self) -> LiveFactory:
        if self._live_factory is not None:
            return self._live_factory
        from rich.live import Live

        return Live

    def _create_console(self):
        factory = self._console_factory
        if factory is None:
            from rich.console import Console

            factory = Console
        return factory(force_terminal=True, color_system="auto")
