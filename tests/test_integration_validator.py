"""Tests for new.md Phase 7 — integration_validator.

Same honesty boundary as every other real phase: deterministic wiring
(short-circuits, check assembly, repair_target mapping, and — the single
most important property here — that teardown/cleanup ALWAYS happens, even
when a step fails or raises) is unit-testable without a live shell/DB/
browser; whether a real generated app actually passes needs a real run —
see README "How to execute the graph".

`FakeShellSandbox`/`FakePovDatabase`/`FakeJourneyRunner` (tests/fakes.py)
stand in for all real I/O — including the health-check HTTP call and the
port-readiness poll, which are part of the `ShellSandbox` interface
precisely so a fake can control them without any real sockets.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pov_builder.graph.nodes import make_integration_validator
from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.repository import ComponentCommit, RepositoryInfo
from pov_builder.models.technical_spec import ContractRef, DetailedTechnicalSpec
from pov_builder.models.validation import RepairTarget, ValidationCategory, ValidationStatus
from pov_builder.prompts.integration_validator import (
    PRIMARY_USER_JOURNEY_SYSTEM_PROMPT,
    SPECIFICATION_VALIDATION_SYSTEM_PROMPT,
    PrimaryUserJourneyOutput,
    SpecificationValidationOutput,
)
from pov_builder.tools.journey_runner import JourneyResult, JourneyStep, JourneyStepResult
from pov_builder.tools.shell_sandbox import ShellResult

from tests.fakes import FakeGitRepoStore, FakeJourneyRunner, FakeLLM, FakePovDatabase, FakeShellSandbox


def _commit(component: str, details: dict) -> ComponentCommit:
    return ComponentCommit(
        component=component,
        commit_sha="abc123",
        files_changed=[f"{component}/file"],
        committed_at=datetime.now(timezone.utc),
        details=details,
    )


def _full_state(tmp_path) -> dict:
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "package.json").write_text("{}")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "package.json").write_text("{}")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}")

    return {
        "user_email": "a@b.com",
        "pov_name": "demo",
        "initial_spec": InitialPOVSpec(executive_summary="a POV"),
        "technical_spec": DetailedTechnicalSpec(
            data_model=ContractRef(path="spec_architect/data_model.json"),
            api_contract=ContractRef(path="spec_architect/api_contract.json"),
            frontend_contract=ContractRef(path="spec_architect/frontend_contract.json"),
        ),
        "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/demo"),
        "seeder_commit": _commit("seed", {"seed_command": "node seed.js", "collections_created": ["orders"]}),
        "backend_dev_commit": _commit(
            "backend",
            {"start_command": "node server.js", "health_endpoint": "/health", "endpoints_implemented": ["GET /orders"]},
        ),
        "frontend_dev_commit": _commit(
            "frontend", {"start_command": "npm run dev", "routes_implemented": ["/orders"]}
        ),
    }


def _passthrough_llm() -> FakeLLM:
    return FakeLLM(
        by_schema={
            SpecificationValidationOutput: SpecificationValidationOutput(),
            PrimaryUserJourneyOutput: PrimaryUserJourneyOutput(
                journey_description="open orders and view one", steps=[JourneyStep(action="goto", value="/orders")]
            ),
        }
    )


def test_missing_dependencies_falls_back_to_placeholder_behavior(tmp_path):
    """No shell_sandbox/pov_database/journey_runner wired in at all (the
    `build_graph` default) — must behave exactly like the original
    placeholder: merge commits, deterministic PASS, touch nothing real."""
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore())

    result = node(_full_state(tmp_path))

    report = result["validation_report"]
    assert report.status == ValidationStatus.PASS
    assert report.repair_required is False
    assert len(result["repository"].commits) == 3


def test_missing_email_falls_back_to_placeholder_even_with_dependencies_wired(tmp_path):
    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    state = _full_state(tmp_path)
    state["user_email"] = ""
    result = node(state)

    assert result["validation_report"].status == ValidationStatus.PASS
    assert shell.checkout_calls == []
    assert db.provision_calls == []


def test_full_happy_path_produces_pass_and_always_tears_down(tmp_path):
    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase(db_name="pov_a_b_com_demo")
    journey = FakeJourneyRunner(JourneyResult(passed=True, steps=[JourneyStepResult(step=JourneyStep(action="goto"), passed=True)]))
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    result = node(_full_state(tmp_path))

    report = result["validation_report"]
    assert report.status == ValidationStatus.PASS
    assert report.repair_required is False
    assert report.repair_targets == []

    # Real resources were provisioned and ALWAYS cleaned up.
    assert shell.checkout_calls == [{"email": "a@b.com", "pov_name": "demo"}]
    assert db.provision_calls == [{"email": "a@b.com", "pov_name": "demo"}]
    assert shell.cleanup_calls == [str(tmp_path)]
    assert db.teardown_calls == ["pov_a_b_com_demo"]
    # Every started process was stopped — none left running.
    assert len(shell.stop_calls) == len(shell.start_calls)

    assert len(result["repository"].commits) == 3


def test_seed_command_failure_produces_fail_and_targets_seeder(tmp_path):
    shell = FakeShellSandbox(
        checkout_path=str(tmp_path),
        run_results={"node seed.js": ShellResult(exit_code=1, stdout="", stderr="boom")},
    )
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    result = node(_full_state(tmp_path))

    report = result["validation_report"]
    assert report.status == ValidationStatus.FAIL
    assert report.repair_required is True
    assert RepairTarget.SEEDER in report.repair_targets
    seed_check = next(c for c in report.checks if c.id == "database-seed")
    assert seed_check.status == ValidationStatus.FAIL
    assert seed_check.category == ValidationCategory.DATABASE
    # Teardown still happened despite the failure.
    assert db.teardown_calls
    assert shell.cleanup_calls


def test_backend_install_failure_produces_fail_and_targets_backend_dev(tmp_path):
    """Regression test: BUILD/STATIC checks aren't in the plain
    category->target map (a category alone doesn't say WHICH component's
    install failed) — this must be resolved via `affected_component`
    instead, or a failed `npm install` for the backend would never route
    anywhere for repair."""
    shell = FakeShellSandbox(
        checkout_path=str(tmp_path),
        run_results={"npm install": ShellResult(exit_code=1, stdout="", stderr="ENOENT")},
    )
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    result = node(_full_state(tmp_path))

    report = result["validation_report"]
    assert report.status == ValidationStatus.FAIL
    assert RepairTarget.BACKEND_DEV in report.repair_targets
    build_backend_check = next(c for c in report.checks if c.id == "build-backend")
    assert build_backend_check.status == ValidationStatus.FAIL
    assert build_backend_check.affected_component == "backend"


def test_unreachable_health_endpoint_produces_fail_and_targets_backend_dev(tmp_path):
    shell = FakeShellSandbox(checkout_path=str(tmp_path), http_results={"/health": None})
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    result = node(_full_state(tmp_path))

    report = result["validation_report"]
    assert report.status == ValidationStatus.FAIL
    assert RepairTarget.BACKEND_DEV in report.repair_targets
    health_check = next(c for c in report.checks if c.id == "api-health")
    assert health_check.status == ValidationStatus.FAIL


def test_backend_never_starts_listening_still_tears_down(tmp_path):
    """wait_ready returning False for every port is the harshest failure
    mode — nothing ever comes up. Must still clean up, not hang or leak."""
    shell = FakeShellSandbox(checkout_path=str(tmp_path), ready_ports=set())
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    result = node(_full_state(tmp_path))

    assert result["validation_report"].status == ValidationStatus.FAIL
    assert db.teardown_calls
    assert shell.cleanup_calls
    assert len(shell.stop_calls) == len(shell.start_calls)


def test_failed_user_journey_produces_fail_and_targets_frontend_dev(tmp_path):
    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase()
    journey = FakeJourneyRunner(
        JourneyResult(
            passed=False,
            steps=[JourneyStepResult(step=JourneyStep(action="goto", value="/orders"), passed=False, evidence="timed out")],
        )
    )
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    result = node(_full_state(tmp_path))

    report = result["validation_report"]
    assert report.status == ValidationStatus.FAIL
    assert RepairTarget.FRONTEND_DEV in report.repair_targets
    journey_check = next(c for c in report.checks if c.id == "user-journey")
    assert journey_check.status == ValidationStatus.FAIL
    assert "timed out" in journey_check.evidence


def test_unmet_requirement_produces_fail_and_targets_specification(tmp_path):
    llm = FakeLLM(
        by_schema={
            SpecificationValidationOutput: SpecificationValidationOutput(
                unmet_requirements=["real-time inventory sync was never implemented"]
            ),
            PrimaryUserJourneyOutput: PrimaryUserJourneyOutput(steps=[]),
        }
    )
    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(llm, FakeGitRepoStore(), shell, db, journey)

    result = node(_full_state(tmp_path))

    report = result["validation_report"]
    assert report.status == ValidationStatus.FAIL
    assert RepairTarget.SPECIFICATION in report.repair_targets
    spec_check = next(c for c in report.checks if c.id == "specification")
    assert "real-time inventory sync" in spec_check.description


def test_teardown_still_happens_when_a_step_raises(tmp_path):
    """The single most important property of this node: a raised
    exception mid-validation (not just a reported FAIL) must never skip
    teardown/cleanup — that would leak a per-POV database and a temp
    checkout on every unexpected error."""

    class _RaisingLLM:
        def with_structured_output(self, schema, **kwargs):
            if schema is SpecificationValidationOutput:

                class _Raiser:
                    def invoke(self, messages):
                        raise RuntimeError("boom")

                return _Raiser()
            return _passthrough_llm().with_structured_output(schema, **kwargs)

    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_RaisingLLM(), FakeGitRepoStore(), shell, db, journey)

    try:
        node(_full_state(tmp_path))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the raised RuntimeError to propagate")

    assert db.teardown_calls
    assert shell.cleanup_calls
    assert len(shell.stop_calls) == len(shell.start_calls)


def test_no_manifest_in_a_component_is_a_warning_not_a_failure(tmp_path):
    state = _full_state(tmp_path)
    (tmp_path / "backend" / "package.json").unlink()  # simulate: no recognized manifest

    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    result = node(state)

    build_backend_check = next(c for c in result["validation_report"].checks if c.id == "build-backend")
    assert build_backend_check.status == ValidationStatus.PASS_WITH_WARNINGS


def test_specification_validation_prompt_mentions_unmet_requirements():
    assert "unmet_requirements" in SPECIFICATION_VALIDATION_SYSTEM_PROMPT


def test_primary_user_journey_prompt_says_it_must_come_from_the_specification():
    assert "must come from the specification" in PRIMARY_USER_JOURNEY_SYSTEM_PROMPT


def test_seed_and_backend_calls_receive_standard_mongodb_db_env_var(tmp_path):
    """Regression test for a real bug found live: a generated seed script
    declared MONGODB_DATABASE (later MONGODB_DB) as required, but the
    validator only ever supplied MONGODB_URI — an unfixable crash no
    repair round could resolve, since the harness (not the generated
    code) was the one missing the value. `MONGODB_DB` must now be
    supplied to every run/start call that touches Mongo."""
    from pov_builder import env_contract

    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase(uri="mongodb://fake/pov_a_b_com_demo", db_name="pov_a_b_com_demo")
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    node(_full_state(tmp_path))

    seed_run = next(c for c in shell.run_calls if "node seed.js" in c["command"])
    assert seed_run["env"][env_contract.MONGODB_DB] == "pov_a_b_com_demo"
    assert seed_run["env"][env_contract.MONGODB_URI] == "mongodb://fake/pov_a_b_com_demo"

    for start_call in shell.start_calls:
        if "node server.js" in start_call["command"]:
            assert start_call["env"][env_contract.MONGODB_DB] == "pov_a_b_com_demo"


def test_seed_call_receives_the_full_hard_env_var_contract(tmp_path):
    """DATASET_SIZE/RANDOM_SEED/DROP_EXISTING_COLLECTIONS/
    ATLAS_SEARCH_INDEX_WAIT_MS are now part of the HARD contract (real
    values always supplied), not just optional-with-defaults — closes off
    a script assuming a default that doesn't match what actually happens
    (e.g. assuming collections are dropped when they aren't)."""
    from pov_builder import env_contract

    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase(db_name="pov_a_b_com_demo")
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    node(_full_state(tmp_path))

    seed_run = next(c for c in shell.run_calls if "node seed.js" in c["command"])
    assert seed_run["env"][env_contract.DATASET_SIZE] == "small"
    assert seed_run["env"][env_contract.RANDOM_SEED] == "pov-builder-validation"
    assert seed_run["env"][env_contract.DROP_EXISTING_COLLECTIONS] == "true"
    assert seed_run["env"][env_contract.ATLAS_SEARCH_INDEX_WAIT_MS] == "5000"


def test_backend_and_frontend_starts_receive_the_full_env_contract(tmp_path):
    """Regression test for two more real gaps found in the same audit as
    MONGODB_DB: Vite (frontend_dev's actual generated tooling, confirmed
    live) only exposes VITE_-prefixed env vars to client code, so a plain
    API_BASE_URL is invisible to it; and REQUIRE_AUTH must always be
    "false" during validation since the harness never authenticates."""
    from pov_builder import env_contract

    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase(db_name="pov_a_b_com_demo")
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    node(_full_state(tmp_path))

    backend_starts = [c for c in shell.start_calls if "node server.js" in c["command"]]
    assert backend_starts
    for call in backend_starts:
        assert call["env"][env_contract.REQUIRE_AUTH] == "false"

    frontend_starts = [c for c in shell.start_calls if "npm run dev" in c["command"]]
    assert frontend_starts
    for call in frontend_starts:
        assert env_contract.API_BASE_URL in call["env"]
        assert env_contract.VITE_API_BASE_URL in call["env"]
        assert call["env"][env_contract.VITE_API_BASE_URL] == call["env"][env_contract.API_BASE_URL]


def test_seed_and_backend_calls_receive_the_newest_standard_vars(tmp_path):
    """CREATE_SEARCH_INDEXES and AUTH_TOKEN were added after a second
    audit found more real, recurring invented-name drift
    (ATLAS_SEARCH_INDEXES as a boolean toggle, ADMIN_TOKEN as an
    unconditionally-checked value) — lock in that both are now part of
    the hard-supplied contract, same as everything else."""
    from pov_builder import env_contract

    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase(db_name="pov_a_b_com_demo")
    journey = FakeJourneyRunner()
    node = make_integration_validator(_passthrough_llm(), FakeGitRepoStore(), shell, db, journey)

    node(_full_state(tmp_path))

    seed_run = next(c for c in shell.run_calls if "node seed.js" in c["command"])
    assert seed_run["env"][env_contract.CREATE_SEARCH_INDEXES] == "true"

    backend_starts = [c for c in shell.start_calls if "node server.js" in c["command"]]
    assert backend_starts
    for call in backend_starts:
        assert call["env"][env_contract.AUTH_TOKEN] == "pov-builder-validation-token"


def test_primary_user_journey_prompt_forbids_guessing_selectors():
    lowered = PRIMARY_USER_JOURNEY_SYSTEM_PROMPT.lower()
    assert "do not guess" in lowered


def test_journey_generation_receives_frontend_devs_declared_testids(tmp_path):
    """Regression test for a real bug: the journey-generation LLM call
    used to guess a selector (h1, [data-testid='page-title']) with no
    visibility into what frontend_dev actually built, causing a real
    Playwright timeout no repair round could fix (two independent LLM
    calls that never shared a selector convention). Now frontend_dev's
    OWN declared key_element_testids must reach the journey prompt."""
    state = _full_state(tmp_path)
    state["frontend_dev_commit"].details["key_element_testids"] = {"search_button": "ticket-search-btn"}

    captured: list = []

    class _CapturingJourneyLLM:
        def with_structured_output(self, schema, **kwargs):
            if schema is PrimaryUserJourneyOutput:

                class _Runnable:
                    def invoke(self, messages):
                        captured.append(messages)
                        return PrimaryUserJourneyOutput(steps=[])

                return _Runnable()
            return _passthrough_llm().with_structured_output(schema, **kwargs)

    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_CapturingJourneyLLM(), FakeGitRepoStore(), shell, db, journey)

    node(state)

    combined = " ".join(str(m.content) for m in captured[0])
    assert "ticket-search-btn" in combined
    assert "search_button" in combined
    assert "page-title" in combined


