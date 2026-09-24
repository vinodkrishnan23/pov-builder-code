"""ValidationReport — the Integration Validator's output (new.md Phase 7).

Reports whether the generated application actually works as one coherent
POV. The validator never modifies application code — a downstream repair
agent does that, driven by `repair_required`/`repair_targets` here.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ValidationStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"


class ValidationCategory(StrEnum):
    SPECIFICATION = "specification"
    STATIC = "static"
    BUILD = "build"
    DATABASE = "database"
    API = "api"
    FRONTEND = "frontend"
    INTEGRATION = "integration"
    USER_JOURNEY = "user_journey"


class RepairTarget(StrEnum):
    SEEDER = "seeder"
    BACKEND_DEV = "backend_dev"
    FRONTEND_DEV = "frontend_dev"
    SPECIFICATION = "specification"


class ValidationCheck(BaseModel):
    id: str
    category: ValidationCategory
    status: ValidationStatus
    description: str
    evidence: str = ""
    affected_component: str | None = None
    severity: str = "medium"
    repair_action: str = ""


class ValidationReport(BaseModel):
    status: ValidationStatus
    checks: list[ValidationCheck] = Field(default_factory=list)
    repair_required: bool = False
    repair_targets: list[RepairTarget] = Field(default_factory=list)
