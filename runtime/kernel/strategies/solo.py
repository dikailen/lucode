from __future__ import annotations

from runtime.kernel.strategies.base import ExecutionContext


class SoloStrategy:
    mode_name = "solo"

    async def execute(self, context: ExecutionContext) -> str:
        from runtime.execution.solo_runner import run_solo_request

        return await run_solo_request(
            context.request.user_input,
            context.model_registry,
            context.mcp_manager,
            context.hooks,
            context.run_agent,
            settings=context.settings,
            project_root=context.request.workspace_root,
        )
