from __future__ import annotations

from runtime.agents.sdk import async_openai_class, openai_chat_completions_model_class


class OpenAICompatibleProvider:
    """Create OpenAI-compatible model objects using the existing Agents SDK wrapper."""

    sdk_type = "openai_compatible"

    def create_model(self, *, api_key: str, base_url: str, model_name: str):
        AsyncOpenAI = async_openai_class()
        OpenAIChatCompletionsModel = openai_chat_completions_model_class()
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return OpenAIChatCompletionsModel(model=model_name, openai_client=client)
