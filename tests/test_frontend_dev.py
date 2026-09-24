"""Tests for new.md Phase 6 — frontend_dev.

Same honesty boundary as seeder/backend_dev's tests: deterministic wiring
(reading the frontend_contract/api_contract back, committing under
frontend/, and assembling a real ComponentCommit) is unit-testable without
a live LLM/GitHub call; whether the generated frontend is actually correct
code needs a real run — see README "How to execute the graph".
"""

from __future__ import annotations

from datetime import datetime, timezone

from pov_builder.graph.nodes import make_frontend_dev
from pov_builder.models.common import AgentRunStatus
from pov_builder.models.repair import RepairAttempt, RepairInfo
from pov_builder.models.repository import ComponentCommit, RepositoryInfo
from pov_builder.models.technical_spec import ContractRef, DetailedTechnicalSpec
from pov_builder.models.validation import (
    RepairTarget,
    ValidationCategory,
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)
from pov_builder.prompts.frontend_dev import SYSTEM_PROMPT, FrontendDevOutput, build_messages

from tests.fakes import FakeGitRepoStore, FakeLLM


def test_missing_frontend_contract_short_circuits_without_calling_llm_or_git():
    llm = FakeLLM(structured_result=FrontendDevOutput(start_command="npm run dev"))
    git = FakeGitRepoStore()
    node = make_frontend_dev(llm, git)

    result = node(
        {
            "technical_spec": DetailedTechnicalSpec(),
            "repository": RepositoryInfo(repo_name="pov-builder"),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    commit = result["frontend_dev_commit"]
    assert commit.commit_sha == "0000000"  # placeholder, canned LLM result never used
    assert git.commit_code_files_calls == []
    assert result["agent_statuses"]["frontend_dev"].status == AgentRunStatus.SUCCEEDED


def test_missing_repo_name_short_circuits():
    spec = DetailedTechnicalSpec(
        frontend_contract=ContractRef(path="spec_architect/frontend_contract.json"),
        api_contract=ContractRef(path="spec_architect/api_contract.json"),
    )
    node = make_frontend_dev(FakeLLM(), FakeGitRepoStore())

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name=None),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    assert result["frontend_dev_commit"].commit_sha == "0000000"


def test_missing_email_or_pov_name_short_circuits():
    spec = DetailedTechnicalSpec(
        frontend_contract=ContractRef(path="spec_architect/frontend_contract.json"),
        api_contract=ContractRef(path="spec_architect/api_contract.json"),
    )
    node = make_frontend_dev(FakeLLM(), FakeGitRepoStore())

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name="pov-builder"),
            "user_email": "a@b.com",
            "pov_name": "",
        }
    )

    assert result["frontend_dev_commit"].commit_sha == "0000000"


def test_real_path_reads_frontend_contract_and_api_contract_commits_under_frontend_prefix():
    frontend_contract_path = "spec_architect/frontend_contract.json"
    api_contract_path = "spec_architect/api_contract.json"
    spec = DetailedTechnicalSpec(
        frontend_contract=ContractRef(path=frontend_contract_path, commit_sha="abc123"),
        api_contract=ContractRef(path=api_contract_path, commit_sha="def456"),
    )
    git = FakeGitRepoStore(
        specs_contracts_content={
            frontend_contract_path: '{"routes": ["/orders"]}',
            api_contract_path: '{"paths": {"/orders": {}}}',
        }
    )
    raw = FrontendDevOutput(
        files={"src/App.jsx": "// real frontend", "package.json": "{}"},
        start_command="npm run dev",
        environment_variables=["API_BASE_URL"],
        routes_implemented=["/orders"],
        apis_consumed=["GET /orders"],
    )
    node = make_frontend_dev(FakeLLM(structured_result=raw), git)

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/order-status-chatbot"),
            "user_email": "a@b.com",
            "pov_name": "order-status-chatbot",
        }
    )

    # Read the actual committed frontend_contract/api_contract back, not re-decided them.
    assert len(git.commit_code_files_calls) == 1
    call = git.commit_code_files_calls[0]
    assert call["email"] == "a@b.com"
    assert call["pov_name"] == "order-status-chatbot"
    assert set(call["files"].keys()) == {"frontend/src/App.jsx", "frontend/package.json"}

    commit = result["frontend_dev_commit"]
    assert commit.component == "frontend"
    assert commit.files_changed == ["frontend/src/App.jsx", "frontend/package.json"]
    assert commit.details["start_command"] == "npm run dev"
    assert commit.details["routes_implemented"] == ["/orders"]
    assert commit.details["apis_consumed"] == ["GET /orders"]


