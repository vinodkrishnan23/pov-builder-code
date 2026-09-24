"""RepairInfo — tracks the repair loop (new.md Phase 8: Repair Router).

The router never fixes anything itself — it decides which implementation
node(s) should repair each failure and returns structured routing
information. `RepairInfo` is the loop's memory: how many attempts have
happened, and whether the configurable cap has forced `HUMAN_REVIEW_REQUIRED`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from pov_builder.models.validation import RepairTarget


class RepairLoopStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class RepairAttempt(BaseModel):
    attempt_number: int
    targets: list[RepairTarget]
    triggered_by_check_ids: list[str] = Field(default_factory=list)
    outcome: str = ""


class RepairInfo(BaseModel):
    attempts: list[RepairAttempt] = Field(default_factory=list)
    max_attempts: int = 3
    status: RepairLoopStatus = RepairLoopStatus.NOT_STARTED

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def cap_exceeded(self) -> bool:
        return self.attempt_count >= self.max_attempts
