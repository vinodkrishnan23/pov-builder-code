"""Test doubles — no live LLM/API calls anywhere in the test suite."""

from __future__ import annotations

from typing import Any


class _FakeStructuredRunnable:
    def __init__(self, result: Any):
        self._result = result

    def invoke(self, messages) -> Any:
        return self._result


class FakeLLM:
    """Stands in for a `ChatOpenAI` instance. Only implements
    `with_structured_output`, since that's the only LLM surface any node
    currently uses — extend as later phases need more (e.g. plain
    `.invoke()` returning an `AIMessage`).

    `build_graph` wires ONE llm instance into every node factory that
    needs one — transcript_analyzer wants an InitialPOVSpec back,
    pov_reviewer wants a POVReviewResult — so a single canned
    `structured_result` isn't enough once more than one schema is in play.
    `by_schema` maps schema class -> canned result for exactly that case;
    `structured_result` remains the default for tests that only ever
    exercise one schema."""

    def __init__(self, structured_result: Any = None, by_schema: dict[type, Any] | None = None):
        self._structured_result = structured_result
        self._by_schema = by_schema or {}

    def with_structured_output(self, schema: type, **kwargs: Any) -> _FakeStructuredRunnable:
        if schema in self._by_schema:
            return _FakeStructuredRunnable(self._by_schema[schema])
        return _FakeStructuredRunnable(self._structured_result)


class FakeGitRepoStore:
    """Stands in for `GitHubRepoStore` — records calls instead of hitting
    the real GitHub API, and returns deterministic, inspectable values.

    `specs_contracts_content` lets a test seed what `get_specs_contract`
    returns for a given path, since `seeder` (and later phases) read
    previously-committed contract content back."""

    def __init__(self, specs_contracts_content: dict[str, str] | None = None):
        self.commit_spec_contracts_calls: list[dict] = []
        self.resolve_repo_calls: list[dict] = []
        self.commit_code_files_calls: list[dict] = []
        self._specs_contracts_content = specs_contracts_content or {}

    def _branch(self, email: str, pov_name: str) -> str:
        return f"{email}/{pov_name}"

    def resolve_repo(self, *, email: str, pov_name: str) -> tuple[str, str, str]:
        self.resolve_repo_calls.append({"email": email, "pov_name": pov_name})
        branch = self._branch(email, pov_name)
        return "pov-builder", f"https://github.com/fake-owner/pov-builder/tree/{branch}", branch

    def commit_spec_contracts(self, *, email: str, pov_name: str, contracts: dict) -> dict:
        from pov_builder.models.technical_spec import ContractRef

        self.commit_spec_contracts_calls.append({"email": email, "pov_name": pov_name, "contracts": contracts})
        return {
            filename: ContractRef(path=f"spec_architect/{filename}", commit_sha=f"fake-sha-{filename}")
            for filename in contracts
        }

    def commit_code_files(self, *, email: str, pov_name: str, files: dict[str, str], message: str) -> str:
        self.commit_code_files_calls.append(
            {"email": email, "pov_name": pov_name, "files": files, "message": message}
        )
        return f"fake-sha-{len(self.commit_code_files_calls)}"

    def get_specs_contract(self, *, email: str, pov_name: str, path: str) -> str:
        return self._specs_contracts_content.get(path, "{}")


class FakeProcessHandle:
    """Stands in for tools.shell_sandbox.ProcessHandle — identity-only,
    no real subprocess."""


