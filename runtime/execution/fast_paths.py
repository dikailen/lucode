from __future__ import annotations

import json
import os
import subprocess
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


def _can_fast_path_mcp_catalog_count(task) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    wants_count = any(marker in text for marker in ["count", "统计", "数量"])
    return "mcp_catalog.json" in text and wants_count and not getattr(task, "write_intent", None)


def _can_fast_path_readme_mcp_count(task) -> bool:
    text = f"{task.title}\n{task.instruction}".lower()
    wants_count = any(marker in text for marker in ["count", "统计", "数量"])
    return "readme" in text and "mcp" in text and wants_count and not getattr(task, "write_intent", None)


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
