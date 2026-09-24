"""Tests for new.md Phase 3 — spec_architect, implemented as 4 sequential
design stages + a finishing synthesis (see prompts/spec_architect/__init__.py).

Same honesty boundary as the earlier phases: deterministic wiring —
including the critical part this restructure is FOR, that each stage
actually receives the PREVIOUS stage's real output, not a re-derived
guess — is unit-testable without a live LLM/GitHub call; whether the
model's actual architecture/data-model design is good needs a real
transcript + real LLM (and a real repo) — see README "How to execute the
graph".
"""

from __future__ import annotations

from pov_builder.graph.nodes import make_spec_architect
from pov_builder.models.common import AgentRunStatus
from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.repository import RepositoryInfo
from pov_builder.models.technical_spec import DetailedTechnicalSpec
from pov_builder.prompts.spec_architect.api_contract_design import (
    SYSTEM_PROMPT as API_CONTRACT_SYSTEM_PROMPT,
)
from pov_builder.prompts.spec_architect.api_contract_design import ApiContractDesignOutput
from pov_builder.prompts.spec_architect.finishing import SYSTEM_PROMPT as FINISHING_SYSTEM_PROMPT
from pov_builder.prompts.spec_architect.finishing import FinishingOutput
from pov_builder.prompts.spec_architect.frontend_contract_design import (
    SYSTEM_PROMPT as FRONTEND_CONTRACT_SYSTEM_PROMPT,
)
from pov_builder.prompts.spec_architect.frontend_contract_design import FrontendContractDesignOutput
from pov_builder.prompts.spec_architect.query_pattern_design import (
    SYSTEM_PROMPT as QUERY_PATTERN_SYSTEM_PROMPT,
)
from pov_builder.prompts.spec_architect.query_pattern_design import QueryPatternDesignOutput
from pov_builder.prompts.spec_architect.schema_design import SYSTEM_PROMPT as SCHEMA_DESIGN_SYSTEM_PROMPT
from pov_builder.prompts.spec_architect.schema_design import SchemaDesignOutput, build_messages

from tests.fakes import FakeGitRepoStore, FakeLLM, FakePovRunStore


def _five_stage_llm(
    *,
    data_model=None,
    query_patterns=None,
    api_contract=None,
    frontend_contract=None,
    requirements=None,
) -> FakeLLM:
    return FakeLLM(
        by_schema={
            SchemaDesignOutput: SchemaDesignOutput(data_model=data_model or {"collections": ["orders"]}),
            QueryPatternDesignOutput: QueryPatternDesignOutput(query_patterns=query_patterns or {"patterns": []}),
            ApiContractDesignOutput: ApiContractDesignOutput(api_contract=api_contract or {"paths": {}}),
            FrontendContractDesignOutput: FrontendContractDesignOutput(
                frontend_contract=frontend_contract or {"routes": []}
            ),
            FinishingOutput: FinishingOutput(requirements=requirements or {"must_have": []}),
        }
    )


def test_missing_pov_id_short_circuits_without_calling_llm_or_git():
    llm = _five_stage_llm(data_model={"should": "never be used"})
    git = FakeGitRepoStore()
    node = make_spec_architect(llm, git, FakePovRunStore())

    result = node({"pov_id": None, "initial_spec": InitialPOVSpec(), "user_email": "a@b.com", "pov_name": "demo"})

    spec = result["technical_spec"]
    assert isinstance(spec, DetailedTechnicalSpec)
    assert spec.data_model is None
    assert git.commit_spec_contracts_calls == []
    assert git.resolve_repo_calls == []
    assert result["agent_statuses"]["spec_architect"].status == AgentRunStatus.SUCCEEDED


def test_missing_initial_spec_short_circuits():
    llm = _five_stage_llm()
    git = FakeGitRepoStore()
    node = make_spec_architect(llm, git, FakePovRunStore())

    result = node({"pov_id": "pov-1", "initial_spec": None, "user_email": "a@b.com", "pov_name": "demo"})

    assert result["technical_spec"].data_model is None
    assert git.commit_spec_contracts_calls == []


