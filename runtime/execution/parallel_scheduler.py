from runtime.config.execution_mode import normalize_execution_mode


def _can_run_group_in_parallel(tasks: list) -> bool:
    return len(_execution_batches_for_group(tasks)) == 1 and len(tasks) > 1


def _format_parallel_batch_audit(group_id: int, batch: list) -> str:
    parts = []
    for task in batch:
        writes = _normalized_write_intent(task)
        read_set = [str(item).strip().replace("\\", "/") for item in list(getattr(task, "read_set", []) or [])]
        scope = ", ".join(writes or [item for item in read_set if item] or ["只读/未声明写入"])
        parts.append(f"{getattr(task, 'id', 'unknown')}[{scope}]")
    return (
        f"执行并行组 {group_id}：安全并行启动 {len(batch)} 个临时 Agent。"
        "依据：任务无依赖关系，写入路径无重叠、无父子包含关系。"
        f"批次范围：{'；'.join(parts)}"
    )


def _execution_batches_for_mode(tasks: list, execution_mode: str) -> list[list]:
    mode = normalize_execution_mode(execution_mode)
    if mode == "full":
        return _execution_batches_for_group(tasks)
    return [[task] for task in tasks]


def _execution_batches_for_group(tasks: list) -> list[list]:
    if not tasks:
        return []

    batches: list[list] = []
    current_batch: list = []
    current_writes: set[str] = set()

    for task in tasks:
        if not current_batch:
            current_batch = [task]
            current_writes = set(_normalized_write_intent(task))
            if _requires_serial_execution(task):
                batches.append(current_batch)
                current_batch = []
                current_writes = set()
            continue

        if _task_conflicts_with_batch(task, current_batch, current_writes):
            batches.append(current_batch)
            current_batch = [task]
            current_writes = set(_normalized_write_intent(task))
            if _requires_serial_execution(task):
                batches.append(current_batch)
                current_batch = []
                current_writes = set()
            continue

        current_batch.append(task)
        current_writes.update(_normalized_write_intent(task))

    if current_batch:
        batches.append(current_batch)
    return batches


def _task_conflicts_with_batch(task, batch: list, batch_writes: set[str]) -> bool:
    task_deps = set(getattr(task, "depends_on", []) or [])
    batch_ids = {getattr(existing, "id", "") for existing in batch}
    if task_deps & batch_ids:
        return True
    for existing in batch:
        existing_deps = set(getattr(existing, "depends_on", []) or [])
        if getattr(task, "id", "") in existing_deps:
            return True

    if _requires_serial_execution(task):
        return True

    writes = set(_normalized_write_intent(task))
    if _write_sets_conflict(writes, batch_writes):
        return True

    for existing in batch:
        if _requires_serial_execution(existing):
            return True
        if _write_sets_conflict(writes, set(_normalized_write_intent(existing))):
            return True
    return False


def _requires_serial_execution(task) -> bool:
    if "safe_backup" in task.mcp:
        return True
    if "workspace_edit" in task.mcp and not _normalized_write_intent(task):
        return True
    for mcp_id in task.mcp:
        if mcp_id not in {"workspace_edit", "project_filesystem_readonly", "code_locator", "web_search", "git_tools"}:
            return True
    return False


def _normalized_write_intent(task) -> list[str]:
    values = []
    for item in list(getattr(task, "write_intent", []) or []):
        value = _normalize_write_path(item)
        if value:
            values.append(value.lower())
    return values


def _normalize_write_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    if value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def _write_sets_conflict(first: set[str], second: set[str]) -> bool:
    for left in first:
        for right in second:
            if _write_paths_conflict(left, right):
                return True
    return False


def _write_paths_conflict(left: str, right: str) -> bool:
    left = _normalize_write_path(left).lower()
    right = _normalize_write_path(right).lower()
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right + "/") or right.startswith(left + "/")
