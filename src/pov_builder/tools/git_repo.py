"""GitHub persistence — new.md: "source code belongs in the Git repository."

ONE repo (`GITHUB_REPO`) holds everything for every POV — both
spec_architect's contracts and the generated application code
(Phases 4-6+). Each POV gets its own BRANCH inside that one repo, not its
own repo: `<email-slug>/<pov_name-slug>`, derived from the human-provided
`user_email` + `pov_name` gathered at the very start of a run — never from
the system-generated `pov_id`. The same (email, pov_name) always resolves
to the same branch, so re-running/continuing a POV extends its existing
branch rather than creating a duplicate; `_ensure_branch` is idempotent for
exactly this reason.

Folder structure INSIDE a POV's branch is flat (`spec_architect/`, `seed/`,
`backend/`, `frontend/`) — the branch itself is already the identity, so
nesting email/pov_name again as folders would be redundant.

`GitHubRepoStore` connects LAZILY (same reason as `MongoPovRunStore`):
`Github(auth=...)` doesn't validate credentials or touch the network at
construction — only an actual API call (get_organization, create_repo,
...) does. Safe to construct even when GITHUB_TOKEN is unset, as long as
nothing on that path is actually invoked.
"""

from __future__ import annotations

import re
from typing import Any

from pov_builder.config.settings import Settings, load_settings
from pov_builder.models.technical_spec import ContractRef


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "pov"


def _branch_name(email: str, pov_name: str) -> str:
    return f"{_slugify(email)}/{_slugify(pov_name)}"