def test_journey_generation_receives_frontend_devs_declared_list_item_prefixes(tmp_path):
    """Regression test for the follow-up bug: a per-row element (e.g.
    "open this ticket") has no single fixed testid, so frontend_dev
    reported a literal `"open-ticket-{ticketId}"` template that the
    journey-generation LLM then used VERBATIM as a CSS selector — matching
    nothing, since real rows carry a real id, never the literal string
    `{ticketId}`. Now frontend_dev's declared STABLE PREFIX must reach the
    journey prompt as a starts-with selector instead."""
    state = _full_state(tmp_path)
    state["frontend_dev_commit"].details["list_item_testid_prefixes"] = {"open_ticket_button": "open-ticket-"}

    captured: list = []

    class _CapturingJourneyLLM:
        def with_structured_output(self, schema, **kwargs):
            if schema is PrimaryUserJourneyOutput:

                class _Runnable:
                    def invoke(self, messages):
                        captured.append(messages)
                        return PrimaryUserJourneyOutput(steps=[])

                return _Runnable()
            return _passthrough_llm().with_structured_output(schema, **kwargs)

    shell = FakeShellSandbox(checkout_path=str(tmp_path))
    db = FakePovDatabase()
    journey = FakeJourneyRunner()
    node = make_integration_validator(_CapturingJourneyLLM(), FakeGitRepoStore(), shell, db, journey)

    node(state)

    combined = " ".join(str(m.content) for m in captured[0])
    assert 'data-testid^="open-ticket-"' in combined
    assert "open_ticket_button" in combined
    assert "{ticketId}" not in combined