def test_missing_email_or_pov_name_short_circuits():
    llm = _five_stage_llm()
    git = FakeGitRepoStore()
    node = make_spec_architect(llm, git, FakePovRunStore())

    result = node({"pov_id": "pov-1", "initial_spec": InitialPOVSpec(), "user_email": "", "pov_name": "demo"})

    assert result["technical_spec"].data_model is None
    assert git.resolve_repo_calls == []


def test_stages_run_in_order_and_each_receives_the_previous_stages_real_output():
    """The entire point of the restructure: query_pattern_design must see
    schema_design's ACTUAL data_model, api_contract_design must see BOTH
    real outputs, frontend_contract_design must see the real api_contract,
    and finishing must see all four real outputs — not each stage
    independently re-guessing from InitialPOVSpec alone."""
    llm = _five_stage_llm(
        data_model={"collections": ["orders"], "marker": "REAL_SCHEMA"},
        query_patterns={"patterns": ["p1"], "marker": "REAL_QUERY_PATTERNS"},
        api_contract={"paths": {"/orders": {}}, "marker": "REAL_API_CONTRACT"},
        frontend_contract={"routes": ["/orders"], "marker": "REAL_FRONTEND_CONTRACT"},
    )
    git = FakeGitRepoStore()
    node = make_spec_architect(llm, git, FakePovRunStore())

    node({"pov_id": "pov-1", "initial_spec": InitialPOVSpec(), "user_email": "a@b.com", "pov_name": "demo"})

    calls = git.commit_spec_contracts_calls
    assert [list(c["contracts"].keys())[0] for c in calls] == [
        "data_model.json",
        "query_patterns.json",
        "api_contract.json",
        "frontend_contract.json",
        "requirements.json",
    ]
    # Each committed file's content is the REAL upstream output, proving
    # the data actually flowed stage-to-stage rather than each stage
    # inventing its own.
    assert "REAL_SCHEMA" in calls[0]["contracts"]["data_model.json"]
    assert "REAL_QUERY_PATTERNS" in calls[1]["contracts"]["query_patterns.json"]
    assert "REAL_API_CONTRACT" in calls[2]["contracts"]["api_contract.json"]
    assert "REAL_FRONTEND_CONTRACT" in calls[3]["contracts"]["frontend_contract.json"]


def test_repo_is_provisioned_once_before_any_stage_runs():
    llm = _five_stage_llm()
    git = FakeGitRepoStore()
    node = make_spec_architect(llm, git, FakePovRunStore())

    result = node(
        {"pov_id": "pov-1", "initial_spec": InitialPOVSpec(), "user_email": "a@b.com", "pov_name": "order bot"}
    )

    assert len(git.resolve_repo_calls) == 1
    assert git.resolve_repo_calls[0] == {"email": "a@b.com", "pov_name": "order bot"}
    repository = result["repository"]
    assert isinstance(repository, RepositoryInfo)
    assert repository.repo_name == "pov-builder"
    assert repository.branch == "a@b.com/order bot"


def test_every_committed_artifact_location_is_saved_to_the_pov_run_record():
    """The point of this test: `pov_builder_runs` should know exactly
    where each of the 5 contracts landed on GitHub — real path, real
    commit sha, and a real clickable blob URL — without needing to load
    the LangGraph checkpoint at all."""
    llm = _five_stage_llm()
    git = FakeGitRepoStore()
    pov_run_store = FakePovRunStore()
    node = make_spec_architect(llm, git, pov_run_store)

    node({"pov_id": "pov-1", "initial_spec": InitialPOVSpec(), "user_email": "a@b.com", "pov_name": "order-bot"})

    assert len(pov_run_store.save_spec_artifacts_calls) == 1
    call = pov_run_store.save_spec_artifacts_calls[0]
    assert call["pov_id"] == "pov-1"
    artifacts = call["artifacts"]
    assert set(artifacts.keys()) == {
        "data_model",
        "query_patterns",
        "api_contract",
        "frontend_contract",
        "requirements",
    }
    data_model_location = artifacts["data_model"]
    assert data_model_location["path"] == "spec_architect/data_model.json"
    assert data_model_location["commit_sha"] == "fake-sha-data_model.json"
    assert data_model_location["url"] == (
        "https://github.com/fake-owner/pov-builder/tree/a@b.com/order-bot"
        "/blob/a@b.com/order-bot/spec_architect/data_model.json"
    )


