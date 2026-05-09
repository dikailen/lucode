from __future__ import annotations

from runtime.safety.auditor import AuditResult


def should_retry(attempt: int, max_attempts: int, audit: AuditResult) -> bool:
    return attempt < max_attempts and audit.needs_replan


def build_repair_request(raw_user_input: str, audit: AuditResult, attempt: int) -> str:
    issues = "\n".join(f"- {item}" for item in audit.remaining_issues) or "- 无明确剩余问题，但审计未通过。"
    strategy = repair_strategy_for_audit(audit)
    return (
        f"这是针对同一请求的第 {attempt} 轮自动修复。\n"
        f"原始请求：{raw_user_input}\n"
        "上一轮审计发现的问题：\n"
        f"{issues}\n\n"
        f"本轮修复策略：{strategy['type']}\n"
        f"策略说明：{strategy['instruction']}\n\n"
        "请基于当前项目最新状态重新规划剩余问题，避免重复完全相同的方法；"
        "优先补齐未完成项、缺失验证和顺序不合理的步骤。"
    )


def repair_strategy_for_audit(audit: AuditResult) -> dict[str, str]:
    issues = " ".join(audit.remaining_issues).lower()
    if "verification" in issues or "diff" in issues or "test" in issues:
        return {
            "type": "verification_failed",
            "instruction": (
                "Please run verification again only where needed, read the failure logs, "
                "and repair the smallest possible unfinished acceptance items."
            ),
        }
    if "tool" in issues or "tools" in issues or "mcp" in issues:
        return {
            "type": "tool_capability_mismatch",
            "instruction": (
                "Please switch to a tool-capable model or downgrade the task to a conservative direct answer "
                "if tool usage is impossible in the current privacy mode."
            ),
        }
    if "conflict" in issues or "write" in issues or "parallel" in issues:
        return {
            "type": "write_conflict",
            "instruction": (
                "Please serialize conflicting tasks, narrow write_intent, and re-plan dependencies "
                "before applying more edits."
            ),
        }
    return {
        "type": "general_replan",
        "instruction": (
            "Please re-check acceptance gaps, missing verification, and oversized task scope before retrying."
        ),
    }
