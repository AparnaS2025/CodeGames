from __future__ import annotations

from typing import Any

from app.config import Settings


def _is_placeholder(value: str) -> bool:
    cleaned = value.strip()
    return not cleaned or (cleaned.startswith("<") and cleaned.endswith(">"))


def build_agent_llm(settings: Settings) -> Any | None:
    if not settings.agent_enable_llm:
        return None
    required_values = (
        settings.azure_openai_endpoint,
        settings.azure_openai_api_key,
        settings.azure_openai_deployment,
        settings.azure_openai_api_version,
    )
    if any(_is_placeholder(value) for value in required_values):
        return None

    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
        azure_deployment=settings.azure_openai_deployment,
        api_version=settings.azure_openai_api_version,
        temperature=0,
        timeout=30,
        max_retries=1,
    )
