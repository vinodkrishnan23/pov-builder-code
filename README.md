# pov-builder

A standalone LangGraph POV Generator, built **phase-by-phase** exactly as
`../new.md` lays out — this project is independent of the Magenta agents
in this workspace (`chat-agent`, `spec-architect`, `coding-orchestrator`);
nothing here touches them.

**Current status: Phase 0 (foundation) only.** Every node in the graph is
a placeholder — the topology is real and executable, but no node does real
work yet. Phases 1-10 replace one placeholder at a time, in the order
`new.md` itself recommends.

## Architecture

- `src/pov_builder/models/` — typed Pydantic models (`InitialPOVSpec`,
  `POVReviewResult`, `DetailedTechnicalSpec`, `RepositoryInfo`,
  `ValidationReport`, `RepairInfo`, `ReadmeStatus`, plus shared enums in
  `common.py`). These are the contract between nodes — no arbitrary dicts.
- `src/pov_builder/graph/state.py` — `POVState`, the single `TypedDict`
  threaded through every node. Holds references/metadata/status/commit
  SHAs, never generated source code (that lives in the POV's Git repo,
  once a later phase wires up real GitHub commits).
- `src/pov_builder/graph/nodes.py` — the 9 placeholder node functions
  (named exactly per `new.md`'s phases 1-10).
- `src/pov_builder/graph/routing.py` — the 3 conditional-edge functions
  (POV-review PASS/FAIL, validation PASS/FAIL, repair fan-out).
- `src/pov_builder/graph/builder.py` — `build_graph()`, wiring everything
  together.
- `src/pov_builder/agents/`, `prompts/`, `tools/` — empty in Phase 0; real
  agent logic, LLM prompts, and tools (Git commit/push, shell wrappers)
  land here one phase at a time.
- `src/pov_builder/config/settings.py` — env-based config (LLM + GitHub
  credentials) — defined now, not yet read by any node.
- `src/pov_builder/errors.py`, `logging_config.py` — shared exception
  hierarchy and structured logging.

## State schema

See `src/pov_builder/graph/state.py`'s docstring for the full field list
and why `agent_statuses` needs a custom merge reducer (three nodes —
`seeder`, `backend_dev`, `frontend_dev` — write to it in the same
super-step during the parallel fan-out).

## Graph topology

```
START
  |
transcript_analyzer                              (Phase 1)
  |
pov_reviewer --(FAIL)--> END                      (Phase 2)
  |(PASS / PASS_WITH_WARNINGS)
spec_architect                                    (Phase 3)
  | \\ \\
seeder  backend_dev  frontend_dev   (parallel)     (Phases 4-6)
  | / /
integration_validator --(PASS)--> howto_helper --> END   (Phase 7, 10)
  |(FAIL, repair_required)
repair_router --(cap exceeded)--> END              (Phase 8)
  |(fan out to whichever of seeder/backend_dev/frontend_dev)
  \\--> loops back into integration_validator
```

A full Mermaid diagram-per-node breakdown will be added as `LANGGRAPH.md`
once Phase 1 lands (matching the convention used by this workspace's other
LangGraph agents), rather than duplicating it here while it's still 100%
placeholders.

## How to run tests

```bash
uv sync
uv run pytest
```

## How to execute the graph

```bash
uv run python -m pov_builder.run
# or, after `uv sync`:
uv run pov-builder
```

Runs one placeholder pass end to end (an in-memory `MemorySaver`
checkpointer, no external services needed) and prints every node's status
plus the final state's key artifacts.

**One-time setup for a REAL run that reaches `integration_validator`**:
`uv run playwright install chromium` — the primary-user-journey check
drives a real headless Chromium via Playwright; the Python package alone
isn't enough, the browser binary is a separate download.

## Where Magenta fits in

This project is deliberately **plain LangGraph today — zero dependency on
`magenta_sdklanggraph`**. That's a decision, not an oversight: `new.md`
never mentions Magenta, and building the graph logic against plain
LangGraph first means faster iteration (no `agentic dev up` round-trip, no
platform quirks) while the pipeline itself gets proven correct.

**Confirmed direction: eventually deploy this to Magenta**, via the
`deploy-langgraph-to-magenta` skill already in this workspace — the same
path `chat-agent`/`spec-architect`/`coding-orchestrator` took, potentially
replacing them once this is working. Concretely, at that point:

- `build_graph()`'s LLM calls get routed through `app.llm(...)` instead of
  a bare `ChatOpenAI` (Grove routing, tracing, guardrails).
- The in-memory `MemorySaver` checkpointer gets swapped for
  `app.checkpointer()` (Mongo-backed).
- An `agent.yaml` gets written: secrets (`OPENAI_*`, `GITHUB_*`), egress
  allow-list for OpenAI/Grove + `api.github.com` (remember the
  `component:`-scoping bug documented in `../debugging-log.html`).
- Any node needing real side effects (git commit/push, shell exec for
  `integration_validator`) becomes either a plain `@app.tool()` (Tool Pod,
  secrets injected) or, for the built-in `shell_execute`/filesystem tools,
  needs `App.deep_agent()` specifically — a real design fork to make when
  Phase 7 is actually implemented, not now.

**Until then**: phases 1-10 land as plain LangGraph, no `agent.yaml`, no
secrets wiring, no Magenta imports anywhere in `src/`. Only once the full
11-phase pipeline works locally end-to-end (a real transcript → a real
repo with seed/backend/frontend → validated → repaired if needed →
README) does `deploy-langgraph-to-magenta` get invoked as a discrete final
step — that's when the `agent.yaml`/secrets/egress/deep_agent decisions
above actually get made for real. Whether this fully replaces the existing
three Magenta agents or runs alongside them is an open question, not
blocking progress today.

## Design decision: one repo, one branch per POV, identity is email + pov_name

Everything git-related — spec_architect's contracts AND the generated
application code (Phases 4-6+) — lives in ONE shared GitHub repo
(`GITHUB_REPO`), not a repo-per-POV or a separate specs-vs-code repo split.
Each POV gets its own **branch**, named `<email-slug>/<pov_name-slug>` —
derived from `user_email`/`pov_name`, which are gathered from the end user
at the very start of a run and are **required** (see `run.py`'s
`--email`/`--pov-name`, the web UI's start form). The system-generated
`pov_id` never appears anywhere in git — it stays purely an internal
MongoDB/checkpoint key (`tools/mongo_store.py`, `thread_id`).

Folder structure inside a POV's branch is flat (`spec_architect/`,
`seed/`, `backend/`, `frontend/`) — the branch itself is already the
identity, so nesting email/pov_name again as folders would be redundant.
`GitHubRepoStore._ensure_branch` is idempotent: the same `(email,
pov_name)` always resolves to the same branch, so re-running/continuing a
POV extends its existing branch rather than creating a duplicate — this is
intentional, matching the spirit of "email + pov_name is the durable
identity, not a system id."

One consequence worth being explicit about: since branch identity is
purely `(email, pov_name)`, the SAME user submitting the SAME pov_name
twice is always treated as continuing that one POV, even if they actually
meant to start a distinct new attempt. If that turns out to be a real
problem in practice, the fix is a UI-level disambiguation (warn/ask before
reusing an existing branch), not a git-layer change.

## Design decision: insufficient-information transcripts

`transcript_analyzer` never fabricates: anything not clearly stated or
reasonably inferable is classified `UNKNOWN` and logged to
`open_questions`, rather than guessed at. There is deliberately **no
clarification round-trip** (no `needs_clarification`/`answers` exchange
like the real Magenta `spec-architect` agent has) — `pov-builder` is a
single batch invocation, not a multi-turn conversation, so it has no
mechanism to *ask*, only to *report*.

Catching "not enough information" is `pov_reviewer`'s job (Phase 2), not
`transcript_analyzer`'s: it compares the extracted spec against the
transcript and can return `FAIL`, which — per `new.md`'s own Phase 2
instruction not to build a refinement agent yet — routes straight to
`END` with the review's issues as the final report. A human reads that
report, supplies a better/supplemented transcript, and re-runs the whole
graph from scratch. This is a deliberate scope decision, not a gap to
silently work around later.

## Design decision: state persistence vs. resumability

These are two different needs, deliberately solved two different ways —
don't conflate them:

- **Resumability (crash/failure recovery)**: solved by LangGraph's own
  checkpointer, not by hand-rolled code. `build_graph(llm, pov_run_store,
  checkpointer=...)` persists the ENTIRE `POVState` after every node
  transition, keyed by `thread_id` — that's what lets a run resume from
  the last completed node instead of redoing everything. `run.py` uses
  `MongoDBSaver` (same Atlas cluster as everything else) whenever a real
  transcript is given, and a plain in-memory `MemorySaver` for the
  zero-credentials placeholder path. Resuming a real run: pass the same
  `--thread-id` again.

  **This claim was false until it was actually tested.** Re-invoking
  `graph.invoke(a_real_state_dict, config)` on a thread with existing
  checkpointed progress does NOT resume it — verified directly against
  real LangGraph semantics (see git history / this session's discussion):
  it unconditionally restarts execution from the entry point
  (`transcript_analyzer`), even for a thread paused mid-graph or sitting
  at a human approval gate, silently re-running already-completed nodes
  (a real LLM call) and discarding whatever progress or human decision
  already existed. The fix: check `graph.get_state(config).values` first
  — if truthy, invoke with `None` (continue from checkpoint) instead of a
  fresh state dict. `run.py` and `webapp/server.py`'s new `/continue`
  endpoint both do this now; `/resume` still uses `Command(resume=...)`
  for the one case that's actually different — an interrupt() call
  genuinely waiting on a human decision, verified via `graph.get_state(config).tasks`
  before assuming one exists (calling `Command(resume=...)` with none
  pending is a caller error `/resume` now catches with a clear 409, rather
  than surfacing whatever LangGraph does internally in that case).
- **A queryable run ledger**: `pov_builder_runs` (`tools/mongo_store.py`)
  is a separate, complementary thing — a lightweight, denormalized record
  per real run (`pov_id`, `user_email`, `pov_name`, `transcript`,
  `initial_spec`, `spec_artifacts`, `created_at`) for listing/searching
  POVs. It is NOT the resumability mechanism and never holds a full
  `POVState` snapshot — that would just reinvent what the checkpointer
  already does, and risk drifting out of sync with it as a second,
  divergent source of truth. `spec_artifacts` is the one deliberate
  exception worth calling out: `make_spec_architect` calls
  `pov_run_store.save_spec_artifacts(pov_id=..., artifacts={...})` right
  after committing all 5 contracts, recording each one's real
  `path`/`commit_sha`/clickable `url` — e.g. `{"data_model": {"path":
  "spec_architect/data_model.json", "commit_sha": "...", "url":
  "https://github.com/.../blob/<branch>/spec_architect/data_model.json"}}`.
  A `ContractRef` inside `technical_spec` only lives in the LangGraph
  checkpoint (msgpack-serialized, not something you'd query directly) —
  this makes "where is this POV's data_model.json" a plain queryable
  field on the run's own Mongo document instead.

## Local chat UI (for testing approval gates)

A small FastAPI server + single-page frontend, for testing the two human
approval gates without typing into a terminal:

```bash
uv run pov-builder-web
# open http://127.0.0.1:8420
```

Paste a transcript, hit Start, and it renders each gate as a card — a
summary, the full JSON, an Approve button, and a feedback box for
"Request revision". This is a thin UI layer over the SAME graph/llm/
checkpointer `run.py` uses (`webapp/server.py` — no reimplementation of
the pipeline); it's why it needs the same real `.env` credentials
(`OPENAI_*`, `MONGODB_URI`, `GITHUB_*`) and, like the web UI itself, isn't
unit-tested — it's a human-facing tool verified by actually using it, same
category as `run.py`.

Three endpoints beyond the basic start/resume flow:
- `GET /api/runs/{thread_id}/status` — read-only, never invokes the graph,
  safe to poll anytime. Returns exactly what `graph.get_state(config)`
  knows right now (node log, `validation_report`, `repair_info`, whether
  it's waiting on an approval gate) independent of whether an invoke is
  in flight. This is the fix for the visibility gap hit directly this
  session — diagnosing a "stuck" run previously meant writing a one-off
  script against the checkpointer; now it's one HTTP call.
- `POST /api/runs/{thread_id}/continue` — for a thread that's paused with
  pending work but NO interrupt (e.g. mid-repair-loop, between super-steps
  with no human gate waiting — exactly the state a real run was found in
  this session). `/resume`'s `Command(resume=...)` doesn't apply there;
  this calls `graph.invoke(None, config)` instead, which continues from
  the checkpoint rather than restarting. A no-op (never raises) if the
  thread's actually already finished, so it's safe to call speculatively.
- `POST /api/runs/{thread_id}/resume` now checks
  `graph.get_state(config).tasks` for a real pending interrupt before
  calling `Command(resume=...)` — calling it with nothing actually waiting
  is a caller error now caught with a clear 409, not something left to
  whatever LangGraph does internally in that case.

The web UI itself doesn't yet have a "continue" button or a status-polling
view wired to these two new endpoints — that's UI work, not done in this
pass; the endpoints exist and are the correct backend for it.

## Design decision: human approval gates

Not a `new.md` requirement (its Phase 2 "checkpoint" is still an automated
LLM reviewer, not a person) — a workspace-specific addition, prompted by
finding `spec_architect`'s output unsatisfying on a live run with nothing
in the pipeline to catch it. Precedent: the real Magenta `coding-orchestrator`
agent in this workspace already refuses to generate code until a human
approves the spec (`check_spec_gate`) — `pov-builder` had no equivalent at
all before this.

Two gates, both via LangGraph's `interrupt()`/`Command(resume=...)`
primitive (not hand-rolled — this is the idiomatic HITL mechanism, and it
needs a real checkpointer, which `run.py` already provides for real runs):

- **`initial_spec_approval_gate`** — after `pov_reviewer`, before
  `spec_architect`. Reviews `InitialPOVSpec` (the "what and why").
- **`technical_spec_approval_gate`** — after `spec_architect`, before the
  seeder/backend_dev/frontend_dev fan-out. Reviews `DetailedTechnicalSpec`
  (the "how") — the more important one to gate hard on, since everything
  past it spends real LLM calls and commits real code to GitHub.

A "revise" decision loops back to re-run the SAME phase with the human's
feedback appended to its prompt (`spec_feedback`/`technical_spec_feedback`
in `POVState`, accumulated across multiple rounds, not just the latest).
`transcript_analyzer` reuses the existing `pov_id` on a revise loop instead
of minting a new one — otherwise every revision round would create a
duplicate `pov_builder_runs` document for what's really one POV.

`run.py` handles this interactively by default (prompts via `input()` in
the same process — see `_prompt_for_decision`); pass `--auto-approve` for
scripted/CI runs. Placeholder mode (empty transcript) never reaches either
gate (`pov_reviewer` FAILs first), so it stays a single non-interactive call.

**`pov_reviewer` has no memory across revise rounds on its own** — every
round it's called fresh with just `(transcript, initial_spec)`, so without
help it can keep re-flagging (or re-inventing a variant of) the exact same
concern forever, even after a human explicitly said "this isn't a real
issue." The web UI's per-issue Reject button (`reviewIssueItem` in
`static/index.html`) feeds those dismissed concerns into
`POVState.rejected_review_notes` (accumulated across rounds by
`initial_spec_approval_gate`, deduped) and `make_pov_reviewer` threads
that list into `pov_reviewer`'s own prompt (`prompts/pov_reviewer.py`)
every round — explicitly telling it not to re-flag them. Accept works
differently and doesn't need this: an accepted issue's recommendation
goes into `spec_feedback` for `transcript_analyzer` to actually fix, and
if it's genuinely fixed, `pov_reviewer` naturally stops flagging it
without needing to be told.

One consequence worth knowing: `PASS`/`PASS_WITH_WARNINGS` issues are
advisory, not a checklist you're required to clear to zero before
Approving — only `FAIL` (used sparingly, per the prompt) is meant to
actually block. If you're stuck revising over and over, check the status
badge first; `PASS_WITH_WARNINGS` is a valid state to just Approve from.

## Design decision: `integration_validator`'s `ShellSandbox` has no Docker

new.md's Phase 7 asks the validator to actually BUILD and RUN the
generated application, not just inspect source — that needs somewhere to
execute real shell commands against the POV's checked-out code. The
obvious-seeming choice was a local Docker container for isolation; it was
deliberately NOT used, once we confirmed how Magenta's own Tool Pod
executes shell commands for a deployed agent: `shell_execute` there is
plain `subprocess.Popen`, no container runtime at all — the Tool Pod
*itself* is the isolation boundary, not something nested inside it.

So `tools/shell_sandbox.py`'s `ShellSandbox` interface
(`checkout`/`run`/`start`/`stop`/`wait_ready`/`http_get`/`cleanup`) is
implemented locally the SAME way it eventually would be on Magenta: plain
`subprocess`, no Docker. `LocalShellSandbox` clones the POV's git branch
into a temp dir and runs commands directly on the machine running
`pov-builder` — acceptable because that machine already holds real
`GITHUB_TOKEN`/`OPENAI_API_KEY` credentials for everything else in this
pipeline; it isn't running untrusted third-party code, it's running code
this same pipeline just generated. `wait_ready`/`http_get` are part of the
interface (not free functions) specifically so tests can fake real
socket/HTTP behavior deterministically — see `tests/test_integration_validator.py`.

Three new dependencies follow the same injection pattern as `llm`/
`git_repo_store`/`pov_run_store` — all default to `None` in
`build_graph(...)`, so `integration_validator` falls back to its original
placeholder behavior (merge commits, deterministic PASS) whenever any is
missing, and every pre-existing test needed zero changes:
- `tools/shell_sandbox.py` — `ShellSandbox` / `LocalShellSandbox`
- `tools/pov_database.py` — `PovDatabaseStore` / `MongoPovDatabaseStore`:
  ONE shared Atlas cluster (same constraint as everywhere else), a
  uniquely-named DATABASE per `(email, pov_name)`, dropped by
  `integration_validator` itself right after checks complete — test-fixture
  style, not a persistent resource.
- `tools/journey_runner.py` — `JourneyRunner` / `PlaywrightJourneyRunner`:
  drives a real headless browser through the "primary user journey" new.md
  asks for. The journey's STEPS come from an LLM call grounded in the
  actual `InitialPOVSpec`/`frontend_contract`/`api_contract` (new.md: "The
  exact journey must come from the specification") — `journey_runner.py`
  itself has no opinion about what the steps should be, only how to
  execute a given list of them.

## Design decision: `ui_contract.py` — the same fix as `env_contract.py`, one layer up

A real run got all the way to 10 of 11 checks PASS (build, seed, backend
start, health, integration, specification) — the ONLY failure was
`user-journey`: a real Playwright timeout waiting for
`"h1, [data-testid='page-title']"`. 3 repair rounds correctly targeted
only `frontend_dev` (repair-mode's per-check targeting worked exactly
right) but couldn't fix it, because the root cause wasn't a bug in
`frontend_dev`'s code — it was that the journey-generation LLM call
(`prompts/integration_validator.py`) invents a selector with ZERO
visibility into what `frontend_dev` actually built. Two independent LLM
calls, no shared convention — the exact same problem class as the
env-var naming drift, one layer up the stack (UI selectors instead of
environment variable names).

Fix, same shape as `env_contract.py`: `src/pov_builder/ui_contract.py`
defines a small set of UNIVERSAL `data-testid` values (`page-title`,
`loading-indicator`, `error-message`, `empty-state` — present on every
page regardless of the POV, matching what `frontend_dev`'s own prompt
already requires building: loading/error/empty states) that
`frontend_dev` must use verbatim. Unlike env vars, though, most of what a
journey needs to click/fill is genuinely POV-specific (a search button, a
results list) — there's no universal name that fits every POV. So instead
of trying to fix those too, `FrontendDevOutput` gained a new field,
`key_element_testids: dict[str, str]` — `frontend_dev` reports the REAL
testids it actually used for its own POV-specific elements, and
`build_primary_user_journey_messages` now receives that real, declared
map (via `format_declared_testids`) instead of asking the journey
generator to guess blind. The journey prompt is now explicit: use ONLY
what's in this list, and if the journey needs something not covered,
simplify the journey rather than inventing a selector.

### Follow-up: `key_element_testids` can't express a per-row element

A later real run hit a follow-up failure on the exact same mechanism:
`Page.click: Timeout 10000ms exceeded` on
`'[data-testid="open-ticket-{ticketId}"]'`. `key_element_testids` was
built for a SINGLE, page-level element with one fixed testid —
`frontend_dev` had no way to express a REPEATED per-row element (e.g.
"open this ticket" on every row of a list), which naturally renders a
DIFFERENT testid per row. Faced with that ambiguity, `frontend_dev`
reported a literal templated string, and the journey-generation LLM used
it VERBATIM as a CSS selector — matching nothing, since a real rendered
row carries a real id, never the literal string `{ticketId}`.

Same "standardize the contract, don't guess" fix, extended one more step:
`FrontendDevOutput` gained a second field, `list_item_testid_prefixes:
dict[str, str]` — `frontend_dev` reports the STABLE PREFIX it renders onto
every row's testid (e.g. `"open-ticket-"`, for rows rendered as
`data-testid="open-ticket-abc123"`), never a template with `{}` in it.
`format_declared_testids` now builds a `[data-testid^="prefix"]`
("starts with") selector for each declared prefix, which Playwright's
`page.click(...)` matches against the first rendered instance — neither
side ever needs to know, or guess, a specific row's real id. No change to
`journey_runner.py` itself; the fix is entirely at the prompt/schema
level.

## Design decision: repair mode — patch the reported failure, don't re-roll the whole generation

Discovered live: a real run hit `HUMAN_REVIEW_REQUIRED` after 3 repair
attempts, all failing on the SAME `E11000 duplicate key` bug in the
generated `seed.js`. Root cause — `seeder`/`backend_dev`/`frontend_dev`
were re-invoked on every repair pass with the EXACT SAME inputs as first
generation: no signal about what `integration_validator` found wrong, and
no notion of "existing code" to patch. Each repair attempt was a
wholesale, independently-randomized regeneration — closer to re-rolling
dice than debugging, which is why the same bug survived all 3 attempts
even though `backend_dev`'s independent issue happened to get fixed by
chance along the way.

Fixed with two changes, both in `make_seeder`/`make_backend_dev`/
`make_frontend_dev` (`graph/nodes.py`):
- **Component-scoped failure feedback**: `_checks_for_target` filters
  `ValidationReport.checks` down to exactly the ones `integration_validator`
  itself would route to THIS component (reusing `_repair_target_for_check`
  — the same mapping in one place, not duplicated) — `seeder` never sees
  `backend_dev`'s failures or vice versa.
- **Repair mode, not regeneration**: `_is_repair_target` detects "was this
  component actually targeted by the last repair attempt." When true,
  `_read_existing_files` reads back the CURRENTLY committed code for that
  component (via `git_repo_store.get_specs_contract` — the same generic
  path-based read already used for spec contracts, now also used for
  generated code) and the prompt (`prompts/_repair_mode.py`, shared by all
  three) asks for new.md's own Phase 9 wording — "the smallest correct
  change" — returning ONLY the changed files rather than the full set.
  `ComponentCommit.files_changed` on a repair round is the UNION of prior
  + newly-changed paths, so a LATER repair round can still read back a
  file this one didn't touch.

### Follow-up bug: repair mode's own path convention silently discarded every fix

Discovered live: a real run's `database-seed` check (and its downstream
`user-journey` failure) survived all 3 repair attempts even though
`repair_router` correctly targeted `seeder` each time. Root cause was in
`_read_existing_files` itself, not in the LLM's fix: `files_changed`
stores FULL committed paths (e.g. `"seed/seed.js"`), and the old code
showed the LLM `existing_files` keyed by that same full path
(`--- seed/seed.js ---`). The repair prompt asks for "ONLY the files you
actually changed" in `files` — so the model naturally echoed back the
exact key it had just been shown, `"seed/seed.js"`, mimicking the
convention it saw rather than the RELATIVE-to-component-folder convention
first-generation actually expects. The node then re-applied the prefix
unconditionally (`f"seed/{path}"`), turning the echoed key into
`"seed/seed/seed.js"` — a dead file nobody runs — while
`"seed/seed.js"` (what `node seed.js` actually executes) was silently
left untouched, every round. Confirmed live: `seed/seed/seed.js` and
`frontend/frontend/src/App.jsx` both showed up as real, committed dead
duplicates in a stuck thread's repository.

Fix: `_read_existing_files` now takes a `strip_prefix` (`"seed/"`,
`"backend/"`, `"frontend/"` — one per caller) and strips it from the
dict keys shown back to the LLM, so `existing_files` and the requested
`files` output always share the SAME relative-path convention, matching
first generation exactly. No prompt-text change needed — the mismatch was
purely in what path string the node showed vs. re-applied.

Deliberately NOT built yet, and sequenced deliberately after this (a
memory system needs this piece to exist first, or it has nothing reliable
to learn from):
- **`RepairAttempt.outcome`** (exists on the model, never populated) —
  the natural next step once this lands: diff consecutive
  `ValidationReport`s to know whether a targeted fix actually resolved the
  specific check it targeted.
- **Cross-POV repair memory with vector search** — persist
  `(root-cause summary, fix)` pairs (embedding a short LLM-produced
  summary, not raw stack traces, to avoid noisy false-positive retrieval)
  and retrieve similar past failures as a HINT for the repair LLM call,
  never as a literally-reapplied patch (generated code differs POV to
  POV — only the underlying principle transfers).
- **Per-component self-checks** before ever reaching the full
  `integration_validator` pass — `seeder` running its own generated script
  once against a scratch DB would have caught this exact bug even cheaper;
  `backend_dev`/`frontend_dev` self-checks are necessarily shallower
  (build/boot-only) since real integration/journey validation is
  inherently cross-component.
- ~~Live run visibility~~ — **done**: see `GET /api/runs/{thread_id}/status`
  in "Local chat UI" above. What's still missing: `start_run`/`resume_run`/
  `continue_run` still block on `graph.invoke(...)` for the actual work
  (a real repair loop can still hold one HTTP request open for many
  minutes) — `/status` lets you check progress from a SEPARATE request
  while that's happening, but doesn't make the blocking call itself
  faster or streamed. Switching to LangGraph's `.stream()` + Server-Sent
  Events would close that remaining gap; not done in this pass.

### `POST /api/runs/{thread_id}/retry_repair` — reopening a repair-exhausted thread in place

Once `repair_router` forces `HUMAN_REVIEW_REQUIRED` (cap exhausted),
`next_nodes` goes empty and there's no pending interrupt — `/resume` and
`/continue` both correctly refuse the thread as terminal. Before this
endpoint, the ONLY way to retry after fixing whatever actually broke
(e.g. the path-prefix bug above) was starting a brand-new run: redoing
`spec_architect`'s 5 sequential LLM calls and every already-correct
component from scratch, just to retry the one thing that failed.

`retry_repair` reopens the SAME thread instead: it raises
`RepairInfo.max_attempts` by `extra_attempts` (default 3), sets
`status` back to `IN_PROGRESS`, and writes that via
`graph.update_state(config, {"repair_info": updated}, as_node="repair_router")`
— the standard LangGraph mechanism for "pretend this node just produced
this output," which sets the checkpoint's pending tasks via
`repair_router`'s real outgoing edge (`route_after_repair_router`). That
function already knows how to fan out to the last attempt's real targets
and already re-stops the loop if the (now higher) cap gets exhausted
again — nothing about the actual repair/routing logic is duplicated,
only the entry point is new. Refuses (409) a thread that isn't actually
in `HUMAN_REVIEW_REQUIRED`, has pending work, or is waiting on a human
approval gate — same defensive shape as `resume_run`/`continue_run`.

## Design decision: `env_contract.py` — a fixed, shared environment-variable contract

Discovered live, right after the repair-mode fix above: a real run's
`seeder` output correctly declared `MONGODB_DATABASE` as a required env
var from its very FIRST generation — and correctly declared it again
(alongside `MONGODB_DB`) on every repair round — but `integration_validator`
only ever supplied `MONGODB_URI`. Every seed run crashed immediately on
`Missing required environment variable: MONGODB_DATABASE`, and no number
of repair rounds could ever fix it: the generated code was declaring a
perfectly reasonable dependency; the validator harness was the one at
fault for never supplying it. Worse, the exact variable NAME drifted
between generations (`MONGODB_DATABASE` → later rounds also tried
`MONGODB_DB`) — there was no way for `integration_validator` to reliably
guess which name a given script would pick.

Fix: `src/pov_builder/env_contract.py` defines a small, fixed set of
standard names ONCE — `MONGODB_URI`, `MONGODB_DB` (matching this project's
OWN `Settings.mongodb_db`/`.env`'s `MONGODB_DB` convention, not a new
invented name), `PORT`, `API_BASE_URL` — and `build_env_contract_section(...)`
appends an explicit "use EXACTLY these names, never invent an alternate
one" instruction to `seeder`/`backend_dev`/`frontend_dev`'s system prompts
(each gets only the subset relevant to it). `integration_validator`
(`_run_seed_check`/`_run_api_checks`/`_run_frontend_and_journey_checks`)
now always supplies exactly this same fixed set — no guessing needed on
either side. Any OTHER env var a generated script wants is explicitly
required to have a sensible default and never crash if unset, since the
harness can't promise to supply an arbitrary invented name.

Extended to a full 6-variable hard contract for `seeder` specifically
(`SEEDER_ENV_VARS`/`SEEDER_VALIDATION_DEFAULTS`): `DATASET_SIZE`,
`RANDOM_SEED`, `DROP_EXISTING_COLLECTIONS`, `ATLAS_SEARCH_INDEX_WAIT_MS`
are now guaranteed-supplied with real, fixed values
(`small`/`pov-builder-validation`/`true`/`5000` — small and deterministic,
since this is an automated validation run, not the final human-facing
demo seed) rather than left to the generated script's own default. This
closes a related but distinct bug class from the missing-`MONGODB_DB`
crash: a script that assumes a DEFAULT (e.g. "collections get dropped
before repopulating") that doesn't match what `integration_validator`
actually does, rather than crashing outright on a missing variable.

Further audits (checking a real historical run's ACTUAL declared
`environment_variables` against the contract, not just guessing) found
more of the same drift and two more real, recurring concepts:
`VITE_API_BASE_URL` (Vite — `frontend_dev`'s actual generated tooling,
confirmed live — only exposes `VITE_`-prefixed vars to client code, so a
plain `API_BASE_URL` is invisible to it), `REQUIRE_AUTH`/`AUTH_TOKEN`
(one real generation checked an `ADMIN_TOKEN` unconditionally regardless
of any auth-required flag), and `CREATE_SEARCH_INDEXES` (a boolean toggle
distinct from `ATLAS_SEARCH_INDEX_WAIT_MS`, which only controls how long
to wait after creating them). Every generation-facing prompt's contract
section was also tightened from "these are guaranteed, anything extra
needs a safe default" to a hard "these are the ONLY environment variables
you may read, for ANY purpose" — the softer wording still left a
generation free to invent and rely on its own additional name (exactly
how `ADMIN_TOKEN` got in), which defeats the whole point of a fixed
contract. `env_contract.py`'s per-component `*_ENV_VARS` lists are now
meant to be genuinely exhaustive — the intended single place to add a
name if a real, new need surfaces, not a floor that generations build on
top of.

Not yet built, discussed but deliberately out of scope for this pass:
restricting `InitialPOVSpec`/technical design to what's actually buildable
on the full MongoDB Atlas platform (cluster + Search + Vector Search +
Online Archive + Charts) and hard-discarding requirements needing infra
we don't have (Kafka, on-prem/multi-cloud deployment, external app
integrations) — this is the OTHER unwinnable-repair-loop source we found
live (`integration_validator`'s specification check kept flagging the
same infra-infeasible requirements as "unmet" on every round, since no
repair agent can stand up a Kafka cluster). Confirmed design, not yet
implemented: hard automatic discard at the `transcript_analyzer`/
`pov_reviewer` stage, informed by the same fixed-allow-list pattern as
this env-var contract.
