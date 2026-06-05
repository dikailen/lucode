from __future__ import annotations

BORROWABLE_SKILL_SOURCES = frozenset({"sample", "user", "workspace"})
RULE_ONLY_SKILLS = frozenset({"cli_command_safety"})
INTERNAL_SKILLS = frozenset(
    {
        "lucode_native_capability",
        "task_router",
        "query_refiner",
        "orchestrator_planner",
        "final_synthesizer",
        "full_worker_contract",
        "serial_executor_contract",
    }
)
PROTECTED_SYSTEM_SKILLS = INTERNAL_SKILLS
DEPRECATED_SKILLS = frozenset({"task_router"})

