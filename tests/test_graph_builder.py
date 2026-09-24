"""Graph-level tests: the topology actually compiles, executes end to end
with placeholder nodes, the parallel fan-out/fan-in genuinely merges (not
overwrites) concurrent `agent_statuses` writes, and each routing function
picks the right destination in isolation.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from pov_builder.graph.builder import NODE_NAMES, build_graph
from pov_builder.graph.routing import (
    route_after_initial_spec_gate,
    route_after_integration_validator,
    route_after_pov_reviewer,
    route_after_repair_router,
    route_after_technical_spec_gate,
)
from pov_builder.models.common import AgentRunStatus
from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.repair import RepairAttempt, RepairInfo, RepairLoopStatus
from pov_builder.models.review import POVReviewResult, ReviewStatus
from pov_builder.models.validation import RepairTarget, ValidationReport, ValidationStatus
from pov_builder.prompts.backend_dev import BackendDevOutput
from pov_builder.prompts.frontend_dev import FrontendDevOutput
from pov_builder.prompts.howto_helper import HowToReadmeOutput
from pov_builder.prompts.seeder import SeederOutput
from pov_builder.prompts.spec_architect.api_contract_design import ApiContractDesignOutput
from pov_builder.prompts.spec_architect.finishing import FinishingOutput
from pov_builder.prompts.spec_architect.frontend_contract_design import FrontendContractDesignOutput
from pov_builder.prompts.spec_architect.query_pattern_design import QueryPatternDesignOutput
from pov_builder.prompts.spec_architect.schema_design import SchemaDesignOutput

from tests.fakes import FakeGitRepoStore, FakeLLM, FakePovRunStore


def _run_to_completion(graph, initial_state, config):
    """Drive a graph past any interrupt()'d approval gates, auto-approving
    each one — mirrors run.py's --auto-approve loop."""
    result = graph.invoke(initial_state, config=config)
    while "__interrupt__" in result:
        result = graph.invoke(Command(resume={"decision": "approve"}), config=config)
    return result


def _happy_path_llm() -> FakeLLM:
    return FakeLLM(
        by_schema={
            InitialPOVSpec: InitialPOVSpec(),
            POVReviewResult: POVReviewResult(status=ReviewStatus.PASS),
            SchemaDesignOutput: SchemaDesignOutput(data_model={"collections": []}),
            QueryPatternDesignOutput: QueryPatternDesignOutput(query_patterns={"patterns": []}),
            ApiContractDesignOutput: ApiContractDesignOutput(api_contract={"paths": {}}),
            FrontendContractDesignOutput: FrontendContractDesignOutput(frontend_contract={"routes": []}),
            FinishingOutput: FinishingOutput(requirements={"must_have": []}),
            SeederOutput: SeederOutput(files={"seed.js": "// seed"}),
            BackendDevOutput: BackendDevOutput(files={"server.js": "// backend"}),
            FrontendDevOutput: FrontendDevOutput(files={"App.jsx": "// frontend"}),
            HowToReadmeOutput: HowToReadmeOutput(readme_content="# Demo POV"),
        }
    )


def test_graph_compiles_with_every_new_md_node():
    graph = build_graph(_happy_path_llm(), FakePovRunStore(), FakeGitRepoStore())
    compiled_nodes = set(graph.get_graph().nodes.keys())
    for name in NODE_NAMES:
        assert name in compiled_nodes