class GitHubRepoStore:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or load_settings()
        self._client = None  # constructed lazily — see module docstring

    def _github(self):
        if self._client is None:
            from github import Auth, Github

            self._client = Github(auth=Auth.Token(self._settings.github_token))
        return self._client

    def _owner(self):
        gh = self._github()
        if self._settings.github_org:
            return gh.get_organization(self._settings.github_org)
        return gh.get_user()

    def _try_get_repo(self, owner: Any, name: str):
        from github import UnknownObjectException

        try:
            return owner.get_repo(name)
        except UnknownObjectException:
            return None

    def _repo(self) -> Any:
        """The one shared repo, creating it if this is the very first call
        ever made against a fresh GITHUB_REPO name — and, either way,
        making sure it actually has a first commit before returning it.

        A repo with ZERO commits has no default-branch ref yet, so nothing
        can be branched off it. `auto_init=True` covers a repo created
        JUST NOW by this call, but NOT a repo that already existed (found
        via `_try_get_repo`) and happens to still be empty — e.g. left
        behind by an earlier run that failed before ever committing
        anything (hit live: a run failed once, got retried against the
        SAME still-empty repo, and hit the exact same 404 again since
        `create_repo(auto_init=True)` never runs a second time for a repo
        that already exists). `_ensure_initialized` below is the general
        fix, covering both cases uniformly."""
        owner = self._owner()
        name = self._settings.github_repo
        repo = self._try_get_repo(owner, name)
        if repo is None:
            repo = owner.create_repo(name, private=True, auto_init=True)
        self._ensure_initialized(repo)
        return repo

    def _ensure_initialized(self, repo: Any) -> None:
        """Idempotent: gives `repo` a first commit on its ACTUAL default
        branch if it has none yet. Deliberately does NOT pass a `branch=`
        kwarg to `create_file` here — omitting it lets GitHub create that
        first commit on whatever the repo's real default branch name is,
        which is the one case the Contents API supports for a genuinely
        empty repo. Once this succeeds, `_ensure_branch` can safely read
        `repo.get_branch(repo.default_branch)` as a normal base ref."""
        from github import GithubException

        try:
            repo.get_branch(repo.default_branch)
            return
        except GithubException as exc:
            if exc.status != 404:
                raise

        repo.create_file(
            ".gitkeep",
            "initialize repository",
            "Auto-created so POV branches have a base commit to branch from.\n",
        )

    def _ensure_branch(self, repo: Any, branch: str) -> None:
        """Idempotent: creates `branch` off the repo's default branch if it
        doesn't exist yet; a no-op on every later call for the SAME
        (email, pov_name) — this is what makes re-running a POV resume its
        existing branch instead of duplicating it."""
        from github import GithubException

        try:
            repo.get_branch(branch)
            return
        except GithubException as exc:
            if exc.status != 404:
                raise

        base_sha = repo.get_branch(repo.default_branch).commit.sha
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)

    def _commit_file(self, repo: Any, path: str, content: str, message: str, branch: str, retries: int = 3) -> str:
        """Create or update one file in `repo` on `branch`. Returns the
        commit sha.

        A brand-new branch with the same tree as its base has all the base
        branch's files already, so `get_contents` 404s here mean "this
        exact path doesn't exist yet on this branch" the normal way —
        `GithubException` + status 404 covers both a missing file and (for
        the very first commit into a genuinely empty repo) "This
        repository is empty".

        Retries on a 409 conflict ("is at X but expected Y") — hit live:
        rapid successive commits to the SAME branch (e.g. spec_architect's
        5 stages, or a repair round's seeder/backend/frontend all
        committing within seconds) can outrun GitHub's own
        read-after-write consistency on the Contents API — `get_contents`
        returns a stale 404 or a stale sha moments after another commit
        actually landed. Without a retry, that one conflict raised all the
        way up, aborted the ENTIRE `graph.invoke()` call, and turned a
        transient GitHub API timing issue into a 500 that lost whatever
        else that repair round was about to do."""
        from github import GithubException

        last_exc: GithubException | None = None
        for _attempt in range(retries):
            try:
                existing = repo.get_contents(path, ref=branch)
                result = repo.update_file(path, message, content, existing.sha, branch=branch)
                return result["commit"].sha
            except GithubException as exc:
                if exc.status != 404:
                    last_exc = exc
                    continue  # sha changed since our read (409) — re-read and retry
                try:
                    result = repo.create_file(path, message, content, branch=branch)
                    return result["commit"].sha
                except GithubException as create_exc:
                    if create_exc.status not in (404, 409):
                        raise
                    last_exc = create_exc  # file appeared between our read and create — re-read and retry
        raise RuntimeError(f"failed to commit {path!r} on branch {branch!r} after {retries} conflicting attempts") from last_exc

    def resolve_repo(self, *, email: str, pov_name: str) -> tuple[str, str, str]:
        """Ensure the POV's branch exists on the one shared repo. Returns
        `(repo_name, repo_url, branch)` — callers populate `RepositoryInfo`
        with this before any commit happens. Safe to call more than once
        for the same (email, pov_name); the second call is a no-op besides
        re-reading the branch."""
        repo = self._repo()
        branch = _branch_name(email, pov_name)
        self._ensure_branch(repo, branch)
        return repo.name, repo.html_url, branch

    # -----------------------------------------------------------------
    # spec_architect (Phase 3) — flat `spec_architect/<filename>` on the
    # POV's branch.
    # -----------------------------------------------------------------
    def commit_spec_contracts(self, *, email: str, pov_name: str, contracts: dict[str, str]) -> dict[str, ContractRef]:
        """Commit each `{filename: json_content}` pair to
        `spec_architect/<filename>` on the POV's branch (creating the
        branch if this is its first commit). Returns one `ContractRef` per
        filename, with the real commit sha."""
        repo = self._repo()
        branch = _branch_name(email, pov_name)
        self._ensure_branch(repo, branch)

        refs: dict[str, ContractRef] = {}
        for filename, content in contracts.items():
            path = f"spec_architect/{filename}"
            commit_sha = self._commit_file(
                repo, path, content, message=f"spec_architect: {filename} for {branch}", branch=branch
            )
            refs[filename] = ContractRef(path=path, commit_sha=commit_sha)
        return refs

    # -----------------------------------------------------------------
    # Phase 4+ — seeder/backend_dev/frontend_dev each write into their own
    # subdirectory (seed/, backend/, frontend/) on the SAME POV branch.
    # -----------------------------------------------------------------
    def commit_code_files(self, *, email: str, pov_name: str, files: dict[str, str], message: str) -> str:
        """Commit `{path: content}` pairs into the POV's branch. Returns
        the commit sha of the LAST file committed (PyGithub has no
        built-in single-commit-multiple-files primitive without the
        lower-level git data API; fine for now since each phase's file
        count is small — revisit if a component ever needs one atomic
        multi-file commit instead of one commit per file)."""
        repo = self._repo()
        branch = _branch_name(email, pov_name)
        self._ensure_branch(repo, branch)

        commit_sha = ""
        for path, content in files.items():
            commit_sha = self._commit_file(repo, path, content, message=message, branch=branch)
        return commit_sha

    # -----------------------------------------------------------------
    # Reading a previously-committed spec contract back — Phase 4+ needs
    # the ACTUAL data_model.json content (DetailedTechnicalSpec only
    # holds a ContractRef pointer, not the content, per Phase 3's design).
    # -----------------------------------------------------------------
    def get_specs_contract(self, *, email: str, pov_name: str, path: str) -> str:
        """Read back a contract's raw content from the POV's branch, by
        the `path` from its `ContractRef` (e.g.
        `technical_spec.data_model.path`)."""
        repo = self._repo()
        branch = _branch_name(email, pov_name)
        content = repo.get_contents(path, ref=branch)
        return content.decoded_content.decode("utf-8")
