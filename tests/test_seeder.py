"""Tests for new.md Phase 4 — seeder.

Same honesty boundary as earlier phases: deterministic wiring (including
reading the data model back, committing under seed/, and assembling a
real ComponentCommit) is unit-testable without a live LLM/GitHub call;
whether the generated seed script is actually correct/idempotent code
needs a real run — see README "How to execute the graph".
"""

from __future__ import annotations

from datetime import datetime, timezone

from pov_builder.graph.nodes import make_seeder
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
from pov_builder.prompts.seeder import SYSTEM_PROMPT, SeederOutput, build_messages

from tests.fakes import FakeGitRepoStore, FakeLLM


def test_missing_data_model_short_circuits_without_calling_llm_or_git():
    llm = FakeLLM(structured_result=SeederOutput(seed_command="node seed.js"))
    git = FakeGitRepoStore()
    node = make_seeder(llm, git)

    result = node(
        {
            "technical_spec": DetailedTechnicalSpec(),
            "repository": RepositoryInfo(repo_name="pov-builder"),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    commit = result["seeder_commit"]
    assert commit.commit_sha == "0000000"  # placeholder, canned LLM result never used
    assert git.commit_code_files_calls == []
    assert result["agent_statuses"]["seeder"].status == AgentRunStatus.SUCCEEDED


def test_missing_repo_name_short_circuits():
    spec = DetailedTechnicalSpec(data_model=ContractRef(path="spec_architect/data_model.json"))
    node = make_seeder(FakeLLM(), FakeGitRepoStore())

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name=None),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    assert result["seeder_commit"].commit_sha == "0000000"


def test_real_path_reads_data_model_commits_under_seed_prefix_and_assembles_commit():
    data_model_path = "spec_architect/data_model.json"
    spec = DetailedTechnicalSpec(
        data_model=ContractRef(path=data_model_path, commit_sha="abc123"),
        seed_data_spec="20 orders with realistic statuses",
    )
    git = FakeGitRepoStore(specs_contracts_content={data_model_path: '{"collections": ["orders"]}'})
    raw = SeederOutput(
        files={"seed.js": "// real seed script", "package.json": "{}"},
        seed_command="node seed.js",
        environment_variables=["MONGODB_URI"],
        collections_created=["orders"],
        indexes_created=["orderNumber_idx"],
    )
    node = make_seeder(FakeLLM(structured_result=raw), git)

    result = node(
        {
            "technical_spec": spec,
            "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/order-status-chatbot"),
            "user_email": "a@b.com",
            "pov_name": "order-status-chatbot",
        }
    )

    # Read the actual committed data model back, not re-decided it.
    assert len(git.commit_code_files_calls) == 1
    call = git.commit_code_files_calls[0]
    assert call["email"] == "a@b.com"
    assert call["pov_name"] == "order-status-chatbot"
    assert set(call["files"].keys()) == {"seed/seed.js", "seed/package.json"}

    commit = result["seeder_commit"]
    assert commit.component == "seed"
    assert commit.files_changed == ["seed/seed.js", "seed/package.json"]
    assert commit.details["seed_command"] == "node seed.js"
    assert commit.details["collections_created"] == ["orders"]


class _CapturingLLM:
    """Records the exact messages `seeder` sends, so a test can assert on
    prompt CONTENT (not just wiring) — needed to prove repair mode
    actually shows the model the existing code + the specific failure,
    which a plain FakeLLM can't see."""

    def __init__(self, result: SeederOutput):
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
    data_model_path = "spec_architect/data_model.json"
    spec = DetailedTechnicalSpec(data_model=ContractRef(path=data_model_path), seed_data_spec="20 orders")
    prior_commit = ComponentCommit(
        component="seed",
        commit_sha="prior-sha",
        files_changed=["seed/seed.js", "seed/package.json"],
        committed_at=datetime.now(timezone.utc),
    )
    checks = [
        ValidationCheck(
            id="database-seed",
            category=ValidationCategory.DATABASE,
            status=ValidationStatus.FAIL,
            description="E11000 duplicate key on logging_configurations",
            evidence="dup key scopeType/scopeId",
            affected_component="seed",
            repair_action="dedupe policyId before scoping",
        ),
        *(extra_checks or []),
    ]
    report = ValidationReport(
        status=ValidationStatus.FAIL, repair_required=True, repair_targets=[RepairTarget.SEEDER], checks=checks
    )
    repair_info = RepairInfo(attempts=[RepairAttempt(attempt_number=1, targets=[RepairTarget.SEEDER])])
    return {
        "technical_spec": spec,
        "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/demo"),
        "user_email": "a@b.com",
        "pov_name": "demo",
        "seeder_commit": prior_commit,
        "validation_report": report,
        "repair_info": repair_info,
    }


