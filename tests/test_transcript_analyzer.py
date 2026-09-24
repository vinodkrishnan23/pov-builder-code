"""Tests for new.md Phase 1 — transcript_analyzer.

What's deterministically testable without a live LLM: the empty-transcript
short-circuit (no LLM call at all), that the node correctly wires the LLM's
(fake, here) structured response into state, and that the prompt itself
actually instructs classification / no-HOW / traceability / "don't
fabricate" — i.e. new.md's scenario list minus the ones that require real
LLM reasoning (explicit vs. inferred classification, contradiction
handling, etc.), which can only be verified by running against a real
transcript + real LLM — see README "How to execute the graph" for that
manual verification step; faking those would be false confidence, not a
real test.
"""

from __future__ import annotations

from pov_builder.graph.nodes import make_transcript_analyzer
from pov_builder.models.common import AgentRunStatus, RequirementClassification, TraceableRequirement
from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.prompts.transcript_analyzer import SYSTEM_PROMPT, build_messages

from tests.fakes import FakeLLM, FakePovRunStore


def test_empty_transcript_short_circuits_without_calling_llm_or_persisting():
    llm = FakeLLM(structured_result=InitialPOVSpec(executive_summary="should never be returned"))
    store = FakePovRunStore()
    node = make_transcript_analyzer(llm, store)

    result = node({"transcript": "   ", "user_email": "a@b.com", "pov_name": "demo"})

    spec = result["initial_spec"]
    assert spec.executive_summary == ""  # the FakeLLM's canned result was never used
    assert any("no transcript" in q.lower() for q in spec.open_questions)
    assert result["agent_statuses"]["transcript_analyzer"].status == AgentRunStatus.SUCCEEDED
    assert result.get("pov_id") is None
    assert store.calls == []  # nothing persisted — nothing real to persist


def test_populated_transcript_uses_the_llms_structured_output_and_persists():
    fake_spec = InitialPOVSpec(
        executive_summary="Real-time inventory sync POV",
        functional_requirements=[
            TraceableRequirement(
                id="fr1",
                description="Sync inventory every 5 minutes",
                classification=RequirementClassification.EXPLICIT,
                evidence="the customer said 'we need this every five minutes'",
            )
        ],
    )
    store = FakePovRunStore(pov_id="pov-123")
    node = make_transcript_analyzer(FakeLLM(structured_result=fake_spec), store)

    transcript = "Customer: we need inventory sync every five minutes."
    result = node({"transcript": transcript, "user_email": "a@b.com", "pov_name": "demo"})

    assert result["initial_spec"] is fake_spec
    assert result["initial_spec"].functional_requirements[0].classification == RequirementClassification.EXPLICIT
    assert result["pov_id"] == "pov-123"

    assert len(store.calls) == 1
    assert store.calls[0]["user_email"] == "a@b.com"
    assert store.calls[0]["pov_name"] == "demo"
    assert store.calls[0]["transcript"] == transcript
    assert store.calls[0]["initial_spec"] is fake_spec


def test_prompt_instructs_classification_taxonomy():
    for label in ("EXPLICIT", "INFERRED", "ASSUMED", "UNKNOWN"):
        assert label in SYSTEM_PROMPT


def test_prompt_forbids_technical_implementation_decisions():
    lowered = SYSTEM_PROMPT.lower()
    assert "must not decide how" in lowered
    for forbidden in ("api endpoints", "collection/index", "react components", "source code"):
        assert forbidden in lowered


def test_prompt_requires_evidence_and_forbids_fabricated_timestamps():
    lowered = SYSTEM_PROMPT.lower()
    assert "evidence" in lowered
    assert "never fabricate" in lowered


def test_build_messages_includes_the_transcript_verbatim():
    messages = build_messages("a very specific unique transcript string")
    assert any("a very specific unique transcript string" in str(m.content) for m in messages)
