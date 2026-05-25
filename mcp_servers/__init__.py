import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace

from runtime.agents.sdk import mcp_stdio_class, mcp_streamable_http_class, static_tool_filter_factory
from runtime.safety.privacy import PrivacyPolicy
from runtime.tools.registry import build_tool_registry


MCP_MODULES = {
    "budgeted_filesystem": "mcp_servers.readonly.budgeted_filesystem_mcp",
    "code_locator": "mcp_servers.readonly.code_locator_mcp",
    "safe_delete": "mcp_servers.mutation.safe_delete_mcp",
    "workspace_edit": "mcp_servers.mutation.workspace_edit_mcp",
    "command_runner": "mcp_servers.execution.command_mcp",
    "git_tools": "mcp_servers.execution.git_mcp",
    "web_search": "mcp_servers.network.web_search_mcp",
}

READ_ONLY_FILESYSTEM_TOOLS = [
    "list_allowed_directories",
    "list_directory",
    "directory_tree",
    "read_file",
    "read_multiple_files",
    "search_files",
    "get_file_info",
]
DEFAULT_READONLY_BUDGET_PROFILE = {
    "max_read_calls": "10",
    "max_files_per_call": "5",
    "max_chars_per_file": "6000",
    "max_total_chars": "30000",
    "max_tree_depth": "3",
    "max_tree_entries": "350",
    "supervisor_expansion": "0",
    "supervisor_extra_read_calls": "0",
    "supervisor_extra_total_chars": "0",
}
FULL_SUPERVISOR_READONLY_BUDGET_PROFILE = {
    "max_read_calls": "14",
    "max_files_per_call": "8",
    "max_chars_per_file": "9000",
    "max_total_chars": "60000",
    "max_tree_depth": "4",
    "max_tree_entries": "600",
    "supervisor_expansion": "1",
    "supervisor_extra_read_calls": "6",
    "supervisor_extra_total_chars": "30000",
}
FULL_SUPERVISOR_READONLY_MCP_IDS = {"project_filesystem_readonly", "skills_filesystem_readonly"}


