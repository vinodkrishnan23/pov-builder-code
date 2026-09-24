"""Prompt for new.md Phase 10 — howto_helper.

Writes the README a developer needs to clone the repo and run the POV
locally. new.md is explicit: "The README must describe what ACTUALLY
EXISTS... Do not document functionality that was specified but not
implemented... If a command cannot be verified, do not invent it."

So this prompt is grounded ONLY in structured facts already extracted by
real prior phases — each ComponentCommit's `details` (seeder/backend_dev/
frontend_dev already return exact start/seed commands, env vars,
endpoints, routes, health endpoint — see their own prompts) and the
ValidationReport's checks (what was actually verified to work, and what
wasn't) — never a fresh guess at the repository's contents. Nothing here
gives the model raw file access; if a fact isn't in what's passed, the
model can't verify it and must say so rather than invent it.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from pov_builder.models.pov_spec import InitialPOVSpec

SYSTEM_PROMPT = """You are writing the README for a generated MongoDB POV \
application, for MongoDB's POV Builder platform (new.md Phase 10, how-to helper).

A developer with a clean laptop must be able to follow this README without \
needing to understand the internal pipeline that generated it. You are \
given ONLY real, already-verified facts: the original POV specification, \
the technical design, each component's ACTUAL commit details (real start/ \
seed commands, real environment variables, real endpoints/routes actually \
implemented), and the ACTUAL validation results (what was verified to \
really work, what wasn't).

## Hard rule
Do NOT invent a command, environment variable, endpoint, route, or file \
that isn't present in what you were given. If something the README should \
cover has no real basis in the facts provided, write "not available" for \
that item rather than guessing. Do not document functionality that was \
specified but never actually implemented (check the validation results and \
each component's real details before claiming something works).

## Include (in this order)
1. POV Overview  2. Architecture  3. Prerequisites  4. Repository structure \
5. MongoDB Atlas setup  6. Required environment variables  7. How to \
configure .env  8. How to install dependencies  9. How to seed MongoDB \
10. How to start the backend  11. How to start the frontend  12. How to \
access the application  13. Sample credentials if applicable  14. Primary \
demo journey  15. MongoDB Atlas features demonstrated  16. Troubleshooting \
17. Known limitations  18. POV scope

## Output
`readme_content`: the complete README.md content (real markdown).
`verified_commands`: every shell command that appears in the README, taken \
directly from the real facts you were given (never invented).
`known_limitations`: anything that didn't work per the validation results, \
or was specified but not implemented.
"""


class HowToReadmeOutput(BaseModel):
    readme_content: str = ""
    verified_commands: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)


def build_messages(
    initial_spec: InitialPOVSpec,
    technical_spec_summary: dict[str, Any],
    repository_summary: dict[str, Any],
    implemented_details: dict[str, Any],
    validation_summary: dict[str, Any],
) -> list[BaseMessage]:
    content = (
        f"InitialPOVSpec:\n{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}\n\n"
        f"Technical design summary:\n{json.dumps(technical_spec_summary, indent=2)}\n\n"
        f"Repository (real repo/branch, real commits):\n{json.dumps(repository_summary, indent=2)}\n\n"
        f"Each component's ACTUAL implementation details (from real commits):\n"
        f"{json.dumps(implemented_details, indent=2)}\n\n"
        f"Actual validation results (what was really verified):\n{json.dumps(validation_summary, indent=2)}"
    )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
