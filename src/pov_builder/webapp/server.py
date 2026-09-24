"""FastAPI server backing the local chat UI (`static/index.html`).

Wires the SAME real graph/llm/checkpointer/stores `run.py` uses for a real
run — this is a UI on top of the existing pipeline, not a second
implementation of it. Built once, at process startup, and reused across
every request (LangGraph's own design: one compiled graph, many
`thread_id`s, state lives in the checkpointer's backing store — see README
"Design decision: state persistence vs. resumability").

Deliberately NOT unit-tested like the graph/node code is — same category
as `run.py`: a human-facing entry point, verified by actually running it
against real infrastructure, not faked.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.mongodb import MongoDBSaver
from pydantic import BaseModel
from pymongo import MongoClient

from pov_builder.config.settings import load_settings
from pov_builder.graph.builder import NODE_NAMES, build_graph
from pov_builder.llm import build_llm
from pov_builder.logging_config import configure_logging
from pov_builder.serde import build_checkpoint_serializer
from pov_builder.tools.git_repo import GitHubRepoStore
from pov_builder.tools.journey_runner import PlaywrightJourneyRunner
from pov_builder.tools.mongo_store import MongoPovRunStore
from pov_builder.tools.pov_database import MongoPovDatabaseStore
from pov_builder.tools.shell_sandbox import LocalShellSandbox

logger = configure_logging()

_STATIC_DIR = __file__.rsplit("/", 1)[0] + "/static"

app = FastAPI(title="pov-builder")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_settings = load_settings()
_checkpointer = MongoDBSaver(
    MongoClient(_settings.mongodb_uri), db_name=_settings.mongodb_db, serde=build_checkpoint_serializer()
)
_graph = build_graph(
    build_llm(),
    MongoPovRunStore(),
    GitHubRepoStore(),
    checkpointer=_checkpointer,
    shell_sandbox=LocalShellSandbox(),
    pov_database=MongoPovDatabaseStore(),
    journey_runner=PlaywrightJourneyRunner(),
)


class StartRequest(BaseModel):
    transcript: str
    user_email: str
    pov_name: str


class ResumeRequest(BaseModel):
    decision: str  # "approve" | "revise"
    feedback: str | None = None
    # Descriptions of reviewer issues the human clicked Reject on — fed
    # into pov_reviewer's own prompt on the next round so it stops
    # re-flagging something already dismissed (see
    # initial_spec_approval_gate/make_pov_reviewer).
    rejected_notes: list[str] | None = None


class RetryRepairRequest(BaseModel):
    extra_attempts: int = 3


def _serialize_state(thread_id: str, values: dict[str, Any], *, has_interrupt: bool, interrupt_payload: Any) -> dict[str, Any]:
    """Shared by `_serialize_result` (after an invoke) and the read-only
    `/status` endpoint (from `graph.get_state()` alone, no invoke) — both
    need the exact same shape, just from a different source of `values`."""
    node_log = [
        {"node": name, "status": str(record.status), "summary": record.summary}
        for name, record in (values.get("agent_statuses") or {}).items()
    ]
    node_log.sort(key=lambda entry: NODE_NAMES.index(entry["node"]) if entry["node"] in NODE_NAMES else 999)

    if has_interrupt:
        return {
            "thread_id": thread_id,
            "status": "waiting_approval",
            "gate": interrupt_payload,
            "node_log": node_log,
            "final": None,
        }

    def _dump(key: str):
        value = values.get(key)
        return value.model_dump(mode="json") if value is not None else None

    return {
        "thread_id": thread_id,
        "status": "done",
        "gate": None,
        "node_log": node_log,
        "final": {
            "initial_spec": _dump("initial_spec"),
            "review_result": _dump("review_result"),
            "technical_spec": _dump("technical_spec"),
            "validation_report": _dump("validation_report"),
            "repair_info": _dump("repair_info"),
            "repository": _dump("repository"),
            "readme_status": _dump("readme_status"),
        },
    }


def _serialize_result(thread_id: str, result: dict[str, Any]) -> dict[str, Any]:
    has_interrupt = "__interrupt__" in result
    interrupt_payload = result["__interrupt__"][0].value if has_interrupt else None
    return _serialize_state(thread_id, result, has_interrupt=has_interrupt, interrupt_payload=interrupt_payload)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(f"{_STATIC_DIR}/index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/runs")
def start_run(req: StartRequest) -> dict:
    if not req.user_email.strip() or not req.pov_name.strip():
        raise HTTPException(status_code=400, detail="user_email and pov_name are both required")

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    try:
        result = _graph.invoke(
            {"transcript": req.transcript, "user_email": req.user_email.strip(), "pov_name": req.pov_name.strip()},
            config=config,
        )
    except Exception as exc:  # noqa: BLE001 — surface to the UI, don't 500 a raw traceback page
        logger.exception("run failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _serialize_result(thread_id, result)


def _has_pending_interrupt(config: dict) -> bool:
    return any(task.interrupts for task in _graph.get_state(config).tasks)


@app.post("/api/runs/{thread_id}/resume")
def resume_run(thread_id: str, req: ResumeRequest) -> dict:
    from langgraph.types import Command

    if req.decision not in ("approve", "revise"):
        raise HTTPException(status_code=400, detail='decision must be "approve" or "revise"')
    resume_value: dict[str, Any] = {"decision": req.decision}
    if req.decision == "revise":
        if not req.feedback or not req.feedback.strip():
            raise HTTPException(status_code=400, detail="feedback is required for a revise decision")
        resume_value["feedback"] = req.feedback.strip()
        if req.rejected_notes:
            resume_value["rejected_notes"] = [n for n in req.rejected_notes if n and n.strip()]

    config = {"configurable": {"thread_id": thread_id}}
    # Command(resume=...) targets a PENDING interrupt() call — calling it
    # when there isn't one (e.g. the thread is mid-repair-loop, paused
    # between super-steps with no human gate waiting) is a caller error,
    # not something LangGraph itself gives a clear message for. Catch it
    # here instead of surfacing a confusing internal exception.
    if not _has_pending_interrupt(config):
        raise HTTPException(
            status_code=409,
            detail="This thread has no pending approval gate to resume — use /continue instead.",
        )
    try:
        result = _graph.invoke(Command(resume=resume_value), config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("resume failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _serialize_result(thread_id, result)


@app.get("/api/runs/{thread_id}/status")
def run_status(thread_id: str) -> dict:
    """Read-only — never invokes the graph, safe to poll at any time.

    This is the fix for a real gap: `start_run`/`resume_run` block on
    `graph.invoke(...)`, which for a real run can span many minutes (real
    subprocess/browser work, possibly several repair-loop iterations) with
    zero visibility until it returns. This exposes exactly what
    `graph.get_state(config)` already knows, independent of any in-flight
    invoke — the same tool used to manually diagnose a "stuck" run earlier
    that had, in fact, already progressed."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = _graph.get_state(config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail=f"no run found for thread_id {thread_id!r}")

    has_interrupt = _has_pending_interrupt(config)
    interrupt_payload = None
    if has_interrupt:
        for task in snapshot.tasks:
            if task.interrupts:
                interrupt_payload = task.interrupts[0].value
                break
    payload = _serialize_state(thread_id, snapshot.values, has_interrupt=has_interrupt, interrupt_payload=interrupt_payload)
    payload["next_nodes"] = list(snapshot.next)
    return payload


