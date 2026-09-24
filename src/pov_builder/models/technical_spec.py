"""DetailedTechnicalSpec — the Spec Architect's output (new.md Phase 3).

Converts "WHAT are we building?" into "HOW will we build it?". This is the
first point in the pipeline allowed to make technical implementation
decisions.

new.md calls for `data_model.json`, `api_contract.json`,
`frontend_contract.json`, and `requirements.json` as "the canonical source
of truth for downstream agents" — these are real, potentially large
documents. Per the Phase 0 state-design principle ("do not put generated
source code into LangGraph state... state should contain references,
metadata, status"), `DetailedTechnicalSpec` holds each contract as a
`ContractRef` (repo-relative path + commit SHA + a short structural
summary) rather than embedding the full JSON — the repo is the source of
truth, the graph state is a pointer to it.

`query_patterns` is a workspace-specific 5th contract, not named in
new.md's own text — spec_architect is implemented as 4 sequential design
stages (schema -> query patterns -> API -> frontend, each consuming the
PREVIOUS stage's real output, not inventing all four at once) plus a
finishing synthesis step; query pattern design needed its own committed
artifact once it became a real stage in its own right. See
prompts/spec_architect/ and graph/nodes.py's make_spec_architect.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContractRef(BaseModel):
    """A pointer to one canonical contract file committed to the POV repo,
    plus a small summary so a node can sanity-check it without re-reading
    the whole file."""

    path: str
    commit_sha: str | None = None
    summary: str = ""


class IndexDefinition(BaseModel):
    name: str
    kind: str = Field(description='e.g. "regular", "search", "vector"')
    fields: list[str] = Field(default_factory=list)
    reason: str = ""


class DetailedTechnicalSpec(BaseModel):
    system_architecture: str = ""
    frontend_architecture: str = ""
    backend_architecture: str = ""

    data_model: ContractRef | None = None
    query_patterns: ContractRef | None = None
    api_contract: ContractRef | None = None
    frontend_contract: ContractRef | None = None
    requirements: ContractRef | None = None

    screen_definitions: list[str] = Field(default_factory=list)
    user_interaction_flows: list[str] = Field(default_factory=list)
    ai_agent_workflows: list[str] = Field(default_factory=list)

    mongodb_atlas_capabilities: list[str] = Field(default_factory=list)
    index_definitions: list[IndexDefinition] = Field(default_factory=list)
    seed_data_spec: str = ""

    environment_requirements: list[str] = Field(default_factory=list)
    integration_requirements: list[str] = Field(default_factory=list)
