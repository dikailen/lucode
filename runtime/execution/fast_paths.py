from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path


def _is_url_only_task(task) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    return any(
        marker in text
        for marker in [
            "url",
            "urls",
            "链接",
            "地址",
            "top urls",
            "仅返回",
            "只返回",
        ]
    )


def _can_fast_path_url_search(task) -> bool:
    return task.skill_id == "project_explorer" and task.mcp == ["web_search"] and _is_url_only_task(task)


def _can_fast_path_git_status(task) -> bool:
    if task.mcp != ["git_tools"]:
        return False
    text = f"{task.title}\n{task.instruction}".lower()
    wants_status = any(
        marker in text
        for marker in [
            "git status",
            "working tree",
            "changed file",
            "changed files",
            "changes",
            "status",
            "工作区",
            "改动",
            "文件名",
            "变更",
        ]
    )
    asks_commit = ("commit" in text and "do not commit" not in text) or (
        "提交" in text and "不要提交" not in text and "不提交" not in text
    )
    return wants_status and not asks_commit


def _can_fast_path_git_diff(task) -> bool:
    if task.mcp != ["git_tools"]:
        return False
    text = f"{task.title}\n{task.instruction}".lower()
    wants_diff = any(marker in text for marker in ["git diff", "diff", "差异", "变更详情", "补丁"])
    asks_commit = "commit" in text and "do not commit" not in text
    return wants_diff and not asks_commit and not getattr(task, "write_intent", None)


def _can_fast_path_mcp_catalog_count(task) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    wants_count = any(marker in text for marker in ["count", "统计", "数量"])
    return "mcp_catalog.json" in text and wants_count and not getattr(task, "write_intent", None)


def _can_fast_path_readme_mcp_count(task) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    wants_count = any(marker in text for marker in ["count", "统计", "数量"])
    return "readme" in text and "mcp" in text and wants_count and not getattr(task, "write_intent", None)


def _can_fast_path_project_manifest_summary(task) -> bool:
    if getattr(task, "write_intent", None):
        return False
    text = _task_text(task)
    if not any(marker in text for marker in ["package.json", "pyproject.toml"]):
        return False
    return any(
        marker in text
        for marker in [
            "summary",
            "summarize",
            "analyze",
            "dependenc",
            "script",
            "version",
            "总结",
            "分析",
            "依赖",
            "脚本",
            "版本",
        ]
    )


def _can_fast_path_config_summary(task) -> bool:
    if getattr(task, "write_intent", None):
        return False
    text = _task_text(task)
    if not any(suffix in text for suffix in [".json", ".toml", ".yaml", ".yml"]):
        return False
    return any(marker in text for marker in ["config", "配置", "读取", "read", "summary", "summarize", "结构"])


def _can_fast_path_directory_summary(project_root: Path, task) -> bool:
    if getattr(task, "write_intent", None):
        return False
    mcp = set(getattr(task, "mcp", []) or [])
    if "project_filesystem_readonly" not in mcp:
        return False
    directories = _directory_summary_paths(project_root, task)
    if not directories:
        return False
    text = _task_text(task)
    return any(marker in text for marker in ["目录", "directory", "folder", "结构", "摘要", "summary", "检查"])


def _task_text(task) -> str:
    read_set = " ".join(str(item) for item in (getattr(task, "read_set", []) or []))
    return f"{task.title}\n{task.instruction}\n{read_set}".lower()


def _run_project_manifest_summary_fast_path(project_root: Path, task) -> str:
    package_path = project_root / "package.json"
    pyproject_path = project_root / "pyproject.toml"
    sections: list[str] = ["项目清单摘要（只读 fast path）"]
    file_count = 0

    if package_path.exists():
        file_count += 1
        sections.extend(["", _summarize_package_json(package_path)])
    if pyproject_path.exists():
        file_count += 1
        sections.extend(["", _summarize_pyproject(pyproject_path)])
    if file_count == 0:
        sections.append("- 未找到 package.json 或 pyproject.toml。")

    output = "\n".join(sections)
    _log_runtime_fast_path(
        project_root,
        tool="project_manifest",
        action="summarize_project_manifests",
        task=task,
        params={"task_id": getattr(task, "id", ""), "file_count": file_count},
        status="success",
        result_summary=output,
    )
    return output


