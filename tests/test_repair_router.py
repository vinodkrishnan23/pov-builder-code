"""Tests for new.md Phase 8 — repair_router.

No LLM/git dependency (per new.md's own framing: a router, not another
giant agent) — the RULES-table mapping itself already happened inside
`make_integration_validator` (`_repair_target_for_check`), populating
`ValidationReport.repair_targets`; this node's job is purely the
bookkeeping new.md calls for: record the attempt, enforce `max_attempts`,
and force HUMAN_REVIEW_REQUIRED once exceeded ("prevent infinite repair
loops").
"""

from __future__ import annotations

from pov_builder.graph.nodes import repair_router
from pov_builder.models.common import AgentRunStatus
from pov_builder.models.repair import RepairAttempt, RepairInfo, RepairLoopStatus
from pov_builder.models.validation import (
    RepairTarget,
    ValidationCategory,
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)


def _report(*, targets, checks) -> ValidationReport:
    return ValidationReport(status=ValidationStatus.FAIL, repair_required=True, repair_targets=targets, checks=checks)


def test_first_attempt_records_targets_from_the_validation_report():
    report = _report(
        targets=[RepairTarget.BACKEND_DEV],
        checks=[
            ValidationCheck(
                id="api-health", category=ValidationCategory.API, status=ValidationStatus.FAIL, description="down"
            )
        ],
    )
    result = repair_router({"validation_report": report})

    repair_info = result["repair_info"]
    assert repair_info.attempt_count == 1
    attempt = repair_info.attempts[0]
    assert attempt.attempt_number == 1
    assert attempt.targets == [RepairTarget.BACKEND_DEV]
    assert attempt.triggered_by_check_ids == ["api-health"]
    assert repair_info.status == RepairLoopStatus.IN_PROGRESS
    assert result["agent_statuses"]["repair_router"].status == AgentRunStatus.SUCCEEDED


def test_triggered_by_check_ids_excludes_passing_checks():
    """A real bug caught while wiring this up for real: the original
    placeholder included EVERY check's id, not just the failing ones —
    misleading, since "triggered_by" should mean "caused this repair"."""
    report = _report(
        targets=[RepairTarget.SEEDER],
        checks=[
            ValidationCheck(
                id="database-seed", category=ValidationCategory.DATABASE, status=ValidationStatus.FAIL, description="failed"
            ),
            ValidationCheck(
                id="api-health", category=ValidationCategory.API, status=ValidationStatus.PASS, description="ok"
            ),
        ],
    )
    result = repair_router({"validation_report": report})

    assert result["repair_info"].attempts[0].triggered_by_check_ids == ["database-seed"]


def test_multiple_targets_all_carried_through():
    report = _report(targets=[RepairTarget.SEEDER, RepairTarget.BACKEND_DEV], checks=[])
    result = repair_router({"validation_report": report})

    assert set(result["repair_info"].attempts[0].targets) == {RepairTarget.SEEDER, RepairTarget.BACKEND_DEV}


def test_subsequent_attempt_increments_attempt_number_and_preserves_history():
    prior = RepairInfo(
        attempts=[
            RepairAttempt(attempt_number=1, targets=[RepairTarget.SEEDER])
        ]
    )
    report = _report(targets=[RepairTarget.BACKEND_DEV], checks=[])
    result = repair_router({"validation_report": report, "repair_info": prior})

    repair_info = result["repair_info"]
    assert repair_info.attempt_count == 2
    assert repair_info.attempts[0].targets == [RepairTarget.SEEDER]  # history preserved
    assert repair_info.attempts[1].attempt_number == 2
    assert repair_info.attempts[1].targets == [RepairTarget.BACKEND_DEV]


def test_reaching_max_attempts_forces_human_review_required():
    prior = RepairInfo(
        attempts=[
            RepairAttempt(attempt_number=n, targets=[RepairTarget.SEEDER])
            for n in (1, 2)
        ],
        max_attempts=3,
    )
    report = _report(targets=[RepairTarget.SEEDER], checks=[])
    result = repair_router({"validation_report": report, "repair_info": prior})

    repair_info = result["repair_info"]
    assert repair_info.attempt_count == 3
    assert repair_info.status == RepairLoopStatus.HUMAN_REVIEW_REQUIRED


def test_missing_validation_report_records_an_empty_attempt_without_raising():
    result = repair_router({})

    repair_info = result["repair_info"]
    assert repair_info.attempt_count == 1
    assert repair_info.attempts[0].targets == []
    assert repair_info.attempts[0].triggered_by_check_ids == []
