import copy
import json
import os
import re
import threading
from pathlib import Path

from catalog_system.model_catalog import load_model_catalog
from catalog_system.permission_policy import load_permission_policy
from runtime.config.skill_frontmatter import (
    frontmatter_bool,
    frontmatter_list,
    frontmatter_text,
    read_skill_frontmatter,
)
from runtime.config.skill_policy import (
    BORROWABLE_SKILL_SOURCES,
    DEPRECATED_SKILLS,
    INTERNAL_SKILLS,
    RULE_ONLY_SKILLS,
)
from skills.registry import SKILLS


CATALOG_DIR_NAME = "catalogs"
CORE_MCP_IDS = {
    "project_filesystem_readonly",
    "skills_filesystem_readonly",
    "code_locator",
    "safe_backup",
    "workspace_edit",
    "command_runner",
    "git_tools",
    "web_search",
    "context7_docs",
    "grep_code_search",
}
_SKILL_CATALOG_CACHE: dict[tuple, dict] = {}
_SKILL_CATALOG_CACHE_LOCK = threading.Lock()

KNOWN_SKILL_POLICIES = {
    "code_engineer": {
        "display_name_zh": "Code Engineer",
        "summary_zh": "General software engineering skill for code implementation, review, debugging, refactoring, tests, and verification.",
        "tags": ["code", "python", "java", "cpp", "review", "debugging", "testing"],
        "good_for": [
            "code implementation",
            "bug fixes",
            "code review",
            "debugging",
            "refactoring",
            "test writing",
            "verification",
        ],
        "not_for": ["general writing polish", "casual chat", "skill creation"],
        "default_model": "",
        "allowed_mcp": [
            "project_filesystem_readonly",
            "code_locator",
            "workspace_edit",
            "command_runner",
            "git_tools",
            "safe_backup",
            "web_search",
            "context7_docs",
            "grep_code_search",
        ],
        "cost_level": "medium",
        "risk_level": "medium",
    },
    "project_explorer": {
        "default_model": "",
        "allowed_mcp": [
            "project_filesystem_readonly",
            "code_locator",
            "workspace_edit",
            "git_tools",
            "safe_backup",
            "web_search",
            "context7_docs",
            "grep_code_search",
        ],
        "cost_level": "medium",
        "risk_level": "low",
    },
    "skill_creator": {
        "default_model": "",
        "allowed_mcp": [
            "skills_filesystem_readonly",
            "workspace_edit",
            "command_runner",
            "git_tools",
            "safe_backup",
            "web_search",
            "context7_docs",
            "grep_code_search",
        ],
        "cost_level": "high",
        "risk_level": "medium",
    },
    "cli_command_safety": {
        "default_model": "",
        "allowed_mcp": [],
        "cost_level": "low",
        "risk_level": "high",
    },
}


def refresh_catalogs(project_root: Path, probe_mode: str | None = None) -> None:
    """Refresh local planning catalogs from skills, known MCPs, and configured models."""

    catalog_dir = project_root / CATALOG_DIR_NAME
    catalog_dir.mkdir(exist_ok=True)

    model_catalog = load_model_catalog(force_reload=True)
    mode = _normalize_probe_mode(probe_mode or os.environ.get("MODEL_PROBE_STARTUP_MODE") or "background")
    if mode == "sync":
        model_catalog = _run_model_probe_refresh(project_root, model_catalog)
    elif mode == "background":
        _start_background_probe_refresh(project_root, model_catalog)

    _write_json_if_changed(
        catalog_dir / "skill_catalog.json",
        build_skill_catalog(project_root, include_dynamic=False, use_cache=False),
    )
    _write_json_if_changed(catalog_dir / "mcp_catalog.json", build_mcp_catalog(project_root))
    _write_json_if_changed(catalog_dir / "model_catalog.generated.json", model_catalog)
    _write_json_if_changed(catalog_dir / "permission_policy.json", load_permission_policy())


def _normalize_probe_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"sync", "synchronous", "blocking"}:
        return "sync"
    if normalized in {"off", "disabled", "disable", "false", "0", "none"}:
        return "off"
    return "background"


def _start_background_probe_refresh(project_root: Path, model_catalog: dict) -> None:
    def _worker() -> None:
        try:
            refreshed_catalog = _run_model_probe_refresh(project_root, model_catalog)
            catalog_dir = project_root / CATALOG_DIR_NAME
            _write_json_if_changed(catalog_dir / "model_catalog.generated.json", refreshed_catalog)
        except Exception:
            pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def _run_model_probe_refresh(project_root: Path, model_catalog: dict) -> dict:
    try:
        from catalog_system.model_probe import refresh_model_probe_cache

        refresh_model_probe_cache(project_root, model_catalog)
        return load_model_catalog(force_reload=True)
    except Exception:
        return model_catalog


