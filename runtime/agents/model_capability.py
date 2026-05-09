from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum


class ModelTier(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


@dataclass(frozen=True)
class ModelExecutionStrategy:
    """Deterministic execution limits derived from a model's practical capacity."""

    tier: ModelTier
    max_files_per_task: int
    max_read_chars_per_file: int
    max_total_read_chars: int
    max_parallel_tasks: int
    force_plan_before_edit: bool
    note_zh: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tier"] = self.tier.value
        return data


SMALL_STRATEGY = ModelExecutionStrategy(
    tier=ModelTier.SMALL,
    max_files_per_task=2,
    max_read_chars_per_file=4000,
    max_total_read_chars=12000,
    max_parallel_tasks=1,
    force_plan_before_edit=True,
    note_zh="小模型策略：强制先定位和计划，只读取少量核心文件，避免一次性塞入过多上下文。",
)
MEDIUM_STRATEGY = ModelExecutionStrategy(
    tier=ModelTier.MEDIUM,
    max_files_per_task=5,
    max_read_chars_per_file=7000,
    max_total_read_chars=28000,
    max_parallel_tasks=2,
    force_plan_before_edit=True,
    note_zh="中模型策略：保持先定位后读取，可处理中等范围的代码任务。",
)
LARGE_STRATEGY = ModelExecutionStrategy(
    tier=ModelTier.LARGE,
    max_files_per_task=8,
    max_read_chars_per_file=10000,
    max_total_read_chars=50000,
    max_parallel_tasks=4,
    force_plan_before_edit=False,
    note_zh="大模型策略：允许更宽的上下文窗口，但仍优先使用定位器缩小范围。",
)


def detect_model_tier(model_name: str = "", reasoning_level: str = "", cost_level: str = "") -> ModelTier:
    """Infer a conservative capability tier from common model names and metadata."""

    text = f"{model_name} {reasoning_level} {cost_level}".lower()
    size_b = _extract_largest_billion_size(text)
    if size_b is not None:
        if size_b <= 10:
            return ModelTier.SMALL
        if size_b <= 34:
            return ModelTier.MEDIUM
        return ModelTier.LARGE

    if any(marker in text for marker in ["mini", "lite", "flash", "small", "tiny"]):
        return ModelTier.SMALL
    if any(marker in text for marker in ["pro", "plus", "medium", "reasoner", "high"]):
        return ModelTier.LARGE if "high" in text or "reasoner" in text else ModelTier.MEDIUM
    if "low" in text:
        return ModelTier.SMALL
    return ModelTier.MEDIUM


def strategy_for_model_name(model_name: str = "", reasoning_level: str = "", cost_level: str = "") -> ModelExecutionStrategy:
    tier = detect_model_tier(model_name, reasoning_level, cost_level)
    return strategy_for_tier(tier)


def strategy_for_model_info(model_info: dict | None) -> ModelExecutionStrategy:
    info = model_info or {}
    return strategy_for_model_name(
        str(info.get("model_name") or info.get("display_name_zh") or info.get("id") or ""),
        str(info.get("reasoning_level") or ""),
        str(info.get("cost_level") or ""),
    )


def strategy_for_tier(tier: ModelTier | str) -> ModelExecutionStrategy:
    normalized = tier if isinstance(tier, ModelTier) else ModelTier(str(tier))
    if normalized == ModelTier.SMALL:
        return SMALL_STRATEGY
    if normalized == ModelTier.LARGE:
        return LARGE_STRATEGY
    return MEDIUM_STRATEGY


def _extract_largest_billion_size(text: str) -> float | None:
    sizes = []
    for match in re.finditer(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", text):
        try:
            sizes.append(float(match.group(1)))
        except ValueError:
            continue
    return max(sizes) if sizes else None
