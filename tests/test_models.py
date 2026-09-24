"""Pydantic model tests — validation and the pieces future phases will
rely on (classification enum, status defaults, repair-cap arithmetic)."""

import pytest
from pydantic import ValidationError

from pov_builder.models.common import (
    AgentRunStatus,
    AgentStatusRecord,
    RequirementClassification,
    TraceableRequirement,
)
from pov_builder.models.repair import RepairAttempt, RepairInfo, RepairLoopStatus
from pov_builder.models.review import POVReviewResult, ReviewStatus
from pov_builder.models.validation import RepairTarget


def test_traceable_requirement_requires_valid_classification():
    with pytest.raises(ValidationError):
        TraceableRequirement(id="r1", description="x", classification="NOT_A_REAL_VALUE")


def test_traceable_requirement_accepts_enum_or_string_value():
    req = TraceableRequirement(id="r1", description="x", classification="EXPLICIT")
    assert req.classification == RequirementClassification.EXPLICIT


def test_agent_status_placeholder_marks_succeeded():
    record = AgentStatusRecord.placeholder("transcript_analyzer")
    assert record.status == AgentRunStatus.SUCCEEDED
    assert record.agent_name == "transcript_analyzer"
    assert "placeholder" in record.summary


def test_review_result_defaults_to_empty_lists():
    result = POVReviewResult(status=ReviewStatus.PASS)
    assert result.issues == []
    assert result.approved_requirements == []


def test_repair_info_cap_exceeded_at_max_attempts():
    info = RepairInfo(
        attempts=[
            RepairAttempt(attempt_number=1, targets=[RepairTarget.BACKEND_DEV]),
            RepairAttempt(attempt_number=2, targets=[RepairTarget.BACKEND_DEV]),
            RepairAttempt(attempt_number=3, targets=[RepairTarget.BACKEND_DEV]),
        ],
        max_attempts=3,
    )
    assert info.attempt_count == 3
    assert info.cap_exceeded is True


def test_repair_info_not_exceeded_below_max_attempts():
    info = RepairInfo(attempts=[RepairAttempt(attempt_number=1, targets=[])], max_attempts=3)
    assert info.cap_exceeded is False


def test_repair_loop_status_human_review_required_is_a_valid_value():
    info = RepairInfo(status=RepairLoopStatus.HUMAN_REVIEW_REQUIRED)
    assert info.status == RepairLoopStatus.HUMAN_REVIEW_REQUIRED