def build_skill_catalog(project_root: Path, *, include_dynamic: bool = True, use_cache: bool = True) -> dict:
    project_root = Path(project_root).resolve()
    skill_roots = _skill_roots(project_root, include_dynamic=include_dynamic)
    cache_key = _skill_catalog_cache_key(project_root, include_dynamic=include_dynamic, skill_roots=skill_roots)
    if use_cache:
        with _SKILL_CATALOG_CACHE_LOCK:
            cached = _SKILL_CATALOG_CACHE.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

    existing = _load_json(project_root / CATALOG_DIR_NAME / "skill_catalog.json")
    existing_by_id = {item["id"]: item for item in existing.get("skills", []) if "id" in item}

    folder_to_id = {meta["folder"]: skill_id for skill_id, meta in SKILLS.items()}
    skill_items = []

    seen_skill_paths: set[Path] = set()
    for skills_dir, source in skill_roots:
        for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
            resolved = skill_file.resolve()
            if resolved in seen_skill_paths:
                continue
            seen_skill_paths.add(resolved)
            folder = skill_file.parent.name
            skill_id = folder_to_id.get(folder) or _normalize_id(folder)
            if skill_id in DEPRECATED_SKILLS:
                continue
            skill_items.append(
                _catalog_item_for_skill_file(
                    skill_file,
                    project_root=project_root,
                    source=source,
                    folder_to_id=folder_to_id,
                    existing_by_id=existing_by_id,
                )
            )

    catalog = {
        "version": 1,
        "description": "Auto-refreshed local skill library used by the orchestrator planner. Internal skills are hidden from planner prompts; borrowable library skills can be shown, and only assignable skills can become task.skill_id.",
        "skills": skill_items,
        "future_memory_interface": {
            "enabled": False,
            "purpose": "Reserved for future knowledge-graph retrieval. The planner must not depend on it yet.",
            "expected_inputs": ["user_preferences", "project_decisions", "active_constraints"],
            "expected_outputs": ["relevant_memory_items"],
        },
    }
    if use_cache:
        with _SKILL_CATALOG_CACHE_LOCK:
            _SKILL_CATALOG_CACHE[cache_key] = copy.deepcopy(catalog)
    return catalog


def _skill_roots(project_root: Path, *, include_dynamic: bool) -> list[tuple[Path, str]]:
    roots = [
        (project_root / "core_skills", "core"),
        (project_root / "skills", "sample"),
    ]
    if include_dynamic:
        roots.append((_user_skill_root(), "user"))
        roots.extend((root, "workspace") for root in _workspace_skill_roots(project_root))
    return roots


def _skill_catalog_cache_key(
    project_root: Path,
    *,
    include_dynamic: bool,
    skill_roots: list[tuple[Path, str]],
) -> tuple:
    root_signatures = []
    for root, source in skill_roots:
        root_signatures.append(
            (
                source,
                str(Path(root).resolve()),
                tuple(_skill_file_signature(path) for path in sorted(Path(root).glob("*/SKILL.md"))),
            )
        )
    return (
        str(project_root),
        include_dynamic,
        os.environ.get("LUCODE_USER_HOME") or "",
        os.environ.get("LUCODE_WORKSPACE_ROOT") or "",
        _path_signature(project_root / CATALOG_DIR_NAME / "skill_catalog.json"),
        tuple(root_signatures),
    )


def _skill_file_signature(path: Path) -> tuple[str, int, int]:
    return (str(path.resolve()), *_path_signature(path))


def _path_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (int(stat.st_mtime_ns), int(stat.st_size))


def _user_skill_root() -> Path:
    return Path(os.environ.get("LUCODE_USER_HOME") or Path.home() / ".lucode") / "skills"


def _workspace_skill_roots(project_root: Path) -> list[Path]:
    roots: list[Path] = []
    app_root = Path(__file__).resolve().parents[1].resolve()
    if Path(project_root).resolve() != app_root:
        roots.append(Path(project_root).resolve() / ".lucode" / "skills")
    workspace_env = os.environ.get("LUCODE_WORKSPACE_ROOT")
    if workspace_env:
        roots.append(Path(workspace_env).resolve() / ".lucode" / "skills")
    return roots


