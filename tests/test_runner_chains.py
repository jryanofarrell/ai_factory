"""Tests for run_chain (ADR-019 — multi-ticket chained execution)."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from factory.manifest import RepoConfig
from factory.providers.base import AgentResult
from factory.runner import RunResult, run_chain
from factory.ticket import Ticket


def _t(tid: str, title: str = "x", deps: list[str] | None = None) -> Ticket:
    return Ticket(
        id=tid,
        title=title,
        target_repo="thms-platform",
        acceptance_criteria=f"- {tid} done",
        depends_on=deps or [],
    )


def _repo(tmp_path: Path) -> RepoConfig:
    return RepoConfig(github="owner/repo", local_path=tmp_path, default_branch="main")


# Patches that make the chain runner reach the inner per-ticket loop without
# touching the filesystem or actually invoking git.
_COMMON_PATCHES = [
    ("factory.runner.check_tools", {}),
    ("factory.runner.is_dirty", {"return_value": False}),
    ("factory.runner.sync_repo", {}),
    ("factory.runner.create_branch", {}),
    ("factory.runner.has_changes", {"return_value": True}),
    ("factory.runner.get_changed_files", {"return_value": ["src/a.py"]}),
    ("factory.runner.detect_install_command", {"return_value": None}),
    ("factory.runner.detect_test_command", {"return_value": None}),
    ("factory.runner.commit", {}),
    ("factory.runner.push", {}),
    ("factory.runner.secret_scan", {"return_value": []}),
    ("factory.runner.write_run_memory", {}),
    ("factory.runner._return_to_default", {}),
    ("factory.runner._discard_uncommitted", {}),
    ("factory.runner.delete_branch", {}),
    ("factory.runner.undo_commit", {}),
]


def _enter_patches(stack: ExitStack) -> None:
    for target, kw in _COMMON_PATCHES:
        stack.enter_context(patch(target, **kw))


# ---------- Single-ticket chain ----------

def test_single_ticket_chain_delegates_to_run_ticket(tmp_path: Path) -> None:
    """run_chain([T]) calls run_ticket and wraps the result."""
    rr = RunResult(
        ticket_id="T1", success=True, pr_url="https://github.com/x/pr/1",
        branch="factory/t1-abc", duration_s=1.0,
    )
    with patch("factory.runner.run_ticket", return_value=rr) as run_ticket_mock:
        result = run_chain([_t("T1")], _repo(tmp_path))

    run_ticket_mock.assert_called_once()
    assert result.chain_id == "T1"
    assert result.success is True
    assert result.pr_url == "https://github.com/x/pr/1"
    assert len(result.per_ticket) == 1
    assert result.per_ticket[0].ticket_id == "T1"


# ---------- Multi-ticket happy path ----------

def test_multi_ticket_chain_opens_pr_with_combined_title(tmp_path: Path) -> None:
    """A three-ticket chain commits each one and opens one PR with all three."""
    with ExitStack() as stack:
        _enter_patches(stack)
        stack.enter_context(
            patch(
                "factory.runner._run_with_fallback",
                return_value=AgentResult(exit_code=0, provider="claude", tokens_used=10),
            )
        )
        create_pr_mock = stack.enter_context(
            patch("factory.runner.create_pr", return_value="https://github.com/x/pr/42")
        )
        commit_mock = stack.enter_context(patch("factory.runner.commit"))
        push_mock = stack.enter_context(patch("factory.runner.push"))

        chain = [_t("T1", "First"), _t("T2", "Second", deps=["T1"]), _t("T3", "Third", deps=["T2"])]
        result = run_chain(chain, _repo(tmp_path))

    assert result.success is True
    assert result.pr_url == "https://github.com/x/pr/42"
    assert len(result.per_ticket) == 3
    assert all(rr.success for rr in result.per_ticket)
    # One push, one PR, three commits.
    assert push_mock.call_count == 1
    assert create_pr_mock.call_count == 1
    assert commit_mock.call_count == 3
    # PR title carries the combined form.
    pr_title = create_pr_mock.call_args.kwargs["title"]
    assert "T1" in pr_title and "2 more" in pr_title
    # PR URL backfilled on every per-ticket result so Linear write-back can use it.
    assert all(rr.pr_url == "https://github.com/x/pr/42" for rr in result.per_ticket)


# ---------- Mid-chain failure ----------

def test_chain_aborts_on_agent_failure_with_branch_preserved(tmp_path: Path) -> None:
    """Ticket 2 fails → no PR, branch kept with ticket 1's commit, T3 not attempted."""
    with ExitStack() as stack:
        _enter_patches(stack)
        stack.enter_context(
            patch(
                "factory.runner._run_with_fallback",
                side_effect=[
                    AgentResult(exit_code=0, provider="claude"),    # T1 ok
                    AgentResult(exit_code=2, provider="claude"),    # T2 fails
                    AgentResult(exit_code=0, provider="claude"),    # T3 should never run
                ],
            )
        )
        create_pr_mock = stack.enter_context(patch("factory.runner.create_pr"))
        push_mock = stack.enter_context(patch("factory.runner.push"))

        chain = [_t("T1"), _t("T2", deps=["T1"]), _t("T3", deps=["T2"])]
        result = run_chain(chain, _repo(tmp_path))

    assert result.success is False
    assert result.pr_url is None
    assert result.branch is not None  # preserved for inspection
    assert "T2" in (result.error or "")
    assert len(result.per_ticket) == 2  # T1 + T2, T3 never attempted
    assert result.per_ticket[0].success is True
    assert result.per_ticket[1].success is False
    push_mock.assert_not_called()
    create_pr_mock.assert_not_called()


