"""ReadmeStatus — the How-To Helper's output (new.md Phase 10).

The helper inspects the actual repository and writes a README describing
what ACTUALLY EXISTS — never functionality that was specified but not
implemented. This model just tracks that it ran and where the result landed;
the README content itself lives in the repo, not in graph state.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReadmeStatus(BaseModel):
    committed: bool = False
    commit_sha: str | None = None
    path: str = "README.md"
    verified_commands: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
