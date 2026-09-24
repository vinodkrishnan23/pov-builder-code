"""Shared enums and small models used across the other model modules."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class RequirementClassification(StrEnum):
    """new.md Phase 1: every extracted requirement must be classified as
    one of these — never present an inference as an explicit customer
    requirement."""

    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    ASSUMED = "ASSUMED"
    UNKNOWN = "UNKNOWN"


class TraceableRequirement(BaseModel):
    """One requirement with its classification and transcript evidence
    (new.md Phase 1: "Every important requirement must have concise
    transcript evidence... Never fabricate timestamps.")."""

    id: str
    description: str
    classification: RequirementClassification
    evidence: str = Field(
        default="", description="Concise transcript excerpt supporting this requirement."
    )
    speaker: str | None = None
    timestamp: str | None = None


class AgentRunStatus(StrEnum):
    """Status of one node's execution for this POV run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AgentStatusRecord(BaseModel):
    """One entry in `POVState.agent_statuses`, keyed by node/agent name."""

    agent_name: str
    status: AgentRunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: str = ""
    error: str | None = None

    @classmethod
    def placeholder(cls, agent_name: str) -> "AgentStatusRecord":
        """Phase 0 nodes are placeholders — this marks a node as having run
        without doing real work, so downstream routing/tests have a
        consistent status to key off of before real logic exists."""
        now = datetime.now(timezone.utc)
        return cls(
            agent_name=agent_name,
            status=AgentRunStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            summary="placeholder — not yet implemented (Phase 0 foundation)",
        )
