from __future__ import annotations

from pathlib import Path

from runtime.execution.dynamic import execute_dynamic_request


async def run_serial_request(
    run_input: str,
    project_root: Path,
    model_registry,
    mcp_manager,
    hooks,
    run_agent,
    settings,
    show_plan: bool = True,
) -> str:
    return await execute_dynamic_request(
        run_input,
        project_root,
        model_registry,
        mcp_manager,
        hooks,
        run_agent=run_agent,
        show_plan=show_plan,
        settings=settings,
    )
