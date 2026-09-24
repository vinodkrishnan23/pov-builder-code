"""Graph nodes — one per new.md phase 1-10 (minus Phase 0 itself, which
isn't a graph node).

`transcript_analyzer` (Phase 1) is the first real implementation — a
factory function (`make_transcript_analyzer(llm)`) since it needs an LLM.
Every other node here is still a Phase 0 placeholder: none do real work;
each only records a status and, where needed for the graph to be
genuinely executable end-to-end, a minimal well-formed default value.

Replace one placeholder at a time with its real implementation, in the
order new.md itself recommends. A node needing an LLM (or any other
dependency) becomes a factory like `make_transcript_analyzer`, registered
in `builder.py`; a node needing none stays a bare `POVState -> dict`
function, registered directly.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from langgraph.types import interrupt

from pov_builder import env_contract
from pov_builder.models.common import AgentStatusRecord
from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.readme_status import ReadmeStatus
from pov_builder.models.repair import RepairAttempt, RepairInfo, RepairLoopStatus
from pov_builder.models.repository import ComponentCommit, RepositoryInfo
from pov_builder.models.review import POVReviewResult, ReviewIssue, ReviewIssueSeverity, ReviewStatus
from pov_builder.models.technical_spec import DetailedTechnicalSpec
from pov_builder.models.validation import (
    RepairTarget,
    ValidationCategory,
    ValidationCheck,
    ValidationReport,
    ValidationStatus,
)

from pov_builder.prompts.backend_dev import BackendDevOutput
from pov_builder.prompts.backend_dev import build_messages as _build_backend_dev_messages
from pov_builder.prompts.frontend_dev import FrontendDevOutput
from pov_builder.prompts.frontend_dev import build_messages as _build_frontend_dev_messages
from pov_builder.prompts.howto_helper import HowToReadmeOutput
from pov_builder.prompts.howto_helper import build_messages as build_howto_messages
from pov_builder.prompts.integration_validator import (
    PrimaryUserJourneyOutput,
    SpecificationValidationOutput,
    build_primary_user_journey_messages,
    build_specification_validation_messages,
)
from pov_builder.prompts.pov_reviewer import build_messages as _build_review_messages
from pov_builder.prompts.seeder import SeederOutput
from pov_builder.prompts.seeder import build_messages as _build_seeder_messages
from pov_builder.prompts.spec_architect.api_contract_design import ApiContractDesignOutput
from pov_builder.prompts.spec_architect.api_contract_design import build_messages as _build_api_contract_messages
from pov_builder.prompts.spec_architect.finishing import FinishingOutput
from pov_builder.prompts.spec_architect.finishing import build_messages as _build_finishing_messages
from pov_builder.prompts.spec_architect.frontend_contract_design import FrontendContractDesignOutput
from pov_builder.prompts.spec_architect.frontend_contract_design import (
    build_messages as _build_frontend_contract_messages,
)
from pov_builder.prompts.spec_architect.query_pattern_design import QueryPatternDesignOutput
from pov_builder.prompts.spec_architect.query_pattern_design import build_messages as _build_query_pattern_messages
from pov_builder.prompts.spec_architect.schema_design import SchemaDesignOutput
from pov_builder.prompts.spec_architect.schema_design import build_messages as _build_schema_design_messages
from pov_builder.prompts.transcript_analyzer import build_messages as _build_transcript_messages

from pov_builder.graph.state import POVState


def _status_update(name: str) -> dict:
    return {"agent_statuses": {name: AgentStatusRecord.placeholder(name)}}


def make_transcript_analyzer(llm, pov_run_store):
    """new.md Phase 1 — real implementation. Binds `InitialPOVSpec` as the
    LLM's structured output schema (`with_structured_output`), so the
    returned object is already a validated `InitialPOVSpec` — no manual
    JSON parsing/retry needed.

    `pov_run_store` persists the transcript + resulting InitialPOVSpec to
    MongoDB Atlas (see tools/mongo_store.py) — a workspace-specific
    addition, not something new.md's own Phase 1 spec calls for. Only
    invoked on the real-transcript path; nothing to persist for an empty
    one, and this keeps a placeholder-only run free of any live
    LLM/MongoDB dependency (see run.py).

    Returns a factory rather than a bare node function because the node
    needs an `llm` and a `pov_run_store` instance; `builder.py` calls this
    once at graph-build time and registers the returned closure as the
    `transcript_analyzer` node.
    """
    structured_llm = llm.with_structured_output(InitialPOVSpec)

    def transcript_analyzer(state: POVState) -> dict:
        transcript = state.get("transcript", "").strip()
        if not transcript:
            # Deterministic short-circuit — no LLM call, no persistence,
            # for a transcript with nothing to extract. This is NOT the
            # "not enough information" case (see README "Design decision:
            # insufficient-information transcripts") — that's a spec with
            # SOME content but real gaps, which still goes through the LLM
            # and is caught by pov_reviewer. A blank transcript has no
            # content to reason about, or persist, at all.
            initial_spec = InitialPOVSpec(
                open_questions=["No transcript was provided — nothing to extract."]
            )
            return {"initial_spec": initial_spec, **_status_update("transcript_analyzer")}

        # Re-running after a human "revise" decision at the approval gate
        # (see make_initial_spec_approval_gate) reuses the SAME pov_id —
        # otherwise every revision round would mint a brand-new pov_id and
        # a duplicate pov_builder_runs document for what's really one POV.
        prior_pov_id = state.get("pov_id")
        feedback = state.get("spec_feedback") or []

        initial_spec = structured_llm.invoke(_build_transcript_messages(transcript, feedback))
        pov_id = pov_run_store.save_transcript_analysis(
            user_email=state.get("user_email", ""),
            pov_name=state.get("pov_name", ""),
            transcript=transcript,
            initial_spec=initial_spec,
            pov_id=prior_pov_id,
        )
        return {
            "pov_id": pov_id,
            "initial_spec": initial_spec,
            **_status_update("transcript_analyzer"),
        }

    return transcript_analyzer


def make_pov_reviewer(llm):
    """new.md Phase 2 — real implementation. Binds `POVReviewResult` as the
    LLM's structured output schema, same pattern as
    `make_transcript_analyzer`.

    A missing/empty `initial_spec` (the degenerate case from
    transcript_analyzer's own empty-transcript short-circuit) has nothing
    real to review — deterministic FAIL, no LLM call, rather than asking
    the model to judge an empty document.

    Passes `rejected_review_notes` (accumulated by
    `initial_spec_approval_gate` from the human's per-issue Reject clicks)
    into the prompt every round — otherwise a dismissed concern can
    resurface every single revise round, since pov_reviewer has no other
    memory of prior rounds at all."""
    structured_llm = llm.with_structured_output(POVReviewResult)

    def pov_reviewer(state: POVState) -> dict:
        transcript = state.get("transcript", "").strip()
        initial_spec = state.get("initial_spec")

        if not transcript or initial_spec is None:
            review_result = POVReviewResult(
                status=ReviewStatus.FAIL,
                issues=[
                    ReviewIssue(
                        id="no-basis-to-review",
                        severity=ReviewIssueSeverity.HIGH,
                        category="missing_input",
                        description="No transcript/initial_spec was available to review.",
                        recommended_action="Provide a real transcript and re-run from transcript_analyzer.",
                    )
                ],
            )
        else:
            rejected_notes = state.get("rejected_review_notes") or []
            review_result = structured_llm.invoke(
                _build_review_messages(transcript, initial_spec, rejected_notes)
            )

        return {"review_result": review_result, **_status_update("pov_reviewer")}

    return pov_reviewer


def initial_spec_approval_gate(state: POVState) -> dict:
    """Human approval gate — workspace-specific addition, not a new.md
    requirement (see README "Design decision: human approval gates").
    Pauses the run via `interrupt()` so a human can review `initial_spec`
    before any design/code work happens. Resume value: `{"decision":
    "approve"} | {"decision": "revise", "feedback": str}`.

    `interrupt()` re-executes this node's logic from the top on resume —
    this node does nothing before calling it, so that's harmless.

    Includes `review_result` in the payload — pov_reviewer's own critique
    (status/issues/approved/removed/clarification-needed) is meant to
    inform this exact decision, not just get computed and archived; see
    README "Design decision: human approval gates" for why pov_reviewer
    still matters once a human is in the loop."""
    initial_spec = state.get("initial_spec")
    review_result = state.get("review_result")
    decision = interrupt(
        {
            "gate": "initial_spec",
            "pov_id": state.get("pov_id"),
            "user_email": state.get("user_email"),
            "pov_name": state.get("pov_name"),
            "summary": initial_spec.executive_summary if initial_spec else "",
            "initial_spec": initial_spec.model_dump(mode="json") if initial_spec else None,
            "review_result": review_result.model_dump(mode="json") if review_result else None,
        }
    )
    update: dict = {"initial_spec_decision": decision.get("decision")}
    if decision.get("decision") == "revise":
        if decision.get("feedback"):
            update["spec_feedback"] = [*(state.get("spec_feedback") or []), decision["feedback"]]
        if decision.get("rejected_notes"):
            prior_rejected = state.get("rejected_review_notes") or []
            # Dedupe while preserving order — the human can reject the
            # "same" issue across multiple rounds if pov_reviewer keeps
            # re-surfacing it; no reason to send pov_reviewer the same
            # dismissed note twice.
            update["rejected_review_notes"] = list(
                dict.fromkeys([*prior_rejected, *decision["rejected_notes"]])
            )
    return {**update, **_status_update("initial_spec_approval_gate")}


def technical_spec_approval_gate(state: POVState) -> dict:
    """Human approval gate for `technical_spec` — see
    `initial_spec_approval_gate`'s docstring for the shared design. This
    one matters more to gate hard on: past this point the pipeline spends
    real LLM calls AND creates real GitHub repos/code."""
    technical_spec = state.get("technical_spec")
    decision = interrupt(
        {
            "gate": "technical_spec",
            "pov_id": state.get("pov_id"),
            "user_email": state.get("user_email"),
            "pov_name": state.get("pov_name"),
            "summary": technical_spec.system_architecture if technical_spec else "",
            "technical_spec": technical_spec.model_dump(mode="json") if technical_spec else None,
        }
    )
    update: dict = {"technical_spec_decision": decision.get("decision")}
    if decision.get("decision") == "revise" and decision.get("feedback"):
        update["technical_spec_feedback"] = [
            *(state.get("technical_spec_feedback") or []),
            decision["feedback"],
        ]
    return {**update, **_status_update("technical_spec_approval_gate")}


def _commit_one_contract(git_repo_store, email: str, pov_name: str, filename: str, content: dict):
    """Commit exactly one contract file — used for the progressive,
    per-stage commits below, rather than one batch commit at the end.
    Reuses `commit_spec_contracts`'s existing dict-of-files interface with
    a single-entry dict; no change needed to tools/git_repo.py."""
    refs = git_repo_store.commit_spec_contracts(
        email=email, pov_name=pov_name, contracts={filename: json.dumps(content, indent=2)}
    )
    ref = refs[filename]
    ref.summary = f"{len(content)} top-level keys"
    return ref


def _artifact_location(repo_url: str, branch: str, ref) -> dict:
    """The real GitHub location of one committed contract — `path` +
    `commit_sha` (already on `ref`) plus a directly clickable blob URL,
    for `pov_builder_runs.spec_artifacts` (see make_spec_architect) —
    queryable straight from Mongo without touching the LangGraph
    checkpoint, where a `ContractRef` would otherwise only live."""
    return {
        "path": ref.path,
        "commit_sha": ref.commit_sha,
        "url": f"{repo_url}/blob/{branch}/{ref.path}",
    }


def make_spec_architect(llm, git_repo_store, pov_run_store):
    """new.md Phase 3 — real implementation, as 4 sequential design stages
    plus a finishing synthesis (see prompts/spec_architect/__init__.py for
    why: each stage consumes the PREVIOUS stage's REAL output, rather than
    one LLM call inventing all four contracts "simultaneously" with
    nothing forcing them to actually agree with each other):

        schema_design -> query_pattern_design -> api_contract_design
        -> frontend_contract_design -> finishing

    Each stage commits its own contract file immediately (progressive
    commits — a clear per-stage audit trail, and partial progress survives
    if a later stage fails), then the final `DetailedTechnicalSpec` is
    assembled from all 5 stages' real outputs (real commit shas, not
    placeholders).

    A missing `pov_id`/`initial_spec`/`user_email`/`pov_name` has nothing
    real to design against — deterministic empty result, no LLM calls, no
    repo writes.

    method="function_calling" on every stage: each stage's `dict[str, Any]`
    output field is deliberately open-ended (shape varies per POV), which
    OpenAI's default strict json_schema structured-output mode rejects
    (`additionalProperties: false` required on every object schema, which
    an open dict can't satisfy) — function_calling mode doesn't enforce
    that. Confirmed live the first time this was a single-stage design;
    applying it to all 5 stages proactively rather than rediscovering the
    same failure 5 times.

    Ensures the POV's branch (on the one shared repo) exists once, at the
    very start — NOT in seeder/backend_dev/frontend_dev themselves, since
    those three run in the SAME parallel super-step and would race to
    create it if each tried independently.

    Also records every contract's real GitHub location into
    `pov_builder_runs` (`pov_run_store.save_spec_artifacts`) — a
    `ContractRef` inside `technical_spec` only lives in the LangGraph
    checkpoint; this makes "where is this POV's data_model.json" a direct,
    queryable fact on the run's own Mongo document instead."""
    schema_llm = llm.with_structured_output(SchemaDesignOutput, method="function_calling")
    query_llm = llm.with_structured_output(QueryPatternDesignOutput, method="function_calling")
    api_llm = llm.with_structured_output(ApiContractDesignOutput, method="function_calling")
    frontend_llm = llm.with_structured_output(FrontendContractDesignOutput, method="function_calling")
    finishing_llm = llm.with_structured_output(FinishingOutput, method="function_calling")

    def spec_architect(state: POVState) -> dict:
        pov_id = state.get("pov_id")
        initial_spec = state.get("initial_spec")
        email = state.get("user_email")
        pov_name = state.get("pov_name")
        if pov_id is None or initial_spec is None or not email or not pov_name:
            return {"technical_spec": DetailedTechnicalSpec(), **_status_update("spec_architect")}

        review_result = state.get("review_result")
        feedback = state.get("technical_spec_feedback")

        # Provisioned FIRST and sequentially — see docstring on why this
        # can't happen inside seeder/backend_dev/frontend_dev instead.
        repo_name, repo_url, branch = git_repo_store.resolve_repo(email=email, pov_name=pov_name)
        repository = RepositoryInfo(repo_name=repo_name, repo_url=repo_url, branch=branch)

        # Stage 1 — schema_design
        schema_raw = schema_llm.invoke(_build_schema_design_messages(initial_spec, review_result, feedback))
        data_model_ref = _commit_one_contract(git_repo_store, email, pov_name, "data_model.json", schema_raw.data_model)

        # Stage 2 — query_pattern_design (consumes schema_design's REAL data_model)
        query_raw = query_llm.invoke(
            _build_query_pattern_messages(initial_spec, schema_raw.data_model, feedback)
        )
        query_patterns_ref = _commit_one_contract(
            git_repo_store, email, pov_name, "query_patterns.json", query_raw.query_patterns
        )

        # Stage 3 — api_contract_design (consumes stages 1 & 2's REAL output)
        api_raw = api_llm.invoke(
            _build_api_contract_messages(initial_spec, schema_raw.data_model, query_raw.query_patterns, feedback)
        )
        api_contract_ref = _commit_one_contract(
            git_repo_store, email, pov_name, "api_contract.json", api_raw.api_contract
        )

        # Stage 4 — frontend_contract_design (consumes ONLY the REAL api_contract)
        frontend_raw = frontend_llm.invoke(
            _build_frontend_contract_messages(initial_spec, api_raw.api_contract, feedback)
        )
        frontend_contract_ref = _commit_one_contract(
            git_repo_store, email, pov_name, "frontend_contract.json", frontend_raw.frontend_contract
        )

        # Stage 5 — finishing (the only stage that sees all four REAL outputs at once)
        finishing_raw = finishing_llm.invoke(
            _build_finishing_messages(
                initial_spec,
                schema_raw.data_model,
                query_raw.query_patterns,
                api_raw.api_contract,
                frontend_raw.frontend_contract,
                feedback,
            )
        )
        requirements_ref = _commit_one_contract(
            git_repo_store, email, pov_name, "requirements.json", finishing_raw.requirements
        )

        pov_run_store.save_spec_artifacts(
            pov_id=pov_id,
            artifacts={
                "data_model": _artifact_location(repo_url, branch, data_model_ref),
                "query_patterns": _artifact_location(repo_url, branch, query_patterns_ref),
                "api_contract": _artifact_location(repo_url, branch, api_contract_ref),
                "frontend_contract": _artifact_location(repo_url, branch, frontend_contract_ref),
                "requirements": _artifact_location(repo_url, branch, requirements_ref),
            },
        )

        technical_spec = DetailedTechnicalSpec(
            system_architecture=finishing_raw.system_architecture,
            frontend_architecture=frontend_raw.frontend_architecture,
            backend_architecture=api_raw.backend_architecture,
            data_model=data_model_ref,
            query_patterns=query_patterns_ref,
            api_contract=api_contract_ref,
            frontend_contract=frontend_contract_ref,
            requirements=requirements_ref,
            screen_definitions=frontend_raw.screen_definitions,
            user_interaction_flows=frontend_raw.user_interaction_flows,
            ai_agent_workflows=finishing_raw.ai_agent_workflows,
            mongodb_atlas_capabilities=schema_raw.mongodb_atlas_capabilities,
            index_definitions=schema_raw.index_definitions,
            seed_data_spec=schema_raw.seed_data_spec,
            environment_requirements=finishing_raw.environment_requirements,
            integration_requirements=finishing_raw.integration_requirements,
        )

        return {
            "technical_spec": technical_spec,
            "repository": repository,
            **_status_update("spec_architect"),
        }

    return spec_architect


def _placeholder_commit(component: str) -> ComponentCommit:
    return ComponentCommit(
        component=component,
        commit_sha="0000000",
        files_changed=[],
        message=f"placeholder {component} commit — Phase 0 foundation",
        committed_at=datetime.now(timezone.utc),
    )


def make_seeder(llm, git_repo_store):
    """new.md Phase 4 — real implementation. Runs in parallel with
    backend_dev/frontend_dev — writes only its own `seeder_commit` key
    (see state.py's docstring), never touches `repository` directly.

    Reads back the ACTUAL data_model.json content from the specs repo
    (technical_spec.data_model is only a ContractRef pointer — see
    prompts/seeder.py) rather than re-deciding the data model itself; the
    LLM's generated files are committed under `seed/` in the per-POV repo
    spec_architect already provisioned (`state["repository"].repo_name`).

    method="function_calling" for the same reason as spec_architect:
    `files: dict[str, str]` is an open-ended dict, incompatible with
    OpenAI's strict json_schema structured-output mode.

    On a REPAIR round (this node re-invoked because `repair_router`
    targeted `RepairTarget.SEEDER`) reads back the CURRENTLY committed
    seed files and the specific failing checks, and asks for the smallest
    correct fix instead of a wholesale regeneration — see
    prompts/_repair_mode.py. On first generation, behaves exactly as
    before."""
    structured_llm = llm.with_structured_output(SeederOutput, method="function_calling")

    def seeder(state: POVState) -> dict:
        technical_spec = state.get("technical_spec")
        repository = state.get("repository")
        email = state.get("user_email")
        pov_name = state.get("pov_name")
        if (
            technical_spec is None
            or technical_spec.data_model is None
            or repository is None
            or repository.repo_name is None
            or not email
            or not pov_name
        ):
            # Nothing real to seed against — deterministic placeholder,
            # no LLM call, no repo write.
            return {"seeder_commit": _placeholder_commit("seed"), **_status_update("seeder")}

        prior_commit = state.get("seeder_commit")
        is_repair = _is_repair_target(state, RepairTarget.SEEDER)
        existing_files: dict[str, str] = {}
        failing_checks: list[ValidationCheck] = []
        if is_repair:
            failing_checks = _checks_for_target(state, RepairTarget.SEEDER)
            if prior_commit:
                existing_files = _read_existing_files(
                    git_repo_store, email, pov_name, prior_commit.files_changed, strip_prefix="seed/"
                )

        data_model_content = git_repo_store.get_specs_contract(
            email=email, pov_name=pov_name, path=technical_spec.data_model.path
        )
        raw = structured_llm.invoke(
            _build_seeder_messages(
                data_model_content,
                technical_spec.seed_data_spec,
                existing_files=existing_files or None,
                failing_checks=failing_checks or None,
            )
        )

        files = {f"seed/{path}": content for path, content in raw.files.items()}
        commit_sha = git_repo_store.commit_code_files(
            email=email,
            pov_name=pov_name,
            files=files,
            message=f"seeder: {'repair' if is_repair else 'generate'} seed script for {pov_name}",
        )

        # Union with the prior commit's file list (not just this round's
        # sparse patch) — so a LATER repair round can still read back a
        # file this one didn't touch.
        # Preserve insertion order (this round's files last) rather than
        # sorting — dict.fromkeys dedupes while keeping first-seen order,
        # so a plain first-generation run is UNCHANGED (files_changed ==
        # list(files.keys()), same as before repair mode existed) and a
        # repair round just appends any newly-touched paths after the
        # prior, untouched ones.
        prior_files = prior_commit.files_changed if is_repair and prior_commit else []
        files_changed = list(dict.fromkeys([*prior_files, *files.keys()]))

        commit = ComponentCommit(
            component="seed",
            commit_sha=commit_sha,
            files_changed=files_changed,
            message=f"seeder: {'repair' if is_repair else 'generate'} seed script for {pov_name}",
            committed_at=datetime.now(timezone.utc),
            details={
                "seed_command": raw.seed_command,
                "environment_variables": raw.environment_variables,
                "collections_created": raw.collections_created,
                "indexes_created": raw.indexes_created,
                "contradictions": raw.contradictions,
            },
        )
        return {"seeder_commit": commit, **_status_update("seeder")}

    return seeder


def make_backend_dev(llm, git_repo_store):
    """new.md Phase 5 — real implementation. Runs in parallel with
    seeder/frontend_dev — writes only its own `backend_dev_commit` key
    (see state.py's docstring), never touches `repository` directly.

    Reads back the ACTUAL api_contract.json + data_model.json content from
    the POV's branch (technical_spec.api_contract/data_model are only
    ContractRef pointers) rather than re-deciding either — implements
    against what spec_architect already committed, exactly like seeder
    does for the data model.

    method="function_calling" for the same reason as seeder/spec_architect:
    `files: dict[str, str]` is an open-ended dict, incompatible with
    OpenAI's strict json_schema structured-output mode.

    On a REPAIR round (targeted via `RepairTarget.BACKEND_DEV`) reads back
    the CURRENTLY committed backend files and the specific failing checks,
    and asks for the smallest correct fix instead of a wholesale
    regeneration — see prompts/_repair_mode.py. On first generation,
    behaves exactly as before."""
    structured_llm = llm.with_structured_output(BackendDevOutput, method="function_calling")

    def backend_dev(state: POVState) -> dict:
        technical_spec = state.get("technical_spec")
        repository = state.get("repository")
        email = state.get("user_email")
        pov_name = state.get("pov_name")
        if (
            technical_spec is None
            or technical_spec.api_contract is None
            or technical_spec.data_model is None
            or repository is None
            or repository.repo_name is None
            or not email
            or not pov_name
        ):
            # Nothing real to implement against — deterministic placeholder,
            # no LLM call, no repo write.
            return {"backend_dev_commit": _placeholder_commit("backend"), **_status_update("backend_dev")}

        prior_commit = state.get("backend_dev_commit")
        is_repair = _is_repair_target(state, RepairTarget.BACKEND_DEV)
        existing_files: dict[str, str] = {}
        failing_checks: list[ValidationCheck] = []
        if is_repair:
            failing_checks = _checks_for_target(state, RepairTarget.BACKEND_DEV)
            if prior_commit:
                existing_files = _read_existing_files(
                    git_repo_store, email, pov_name, prior_commit.files_changed, strip_prefix="backend/"
                )

        api_contract_content = git_repo_store.get_specs_contract(
            email=email, pov_name=pov_name, path=technical_spec.api_contract.path
        )
        data_model_content = git_repo_store.get_specs_contract(
            email=email, pov_name=pov_name, path=technical_spec.data_model.path
        )
        raw = structured_llm.invoke(
            _build_backend_dev_messages(
                api_contract_content,
                data_model_content,
                existing_files=existing_files or None,
                failing_checks=failing_checks or None,
            )
        )

        files = {f"backend/{path}": content for path, content in raw.files.items()}
        commit_sha = git_repo_store.commit_code_files(
            email=email,
            pov_name=pov_name,
            files=files,
            message=f"backend_dev: {'repair' if is_repair else 'implement'} backend for {pov_name}",
        )

        # Preserve insertion order (this round's files last) rather than
        # sorting — dict.fromkeys dedupes while keeping first-seen order,
        # so a plain first-generation run is UNCHANGED (files_changed ==
        # list(files.keys()), same as before repair mode existed) and a
        # repair round just appends any newly-touched paths after the
        # prior, untouched ones.
        prior_files = prior_commit.files_changed if is_repair and prior_commit else []
        files_changed = list(dict.fromkeys([*prior_files, *files.keys()]))

        commit = ComponentCommit(
            component="backend",
            commit_sha=commit_sha,
            files_changed=files_changed,
            message=f"backend_dev: {'repair' if is_repair else 'implement'} backend for {pov_name}",
            committed_at=datetime.now(timezone.utc),
            details={
                "start_command": raw.start_command,
                "environment_variables": raw.environment_variables,
                "endpoints_implemented": raw.endpoints_implemented,
                "health_endpoint": raw.health_endpoint,
                "specification_conflicts": raw.specification_conflicts,
            },
        )
        return {"backend_dev_commit": commit, **_status_update("backend_dev")}

    return backend_dev


def make_frontend_dev(llm, git_repo_store):
    """new.md Phase 6 — real implementation. Runs in parallel with
    seeder/backend_dev — writes only its own `frontend_dev_commit` key
    (see state.py's docstring), never touches `repository` directly.

    Reads back the ACTUAL frontend_contract.json + api_contract.json
    content from the POV's branch (technical_spec.frontend_contract/
    api_contract are only ContractRef pointers) — deliberately NOT the
    data model, matching frontend_contract_design's own principle that the
    frontend should never assume anything about the database that isn't
    already exposed through the API.

    method="function_calling" for the same reason as backend_dev/seeder/
    spec_architect: `files: dict[str, str]` is an open-ended dict,
    incompatible with OpenAI's strict json_schema structured-output mode.

    On a REPAIR round (targeted via `RepairTarget.FRONTEND_DEV` — which
    covers FRONTEND/INTEGRATION/USER_JOURNEY category failures, see
    `_repair_target_for_check`) reads back the CURRENTLY committed
    frontend files and the specific failing checks, and asks for the
    smallest correct fix instead of a wholesale regeneration — see
    prompts/_repair_mode.py. On first generation, behaves exactly as
    before."""
    structured_llm = llm.with_structured_output(FrontendDevOutput, method="function_calling")

    def frontend_dev(state: POVState) -> dict:
        technical_spec = state.get("technical_spec")
        repository = state.get("repository")
        email = state.get("user_email")
        pov_name = state.get("pov_name")
        if (
            technical_spec is None
            or technical_spec.frontend_contract is None
            or technical_spec.api_contract is None
            or repository is None
            or repository.repo_name is None
            or not email
            or not pov_name
        ):
            # Nothing real to implement against — deterministic placeholder,
            # no LLM call, no repo write.
            return {"frontend_dev_commit": _placeholder_commit("frontend"), **_status_update("frontend_dev")}

        prior_commit = state.get("frontend_dev_commit")
        is_repair = _is_repair_target(state, RepairTarget.FRONTEND_DEV)
        existing_files: dict[str, str] = {}
        failing_checks: list[ValidationCheck] = []
        if is_repair:
            failing_checks = _checks_for_target(state, RepairTarget.FRONTEND_DEV)
            if prior_commit:
                existing_files = _read_existing_files(
                    git_repo_store, email, pov_name, prior_commit.files_changed, strip_prefix="frontend/"
                )

        frontend_contract_content = git_repo_store.get_specs_contract(
            email=email, pov_name=pov_name, path=technical_spec.frontend_contract.path
        )
        api_contract_content = git_repo_store.get_specs_contract(
            email=email, pov_name=pov_name, path=technical_spec.api_contract.path
        )
        raw = structured_llm.invoke(
            _build_frontend_dev_messages(
                frontend_contract_content,
                api_contract_content,
                existing_files=existing_files or None,
                failing_checks=failing_checks or None,
            )
        )

        files = {f"frontend/{path}": content for path, content in raw.files.items()}
        commit_sha = git_repo_store.commit_code_files(
            email=email,
            pov_name=pov_name,
            files=files,
            message=f"frontend_dev: {'repair' if is_repair else 'implement'} frontend for {pov_name}",
        )

        # Preserve insertion order (this round's files last) rather than
        # sorting — dict.fromkeys dedupes while keeping first-seen order,
        # so a plain first-generation run is UNCHANGED (files_changed ==
        # list(files.keys()), same as before repair mode existed) and a
        # repair round just appends any newly-touched paths after the
        # prior, untouched ones.
        prior_files = prior_commit.files_changed if is_repair and prior_commit else []
        files_changed = list(dict.fromkeys([*prior_files, *files.keys()]))

        commit = ComponentCommit(
            component="frontend",
            commit_sha=commit_sha,
            files_changed=files_changed,
            message=f"frontend_dev: {'repair' if is_repair else 'implement'} frontend for {pov_name}",
            committed_at=datetime.now(timezone.utc),
            details={
                "start_command": raw.start_command,
                "environment_variables": raw.environment_variables,
                "routes_implemented": raw.routes_implemented,
                "apis_consumed": raw.apis_consumed,
                "key_element_testids": raw.key_element_testids,
                "list_item_testid_prefixes": raw.list_item_testid_prefixes,
                "specification_conflicts": raw.specification_conflicts,
            },
        )
        return {"frontend_dev_commit": commit, **_status_update("frontend_dev")}

    return frontend_dev


_CATEGORY_TO_REPAIR_TARGET: dict[ValidationCategory, RepairTarget] = {
    ValidationCategory.DATABASE: RepairTarget.SEEDER,
    ValidationCategory.API: RepairTarget.BACKEND_DEV,
    ValidationCategory.FRONTEND: RepairTarget.FRONTEND_DEV,
    ValidationCategory.INTEGRATION: RepairTarget.FRONTEND_DEV,
    ValidationCategory.USER_JOURNEY: RepairTarget.FRONTEND_DEV,
    ValidationCategory.SPECIFICATION: RepairTarget.SPECIFICATION,
}

_COMPONENT_TO_REPAIR_TARGET: dict[str, RepairTarget] = {
    "seed": RepairTarget.SEEDER,
    "backend": RepairTarget.BACKEND_DEV,
    "frontend": RepairTarget.FRONTEND_DEV,
}


def _repair_target_for_check(check: ValidationCheck) -> RepairTarget | None:
    """`BUILD`/`STATIC` checks (from `_run_static_build_checks`) aren't in
    `_CATEGORY_TO_REPAIR_TARGET` — a category alone doesn't say WHICH
    component's install failed, only `affected_component` does (it's set
    to "seed"/"backend"/"frontend" for exactly these checks). Every other
    category already implies a single component on its own."""
    if check.category in (ValidationCategory.BUILD, ValidationCategory.STATIC):
        return _COMPONENT_TO_REPAIR_TARGET.get(check.affected_component or "")
    return _CATEGORY_TO_REPAIR_TARGET.get(check.category)


def _is_repair_target(state: POVState, target: RepairTarget) -> bool:
    """True when the LAST repair attempt actually targeted `target` —
    this is how `seeder`/`backend_dev`/`frontend_dev` (the SAME nodes on
    both the first-generation fan-out and the repair fan-out) tell which
    mode they're being invoked in."""
    repair_info = state.get("repair_info")
    if not repair_info or not repair_info.attempts:
        return False
    return target in repair_info.attempts[-1].targets


def _checks_for_target(state: POVState, target: RepairTarget) -> list[ValidationCheck]:
    """The specific failing checks a repair round should show to
    `target`'s node — reuses `_repair_target_for_check` so a component
    only ever sees the checks `integration_validator` itself would route
    to it, never another component's or a cross-cutting one."""
    report = state.get("validation_report")
    if not report:
        return []
    return [
        c for c in report.checks if c.status == ValidationStatus.FAIL and _repair_target_for_check(c) == target
    ]


def _read_existing_files(
    git_repo_store, email: str, pov_name: str, paths: list[str], *, strip_prefix: str = ""
) -> dict[str, str]:
    """Reads back the CURRENT committed content of each path on the POV's
    branch — used only on a repair round, so the model can patch real code
    instead of guessing at what it wrote last time. Best-effort: a path
    that can't be read is just omitted rather than failing the whole
    repair attempt.

    `paths` (from `ComponentCommit.files_changed`) are full repo paths
    (e.g. "seed/seed.js") — that's what `get_specs_contract` needs to
    actually read the file. But `raw.files`/`FrontendDevOutput.files` etc.
    always use paths RELATIVE to the component folder (e.g. "seed.js"),
    since that's what gets re-prefixed with `f"seed/{path}"` when
    committing. `strip_prefix` (e.g. "seed/") converts the dict keys shown
    back to the LLM to that same relative convention — otherwise the LLM
    echoes the full path it was shown as a `files` key, which then gets
    double-prefixed (e.g. "seed/seed/seed.js") and silently never
    overwrites the file that's actually run."""
    files: dict[str, str] = {}
    for path in paths:
        try:
            content = git_repo_store.get_specs_contract(email=email, pov_name=pov_name, path=path)
        except Exception:  # noqa: BLE001 — best-effort; a missing file just isn't shown back
            continue
        key = path[len(strip_prefix) :] if strip_prefix and path.startswith(strip_prefix) else path
        files[key] = content
    return files


def _merge_commits_into_repository(state: POVState) -> RepositoryInfo:
    """Shared by both the placeholder short-circuit and the real path —
    consolidating the three parallel commits into `repository.commits` is
    unconditional, independent of whether real validation ran."""
    prior_repo = state.get("repository") or RepositoryInfo()
    new_commits = [
        c
        for c in (
            state.get("seeder_commit"),
            state.get("backend_dev_commit"),
            state.get("frontend_dev_commit"),
        )
        if c is not None
    ]
    return RepositoryInfo(
        repo_url=prior_repo.repo_url,
        repo_name=prior_repo.repo_name,
        default_branch=prior_repo.default_branch,
        branch=prior_repo.branch,
        commits=[*prior_repo.commits, *new_commits],
    )


def make_integration_validator(llm, git_repo_store, shell_sandbox=None, pov_database=None, journey_runner=None):
    """new.md Phase 7 — real implementation. Actually builds/runs/tests
    the generated application (new.md: "Do not simply inspect source
    code... Where possible, BUILD and RUN the application and test it"),
    rather than just reporting PASS.

    `shell_sandbox`/`pov_database`/`journey_runner` default to `None` —
    when any is missing (or `technical_spec`/`repository`/`email`/
    `pov_name` are), falls back to the ORIGINAL placeholder behavior
    (merge commits, deterministic PASS, no real execution). This is what
    keeps every existing graph-level test working unchanged; real
    validation only happens once the caller actually wires real
    infrastructure in (see run.py/webapp/server.py).

    Everything below is deterministic wiring EXCEPT two LLM judgment
    calls (specification validation, and generating the primary user
    journey's steps) — see prompts/integration_validator.py."""
    structured_spec_llm = llm.with_structured_output(SpecificationValidationOutput, method="function_calling")
    structured_journey_llm = llm.with_structured_output(PrimaryUserJourneyOutput, method="function_calling")

    def integration_validator(state: POVState) -> dict:
        repository = _merge_commits_into_repository(state)
        technical_spec = state.get("technical_spec")
        email = state.get("user_email")
        pov_name = state.get("pov_name")

        if (
            technical_spec is None
            or not email
            or not pov_name
            or shell_sandbox is None
            or pov_database is None
            or journey_runner is None
        ):
            return {
                "repository": repository,
                "validation_report": ValidationReport(status=ValidationStatus.PASS, repair_required=False),
                **_status_update("integration_validator"),
            }

        checks: list[ValidationCheck] = []
        checkout_path: str | None = None
        db_name: str | None = None
        # Every ProcessHandle we start goes here the moment it's created,
        # BEFORE we do anything that might raise — the `finally` below
        # stops whatever's still in this list, so a process is never left
        # running just because a later step blew up.
        running_processes: list = []

        def _stop(handle) -> None:
            if handle is not None and handle in running_processes:
                shell_sandbox.stop(handle)
                running_processes.remove(handle)

        try:
            checkout_path = shell_sandbox.checkout(email=email, pov_name=pov_name)
            mongo_uri, db_name = pov_database.provision(email=email, pov_name=pov_name)

            checks.extend(_run_static_build_checks(shell_sandbox, checkout_path))
            checks.append(_run_seed_check(shell_sandbox, checkout_path, state, mongo_uri, db_name))

            api_checks, api_handle, _ = _run_api_checks(shell_sandbox, checkout_path, state, mongo_uri, db_name)
            if api_handle is not None:
                running_processes.append(api_handle)
            checks.extend(api_checks)
            _stop(api_handle)  # fresh backend for the journey phase below, not this one reused

            journey_checks, backend_handle, frontend_handle = _run_frontend_and_journey_checks(
                llm_journey=structured_journey_llm,
                journey_runner=journey_runner,
                shell_sandbox=shell_sandbox,
                git_repo_store=git_repo_store,
                checkout_path=checkout_path,
                state=state,
                mongo_uri=mongo_uri,
                db_name=db_name,
            )
            for handle in (backend_handle, frontend_handle):
                if handle is not None:
                    running_processes.append(handle)
            checks.extend(journey_checks)
            _stop(backend_handle)
            _stop(frontend_handle)

            checks.append(_run_specification_check(structured_spec_llm, state))
        finally:
            for handle in list(running_processes):
                shell_sandbox.stop(handle)
            if db_name is not None:
                pov_database.teardown(db_name)
            if checkout_path is not None:
                shell_sandbox.cleanup(checkout_path)

        failing = [c for c in checks if c.status == ValidationStatus.FAIL]
        warning = [c for c in checks if c.status == ValidationStatus.PASS_WITH_WARNINGS]
        if failing:
            overall_status = ValidationStatus.FAIL
        elif warning:
            overall_status = ValidationStatus.PASS_WITH_WARNINGS
        else:
            overall_status = ValidationStatus.PASS

        repair_targets: list[RepairTarget] = []
        for check in failing:
            target = _repair_target_for_check(check)
            if target and target not in repair_targets:
                repair_targets.append(target)

        report = ValidationReport(
            status=overall_status,
            checks=checks,
            repair_required=bool(failing),
            repair_targets=repair_targets,
        )
        return {
            "repository": repository,
            "validation_report": report,
            **_status_update("integration_validator"),
        }

    return integration_validator


def _run_static_build_checks(shell_sandbox, checkout_path: str) -> list[ValidationCheck]:
    """Best-effort install for whatever manifest each component actually
    has — backend_dev/frontend_dev's prompts default to Node.js but don't
    guarantee it, so a missing/unrecognized manifest is a note, not a
    failure."""
    checks: list[ValidationCheck] = []
    for component, subdir in (("seed", "seed"), ("backend", "backend"), ("frontend", "frontend")):
        component_path = os.path.join(checkout_path, subdir)
        if not os.path.isdir(component_path):
            continue
        if os.path.exists(os.path.join(component_path, "package.json")):
            result = shell_sandbox.run(cwd=component_path, command="npm install", timeout=180)
            install_cmd = "npm install"
        elif os.path.exists(os.path.join(component_path, "requirements.txt")):
            result = shell_sandbox.run(cwd=component_path, command="pip install -r requirements.txt", timeout=180)
            install_cmd = "pip install -r requirements.txt"
        else:
            checks.append(
                ValidationCheck(
                    id=f"build-{component}",
                    category=ValidationCategory.BUILD,
                    status=ValidationStatus.PASS_WITH_WARNINGS,
                    description=f"{component}: no recognized manifest (package.json/requirements.txt) — skipped install",
                    affected_component=component,
                )
            )
            continue
        checks.append(
            ValidationCheck(
                id=f"build-{component}",
                category=ValidationCategory.BUILD,
                status=ValidationStatus.PASS if result.ok else ValidationStatus.FAIL,
                description=f"{component}: {install_cmd}",
                evidence=result.stderr if not result.ok else "",
                affected_component=component,
                repair_action="" if result.ok else f"fix dependencies for {component}",
            )
        )
    return checks


def _run_seed_check(shell_sandbox, checkout_path: str, state: POVState, mongo_uri: str, db_name: str) -> ValidationCheck:
    seeder_commit = state.get("seeder_commit")
    seed_command = (seeder_commit.details.get("seed_command") if seeder_commit else "") or ""
    seed_path = os.path.join(checkout_path, "seed")
    if not seed_command or not os.path.isdir(seed_path):
        return ValidationCheck(
            id="database-seed",
            category=ValidationCategory.DATABASE,
            status=ValidationStatus.FAIL,
            description="no seed command/directory available to run",
            affected_component="seed",
            repair_action="generate a real seed script",
        )
    result = shell_sandbox.run(
        cwd=seed_path,
        command=seed_command,
        env={
            env_contract.MONGODB_URI: mongo_uri,
            env_contract.MONGODB_DB: db_name,
            **env_contract.SEEDER_VALIDATION_DEFAULTS,
        },
        timeout=180,
    )
    return ValidationCheck(
        id="database-seed",
        category=ValidationCategory.DATABASE,
        status=ValidationStatus.PASS if result.ok else ValidationStatus.FAIL,
        description=f"ran seed command: {seed_command}",
        evidence=result.stderr if not result.ok else "",
        affected_component="seed",
        repair_action="" if result.ok else "fix the seed script",
    )


def _run_api_checks(shell_sandbox, checkout_path: str, state: POVState, mongo_uri: str, db_name: str):
    """Starts the backend, polls its health endpoint, hits one real
    endpoint. Always returns the ProcessHandle (or None) so the caller can
    guarantee it gets stopped even if a check below fails."""
    backend_commit = state.get("backend_dev_commit")
    backend_path = os.path.join(checkout_path, "backend")
    start_command = (backend_commit.details.get("start_command") if backend_commit else "") or ""
    health_endpoint = (backend_commit.details.get("health_endpoint") if backend_commit else "") or "/health"

    if not start_command or not os.path.isdir(backend_path):
        return (
            [
                ValidationCheck(
                    id="api-start",
                    category=ValidationCategory.API,
                    status=ValidationStatus.FAIL,
                    description="no backend start command/directory available",
                    affected_component="backend",
                    repair_action="generate a real backend implementation",
                )
            ],
            None,
            None,
        )

    port = shell_sandbox.free_port()
    handle = shell_sandbox.start(
        cwd=backend_path,
        command=start_command,
        env={
            env_contract.MONGODB_URI: mongo_uri,
            env_contract.MONGODB_DB: db_name,
            env_contract.PORT: str(port),
            **env_contract.BACKEND_VALIDATION_DEFAULTS,
        },
    )
    checks: list[ValidationCheck] = []

    if not shell_sandbox.wait_ready(host="127.0.0.1", port=port, timeout=30):
        checks.append(
            ValidationCheck(
                id="api-start",
                category=ValidationCategory.API,
                status=ValidationStatus.FAIL,
                description=f"backend did not start listening on port {port} within 30s",
                affected_component="backend",
                repair_action="fix backend startup",
            )
        )
        return checks, handle, port

    checks.append(
        ValidationCheck(
            id="api-start",
            category=ValidationCategory.API,
            status=ValidationStatus.PASS,
            description=f"backend listening on port {port}",
            affected_component="backend",
        )
    )

    health_url = f"http://127.0.0.1:{port}{health_endpoint}"
    status_code = shell_sandbox.http_get(health_url, timeout=10)
    status_ok = status_code is not None and 200 <= status_code < 300
    checks.append(
        ValidationCheck(
            id="api-health",
            category=ValidationCategory.API,
            status=ValidationStatus.PASS if status_ok else ValidationStatus.FAIL,
            description=(
                f"health endpoint {health_endpoint} OK"
                if status_ok
                else f"health endpoint {health_endpoint} unreachable or non-2xx (status={status_code})"
            ),
            affected_component="backend",
            repair_action="" if status_ok else "fix or correctly report the health endpoint",
        )
    )

    return checks, handle, port


def _run_frontend_and_journey_checks(
    *, llm_journey, journey_runner, shell_sandbox, git_repo_store, checkout_path, state, mongo_uri, db_name
):
    """Starts a FRESH backend + the frontend together, generates the
    primary user journey from the real specification, and runs it.
    Returns (checks, backend_handle, frontend_handle) — both handles (or
    None) so the caller can guarantee they're stopped."""
    backend_commit = state.get("backend_dev_commit")
    frontend_commit = state.get("frontend_dev_commit")
    technical_spec = state.get("technical_spec")
    initial_spec = state.get("initial_spec")
    backend_path = os.path.join(checkout_path, "backend")
    frontend_path = os.path.join(checkout_path, "frontend")

    backend_start = (backend_commit.details.get("start_command") if backend_commit else "") or ""
    frontend_start = (frontend_commit.details.get("start_command") if frontend_commit else "") or ""

    if not backend_start or not frontend_start or not os.path.isdir(backend_path) or not os.path.isdir(frontend_path):
        return (
            [
                ValidationCheck(
                    id="integration-start",
                    category=ValidationCategory.INTEGRATION,
                    status=ValidationStatus.FAIL,
                    description="backend and/or frontend not available to run together",
                    repair_action="generate real backend and frontend implementations",
                )
            ],
            None,
            None,
        )

    backend_port = shell_sandbox.free_port()
    frontend_port = shell_sandbox.free_port()
    backend_base_url = f"http://127.0.0.1:{backend_port}"

    backend_handle = shell_sandbox.start(
        cwd=backend_path,
        command=backend_start,
        env={
            env_contract.MONGODB_URI: mongo_uri,
            env_contract.MONGODB_DB: db_name,
            env_contract.PORT: str(backend_port),
            **env_contract.BACKEND_VALIDATION_DEFAULTS,
        },
    )
    frontend_handle = shell_sandbox.start(
        cwd=frontend_path,
        command=frontend_start,
        env={
            env_contract.PORT: str(frontend_port),
            env_contract.API_BASE_URL: backend_base_url,
            env_contract.VITE_API_BASE_URL: backend_base_url,
        },
    )

    checks: list[ValidationCheck] = []
    backend_up = shell_sandbox.wait_ready(host="127.0.0.1", port=backend_port, timeout=30)
    frontend_up = shell_sandbox.wait_ready(host="127.0.0.1", port=frontend_port, timeout=30)
    checks.append(
        ValidationCheck(
            id="integration-start",
            category=ValidationCategory.INTEGRATION,
            status=ValidationStatus.PASS if (backend_up and frontend_up) else ValidationStatus.FAIL,
            description="backend and frontend both started together"
            if (backend_up and frontend_up)
            else "backend and/or frontend failed to start together",
            repair_action="" if (backend_up and frontend_up) else "fix startup",
        )
    )
    if not (backend_up and frontend_up):
        return checks, backend_handle, frontend_handle

    frontend_base_url = f"http://127.0.0.1:{frontend_port}"
    email = state.get("user_email", "")
    pov_name = state.get("pov_name", "")
    frontend_contract = _read_contract_json(git_repo_store, email, pov_name, technical_spec, "frontend_contract")
    api_contract = _read_contract_json(git_repo_store, email, pov_name, technical_spec, "api_contract")
    key_element_testids = (frontend_commit.details.get("key_element_testids") if frontend_commit else None) or {}
    list_item_testid_prefixes = (
        frontend_commit.details.get("list_item_testid_prefixes") if frontend_commit else None
    ) or {}
    journey_raw = llm_journey.invoke(
        build_primary_user_journey_messages(
            initial_spec or InitialPOVSpec(),
            frontend_contract,
            api_contract,
            key_element_testids,
            list_item_testid_prefixes,
        )
    )
    journey_result = journey_runner.run(base_url=frontend_base_url, steps=journey_raw.steps)
    checks.append(
        ValidationCheck(
            id="user-journey",
            category=ValidationCategory.USER_JOURNEY,
            status=ValidationStatus.PASS if journey_result.passed else ValidationStatus.FAIL,
            description=journey_raw.journey_description or "primary user journey",
            evidence="; ".join(
                f"{s.step.action}: {s.evidence}" for s in journey_result.steps if not s.passed
            ),
            affected_component="frontend",
            repair_action="" if journey_result.passed else "fix the primary user journey",
        )
    )
    checks.append(
        ValidationCheck(
            id="frontend-start",
            category=ValidationCategory.FRONTEND,
            status=ValidationStatus.PASS,
            description=f"frontend listening on port {frontend_port}",
            affected_component="frontend",
        )
    )
    return checks, backend_handle, frontend_handle


def _read_contract_json(git_repo_store, email: str, pov_name: str, technical_spec, field: str) -> dict:
    """Reads a contract's REAL committed content back (same pattern as
    seeder/backend_dev/frontend_dev — a ContractRef is only a pointer),
    so the journey prompt is grounded in the actual contract, not a
    generic guess. Degrades to an empty dict if the ref/read isn't
    available, rather than raising and losing the rest of the run."""
    ref = getattr(technical_spec, field, None) if technical_spec else None
    if ref is None:
        return {}
    try:
        content = git_repo_store.get_specs_contract(email=email, pov_name=pov_name, path=ref.path)
        return json.loads(content)
    except Exception:  # noqa: BLE001 — best-effort grounding, never fatal to the run
        return {}


def _run_specification_check(llm_spec, state: POVState) -> ValidationCheck:
    initial_spec = state.get("initial_spec") or InitialPOVSpec()
    seeder_commit = state.get("seeder_commit")
    backend_commit = state.get("backend_dev_commit")
    frontend_commit = state.get("frontend_dev_commit")
    implemented_surface = {
        "collections_created": (seeder_commit.details.get("collections_created") if seeder_commit else []) or [],
        "endpoints_implemented": (backend_commit.details.get("endpoints_implemented") if backend_commit else [])
        or [],
        "routes_implemented": (frontend_commit.details.get("routes_implemented") if frontend_commit else []) or [],
    }
    raw = llm_spec.invoke(build_specification_validation_messages(initial_spec, implemented_surface))
    status = ValidationStatus.FAIL if raw.unmet_requirements else ValidationStatus.PASS
    return ValidationCheck(
        id="specification",
        category=ValidationCategory.SPECIFICATION,
        status=status,
        description="; ".join(raw.unmet_requirements) if raw.unmet_requirements else "all requirements covered",
        evidence="; ".join(raw.scope_creep),
        repair_action="" if status == ValidationStatus.PASS else "address unmet requirements",
    )


def repair_router(state: POVState) -> dict:
    """new.md Phase 8 — real implementation. No LLM needed (per new.md's
    own framing: "a router + specialized repair agents, rather than
    another giant agent") — the actual RULES-table mapping (data/seed
    failures -> seeder, API/backend failures -> backend_dev, UI/frontend
    failures -> frontend_dev, spec mismatch -> specification) already
    happened deterministically in `make_integration_validator`
    (`_repair_target_for_check`), which is what populated
    `ValidationReport.repair_targets` this node just reads. This node's
    only real job is bookkeeping: record the attempt, track it against
    `max_attempts`, and force `HUMAN_REVIEW_REQUIRED` once exceeded — new.md:
    "Prevent infinite repair loops."

    The actual REPAIR (Phase 9's "repair agents") is done by re-running
    seeder/backend_dev/frontend_dev themselves via
    `route_after_repair_router`'s fan-out — not a separate set of nodes.
    Those three don't yet receive any signal about WHAT failed on a
    repair loop (they always regenerate from the same fixed contracts) —
    a real gap worth closing later, tracked here rather than silently
    ignored."""
    prior = state.get("repair_info") or RepairInfo()
    report = state.get("validation_report")
    targets = list(report.repair_targets) if report else []
    failing_checks = [c for c in (report.checks if report else []) if c.status == ValidationStatus.FAIL]
    attempt = RepairAttempt(
        attempt_number=prior.attempt_count + 1,
        targets=targets,
        triggered_by_check_ids=[c.id for c in failing_checks],
    )
    status = RepairLoopStatus.IN_PROGRESS
    if prior.attempt_count + 1 >= prior.max_attempts:
        status = RepairLoopStatus.HUMAN_REVIEW_REQUIRED
    repair_info = RepairInfo(
        attempts=[*prior.attempts, attempt],
        max_attempts=prior.max_attempts,
        status=status,
    )
    return {
        "repair_info": repair_info,
        **_status_update("repair_router"),
    }


def make_howto_helper(llm, git_repo_store):
    """new.md Phase 10 — real implementation. Writes a README grounded
    ONLY in real, already-verified facts (see prompts/howto_helper.py) —
    never re-inspects raw files itself (no shell/file access here), which
    is exactly what keeps it honest: it literally cannot invent a command
    it wasn't given.

    `HowToReadmeOutput`'s fields are all plain strings/string-lists (no
    open-ended dict), so this is the one real-LLM node that does NOT need
    method="function_calling" — OpenAI's default strict structured-output
    mode has no problem with it.

    A missing `technical_spec`/`repository`/`email`/`pov_name` has nothing
    real to document — honest short-circuit (`committed=False`), not the
    old placeholder's `committed=True` with a fake sha."""
    structured_llm = llm.with_structured_output(HowToReadmeOutput)

    def howto_helper(state: POVState) -> dict:
        technical_spec = state.get("technical_spec")
        repository = state.get("repository")
        email = state.get("user_email")
        pov_name = state.get("pov_name")
        if technical_spec is None or repository is None or repository.repo_name is None or not email or not pov_name:
            return {"readme_status": ReadmeStatus(committed=False), **_status_update("howto_helper")}

        initial_spec = state.get("initial_spec") or InitialPOVSpec()
        validation_report = state.get("validation_report")

        implemented_details = {
            component: (commit.details if commit else {})
            for component, commit in (
                ("seed", state.get("seeder_commit")),
                ("backend", state.get("backend_dev_commit")),
                ("frontend", state.get("frontend_dev_commit")),
            )
        }
        technical_spec_summary = {
            "system_architecture": technical_spec.system_architecture,
            "backend_architecture": technical_spec.backend_architecture,
            "frontend_architecture": technical_spec.frontend_architecture,
            "environment_requirements": technical_spec.environment_requirements,
            "integration_requirements": technical_spec.integration_requirements,
            "mongodb_atlas_capabilities": technical_spec.mongodb_atlas_capabilities,
        }
        repository_summary = {
            "repo_name": repository.repo_name,
            "repo_url": repository.repo_url,
            "branch": repository.branch,
            "commits": [c.model_dump(mode="json") for c in repository.commits],
        }
        validation_summary = validation_report.model_dump(mode="json") if validation_report else {}

        raw = structured_llm.invoke(
            build_howto_messages(
                initial_spec, technical_spec_summary, repository_summary, implemented_details, validation_summary
            )
        )

        commit_sha = git_repo_store.commit_code_files(
            email=email,
            pov_name=pov_name,
            files={"README.md": raw.readme_content},
            message=f"howto_helper: generate README for {pov_name}",
        )
        readme_status = ReadmeStatus(
            committed=True,
            commit_sha=commit_sha,
            verified_commands=raw.verified_commands,
            known_limitations=raw.known_limitations,
        )
        return {"readme_status": readme_status, **_status_update("howto_helper")}

    return howto_helper
