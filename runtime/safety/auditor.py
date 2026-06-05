from __future__ import annotations

from dataclasses import dataclass, field
import re

from planning.plan_reviewer import PlanReview
from planning.planner_schema import PlannerResult
from runtime.execution.pipeline import PipelineRunState

GENERIC_SEMANTIC_TERMS = {
    "说明",
    "解释",
    "列出",
    "总结",
    "分析",
    "覆盖",
    "包括",
    "包含",
    "涉及",
    "提到",
    "输出",
    "返回",
    "自然中文",
    "目录",
    "目录结构",
    "职责",
    "用途",
    "更新",
    "正常",
    "完成",
    "结果",
    "内容",
    "这些内容",
    "以下内容",
    "三类职责",
}


@dataclass
class AuditResult:
    passed: bool
    summary: str
    remaining_issues: list[str] = field(default_factory=list)
    modifications: list[str] = field(default_factory=list)
    verifications: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    models_used: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    needs_replan: bool = False
    rollback_happened: bool = False
    rollback_message: str = ""


def audit_execution(
    plan: PlannerResult,
    state: PipelineRunState,
    final_output: str,
    rollback_message: str = "",
    rollback_happened: bool = False,
) -> AuditResult:
    remaining_issues: list[str] = []
    warnings: list[str] = []
    soft_semantic_warnings: dict[str, list[str]] = {}
    modifications: list[str] = []
    verifications: list[str] = []
    models_used = sorted({task.model for task in plan.tasks if task.model})
    files_touched = sorted({path for task in plan.tasks for path in task.write_intent})

    for task in state.tasks:
        if task.status == "completed":
            if task.output_preview:
                modifications.append(f"{task.title}: {_one_line(task.output_preview)}")
            if task.verification:
                verifications.append(f"{task.title}: {_one_line(task.verification)}")
        elif task.status == "failed":
            remaining_issues.append(f"任务 {task.id} 执行失败：{task.error or '无详细错误'}")
        else:
            remaining_issues.append(f"任务 {task.id} 未完成。")

    if state.errors:
        for error in state.errors:
            message = str(error)
            if message not in remaining_issues:
                remaining_issues.append(message)

    for task in plan.tasks:
        record = next((item for item in state.tasks if item.id == task.id), None)
        if not record:
            continue

        if task.acceptance_criteria:
            for item in task.acceptance_criteria:
                criterion = str(item).strip()
                if not criterion:
                    continue
                if criterion.lower().startswith("must_contain:"):
                    needle = criterion.split(":", 1)[1].strip()
                    if needle and needle not in final_output and needle not in record.output_preview:
                        remaining_issues.append(
                            f"任务 {task.id} 的验收标记未出现：{needle}"
                        )
                elif record.status == "completed" and not _criterion_looks_satisfied(
                    criterion,
                    record.output_preview,
                    final_output,
                ):
                    message = f"任务 {task.id} 的语义验收未完全确认：{criterion}"
                    if _should_enforce_semantic_acceptance(task, record, criterion):
                        remaining_issues.append(message)
                    else:
                        _record_soft_semantic_warning(
                            soft_semantic_warnings,
                            task.id,
                            f"验收：{criterion}",
                        )

        if task.expected_outputs and record.status == "completed":
            for expected in task.expected_outputs:
                expected_text = str(expected).strip()
                if not expected_text:
                    continue
                if expected_text.lower().startswith("must_contain:"):
                    needle = expected_text.split(":", 1)[1].strip()
                    if needle and needle not in record.output_preview and needle not in final_output:
                        remaining_issues.append(
                            f"任务 {task.id} 的预期输出未出现：{needle}"
                        )
                elif not _criterion_looks_satisfied(expected_text, record.output_preview, final_output):
                    message = f"任务 {task.id} 的预期输出语义未完全确认：{expected_text}"
                    if _should_enforce_semantic_acceptance(task, record, expected_text):
                        remaining_issues.append(message)
                    else:
                        _record_soft_semantic_warning(
                            soft_semantic_warnings,
                            task.id,
                            f"预期输出：{expected_text}",
                        )

        if "workspace_edit" in task.mcp and record.status == "completed" and not record.verification:
            remaining_issues.append(
                f"任务 {task.id} 修改了文件，但没有 verification 结果，无法确认验收标准是否满足。"
            )

    warnings.extend(_compact_soft_semantic_warnings(soft_semantic_warnings))

    if not final_output.strip():
        remaining_issues.append("最终回答为空。")
    elif _looks_like_process_only_final_answer(final_output):
        remaining_issues.append("最终回答只描述准备或正在执行的步骤，没有给出实际结果。")

    passed = not remaining_issues
    summary = "本轮执行满足计划验收要求。" if passed else "本轮执行仍有未完成问题，需要继续修复。"

    return AuditResult(
        passed=passed,
        summary=summary,
        remaining_issues=remaining_issues,
        modifications=modifications,
        verifications=verifications,
        warnings=warnings,
        models_used=models_used,
        files_touched=files_touched,
        needs_replan=not passed and not rollback_happened,
        rollback_happened=rollback_happened,
        rollback_message=rollback_message,
    )