def _run_config_summary_fast_path(project_root: Path, task) -> str:
    candidates = _config_summary_paths(project_root, task)
    sections = ["配置文件摘要（只读 fast path）"]
    for path in candidates:
        sections.extend(["", _summarize_config_file(path)])
    if not candidates:
        sections.append("- 未找到可读取的 JSON/TOML/YAML 配置文件。")

    output = "\n".join(sections)
    _log_runtime_fast_path(
        project_root,
        tool="config_summary",
        action="summarize_config_files",
        task=task,
        params={"task_id": getattr(task, "id", ""), "file_count": len(candidates)},
        status="success",
        result_summary=output,
    )
    return output


def _run_directory_summary_fast_path(project_root: Path, task) -> str:
    directories = _directory_summary_paths(project_root, task)
    sections = ["目录结构摘要（只读 fast path）"]
    for directory in directories:
        sections.extend(["", _summarize_directory(project_root, directory)])
    if not directories:
        sections.append("- 未找到可读取的目录。")

    output = "\n".join(sections)
    _log_runtime_fast_path(
        project_root,
        tool="project_filesystem_readonly",
        action="directory_summary",
        task=task,
        params={
            "task_id": getattr(task, "id", ""),
            "directories": [_relative_path(project_root, path) for path in directories],
        },
        status="success",
        result_summary=output,
    )
    return output


def _run_mcp_catalog_count_fast_path(project_root: Path, task) -> str:
    catalog_path = project_root / "mcp_catalog.json"
    if not catalog_path.exists():
        catalog_path = project_root / "catalogs" / "mcp_catalog.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    servers = payload.get("mcp_servers") or []
    output = (
        f"mcp_catalog.json 中共有 {len(servers)} 个 MCP 服务器。\n"
        "服务器 ID：" + ", ".join(str(item.get("id") or "") for item in servers if item.get("id"))
    )
    _log_runtime_fast_path(
        project_root,
        tool="mcp_catalog_count",
        action="count_mcp_servers",
        task=task,
        params={"task_id": getattr(task, "id", ""), "path": str(catalog_path), "count": len(servers)},
        status="success",
        result_summary=output,
    )
    return output


def _directory_summary_paths(project_root: Path, task) -> list[Path]:
    paths: list[Path] = []
    for raw in list(getattr(task, "read_set", []) or []):
        path = _resolve_readonly_directory(project_root, str(raw))
        if path and path not in paths:
            paths.append(path)
    if paths:
        return paths[:4]

    text = _task_text(task)
    candidates = []
    for match in re_find_directory_like_paths(text):
        path = _resolve_readonly_directory(project_root, match)
        if path and path not in candidates:
            candidates.append(path)
    return candidates[:4]


def re_find_directory_like_paths(text: str) -> list[str]:
    import re

    values = []
    for match in re.findall(r"[\w.-]+(?:/[\w.-]+)+|[\w.-]+", str(text or "")):
        if match in {"and", "or", "summary", "directory", "folder"}:
            continue
        values.append(match)
    return values


def _resolve_readonly_directory(project_root: Path, raw_path: str) -> Path | None:
    value = raw_path.strip().strip("`\"'“”‘’（）()[]<>，,。；;：:")
    if not value or "://" in value:
        return None
    value = value.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if not value or any(part == ".." for part in value.split("/")):
        return None
    root = project_root.resolve()
    path = (root / value).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    if any(part.lower() in {".git", ".venv", "__pycache__", "node_modules", ".agent_quarantine"} for part in relative.parts):
        return None
    return path if path.is_dir() else None


def _summarize_directory(project_root: Path, directory: Path) -> str:
    relative = _relative_path(project_root, directory)
    children = []
    try:
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if _skip_directory_summary_child(child):
                continue
            children.append(child)
    except OSError as exc:
        return f"{relative}\n- 读取失败: {exc}"

    files = [child for child in children if child.is_file()]
    dirs = [child for child in children if child.is_dir()]
    lines = [
        relative,
        f"- 子目录: {len(dirs)} 个；文件: {len(files)} 个",
    ]
    if dirs:
        lines.append("- 子目录列表: " + ", ".join(child.name for child in dirs[:12]))
    if files:
        lines.append("- 主要文件:")
        for file_path in files[:16]:
            lines.append(f"  - {_relative_path(project_root, file_path)}：{_summarize_source_file(file_path)}")
        if len(files) > 16:
            lines.append(f"  - 其余 {len(files) - 16} 个文件已省略。")
    return "\n".join(lines)