def _env_value(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _safe_mcp_env(values: dict[str, str]) -> dict[str, str]:
    """Return a minimal child-process env without inherited API keys or tokens."""

    denied_markers = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
    project_root = str(Path(__file__).resolve().parent.parent)
    safe = {}
    for key, value in values.items():
        normalized = str(key or "").upper()
        if any(marker in normalized for marker in denied_markers):
            continue
        safe[str(key)] = str(value)
    safe.setdefault("PYTHONIOENCODING", "utf-8")
    safe.setdefault("PYTHONPATH", project_root)
    return safe


def _module_args(module_name: str) -> list[str]:
    return ["-m", module_name]


def create_readonly_filesystem_server(
    root_dir: Path,
    name: str,
    budget_profile: dict[str, str] | None = None,
):
    """Create a budgeted read-only filesystem MCP server limited to one directory."""

    profile = {**DEFAULT_READONLY_BUDGET_PROFILE, **(budget_profile or {})}
    MCPServerStdio = mcp_stdio_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStdio(
        name=name,
        params={
            "command": sys.executable,
            "args": _module_args(MCP_MODULES["budgeted_filesystem"]),
            "env": _safe_mcp_env({
                "BUDGETED_FS_ROOT": str(root_dir),
                "BUDGETED_FS_LABEL": name,
                "BUDGETED_FS_MAX_READ_CALLS": _env_value("MCP_FS_MAX_READ_CALLS", profile["max_read_calls"]),
                "BUDGETED_FS_MAX_FILES_PER_CALL": _env_value(
                    "MCP_FS_MAX_FILES_PER_CALL",
                    profile["max_files_per_call"],
                ),
                "BUDGETED_FS_MAX_CHARS_PER_FILE": _env_value(
                    "MCP_FS_MAX_CHARS_PER_FILE",
                    profile["max_chars_per_file"],
                ),
                "BUDGETED_FS_MAX_TOTAL_CHARS": _env_value(
                    "MCP_FS_MAX_TOTAL_CHARS",
                    profile["max_total_chars"],
                ),
                "BUDGETED_FS_MAX_TREE_DEPTH": _env_value("MCP_FS_MAX_TREE_DEPTH", profile["max_tree_depth"]),
                "BUDGETED_FS_MAX_TREE_ENTRIES": _env_value(
                    "MCP_FS_MAX_TREE_ENTRIES",
                    profile["max_tree_entries"],
                ),
                "BUDGETED_FS_SUPERVISOR_EXPANSION": _env_value(
                    "MCP_FS_SUPERVISOR_EXPANSION",
                    profile["supervisor_expansion"],
                ),
                "BUDGETED_FS_SUPERVISOR_EXTRA_READ_CALLS": _env_value(
                    "MCP_FS_SUPERVISOR_EXTRA_READ_CALLS",
                    profile["supervisor_extra_read_calls"],
                ),
                "BUDGETED_FS_SUPERVISOR_EXTRA_TOTAL_CHARS": _env_value(
                    "MCP_FS_SUPERVISOR_EXTRA_TOTAL_CHARS",
                    profile["supervisor_extra_total_chars"],
                ),
                "PYTHONIOENCODING": "utf-8",
            }),
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=READ_ONLY_FILESYSTEM_TOOLS,
        ),
        require_approval="never",
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    )


def create_safe_delete_server(project_root: Path, quarantine_dir: Path):
    """Create a safe-delete MCP server that always requires user approval."""

    MCPServerStdio = mcp_stdio_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStdio(
        name="safe_delete_mcp",
        params={
            "command": sys.executable,
            "args": _module_args(MCP_MODULES["safe_delete"]),
            "env": _safe_mcp_env({
                "SAFE_DELETE_PROJECT_ROOT": str(project_root),
                "SAFE_DELETE_QUARANTINE_DIR": str(quarantine_dir),
                "SAFE_DELETE_MAX_BACKUP_BYTES": _env_value("SAFE_DELETE_MAX_BACKUP_BYTES", str(50 * 1024 * 1024)),
                "SAFE_DELETE_MAX_BACKUP_FILES": _env_value("SAFE_DELETE_MAX_BACKUP_FILES", "5000"),
                "PYTHONIOENCODING": "utf-8",
            }),
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


def create_web_search_server(project_root: Path):
    """Create a web-search MCP server for current external information lookup."""

    approval = "never"
    if PrivacyPolicy.from_env().mode == "offline":
        approval = "always"

    MCPServerStdio = mcp_stdio_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStdio(
        name="web_search_mcp",
        params={
            "command": sys.executable,
            "args": _module_args(MCP_MODULES["web_search"]),
            "env": _safe_mcp_env({
                "PYTHONIOENCODING": "utf-8",
                "WEB_SEARCH_MAX_RESULTS": "5",
                "WEB_SEARCH_TIMEOUT_SECONDS": "15",
            }),
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=["web_search", "web_fetch"],
        ),
        require_approval=approval,
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    )


def create_context7_docs_server(project_root: Path):
    """Create a hosted Context7 MCP server for current library documentation lookup."""

    headers = {}
    api_key = os.environ.get("CONTEXT7_API_KEY")
    if api_key:
        headers["CONTEXT7_API_KEY"] = api_key

    MCPServerStreamableHttp = mcp_streamable_http_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStreamableHttp(
        name="context7_docs_mcp",
        params={
            "url": os.environ.get("CONTEXT7_MCP_URL") or "https://mcp.context7.com/mcp",
            "headers": headers,
            "timeout": _env_float("CONTEXT7_MCP_TIMEOUT_SECONDS", 30.0),
            "sse_read_timeout": _env_float("CONTEXT7_MCP_SSE_TIMEOUT_SECONDS", 30.0),
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=["resolve-library-id", "query-docs"],
        ),
        require_approval="never",
        cache_tools_list=True,
        client_session_timeout_seconds=_env_float("CONTEXT7_MCP_SESSION_TIMEOUT_SECONDS", 30.0),
        max_retry_attempts=int(_env_value("CONTEXT7_MCP_MAX_RETRY_ATTEMPTS", "1")),
    )


def create_grep_code_search_server(project_root: Path):
    """Create Vercel Grep's hosted MCP server for public GitHub code search."""

    MCPServerStreamableHttp = mcp_streamable_http_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStreamableHttp(
        name="grep_code_search_mcp",
        params={
            "url": os.environ.get("GREP_MCP_URL") or "https://mcp.grep.app",
            "timeout": _env_float("GREP_MCP_TIMEOUT_SECONDS", 45.0),
            "sse_read_timeout": _env_float("GREP_MCP_SSE_TIMEOUT_SECONDS", 45.0),
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=["searchGitHub"],
        ),
        require_approval="never",
        cache_tools_list=True,
        client_session_timeout_seconds=_env_float("GREP_MCP_SESSION_TIMEOUT_SECONDS", 45.0),
        max_retry_attempts=int(_env_value("GREP_MCP_MAX_RETRY_ATTEMPTS", "1")),
    )


def create_code_locator_server(project_root: Path):
    """Create a read-only code locator MCP server for scoped code discovery."""

    MCPServerStdio = mcp_stdio_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStdio(
        name="code_locator_mcp",
        params={
            "command": sys.executable,
            "args": _module_args(MCP_MODULES["code_locator"]),
            "env": _safe_mcp_env({
                "CODE_LOCATOR_PROJECT_ROOT": str(project_root),
                "CODE_LOCATOR_CACHE_DIR": str(project_root / ".agent_cache"),
                "CODE_LOCATOR_MAX_FILES": _env_value("CODE_LOCATOR_MAX_FILES", "700"),
                "CODE_LOCATOR_MAX_FILE_BYTES": _env_value("CODE_LOCATOR_MAX_FILE_BYTES", "300000"),
                "PYTHONIOENCODING": "utf-8",
            }),
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=["locate_code", "get_file_outline"],
        ),
        require_approval="never",
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    )


def create_workspace_edit_server(project_root: Path, quarantine_dir: Path):
    """Create a workspace editing MCP server. Mutating tools require approval."""

    MCPServerStdio = mcp_stdio_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStdio(
        name="workspace_edit_mcp",
        params={
            "command": sys.executable,
            "args": _module_args(MCP_MODULES["workspace_edit"]),
            "env": _safe_mcp_env({
                "WORKSPACE_EDIT_PROJECT_ROOT": str(project_root),
                "WORKSPACE_EDIT_QUARANTINE_DIR": str(quarantine_dir),
                "WORKSPACE_EDIT_STRICT_SHA256": _env_value("WORKSPACE_EDIT_STRICT_SHA256", "1"),
                "WORKSPACE_EDIT_MAX_BACKUP_BYTES": _env_value(
                    "WORKSPACE_EDIT_MAX_BACKUP_BYTES",
                    str(50 * 1024 * 1024),
                ),
                "WORKSPACE_EDIT_MAX_BACKUP_FILES": _env_value("WORKSPACE_EDIT_MAX_BACKUP_FILES", "5000"),
                "PYTHONIOENCODING": "utf-8",
            }),
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=[
                "create_file",
                "write_file",
                "replace_in_file",
                "apply_unified_patch",
                "delete_file",
            ],
        ),
        require_approval="always",
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    )


def create_command_runner_server(project_root: Path, quarantine_dir: Path):
    """Create a command runner MCP server. Commands require approval."""

    MCPServerStdio = mcp_stdio_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStdio(
        name="command_runner_mcp",
        params={
            "command": sys.executable,
            "args": _module_args(MCP_MODULES["command_runner"]),
            "env": _safe_mcp_env({
                "COMMAND_RUNNER_PROJECT_ROOT": str(project_root),
                "COMMAND_RUNNER_QUARANTINE_DIR": str(quarantine_dir),
                "PYTHONIOENCODING": "utf-8",
            }),
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=["run_command"],
        ),
        require_approval="always",
        cache_tools_list=True,
        client_session_timeout_seconds=30,
    )


def create_git_tools_server(project_root: Path, quarantine_dir: Path):
    """Create a Git helper MCP server. Read-only tools are allowed; commits require approval."""

    MCPServerStdio = mcp_stdio_class()
    create_static_tool_filter = static_tool_filter_factory()
    return MCPServerStdio(
        name="git_tools_mcp",
        params={
            "command": sys.executable,
            "args": _module_args(MCP_MODULES["git_tools"]),
            "env": _safe_mcp_env({
                "GIT_TOOLS_PROJECT_ROOT": str(project_root),
                "GIT_TOOLS_QUARANTINE_DIR": str(quarantine_dir),
                "PYTHONIOENCODING": "utf-8",
            }),
        },
        tool_filter=create_static_tool_filter(
            allowed_tool_names=["git_status", "git_diff", "git_log", "git_commit"],
        ),
        require_approval={
            "always": {
                "tool_names": ["git_commit"],
            },
        },
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
        self._readonly_budget_profiles: dict[str, dict[str, str]] = {}

    async def __aenter__(self):
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return await self._stack.__aexit__(exc_type, exc, tb)

    async def get(self, mcp_id: str):
        if mcp_id in self._servers:
            return self._servers[mcp_id]

        self.validate_mcp_id(mcp_id)
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

    def set_readonly_budget_profile(self, mcp_id: str, profile: dict[str, str]) -> None:
        self._readonly_budget_profiles[mcp_id] = dict(profile)

    def validate_mcp_id(self, mcp_id: str) -> None:
        registry = build_tool_registry(workspace_context=self._registry_context())
        registry.validate_core_mcp_start(mcp_id)

    def _registry_context(self):
        app_home = Path(os.environ.get("LUCODE_APP_HOME") or Path(__file__).resolve().parent.parent).resolve()
        user_home = Path(os.environ.get("LUCODE_USER_HOME") or Path.home() / ".lucode").resolve()
        return SimpleNamespace(app_home=app_home, user_home=user_home, workspace_root=self.project_root)

    def _create_server(self, mcp_id: str):
        if mcp_id == "project_filesystem_readonly":
            return create_readonly_filesystem_server(
                self.project_root,
                "project_filesystem_readonly",
                budget_profile=self._readonly_budget_profiles.get(mcp_id),
            )
        if mcp_id == "skills_filesystem_readonly":
            return create_readonly_filesystem_server(
                self.project_root / "skills",
                "skills_filesystem_readonly",
                budget_profile=self._readonly_budget_profiles.get(mcp_id),
            )
        if mcp_id == "safe_backup":
            return create_safe_delete_server(self.project_root, self.quarantine_dir)
        if mcp_id == "web_search":
            return create_web_search_server(self.project_root)
        if mcp_id == "context7_docs":
            return create_context7_docs_server(self.project_root)
        if mcp_id == "grep_code_search":
            return create_grep_code_search_server(self.project_root)
        if mcp_id == "code_locator":
            return create_code_locator_server(self.project_root)
        if mcp_id == "workspace_edit":
            return create_workspace_edit_server(self.project_root, self.quarantine_dir)
        if mcp_id == "command_runner":
            return create_command_runner_server(self.project_root, self.quarantine_dir)
        if mcp_id == "git_tools":
            return create_git_tools_server(self.project_root, self.quarantine_dir)
        raise KeyError(f"Unknown MCP server id: {mcp_id}")


def apply_full_supervisor_readonly_budget_profile(mcp_manager, mcp_ids: list[str]) -> bool:
    """Apply the wider full-mode readonly budget before MCP servers start."""

    setter = getattr(mcp_manager, "set_readonly_budget_profile", None)
    if not callable(setter):
        return False
    applied = False
    for mcp_id in list(mcp_ids or []):
        if mcp_id not in FULL_SUPERVISOR_READONLY_MCP_IDS:
            continue
        setter(mcp_id, FULL_SUPERVISOR_READONLY_BUDGET_PROFILE)
        applied = True
    return applied
