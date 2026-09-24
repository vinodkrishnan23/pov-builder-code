"""Prompts for new.md Phase 7 — integration_validator's two LLM-judgment
calls. Everything else in the validator is deterministic (run a command,
check an exit code, poll a port) — these two need real judgment:

- Whether the specification's requirements actually got implemented (not
  just "does the code exist", but "does it cover what was asked for").
- What the "primary user journey" actually IS for this specific POV — \
  new.md: "The exact journey must come from the specification", never a
  hardcoded generic flow.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.tools.journey_runner import JourneyStep
from pov_builder.ui_contract import format_declared_testids

SPECIFICATION_VALIDATION_SYSTEM_PROMPT = """You are validating whether a generated \
application actually implements its specification, for MongoDB's POV Builder \
platform (new.md Phase 7, specification validation).

You are given the original InitialPOVSpec and what was ACTUALLY implemented \
(endpoints, routes, collections, indexes — drawn from the real commits the \
implementation agents made, not from re-reading source code yourself).

## Check
- Every MUST_HAVE / EXPLICIT requirement is covered by something actually implemented.
- No major unsupported functionality was introduced beyond the spec (scope creep).

Do NOT flag a requirement as unmet just because the naming doesn't match \
exactly — judge by capability, not literal string matching. Only report a \
requirement as unmet when there's genuinely nothing implemented that covers it.

## Output
`unmet_requirements`: requirements from the spec with nothing implemented to cover them.
`scope_creep`: implemented functionality with no basis in the spec.
"""


class SpecificationValidationOutput(BaseModel):
    unmet_requirements: list[str] = Field(default_factory=list)
    scope_creep: list[str] = Field(default_factory=list)


def build_specification_validation_messages(
    initial_spec: InitialPOVSpec, implemented_surface: dict[str, Any]
) -> list[BaseMessage]:
    content = (
        f"InitialPOVSpec:\n{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}\n\n"
        f"Actually implemented (from real commits):\n{json.dumps(implemented_surface, indent=2)}"
    )
    return [SystemMessage(content=SPECIFICATION_VALIDATION_SYSTEM_PROMPT), HumanMessage(content=content)]


PRIMARY_USER_JOURNEY_SYSTEM_PROMPT = """You are defining the PRIMARY user journey to \
test for a generated POV application, for MongoDB's POV Builder platform (new.md \
Phase 7, user journey validation).

The exact journey must come from the specification — never a generic, \
hardcoded flow. Example shape (new.md): open application -> search -> \
select an entity -> view details -> perform an action -> verify a state \
change. Ground every step in the ACTUAL frontend contract (real routes/\
selectors-worthy elements) and API contract (real endpoints) you're given \
— not an imagined UI.

## Output
`journey_description`: one sentence describing the journey in plain language.
`steps`: an ordered list of browser actions. Each step's `action` is one of:
- "goto" (value = a path like "/orders", relative to the frontend's base URL)
- "click" (selector = a CSS selector for the element to click)
- "fill" (selector = CSS selector, value = text to type)
- "assert_visible" (selector = CSS selector that must be visible)
- "assert_text" (selector = CSS selector, value = text expected inside it)
You do NOT have access to the real rendered HTML — do NOT guess a CSS \
class, tag-only selector, or invented data-testid. You are given the \
ACTUAL data-testid attributes frontend_dev really built (both the \
universal ones every page has, and any POV-specific ones it declared) — \
use ONLY those. A selector that isn't in that list is a guess, and a \
guessed selector already caused a real Playwright timeout in production \
use of this validator — ground every step in what's actually there.
"""


class PrimaryUserJourneyOutput(BaseModel):
    journey_description: str = ""
    steps: list[JourneyStep] = Field(default_factory=list)


def build_primary_user_journey_messages(
    initial_spec: InitialPOVSpec,
    frontend_contract: dict[str, Any],
    api_contract: dict[str, Any],
    key_element_testids: dict[str, str] | None = None,
    list_item_testid_prefixes: dict[str, str] | None = None,
) -> list[BaseMessage]:
    content = (
        f"InitialPOVSpec:\n{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}\n\n"
        f"Frontend contract:\n{json.dumps(frontend_contract, indent=2)}\n\n"
        f"API contract:\n{json.dumps(api_contract, indent=2)}"
    )
    content += format_declared_testids(key_element_testids, list_item_testid_prefixes)
    return [SystemMessage(content=PRIMARY_USER_JOURNEY_SYSTEM_PROMPT), HumanMessage(content=content)]
