"""Stage 3 of 4 — api_contract_design.

Consumes schema_design's data_model AND query_pattern_design's
query_patterns (both real, not re-derived) and designs the actual API
contract on top of them.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.models.pov_spec import InitialPOVSpec

SYSTEM_PROMPT = """You are designing the API contract for a POV, for MongoDB's POV \
Builder platform (new.md Phase 3, stage 3 of 4 — api_contract_design).

You are given the InitialPOVSpec, the ALREADY-DESIGNED data model
(data_model.json), and the ALREADY-DESIGNED query patterns (query_patterns.json).
Both are authoritative — do NOT redesign them. Every endpoint should be
backed by an actual query pattern or a straightforward CRUD operation
implied by the data model; if something doesn't fit, say so rather than
inventing a new query pattern yourself.

## For every API endpoint, specify:
- Method and path.
- Purpose, in plain language.
- Request shape.
- Response shape.
- Error cases.
- Which MongoDB operation (or named query pattern) backs it.

## Also produce:
- `backend_architecture`: a plain-language description of the backend's
  overall structure (modules/layers) needed to implement this contract —
  grounded in the actual endpoints you just designed, not generic boilerplate.

## Output
`api_contract`: a real, structured JSON object (not prose) — this becomes
its own committed contract (api_contract.json) that frontend_contract_design
will build the frontend on top of. It must be machine-readable.
"""


class ApiContractDesignOutput(BaseModel):
    api_contract: dict[str, Any] = Field(default_factory=dict)
    backend_architecture: str = ""


def build_messages(
    initial_spec: InitialPOVSpec,
    data_model: dict[str, Any],
    query_patterns: dict[str, Any],
    feedback: list[str] | None = None,
) -> list[BaseMessage]:
    content = (
        f"InitialPOVSpec:\n{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}\n\n"
        f"Data model (data_model.json, authoritative):\n{json.dumps(data_model, indent=2)}\n\n"
        f"Query patterns (query_patterns.json, authoritative):\n{json.dumps(query_patterns, indent=2)}"
    )
    if feedback:
        feedback_text = "\n".join(f"- {f}" for f in feedback)
        content += (
            "\n\nA human reviewer previously requested changes to an earlier technical "
            f"design for this SAME InitialPOVSpec (address ALL of it, oldest first):\n{feedback_text}"
        )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