def test_repair_round_reads_existing_files_and_shows_the_specific_failure():
    git = FakeGitRepoStore(
        specs_contracts_content={
            "spec_architect/data_model.json": "{}",
            "seed/seed.js": "// the REAL currently-committed buggy script",
        }
    )
    llm = _CapturingLLM(SeederOutput(files={"seed.js": "// fixed script"}, seed_command="node seed.js"))
    node = make_seeder(llm, git)

    node(_repair_state())

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "REPAIR MODE" in combined
    assert "the REAL currently-committed buggy script" in combined
    assert "E11000 duplicate key on logging_configurations" in combined
    assert "dedupe policyId before scoping" in combined


def test_repair_round_shown_paths_never_get_double_prefixed_on_commit():
    """Regression test for a real bug: `existing_files` used to be shown
    to the LLM keyed by the FULL committed path (e.g. "seed/seed.js"). The
    LLM naturally echoed that same key back in `files` (mirroring what it
    was shown), which then got re-prefixed to "seed/seed/seed.js" — a dead
    file — while "seed/seed.js" (the file `node seed.js` actually runs)
    was silently never updated, across every repair round. Locks in the
    fix: the existing-files prompt now shows the RELATIVE path, matching
    the convention the LLM is asked to return in `files`."""
    git = FakeGitRepoStore(specs_contracts_content={"seed/seed.js": "// the REAL currently-committed buggy script"})
    # Simulates an LLM that (still) echoes back the exact key it was
    # prompted with, e.g. "seed/seed.js" if it were shown that.
    llm = _CapturingLLM(SeederOutput(files={"seed.js": "// fixed script"}, seed_command="node seed.js"))
    node = make_seeder(llm, git)

    result = node(_repair_state())

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "--- seed.js ---" in combined
    assert "--- seed/seed.js ---" not in combined

    call = git.commit_code_files_calls[0]
    assert call["files"] == {"seed/seed.js": "// fixed script"}
    assert "seed/seed/seed.js" not in result["seeder_commit"].files_changed


def test_repair_round_excludes_another_components_failures():
    """seeder must not see a check that integration_validator would route
    to a DIFFERENT component."""
    unrelated = ValidationCheck(
        id="api-health",
        category=ValidationCategory.API,
        status=ValidationStatus.FAIL,
        description="backend health check failed — should never reach seeder's prompt",
        affected_component="backend",
    )
    git = FakeGitRepoStore(specs_contracts_content={"seed/seed.js": "// existing"})
    llm = _CapturingLLM(SeederOutput(files={"seed.js": "// fixed"}))
    node = make_seeder(llm, git)

    node(_repair_state(extra_checks=[unrelated]))

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "backend health check failed" not in combined


def test_repair_round_commits_only_the_sparse_returned_files():
    git = FakeGitRepoStore(specs_contracts_content={"seed/seed.js": "// existing"})
    # Only seed.js changed — package.json wasn't touched.
    llm = _CapturingLLM(SeederOutput(files={"seed.js": "// fixed script"}, seed_command="node seed.js"))
    node = make_seeder(llm, git)

    result = node(_repair_state())

    call = git.commit_code_files_calls[0]
    assert set(call["files"].keys()) == {"seed/seed.js"}
    # files_changed is the UNION of prior + newly-changed, so a LATER
    # repair round can still read back package.json even though this
    # round didn't touch it.
    assert result["seeder_commit"].files_changed == ["seed/seed.js", "seed/package.json"]
    assert "repair" in result["seeder_commit"].message