def _skip_directory_summary_child(path: Path) -> bool:
    name = path.name.lower()
    return name in {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"} or any(
        marker in name for marker in ["secret", "token", "apikey", "api_key"]
    )


def _summarize_source_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"读取失败: {exc}"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            return stripped.strip("#/ ").strip()[:120] or "源码/文本文件"
        if stripped.startswith('"""') or stripped.startswith("'''"):
            clean = stripped.strip("\"' ").strip()
            if clean:
                return clean[:120]
            continue
        if stripped.startswith(("class ", "def ", "async def ")):
            return f"定义 {stripped[:100]}"
        if stripped.startswith("from ") or stripped.startswith("import "):
            return "Python 模块，包含导入和运行逻辑"
        return stripped[:120]
    return "空文件或无可摘要内容"


def _relative_path(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _run_readme_mcp_count_fast_path(project_root: Path, task) -> str:
    readme_path = project_root / "README.md"
    text = readme_path.read_text(encoding="utf-8")
    count = 0
    ids = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### ") and "MCP" in stripped and "工具服务器" in stripped:
            in_section = True
            continue
        if in_section and stripped.startswith("### "):
            break
        if in_section and stripped.startswith("| `"):
            parts = stripped.split("|")
            if len(parts) >= 2:
                server_id = parts[1].strip().strip("`")
                if server_id and server_id != "MCP 服务器":
                    ids.append(server_id)
                    count += 1
    output = (
        f"README.md 的 MCP 工具服务器章节列出 {count} 个 MCP 服务器。\n"
        "服务器 ID：" + ", ".join(ids)
    )
    _log_runtime_fast_path(
        project_root,
        tool="readme_mcp_count",
        action="count_readme_mcp_servers",
        task=task,
        params={"task_id": getattr(task, "id", ""), "path": str(readme_path), "count": count},
        status="success",
        result_summary=output,
    )
    return output


def _run_git_status_fast_path(project_root: Path, task) -> str:
    print("执行优化：git 只读状态查询直接调用 git status，避免模型二次返场。")
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        shell=False,
    )
    if result.returncode != 0:
        output = (
            "git status 执行失败。\n"
            f"returncode：{result.returncode}\n"
            f"stderr：{result.stderr.strip() or '无'}"
        )
        _log_runtime_fast_path(
            project_root,
            tool="git_status",
            action="git_status",
            task=task,
            params={"task_id": getattr(task, "id", ""), "command": "git status --short"},
            status="failed",
            result_summary=output,
            error=result.stderr.strip() or output,
        )
        return output

    parsed = _parse_git_status_short(result.stdout)
    if not parsed:
        output = "当前 git 工作区没有改动。"
        _log_runtime_fast_path(
            project_root,
            tool="git_status",
            action="git_status",
            task=task,
            params={"task_id": getattr(task, "id", ""), "command": "git status --short", "changed_files": 0},
            status="success",
            result_summary=output,
        )
        return output

    lines = ["当前 git 工作区改动文件："]
    for item in parsed:
        lines.append(f"- {item['path']}（{item['status']}）")
    output = "\n".join(lines)
    _log_runtime_fast_path(
        project_root,
        tool="git_status",
        action="git_status",
        task=task,
        params={
            "task_id": getattr(task, "id", ""),
            "command": "git status --short",
            "changed_files": len(parsed),
        },
        status="success",
        result_summary=output,
    )
    return output