def _catalog_item_for_skill_file(
    skill_file: Path,
    *,
    project_root: Path,
    source: str,
    folder_to_id: dict[str, str],
    existing_by_id: dict[str, dict],
) -> dict:
    folder = skill_file.parent.name
    skill_id = folder_to_id.get(folder) or _normalize_id(folder)
    meta = _read_skill_frontmatter(skill_file)
    previous = existing_by_id.get(skill_id, {})
    if source in {"user", "workspace"} or (previous.get("source") and previous.get("source") != source):
        previous = {}
    policy = KNOWN_SKILL_POLICIES.get(skill_id, {}) if source in {"core", "sample"} else {}

    if source in {"core", "sample"}:
        description = policy.get("summary_zh") or previous.get("summary_zh") or frontmatter_text(meta, "description") or ""
    else:
        description = frontmatter_text(meta, "description") or previous.get("summary_zh") or ""
    display_name = policy.get("display_name_zh") or previous.get("display_name_zh") or frontmatter_text(meta, "name") or folder
    internal = skill_id in INTERNAL_SKILLS or source == "core"
    rule_only = skill_id in RULE_ONLY_SKILLS
    borrowable = (not internal) and source in BORROWABLE_SKILL_SOURCES
    assignable = borrowable and not rule_only
    selectable = assignable
    planner_visible = borrowable
    default_model = policy.get("default_model") or _guess_default_model(skill_id, description)
    allowed_tools = frontmatter_list(meta, "allowed-tools", "allowed_tools")
    frontmatter_mcp = [tool for tool in allowed_tools if tool in CORE_MCP_IDS]
    previous_mcp = [mcp_id for mcp_id in previous.get("allowed_mcp") or [] if mcp_id in CORE_MCP_IDS]
    allowed_mcp = policy.get("allowed_mcp") or previous_mcp or frontmatter_mcp
    trigger = frontmatter_list(meta, "trigger", "triggers")
    argument_hint = frontmatter_text(meta, "argument-hint", "argument_hint")
    model = frontmatter_text(meta, "model")
    disable_model_invocation = frontmatter_bool(
        meta,
        "disable-model-invocation",
        "disable_model_invocation",
    )
    if skill_id == "lucode_native_capability":
        default_model = ""
        allowed_mcp = [
            "project_filesystem_readonly",
            "code_locator",
            "workspace_edit",
            "command_runner",
            "git_tools",
            "safe_backup",
            "web_search",
            "context7_docs",
            "grep_code_search",
        ]

    return {
        "id": skill_id,
        "folder": folder,
        "display_name_zh": display_name,
        "summary_zh": description,
        "tags": policy.get("tags") or previous.get("tags") or _guess_tags(skill_id, description),
        "default_model": default_model,
        "allowed_mcp": allowed_mcp,
        "allowed_tools": allowed_tools,
        "trigger": trigger,
        "argument_hint": argument_hint,
        "model": model,
        "disable_model_invocation": disable_model_invocation,
        "good_for": policy.get("good_for") or previous.get("good_for") or _guess_good_for(description),
        "not_for": policy.get("not_for") or previous.get("not_for") or [],
        "cost_level": policy.get("cost_level") or previous.get("cost_level") or "medium",
        "risk_level": policy.get("risk_level") or previous.get("risk_level") or "medium",
        "borrowable": borrowable,
        "assignable": assignable,
        "selectable": selectable,
        "internal": internal,
        "planner_visible": planner_visible,
        "source": source,
        "path": _catalog_path_for_skill_file(skill_file, source=source, project_root=project_root),
    }


def _catalog_path_for_skill_file(skill_file: Path, *, source: str, project_root: Path) -> str:
    """Return a non-sensitive path that the runtime can resolve by source layer."""

    folder = skill_file.parent.name
    if source == "core":
        return f"core_skills/{folder}"
    if source == "sample":
        return f"skills/{folder}"
    if source == "user":
        return f"skills/{folder}"
    if source == "workspace":
        return f".lucode/skills/{folder}"
    try:
        return skill_file.parent.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return folder


