from __future__ import annotations

import json
from dataclasses import dataclass, field

from runtime.agent.approval_policy import FullModeApprovalPolicy
from runtime.agent.runner import run_agent_once
from runtime.common.text_utils import sanitize_text
from runtime.hooks import record_post_tool_use, record_pre_tool_use
from runtime.safety.command_analyzer import analyze_command, render_command_analysis


@dataclass
class ApprovalTerminalResult:
    final_output: str
    interruptions: list = field(default_factory=list)


async def run_with_approval(agent, run_input, hooks, session=None, max_turns=20, approval_policy=None):
    """Run an agent and ask the user before executing approval-required tools."""

    once_approved_signatures = set()
    approved_tools_for_session = set()
    approved_tool_rules = set()
    result = await run_agent_once(agent, run_input, hooks, max_turns=max_turns)

    while result.interruptions:
        state = result.to_state()

        for item in result.interruptions:
            tool_name = item.qualified_name or item.name
            signature = (tool_name, item.arguments or "")
            tool_rule = approval_tool_rule(tool_name)
            pre_event = record_pre_tool_use(hooks, tool_name, item.arguments, tool_rule=tool_rule)
            policy_decision = approval_policy.decide(tool_name, item.arguments) if approval_policy is not None else None
            if policy_decision is not None and policy_decision.approve:
                state.approve(item)
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="approved",
                    status="supervisor_auto_approved",
                    reason=policy_decision.reason or "full_supervisor_planned_scope",
                )
                continue
            if policy_decision is not None and policy_decision.reject:
                state.reject(
                    item,
                    rejection_message=policy_decision.rejection_message
                    or "主管拒绝了该工具调用，请调整为已声明的工具和命令后继续。",
                )
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="rejected",
                    status="supervisor_rejected",
                    reason=policy_decision.reason or "full_supervisor_policy_rejected",
                )
                continue
            if (
                tool_name in approved_tools_for_session
                or tool_rule in approved_tool_rules
            ):
                state.approve(item)
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="approved",
                    status="auto_approved",
                    reason="session_or_rule_approval",
                )
                continue
            if signature in once_approved_signatures:
                state.reject(
                    item,
                    rejection_message=(
                        "同一工具调用已经按“允许一次”执行过。请不要重复请求相同工具，"
                        "请根据上一次工具结果直接给出最终回答。"
                    ),
                )
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="rejected",
                    status="duplicate_rejected",
                    reason="once_signature_already_used",
                )
                continue

            print("\n--- 需要你的确认 ---")
            print(f"工具：{tool_name}")
            preview = format_tool_preview(tool_name, item.arguments)
            if preview:
                print(preview)
            print("参数：")
            print(format_tool_arguments(item.arguments))
            print("说明：请检查参数。写入、删除、命令或提交类工具可能改变项目状态；删除/覆盖会先做备份。")

            if session is not None:
                answer = await session.request_approval(approval_prompt())
            else:
                try:
                    answer = sanitize_text(input(approval_prompt())).strip().lower()
                except EOFError:
                    answer = ""
            if answer in {"yes", "y", "once", "o", "1"}:
                state.approve(item)
                once_approved_signatures.add(signature)
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="approved",
                    status="approved_once",
                    reason="user_approved_once",
                )
            elif answer in {"session", "s", "all", "2"}:
                state.approve(item)
                approved_tools_for_session.add(tool_name)
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="approved",
                    status="approved_session_tool",
                    reason="user_approved_tool_for_session",
                )
            elif answer in {"rule", "r", "3"}:
                state.approve(item)
                approved_tool_rules.add(tool_rule)
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="approved",
                    status="approved_session_rule",
                    reason="user_approved_rule_for_session",
                )
            elif answer in {"no", "n", "deny", "reject", "0"}:
                state.reject(
                    item,
                    rejection_message=(
                        "用户拒绝了该工具调用。"
                        "请停止请求写入、删除、命令或提交工具，并给出替代建议。"
                    ),
                )
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="rejected",
                    status="denied",
                    reason="user_denied",
                )
                return ApprovalTerminalResult(
                    final_output=(
                        f"已拒绝工具调用：{tool_name}。\n"
                        "命令或写入操作没有执行。你可以调整任务范围后重新提出。"
                    )
                )
            elif answer in {"edit", "e", "4"}:
                state.reject(
                    item,
                    rejection_message=(
                        "用户选择编辑指令而不是批准当前工具调用。请停止当前工具请求，"
                        "用更小范围、更明确、更安全的方式重新提出方案。"
                    ),
                )
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="rejected",
                    status="edit_requested",
                    reason="user_requested_tool_instruction_edit",
                )
            else:
                state.reject(
                    item,
                    rejection_message=(
                        "用户未批准该工具调用，或当前输入流无法交互审批。"
                        "请停止请求写入、删除、命令或提交工具，并给出替代建议。"
                    ),
                )
                record_post_tool_use(
                    hooks,
                    pre_event,
                    decision="rejected",
                    status="denied",
                    reason="user_denied_or_noninteractive",
                )

        result = await run_agent_once(agent, state, hooks, max_turns=max_turns)

    return result


