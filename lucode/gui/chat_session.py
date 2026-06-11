from __future__ import annotations

import asyncio
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from catalog_system.model_catalog import ModelRegistry, load_model_catalog
from lucode.shell.turn_display import format_turn_error
from runtime.common.conversation import append_recent_turn, compose_recent_context
from runtime.config.execution_mode import explicit_execution_mode_for_input, normalize_execution_mode
from runtime.config.model_config import normalize_model_role
from runtime.config.settings import RuntimeSettings
from runtime.safety.privacy import normalize_privacy_mode
from runtime.config.workspace import discover_workspace_context
from runtime.events import ExecutionEventBus
from runtime.history import HistoryStore
from runtime.kernel import KernelFacade
from runtime.kernel.session import create_token_logger_hooks
from runtime.safety.session_checkpoint import SessionCheckpointManager


APP_HOME = Path(__file__).resolve().parents[2]


@dataclass
class GuiTurnResult:
    final_output: str
    stopped: bool = False
    failed: bool = False
    execution_mode: str = ""
    mcp_ids_used: list[str] = field(default_factory=list)
    run_context_summary: str = ""


class RejectingApprovalSession:
    async def request_approval(self, prompt: str) -> str:
        del prompt
        return "n"


class GuiChatSession:
    def __init__(
        self,
        *,
        workspace: Path,
        mode: str = "",
        event_bridge=None,
        approval_session=None,
        model_registry=None,
        runtime_settings=None,
    ) -> None:
        self.workspace_context = discover_workspace_context(APP_HOME, cwd=Path(workspace).resolve())
        self.model_registry = model_registry or ModelRegistry()
        self.settings = copy(runtime_settings or RuntimeSettings.from_env())
        if mode:
            self.settings.execution_mode = normalize_execution_mode(mode)
        self.event_bridge = event_bridge
        self.approval_session = approval_session or RejectingApprovalSession()
        self.recent_turns: list[dict[str, str]] = []
        self.resumed_session_summary = ""
        self.session_store = HistoryStore(self.workspace_context.workspace_root)
        self.current_session_id: str | None = None
        self.checkpoints = SessionCheckpointManager(self.workspace_context.workspace_root)
        self.last_run_context_summary = ""

    def list_configured_models(self) -> list[tuple[str, str]]:
        try:
            catalog = load_model_catalog()
        except Exception:
            return []
        models = []
        for item in catalog.get("models", []):
            if not item.get("configured"):
                continue
            model_id = str(item.get("id") or "")
            if not model_id:
                continue
            label = str(item.get("display_name_zh") or model_id)
            models.append((model_id, label))
        return models

    def set_execution_mode(self, mode: str) -> str:
        normalized = normalize_execution_mode(mode)
        self.settings.execution_mode = normalized
        return normalized

    def set_privacy_mode(self, mode: str) -> str:
        normalized = normalize_privacy_mode(mode)
        self.settings.privacy_mode = normalized
        return normalized

    def set_query_refiner_enabled(self, enabled: bool) -> bool:
        value = bool(enabled)
        self.settings.query_refiner_enabled = value
        return value

    def set_model_for_role(self, role: str, model_id: str) -> list[str]:
        canonical = normalize_model_role(role)
        chosen = str(model_id or "").strip()
        priority = self.settings.model_priority_for(canonical)
        if chosen:
            priority = [chosen] + [item for item in priority if item != chosen]
        _MODEL_PRIORITY_FIELDS = {
            "query_refiner": "query_refiner_model_priority",
            "orchestrator": "orchestrator_model_priority",
            "executor": "executor_model_priority",
            "final_synthesizer": "final_synthesizer_model_priority",
        }
        setattr(self.settings, _MODEL_PRIORITY_FIELDS[canonical], priority)
        return priority

    def set_allowed_worker_models(self, model_ids: list[str]) -> list[str]:
        configured = {model_id for model_id, _label in self.list_configured_models()}
        seen: set[str] = set()
        pool: list[str] = []
        for raw in model_ids or []:
            model_id = str(raw or "").strip()
            if not model_id or model_id in seen:
                continue
            if configured and model_id not in configured:
                continue
            seen.add(model_id)
            pool.append(model_id)
        self.settings.allowed_worker_models = pool
        return pool

    async def run_turn(self, user_input: str) -> GuiTurnResult:
        clean_input = str(user_input or "").strip()
        if not clean_input:
            return GuiTurnResult(final_output="", execution_mode=self.settings.execution_mode)

        hooks = create_token_logger_hooks()
        turn_settings = _settings_for_turn(self.settings, clean_input)
        run_input = compose_recent_context(
            self.recent_turns,
            clean_input,
            session_summary=self.resumed_session_summary,
        )
        bus = ExecutionEventBus()
        unsubscribe = None
        if self.event_bridge is not None and hasattr(self.event_bridge, "on_bus_event"):
            unsubscribe = bus.subscribe(self.event_bridge.on_bus_event)

        self.checkpoints.begin_turn()
        response = None
        stopped = False
        failed = False
        final_output = ""
        mcp_ids_used: list[str] = []
        run_context_summary = ""
        try:
            response = await KernelFacade(self.workspace_context).run_once(
                run_input,
                show_plan=True,
                approval_session=self.approval_session,
                settings=turn_settings,
                model_registry=self.model_registry,
                hooks=hooks,
                routing_input=clean_input,
                event_bus=bus,
            )
            final_output = str(response.final_output or "")
            stopped = bool(response.stopped)
            mcp_ids_used = list(response.mcp_ids_used or [])
            run_context_summary = str(response.run_context_summary or "")
        except asyncio.CancelledError:
            stopped = True
            final_output = "(Stopped)"
        except Exception as exc:
            stopped = False
            final_output = _format_gui_turn_exception(exc)
            failed = True
        finally:
            if self.event_bridge is not None and hasattr(self.event_bridge, "flush"):
                self.event_bridge.flush()
            if unsubscribe is not None:
                unsubscribe()
            self.checkpoints.complete_turn()

        self.last_run_context_summary = run_context_summary
        self._remember_and_record(
            clean_input,
            final_output,
            execution_mode=turn_settings.execution_mode,
            stopped=stopped,
            started_mcp_ids=mcp_ids_used,
            run_context_summary=run_context_summary,
        )
        return GuiTurnResult(
            final_output=final_output,
            stopped=stopped,
            failed=failed,
            execution_mode=turn_settings.execution_mode,
            mcp_ids_used=mcp_ids_used,
            run_context_summary=run_context_summary,
        )

    def _remember_and_record(
        self,
        user_input: str,
        final_output: str,
        *,
        execution_mode: str,
        stopped: bool,
        started_mcp_ids: list[str],
        run_context_summary: str,
    ) -> None:
        append_recent_turn(self.recent_turns, "user", user_input)
        append_recent_turn(self.recent_turns, "assistant", str(final_output), max_chars=800)
        self.recent_turns = self.recent_turns[-6:]
        try:
            if not self.current_session_id:
                self.current_session_id = self.session_store.start_session(user_input)
            _record_session_turn(
                self.session_store,
                self.current_session_id,
                user_input,
                str(final_output),
                execution_mode=execution_mode,
                stopped=stopped,
                started_mcp_ids=started_mcp_ids,
                run_context_summary=run_context_summary,
            )
        except Exception:
            return


