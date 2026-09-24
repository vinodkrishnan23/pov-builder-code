"""LLM factory — OpenAI-compatible, works direct OR through Grove (MongoDB's
Azure APIM proxy). Matches the pattern used by chat-agent/spec-architect/
coding-orchestrator in this workspace's Magenta agents (kept consistent
even though this project has no Magenta dependency yet — see README
"Where Magenta fits in").
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from pov_builder.config.settings import Settings, load_settings


def build_llm(settings: Settings | None = None) -> ChatOpenAI:
    settings = settings or load_settings()

    kwargs: dict[str, object] = {"model": settings.openai_model, "temperature": 0}
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    if settings.openai_base_url:
        if "grove-foundry" in settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url.split("/v1")[0] + "/v1"
            if settings.openai_api_key:
                kwargs["default_headers"] = {"api-key": settings.openai_api_key}
        else:
            kwargs["base_url"] = settings.openai_base_url.rstrip("/")
    return ChatOpenAI(**kwargs)