def _run_git_diff_fast_path(project_root: Path, task) -> str:
    stat_result = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        shell=False,
    )
    name_result = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        shell=False,
    )
    if stat_result.returncode != 0:
        output = (
            "git diff 执行失败。\n"
            f"returncode: {stat_result.returncode}\n"
            f"stderr: {stat_result.stderr.strip() or '无'}"
        )
        _log_runtime_fast_path(
            project_root,
            tool="git_diff",
            action="git_diff",
            task=task,
            params={"task_id": getattr(task, "id", ""), "command": "git diff --stat"},
            status="failed",
            result_summary=output,
            error=stat_result.stderr.strip() or output,
        )
        return output

    stat = stat_result.stdout.strip()
    names = [line.strip() for line in name_result.stdout.splitlines() if line.strip()]
    if not stat and not names:
        output = "当前没有未暂存 git diff。"
    else:
        lines = ["git diff --stat", stat or "无统计信息", "", "变更文件："]
        lines.extend(f"- {name}" for name in names[:50])
        if len(names) > 50:
            lines.append(f"- 其余 {len(names) - 50} 个文件已省略。")
        output = "\n".join(lines)

    _log_runtime_fast_path(
        project_root,
        tool="git_diff",
        action="git_diff",
        task=task,
        params={
            "task_id": getattr(task, "id", ""),
            "command": "git diff --stat && git diff --name-only",
            "changed_files": len(names),
        },
        status="success",
        result_summary=output,
    )
    return output


def _parse_git_status_short(stdout: str) -> list[dict[str, str]]:
    items = []
    status_names = {
        "M": "已修改",
        "A": "已新增",
        "D": "已删除",
        "R": "已重命名",
        "C": "已复制",
        "U": "冲突",
        "?": "未跟踪",
        "!": "已忽略",
    }
    for line in stdout.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        raw_path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        status_key = code.strip()[:1] or "?"
        items.append(
            {
                "path": raw_path,
                "status": status_names.get(status_key, code.strip() or "未知"),
            }
        )
    return items


