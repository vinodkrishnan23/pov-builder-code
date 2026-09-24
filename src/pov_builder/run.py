"""Executable entry point — `python -m pov_builder.run`.

Runs one pass end to end, then prints a summary of every node's status
plus the final state's key artifacts.

By default runs with a placeholder transcript and an in-memory checkpointer
(useful for confirming the topology itself still executes — new.md Phase
0: "The graph should initially be executable even though nodes may return
placeholder results"; zero external credentials needed) — this path never
reaches a human approval gate (pov_reviewer FAILs first on an empty
transcript), so it stays a single non-interactive call. Pass
--transcript-file for a real live-LLM run: this uses a REAL, MongoDB-backed
checkpointer (`MongoDBSaver`, same Atlas cluster as MongoPovRunStore) so the
run can actually be resumed from `--thread-id` after a crash instead of
redoing every node from scratch — see README "Design decision: state
persistence vs. resumability". It's also the only way to judge real
extraction/classification quality; no unit test fakes an LLM's reasoning.

A real run WILL pause interactively (twice, normally) at the two human
approval gates — see README "Design decision: human approval gates". Pass
--auto-approve to skip prompting (scripted/CI use, not real POV work).
"""

from __future__ import annotations

import argparse
import json

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.types import Command
from pymongo import MongoClient

from pov_builder.config.settings import load_settings
from pov_builder.graph.builder import build_graph
from pov_builder.graph.state import POVState
from pov_builder.llm import build_llm
from pov_builder.logging_config import configure_logging
from pov_builder.serde import build_checkpoint_serializer
from pov_builder.tools.git_repo import GitHubRepoStore
from pov_builder.tools.journey_runner import PlaywrightJourneyRunner
from pov_builder.tools.mongo_store import MongoPovRunStore
from pov_builder.tools.pov_database import MongoPovDatabaseStore
from pov_builder.tools.shell_sandbox import LocalShellSandbox

logger = configure_logging()

_PLACEHOLDER_TRANSCRIPT = ""  # empty -> transcript_analyzer's deterministic short-circuit, no LLM call


class _NoOpLLM:
    """Stands in for a real LLM in placeholder mode (no --transcript-file).
    `ChatOpenAI(...)` validates credentials at CONSTRUCTION time, not per
    call, so building a real LLM unconditionally would break the
    zero-dependency default run Phase 0 established.

    `make_transcript_analyzer` calls `.with_structured_output(...)` once at
    GRAPH-BUILD time regardless of transcript content — so this must not
    raise there. Only `.invoke(...)` on the object it returns would raise,
    and that's only reached if a node actually tries to call the LLM,
    which never happens on the empty-transcript short-circuit path."""

    def with_structured_output(self, schema: type, **kwargs) -> "_NoOpLLM":
        return self

    def invoke(self, messages):
        raise AssertionError("_NoOpLLM.invoke should never be called — only used for an empty placeholder transcript")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transcript-file",
        type=str,
        default=None,
        help="Path to a real transcript .txt file. Omit to run with a placeholder transcript.",
    )
    parser.add_argument(
        "--email",
        type=str,
        required=True,
        help="POVState.user_email — required, gathered from the end user up front; used as git branch identity.",
    )
    parser.add_argument(
        "--pov-name",
        type=str,
        required=True,
        help="POVState.pov_name — required, gathered from the end user up front; used as git branch identity.",
    )
    parser.add_argument(
        "--thread-id",
        type=str,
        default="cli-run",
        help="Checkpointer thread_id — change this to start a fresh run rather than resuming a prior one.",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help=(
            "Approve every human approval gate automatically instead of prompting "
            "interactively. For scripted/CI runs — not for real POV work."
        ),
    )
    return parser.parse_args()


def _prompt_for_decision(payload: dict, *, auto_approve: bool) -> dict:
    """Handles one interrupt()'d approval gate. Returns the value to pass
    to Command(resume=...): {"decision": "approve"} or {"decision":
    "revise", "feedback": str}."""
    gate = payload.get("gate", "unknown")
    detail_key = "initial_spec" if gate == "initial_spec" else "technical_spec"

    print(f"\n{'=' * 70}")
    print(f"APPROVAL NEEDED — {gate} (pov_id={payload.get('pov_id')})")
    print(f"{'=' * 70}")
    print(f"Summary: {payload.get('summary') or '(empty)'}")
    print(f"\nFull {detail_key} (JSON):")
    print(json.dumps(payload.get(detail_key), indent=2))

    if auto_approve:
        print("\n--auto-approve set — approving without prompting.")
        return {"decision": "approve"}

    print("\nPress Enter to APPROVE, or type feedback to request a revision.")
    answer = input("> ").strip()
    if not answer:
        return {"decision": "approve"}
    return {"decision": "revise", "feedback": answer}


