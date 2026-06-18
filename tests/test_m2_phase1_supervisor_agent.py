from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from planning.planner_schema import PlannerResult
from runtime.execution.run_context import RunContextStore
from runtime.agent.supervisor import WorkerReport
from runtime.agents.factory import AgentFactory
from runtime.execution.pipeline import PipelineRunState


class _FakeRegistry:
    def get_model(self, model_id):
        return f"model:{model_id}"

    def get_model_info(self, model_id):
        del model_id
        return {"supports_tools": True}


class _FakeMcpManager:
    pass


class _FakeReadonlyServer:
    name = "run_workspace_readonly"


def _team_plan() -> PlannerResult:
    return PlannerResult(
        route_type="multi_agent",
        reason="team supervisor final answer",
        refined_request="修复认证逻辑并汇总结果",
        tasks=[],
        needs_synthesis=False,
        memory_interface={
            "execution_contract": {
                "supervisor_route": "team",
                "summary_helper": {"enabled": False, "reason": "lead_supervisor_final_answer"},
            }
        },
    )


def test_create_supervisor_agent_loads_full_supervisor_skill():
    factory = AgentFactory(_FakeRegistry(), _FakeMcpManager())
    server = _FakeReadonlyServer()

    agent = factory.create_supervisor_agent("supervisor-model", [server])

    assert agent.name == "full_supervisor_agent"
    assert agent.model == "model:supervisor-model"
    assert agent.mcp_servers == [server]
    assert "主管" in agent.instructions
    assert "Full Supervisor Contract" in agent.instructions


