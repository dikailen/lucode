from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from catalog_system.model_catalog import ModelRegistry
from mcp_servers import MCPServerManager
from runtime.agent.approval import run_with_approval
from runtime.config.settings import RuntimeSettings
from runtime.events import ExecutionEventBus
from runtime.kernel.session import create_token_logger_hooks
from runtime.kernel.strategies import ExecutionContext, create_execution_strategy
from runtime.ui.output_controller import OutputController
from runtime.ui.output_visibility import should_suppress_final_output


@dataclass
class KernelRequest:
    user_input: str
    workspace_root: Path
    execution_mode: str = ""
    show_plan: bool = True
    routing_input: str = ""


@dataclass
class KernelResponse:
    final_output: str
    stopped: bool = False
    turn_status: str = "完成"
    mcp_ids_used: list[str] = field(default_factory=list)
    run_context_summary: str = ""
    output_already_printed: bool = False
    _summary_printer: Callable[[], None] | None = field(default=None, repr=False)

    def print_summary(self) -> None:
        if self._summary_printer is not None:
            self._summary_printer()


class KernelFacade:
    """Thin boundary between CLI shell commands and the runtime kernel."""

    def __init__(self, workspace_context):
        self.workspace_context = workspace_context

    async def run_once(
        self,
        prompt: str,
        *,
        show_plan: bool = True,
        approval_session=None,
        settings=None,
        model_registry=None,
        hooks=None,
        routing_input: str | None = None,
        verbose_runtime: bool = False,
        output_controller=None,
        event_bus=None,
    ) -> KernelResponse:
        user_input = str(prompt or "").strip()
        if not user_input:
            return KernelResponse(final_output="", turn_status="空输入")

        settings = settings or RuntimeSettings.from_env()
        request = KernelRequest(
            user_input=user_input,
            workspace_root=self.workspace_context.workspace_root,
            execution_mode=settings.execution_mode,
            show_plan=show_plan,
            routing_input=str(routing_input or user_input).strip(),
        )
        hooks = hooks or create_token_logger_hooks()
        model_registry = model_registry or ModelRegistry()
        output_controller = output_controller or OutputController(mode=settings.execution_mode)
        event_bus = event_bus or ExecutionEventBus()
        if hasattr(output_controller, "configure"):
            output_controller.configure(mode=settings.execution_mode)
        quarantine_dir = request.workspace_root / ".agent_quarantine"
        stopped = False
        turn_status = "completed"
        output = ""
        started_mcp_ids: list[str] = []
        run_context_summary = ""

        _emit_kernel_event(
            event_bus,
            "TurnStarted",
            "turn started",
            mode=settings.execution_mode,
            agent="kernel",
            status="running",
            payload={
                "mode": settings.execution_mode,
                "workspace_root": str(request.workspace_root),
            },
        )

        try:
            async with MCPServerManager(request.workspace_root, quarantine_dir, verbose=verbose_runtime) as mcp_manager:
                def run_agent(
                    agent,
                    turn_input,
                    turn_hooks,
                    max_turns=20,
                    approval_policy=None,
                    stream_output=None,
                    on_delta=None,
                ):
                    return run_with_approval(
                        agent,
                        turn_input,
                        turn_hooks,
                        session=approval_session,
                        max_turns=max_turns,
                        approval_policy=approval_policy,
                        stream_output=stream_output,
                        on_delta=on_delta or _agent_delta_emitter(event_bus, agent=getattr(agent, "name", "") or ""),
                    )

                strategy = create_execution_strategy(
                    routing_input=request.routing_input or request.user_input,
                    execution_mode=settings.execution_mode,
                )
                context = ExecutionContext(
                    request=request,
                    model_registry=model_registry,
                    mcp_manager=mcp_manager,
                    hooks=hooks,
                    run_agent=run_agent,
                    settings=settings,
                    output_controller=output_controller,
                    event_bus=event_bus,
                )
                timeout_seconds = _turn_timeout_seconds()
                try:
                    output = await _execute_with_turn_guard(strategy, context, timeout_seconds=timeout_seconds)
                except asyncio.TimeoutError:
                    if hasattr(output_controller, "enter_failed"):
                        output_controller.enter_failed("turn timeout")
                    output = _format_turn_timeout_message(timeout_seconds)
                    stopped = True
                    turn_status = "timeout"
                started_mcp_ids = list(mcp_manager.started_ids)
                run_context_summary = str(getattr(output, "run_context_summary", "") or "")
        except Exception:
            _emit_kernel_event(
                event_bus,
                "TurnEnded",
                "turn failed",
                mode=settings.execution_mode,
                agent="kernel",
                status="failed",
                payload={"status": "failed", "stopped": stopped},
            )
            raise

        _emit_kernel_event(
            event_bus,
            "TurnEnded",
            "turn ended",
            mode=settings.execution_mode,
            agent="kernel",
            status="stopped" if stopped else "completed",
            payload={"status": turn_status, "stopped": stopped},
        )

        return KernelResponse(
            final_output=str(output or ""),
            stopped=stopped,
            turn_status="超时" if stopped else "完成",
            mcp_ids_used=started_mcp_ids,
            run_context_summary=run_context_summary,
            output_already_printed=should_suppress_final_output(hooks, str(output or "")),
            _summary_printer=hooks.print_summary,
        )


async def _execute_with_turn_guard(strategy, context: ExecutionContext, *, timeout_seconds: float):
    task = strategy.execute(context)
    if timeout_seconds <= 0:
        return await task
    return await asyncio.wait_for(task, timeout=timeout_seconds)


def _turn_timeout_seconds() -> float:
    raw = str(os.environ.get("AGENTS_TURN_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _agent_delta_emitter(event_bus, *, agent: str = ""):
    def _emit_delta(text: str) -> None:
        if not text:
            return
        _emit_kernel_event(
            event_bus,
            "AgentMessageDelta",
            str(text),
            agent=str(agent or ""),
            status="streaming",
            payload={"text": str(text)},
        )

    return _emit_delta


def _emit_kernel_event(event_bus, event_type: str, message: str = "", **kwargs) -> None:
    if event_bus is None or not hasattr(event_bus, "emit"):
        return
    try:
        event_bus.emit(event_type, message, **kwargs)
    except Exception:
        return


def _format_turn_timeout_message(timeout_seconds: float) -> str:
    return (
        f"本轮任务已超过整轮超时限制（AGENTS_TURN_TIMEOUT_SECONDS={timeout_seconds:g}s），"
        "系统已停止等待并进入恢复路径。\n"
        "你可以缩小任务范围、切换到 serial/solo，或调大 AGENTS_TURN_TIMEOUT_SECONDS 后重试。"
    )
