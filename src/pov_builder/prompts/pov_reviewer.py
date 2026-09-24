"""Prompt for new.md Phase 2 — pov_reviewer.

Answers "Did we correctly understand what the customer wants?" by comparing
InitialPOVSpec against the original transcript. Never redesigns or
rewrites the spec — only reports a POVReviewResult.
"""

from __future__ import annotations

import json

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from pov_builder.models.pov_spec import InitialPOVSpec

SYSTEM_PROMPT = """You are reviewing a POV (Point of View) specification against \
the original customer meeting transcript, for MongoDB's POV Builder platform.

You are NOT designing a solution and you must NOT rewrite the specification. \
Your only job is to validate whether the specification accurately represents \
the conversation, and report a structured review result.

## Compare the specification against the transcript. Identify:
- Missing requirements — things the customer clearly asked for that the spec omitted.
- Unsupported requirements — things the spec claims that have no basis in the transcript.
- Incorrect interpretations — places the spec misreads what was actually said.
- Contradictions — the spec conflicts with itself or with the transcript.
- Missing user journeys, missing personas, missing business entities.
- Scope creep — the spec adds functionality beyond what was actually discussed.
- Incorrect priorities — the spec over- or under-emphasizes something relative \
to how the customer actually weighted it.
- Unsupported MongoDB Atlas capabilities — the spec claims an Atlas feature is \
relevant when the transcript gives no real basis for it.

## Status
- PASS: the specification accurately reflects the conversation.
- PASS_WITH_WARNINGS: minor issues exist (small gaps, mild ambiguity, minor \
scope creep) that do NOT block moving forward to technical design.
- FAIL: the specification MATERIALLY misrepresents the conversation — a \
requirement with no basis at all, a real contradiction, or missing information \
that's essential to understanding what's being asked for. Use FAIL sparingly: \
only when continuing to design against this spec would mean building on a \
wrong foundation. Ordinary gaps belong in \
`requirements_requiring_clarification` or a lower-severity issue, not an
automatic FAIL.

## Output
Populate every field: `issues` (each with id, severity, category, description, \
evidence, affected_requirement, recommended_action), `approved_requirements` \
(field names/ids you're confident are correct), `removed_requirements` (field \
names/ids that should be dropped because they're unsupported), and \
`requirements_requiring_clarification` (field names/ids too ambiguous to \
proceed on, but not by themselves a hard FAIL).
"""


def build_messages(
    transcript: str, initial_spec: InitialPOVSpec, rejected_notes: list[str] | None = None
) -> list[BaseMessage]:
    content = (
        f"Transcript:\n{transcript}\n\n"
        "Specification to review (InitialPOVSpec):\n"
        f"{json.dumps(initial_spec.model_dump(mode='json'), indent=2)}"
    )
    if rejected_notes:
        # A human already looked at a PRIOR review of this exact spec and
        # explicitly dismissed these specific concerns as not real issues
        # (see graph/nodes.py's initial_spec_approval_gate, which is the
        # only place this list is populated, from the human's own
        # per-issue Reject clicks in the UI). Without this, every revise
        # round starts pov_reviewer from a blank slate and it can
        # re-flag/re-invent the exact same dismissed concern forever —
        # this is what makes the human's Accept/Reject decisions actually
        # stick across rounds instead of only affecting this one round.
        rejected_text = "\n".join(f"- {note}" for note in rejected_notes)
        content += (
            "\n\nA human reviewer already dismissed these EXACT concerns in a prior "
            "round as not real issues — do NOT re-flag any of them unless the "
            f"specification has changed in a way that's newly relevant to one:\n{rejected_text}"
        )
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=content),
    ]