def _settings_for_turn(runtime_settings, user_input: str):
    explicit_mode = explicit_execution_mode_for_input(user_input)
    if not explicit_mode or explicit_mode == getattr(runtime_settings, "execution_mode", ""):
        return runtime_settings
    turn_settings = copy(runtime_settings)
    turn_settings.execution_mode = explicit_mode
    return turn_settings


def _record_session_turn(
    session_store: HistoryStore,
    session_id: str,
    user_input: str,
    final_output: str,
    *,
    execution_mode: str,
    stopped: bool,
    started_mcp_ids: list[str],
    run_context_summary: str = "",
) -> None:
    assistant_metadata: dict[str, Any] = {
        "execution_mode": execution_mode,
        "stopped": bool(stopped),
        "mcp_ids": list(started_mcp_ids or []),
    }
    if str(run_context_summary or "").strip():
        assistant_metadata["run_context_summary"] = str(run_context_summary)
    session_store.append_message(
        session_id,
        "user",
        user_input,
        metadata={"execution_mode": execution_mode},
    )
    session_store.append_message(
        session_id,
        "assistant",
        final_output,
        metadata=assistant_metadata,
    )


def _format_gui_turn_exception(exc: Exception) -> str:
    if exc.__class__.__name__ == "MaxTurnsExceeded":
        return (
            "This turn exceeded the maximum model/tool turn count and was stopped. "
            "Try narrowing the task or switching to serial/solo."
        )
    return format_turn_error(exc)
