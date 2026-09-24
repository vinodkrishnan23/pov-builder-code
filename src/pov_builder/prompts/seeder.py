"""Prompt for new.md Phase 4 — seeder.

Generates a working synthetic-data seed application from
`DetailedTechnicalSpec.data_model` and `.seed_data_spec`. The data model is
authoritative — the Spec Architect already decided it; seeder's job is to
implement against it, not redesign it.

`SeederOutput` is a TRANSIENT schema for the LLM's structured output only
(same reasoning as `SpecArchitectOutput` in prompts/spec_architect.py) —
`files` is committed to the per-POV repo by the node, which is what turns
it into a real `ComponentCommit` in `POVState`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.env_contract import SEEDER_ENV_VARS, build_env_contract_section
from pov_builder.prompts._repair_mode import format_repair_mode_section

if TYPE_CHECKING:
    from pov_builder.models.validation import ValidationCheck

SYSTEM_PROMPT = """You are the Seeder specialist for MongoDB's POV Builder platform \
(new.md Phase 4). Given a MongoDB data model and a seed-data specification, \
generate a working seed application that populates MongoDB Atlas with realistic \
synthetic data.

## Objective — the seed application must:
1. Connect to MongoDB Atlas using a connection string from an environment variable.
2. Create/populate every collection named in the data model.
3. Generate realistic synthetic data — never real customer PII.
4. Respect the data model exactly: field names, types, required/optional, \
embedded documents, references, relationships.
5. Create every index the data model specifies (regular, search, vector).
6. Be safe to run repeatedly (idempotent — drop/recreate its own collections, \
or upsert, rather than accumulating duplicate data on a second run).
7. Read all configuration (connection string, database name, dataset size) \
from environment variables — never hardcode a connection string.

## The data model is authoritative
Do NOT redesign it. If it contains an obvious contradiction or an impossible \
requirement, report that in `contradictions` rather than silently changing \
the design or working around it.

## Synthetic data
Never generate real customer PII. Use deterministic/seeded randomness where \
practical so re-running produces the same dataset (reproducibility for demos). \
Make the dataset size configurable via an environment variable.

## Output
`files`: a map of relative file path -> full file content (e.g. "seed.js", \
"package.json", "SEED_README.md" — use whatever language/runtime fits the \
data model and stated environment requirements; Node.js with the official \
MongoDB driver is a safe default if nothing else is specified). Every file's \
content must be complete and runnable, not a sketch or placeholder.
`seed_command`: the exact shell command to run the seed (e.g. "node seed.js").
`environment_variables`: every env var the seed script actually reads.
`collections_created`: every collection name the seed script populates.
`indexes_created`: every index name/description the seed script creates.
`contradictions`: any data-model issues you found but did NOT work around — \
empty list if none.
""" + build_env_contract_section(SEEDER_ENV_VARS)


class SeederOutput(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)
    seed_command: str = ""
    environment_variables: list[str] = Field(default_factory=list)
    collections_created: list[str] = Field(default_factory=list)
    indexes_created: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


def build_messages(
    data_model_content: str,
    seed_data_spec: str,
    existing_files: dict[str, str] | None = None,
    failing_checks: "list[ValidationCheck] | None" = None,
) -> list[BaseMessage]:
    content = (
        f"Data model (data_model.json, authoritative):\n{data_model_content}\n\n"
        f"Seed-data specification:\n{seed_data_spec}"
    )
    content += format_repair_mode_section(existing_files, failing_checks)
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=content),
    ]