def build_mcp_catalog(project_root: Path | None = None) -> dict:
    known_module_ids = {
        "budgeted_filesystem",
        "command",
        "code_locator",
        "git",
        "safe_delete",
        "workspace_edit",
        "command_runner",
        "git_tools",
        "web_search",
    }
    catalog = {
        "version": 1,
        "description": "Auto-refreshed local MCP library used by the orchestrator planner. The program validates every requested MCP against this catalog before execution.",
        "mcp_servers": [
            {
                "id": "project_filesystem_readonly",
                "display_name_zh": "项目文件只读工具",
                "summary_zh": "带可配置读取预算的项目文件只读工具，可读取目录树、文件信息和少量目标文件，避免一次性吞入过多上下文。",
                "tools": [
                    "list_allowed_directories",
                    "list_directory",
                    "directory_tree",
                    "read_file",
                    "read_multiple_files",
                    "search_files",
                    "get_file_info",
                ],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "project_explorer"],
                "approval_required": False,
                "side_effects": "none",
                "risk_level": "low",
                "implemented": True,
            },
            {
                "id": "skills_filesystem_readonly",
                "display_name_zh": "Skills 目录只读工具",
                "summary_zh": "带可配置读取预算的 skills 目录只读工具，可读取 SKILL.md、参考文件和 skill 结构。",
                "tools": [
                    "list_allowed_directories",
                    "list_directory",
                    "directory_tree",
                    "read_file",
                    "read_multiple_files",
                    "search_files",
                    "get_file_info",
                ],
                "allowed_for_skills": ["skill_creator"],
                "approval_required": False,
                "side_effects": "none",
                "risk_level": "low",
                "implemented": True,
            },
            {
                "id": "code_locator",
                "display_name_zh": "代码定位工具",
                "summary_zh": "在读取大文件前先定位最相关的代码文件、符号和片段；当前支持本地 BM25 召回、Python AST 符号索引、SQLite 调用图缓存和调用链展开，适合代码修复、评审、重构和项目入口查找。",
                "tools": ["locate_code", "get_file_outline"],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "project_explorer"],
                "approval_required": False,
                "side_effects": "none",
                "risk_level": "low",
                "implemented": True,
                "use_when": ["代码定位", "bug 修复前找文件", "代码评审前缩小范围", "项目入口查找"],
                "avoid_when": ["中文润色", "闲聊", "已明确给出唯一目标文件且可直接读取"],
            },
            {
                "id": "safe_backup",
                "display_name_zh": "删除前备份工具",
                "summary_zh": "在用户确认后为目标文件或目录创建 zip 备份。它不会移动、删除或修改原文件。",
                "tools": ["safe_delete_file"],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "project_explorer", "skill_creator"],
                "approval_required": True,
                "side_effects": "creates_zip_backup",
                "risk_level": "medium",
                "implemented": True,
            },
            {
                "id": "workspace_edit",
                "display_name_zh": "项目文件编辑工具",
                "summary_zh": "在项目根目录内创建、写入、精确替换、应用 unified diff patch 或删除文件。写入、patch、删除均需要用户确认；覆盖和删除前会创建 zip 备份。",
                "tools": [
                    "create_file",
                    "write_file",
                    "replace_in_file",
                    "apply_unified_patch",
                    "delete_file",
                ],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "project_explorer", "skill_creator"],
                "approval_required": True,
                "side_effects": "writes_or_deletes_project_files_with_backup",
                "risk_level": "high",
                "implemented": True,
                "use_when": ["用户明确要求创建或修改文件", "代码实现", "删除项目文件", "修改 SKILL.md"],
                "avoid_when": ["只读分析", "闲聊", "用户只要求规划或解释"],
            },
            {
                "id": "command_runner",
                "display_name_zh": "本地命令执行工具",
                "summary_zh": "在项目根目录作为工作目录运行本地命令。命令不经过 shell，危险命令会被拒绝，执行前需要用户确认。",
                "tools": ["run_command"],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "skill_creator"],
                "approval_required": True,
                "side_effects": "runs_local_process",
                "risk_level": "high",
                "implemented": True,
                "use_when": ["运行测试", "运行 lint", "执行编译检查", "查看工具版本"],
                "avoid_when": ["只读项目分析", "用户未授权执行命令"],
            },
            {
                "id": "git_tools",
                "display_name_zh": "Git 项目工具",
                "summary_zh": "读取 git status、diff、log；本地 commit 需要用户确认，不提供 push/reset/clean。",
                "tools": ["git_status", "git_diff", "git_log", "git_commit"],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "project_explorer", "skill_creator"],
                "approval_required": "git_commit_only",
                "side_effects": "git_commit_can_create_local_commit",
                "risk_level": "medium",
                "implemented": True,
                "use_when": ["检查工作区变化", "查看 diff", "生成本地提交"],
                "avoid_when": ["用户未要求 git 信息"],
            },
            {
                "id": "web_search",
                "display_name_zh": "联网搜索与网页读取工具",
                "summary_zh": "搜索最新信息、官方文档和外部资料，并可抓取网页正文用于核验；结果按官方文档、官方 GitHub、文档、包仓库、社区来源分级排序。",
                "tools": ["web_search", "web_fetch"],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "project_explorer", "skill_creator"],
                "approval_required": False,
                "side_effects": "external_network_request",
                "risk_level": "medium",
                "implemented": True,
                "use_when": ["用户明确要求联网", "最新版本或最新文档", "本地文件无法回答", "需要引用外部来源"],
                "avoid_when": ["闲聊", "本地代码足够回答", "中文润色", "不需要时效性的信息"],
            },
            {
                "id": "context7_docs",
                "display_name_zh": "Context7 文档检索工具",
                "summary_zh": "连接 Context7 官方远程 MCP，按库名解析 Context7 library ID，并查询最新库文档与代码示例；适合查框架、SDK、库 API 的用法，不适合发送私有代码或密钥。",
                "tools": ["resolve-library-id", "query-docs"],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "project_explorer", "skill_creator"],
                "approval_required": False,
                "side_effects": "external_network_request_to_context7",
                "risk_level": "medium",
                "implemented": True,
                "use_when": ["需要最新库文档", "需要框架 API 示例", "用户明确要求使用 Context7"],
                "avoid_when": ["查询项目私有代码", "用户提供了密钥或敏感信息", "普通本地文件分析"],
            },
            {
                "id": "grep_code_search",
                "display_name_zh": "Grep GitHub 代码搜索工具",
                "summary_zh": "连接 Vercel Grep 官方远程 MCP，在公开 GitHub 仓库中搜索真实代码片段；适合查开源项目写法、API 调用样例和仓库内代码模式，不适合搜索私有代码。",
                "tools": ["searchGitHub"],
                "allowed_for_skills": ["lucode_native_capability", "code_engineer", "project_explorer", "skill_creator"],
                "approval_required": False,
                "side_effects": "external_network_request_to_grep",
                "risk_level": "medium",
                "implemented": True,
                "use_when": ["需要 GitHub 公开代码示例", "需要搜索真实项目写法", "用户明确要求使用 Grep 或 GitHub 代码搜索"],
                "avoid_when": ["搜索私有仓库代码", "用户提供了密钥或敏感信息", "只需要普通网页搜索"],
            },
        ],
        "future_memory_interface": {
            "enabled": False,
            "purpose": "Reserved for future knowledge-graph backed MCP discovery and user preference retrieval.",
        },
    }
    if project_root is None:
        return catalog

    known_ids = {item["id"] for item in catalog["mcp_servers"]}
    mcp_dir = project_root / "mcp_servers"
    if not mcp_dir.exists():
        return catalog

    for path in sorted(mcp_dir.rglob("*_mcp.py")):
        mcp_id = _normalize_mcp_id(path.stem)
        if not mcp_id or mcp_id in known_ids or mcp_id in known_module_ids:
            continue
        catalog["mcp_servers"].append(
            {
                "id": mcp_id,
                "display_name_zh": f"{mcp_id}（待登记 MCP）",
                "summary_zh": f"检测到文件 {path.name}，但尚未登记到 MCP 图书馆。待登记后才能正式给主脑调度。",
                "tools": [],
                "allowed_for_skills": [],
                "approval_required": True,
                "side_effects": "unknown",
                "risk_level": "unknown",
                "implemented": False,
            }
        )
        known_ids.add(mcp_id)
    return catalog


