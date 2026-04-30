import json
import os
import re
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from agents import AsyncOpenAI, OpenAIChatCompletionsModel


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


KNOWN_MODEL_DEFINITIONS = [
    {
        "id": "deepseek_V4_flash_model",
        "display_name_zh": "DeepSeek V4 Flash",
        "provider": "deepseek",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "model_env": "DEEPSEEK_MODEL",
        "strengths": ["中文解释", "项目分析", "文本处理", "低成本常规任务"],
        "best_for_skills": ["project_explorer", "humanizer_zh"],
        "cost_level": "low",
        "reasoning_level": "medium",
    },
    {
        "id": "deepseek_V4_pro_model",
        "display_name_zh": "DeepSeek V4 Pro",
        "provider": "deepseek",
        "api_key_env": "DEEPSEEK_pro_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_pro_URL",
        "model_env": "DEEPSEEK_pro_MODEL",
        "strengths": ["复杂规划", "Skill 创建", "多任务拆分", "汇总判断"],
        "best_for_skills": ["skill_creator", "orchestrator_planner", "final_synthesizer"],
        "cost_level": "high",
        "reasoning_level": "high",
    },
    {
        "id": "mimo_model",
        "display_name_zh": "MiMo v2.5 Pro",
        "provider": "mimo",
        "api_key_env": "MIMO_API_KEY",
        "base_url_env": "MIMO_API_BASE_URL",
        "model_env": "MIMO_API_MODEL",
        "strengths": ["代码生成", "代码解释", "代码评审", "排查报错"],
        "best_for_skills": ["jpc_now_skill"],
        "cost_level": "medium",
        "reasoning_level": "medium",
    },
]


def _normalize_model_id(raw_id: str) -> str:
    value = raw_id.strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value.endswith("_model"):
        value += "_model"
    return value


def _env_keys() -> set[str]:
    keys = set(os.environ)
    env_file = dotenv_values(BASE_DIR / ".env")
    keys.update(env_file.keys())
    return {key for key in keys if key}


def discover_model_definitions() -> list[dict]:
    """Discover known models plus generic MODEL_<ID>_* entries from .env."""

    definitions = [dict(item) for item in KNOWN_MODEL_DEFINITIONS]
    known_ids = {item["id"] for item in definitions}

    groups = set()
    for key in _env_keys():
        match = re.match(r"MODEL_([A-Z0-9_]+)_(API_KEY|BASE_URL|MODEL|NAME)$", key)
        if match:
            groups.add(match.group(1))

    for group in sorted(groups):
        model_env = f"MODEL_{group}_MODEL"
        if model_env not in _env_keys():
            model_env = f"MODEL_{group}_NAME"

        model_id = _normalize_model_id(group)
        if model_id in known_ids:
            continue

        display_name = os.getenv(f"MODEL_{group}_DISPLAY_NAME") or group.replace("_", " ").title()
        provider = os.getenv(f"MODEL_{group}_PROVIDER") or group.lower()
        strengths = _split_env_list(os.getenv(f"MODEL_{group}_STRENGTHS")) or ["通用任务"]
        best_for_skills = _split_env_list(os.getenv(f"MODEL_{group}_BEST_FOR_SKILLS"))

        definitions.append(
            {
                "id": model_id,
                "display_name_zh": display_name,
                "provider": provider,
                "api_key_env": f"MODEL_{group}_API_KEY",
                "base_url_env": f"MODEL_{group}_BASE_URL",
                "model_env": model_env,
                "strengths": strengths,
                "best_for_skills": best_for_skills,
                "cost_level": os.getenv(f"MODEL_{group}_COST_LEVEL") or "medium",
                "reasoning_level": os.getenv(f"MODEL_{group}_REASONING_LEVEL") or "medium",
            }
        )
        known_ids.add(model_id)

    return definitions


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_model_catalog() -> dict:
    """Build a model catalog from models actually configured in .env/environment."""

    models = []
    for item in discover_model_definitions():
        api_key = os.getenv(item["api_key_env"])
        base_url = os.getenv(item["base_url_env"])
        model_name = os.getenv(item["model_env"])

        models.append(
            {
                "id": item["id"],
                "display_name_zh": item["display_name_zh"],
                "provider": item["provider"],
                "configured": bool(api_key and base_url and model_name),
                "base_url_configured": bool(base_url),
                "model_name": model_name or "",
                "strengths": item["strengths"],
                "best_for_skills": item["best_for_skills"],
                "cost_level": item["cost_level"],
                "reasoning_level": item["reasoning_level"],
                "source": "env",
            }
        )

    return {
        "version": 1,
        "selection_rules": [
            "只能选择 configured=true 的模型。",
            "代码任务优先 mimo_model。",
            "复杂规划、Skill 创建、多 Agent 汇总优先 deepseek_V4_pro_model。",
            "中文解释、项目探索、文本润色优先 deepseek_V4_flash_model。",
            "如果首选模型未配置，选择同类能力中 configured=true 的替代模型。",
        ],
        "models": models,
    }


def compact_model_catalog_for_prompt() -> str:
    catalog = load_model_catalog()
    lines = ["模型图书馆（只能选择 configured=true）："]
    for item in catalog.get("models", []):
        lines.append(
            "- "
            f"{item['id']} | "
            f"configured:{item.get('configured')} | "
            f"name:{item.get('model_name') or '未配置'} | "
            f"能力:{','.join(item.get('strengths') or [])} | "
            f"适合:{','.join(item.get('best_for_skills') or []) or '通用'} | "
            f"成本:{item.get('cost_level')} | "
            f"推理:{item.get('reasoning_level')}"
        )
    return "\n".join(lines)


class ModelRegistry:
    """Create model objects by id using the current .env configuration."""

    def __init__(self):
        self.definitions = {item["id"]: item for item in discover_model_definitions()}

    def get_model(self, model_id: str) -> OpenAIChatCompletionsModel:
        if model_id not in self.definitions:
            raise KeyError(f"Unknown model id: {model_id}")

        item = self.definitions[model_id]
        api_key = os.getenv(item["api_key_env"])
        base_url = os.getenv(item["base_url_env"])
        model_name = os.getenv(item["model_env"])

        if not api_key or not base_url or not model_name:
            raise ValueError(f"Model is not fully configured: {model_id}")

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

        return OpenAIChatCompletionsModel(
            model=model_name,
            openai_client=client,
        )

    def first_configured(self, preferred: list[str]) -> str:
        catalog = load_model_catalog()
        configured = {item["id"] for item in catalog["models"] if item["configured"]}
        for model_id in preferred:
            if model_id in configured:
                return model_id
        if configured:
            return sorted(configured)[0]
        raise ValueError("No configured models found in .env")
