import json
import re
from pathlib import Path

from catalog_system.model_catalog import load_model_catalog
from catalog_system.model_probe import refresh_model_probe_cache
from catalog_system.permission_policy import load_permission_policy
from skills.registry import SKILLS


CATALOG_DIR_NAME = "catalogs"
INTERNAL_SKILLS = {
    "task_router",
    "query_refiner",
    "orchestrator_planner",
    "final_synthesizer",
}

KNOWN_SKILL_POLICIES = {
    "jpc_now_skill": {
        "default_model": "mimo_v25_pro_model",
        "allowed_mcp": [
            "project_filesystem_readonly",
            "code_locator",
            "workspace_edit",
            "command_runner",
            "git_tools",
            "safe_backup",
            "web_search",
        ],
        "cost_level": "medium",
        "risk_level": "medium",
    },
    "humanizer_zh": {
        "default_model": "deepseek_v4_flash_model",
        "allowed_mcp": [],
        "cost_level": "low",
        "risk_level": "low",
    },
    "project_explorer": {
        "default_model": "deepseek_v4_flash_model",
        "allowed_mcp": [
            "project_filesystem_readonly",
            "code_locator",
            "workspace_edit",
            "git_tools",
            "safe_backup",
            "web_search",
        ],
        "cost_level": "medium",
        "risk_level": "low",
    },
    "skill_creator": {
        "default_model": "deepseek_v4_pro_model",
        "allowed_mcp": [
            "skills_filesystem_readonly",
            "workspace_edit",
            "command_runner",
            "git_tools",
            "safe_backup",
            "web_search",
        ],
        "cost_level": "high",
        "risk_level": "medium",
    },
}


def refresh_catalogs(project_root: Path) -> None:
    """Refresh local planning catalogs from skills, known MCPs, and configured models."""

    catalog_dir = project_root / CATALOG_DIR_NAME
    catalog_dir.mkdir(exist_ok=True)

    model_catalog = load_model_catalog(force_reload=True)
    try:
        refresh_model_probe_cache(project_root, model_catalog)
        model_catalog = load_model_catalog(force_reload=True)
    except Exception:
        pass

    _write_json_if_changed(catalog_dir / "skill_catalog.json", build_skill_catalog(project_root))
    _write_json_if_changed(catalog_dir / "mcp_catalog.json", build_mcp_catalog(project_root))
    _write_json_if_changed(catalog_dir / "model_catalog.generated.json", model_catalog)
    _write_json_if_changed(catalog_dir / "permission_policy.json", load_permission_policy())


def build_skill_catalog(project_root: Path) -> dict:
    existing = _load_json(project_root / CATALOG_DIR_NAME / "skill_catalog.json")
    existing_by_id = {item["id"]: item for item in existing.get("skills", []) if "id" in item}

    folder_to_id = {meta["folder"]: skill_id for skill_id, meta in SKILLS.items()}
    skills_dir = project_root / "skills"
    skill_items = []

    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        folder = skill_file.parent.name
        skill_id = folder_to_id.get(folder) or _normalize_id(folder)
        meta = _read_skill_frontmatter(skill_file)
        previous = existing_by_id.get(skill_id, {})
        policy = KNOWN_SKILL_POLICIES.get(skill_id, {})

        description = meta.get("description") or previous.get("summary_zh") or ""
        display_name = previous.get("display_name_zh") or meta.get("name") or folder
        selectable = skill_id not in INTERNAL_SKILLS

        item = {
            "id": skill_id,
            "folder": folder,
            "display_name_zh": display_name,
            "summary_zh": description,
            "tags": previous.get("tags") or _guess_tags(skill_id, description),
            "default_model": previous.get("default_model")
            or policy.get("default_model")
            or _guess_default_model(skill_id, description),
            "allowed_mcp": policy.get("allowed_mcp", previous.get("allowed_mcp") or []),
            "good_for": previous.get("good_for") or _guess_good_for(description),
            "not_for": previous.get("not_for") or [],
            "cost_level": policy.get("cost_level") or previous.get("cost_level") or "medium",
            "risk_level": policy.get("risk_level") or previous.get("risk_level") or "medium",
            "selectable": selectable,
            "internal": skill_id in INTERNAL_SKILLS,
        }
        skill_items.append(item)

    return {
        "version": 1,
        "description": "Auto-refreshed local skill library used by the orchestrator planner. Full SKILL.md files are loaded only when an execution Agent is created.",
        "skills": skill_items,
        "future_memory_interface": {
            "enabled": False,
            "purpose": "Reserved for future knowledge-graph retrieval. The planner must not depend on it yet.",
            "expected_inputs": ["user_preferences", "project_decisions", "active_constraints"],
            "expected_outputs": ["relevant_memory_items"],
        },
    }


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
                "allowed_for_skills": ["jpc_now_skill", "project_explorer"],
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
                "allowed_for_skills": ["jpc_now_skill", "project_explorer"],
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
                "allowed_for_skills": ["jpc_now_skill", "project_explorer", "skill_creator"],
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
                "allowed_for_skills": ["jpc_now_skill", "project_explorer", "skill_creator"],
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
                "allowed_for_skills": ["jpc_now_skill", "skill_creator"],
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
                "allowed_for_skills": ["jpc_now_skill", "project_explorer", "skill_creator"],
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
                "allowed_for_skills": ["jpc_now_skill", "project_explorer", "skill_creator"],
                "approval_required": False,
                "side_effects": "external_network_request",
                "risk_level": "medium",
                "implemented": True,
                "use_when": ["用户明确要求联网", "最新版本或最新文档", "本地文件无法回答", "需要引用外部来源"],
                "avoid_when": ["闲聊", "本地代码足够回答", "中文润色", "不需要时效性的信息"],
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
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    meta = {}
    current_key = None
    lines = block.splitlines()
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:", line):
            key, value = line.split(":", 1)
            current_key = key.strip()
            meta[current_key] = value.strip().strip('"')
        elif current_key and line.startswith((" ", "\t")):
            meta[current_key] = (meta[current_key] + " " + line.strip()).strip()
    return meta


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
    text = f"{skill_id} {description}".lower()
    if any(word in text for word in ["code", "python", "java", "c++", "代码"]):
        return "mimo_model"
    if any(word in text for word in ["skill", "规划", "评估"]):
        return "deepseek_V4_pro_model"
    return "deepseek_V4_flash_model"


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