def _normalize_mcp_id(stem: str) -> str:
    value = stem.removesuffix("_mcp").lower().replace("-", "_")
    return re.sub(r"[^a-z0-9_]+", "_", value).strip("_")


def _read_skill_frontmatter(skill_file: Path) -> dict:
    return read_skill_frontmatter(skill_file)


def _normalize_id(value: str) -> str:
    value = value.lower().replace("-", "_")
    return re.sub(r"[^a-z0-9_]+", "_", value).strip("_")


def _guess_tags(skill_id: str, description: str) -> list[str]:
    text = f"{skill_id} {description}".lower()
    tags = []
    candidates = {
        "code": ["code", "python", "java", "c++", "bug", "代码"],
        "writing": ["writing", "rewrite", "润色", "文本"],
        "project": ["project", "repository", "项目", "目录"],
        "skill": ["skill", "技能", "prompt"],
        "web": ["web", "search", "联网", "官方文档"],
    }
    for tag, words in candidates.items():
        if any(word in text for word in words):
            tags.append(tag)
    return tags or ["general"]


def _guess_default_model(skill_id: str, description: str) -> str:
    return ""


def _guess_good_for(description: str) -> list[str]:
    if not description:
        return []
    return [description[:120]]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_if_changed(path: Path, data: dict) -> None:
    new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == new_text:
        return
    path.write_text(new_text, encoding="utf-8")