def main() -> None:
    args = _parse_args()

    if args.transcript_file:
        with open(args.transcript_file, encoding="utf-8") as f:
            transcript = f.read()
        logger.info("Loaded transcript from %s (%d chars)", args.transcript_file, len(transcript))
        llm = build_llm()  # only touches OPENAI_* credentials when we actually need a real call

        # MongoDBSaver creates indexes at CONSTRUCTION time (a real network
        # call) — unlike MongoPovRunStore, it can't be built unconditionally
        # without breaking the placeholder-mode zero-credentials guarantee.
        # Real, persistent checkpointing (crash/failure resumability via
        # thread_id — see README) only when there's a real transcript.
        settings = load_settings()
        checkpointer = MongoDBSaver(
            MongoClient(settings.mongodb_uri),
            db_name=settings.mongodb_db,
            serde=build_checkpoint_serializer(),
        )
    else:
        transcript = _PLACEHOLDER_TRANSCRIPT
        logger.info("No --transcript-file given — running placeholder-only, no LLM/DB credentials needed.")
        llm = _NoOpLLM()
        checkpointer = MemorySaver()

    # MongoPovRunStore/GitHubRepoStore/MongoPovDatabaseStore all connect
    # lazily, and LocalShellSandbox/PlaywrightJourneyRunner touch nothing
    # external at construction either — safe to construct all of them
    # even when MONGODB_URI/GITHUB_TOKEN are unset, since placeholder
    # mode's empty transcript never reaches spec_architect at all
    # (pov_reviewer FAILs first — see README "Design decision:
    # insufficient-information transcripts").
    graph = build_graph(
        llm,
        MongoPovRunStore(),
        GitHubRepoStore(),
        checkpointer=checkpointer,
        shell_sandbox=LocalShellSandbox(),
        pov_database=MongoPovDatabaseStore(),
        journey_runner=PlaywrightJourneyRunner(),
    )

    initial_state: POVState = {
        "transcript": transcript,
        "user_email": args.email,
        "pov_name": args.pov_name,
    }
    config = {"configurable": {"thread_id": args.thread_id}}

    # Reusing --thread-id is documented as "resume a prior run instead of
    # starting fresh" — but `graph.invoke(a_real_state_dict, config)` is NOT
    # a resume in LangGraph: it ALWAYS restarts execution from the entry
    # point (transcript_analyzer), even for a thread that already has
    # checkpointed progress or is paused at an interrupt. Passing `None`
    # instead is what actually continues from wherever the checkpoint left
    # off. Verified directly against real LangGraph semantics: re-invoking
    # a paused/completed thread with fresh input silently re-runs the
    # already-completed node(s) instead of continuing — a real bug, caught
    # live, not a hypothetical.
    already_has_progress = bool(graph.get_state(config).values)
    result = graph.invoke(None if already_has_progress else initial_state, config=config)
    while "__interrupt__" in result:
        # Two gates can fire in the same run (initial_spec, then later
        # technical_spec) — this loop handles however many come up, not
        # just one, and also handles a gate firing more than once if the
        # human keeps requesting revisions.
        pending = result["__interrupt__"][0]
        decision = _prompt_for_decision(pending.value, auto_approve=args.auto_approve)
        result = graph.invoke(Command(resume=decision), config=config)
    final_state = result

    logger.info("Run complete. Node statuses:")
    for name, record in final_state.get("agent_statuses", {}).items():
        logger.info("  %-22s %-10s %s", name, record.status, record.summary)

    if final_state.get("pov_id"):
        logger.info("Persisted to MongoDB Atlas as pov_id=%s", final_state["pov_id"])

    initial_spec = final_state.get("initial_spec")
    if initial_spec is not None:
        logger.info("initial_spec (Phase 1 output):")
        print(json.dumps(initial_spec.model_dump(mode="json"), indent=2))

    technical_spec = final_state.get("technical_spec")
    if technical_spec is not None:
        logger.info("technical_spec (Phase 3 output):")
        print(json.dumps(technical_spec.model_dump(mode="json"), indent=2))

    logger.info("Other final artifacts:")
    logger.info("  review_result:      %s", final_state.get("review_result"))
    logger.info("  validation_report:  %s", final_state.get("validation_report"))
    repository = final_state.get("repository")
    logger.info(
        "  repository.commits: %s",
        f"{len(repository.commits)} commit(s)" if repository else "N/A (run stopped before spec_architect)",
    )
    logger.info("  readme_status:      %s", final_state.get("readme_status"))


if __name__ == "__main__":
    main()