@app.post("/api/runs/{thread_id}/continue")
def continue_run(thread_id: str) -> dict:
    """For a thread that's paused with pending work but NO interrupt (e.g.
    mid-repair-loop) — `resume_run`'s `Command(resume=...)` doesn't apply
    here, since there's no interrupt() call site to receive it. Plain
    `graph.invoke(None, config)` continues from the checkpoint instead of
    restarting — see run.py's own fix for the same underlying issue
    (re-invoking with a FRESH input dict always restarts from the entry
    point, even for a thread with real progress)."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = _graph.get_state(config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail=f"no run found for thread_id {thread_id!r}")
    if _has_pending_interrupt(config):
        raise HTTPException(
            status_code=409,
            detail="This thread is waiting on a human approval gate — use /resume, not /continue.",
        )
    if not snapshot.next:
        # Already finished — a no-op, not an error, so polling/retrying
        # this endpoint is always safe.
        return _serialize_state(thread_id, snapshot.values, has_interrupt=False, interrupt_payload=None)

    try:
        result = _graph.invoke(None, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("continue failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _serialize_result(thread_id, result)


@app.post("/api/runs/{thread_id}/retry_repair")
def retry_repair(thread_id: str, req: RetryRepairRequest) -> dict:
    """For a thread that hit HUMAN_REVIEW_REQUIRED (repair cap exhausted,
    `next_nodes` empty, no pending interrupt) — reopens the SAME repair
    loop in place, rather than requiring an entirely new run from
    `spec_architect`. Everything already committed (technical_spec,
    repository, prior component commits) stays exactly as-is; this only
    raises the cap and re-enters the loop at `repair_router`, so
    `route_after_repair_router` re-derives the fan-out from the LAST
    attempt's real targets — the same routing logic a normal repair round
    already uses, not a duplicate of it.

    Fixes a real gap: once `max_attempts` is exhausted, there was no way
    to give a thread another shot after fixing the underlying bug — the
    only path was a brand-new run, re-doing every already-correct
    component from scratch just to retry the one thing that was broken."""
    from pov_builder.models.repair import RepairInfo, RepairLoopStatus

    config = {"configurable": {"thread_id": thread_id}}
    snapshot = _graph.get_state(config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail=f"no run found for thread_id {thread_id!r}")
    if _has_pending_interrupt(config):
        raise HTTPException(
            status_code=409, detail="This thread is waiting on a human approval gate — use /resume."
        )
    if snapshot.next:
        raise HTTPException(status_code=409, detail="This thread still has pending work — use /continue.")

    repair_info = snapshot.values.get("repair_info")
    if repair_info is None or repair_info.status != RepairLoopStatus.HUMAN_REVIEW_REQUIRED:
        raise HTTPException(
            status_code=409,
            detail="This thread never hit HUMAN_REVIEW_REQUIRED — nothing to retry.",
        )
    if req.extra_attempts < 1:
        raise HTTPException(status_code=400, detail="extra_attempts must be at least 1")

    updated = RepairInfo(
        attempts=repair_info.attempts,
        max_attempts=repair_info.max_attempts + req.extra_attempts,
        status=RepairLoopStatus.IN_PROGRESS,
    )
    # as_node="repair_router" makes the checkpoint's "next" resolve via
    # route_after_repair_router, exactly as if repair_router had just
    # produced this repair_info itself.
    _graph.update_state(config, {"repair_info": updated}, as_node="repair_router")
    try:
        result = _graph.invoke(None, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("retry_repair failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _serialize_result(thread_id, result)


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8420)


if __name__ == "__main__":
    run()
