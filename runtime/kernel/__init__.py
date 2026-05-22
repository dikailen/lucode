from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


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
    ) -> KernelResponse:
        from catalog_system.model_catalog import ModelRegistry
        from mcp_servers import MCPServerManager
        from runtime.agent.approval import run_with_approval
        from runtime.config.settings import RuntimeSettings
        from runtime.kernel.session import create_token_logger_hooks
        from runtime.kernel.strategies import ExecutionContext, create_execution_strategy
        from runtime.ui.output_visibility import streamed_output_is_sufficient

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
        quarantine_dir = request.workspace_root / ".agent_quarantine"

        async with MCPServerManager(request.workspace_root, quarantine_dir, verbose=verbose_runtime) as mcp_manager:
            run_agent = lambda agent, turn_input, turn_hooks, max_turns=20: run_with_approval(
                agent,
                turn_input,
                turn_hooks,
                session=approval_session,
                max_turns=max_turns,
            )
            strategy = create_execution_strategy(
                routing_input=request.routing_input or request.user_input,
                execution_mode=settings.execution_mode,
            )
            output = await strategy.execute(
                ExecutionContext(
                    request=request,
                    model_registry=model_registry,
                    mcp_manager=mcp_manager,
                    hooks=hooks,
                    run_agent=run_agent,
                    settings=settings,
                )
            )
            started_mcp_ids = list(mcp_manager.started_ids)
            run_context_summary = str(getattr(output, "run_context_summary", "") or "")

        return KernelResponse(
            final_output=str(output or ""),
            turn_status="完成",
            mcp_ids_used=started_mcp_ids,
            run_context_summary=run_context_summary,
            output_already_printed=streamed_output_is_sufficient(hooks),
            _summary_printer=hooks.print_summary,
        )
