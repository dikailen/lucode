# Catalogs

This directory is the generated local library used by the dynamic multi-agent runtime.

- `skill_catalog.json`: what each local skill is for, which model it prefers, and which MCPs it may request.
- `mcp_catalog.json`: what each MCP can do, its risk level, and which skills may use it.
- `model_catalog.generated.json`: which models are configured in `.env`, without storing API keys.

`main.py` refreshes these catalogs on startup. The planner reads compact summaries from them before choosing direct answer, single-agent, or multi-agent execution. The executor validates the plan against these catalogs before creating any temporary Agent.

Source code is organized separately:

- `catalog_system/`: catalog refresh, catalog loading, and model discovery.
- `planning/`: query refinement, orchestration planning, schema parsing, and plan validation.
- `runtime/`: dynamic Agent creation, execution, temporary run workspace, and multi-agent synthesis.
- `mcp_servers/`: local MCP server factories and MCP tool implementations.

Knowledge-graph memory is intentionally not implemented yet. The `future_memory_interface` blocks are placeholders for later integration.
