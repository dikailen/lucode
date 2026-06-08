from __future__ import annotations

from pathlib import Path


async def run_serial_request(
    run_input: str,
    project_root: Path,
    model_registry,
    mcp_manager,
    hooks,
    run_agent,
    settings,
    show_plan: bool = True,
    output_controller=None,
) -> str:
    """Compatibility wrapper; SerialStrategy calls dynamic execution directly."""
    from runtime.execution import execute_dynamic_request

    kwargs = {
        "run_agent": run_agent,
        "show_plan": show_plan,
        "settings": settings,
    }
    if output_controller is not None:
        kwargs["output_controller"] = output_controller
    return await execute_dynamic_request(
        run_input,
        project_root,
        model_registry,
        mcp_manager,
        hooks,
        **kwargs,
    )


__all__ = ["run_serial_request"]