class _CapturingLLM:
    def __init__(self, result: FrontendDevOutput):
        self._result = result
        self.captured_messages: list = []

    def with_structured_output(self, schema, **kwargs):
        outer = self

        class _Runnable:
            def invoke(self, messages):
                outer.captured_messages.append(messages)
                return outer._result

        return _Runnable()


def _repair_state(*, extra_checks=None):
    frontend_contract_path = "spec_architect/frontend_contract.json"
    api_contract_path = "spec_architect/api_contract.json"
    spec = DetailedTechnicalSpec(
        frontend_contract=ContractRef(path=frontend_contract_path), api_contract=ContractRef(path=api_contract_path)
    )
    prior_commit = ComponentCommit(
        component="frontend",
        commit_sha="prior-sha",
        files_changed=["frontend/src/App.jsx", "frontend/package.json"],
        committed_at=datetime.now(timezone.utc),
    )
    checks = [
        ValidationCheck(
            id="user-journey",
            category=ValidationCategory.USER_JOURNEY,
            status=ValidationStatus.FAIL,
            description="clicking Search never shows results",
            evidence="selector .search-results never became visible",
            affected_component="frontend",
            repair_action="wire the search button to the results list",
        ),
        *(extra_checks or []),
    ]
    report = ValidationReport(
        status=ValidationStatus.FAIL, repair_required=True, repair_targets=[RepairTarget.FRONTEND_DEV], checks=checks
    )
    repair_info = RepairInfo(attempts=[RepairAttempt(attempt_number=1, targets=[RepairTarget.FRONTEND_DEV])])
    return {
        "technical_spec": spec,
        "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/demo"),
        "user_email": "a@b.com",
        "pov_name": "demo",
        "frontend_dev_commit": prior_commit,
        "validation_report": report,
        "repair_info": repair_info,
    }


def test_repair_round_reads_existing_files_and_shows_the_specific_failure():
    git = FakeGitRepoStore(
        specs_contracts_content={
            "spec_architect/frontend_contract.json": "{}",
            "spec_architect/api_contract.json": "{}",
            "frontend/src/App.jsx": "// the REAL currently-committed broken App",
        }
    )
    llm = _CapturingLLM(FrontendDevOutput(files={"src/App.jsx": "// fixed"}, start_command="npm run dev"))
    node = make_frontend_dev(llm, git)

    node(_repair_state())

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "REPAIR MODE" in combined
    assert "the REAL currently-committed broken App" in combined
    assert "clicking Search never shows results" in combined
    assert "wire the search button to the results list" in combined


def test_repair_round_shown_paths_never_get_double_prefixed_on_commit():
    """Regression test — see the mirrored test in test_seeder.py for the
    full explanation: `existing_files` must be shown to the LLM keyed by
    the RELATIVE path ("src/App.jsx"), never the full committed path
    ("frontend/src/App.jsx"), or the LLM's echoed key gets double-prefixed
    on commit and the real, currently-served file never gets patched.
    This is a real bug hit live: `frontend/frontend/src/App.jsx` showed up
    as a dead duplicate alongside the untouched `frontend/src/App.jsx`."""
    git = FakeGitRepoStore(specs_contracts_content={"frontend/src/App.jsx": "// the REAL currently-committed broken App"})
    llm = _CapturingLLM(FrontendDevOutput(files={"src/App.jsx": "// fixed"}, start_command="npm run dev"))
    node = make_frontend_dev(llm, git)

    result = node(_repair_state())

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "--- src/App.jsx ---" in combined
    assert "--- frontend/src/App.jsx ---" not in combined

    call = git.commit_code_files_calls[0]
    assert call["files"] == {"frontend/src/App.jsx": "// fixed"}
    assert "frontend/frontend/src/App.jsx" not in result["frontend_dev_commit"].files_changed


def test_repair_round_excludes_another_components_failures():
    unrelated = ValidationCheck(
        id="database-seed",
        category=ValidationCategory.DATABASE,
        status=ValidationStatus.FAIL,
        description="seed script duplicate key — should never reach frontend_dev's prompt",
        affected_component="seed",
    )
    git = FakeGitRepoStore(specs_contracts_content={"frontend/src/App.jsx": "// existing"})
    llm = _CapturingLLM(FrontendDevOutput(files={"src/App.jsx": "// fixed"}))
    node = make_frontend_dev(llm, git)

    node(_repair_state(extra_checks=[unrelated]))

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "seed script duplicate key" not in combined