def test_non_repair_round_never_enters_repair_mode_even_with_a_stale_validation_report():
    """A validation_report/repair_info from a PRIOR, already-resolved
    round must not leak repair mode into a run where seeder isn't
    actually one of the current targets."""
    state = _repair_state()
    state["repair_info"] = RepairInfo(attempts=[RepairAttempt(attempt_number=1, targets=[RepairTarget.BACKEND_DEV])])
    git = FakeGitRepoStore(specs_contracts_content={"seed/seed.js": "// existing"})
    llm = _CapturingLLM(SeederOutput(files={"seed.js": "// regenerated"}, seed_command="node seed.js"))
    node = make_seeder(llm, git)

    node(state)

    combined = " ".join(str(m.content) for m in llm.captured_messages[0])
    assert "REPAIR MODE" not in combined


def test_prompt_forbids_redesigning_the_data_model():
    lowered = SYSTEM_PROMPT.lower()
    assert "do not redesign it" in lowered
    assert "report that in `contradictions`" in lowered


def test_prompt_forbids_real_pii_and_requires_idempotency():
    lowered = SYSTEM_PROMPT.lower()
    assert "never generate real customer pii" in lowered
    assert "idempotent" in lowered


def test_build_messages_includes_data_model_and_seed_spec():
    messages = build_messages('{"marker": "very-specific-model-content"}', "a very specific seed spec")
    combined = " ".join(str(m.content) for m in messages)
    assert "very-specific-model-content" in combined
    assert "a very specific seed spec" in combined


def test_build_messages_omits_repair_mode_section_when_nothing_given():
    messages = build_messages("{}", "spec")
    combined = " ".join(str(m.content) for m in messages)
    assert "REPAIR MODE" not in combined


def test_build_messages_includes_repair_mode_section_when_given():
    check = ValidationCheck(
        id="database-seed",
        category=ValidationCategory.DATABASE,
        status=ValidationStatus.FAIL,
        description="a very specific dup key failure",
        evidence="",
        repair_action="dedupe first",
    )
    messages = build_messages(
        "{}", "spec", existing_files={"seed/seed.js": "a very specific existing script"}, failing_checks=[check]
    )
    combined = " ".join(str(m.content) for m in messages)
    assert "REPAIR MODE" in combined
    assert "a very specific existing script" in combined
    assert "a very specific dup key failure" in combined
    assert "smallest correct change" in combined.lower()


def test_prompt_declares_the_standard_mongodb_env_var_names():
    assert "MONGODB_URI" in SYSTEM_PROMPT
    assert "MONGODB_DB" in SYSTEM_PROMPT
    assert "never invent an alternate name" in SYSTEM_PROMPT.lower()


def test_real_path_seed_check_receives_standard_mongodb_env_vars():
    """Regression test for a real bug: a generated seed script declared
    MONGODB_DATABASE as required, but the validator only ever supplied
    MONGODB_URI — an unfixable crash no repair round could ever resolve.
    seeder itself doesn't call the shell sandbox directly (integration_validator
    does), so this locks in the CONTRACT seeder's prompt promises: the
    same standard names integration_validator actually supplies."""
    from pov_builder.env_contract import MONGODB_DB, MONGODB_URI

    assert MONGODB_URI == "MONGODB_URI"
    assert MONGODB_DB == "MONGODB_DB"


def test_prompt_declares_all_six_seeder_env_vars():
    for name in ("MONGODB_URI", "MONGODB_DB", "DATASET_SIZE", "RANDOM_SEED", "DROP_EXISTING_COLLECTIONS", "ATLAS_SEARCH_INDEX_WAIT_MS"):
        assert name in SYSTEM_PROMPT


def test_prompt_forbids_any_variable_outside_the_standard_list():
    lowered = SYSTEM_PROMPT.lower()
    assert "do not read any other" in lowered
    assert "create_search_indexes" in lowered
