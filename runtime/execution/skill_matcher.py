from __future__ import annotations

from pathlib import Path

from runtime.config.extensions import discover_skill_layers


BORROWABLE_SKILL_SOURCES = ("workspace", "user", "sample")
MAX_MATCHES = 2
MAX_BODY_CHARS = 900


def render_matching_user_skill_context(user_input: str, workspace_context=None) -> str:
    query = str(user_input or "").strip().lower()
    if not query:
        return ""
    matches: list[tuple[int, dict]] = []
    try:
        layers = discover_skill_layers(workspace_context)
    except Exception:
        return ""
    seen_ids: set[str] = set()
    for source in BORROWABLE_SKILL_SOURCES:
        for item in layers.get(source) or []:
            if item.get("blocked") or item.get("borrowable") is False:
                continue
            skill_id = str(item.get("id") or "")
            if skill_id in seen_ids:
                continue
            score = _skill_match_score(item, query)
            if score > 0:
                seen_ids.add(skill_id)
                matches.append((score, item))
                if len(matches) >= MAX_MATCHES:
                    break
        if len(matches) >= MAX_MATCHES:
            break
    if not matches:
        return ""

    lines = ["匹配到的可借阅 Skill："]
    for _, item in sorted(matches, key=lambda entry: entry[0], reverse=True):
        lines.append(f"- {item.get('id')} | {_source_label(item.get('source'))} | {item.get('summary_zh') or '无说明'}")
        triggers = item.get("trigger") or []
        if triggers:
            lines.append(f"  触发词：{', '.join(str(value) for value in triggers)}")
        body = _skill_body_excerpt(item.get("path"))
        if body:
            lines.append("  指令片段：")
            lines.append(_indent(body, "    "))
    return "\n".join(lines)


def _skill_match_score(item: dict, query: str) -> int:
    compact_query = "".join(query.split())
    if not compact_query:
        return 0

    explicit_names = [item.get("id"), item.get("folder")]
    for raw in explicit_names:
        value = _normalize_match_text(raw)
        if value and (value in compact_query or compact_query == value):
            return 100

    for raw in item.get("trigger") or []:
        value = _normalize_match_text(raw)
        if value and value in compact_query:
            return 90

    if len(compact_query) < 4:
        return 0

    display_name = _normalize_match_text(item.get("display_name_zh"))
    if display_name and (display_name in compact_query or compact_query in display_name):
        return 60

    summary = _normalize_match_text(item.get("summary_zh"))
    if summary and compact_query in summary:
        return 35
    return 0


def _normalize_match_text(value) -> str:
    return "".join(str(value or "").strip().lower().split())


def _skill_body_excerpt(path_value) -> str:
    if not path_value:
        return ""
    skill_file = Path(str(path_value)) / "SKILL.md"
    try:
        text = skill_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    body = _strip_frontmatter(text).strip()
    if not body:
        return ""
    if len(body) > MAX_BODY_CHARS:
        return body[:MAX_BODY_CHARS].rstrip() + "..."
    return body


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :]


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in str(text).splitlines())


def _source_label(source: str | None) -> str:
    return {
        "user": "用户全局",
        "workspace": "当前项目",
        "sample": "图书馆",
    }.get(str(source or ""), str(source or "未知"))
