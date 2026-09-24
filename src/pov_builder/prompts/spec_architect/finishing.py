"""Stage 5 (finishing) — synthesizes the fields that need the COMPLETE
picture: system_architecture, ai_agent_workflows, requirements.json,
environment_requirements, integration_requirements.

Not one of the 4 named design stages — this is the only point that sees
all four real outputs (data_model, query_patterns, api_contract,
frontend_contract) at once, so it's the only stage that can ground these
fields in what was actually designed rather than guessing at the whole
picture from a fragment of it.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.models.pov_spec import InitialPOVSpec

SYSTEM_PROMPT = """You are writing the final synthesis for a POV's technical design, \
for MongoDB's POV Builder platform (new.md Phase 3, finishing stage).

You are given the InitialPOVSpec and all four ALREADY-DESIGNED artifacts:
the data model, query patterns, API contract, and frontend contract. All
four are authoritative — do NOT redesign any of them. Your job is to
synthesize a few things that need the complete picture, not add new design
decisions.

## Produce:
- `system_architecture`: a plain-language description tying the frontend,
  backend, data model, and any AI/agent behavior together into one coherent
  narrative — grounded in the four real artifacts, not a generic description.
- `ai_agent_workflows`: for any AI-driven workflow implied by the spec,
  describe user intent -> agent -> tools -> MongoDB/external systems ->
  result -> user response, using the ACTUAL endpoints/collections already
  designed.
- `requirements`: a real, structured JSON object consolidating requirement
  traceability across all four artifacts — which InitialPOVSpec requirement
  maps to which collection/query pattern/endpoint/page. This is what
  downstream agents (Seeder, Backend Dev, Frontend Dev) will read for the
  full picture.
- `environment_requirements`: what needs to exist to run this (Atlas
  cluster, env vars, etc.) — grounded in what the four artifacts actually need.
- `integration_requirements`: any external system integrations implied.

## Output
`requirements` must be a real JSON object, not prose.
"""


class FinishingOutput(BaseModel):
    system_architecture: str = ""
    ai_agent_workflows: list[str] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    environment_requirements: list[str] = Field(default_factory=list)
    integration_requirements: list[str] = Field(default_factory=list)


def build_messages(
    initial_spec: InitialPOVSpec,
    data_model: dict[str, Any],
    query_patterns: dict[str, Any],
    api_contract: dict[str, Any],
    frontend_contract: dict[str, Any],
    feedback: list[str] | None = None,
) -> list[BaseMessage]:
    content = (
        f"InitialPOVSpec:\n{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}\n\n"
        f"Data model:\n{json.dumps(data_model, indent=2)}\n\n"
        f"Query patterns:\n{json.dumps(query_patterns, indent=2)}\n\n"
        f"API contract:\n{json.dumps(api_contract, indent=2)}\n\n"
        f"Frontend contract:\n{json.dumps(frontend_contract, indent=2)}"
    )
    if feedback:
        feedback_text = "\n".join(f"- {f}" for f in feedback)
        content += (
            "\n\nA human reviewer previously requested changes to an earlier technical "
            f"design for this SAME InitialPOVSpec (address ALL of it, oldest first):\n{feedback_text}"
        )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
