from __future__ import annotations

import contextlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeRichLiveRuntime:
    instances: list["FakeRichLiveRuntime"] = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = dict(kwargs)
        self.refresh_calls = []
        self.stop_calls = []
        FakeRichLiveRuntime.instances.append(self)

    def refresh(self, run_state, *, mode: str, attempt: int, active: str = "") -> bool:
        from runtime.ui.rich_live_view import build_rich_live_view

        view = build_rich_live_view(run_state, mode=mode, attempt=attempt, active=active)
        self.refresh_calls.append(
            {
                "mode": mode,
                "attempt": attempt,
                "active": active,
                "actor_titles": [block.title for block in view.actor_blocks],
                "actor_model_labels": [block.model_label for block in view.actor_blocks],
                "actor_statuses": [block.status for block in view.actor_blocks],
            }
        )
        return True

    def stop(self, final_behavior: str = "clear") -> None:
        self.stop_calls.append(final_behavior)


class SoloRichLiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeRichLiveRuntime.instances.clear()

    async def test_solo_uses_rich_live_actor_when_dynamic_ui_is_on(self):
        from runtime.config.settings import RuntimeSettings
        from runtime.execution.solo_runner import run_solo_request

        dynamic_status_calls = []

        @contextlib.contextmanager
        def fake_dynamic_status(*args, **kwargs):
            dynamic_status_calls.append({"args": args, "kwargs": dict(kwargs)})
            yield

        class Registry:
            def first_configured(self, model_ids):
                return list(model_ids)[0]

            def get_model_info(self, model_id):
                return {
                    "display_name_zh": "DeepSeek V4 Pro",
                    "model_name": "deepseek-v4-pro",
                    "provider": "deepseek",
                }

        class McpManager:
            def set_readonly_budget_profile(self, *args, **kwargs):
                pass

            async def get_many(self, mcp_ids):
                return []

        class FakeFactory:
            def __init__(self, *args, **kwargs):
                pass

            def create_solo_agent(self, model_id, mcp_servers):
                return {"model_id": model_id, "servers": mcp_servers}

        class Result:
            final_output = "solo answer"

        run_agent_kwargs = []

        async def fake_run_agent(*args, **kwargs):
            run_agent_kwargs.append(dict(kwargs))
            return Result()

        settings = RuntimeSettings(
            executor_model_priority=["deepseek_v4_pro_model"],
            privacy_mode="offline",
            execution_mode="solo",
        )

        with patch.dict(os.environ, {"AGENTS_DYNAMIC_UI": "on"}, clear=False), patch(
            "runtime.execution.solo_runner.RichLiveRuntime",
            FakeRichLiveRuntime,
            create=True,
        ), patch("runtime.execution.solo_runner.AgentFactory", FakeFactory), patch(
            "runtime.execution.solo_runner.dynamic_status",
            fake_dynamic_status,
        ):
            output = await run_solo_request(
                "answer directly",
                Registry(),
                McpManager(),
                hooks=None,
                run_agent=fake_run_agent,
                settings=settings,
                project_root=Path.cwd(),
            )

        self.assertEqual(str(output), "solo answer")
        self.assertEqual(len(FakeRichLiveRuntime.instances), 1)
        runtime = FakeRichLiveRuntime.instances[0]
        self.assertTrue(runtime.refresh_calls)
        self.assertTrue(runtime.stop_calls)
        first_call = runtime.refresh_calls[0]
        last_call = runtime.refresh_calls[-1]
        self.assertEqual(first_call["mode"], "solo")
        self.assertEqual(first_call["attempt"], 1)
        self.assertEqual(first_call["actor_titles"], ["Agent"])
        self.assertEqual(first_call["actor_model_labels"], ["DeepSeek V4 Pro"])
        self.assertEqual(first_call["actor_statuses"], ["running"])
        self.assertEqual(last_call["actor_statuses"], ["completed"])
        self.assertEqual(run_agent_kwargs[0].get("stream_output"), False)
        self.assertEqual(dynamic_status_calls[0]["kwargs"].get("enabled"), False)

    def test_solo_rich_snapshot_uses_single_agent_snow_actor(self):
        from planning.planner_schema import PlannedTask, PlannerResult
        from runtime.execution.pipeline import PipelineRunState
        from runtime.ui.rich_live import render_rich_live_snapshot

        task = PlannedTask(
            id="solo_agent",
            title="Solo request",
            instruction="answer",
            skill_id="solo",
            model="deepseek_v4_pro_model",
            mcp=[],
        )
        state = PipelineRunState.create(
            "answer",
            PlannerResult(route_type="single_agent", reason="solo", refined_request="answer", tasks=[task]),
            mode="solo",
        )
        state.model_labels = {"deepseek_v4_pro_model": "DeepSeek V4 Pro"}
        state.record_task_started(task)

        frame_0 = render_rich_live_snapshot(state, mode="solo", attempt=1, active="Answering request", frame_index=0)
        frame_3 = render_rich_live_snapshot(state, mode="solo", attempt=1, active="Answering request", frame_index=3)

        self.assertIn("\u2726 Agent  solo  DeepSeek V4 Pro", frame_0)
        self.assertIn("\u2737 Agent  solo  DeepSeek V4 Pro", frame_3)
        self.assertIn("Answering request", frame_0)
        self.assertNotIn("Plan", frame_0)
        self.assertNotIn("Waiting for plan", frame_0)
        self.assertNotIn("Supervisor", frame_0)
        self.assertNotIn("Worker", frame_0)
        self.assertNotEqual(frame_0, frame_3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
