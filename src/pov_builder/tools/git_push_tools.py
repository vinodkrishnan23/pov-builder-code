"""spec_architect's git-push operations, exposed as agentic tool
definitions.

Wraps `GitHubRepoStore`'s spec_architect-facing methods
(`resolve_repo`/`commit_spec_contracts`) as standalone LangChain tools.
The current pipeline does NOT call these — `graph.nodes.make_spec_architect`
calls `GitHubRepoStore` directly as a deterministic node body, not via
LLM tool-selection (see README "Design decision" sections: everything in
this pipeline except two LLM-judgment calls is deterministic wiring).
This module exists purely to show the TOOL shape, for a future agentic
(tool-calling) reimplementation — it is not a second, integrated code
path today.

If this ever gets deployed as a Magenta deep_agent, the identical
function signature and docstring become an `@app.tool()` function instead
of `@tool` — nothing else changes. Per scaffold-magenta-multi-agent's own
notes: "the docstring is what the LLM reads to decide when to call it,
and the type hints become its argument schema" — both are load-bearing
here, not just documentation.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from pov_builder.tools.git_repo import GitHubRepoStore

# Lazy-connecting, same as everywhere else this store is constructed —
# safe even before GITHUB_TOKEN is set, since nothing here touches the
# network until a tool is actually invoked.
_store = GitHubRepoStore()


@tool
def resolve_pov_git_branch(email: str, pov_name: str) -> dict:
    """Ensure the POV's branch exists on the one shared GitHub repo, creating it off the repo's default branch the first time this exact (email, pov_name) is seen.

    Call this ONCE per POV, before committing any spec_architect contract
    file — it's what makes every later commit for the SAME POV land on
    the SAME branch instead of creating a duplicate. Calling it again for
    a POV that already has a branch is a safe no-op.

    Args:
        email: The end user's email — half of the POV's durable git identity.
        pov_name: The end user's chosen name for this POV — the other half.

    Returns:
        A dict with `repo_name` (the shared repo's bare name), `repo_url`
        (its GitHub URL), and `branch` (the POV's own branch name, in the
        form `<email-slug>/<pov_name-slug>`).
    """
    repo_name, repo_url, branch = _store.resolve_repo(email=email, pov_name=pov_name)
    return {"repo_name": repo_name, "repo_url": repo_url, "branch": branch}


@tool
def commit_spec_contract(email: str, pov_name: str, filename: str, content_json: str) -> dict:
    """Commit one spec_architect contract file to `spec_architect/<filename>` on the POV's own branch, creating the branch first if it doesn't exist yet.

    This call IS the push — GitHub's Contents API creates the commit
    directly in one HTTP call. There is no separate local `git add` /
    `git commit` / `git push` sequence anywhere in this path.

    Args:
        email: The end user's email — must match what `resolve_pov_git_branch` was called with for this POV.
        pov_name: The end user's chosen POV name — must match `resolve_pov_git_branch`.
        filename: The bare file name to commit under `spec_architect/`, e.g. "data_model.json".
        content_json: The file's full content as a JSON string, already serialized.

    Returns:
        A dict with `path` (the committed path on the branch) and
        `commit_sha` (the real GitHub commit SHA for this write).
    """
    refs = _store.commit_spec_contracts(email=email, pov_name=pov_name, contracts={filename: content_json})
    ref = refs[filename]
    return {"path": ref.path, "commit_sha": ref.commit_sha}


@tool
def commit_pov_code_files(email: str, pov_name: str, files_json: str, message: str) -> dict:
    """Commit one or more generated code files (seed/backend/frontend) to the POV's own branch, creating the branch first if it doesn't exist yet.

    Used by seeder/backend_dev/frontend_dev today (as a direct
    `GitHubRepoStore.commit_code_files` call, not a tool call) to push
    whatever files they just generated under `seed/`, `backend/`, or
    `frontend/`.

    Args:
        email: The end user's email — must match what `resolve_pov_git_branch` was called with for this POV.
        pov_name: The end user's chosen POV name — must match `resolve_pov_git_branch`.
        files_json: A JSON object string mapping each relative path (e.g. "backend/server.js") to its full file content.
        message: The commit message.

    Returns:
        A dict with `commit_sha` — the sha of the last file committed in this call.
    """
    files: dict[str, str] = json.loads(files_json)
    commit_sha = _store.commit_code_files(email=email, pov_name=pov_name, files=files, message=message)
    return {"commit_sha": commit_sha}