def test_chain_aborts_on_all_providers_exhausted(tmp_path: Path) -> None:
    """All providers exhausted mid-chain → chain aborts, branch preserved."""
    with ExitStack() as stack:
        _enter_patches(stack)
        stack.enter_context(
            patch(
                "factory.runner._run_with_fallback",
                side_effect=[
                    AgentResult(exit_code=0, provider="claude"),
                    AgentResult(
                        exit_code=-1, usage_limit_hit=True, provider="exhausted"
                    ),
                ],
            )
        )

        chain = [_t("T1"), _t("T2", deps=["T1"])]
        result = run_chain(chain, _repo(tmp_path))

    assert result.success is False
    assert result.per_ticket[0].success is True
    assert result.per_ticket[1].reason == "all_providers_quota_exhausted"


# ---------- Secret scan failure ----------

def test_chain_secret_scan_failure_undoes_all_commits_and_deletes_branch(
    tmp_path: Path,
) -> None:
    """If secret scan fires after all commits, undo every commit and delete branch."""
    with ExitStack() as stack:
        _enter_patches(stack)
        stack.enter_context(
            patch(
                "factory.runner._run_with_fallback",
                return_value=AgentResult(exit_code=0, provider="claude"),
            )
        )
        # Override the base patch — make secret_scan return leaks for this test.
        stack.enter_context(
            patch("factory.runner.secret_scan", return_value=["aws-access-key"])
        )
        create_pr_mock = stack.enter_context(patch("factory.runner.create_pr"))
        undo_mock = stack.enter_context(patch("factory.runner.undo_commit"))
        delete_branch_mock = stack.enter_context(patch("factory.runner.delete_branch"))

        chain = [_t("T1"), _t("T2", deps=["T1"]), _t("T3", deps=["T2"])]
        result = run_chain(chain, _repo(tmp_path))

    assert result.success is False
    assert "Secret scan failed" in (result.error or "")
    assert undo_mock.call_count == 3  # one per chained ticket
    delete_branch_mock.assert_called_once()
    create_pr_mock.assert_not_called()
    assert result.branch is None  # signal to caller that the branch is gone