def _summarize_package_json(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"package.json\n- 读取失败: {exc}"
    scripts = payload.get("scripts") or {}
    dependencies = payload.get("dependencies") or {}
    dev_dependencies = payload.get("devDependencies") or {}
    return "\n".join(
        [
            "package.json",
            f"- name: {payload.get('name') or '未填写'}",
            f"- version: {payload.get('version') or '未填写'}",
            f"- scripts: {_join_keys(scripts)}",
            f"- dependencies: {len(dependencies)} 个",
            f"- devDependencies: {len(dev_dependencies)} 个",
        ]
    )


def _summarize_pyproject(path: Path) -> str:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"pyproject.toml\n- 读取失败: {exc}"
    project = payload.get("project") or {}
    dependencies = project.get("dependencies") or []
    optional = project.get("optional-dependencies") or {}
    tool = payload.get("tool") or {}
    return "\n".join(
        [
            "pyproject.toml",
            f"- name: {project.get('name') or '未填写'}",
            f"- version: {project.get('version') or '未填写'}",
            f"- dependencies: {len(dependencies)} 个",
            f"- optional-dependencies: {_join_keys(optional)}",
            f"- tool sections: {_join_keys(tool)}",
        ]
    )


def _config_summary_paths(project_root: Path, task) -> list[Path]:
    paths: list[Path] = []
    for raw in getattr(task, "read_set", []) or []:
        path = _resolve_readonly_config_path(project_root, str(raw))
        if path and path not in paths:
            paths.append(path)
    if paths:
        return paths[:8]

    text = _task_text(task)
    for candidate in sorted(project_root.iterdir() if project_root.exists() else []):
        if candidate.is_file() and candidate.suffix.lower() in {".json", ".toml", ".yaml", ".yml"}:
            if candidate.name.lower() in text:
                paths.append(candidate)
    return paths[:8]


def _resolve_readonly_config_path(project_root: Path, raw_path: str) -> Path | None:
    value = raw_path.strip().strip("\"'")
    if not value:
        return None
    path = (project_root / value).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError:
        return None
    if not path.is_file() or path.suffix.lower() not in {".json", ".toml", ".yaml", ".yml"}:
        return None
    return path


def _summarize_config_file(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
        if suffix == ".json":
            payload = json.loads(text)
            return _summarize_mapping(path.name, payload)
        if suffix == ".toml":
            payload = tomllib.loads(text)
            return _summarize_mapping(path.name, payload)
        return _summarize_yaml_keys(path.name, text)
    except Exception as exc:
        return f"{path.name}\n- 读取失败: {exc}"


def _summarize_mapping(name: str, payload) -> str:
    if isinstance(payload, dict):
        keys = list(payload)
        sensitive = [key for key in keys if _looks_sensitive_key(str(key))]
        lines = [
            name,
            f"- top-level keys: {_join_keys(keys)}",
        ]
        if sensitive:
            lines.append(f"- sensitive keys: {_join_keys(sensitive)}（值已隐藏）")
        return "\n".join(lines)
    if isinstance(payload, list):
        return "\n".join([name, f"- list items: {len(payload)} 个"])
    return "\n".join([name, f"- type: {type(payload).__name__}"])


def _summarize_yaml_keys(name: str, text: str) -> str:
    keys: list[str] = []
    sensitive: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip().strip("\"'")
        if key and key not in keys:
            keys.append(key)
        if _looks_sensitive_key(key) and key not in sensitive:
            sensitive.append(key)
    lines = [name, f"- keys: {_join_keys(keys)}"]
    if sensitive:
        lines.append(f"- sensitive keys: {_join_keys(sensitive)}（值已隐藏）")
    return "\n".join(lines)


def _join_keys(value) -> str:
    if isinstance(value, dict):
        keys = list(value)
    else:
        keys = list(value or [])
    return ", ".join(str(item) for item in keys[:8]) or "无"


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ["key", "token", "secret", "password", "credential"])


def web_search(query: str, max_results: int = 5) -> str:
    from mcp_servers.network.web_search_mcp import web_search as _web_search

    return _web_search(query, max_results=max_results)


def _run_url_search_fast_path(refined_request: str, task) -> str:
    print("执行优化：URL-only 联网任务直接调用 web_search 一次，避免模型重复搜索。")
    query = _build_url_search_query(refined_request, task)
    print("工具调用：runtime -> web_search")
    raw_result = web_search(query, max_results=5)
    print(f"工具完成：runtime <- web_search（结果约 {len(raw_result)} 字符）")

    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        _log_runtime_fast_path(
            Path.cwd(),
            tool="web_search",
            action="web_search",
            task=task,
            params={"task_id": getattr(task, "id", ""), "query": query, "max_results": 5},
            status="success",
            result_summary=raw_result[:500],
        )
        return raw_result

    urls = [item.get("url") for item in payload.get("results", []) if item.get("url")]
    if not urls:
        output = "没有搜索到可靠 URL。"
        _log_runtime_fast_path(
            Path.cwd(),
            tool="web_search",
            action="web_search",
            task=task,
            params={"task_id": getattr(task, "id", ""), "query": query, "max_results": 5, "url_count": 0},
            status="success",
            result_summary=output,
        )
        return output

    output = "\n".join(f"- {url}" for url in urls)
    _log_runtime_fast_path(
        Path.cwd(),
        tool="web_search",
        action="web_search",
        task=task,
        params={
            "task_id": getattr(task, "id", ""),
            "query": query,
            "max_results": 5,
            "url_count": len(urls),
        },
        status="success",
        result_summary=output,
    )
    return output


def _runtime_operation_log(project_root: Path) -> Path:
    override = str(os.environ.get("AGENTS_OPERATION_LOG_PATH") or "").strip()
    if override:
        return Path(override)
    return project_root.resolve() / ".agent_quarantine" / "operations.jsonl"


def _log_runtime_fast_path(
    project_root: Path,
    *,
    tool: str,
    action: str,
    task,
    params: dict,
    status: str,
    result_summary: str,
    error: str = "",
) -> None:
    from mcp_servers.core.operation_log import append_operation_log

    append_operation_log(
        _runtime_operation_log(project_root),
        tool=f"runtime_fast_path.{tool}",
        action=action,
        reason=f"runtime fast path for task {getattr(task, 'id', '') or 'unknown'}",
        status=status,
        params_summary=params,
        approval_required=False,
        approval_note="Read-only runtime fast path.",
        result_summary=result_summary,
        error=error,
    )


def _build_url_search_query(refined_request: str, task) -> str:
    text = f"{refined_request}\n{task.title}\n{task.instruction}"
    lowered = text.lower()
    if "openai" in lowered and "mcp" in lowered and ("agents" in lowered or "sdk" in lowered):
        return "OpenAI Agents SDK MCP documentation"
    return text[:300]
