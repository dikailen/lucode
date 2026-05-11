# Catalogs

This directory is the generated local library used by the dynamic multi-agent runtime.

- `skill_catalog.json`: what each local skill is for, which model it prefers, and which MCPs it may request.
- `mcp_catalog.json`: what each MCP can do, its risk level, and which skills may use it.
- `model_catalog.generated.json`: which models are configured in `.env`, without storing API keys.
- `permission_policy.json`: the local read/edit/delete/bash/git/web permission policy used by the planner and MCP layer.

`main.py` refreshes these catalogs on startup. The planner reads compact summaries from them before choosing direct answer, single-agent, or multi-agent execution. The executor validates the plan against these catalogs before creating any temporary Agent.

Source code is organized separately:

- `catalog_system/`: catalog refresh, catalog loading, and model discovery.
- `planning/`: query refinement, orchestration planning, schema parsing, and plan validation.
- `runtime/`: dynamic Agent creation, execution, temporary run workspace, and multi-agent synthesis.
- `mcp_servers/`: local MCP server factories and MCP tool implementations.

Current MCP layers:

- Budgeted read-only filesystem tools for project and skills directories.
- `code_locator` for finding likely files and symbols before reading large project context, with a local `.agent_cache/` index.
- `workspace_edit` for approved file creation, replacement, patching, and backed-up deletion.
- `command_runner` for approved local commands without shell chaining.
- `git_tools` for git status/diff/log and approved local commits.
- `web_search` for external search/fetch with source tiers: official docs, official GitHub, docs, package registry, general, community.

Knowledge-graph memory is intentionally not implemented yet. The `future_memory_interface` blocks are placeholders for later integration.
