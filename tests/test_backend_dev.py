"""Tests for new.md Phase 5 — backend_dev.

Same honesty boundary as seeder's tests: deterministic wiring (reading the
api_contract/data_model back, committing under backend/, and assembling a
real ComponentCommit) is unit-testable without a live LLM/GitHub call;
whether the generated backend is actually correct code needs a real run —
see README "How to execute the graph".
"""

from __future__ import annotations

from datetime import datetime, timezone

from pov_builder.graph.nodes import make_backend_dev
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
from pov_builder.prompts.backend_dev import SYSTEM_PROMPT, BackendDevOutput, build_messages

from tests.fakes import FakeGitRepoStore, FakeLLM


def test_missing_api_contract_short_circuits_without_calling_llm_or_git():
    llm = FakeLLM(structured_result=BackendDevOutput(start_command="node server.js"))
    git = FakeGitRepoStore()
    node = make_backend_dev(llm, git)

    result = node(
        {
            "technical_spec": DetailedTechnicalSpec(),
            "repository": RepositoryInfo(repo_name="pov-builder"),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    commit = result["backend_dev_commit"]
    assert commit.commit_sha == "0000000"  # placeholder, canned LLM result never used
    assert git.commit_code_files_calls == []
    assert result["agent_statuses"]["backend_dev"].status == AgentRunStatus.SUCCEEDED


def test_missing_repo_name_short_circuits():
    spec = DetailedTechnicalSpec(
        api_contract=ContractRef(path="spec_architect/api_contract.json"),
        data_model=ContractRef(path="spec_architect/data_model.json"),
    )
    node = make_backend_dev(FakeLLM(), FakeGitRepoStore())

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name=None),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    assert result["backend_dev_commit"].commit_sha == "0000000"


def test_missing_email_or_pov_name_short_circuits():
    spec = DetailedTechnicalSpec(
        api_contract=ContractRef(path="spec_architect/api_contract.json"),
        data_model=ContractRef(path="spec_architect/data_model.json"),
    )
    node = make_backend_dev(FakeLLM(), FakeGitRepoStore())

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name="pov-builder"),
            "user_email": "",
            "pov_name": "demo",
        }
    )

    assert result["backend_dev_commit"].commit_sha == "0000000"


def test_real_path_reads_api_contract_and_data_model_commits_under_backend_prefix():
    api_contract_path = "spec_architect/api_contract.json"
    data_model_path = "spec_architect/data_model.json"
    spec = DetailedTechnicalSpec(
        api_contract=ContractRef(path=api_contract_path, commit_sha="abc123"),
        data_model=ContractRef(path=data_model_path, commit_sha="def456"),
    )
    git = FakeGitRepoStore(
        specs_contracts_content={
            api_contract_path: '{"paths": {"/orders": {}}}',
            data_model_path: '{"collections": ["orders"]}',
        }
    )
    raw = BackendDevOutput(
        files={"server.js": "// real backend", "package.json": "{}"},
        start_command="node server.js",
        environment_variables=["MONGODB_URI"],
        endpoints_implemented=["GET /orders"],
        health_endpoint="/health",
    )
    node = make_backend_dev(FakeLLM(structured_result=raw), git)

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/order-status-chatbot"),
            "user_email": "a@b.com",
            "pov_name": "order-status-chatbot",
        }
    )

    # Read the actual committed api_contract/data_model back, not re-decided them.
    assert len(git.commit_code_files_calls) == 1
    call = git.commit_code_files_calls[0]
    assert call["email"] == "a@b.com"
    assert call["pov_name"] == "order-status-chatbot"
    assert set(call["files"].keys()) == {"backend/server.js", "backend/package.json"}

    commit = result["backend_dev_commit"]
    assert commit.component == "backend"
    assert commit.files_changed == ["backend/server.js", "backend/package.json"]
    assert commit.details["start_command"] == "node server.js"
    assert commit.details["endpoints_implemented"] == ["GET /orders"]
    assert commit.details["health_endpoint"] == "/health"


class _CapturingLLM:
    def __init__(self, result: BackendDevOutput):
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
    api_contract_path = "spec_architect/api_contract.json"
    data_model_path = "spec_architect/data_model.json"
    spec = DetailedTechnicalSpec(
        api_contract=ContractRef(path=api_contract_path), data_model=ContractRef(path=data_model_path)
    )
    prior_commit = ComponentCommit(
        component="backend",
        commit_sha="prior-sha",
        files_changed=["backend/server.js", "backend/package.json"],
        committed_at=datetime.now(timezone.utc),
    )
    checks = [
        ValidationCheck(
            id="api-health",
            category=ValidationCategory.API,
            status=ValidationStatus.FAIL,
            description="health endpoint unreachable on port 5001",
            evidence="connection refused",
            affected_component="backend",
            repair_action="fix startup crash",
        ),
        *(extra_checks or []),
    ]
    report = ValidationReport(
        status=ValidationStatus.FAIL, repair_required=True, repair_targets=[RepairTarget.BACKEND_DEV], checks=checks
    )
    repair_info = RepairInfo(attempts=[RepairAttempt(attempt_number=1, targets=[RepairTarget.BACKEND_DEV])])
    return {
        "technical_spec": spec,
        "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/demo"),
        "user_email": "a@b.com",
        "pov_name": "demo",
        "backend_dev_commit": prior_commit,
        "validation_report": report,
        "repair_info": repair_info,
    }


