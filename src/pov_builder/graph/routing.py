"""Conditional-edge functions. Each is a pure function of `POVState`
returning either a single destination-key string, or (for
`route_after_repair_router`) a list of them for dynamic fan-out — see
`builder.py` for how each return value maps to an actual node/END.
"""

from __future__ import annotations

from pov_builder.models.repair import RepairLoopStatus
from pov_builder.models.review import ReviewStatus
from pov_builder.models.validation import RepairTarget, ValidationStatus

from pov_builder.graph.state import POVState

# RepairTarget -> the graph node name that repairs it. SPECIFICATION has no
# repair node yet (new.md Phase 2's "Initial Spec refinement" doesn't exist
# as a node in this graph) — see route_after_repair_router for the fallback.
_REPAIR_TARGET_TO_NODE: dict[RepairTarget, str] = {
    RepairTarget.SEEDER: "seeder",
    RepairTarget.BACKEND_DEV: "backend_dev",
    RepairTarget.FRONTEND_DEV: "frontend_dev",
}


def route_after_pov_reviewer(state: POVState) -> str:
    review_result = state.get("review_result")
    if review_result is not None and review_result.status == ReviewStatus.FAIL:
        # No spec-refinement node exists yet (new.md: "Do not implement the
        # refinement agent yet unless the existing graph requires a
        # placeholder") — a FAIL simply ends the run here.
        return "end"
    return "initial_spec_approval_gate"


def route_after_initial_spec_gate(state: POVState) -> str:
    """Human approval gate — workspace-specific addition (see README
    "Design decision: human approval gates"), not a new.md phase."""
    if state.get("initial_spec_decision") == "revise":
        return "transcript_analyzer"
    return "spec_architect"


def route_after_technical_spec_gate(state: POVState) -> list[str]:
    if state.get("technical_spec_decision") == "revise":
        return ["spec_architect"]
    # Fan out to all three in parallel, same as the pre-gate topology.
    return ["seeder", "backend_dev", "frontend_dev"]


def route_after_integration_validator(state: POVState) -> str:
    report = state.get("validation_report")
    if report is not None and report.status == ValidationStatus.FAIL and report.repair_required:
        return "repair_router"
    return "howto_helper"


def route_after_repair_router(state: POVState) -> list[str]:
    """Fan out to whichever implementation nodes the last repair attempt
    targets, then loop back through `integration_validator` to re-check.
    Stops the loop (routes to `end`) once `repair_info.status` has been
    forced to HUMAN_REVIEW_REQUIRED by the attempt cap."""
    repair_info = state.get("repair_info")
    if repair_info is not None and repair_info.status == RepairLoopStatus.HUMAN_REVIEW_REQUIRED:
        return ["end"]

    last_attempt = repair_info.attempts[-1] if repair_info and repair_info.attempts else None
    targets = last_attempt.targets if last_attempt else []
    destinations = [_REPAIR_TARGET_TO_NODE[t] for t in targets if t in _REPAIR_TARGET_TO_NODE]

    if not destinations:
        # Nothing concrete to repair (e.g. only SPECIFICATION was flagged,
        # or the placeholder repair_router hasn't been told any real
        # targets yet) — re-validate directly rather than dead-ending.
        return ["integration_validator"]
    return destinations