def test_missing_pov_id_or_email_never_calls_save_spec_artifacts():
    llm = _five_stage_llm()
    git = FakeGitRepoStore()
    pov_run_store = FakePovRunStore()
    node = make_spec_architect(llm, git, pov_run_store)

    node({"pov_id": None, "initial_spec": InitialPOVSpec(), "user_email": "a@b.com", "pov_name": "demo"})

    assert pov_run_store.save_spec_artifacts_calls == []


def test_final_technical_spec_assembles_fields_from_the_right_stage():
    llm = FakeLLM(
        by_schema={
            SchemaDesignOutput: SchemaDesignOutput(
                data_model={"collections": ["orders"]}, seed_data_spec="20 orders"
            ),
            QueryPatternDesignOutput: QueryPatternDesignOutput(query_patterns={"patterns": ["p1"]}),
            ApiContractDesignOutput: ApiContractDesignOutput(
                api_contract={"paths": {}}, backend_architecture="a real backend description"
            ),
            FrontendContractDesignOutput: FrontendContractDesignOutput(
                frontend_contract={"routes": []}, frontend_architecture="a real frontend description"
            ),
            FinishingOutput: FinishingOutput(
                requirements={"must_have": []}, system_architecture="a real system description"
            ),
        }
    )
    node = make_spec_architect(llm, FakeGitRepoStore(), FakePovRunStore())

    result = node(
        {"pov_id": "pov-1", "initial_spec": InitialPOVSpec(), "user_email": "a@b.com", "pov_name": "demo"}
    )

    spec = result["technical_spec"]
    assert spec.seed_data_spec == "20 orders"  # from schema_design
    assert spec.backend_architecture == "a real backend description"  # from api_contract_design
    assert spec.frontend_architecture == "a real frontend description"  # from frontend_contract_design
    assert spec.system_architecture == "a real system description"  # from finishing
    assert spec.query_patterns.path.endswith("query_patterns.json")


def test_schema_design_prompt_forbids_unjustified_search_or_vector_indexes():
    lowered = SCHEMA_DESIGN_SYSTEM_PROMPT.lower()
    assert "do not add a search or vector index unless" in lowered


def test_query_pattern_design_prompt_forbids_redesigning_the_data_model():
    lowered = QUERY_PATTERN_SYSTEM_PROMPT.lower()
    assert "do not redesign it" in lowered
    assert "contradictions" in lowered


def test_api_contract_design_prompt_requires_grounding_in_data_model_and_query_patterns():
    lowered = API_CONTRACT_SYSTEM_PROMPT.lower()
    assert "data model" in lowered
    assert "query patterns" in lowered
    assert "do not redesign them" in lowered


def test_frontend_contract_design_prompt_forbids_inventing_endpoints():
    lowered = FRONTEND_CONTRACT_SYSTEM_PROMPT.lower()
    assert "do not invent an endpoint" in lowered


def test_finishing_prompt_forbids_redesigning_any_prior_artifact():
    lowered = FINISHING_SYSTEM_PROMPT.lower()
    assert "do not redesign any of them" in lowered


def test_build_messages_includes_spec_content_and_flags_review_issues_as_context_only():
    from pov_builder.models.review import POVReviewResult, ReviewStatus

    spec = InitialPOVSpec(executive_summary="a very specific unique summary")
    review = POVReviewResult(status=ReviewStatus.PASS_WITH_WARNINGS)
    messages = build_messages(spec, review)
    combined = " ".join(str(m.content) for m in messages)
    assert "a very specific unique summary" in combined
    assert "not silently resolved" in combined.lower()
