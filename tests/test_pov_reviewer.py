"""Tests for new.md Phase 2 — pov_reviewer.

Same honesty boundary as test_transcript_analyzer.py: deterministic wiring
and prompt-content are unit-testable without a live LLM; whether the model
actually judges "correct spec" vs. "missing requirement" vs. "scope creep"
correctly (new.md's own Phase 2 test list) needs a real transcript + real
LLM — see README "How to execute the graph".
"""

from __future__ import annotations

from pov_builder.graph.nodes import make_pov_reviewer
from pov_builder.models.common import AgentRunStatus
from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.review import POVReviewResult, ReviewStatus
from pov_builder.prompts.pov_reviewer import SYSTEM_PROMPT, build_messages

from tests.fakes import FakeLLM


def test_missing_transcript_fails_deterministically_without_calling_llm():
    llm = FakeLLM(structured_result=POVReviewResult(status=ReviewStatus.PASS))
    node = make_pov_reviewer(llm)

    result = node({"transcript": "  ", "initial_spec": InitialPOVSpec()})

    review = result["review_result"]
    assert review.status == ReviewStatus.FAIL  # canned PASS from the fake was never used
    assert any(issue.id == "no-basis-to-review" for issue in review.issues)
    assert result["agent_statuses"]["pov_reviewer"].status == AgentRunStatus.SUCCEEDED


def test_missing_initial_spec_fails_deterministically_without_calling_llm():
    llm = FakeLLM(structured_result=POVReviewResult(status=ReviewStatus.PASS))
    node = make_pov_reviewer(llm)

    result = node({"transcript": "a real transcript", "initial_spec": None})

    assert result["review_result"].status == ReviewStatus.FAIL


def test_populated_state_uses_the_llms_structured_output():
    fake_review = POVReviewResult(status=ReviewStatus.PASS_WITH_WARNINGS, approved_requirements=["FR-1"])
    node = make_pov_reviewer(FakeLLM(structured_result=fake_review))

    result = node(
        {
            "transcript": "Customer: we need X.",
            "initial_spec": InitialPOVSpec(executive_summary="X POV"),
        }
    )

    assert result["review_result"] is fake_review


def test_prompt_defines_the_three_statuses():
    for status in ("PASS", "PASS_WITH_WARNINGS", "FAIL"):
        assert status in SYSTEM_PROMPT


def test_prompt_forbids_rewriting_the_spec():
    assert "must not rewrite" in SYSTEM_PROMPT.lower()


def test_prompt_instructs_fail_sparingly():
    lowered = SYSTEM_PROMPT.lower()
    assert "use fail sparingly" in lowered
    assert "materially misrepresent" in lowered


def test_build_messages_includes_transcript_and_spec_content():
    spec = InitialPOVSpec(executive_summary="a very specific unique summary")
    messages = build_messages("a very specific unique transcript", spec)
    combined = " ".join(str(m.content) for m in messages)
    assert "a very specific unique transcript" in combined
    assert "a very specific unique summary" in combined


def test_build_messages_includes_rejected_notes_and_tells_the_model_not_to_reflag_them():
    spec = InitialPOVSpec()
    messages = build_messages("a transcript", spec, rejected_notes=["a very specific dismissed concern"])
    combined = " ".join(str(m.content) for m in messages)
    assert "a very specific dismissed concern" in combined
    assert "do not re-flag" in combined.lower()


def test_build_messages_omits_rejected_section_when_none_given():
    messages = build_messages("a transcript", InitialPOVSpec())
    combined = " ".join(str(m.content) for m in messages)
    assert "dismissed" not in combined.lower()


def test_pov_reviewer_node_passes_rejected_review_notes_from_state_into_the_prompt():
    """Verifies the node actually reads state["rejected_review_notes"] and
    threads it through — a prompt-content test alone can't see this;
    it's the part that makes Reject clicks stick across rounds."""
    captured: list = []

    class _CapturingLLM:
        def with_structured_output(self, schema, **kwargs):
            outer = self

            class _Runnable:
                def invoke(self, messages):
                    captured.append(messages)
                    return POVReviewResult(status=ReviewStatus.PASS)

            return _Runnable()

    node = make_pov_reviewer(_CapturingLLM())
    node(
        {
            "transcript": "a transcript",
            "initial_spec": InitialPOVSpec(),
            "rejected_review_notes": ["a previously dismissed concern"],
        }
    )

    combined = " ".join(str(m.content) for m in captured[0])
    assert "a previously dismissed concern" in combined
