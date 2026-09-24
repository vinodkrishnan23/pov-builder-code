"""POVReviewResult — the POV Reviewer's output (new.md Phase 2).

Answers "Did we correctly understand what the customer wants?" by comparing
`InitialPOVSpec` against the transcript. Never rewrites the spec — only
reports issues.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"


class ReviewIssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReviewIssue(BaseModel):
    id: str
    severity: ReviewIssueSeverity
    category: str
    description: str
    evidence: str = ""
    affected_requirement: str | None = None
    recommended_action: str = ""


class POVReviewResult(BaseModel):
    status: ReviewStatus
    issues: list[ReviewIssue] = Field(default_factory=list)
    approved_requirements: list[str] = Field(default_factory=list)
    removed_requirements: list[str] = Field(default_factory=list)
    requirements_requiring_clarification: list[str] = Field(default_factory=list)