def test_repair_round_commits_only_the_sparse_returned_files():
    git = FakeGitRepoStore(specs_contracts_content={"frontend/src/App.jsx": "// existing"})
    llm = _CapturingLLM(FrontendDevOutput(files={"src/App.jsx": "// fixed"}, start_command="npm run dev"))
    node = make_frontend_dev(llm, git)

    result = node(_repair_state())

    call = git.commit_code_files_calls[0]
    assert set(call["files"].keys()) == {"frontend/src/App.jsx"}
    assert result["frontend_dev_commit"].files_changed == ["frontend/src/App.jsx", "frontend/package.json"]
    assert "repair" in result["frontend_dev_commit"].message


def test_prompt_forbids_inventing_functionality_and_hardcoding_data():
    lowered = SYSTEM_PROMPT.lower()
    assert "do not invent additional business functionality" in lowered
    assert "do not hardcode data" in lowered
    assert "specification_conflicts" in lowered


def test_build_messages_includes_frontend_contract_and_api_contract():
    messages = build_messages(
        '{"marker": "very-specific-frontend-contract-content"}',
        '{"marker": "very-specific-api-contract-content"}',
    )
    combined = " ".join(str(m.content) for m in messages)
    assert "very-specific-frontend-contract-content" in combined
    assert "very-specific-api-contract-content" in combined


def test_build_messages_includes_repair_mode_section_when_given():
    check = ValidationCheck(
        id="user-journey",
        category=ValidationCategory.USER_JOURNEY,
        status=ValidationStatus.FAIL,
        description="a very specific frontend failure",
        repair_action="fix it",
    )
    messages = build_messages(
        "{}", "{}", existing_files={"frontend/src/App.jsx": "a very specific existing app"}, failing_checks=[check]
    )
    combined = " ".join(str(m.content) for m in messages)
    assert "REPAIR MODE" in combined
    assert "a very specific existing app" in combined
    assert "a very specific frontend failure" in combined


def test_prompt_declares_both_api_base_url_names_for_vite_compatibility():
    for name in ("PORT", "API_BASE_URL", "VITE_API_BASE_URL"):
        assert name in SYSTEM_PROMPT
    assert "vite" in SYSTEM_PROMPT.lower()


def test_prompt_declares_universal_testids_and_key_element_testids_field():
    lowered = SYSTEM_PROMPT.lower()
    assert "page-title" in lowered
    assert "loading-indicator" in lowered
    assert "key_element_testids" in lowered


def test_prompt_declares_list_item_testid_prefixes_and_warns_against_literal_templates():
    lowered = SYSTEM_PROMPT.lower()
    assert "list_item_testid_prefixes" in lowered
    assert "{ticketid}" in lowered
    assert "never report a literal template" in lowered


def test_real_path_stores_key_element_testids_in_commit_details():
    api_contract_path = "spec_architect/api_contract.json"
    frontend_contract_path = "spec_architect/frontend_contract.json"
    spec = DetailedTechnicalSpec(
        frontend_contract=ContractRef(path=frontend_contract_path), api_contract=ContractRef(path=api_contract_path)
    )
    git = FakeGitRepoStore(
        specs_contracts_content={frontend_contract_path: "{}", api_contract_path: "{}"}
    )
    raw = FrontendDevOutput(
        files={"src/App.jsx": "// app"},
        start_command="npm run dev",
        key_element_testids={"search_button": "ticket-search-btn", "results_list": "search-results"},
    )
    node = make_frontend_dev(FakeLLM(structured_result=raw), git)

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/demo"),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    assert result["frontend_dev_commit"].details["key_element_testids"] == {
        "search_button": "ticket-search-btn",
        "results_list": "search-results",
    }


def test_real_path_stores_list_item_testid_prefixes_in_commit_details():
    api_contract_path = "spec_architect/api_contract.json"
    frontend_contract_path = "spec_architect/frontend_contract.json"
    spec = DetailedTechnicalSpec(
        frontend_contract=ContractRef(path=frontend_contract_path), api_contract=ContractRef(path=api_contract_path)
    )
    git = FakeGitRepoStore(
        specs_contracts_content={frontend_contract_path: "{}", api_contract_path: "{}"}
    )
    raw = FrontendDevOutput(
        files={"src/App.jsx": "// app"},
        start_command="npm run dev",
        list_item_testid_prefixes={"open_ticket_button": "open-ticket-"},
    )
    node = make_frontend_dev(FakeLLM(structured_result=raw), git)

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/demo"),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    assert result["frontend_dev_commit"].details["list_item_testid_prefixes"] == {
        "open_ticket_button": "open-ticket-",
    }
