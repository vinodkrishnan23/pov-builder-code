"""Tests for new.md Phase 10 — howto_helper.

Same honesty boundary as every other real phase: deterministic wiring
(short-circuits, grounding the prompt in real prior facts, committing
README.md, assembling ReadmeStatus) is unit-testable without a live LLM/
GitHub call; whether the generated README is actually accurate needs a
real run — see README "How to execute the graph".
"""

from __future__ import annotations

from datetime import datetime, timezone

from pov_builder.graph.nodes import make_howto_helper
from pov_builder.models.common import AgentRunStatus
from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.repository import ComponentCommit, RepositoryInfo
from pov_builder.models.technical_spec import DetailedTechnicalSpec
from pov_builder.prompts.howto_helper import SYSTEM_PROMPT, HowToReadmeOutput, build_messages

from tests.fakes import FakeGitRepoStore, FakeLLM


def test_missing_technical_spec_short_circuits_without_calling_llm_or_git():
    llm = FakeLLM(structured_result=HowToReadmeOutput(readme_content="should never be committed"))
    git = FakeGitRepoStore()
    node = make_howto_helper(llm, git)

    result = node(
        {
            "technical_spec": None,
            "repository": RepositoryInfo(repo_name="pov-builder"),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    assert result["readme_status"].committed is False
    assert git.commit_code_files_calls == []
    assert result["agent_statuses"]["howto_helper"].status == AgentRunStatus.SUCCEEDED


def test_missing_repo_name_short_circuits():
    node = make_howto_helper(FakeLLM(), FakeGitRepoStore())

    result = node(
        {
            "technical_spec": DetailedTechnicalSpec(),
            "repository": RepositoryInfo(repo_name=None),
            "user_email": "a@b.com",
            "pov_name": "demo",
        }
    )

    assert result["readme_status"].committed is False


def test_missing_email_or_pov_name_short_circuits():
    node = make_howto_helper(FakeLLM(), FakeGitRepoStore())

    result = node(
        {
            "technical_spec": DetailedTechnicalSpec(),
            "repository": RepositoryInfo(repo_name="pov-builder"),
            "user_email": "",
            "pov_name": "demo",
        }
    )

    assert result["readme_status"].committed is False


def test_real_path_commits_readme_and_assembles_status():
    raw = HowToReadmeOutput(
        readme_content="# Order Status Chatbot\n\nA real README.",
        verified_commands=["node seed.js", "node server.js"],
        known_limitations=["frontend search is not implemented"],
    )
    git = FakeGitRepoStore()
    node = make_howto_helper(FakeLLM(structured_result=raw), git)

    result = node(
        {
            "technical_spec": DetailedTechnicalSpec(system_architecture="a real architecture"),
            "repository": RepositoryInfo(repo_name="pov-builder", branch="a@b.com/order-status-chatbot"),
            "user_email": "a@b.com",
            "pov_name": "order-status-chatbot",
            "seeder_commit": ComponentCommit(
                component="seed",
                commit_sha="abc",
                committed_at=datetime.now(timezone.utc),
                details={"seed_command": "node seed.js"},
            ),
        }
    )

    assert len(git.commit_code_files_calls) == 1
    call = git.commit_code_files_calls[0]
    assert call["email"] == "a@b.com"
    assert call["pov_name"] == "order-status-chatbot"
    assert call["files"] == {"README.md": raw.readme_content}

    status = result["readme_status"]
    assert status.committed is True
    assert status.verified_commands == ["node seed.js", "node server.js"]
    assert status.known_limitations == ["frontend search is not implemented"]


def test_prompt_forbids_inventing_unverified_commands():
    lowered = SYSTEM_PROMPT.lower()
    assert "do not invent" in lowered
    assert "not available" in lowered


def test_prompt_lists_all_required_sections():
    for section in ("mongodb atlas setup", "primary demo journey", "known limitations", "troubleshooting"):
        assert section in SYSTEM_PROMPT.lower()


def test_build_messages_includes_all_the_grounding_facts():
    messages = build_messages(
        InitialPOVSpec(executive_summary="a very specific summary"),
        {"marker": "very-specific-technical-spec-summary"},
        {"marker": "very-specific-repository-summary"},
        {"marker": "very-specific-implemented-details"},
        {"marker": "very-specific-validation-summary"},
    )
    combined = " ".join(str(m.content) for m in messages)
    assert "a very specific summary" in combined
    assert "very-specific-technical-spec-summary" in combined
    assert "very-specific-repository-summary" in combined
    assert "very-specific-implemented-details" in combined
    assert "very-specific-validation-summary" in combined
