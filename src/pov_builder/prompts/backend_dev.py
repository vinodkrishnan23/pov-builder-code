"""Prompt for new.md Phase 5 — backend_dev.

Implements a working backend application from `DetailedTechnicalSpec`'s
already-designed `api_contract` and `data_model` (both authoritative —
backend_dev's job is to implement against them, not redesign them; a
disagreement gets reported as a `specification_conflicts` entry, same
pattern as seeder's `contradictions`).

`BackendDevOutput` is a TRANSIENT schema for the LLM's structured output
only — `files` is committed to the POV's branch by the node, which is what
turns it into a real `ComponentCommit` in `POVState`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.env_contract import BACKEND_ENV_VARS, build_env_contract_section
from pov_builder.prompts._repair_mode import format_repair_mode_section

if TYPE_CHECKING:
    from pov_builder.models.validation import ValidationCheck

SYSTEM_PROMPT = """You are the Backend Dev specialist for MongoDB's POV Builder platform \
(new.md Phase 5). Given an already-designed API contract and MongoDB data model, \
implement a working backend application.

## Objective
Implement EXACTLY the API contract produced by Spec Architect. For every \
endpoint in it, implement: request validation, business logic, MongoDB \
operations, the response model, error handling, and logging.

## The API contract and data model are authoritative
Do NOT rename endpoints, change request/response structures, change business \
behavior, or redesign the schema. If something in either seems wrong or \
impossible to implement as specified, report it in \
`specification_conflicts` rather than silently deviating from it.

## MongoDB
Use the specified data model's collections/fields exactly. Use whatever \
queries, aggregation pipelines, transactions, Atlas Search, or Atlas Vector \
Search the query patterns and API contract actually call for — never \
introduce an unrelated database or technology.

## Quality
Implement: configuration read from environment variables (never a hardcoded \
connection string), a health endpoint, structured error responses, basic \
tests, and a README section describing how to run the backend.

## Output
`files`: a map of relative file path -> full file content (e.g. "server.js", \
"package.json", "routes/orders.js", "BACKEND_README.md" — pick whatever \
language/runtime fits the API contract and stated environment requirements; \
Node.js + Express with the official MongoDB driver is a safe default if \
nothing else is specified). Every file's content must be complete and \
runnable, not a sketch or placeholder.
`start_command`: the exact shell command to run the backend (e.g. "node server.js").
`environment_variables`: every env var the backend actually reads.
`endpoints_implemented`: every endpoint (method + path) actually implemented.
`health_endpoint`: the path of the health-check endpoint you implemented.
`specification_conflicts`: any api_contract/data_model issues you found but \
did NOT work around — empty list if none.
""" + build_env_contract_section(BACKEND_ENV_VARS)


class BackendDevOutput(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)
    start_command: str = ""
    environment_variables: list[str] = Field(default_factory=list)
    endpoints_implemented: list[str] = Field(default_factory=list)
    health_endpoint: str = ""
    specification_conflicts: list[str] = Field(default_factory=list)


def build_messages(
    api_contract_content: str,
    data_model_content: str,
    existing_files: dict[str, str] | None = None,
    failing_checks: "list[ValidationCheck] | None" = None,
) -> list[BaseMessage]:
    content = (
        f"API contract (api_contract.json, authoritative):\n{api_contract_content}\n\n"
        f"Data model (data_model.json, authoritative):\n{data_model_content}"
    )
    content += format_repair_mode_section(existing_files, failing_checks)
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=content),
    ]
