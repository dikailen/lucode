from __future__ import annotations

from pathlib import Path


async def run_full_request(
    run_input: str,
    project_root: Path,
    model_registry,
    mcp_manager,
    hooks,
    run_agent,
    settings,
    show_plan: bool = True,
) -> str:
    """Compatibility wrapper; FullStrategy calls dynamic execution directly."""
    from runtime.execution import execute_dynamic_request

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


__all__ = ["run_full_request"]
