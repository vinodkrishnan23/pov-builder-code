"""Stage 2 of 4 — query_pattern_design.

Consumes schema_design's REAL data_model (not re-derived) and designs the
query patterns the API will need — the bridge between "what the data looks
like" and "what the API actually does".
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.models.pov_spec import InitialPOVSpec

SYSTEM_PROMPT = """You are designing query patterns for a POV, for MongoDB's POV \
Builder platform (new.md Phase 3, stage 2 of 4 — query_pattern_design).

You are given the InitialPOVSpec and the ALREADY-DESIGNED data model
(data_model.json). The data model is authoritative — do NOT redesign it. If a
use case genuinely cannot be served by the given data model, report that in
`contradictions` rather than silently changing the schema.

## For every meaningful access pattern implied by the spec's use cases/user
stories, define a query pattern with:
- An id and a name.
- Which user story/use case it serves.
- Which collection(s) it touches.
- An aggregation/query sketch (a real MongoDB query or aggregation pipeline
  shape — stages, filters, projections — not prose).
- What it's for, in plain language.

## Traceability
Every query pattern should trace back to a specific use case or user story
in the InitialPOVSpec. Don't invent query patterns nothing in the spec asks for.

## Output
`query_patterns`: a real, structured JSON object (not prose) — this becomes
its own committed contract (query_patterns.json) that api_contract_design
will build the actual API endpoints on top of.
"""


class QueryPatternDesignOutput(BaseModel):
    query_patterns: dict[str, Any] = Field(default_factory=dict)
    contradictions: list[str] = Field(default_factory=list)


def build_messages(
    initial_spec: InitialPOVSpec,
    data_model: dict[str, Any],
    feedback: list[str] | None = None,
) -> list[BaseMessage]:
    content = (
        f"InitialPOVSpec:\n{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}\n\n"
        f"Data model (data_model.json, authoritative):\n{json.dumps(data_model, indent=2)}"
    )
    if feedback:
        feedback_text = "\n".join(f"- {f}" for f in feedback)
        content += (
            "\n\nA human reviewer previously requested changes to an earlier technical "
            f"design for this SAME InitialPOVSpec (address ALL of it, oldest first):\n{feedback_text}"
        )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
