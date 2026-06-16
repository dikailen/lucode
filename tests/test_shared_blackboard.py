from __future__ import annotations

import threading

from planning.planner_schema import PlannedTask
from runtime.agent.supervisor import WorkerReport
from runtime.execution.inline_context import _inline_project_file_context
from runtime.execution.multi_agent_runner import _record_worker_report_to_blackboard
from runtime.execution.run_context import RunContextStore, SharedBlackboard


def test_shared_blackboard_put_get_read_and_render(tmp_path):
    board = SharedBlackboard(tmp_path)

    board.put_read("runtime/app.py", content="print('hello')", summary="read app", source="t1")
    hit = board.get_read("runtime\\app.py")

    assert hit is not None
    assert hit.kind == "read_content"
    assert hit.content == "print('hello')"
    rendered = board.render_for_worker()
    assert "共享黑板" in rendered
    assert "runtime/app.py" in rendered
    assert "read app" in rendered
    assert "print('hello')" in rendered


def test_shared_blackboard_records_insight_and_write(tmp_path):
    board = SharedBlackboard(tmp_path)

    board.put_insight("认证逻辑集中在 auth.py", source="supervisor")
    board.put_write("runtime/auth.py", summary="补充 token 校验", source="worker1")

    rendered = board.render_for_worker()

    assert "认证逻辑集中在 auth.py" in rendered
    assert "runtime/auth.py" in rendered
    assert "补充 token 校验" in rendered


def test_shared_blackboard_concurrent_writes_do_not_drop_entries(tmp_path):
    board = SharedBlackboard(tmp_path)

    def write(index: int) -> None:
        board.put_insight(f"insight-{index}", source=f"t{index}")

    threads = [threading.Thread(target=write, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rendered = board.render_for_worker(max_entries=25)

    assert "insight-0" in rendered
    assert "insight-19" in rendered


def test_run_context_store_mirrors_file_snapshot_to_blackboard(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    store = RunContextStore(tmp_path)

    store.record_file_snapshot(path=target, task_id="reader", summary="读取 a.py", excerpt="L0001: x = 1")

    hit = store.get_read("a.py")
    assert hit is not None
    assert hit.source_task_id == "reader"
    assert "L0001: x = 1" in store.render_for_task("worker")


def test_run_context_store_put_write_is_visible_to_workers(tmp_path):
    store = RunContextStore(tmp_path)

    store.put_write("runtime/new_file.py", summary="worker 写入新文件", source="worker1")

    rendered = store.render_for_task("worker2")

    assert "runtime/new_file.py" in rendered
    assert "worker 写入新文件" in rendered


def test_inline_context_reuses_blackboard_read_cache(monkeypatch, tmp_path):
    target = tmp_path / "a.py"
    target.write_text("disk content should not be read\n", encoding="utf-8")
    store = RunContextStore(tmp_path)
    store.put_read("a.py", content="cached blackboard content", summary="cached a.py", source="supervisor")
    task = PlannedTask(
        id="reader",
        title="读取 a.py",
        instruction="分析 a.py",
        skill_id="project_explorer",
        model="m1",
        mcp=["project_filesystem_readonly"],
        read_set=["a.py"],
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("blackboard cache miss caused a file read")

    monkeypatch.setattr("runtime.execution.inline_context._read_project_file_excerpt", fail_if_called)

    rendered = _inline_project_file_context(tmp_path, task, "分析 a.py", run_context=store)

    assert "cached blackboard content" in rendered


def test_worker_report_writes_summary_and_written_files_to_blackboard(tmp_path):
    store = RunContextStore(tmp_path)
    run_state = type("RunState", (), {"run_context": store})()
    report = WorkerReport(
        task_id="worker-auth",
        summary="implemented token validation",
        files_written=["runtime/auth.py"],
    )

    assert _record_worker_report_to_blackboard(run_state, report) is True

    rendered = store.render_for_task("worker-tests")
    assert "runtime/auth.py" in rendered
    assert "implemented token validation" in rendered
    assert "worker-auth" in rendered


def test_worker_report_blackboard_noops_without_run_context():
    report = WorkerReport(
        task_id="worker-empty",
        summary="nothing to record",
        files_written=["runtime/unused.py"],
    )

    assert _record_worker_report_to_blackboard(None, report) is False
    assert _record_worker_report_to_blackboard(object(), report) is False


def test_multiple_worker_reports_keep_written_file_sources_separate(tmp_path):
    store = RunContextStore(tmp_path)
    run_state = type("RunState", (), {"run_context": store})()

    _record_worker_report_to_blackboard(
        run_state,
        WorkerReport(task_id="worker-a", summary="changed auth", files_written=["runtime/auth.py"]),
    )
    _record_worker_report_to_blackboard(
        run_state,
        WorkerReport(task_id="worker-b", summary="changed tests", files_written=["tests/test_auth.py"]),
    )

    assert store.blackboard.entries["write:runtime/auth.py"].source_task_id == "worker-a"
    assert store.blackboard.entries["write:tests/test_auth.py"].source_task_id == "worker-b"
    rendered = store.render_for_task("worker-c")
    assert "runtime/auth.py" in rendered
    assert "tests/test_auth.py" in rendered
    assert "changed auth" in rendered
    assert "changed tests" in rendered
