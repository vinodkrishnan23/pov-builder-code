"""Stage 1 of 4 — schema_design.

Converts InitialPOVSpec's requirements/entities into a MongoDB data model.
Authoritative for every later stage — nothing downstream may redesign it,
only consume it.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.review import POVReviewResult
from pov_builder.models.technical_spec import IndexDefinition

SYSTEM_PROMPT = """You are designing the MongoDB data model for a POV, for MongoDB's \
POV Builder platform (new.md Phase 3, stage 1 of 4 — schema_design).

Given the InitialPOVSpec (and any POVReviewResult context), design the MongoDB \
collections needed to support it.

## For every collection, specify:
- Fields, with types, and whether each is required or optional.
- Embedded documents vs. references, and why.
- Relationships to other collections.
- Indexes: regular, Atlas Search, or Atlas Vector Search — including WHY \
each index exists, tied to an actual access pattern implied by the spec. \
Do not add a search or vector index unless the spec's use cases genuinely \
call for one.

## Also produce:
- `mongodb_atlas_capabilities`: which Atlas features are actually relevant \
here (plain queries, Atlas Search, Vector Search, Triggers, etc.) — don't \
list capabilities the POV doesn't need.
- `seed_data_spec`: a plain-language spec for what synthetic seed data this \
data model needs (roughly how many documents per collection, what realistic \
variety looks like) — this feeds a later seeder implementation, not code.

## Traceability
Every collection/field should trace back to something in the InitialPOVSpec \
— its business_entities, use_cases, or functional_requirements. Don't invent \
entities the spec gives no basis for.

## Output
`data_model`: a real, structured JSON object (not prose) — this is the \
canonical contract every later stage will build on. Downstream stages will \
NOT be allowed to redesign it, only consume it, so get the field names and \
shapes right here.
"""


class SchemaDesignOutput(BaseModel):
    data_model: dict[str, Any] = Field(default_factory=dict)
    index_definitions: list[IndexDefinition] = Field(default_factory=list)
    mongodb_atlas_capabilities: list[str] = Field(default_factory=list)
    seed_data_spec: str = ""


def build_messages(
    initial_spec: InitialPOVSpec,
    review_result: POVReviewResult | None,
    feedback: list[str] | None = None,
) -> list[BaseMessage]:
    content = f"InitialPOVSpec:\n{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}\n\n"
    if review_result is not None:
        content += (
            "POVReviewResult (context only — issues here should be reflected in "
            "later stages' `requirements`, not silently resolved by you):\n"
            f"{json.dumps(review_result.model_dump(mode='json'), indent=2)}\n\n"
        )
    if feedback:
        feedback_text = "\n".join(f"- {f}" for f in feedback)
        content += (
            "A human reviewer previously requested changes to an earlier technical "
            f"design for this SAME InitialPOVSpec (address ALL of it, oldest first):\n{feedback_text}"
        )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
