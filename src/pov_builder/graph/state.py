"""POVState — the central, typed state threaded through every node
(new.md Phase 0 "STATE" / "DESIGN PRINCIPLE").

Deliberately does NOT hold generated source code — source lives in the
POV's Git repository. State holds references, metadata, status, commit
SHAs, and validation results only.

`agent_statuses` needs a custom reducer: `seeder`, `backend_dev`, and
`frontend_dev` run in the SAME super-step (parallel fan-out from
`spec_architect`), and each writes only its own key into this dict. Without
a reducer, LangGraph treats two nodes writing the same top-level state key
("agent_statuses") in one super-step as a conflicting concurrent write and
raises `InvalidUpdateError` — `_merge_status_dicts` makes that a safe
shallow merge instead. `seeder_commit`/`backend_dev_commit`/
`frontend_dev_commit` sidestep the same problem more simply, by giving each
parallel writer its own top-level key rather than sharing one list.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from pov_builder.models.common import AgentStatusRecord
from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.readme_status import ReadmeStatus
from pov_builder.models.repair import RepairInfo
from pov_builder.models.repository import ComponentCommit, RepositoryInfo
from pov_builder.models.review import POVReviewResult
from pov_builder.models.technical_spec import DetailedTechnicalSpec
from pov_builder.models.validation import ValidationReport


def _merge_status_dicts(
    current: dict[str, AgentStatusRecord] | None,
    update: dict[str, AgentStatusRecord] | None,
) -> dict[str, AgentStatusRecord]:
    merged = dict(current or {})
    merged.update(update or {})
    return merged


class POVState(TypedDict, total=False):
    # Input — user_email and pov_name are gathered from the end user BEFORE
    # the run starts (see run.py/webapp) and are the durable, human-meaningful
    # identity used everywhere in git (branch name, PyGithub calls) — pov_id
    # below never appears in git; it stays purely an internal Mongo/checkpoint key.
    transcript: str
    user_email: str
    pov_name: str

    # Phase 1 — Transcript Analyzer
    pov_id: str | None  # minted at persist time (tools/mongo_store.py), not before
    initial_spec: InitialPOVSpec | None

    # Phase 2 — POV Reviewer
    review_result: POVReviewResult | None

    # Human approval gates (workspace-specific addition, not a new.md
    # requirement — see README "Design decision: human approval gates").
    # Not real graph state so much as the interrupt()/Command(resume=...)
    # protocol's payload: each gate node sets its own *_decision after
    # resuming, and appends to *_feedback on a "revise" decision so the
    # re-run phase's prompt can see every round of feedback, not just the
    # latest one.
    initial_spec_decision: str | None  # "approve" | "revise"
    spec_feedback: list[str]
    # Descriptions of pov_reviewer issues the human explicitly clicked
    # Reject on, accumulated across every revise round (see
    # initial_spec_approval_gate) — fed back into pov_reviewer's OWN
    # prompt on the next round so a dismissed concern doesn't keep
    # resurfacing forever (pov_reviewer otherwise has no memory across
    # rounds at all; see README "Design decision: human approval gates").
    rejected_review_notes: list[str]
    technical_spec_decision: str | None
    technical_spec_feedback: list[str]

    # Phase 3 — Spec Architect
    technical_spec: DetailedTechnicalSpec | None

    # Phases 4-6 — Seeder / Backend Dev / Frontend Dev (parallel fan-out;
    # each writes only its own key — see module docstring). Consolidated
    # into `repository.commits` by `integration_validator` once all three
    # have run.
    seeder_commit: ComponentCommit | None
    backend_dev_commit: ComponentCommit | None
    frontend_dev_commit: ComponentCommit | None
    repository: RepositoryInfo

    # Phase 7 — Integration Validator
    validation_report: ValidationReport | None

    # Phase 8/9 — Repair Router + repair passes
    repair_info: RepairInfo

    # Phase 10 — How-To Helper
    readme_status: ReadmeStatus | None

    # Cross-cutting: per-node run status, merged (not overwritten) across
    # concurrent parallel writers.
    agent_statuses: Annotated[dict[str, AgentStatusRecord], _merge_status_dicts]

    # Escape hatch for anything a placeholder node wants to record that
    # doesn't yet have a typed home — real phases should add a typed field
    # instead of writing here.
    scratch: dict[str, Any]
