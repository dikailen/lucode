from __future__ import annotations

from runtime.kernel.strategies.base import ExecutionContext


class FullStrategy:
    mode_name = "full"

    async def execute(self, context: ExecutionContext) -> str:
        from runtime.execution import execute_dynamic_request

        return await execute_dynamic_request(
            context.request.user_input,
            context.request.workspace_root,
            context.model_registry,
            context.mcp_manager,
            context.hooks,
            run_agent=context.run_agent,
            settings=context.settings,
            show_plan=context.request.show_plan,
        )
