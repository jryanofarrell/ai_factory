import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.manifest import RepoConfig
from factory.pr_comments import _gh_api_json, address_pr_comments
from factory.providers.base import AgentResult


def _repo(tmp_path: Path) -> RepoConfig:
    return RepoConfig(
        github="owner/repo",
        local_path=tmp_path,
        default_branch="main",
    )


def test_gh_api_json_flattens_paginated_slurp(tmp_path: Path) -> None:
    payload = [[{"id": 1}], [{"id": 2}]]
    with patch(
        "factory.pr_comments._run_checked",
        return_value=subprocess.CompletedProcess(["gh"], 0, stdout=json.dumps(payload)),
    ):
        assert _gh_api_json(tmp_path, "repos/owner/repo/pulls/1/comments") == [
            {"id": 1},
            {"id": 2},
        ]


def test_address_pr_comments_commits_and_pushes_when_executor_edits(tmp_path: Path) -> None:
    metadata = {
        "number": 7,
        "title": "PR",
        "body": "",
        "url": "https://github.com/owner/repo/pull/7",
        "headRefName": "feature/pr",
        "baseRefName": "main",
        "author": {"login": "alice"},
        "reviews": [],
        "comments": [],
    }

    with (
        patch("factory.pr_comments.check_tools"),
        patch("factory.pr_comments._fetch_pr_metadata", return_value=metadata),
        patch(
            "factory.pr_comments._fetch_review_comments",
            return_value=[{"id": 1, "body": "Fix this"}],
        ),
        patch("factory.pr_comments._fetch_issue_comments", return_value=[]),
        patch("factory.pr_comments._checkout_pr_branch") as checkout,
        patch(
            "factory.pr_comments._run_with_fallback",
            return_value=AgentResult(exit_code=0, provider="codex"),
        ) as run_agent,
        patch("factory.pr_comments.get_changed_files", return_value=["src/app.py"]),
        patch("factory.pr_comments.is_dirty", return_value=True),
        patch("factory.pr_comments.commit") as commit,
        patch("factory.pr_comments.push") as push,
    ):
        result = address_pr_comments(
            repo=_repo(tmp_path),
            pr_number=7,
            providers=["codex"],
            quota_tracker=None,
            max_utilization=0.9,
        )

    checkout.assert_called_once_with(tmp_path, 7, None)
    assert "Fix this" in run_agent.call_args.args[1]
    commit.assert_called_once_with(tmp_path, "Address PR #7 comments")
    push.assert_called_once_with(tmp_path, "feature/pr")
    assert result.committed is True
    assert result.files_changed == ["src/app.py"]


def test_address_pr_comments_uses_optional_branch(tmp_path: Path) -> None:
    metadata = {
        "number": 8,
        "title": "PR",
        "body": "",
        "url": "https://github.com/owner/repo/pull/8",
        "headRefName": "feature/pr",
        "baseRefName": "main",
        "author": {"login": "alice"},
        "reviews": [],
        "comments": [],
    }

    with (
        patch("factory.pr_comments.check_tools"),
        patch("factory.pr_comments._fetch_pr_metadata", return_value=metadata),
        patch("factory.pr_comments._fetch_review_comments", return_value=[]),
        patch("factory.pr_comments._fetch_issue_comments", return_value=[]),
        patch("factory.pr_comments._checkout_pr_branch") as checkout,
        patch(
            "factory.pr_comments._run_with_fallback",
            return_value=AgentResult(exit_code=0, provider="codex"),
        ),
        patch("factory.pr_comments.get_changed_files", return_value=[]),
        patch("factory.pr_comments.is_dirty", return_value=False),
        patch("factory.pr_comments.commit") as commit,
        patch("factory.pr_comments.push") as push,
    ):
        result = address_pr_comments(
            repo=_repo(tmp_path),
            pr_number=8,
            providers=["codex"],
            quota_tracker=None,
            max_utilization=0.9,
            branch="review/fix-pr-8",
        )

    checkout.assert_called_once_with(tmp_path, 8, "review/fix-pr-8")
    commit.assert_not_called()
    push.assert_not_called()
    assert result.branch == "review/fix-pr-8"
    assert result.committed is False
