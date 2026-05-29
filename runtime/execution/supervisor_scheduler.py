from __future__ import annotations

from runtime.execution.supervisor_observer import _resources_conflict


READONLY_PARALLEL_MCPS = frozenset(
    {
        "project_filesystem_readonly",
        "code_locator",
        "web_search",
        "context7_docs",
        "grep_code_search",
        "git_tools",
    }
)
DECLARED_WRITE_MCPS = frozenset({"workspace_edit"})
SERIAL_MCPS = frozenset({"command_runner", "safe_backup"})


def supervisor_execution_batches_for_full(tasks: list) -> list[list]:
    """Build supervised full-mode batches with deterministic resource safety rules."""

    if not tasks:
        return []

    batches: list[list] = []
    current_batch: list = []
    current_writes: set[str] = set()

    for task in tasks:
        if not current_batch:
            current_batch, current_writes = _new_batch_for_task(task)
            if supervisor_requires_serial_execution(task):
                batches.append(current_batch)
                current_batch, current_writes = [], set()
            continue

        if supervisor_task_conflicts_with_batch(task, current_batch, current_writes):
            batches.append(current_batch)
            current_batch, current_writes = _new_batch_for_task(task)
            if supervisor_requires_serial_execution(task):
                batches.append(current_batch)
                current_batch, current_writes = [], set()
            continue

        current_batch.append(task)
        current_writes.update(supervisor_normalized_write_intent(task))

    if current_batch:
        batches.append(current_batch)
    return batches


def supervisor_task_conflicts_with_batch(task, batch: list, batch_writes: set[str] | None = None) -> bool:
    if _dependency_conflicts(task, batch):
        return True
    if supervisor_requires_serial_execution(task):
        return True
    for existing in batch:
        if supervisor_requires_serial_execution(existing):
            return True

    writes = set(supervisor_normalized_write_intent(task))
    if _write_sets_conflict(writes, set(batch_writes or [])):
        return True
    for existing in batch:
        if _write_sets_conflict(writes, set(supervisor_normalized_write_intent(existing))):
            return True
    return False


def supervisor_requires_serial_execution(task) -> bool:
    mcp_ids = {str(mcp_id or "").strip() for mcp_id in list(getattr(task, "mcp", []) or [])}
    if not mcp_ids:
        return False
    if mcp_ids & SERIAL_MCPS:
        return True
    if "workspace_edit" in mcp_ids and not supervisor_normalized_write_intent(task):
        return True
    allowed = READONLY_PARALLEL_MCPS | DECLARED_WRITE_MCPS
    return any(mcp_id not in allowed for mcp_id in mcp_ids)


def supervisor_normalized_write_intent(task) -> list[str]:
    values: list[str] = []
    for item in list(getattr(task, "write_intent", []) or []):
        value = supervisor_normalize_resource(item).lower()
        if value:
            values.append(value)
    return values


def supervisor_normalize_resource(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    if value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def _new_batch_for_task(task) -> tuple[list, set[str]]:
    return [task], set(supervisor_normalized_write_intent(task))


def _dependency_conflicts(task, batch: list) -> bool:
    task_deps = set(getattr(task, "depends_on", []) or [])
    batch_ids = {getattr(existing, "id", "") for existing in batch}
    if task_deps & batch_ids:
        return True
    task_id = getattr(task, "id", "")
    for existing in batch:
        existing_deps = set(getattr(existing, "depends_on", []) or [])
        if task_id in existing_deps:
            return True
    return False


def _write_sets_conflict(first: set[str], second: set[str]) -> bool:
    for left in first:
        for right in second:
            if _resources_conflict(left, right):
                return True
    return False
