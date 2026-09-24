"""InitialPOVSpec — the Transcript Analyzer's output (new.md Phase 1).

Answers "WHAT are we building and WHY?" — never "HOW should we implement
it?". No API endpoints, MongoDB collections/indexes, React components,
backend modules, source code, seed scripts, or infrastructure belong here;
those are Spec Architect's job (Phase 3) and downstream.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from pov_builder.models.common import TraceableRequirement


class Persona(BaseModel):
    id: str
    name: str
    description: str
    goals: list[str] = Field(default_factory=list)


class UserJourney(BaseModel):
    id: str
    persona_id: str
    title: str
    steps: list[str] = Field(default_factory=list)


class UseCase(BaseModel):
    id: str
    title: str
    description: str
    related_persona_ids: list[str] = Field(default_factory=list)


class BusinessEntity(BaseModel):
    name: str
    description: str
    rough_volume: str | None = None


class InitialPOVSpec(BaseModel):
    """The Chat/Transcript Analyzer's output — stored as
    `POVState.initial_spec`. Every requirement-shaped field is a list of
    `TraceableRequirement` so classification + evidence travel with it,
    rather than being asserted as plain strings."""

    executive_summary: str = ""
    business_problem: str = ""
    current_state: str = ""
    desired_future_state: str = ""

    business_objectives: list[TraceableRequirement] = Field(default_factory=list)
    personas: list[Persona] = Field(default_factory=list)
    user_journeys: list[UserJourney] = Field(default_factory=list)
    use_cases: list[UseCase] = Field(default_factory=list)

    functional_requirements: list[TraceableRequirement] = Field(default_factory=list)
    ai_genai_requirements: list[TraceableRequirement] = Field(default_factory=list)
    business_entities: list[BusinessEntity] = Field(default_factory=list)
    integrations: list[TraceableRequirement] = Field(default_factory=list)
    mongodb_atlas_opportunities: list[TraceableRequirement] = Field(default_factory=list)

    pov_scope: str = ""
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    success_criteria: list[TraceableRequirement] = Field(default_factory=list)
