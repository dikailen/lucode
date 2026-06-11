from __future__ import annotations

from dataclasses import dataclass, field

import runtime.execution.dynamic as dynamic
from lucode.gui.control_panel import (
    worker_pool_available_for_mode,
)
from planning.planner_schema import PlannedTask, PlannerResult
from runtime.config.settings import RuntimeSettings


@dataclass
class _FakeSettings:
    executor_model_priority: list[str] = field(default_factory=list)
    allowed_worker_models: list[str] = field(default_factory=list)
    privacy_mode: str = "local_first"
    _executor_default: str = "exec_default"

    def worker_model_pool(self, model_registry=None) -> list[str]:
        return [m for m in self.allowed_worker_models if m]

    def select_model_id(self, model_registry, role: str) -> str:
        return self._executor_default


def _plan(*models: str) -> PlannerResult:
    tasks = [
        PlannedTask(id=f"t{i}", title=f"任务{i}", instruction="", skill_id="code_engineer", model=m)
        for i, m in enumerate(models)
    ]
    return PlannerResult(route_type="multi_agent", reason="", refined_request="", tasks=tasks)


def _all_usable(monkeypatch, usable: set[str] | None = None) -> None:
    def fake(model_registry, model_id, *, privacy_mode="local_first", requires_tools=False):
        if usable is None:
            return bool(model_id)
        return model_id in usable

    monkeypatch.setattr(dynamic, "_task_model_is_usable", fake)


# ---- settings field ----

def test_worker_model_pool_filters_blanks():
    settings = RuntimeSettings(allowed_worker_models=["a", "", "  ", "b"])
    assert settings.worker_model_pool() == ["a", "b"]


def test_worker_model_pool_empty_by_default():
    assert RuntimeSettings().worker_model_pool() == []


# ---- round-robin backfill ----

def test_empty_models_round_robin_over_pool(monkeypatch):
    _all_usable(monkeypatch)
    plan = _plan("", "", "")
    settings = _FakeSettings(allowed_worker_models=["m1", "m2"])
    dynamic._apply_executor_model_defaults(plan, settings, model_registry=None)
    assert [t.model for t in plan.tasks] == ["m1", "m2", "m1"]


def test_out_of_pool_model_is_replaced(monkeypatch):
    _all_usable(monkeypatch)
    plan = _plan("rogue", "m2")
    settings = _FakeSettings(allowed_worker_models=["m1", "m2"])
    dynamic._apply_executor_model_defaults(plan, settings, model_registry=None)
    # rogue not in pool -> backfilled; m2 already in pool and usable -> kept
    assert plan.tasks[0].model == "m1"
    assert plan.tasks[1].model == "m2"


def test_no_pool_falls_back_to_executor_priority(monkeypatch):
    _all_usable(monkeypatch)
    plan = _plan("", "")
    settings = _FakeSettings(executor_model_priority=["pa", "pb"])
    dynamic._apply_executor_model_defaults(plan, settings, model_registry=None)
    assert [t.model for t in plan.tasks] == ["pa", "pb"]


def test_valid_in_pool_model_is_left_alone(monkeypatch):
    _all_usable(monkeypatch)
    plan = _plan("m2")
    settings = _FakeSettings(allowed_worker_models=["m1", "m2"])
    dynamic._apply_executor_model_defaults(plan, settings, model_registry=None)
    assert plan.tasks[0].model == "m2"


def test_unusable_candidates_skipped(monkeypatch):
    _all_usable(monkeypatch, usable={"m2"})
    plan = _plan("", "")
    settings = _FakeSettings(allowed_worker_models=["m1", "m2"])
    dynamic._apply_executor_model_defaults(plan, settings, model_registry=None)
    # m1 unusable -> both tasks land on m2
    assert [t.model for t in plan.tasks] == ["m2", "m2"]


def test_no_candidates_leaves_models_untouched(monkeypatch):
    _all_usable(monkeypatch)
    plan = _plan("", "")
    settings = _FakeSettings(_executor_default="")
    dynamic._apply_executor_model_defaults(plan, settings, model_registry=None)
    assert [t.model for t in plan.tasks] == ["", ""]


# ---- GUI helper ----

def test_worker_pool_available_only_in_full():
    assert worker_pool_available_for_mode("full") is True
    assert worker_pool_available_for_mode("serial") is False
    assert worker_pool_available_for_mode("solo") is False