def format_final_report(final_output: str, audit: AuditResult) -> str:
    lines = [final_output.rstrip() or "本轮没有生成可展示的正文回答。", "", f"最终审核：{'通过' if audit.passed else '未通过'}", audit.summary]

    if audit.modifications:
        lines.append("")
        lines.append("修改内容：")
        lines.extend(f"- {item}" for item in audit.modifications)

    if audit.verifications:
        lines.append("")
        lines.append("验证情况：")
        lines.extend(f"- {item}" for item in audit.verifications)

    if audit.warnings:
        lines.append("")
        lines.append("审核提醒（不影响通过）：" if audit.passed else "审核提醒：")
        lines.extend(f"- {item}" for item in audit.warnings)

    if audit.remaining_issues:
        lines.append("")
        lines.append("剩余问题：")
        lines.extend(f"- {item}" for item in audit.remaining_issues)

    if audit.rollback_happened:
        lines.append("")
        lines.append("回滚状态：")
        lines.append(f"- {audit.rollback_message or '已执行回滚。'}")

    return "\n".join(lines).strip()


def _should_enforce_semantic_acceptance(task, record=None, criterion: str = "") -> bool:
    if _has_successful_verification(record) and not _looks_like_hard_semantic_requirement(criterion):
        return False
    mcp_ids = set(getattr(task, "mcp", []) or [])
    if mcp_ids.intersection({"workspace_edit", "safe_backup", "command_runner"}):
        return True
    if getattr(task, "write_intent", None):
        return True
    return False


def _has_successful_verification(record) -> bool:
    verification = str(getattr(record, "verification", "") or "").lower()
    if not verification:
        return False
    failure_markers = [
        "returncode=1",
        "returncode=2",
        "returncode=124",
        "returncode=127",
        "failed",
        "失败",
        "错误",
        "timed out",
        "timeout",
    ]
    if any(marker in verification for marker in failure_markers):
        return False
    return any(marker in verification for marker in ["returncode=0", "通过", "success", "passed", "ok"])


def _looks_like_hard_semantic_requirement(value: str) -> bool:
    text = _normalize_text(value)
    if not text:
        return False
    hard_markers = [
        "must_contain",
        "必须包含",
        "必须输出",
        "必须返回",
        "不得",
        "不能",
        "禁止",
        "exact",
        "required",
    ]
    if any(marker in text for marker in hard_markers):
        return True
    if re.search(r"[A-Z0-9_]{4,}", str(value or "")):
        return True
    return False


def _record_soft_semantic_warning(warnings_by_task: dict[str, list[str]], task_id: str, detail: str) -> None:
    value = str(detail or "").strip()
    if not value:
        return
    warnings_by_task.setdefault(str(task_id), []).append(value)


def _compact_soft_semantic_warnings(warnings_by_task: dict[str, list[str]]) -> list[str]:
    warnings: list[str] = []
    for task_id, items in warnings_by_task.items():
        unique_items = _dedupe_preserving_order(items)
        if not unique_items:
            continue
        if len(unique_items) == 1:
            warnings.append(f"任务 {task_id} 的语义验收未完全确认：{unique_items[0]}")
            continue
        preview = "；".join(unique_items[:2])
        extra = len(unique_items) - 2
        suffix = f"；另有 {extra} 条已折叠" if extra > 0 else ""
        warnings.append(
            f"任务 {task_id} 的语义验收未完全确认：共 {len(unique_items)} 条只读语义提醒，{preview}{suffix}。"
        )
    return warnings


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = _normalize_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def audit_plan_review_failure(review: PlanReview) -> AuditResult:
    issues = list(review.issues) or ["计划审核未通过。"]
    return AuditResult(
        passed=False,
        summary="计划审核发现当前规划不安全或不完整，需要重规划。",
        remaining_issues=issues,
        modifications=[],
        verifications=[],
        models_used=[],
        files_touched=[],
        needs_replan=True,
    )