def test_supervisor_finalizer_calls_agent_with_reports_and_blackboard(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    captured = {}

    class FakeFactory:
        def create_supervisor_agent(self, model_id, readonly_servers):
            captured["model_id"] = model_id
            captured["servers"] = readonly_servers
            return SimpleNamespace(name="full_supervisor_agent")

    class FakeServer:
        async def __aenter__(self):
            return _FakeReadonlyServer()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_create_readonly_server(run_dir, server_name):
        captured["run_dir"] = run_dir
        captured["server_name"] = server_name
        return FakeServer()

    async def fake_run_agent(agent, prompt, hooks, **kwargs):
        captured["agent"] = agent
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return SimpleNamespace(final_output="主管 Agent 收口结果")

    monkeypatch.setattr(runner, "create_readonly_filesystem_server", fake_create_readonly_server)
    state = PipelineRunState.create("修复认证逻辑", _team_plan(), project_root=tmp_path, mode="full")
    state.run_context.put_insight("auth.py 已经由 worker-a 读取", source="worker-a")

    output = asyncio.run(
        runner._finalize_with_supervisor_agent(
            refined_request="修复认证逻辑",
            plan=_team_plan(),
            run_dir=tmp_path,
            run_state=state,
            mode="full",
            model_id="supervisor-model",
            factory=FakeFactory(),
            hooks=None,
            run_agent=fake_run_agent,
            worker_outputs=[("worker-a", "Auth", "done")],
            worker_reports=[WorkerReport(task_id="worker-a", status="completed", summary="done")],
            lead_review_findings=[],
            lead_rework_limits=[],
        )
    )

    assert output == "主管 Agent 收口结果"
    assert captured["model_id"] == "supervisor-model"
    assert captured["servers"][0].name == "run_workspace_readonly"
    assert captured["server_name"] == "run_workspace_readonly"
    assert captured["agent"].name == "full_supervisor_agent"
    assert "WorkerReport" in captured["prompt"]
    assert "共享黑板" in captured["prompt"]
    assert "auth.py 已经由 worker-a 读取" in captured["prompt"]
    assert captured["kwargs"]["stream_output"] is False


def test_full_team_finalization_prefers_supervisor_agent(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    async def fake_supervisor_finalize(**kwargs):
        assert kwargs["model_id"] == "supervisor-model"
        return "主管 Agent 最终答案"

    def fail_template(*args, **kwargs):
        del args, kwargs
        raise AssertionError("template fallback should not run when supervisor agent succeeds")

    monkeypatch.setattr(runner, "_finalize_with_supervisor_agent", fake_supervisor_finalize)
    monkeypatch.setattr(runner, "_render_lead_supervisor_output", fail_template)

    output = asyncio.run(
        runner._run_multi_agent(
            "修复认证逻辑",
            _team_plan(),
            tmp_path,
            "supervisor-model",
            factory=SimpleNamespace(),
            hooks=None,
            run_agent=None,
            run_state=PipelineRunState.create("修复认证逻辑", _team_plan(), project_root=tmp_path, mode="full"),
            execution_mode="full",
            show_progress=False,
        )
    )

    assert output == "主管 Agent 最终答案"


def test_full_team_finalization_falls_back_to_template_when_agent_unavailable(monkeypatch, tmp_path):
    from runtime.execution import multi_agent_runner as runner

    async def fake_supervisor_finalize(**kwargs):
        del kwargs
        return ""

    monkeypatch.setattr(runner, "_finalize_with_supervisor_agent", fake_supervisor_finalize)

    output = asyncio.run(
        runner._run_multi_agent(
            "修复认证逻辑",
            _team_plan(),
            tmp_path,
            "supervisor-model",
            factory=SimpleNamespace(),
            hooks=None,
            run_agent=None,
            run_state=PipelineRunState.create("修复认证逻辑", _team_plan(), project_root=tmp_path, mode="full"),
            execution_mode="full",
            show_progress=False,
        )
    )

    assert output.startswith("主管最终汇报")


def test_planning_supervisor_scout_reads_key_files_into_blackboard(tmp_path):
    from planning.planner import scout_project_context_for_planning

    (tmp_path / "README.md").write_text("# Demo\nProject overview\n", encoding="utf-8")
    package = tmp_path / "package.json"
    package.write_text('{"scripts":{"test":"pytest"}}\n', encoding="utf-8")
    (tmp_path / "notes.log").write_text("ignore me\n", encoding="utf-8")
    store = RunContextStore(tmp_path)

    context = scout_project_context_for_planning(
        "检查当前项目结构和测试入口",
        project_root=tmp_path,
        run_context=store,
        max_files=2,
    )

    rendered = store.render_for_task("worker")
    assert "规划期主管侦察" in context
    assert "README.md" in context
    assert "package.json" in context
    assert "Project overview" in rendered
    assert "pytest" in rendered
    assert "notes.log" not in rendered


def test_preview_plan_includes_planning_scout_context_in_planner_prompt(monkeypatch, tmp_path):
    from planning import planner

    (tmp_path / "README.md").write_text("# Demo\nPlanner scout target\n", encoding="utf-8")
    store = RunContextStore(tmp_path)
    captured = {}

    class FakeRunner:
        @staticmethod
        async def run(agent, prompt, hooks=None):
            del agent, hooks
            captured["prompt"] = prompt
            return SimpleNamespace(
                final_output=json.dumps(
                    {
                        "route_type": "single_agent",
                        "reason": "scout context available",
                        "tasks": [],
                        "needs_synthesis": False,
                    }
                )
            )

    monkeypatch.setattr(planner, "runner_class", lambda: FakeRunner)
    monkeypatch.setattr(planner, "build_orchestrator_planner", lambda *args, **kwargs: SimpleNamespace(name="planner"))

    refined, plan = asyncio.run(
        planner.preview_plan(
            "分析当前项目",
            refiner_model=None,
            planner_model=object(),
            refiner_enabled=False,
            project_root=tmp_path,
            run_context=store,
        )
    )

    assert refined.refined_request == "分析当前项目"
    assert plan.route_type == "single_agent"
    assert "规划期主管侦察" in captured["prompt"]
    assert "README.md" in captured["prompt"]
    assert "Planner scout target" in store.render_for_task("worker")
