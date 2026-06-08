from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from catalog_system.model_catalog import ModelRegistry
    from runtime.config.settings import RuntimeSettings


async def execute_dynamic_request(
    raw_user_input: str,
    project_root: Path,
    model_registry: "ModelRegistry",
    mcp_manager: Any,
    hooks: Any,
    run_agent: Any,
    show_plan: bool = False,
    settings: "RuntimeSettings | None" = None,
    display_input: str | None = None,
    output_controller: Any = None,
) -> str:
    from runtime.execution.dynamic import execute_dynamic_request as _execute_dynamic_request

    return await _execute_dynamic_request(
        raw_user_input,
        project_root,
        model_registry,
        mcp_manager,
        hooks,
        run_agent=run_agent,
        show_plan=show_plan,
        settings=settings,
        display_input=display_input,
        output_controller=output_controller,
    )


__all__ = ["execute_dynamic_request"]