def _criterion_looks_satisfied(criterion: str, output_preview: str, final_output: str) -> bool:
    text = _normalize_text(f"{output_preview}\n{final_output}")
    token = _normalize_text(criterion)
    if not token:
        return True
    if token in {"ok", "pass", "success", "通过", "完成"}:
        return any(word in text for word in ["ok", "pass", "success", "完成", "通过"])
    if token in text:
        return True

    required = _extract_required_terms(criterion)
    if not required:
        return True
    matched = [term for term in required if _normalize_text(term) in text]
    if len(required) <= 2:
        return len(matched) == len(required)
    return len(matched) >= max(2, int(len(required) * 0.7 + 0.999))


def _extract_required_terms(criterion: str) -> list[str]:
    text = str(criterion or "")
    explicit = _terms_after_markers(text)
    if explicit:
        return explicit

    terms: list[str] = []
    for term in re.findall(r"[A-Za-z_][A-Za-z0-9_/\-.]{2,}", text):
        _append_term(terms, term)
    for term in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if _is_generic_semantic_term(term):
            continue
        if _looks_like_specific_term(term):
            _append_term(terms, term)
    return terms[:8]


def _terms_after_markers(text: str) -> list[str]:
    terms: list[str] = []
    marker_pattern = r"(?:覆盖|包括|包含|涉及|列出|提到|说明|解释|总结|输出|返回)"
    for match in re.finditer(marker_pattern + r"([^。；;\n]+)", text):
        segment = match.group(1)
        for part in re.split(r"[、,，/和及与\s]+", segment):
            part = part.strip(" ：:，,。.;；()（）[]【】")
            if not part or _is_generic_semantic_term(part):
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]{1}", part):
                continue
            if not _looks_like_specific_term(part):
                continue
            _append_term(terms, part)
    return terms[:8]


def _append_term(terms: list[str], term: str) -> None:
    value = term.strip()
    if not value:
        return
    normalized = _normalize_text(value)
    if not normalized or normalized in {_normalize_text(item) for item in terms}:
        return
    terms.append(value)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _is_generic_semantic_term(value: str) -> bool:
    normalized = _normalize_text(value)
    if normalized in {_normalize_text(item) for item in GENERIC_SEMANTIC_TERMS}:
        return True
    return any(
        normalized.endswith(_normalize_text(suffix))
        for suffix in ["职责", "用途", "内容", "结果", "正常", "通过", "可用", "正确", "成功", "完成"]
    )


def _looks_like_specific_term(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"[A-Za-z0-9_/\-.]", text):
        return True
    if "/" in text or "\\" in text or "." in text:
        return True
    if len(text) <= 8 and not _is_generic_semantic_term(text):
        return True
    return False


def _looks_like_process_only_final_answer(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = _normalize_text(text)
    process_markers = [
        "我会先",
        "我将先",
        "我准备",
        "接下来",
        "正在",
        "将根据需要",
        "根据需要补充",
        "最后输出",
        "先列出",
        "获取两个目录",
        "获取目录",
    ]
    process_count = sum(1 for marker in process_markers if _normalize_text(marker) in normalized)
    if process_count < 2:
        return False
    concrete_markers = [
        "负责",
        "包含",
        "主要文件",
        "用途",
        "覆盖",
        "发现",
        "结论",
        "摘要如下",
        "已检查",
        "已完成",
        "runtime/",
        "tests/",
        ".py",
        ".md",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
    ]
    concrete_count = sum(1 for marker in concrete_markers if _normalize_text(marker) in normalized)
    if concrete_count >= 3:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullet_lines = [line for line in lines if line.startswith(("-", "*"))]
    if bullet_lines and all(_process_only_bullet(line) for line in bullet_lines):
        return True
    return process_count >= 3 and concrete_count <= 1


def _process_only_bullet(line: str) -> bool:
    text = _normalize_text(line)
    if not text:
        return False
    process_tokens = ["runtime/ui", "tests", "目录", "列表", "获取"]
    return any(_normalize_text(token) in text for token in process_tokens) and not any(
        token in text for token in [".py", ".md", ".json", "负责", "用途", "包含"]
    )


def _one_line(value: str, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[截断 {len(text) - limit} 字符]"
