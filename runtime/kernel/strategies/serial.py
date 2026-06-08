from __future__ import annotations

from runtime.kernel.strategies.base import ExecutionContext


class SerialStrategy:
    mode_name = "serial"

    async def execute(self, context: ExecutionContext) -> str:
        from runtime.execution import execute_dynamic_request

        kwargs = {
            "run_agent": context.run_agent,
            "settings": context.settings,
            "show_plan": context.request.show_plan,
            "display_input": context.request.routing_input or context.request.user_input,
        }
        if getattr(context, "output_controller", None) is not None:
            kwargs["output_controller"] = context.output_controller
        return await execute_dynamic_request(
            context.request.user_input,
            context.request.workspace_root,
            context.model_registry,
            context.mcp_manager,
            context.hooks,
            **kwargs,
        )