class FakeShellSandbox:
    """Stands in for `LocalShellSandbox` — no real subprocess/git calls.

    Every knob a test needs is keyed by something the node actually
    passes in, so a test can make exactly one command/port "fail" without
    having to fake the whole sequence:
    - `run_results`: {command_substring: ShellResult} — falls back to a
      successful ShellResult if the command doesn't match anything.
    - `ready_ports`: set of ports `wait_ready` should report as up
      (defaults to ALL ports ready, since most tests care about a
      specific failure, not about faking readiness for every port).
    """

    def __init__(
        self,
        *,
        run_results: dict | None = None,
        ready_ports: set[int] | None = None,
        http_results: dict | None = None,
        checkout_path: str = "/tmp/fake-checkout",
    ):
        from pov_builder.tools.shell_sandbox import ShellResult

        self._ShellResult = ShellResult
        self._run_results = run_results or {}
        self._ready_ports = ready_ports  # None => everything is ready
        # {url_substring: status_code_or_None} — defaults to 200 for any URL.
        self._http_results = http_results or {}
        self.checkout_path = checkout_path
        self.checkout_calls: list[dict] = []
        self.run_calls: list[dict] = []
        self.start_calls: list[dict] = []
        self.stop_calls: list[FakeProcessHandle] = []
        self.cleanup_calls: list[str] = []
        self.http_get_calls: list[str] = []
        self._next_port = 40000

    def checkout(self, *, email: str, pov_name: str) -> str:
        self.checkout_calls.append({"email": email, "pov_name": pov_name})
        return self.checkout_path

    def run(self, *, cwd: str, command: str, env: dict | None = None, timeout: int = 180):
        self.run_calls.append({"cwd": cwd, "command": command, "env": env, "timeout": timeout})
        for substring, result in self._run_results.items():
            if substring in command:
                return result
        return self._ShellResult(exit_code=0, stdout="", stderr="")

    def start(self, *, cwd: str, command: str, env: dict | None = None) -> FakeProcessHandle:
        handle = FakeProcessHandle()
        self.start_calls.append({"cwd": cwd, "command": command, "env": env, "handle": handle})
        return handle

    def stop(self, handle: FakeProcessHandle) -> None:
        self.stop_calls.append(handle)

    def free_port(self) -> int:
        self._next_port += 1
        return self._next_port

    def wait_ready(self, *, host: str, port: int, timeout: int = 30) -> bool:
        if self._ready_ports is None:
            return True
        return port in self._ready_ports

    def http_get(self, url: str, timeout: int = 10) -> int | None:
        self.http_get_calls.append(url)
        for substring, status in self._http_results.items():
            if substring in url:
                return status
        return 200

    def cleanup(self, path: str) -> None:
        self.cleanup_calls.append(path)


class FakePovDatabase:
    """Stands in for `MongoPovDatabaseStore` — no real Atlas connection."""

    def __init__(self, uri: str = "mongodb://fake/", db_name: str = "pov_fake_db"):
        self._uri = uri
        self._db_name = db_name
        self.provision_calls: list[dict] = []
        self.teardown_calls: list[str] = []

    def provision(self, *, email: str, pov_name: str) -> tuple[str, str]:
        self.provision_calls.append({"email": email, "pov_name": pov_name})
        return self._uri, self._db_name

    def teardown(self, db_name: str) -> None:
        self.teardown_calls.append(db_name)


class FakeJourneyRunner:
    """Stands in for `PlaywrightJourneyRunner` — no real browser."""

    def __init__(self, result=None):
        from pov_builder.tools.journey_runner import JourneyResult

        self._result = result or JourneyResult(passed=True, steps=[])
        self.run_calls: list[dict] = []

    def run(self, *, base_url: str, steps: list) -> Any:
        self.run_calls.append({"base_url": base_url, "steps": steps})
        return self._result


class FakePovRunStore:
    """Stands in for `MongoPovRunStore` — records calls instead of hitting
    a real Atlas cluster, and returns a fixed, inspectable pov_id."""

    def __init__(self, pov_id: str = "fake-pov-id"):
        self.pov_id = pov_id
        self.calls: list[dict] = []
        self.save_spec_artifacts_calls: list[dict] = []

    def save_transcript_analysis(
        self,
        *,
        user_email: str,
        pov_name: str,
        transcript: str,
        initial_spec: Any,
        pov_id: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "user_email": user_email,
                "pov_name": pov_name,
                "transcript": transcript,
                "initial_spec": initial_spec,
                "pov_id": pov_id,
            }
        )
        return pov_id or self.pov_id

    def save_spec_artifacts(self, *, pov_id: str, artifacts: dict) -> None:
        self.save_spec_artifacts_calls.append({"pov_id": pov_id, "artifacts": artifacts})
