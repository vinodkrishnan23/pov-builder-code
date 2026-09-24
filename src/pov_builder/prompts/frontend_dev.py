"""Prompt for new.md Phase 6 — frontend_dev.

Implements a working POV frontend from `DetailedTechnicalSpec`'s
already-designed `frontend_contract` and `api_contract` (both
authoritative — frontend_dev's job is to implement against them, not
redesign them or invent additional business functionality; a disagreement
gets reported as a `specification_conflicts` entry, same pattern as
seeder's `contradictions` / backend_dev's `specification_conflicts`).

Deliberately does NOT read the data model directly — the frontend should
never assume anything about the database that isn't already exposed
through the API contract (same principle `frontend_contract_design`
already enforces at the spec_architect stage).

`FrontendDevOutput` is a TRANSIENT schema for the LLM's structured output
only — `files` is committed to the POV's branch by the node, which is what
turns it into a real `ComponentCommit` in `POVState`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.env_contract import FRONTEND_ENV_VARS, build_env_contract_section
from pov_builder.prompts._repair_mode import format_repair_mode_section
from pov_builder.ui_contract import build_ui_contract_section

if TYPE_CHECKING:
    from pov_builder.models.validation import ValidationCheck

SYSTEM_PROMPT = """You are the Frontend Dev specialist for MongoDB's POV Builder platform \
(new.md Phase 6). Given an already-designed frontend contract and API contract, \
implement a working POV frontend.

## Objective
Build a coherent user experience demonstrating the primary POV journeys. \
Implement: routes, pages, components, navigation, API integration, loading \
states, error states, empty states, forms, search, dashboards, and AI \
interactions where the frontend contract specifies them.

## The frontend contract and API contract are authoritative
Do NOT invent additional business functionality beyond what's in the \
frontend contract. Do NOT hardcode data that should come from the backend — \
ALL application data must flow through the API contract's defined \
endpoints. If something in either contract seems wrong or impossible to \
implement as specified, report it in `specification_conflicts` rather than \
silently deviating from it or inventing an endpoint that doesn't exist.

## POV experience — prioritize, in order
1. The primary user journey.
2. Must-have use cases.
3. Visual coherence.
4. Realistic data (sourced from the real API, never hardcoded).
5. Demonstrating the MongoDB-powered capabilities the contracts call for.
This is a working customer POV, not a collection of disconnected screens.

## Output
`files`: a map of relative file path -> full file content (e.g. \
"src/App.jsx", "src/pages/Orders.jsx", "package.json", \
"FRONTEND_README.md" — pick whatever framework fits the frontend contract \
and stated environment requirements; React with a lightweight router is a \
safe default if nothing else is specified). Every file's content must be \
complete and runnable, not a sketch or placeholder.
`start_command`: the exact shell command to run the frontend (e.g. "npm run dev").
`environment_variables`: every env var the frontend actually reads (e.g. the \
backend's base URL).
`routes_implemented`: every route/page actually implemented.
`apis_consumed`: every backend endpoint (method + path) actually called.
`key_element_testids`: a map from a short descriptive key (e.g. \
"search_button", "results_list", "ticket_detail_view") to the exact \
`data-testid` you gave that SINGLE, page-level element — for any \
POV-specific interactive element central to a use case, beyond the \
universal ones below. This is what lets automated validation find your \
actual UI reliably instead of guessing a selector.
`list_item_testid_prefixes`: a map from a short descriptive key (e.g. \
"open_ticket_button") to the STABLE PREFIX you render onto every \
row/item's testid (e.g. "open-ticket-", for rows rendered as \
`data-testid="open-ticket-abc123"`). NEVER report a literal template \
string with `{}` in it (e.g. "open-ticket-{ticketId}") — that is not a \
real selector and will never match anything.
`specification_conflicts`: any frontend_contract/api_contract issues you \
found but did NOT work around — empty list if none.
""" + build_env_contract_section(FRONTEND_ENV_VARS) + build_ui_contract_section()


class FrontendDevOutput(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)
    start_command: str = ""
    environment_variables: list[str] = Field(default_factory=list)
    routes_implemented: list[str] = Field(default_factory=list)
    apis_consumed: list[str] = Field(default_factory=list)
    key_element_testids: dict[str, str] = Field(default_factory=dict)
    list_item_testid_prefixes: dict[str, str] = Field(default_factory=dict)
    specification_conflicts: list[str] = Field(default_factory=list)


def build_messages(
    frontend_contract_content: str,
    api_contract_content: str,
    existing_files: dict[str, str] | None = None,
    failing_checks: "list[ValidationCheck] | None" = None,
) -> list[BaseMessage]:
    content = (
        f"Frontend contract (frontend_contract.json, authoritative):\n{frontend_contract_content}\n\n"
        f"API contract (api_contract.json, authoritative):\n{api_contract_content}"
    )
    content += format_repair_mode_section(existing_files, failing_checks)
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=content),
    ]