def approval_prompt() -> str:
    return (
        "是否批准执行？"
        " y=yes=允许一次，n=no=拒绝，session=本会话允许同一工具，"
        "rule=本会话允许同类工具，edit=让模型改指令："
    )


def approval_tool_rule(tool_name: str) -> str:
    name = str(tool_name or "")
    if "." in name:
        return name.split(".", 1)[0]
    if "_" in name:
        return name.split("_", 1)[0]
    return name


def format_tool_arguments(arguments):
    if not arguments:
        return "无"

    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments

    return json.dumps(parsed, ensure_ascii=False, indent=2)


def format_tool_preview(tool_name: str, arguments: str | None) -> str:
    if not arguments:
        return ""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return ""
    name = str(tool_name or "")
    path = parsed.get("path") or parsed.get("target") or parsed.get("file_path") or ""
    reason = parsed.get("reason") or ""
    if any(marker in name for marker in ["write_file", "create_file", "replace_in_file", "apply_unified_patch"]):
        lines = ["写入预览"]
        if path:
            lines.append(f"- 目标：{path}")
        if "content" in parsed:
            lines.append(f"- 内容长度：{len(str(parsed.get('content') or ''))} 字符")
        if "old_text" in parsed:
            lines.append(f"- 将替换文本长度：{len(str(parsed.get('old_text') or ''))} 字符")
        if "new_text" in parsed:
            lines.append(f"- 新文本长度：{len(str(parsed.get('new_text') or ''))} 字符")
        if "patch" in parsed:
            patch_text = str(parsed.get("patch") or "")
            lines.append(f"- Patch 长度：{len(patch_text)} 字符")
            patch_preview = patch_preview_lines(patch_text)
            if patch_preview:
                lines.append("Patch 预览：")
                lines.extend(patch_preview)
        if parsed.get("expected_sha256") or parsed.get("expected_sha256_map"):
            lines.append("- 已提供 sha256 基线")
        return "\n".join(lines)
    if "delete" in name or "safe_delete" in name:
        lines = ["删除/备份预览"]
        if path:
            lines.append(f"- 目标：{path}")
        if reason:
            lines.append(f"- 说明：{reason}")
        lines.append("- 删除或覆盖前会按工具策略创建备份。")
        return "\n".join(lines)
    if "command" in name:
        command = parsed.get("command") or parsed.get("message") or ""
        lines = ["执行预览", f"- 内容：{command or '未提供'}"]
        if command:
            lines.extend(render_command_analysis(analyze_command(command)))
        return "\n".join(lines)
    if "git_commit" in name:
        message = parsed.get("message") or ""
        return "\n".join(["执行预览", f"- 内容：{message or '未提供'}"])
    return ""


def patch_preview_lines(patch_text: str, max_lines: int = 18, max_chars: int = 1800) -> list[str]:
    text = str(patch_text or "").strip()
    if not text:
        return []
    raw_lines = text.splitlines()
    preview_lines = raw_lines[:max_lines]
    rendered: list[str] = []
    used_chars = 0
    truncated = len(raw_lines) > len(preview_lines)
    for line in preview_lines:
        remaining = max_chars - used_chars
        if remaining <= 0:
            truncated = True
            break
        visible = line[:remaining]
        rendered.append(f"  {visible}")
        used_chars += len(visible)
        if len(visible) < len(line):
            truncated = True
            break
    if truncated:
        rendered.append("  ...已截断，完整 diff 请用 /diff 查看。")
    return rendered
