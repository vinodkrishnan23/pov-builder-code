"""Environment-based configuration, read once at process start.

Nothing here is consumed by Phase 0 (no node calls an LLM or GitHub yet) —
defined now so later phases don't need a config-shape change, matching
`.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    openai_base_url: str | None

    github_org: str | None
    github_username: str | None
    github_token: str | None
    github_repo: str

    mongodb_uri: str | None
    mongodb_db: str

    log_level: str


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-5.4"),
        openai_base_url=os.environ.get("OPENAI_BASE_URL"),
        github_org=os.environ.get("GITHUB_ORG"),
        github_username=os.environ.get("GITHUB_USERNAME"),
        github_token=os.environ.get("GITHUB_TOKEN"),
        github_repo=os.environ.get("GITHUB_REPO", "pov-builder"),
        mongodb_uri=os.environ.get("MONGODB_URI"),
        mongodb_db=os.environ.get("MONGODB_DB", "pov_builder"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
