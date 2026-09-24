"""ShellSandbox — executes real shell commands against a POV's checked-out
code, for `integration_validator` (new.md Phase 7) to actually build/run/
test the generated application instead of just inspecting source.

Deliberately plain `subprocess`, no Docker: Magenta's own Tool Pod
`shell_execute` implementation is plain `subprocess.Popen` too — the Tool
Pod itself is the sandbox boundary there, not a nested container. Building
this LOCAL implementation the same way means the eventual Magenta
implementation of this exact interface is mechanically identical, not a
second design — see README "Design decision: ShellSandbox has no Docker".

Every method that touches a credential (`checkout`'s GitHub token) scrubs
it from anything it returns or raises — a value that reaches a
`ValidationCheck.evidence` field could end up rendered in the web UI or a
log, so the token must never survive past the one `git clone` call it's
embedded in.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from pov_builder.config.settings import Settings, load_settings


class ShellResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class ProcessHandle:
    pid: int
    log_path: str
    _process: subprocess.Popen | None = None


class ShellSandbox(Protocol):
    def checkout(self, *, email: str, pov_name: str) -> str:
        """Clone the POV's branch into a fresh temp dir. Returns its path."""
        ...

    def run(self, *, cwd: str, command: str, env: dict[str, str] | None = None, timeout: int = 180) -> ShellResult:
        """Run one command to completion (blocking) — install/build/seed steps."""
        ...

    def start(self, *, cwd: str, command: str, env: dict[str, str] | None = None) -> ProcessHandle:
        """Start a long-running command (a server) in the background."""
        ...

    def stop(self, handle: ProcessHandle) -> None:
        """Terminate a process started by `start`, and everything it
        spawned (e.g. `npm run dev` -> `vite`) — not just the immediate
        process. Found live, not hypothetically: an orphaned `vite`
        process from a prior validation run was found still running,
        parented to init, well after `stop` had supposedly been called on
        it."""
        ...

    def free_port(self) -> int:
        """An OS-assigned free TCP port, for a backend/frontend to bind to."""
        ...

    def wait_ready(self, *, host: str, port: int, timeout: int = 30) -> bool:
        """Polls until something is listening on (host, port), or
        `timeout` elapses — part of the interface (not a free function) so
        a fake can control readiness deterministically in tests, instead
        of every caller doing real socket I/O directly."""
        ...

    def http_get(self, url: str, timeout: int = 10) -> int | None:
        """GETs `url`, returns the status code, or None on any connection
        failure/timeout. Part of the interface for the same reason as
        `wait_ready` — a health-check HTTP call must be fakeable, not a
        hardcoded `urllib` call a fake can't intercept."""
        ...

    def cleanup(self, path: str) -> None:
        """Remove a temp checkout created by `checkout`."""
        ...


_TOKEN_SCRUB_RE = re.compile(r"x-access-token:[^@]+@")


def _scrub(text: str) -> str:
    return _TOKEN_SCRUB_RE.sub("x-access-token:***@", text)


class LocalShellSandbox:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or load_settings()

    def checkout(self, *, email: str, pov_name: str) -> str:
        from pov_builder.tools.git_repo import _branch_name

        branch = _branch_name(email, pov_name)
        owner = self._settings.github_org or self._settings.github_username
        repo = self._settings.github_repo
        clone_url = f"https://x-access-token:{self._settings.github_token}@github.com/{owner}/{repo}.git"

        dest = tempfile.mkdtemp(prefix="pov-builder-checkout-")
        result = subprocess.run(
            ["git", "clone", "--branch", branch, "--single-branch", "--depth", "1", clone_url, dest],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(f"git clone failed for branch {branch!r}: {_scrub(result.stderr)}")
        return dest

    def run(self, *, cwd: str, command: str, env: dict[str, str] | None = None, timeout: int = 180) -> ShellResult:
        merged_env = {**os.environ, **(env or {})}
        try:
            result = subprocess.run(
                command, shell=True, cwd=cwd, env=merged_env, capture_output=True, text=True, timeout=timeout
            )
            return ShellResult(exit_code=result.returncode, stdout=_scrub(result.stdout), stderr=_scrub(result.stderr))
        except subprocess.TimeoutExpired as exc:
            return ShellResult(
                exit_code=-1,
                stdout=_scrub(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=f"timed out after {timeout}s",
            )

    def start(self, *, cwd: str, command: str, env: dict[str, str] | None = None) -> ProcessHandle:
        merged_env = {**os.environ, **(env or {})}
        log_fd, log_path = tempfile.mkstemp(prefix="pov-builder-proc-", suffix=".log")
        log_file = os.fdopen(log_fd, "w")
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            env=merged_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            # New session/process group: with shell=True, this Popen's own
            # pid is the SHELL, not the real command (e.g. `npm run dev`
            # spawns `vite` as ITS OWN child) — terminating just the shell
            # can leave grandchildren running, orphaned. start_new_session
            # makes this process the leader of its own group, so `stop()`
            # can signal the whole tree via os.killpg, not just the shell.
            start_new_session=True,
        )
        return ProcessHandle(pid=process.pid, log_path=log_path, _process=process)

    def stop(self, handle: ProcessHandle) -> None:
        if handle._process is None:
            return
        pid = handle._process.pid
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return  # already gone
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            handle._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                handle._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def wait_ready(self, *, host: str, port: int, timeout: int = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1):
                    return True
            except OSError:
                time.sleep(0.5)
        return False

    def http_get(self, url: str, timeout: int = 10) -> int | None:
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — localhost only, caller-controlled
                return resp.status
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

    def cleanup(self, path: str) -> None:
        shutil.rmtree(path, ignore_errors=True)
