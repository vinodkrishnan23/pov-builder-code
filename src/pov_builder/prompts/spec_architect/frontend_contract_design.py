"""Stage 4 of 4 — frontend_contract_design.

Consumes ONLY api_contract_design's REAL api_contract (deliberately not the
data model directly — the frontend should never assume anything about the
database that isn't already exposed through the API) and designs the
frontend contract on top of it.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.models.pov_spec import InitialPOVSpec

SYSTEM_PROMPT = """You are designing the frontend contract for a POV, for MongoDB's \
POV Builder platform (new.md Phase 3, stage 4 of 4 — frontend_contract_design).

You are given the InitialPOVSpec and the ALREADY-DESIGNED API contract
(api_contract.json). It is authoritative — do NOT invent an endpoint the API
contract doesn't have. If a use case seems to need data the API doesn't
expose, say so rather than assuming a backend capability that wasn't designed.

## Design:
- Routes and pages, one per meaningful use case/user story.
- For each page: its purpose, what data it needs, which API endpoints it
  consumes, user actions available, loading states, error states.
- Components needed to build those pages.

## Also produce:
- `frontend_architecture`: a plain-language description of the frontend's
  overall structure, grounded in the actual pages/routes you just designed.
- `screen_definitions`: one entry per screen, human-readable.
- `user_interaction_flows`: step-by-step flows through the actual pages/APIs
  you just designed (not generic UX patterns).

## Output
`frontend_contract`: a real, structured JSON object (not prose) — routes,
pages, components, each page's purpose/data/APIs/actions/states.
"""


class FrontendContractDesignOutput(BaseModel):
    frontend_contract: dict[str, Any] = Field(default_factory=dict)
    frontend_architecture: str = ""
    screen_definitions: list[str] = Field(default_factory=list)
    user_interaction_flows: list[str] = Field(default_factory=list)


def build_messages(
    initial_spec: InitialPOVSpec,
    api_contract: dict[str, Any],
    feedback: list[str] | None = None,
) -> list[BaseMessage]:
    content = (
        f"InitialPOVSpec:\n{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}\n\n"
        f"API contract (api_contract.json, authoritative):\n{json.dumps(api_contract, indent=2)}"
    )
    if feedback:
        feedback_text = "\n".join(f"- {f}" for f in feedback)
        content += (
            "\n\nA human reviewer previously requested changes to an earlier technical "
            f"design for this SAME InitialPOVSpec (address ALL of it, oldest first):\n{feedback_text}"
        )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
