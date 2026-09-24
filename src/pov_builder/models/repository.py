"""RepositoryInfo — tracks the POV's real GitHub repo, per new.md's "source
code belongs in the Git repository" instruction. Every implementation
node (seeder/backend_dev/frontend_dev) records one `ComponentCommit` after
writing and pushing its component; nothing here holds file contents.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ComponentCommit(BaseModel):
    component: str = Field(description='"seed" | "backend" | "frontend" | "readme"')
    commit_sha: str
    files_changed: list[str] = Field(default_factory=list)
    message: str = ""
    committed_at: datetime | None = None
    # Phase-specific structured metadata new.md asks each implementation
    # phase to return (seeder: seed_command/collections_created/
    # indexes_created/contradictions; backend_dev/frontend_dev will add
    # their own keys later) — kept generic rather than adding one-off
    # fields to this shared model per component type.
    details: dict[str, Any] = Field(default_factory=dict)


class RepositoryInfo(BaseModel):
    repo_url: str | None = None
    # Bare repo name (e.g. "pov-order-status-chatbot"), needed by
    # GitHubRepoStore.commit_code_files — provisioned ONCE by spec_architect
    # (sequential, before the seeder/backend_dev/frontend_dev fan-out) to
    # avoid three parallel nodes racing to create the same repo.
    repo_name: str | None = None
    default_branch: str = "main"
    # The POV's OWN branch (e.g. "vinod-example-com/order-bot") — distinct
    # from `default_branch`, which is the shared repo's real default
    # branch, used only as the base ref a new POV branch is created from.
    branch: str | None = None
    commits: list[ComponentCommit] = Field(default_factory=list)
