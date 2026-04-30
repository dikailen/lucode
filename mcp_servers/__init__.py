import sys
from contextlib import AsyncExitStack
from pathlib import Path

from agents.mcp import MCPServerStdio, create_static_tool_filter


READ_ONLY_FILESYSTEM_TOOLS = [
    "list_allowed_directories",
    "list_directory",
    "directory_tree",
    "read_file",
    "read_multiple_files",
    "search_files",
    "get_file_info",
]


def create_readonly_filesystem_server(root_dir: Path, name: str) -> MCPServerStdio:
    """Create a read-only filesystem MCP server limited to one directory."""

    return MCPServerStdio(
        name=name,
        params={
            "command": sys.executable,
            "args": [
                str(Path(__file__).resolve().parent / "quiet_stdio.py"),
                "npx",
                "--yes",
                "--loglevel=error",
                "--no-update-notifier",
                "@modelcontextprotocol/server-filesystem",
                str(root_dir),
            ],
            "env": {
                "NO_UPDATE_NOTIFIER": "1",
                "NPM_CONFIG_LOGLEVEL": "error",
            },
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=READ_ONLY_FILESYSTEM_TOOLS,
        ),
        require_approval="never",
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    )


def create_safe_delete_server(project_root: Path, quarantine_dir: Path) -> MCPServerStdio:
    """Create a safe-delete MCP server that always requires user approval."""

    return MCPServerStdio(
        name="safe_delete_mcp",
        params={
            "command": sys.executable,
            "args": [
                str(Path(__file__).resolve().parent / "safe_delete_mcp.py"),
            ],
            "env": {
                "SAFE_DELETE_PROJECT_ROOT": str(project_root),
                "SAFE_DELETE_QUARANTINE_DIR": str(quarantine_dir),
                "PYTHONIOENCODING": "utf-8",
            },
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=["safe_delete_file"],
        ),
        require_approval={
            "always": {
                "tool_names": ["safe_delete_file"],
            },
        },
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    )


def create_web_search_server(project_root: Path) -> MCPServerStdio:
    """Create a web-search MCP server for current external information lookup."""

    return MCPServerStdio(
        name="web_search_mcp",
        params={
            "command": sys.executable,
            "args": [
                str(Path(__file__).resolve().parent / "web_search_mcp.py"),
            ],
            "env": {
                "PYTHONIOENCODING": "utf-8",
                "WEB_SEARCH_MAX_RESULTS": "5",
                "WEB_SEARCH_TIMEOUT_SECONDS": "15",
            },
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=["web_search", "web_fetch"],
        ),
        require_approval="never",
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    )


class MCPServerManager:
    """Lazy-start MCP servers only when a planned task actually needs them."""

    def __init__(self, project_root: Path, quarantine_dir: Path | None = None, verbose: bool = False):
        self.project_root = project_root.resolve()
        self.quarantine_dir = (quarantine_dir or self.project_root / ".agent_quarantine").resolve()
        self.verbose = verbose
        self._stack = AsyncExitStack()
        self._servers = {}

    async def __aenter__(self):
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return await self._stack.__aexit__(exc_type, exc, tb)

    async def get(self, mcp_id: str):
        if mcp_id in self._servers:
            return self._servers[mcp_id]

        server = self._create_server(mcp_id)
        if self.verbose:
            print(f"启动 MCP：{mcp_id}")
        self._servers[mcp_id] = await self._stack.enter_async_context(server)
        return self._servers[mcp_id]

    async def get_many(self, mcp_ids: list[str]):
        return [await self.get(mcp_id) for mcp_id in mcp_ids]

    @property
    def started_ids(self) -> list[str]:
        return sorted(self._servers)

    def _create_server(self, mcp_id: str) -> MCPServerStdio:
        if mcp_id == "project_filesystem_readonly":
            return create_readonly_filesystem_server(
                self.project_root,
                "project_filesystem_readonly",
            )
        if mcp_id == "skills_filesystem_readonly":
            return create_readonly_filesystem_server(
                self.project_root / "skills",
                "skills_filesystem_readonly",
            )
        if mcp_id == "safe_backup":
            return create_safe_delete_server(self.project_root, self.quarantine_dir)
        if mcp_id == "web_search":
            return create_web_search_server(self.project_root)
        raise KeyError(f"Unknown MCP server id: {mcp_id}")