def test_default_run_reaches_howto_helper_via_happy_path():
    graph = build_graph(_happy_path_llm(), FakePovRunStore(), FakeGitRepoStore(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-thread"}}
    final_state = _run_to_completion(
        graph,
        {"transcript": "irrelevant for Phase 0 placeholders", "user_email": "a@b.com", "pov_name": "demo"},
        config,
    )
    statuses = final_state["agent_statuses"]
    # Every placeholder node ran, including all three parallel ones — this
    # is the real proof the agent_statuses merge reducer works: if it
    # didn't, one of the three parallel writers' status would be missing
    # (last-write-wins) or the graph would have raised InvalidUpdateError.
    for name in NODE_NAMES:
        if name == "repair_router":
            continue  # not reached on the default PASS path
        assert name in statuses, f"{name} never ran"
        assert statuses[name].status == AgentRunStatus.SUCCEEDED

    assert final_state["readme_status"].committed is True
    assert len(final_state["repository"].commits) == 3  # seed + backend + frontend
    # repo_name/branch (provisioned once by spec_architect) must survive
    # integration_validator's later rewrite of `repository` — this was a
    # real bug: integration_validator's RepositoryInfo reconstruction
    # forgot to carry it forward before this test caught it.
    assert final_state["repository"].repo_name == "pov-builder"
    assert final_state["repository"].branch == "a@b.com/demo"


def test_initial_spec_gate_payload_includes_pov_reviewers_review_result():
    from pov_builder.models.review import ReviewIssue, ReviewIssueSeverity

    review = POVReviewResult(
        status=ReviewStatus.PASS_WITH_WARNINGS,
        issues=[
            ReviewIssue(
                id="ISSUE-1",
                severity=ReviewIssueSeverity.LOW,
                category="missing_personas",
                description="no personas extracted",
                recommended_action="add a persona",
            )
        ],
    )
    llm = FakeLLM(
        by_schema={InitialPOVSpec: InitialPOVSpec(), POVReviewResult: review}
    )
    graph = build_graph(llm, FakePovRunStore(), FakeGitRepoStore(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "review-result-payload-test"}}

    result = graph.invoke({"transcript": "some transcript", "user_email": "a@b.com", "pov_name": "demo"}, config=config)

    payload = result["__interrupt__"][0].value
    assert payload["review_result"] is not None
    assert payload["review_result"]["status"] == "PASS_WITH_WARNINGS"
    assert payload["review_result"]["issues"][0]["id"] == "ISSUE-1"


def test_revising_the_initial_spec_gate_reruns_transcript_analyzer_with_feedback_and_keeps_pov_id():
    llm = _happy_path_llm()
    pov_store = FakePovRunStore()
    graph = build_graph(llm, pov_store, FakeGitRepoStore(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "revise-thread"}}

    result = graph.invoke({"transcript": "some transcript", "user_email": "a@b.com", "pov_name": "demo"}, config=config)
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["gate"] == "initial_spec"
    pov_id_before_revise = result["pov_id"]

    # Request a revision instead of approving.
    result = graph.invoke(
        Command(resume={"decision": "revise", "feedback": "add more personas"}), config=config
    )
    # transcript_analyzer re-ran (real path, since transcript is non-empty)
    # and we're back at the SAME gate again — not somewhere else.
    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["gate"] == "initial_spec"
    assert result["pov_id"] == pov_id_before_revise  # never re-minted
    assert len(pov_store.calls) == 2  # ran transcript_analyzer twice
    assert pov_store.calls[1]["pov_id"] == pov_id_before_revise  # second call reused it
    assert result["spec_feedback"] == ["add more personas"]

    # Feedback reaching the prompt itself is covered by
    # test_transcript_analyzer.py (prompt-content tests) — what this test
    # protects is that the STATE actually carries it forward across the
    # revise loop, which is the part a prompt-content test can't see.

    # Now approve — should proceed past the gate.
    result = _run_to_completion(graph, Command(resume={"decision": "approve"}), config)
    assert result["readme_status"].committed is True


def test_rejected_review_notes_accumulate_across_revise_rounds_and_reach_pov_reviewer():
    """The fix for the "endless loop" problem: a human's per-issue Reject
    click must actually stop pov_reviewer from re-flagging that exact
    concern on the NEXT round, since pov_reviewer otherwise starts every
    round from a blank slate."""
    captured_messages: list = []

    class _CapturingReviewLLM:
        def with_structured_output(self, schema, **kwargs):
            from pov_builder.models.pov_spec import InitialPOVSpec
            from pov_builder.models.review import POVReviewResult

            if schema is InitialPOVSpec:
                return _Fixed(InitialPOVSpec())
            if schema is POVReviewResult:
                return _CapturingReview(captured_messages)
            return _Fixed(None)

    class _Fixed:
        def __init__(self, value):
            self._value = value

        def invoke(self, messages):
            return self._value

    class _CapturingReview:
        def __init__(self, sink):
            self._sink = sink

        def invoke(self, messages):
            from pov_builder.models.review import POVReviewResult, ReviewStatus

            self._sink.append(messages)
            return POVReviewResult(status=ReviewStatus.PASS_WITH_WARNINGS)

    graph = build_graph(_CapturingReviewLLM(), FakePovRunStore(), FakeGitRepoStore(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "rejected-notes-thread"}}

    graph.invoke({"transcript": "some transcript", "user_email": "a@b.com", "pov_name": "demo"}, config=config)

    # First revise round: reject one specific concern.
    result = graph.invoke(
        Command(
            resume={
                "decision": "revise",
                "feedback": "add more personas",
                "rejected_notes": ["a dismissed concern about scope creep"],
            }
        ),
        config=config,
    )
    assert result["rejected_review_notes"] == ["a dismissed concern about scope creep"]

    # pov_reviewer ran a SECOND time (after the revise) — its prompt must
    # already contain the dismissed concern from THIS round's decision by
    # the time it next runs again below.
    result = graph.invoke(
        Command(resume={"decision": "revise", "feedback": "still needs work"}), config=config
    )
    # Nothing new rejected this round — the prior round's note must
    # persist (not get wiped) rather than requiring the human to re-reject
    # the same thing every single round.
    assert result["rejected_review_notes"] == ["a dismissed concern about scope creep"]

    # The most recent pov_reviewer call (reached via this second revise)
    # must have seen the dismissed note in its prompt.
    combined = " ".join(str(m.content) for m in captured_messages[-1])
    assert "a dismissed concern about scope creep" in combined


def test_route_after_pov_reviewer_pass_goes_to_initial_spec_approval_gate():
    state = {"review_result": POVReviewResult(status=ReviewStatus.PASS)}
    assert route_after_pov_reviewer(state) == "initial_spec_approval_gate"


def test_route_after_pov_reviewer_fail_ends_the_run():
    state = {"review_result": POVReviewResult(status=ReviewStatus.FAIL)}
    assert route_after_pov_reviewer(state) == "end"


def test_route_after_initial_spec_gate_approve_goes_to_spec_architect():
    assert route_after_initial_spec_gate({"initial_spec_decision": "approve"}) == "spec_architect"


def test_route_after_initial_spec_gate_revise_loops_back_to_transcript_analyzer():
    assert route_after_initial_spec_gate({"initial_spec_decision": "revise"}) == "transcript_analyzer"


def test_route_after_technical_spec_gate_approve_fans_out_to_all_three():
    destinations = route_after_technical_spec_gate({"technical_spec_decision": "approve"})
    assert set(destinations) == {"seeder", "backend_dev", "frontend_dev"}


def test_route_after_technical_spec_gate_revise_loops_back_to_spec_architect():
    assert route_after_technical_spec_gate({"technical_spec_decision": "revise"}) == ["spec_architect"]


def test_route_after_integration_validator_pass_goes_to_howto_helper():
    state = {"validation_report": ValidationReport(status=ValidationStatus.PASS)}
    assert route_after_integration_validator(state) == "howto_helper"


def test_route_after_integration_validator_fail_goes_to_repair_router():
    state = {
        "validation_report": ValidationReport(
            status=ValidationStatus.FAIL, repair_required=True, repair_targets=[RepairTarget.BACKEND_DEV]
        )
    }
    assert route_after_integration_validator(state) == "repair_router"


def test_route_after_repair_router_fans_out_to_named_targets():
    state = {
        "repair_info": RepairInfo(
            attempts=[
                RepairAttempt(
                    attempt_number=1, targets=[RepairTarget.BACKEND_DEV, RepairTarget.FRONTEND_DEV]
                )
            ],
            max_attempts=3,
        )
    }
    destinations = route_after_repair_router(state)
    assert set(destinations) == {"backend_dev", "frontend_dev"}


def test_route_after_repair_router_falls_back_to_revalidate_with_no_concrete_targets():
    state = {"repair_info": RepairInfo(attempts=[RepairAttempt(attempt_number=1, targets=[])])}
    assert route_after_repair_router(state) == ["integration_validator"]


def test_route_after_repair_router_stops_when_cap_exceeded():
    state = {"repair_info": RepairInfo(status=RepairLoopStatus.HUMAN_REVIEW_REQUIRED)}
    assert route_after_repair_router(state) == ["end"]