def test_repair_round_reads_existing_files_and_shows_the_specific_failure():
    git = FakeGitRepoStore(
        specs_contracts_content={
            "spec_architect/api_contract.json": "{}",
            "spec_architect/data_model.json": "{}",
            "backend/server.js": "// the REAL currently-committed crashing server",
        }
    )
    llm = _CapturingLLM(BackendDevOutput(files={"server.js": "// fixed"}, start_command="node server.js"))
    node = make_backend_dev(llm, git)

    node(_repair_state())

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "REPAIR MODE" in combined
    assert "the REAL currently-committed crashing server" in combined
    assert "health endpoint unreachable on port 5001" in combined
    assert "fix startup crash" in combined


def test_repair_round_shown_paths_never_get_double_prefixed_on_commit():
    """Regression test — see the mirrored test in test_seeder.py for the
    full explanation: `existing_files` must be shown to the LLM keyed by
    the RELATIVE path ("server.js"), never the full committed path
    ("backend/server.js"), or the LLM's echoed key gets double-prefixed
    on commit and the real, currently-run file never gets patched."""
    git = FakeGitRepoStore(specs_contracts_content={"backend/server.js": "// the REAL currently-committed crashing server"})
    llm = _CapturingLLM(BackendDevOutput(files={"server.js": "// fixed"}, start_command="node server.js"))
    node = make_backend_dev(llm, git)

    result = node(_repair_state())

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "--- server.js ---" in combined
    assert "--- backend/server.js ---" not in combined

    call = git.commit_code_files_calls[0]
    assert call["files"] == {"backend/server.js": "// fixed"}
    assert "backend/backend/server.js" not in result["backend_dev_commit"].files_changed


def test_repair_round_excludes_another_components_failures():
    unrelated = ValidationCheck(
        id="database-seed",
        category=ValidationCategory.DATABASE,
        status=ValidationStatus.FAIL,
        description="seed script duplicate key — should never reach backend_dev's prompt",
        affected_component="seed",
    )
    git = FakeGitRepoStore(specs_contracts_content={"backend/server.js": "// existing"})
    llm = _CapturingLLM(BackendDevOutput(files={"server.js": "// fixed"}))
    node = make_backend_dev(llm, git)

    node(_repair_state(extra_checks=[unrelated]))

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "seed script duplicate key" not in combined


def test_repair_round_commits_only_the_sparse_returned_files():
    git = FakeGitRepoStore(specs_contracts_content={"backend/server.js": "// existing"})
    llm = _CapturingLLM(BackendDevOutput(files={"server.js": "// fixed"}, start_command="node server.js"))
    node = make_backend_dev(llm, git)

    result = node(_repair_state())

    call = git.commit_code_files_calls[0]
    assert set(call["files"].keys()) == {"backend/server.js"}
    assert result["backend_dev_commit"].files_changed == ["backend/server.js", "backend/package.json"]
    assert "repair" in result["backend_dev_commit"].message


def test_prompt_forbids_redesigning_the_api_contract_or_data_model():
    lowered = SYSTEM_PROMPT.lower()
    assert "do not rename endpoints" in lowered
    assert "specification_conflicts" in lowered


def test_prompt_requires_health_endpoint_and_env_config():
    lowered = SYSTEM_PROMPT.lower()
    assert "health endpoint" in lowered
    assert "environment variables" in lowered


def test_build_messages_includes_api_contract_and_data_model():
    messages = build_messages(
        '{"marker": "very-specific-api-contract-content"}',
        '{"marker": "very-specific-data-model-content"}',
    )
    combined = " ".join(str(m.content) for m in messages)
    assert "very-specific-api-contract-content" in combined
    assert "very-specific-data-model-content" in combined


def test_build_messages_includes_repair_mode_section_when_given():
    check = ValidationCheck(
        id="api-health",
        category=ValidationCategory.API,
        status=ValidationStatus.FAIL,
        description="a very specific backend failure",
        repair_action="fix it",
    )
    messages = build_messages(
        "{}", "{}", existing_files={"backend/server.js": "a very specific existing server"}, failing_checks=[check]
    )
    combined = " ".join(str(m.content) for m in messages)
    assert "REPAIR MODE" in combined
    assert "a very specific existing server" in combined
    assert "a very specific backend failure" in combined


def test_prompt_declares_backend_env_vars_including_require_auth():
    for name in ("MONGODB_URI", "MONGODB_DB", "PORT", "REQUIRE_AUTH"):
        assert name in SYSTEM_PROMPT


def test_prompt_declares_auth_token_and_forbids_other_vars():
    lowered = SYSTEM_PROMPT.lower()
    assert "auth_token" in lowered
    assert "do not read any other" in lowered
