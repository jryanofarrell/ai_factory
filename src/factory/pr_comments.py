from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .git_ops import check_tools, commit, get_changed_files, is_dirty, push
from .manifest import RepoConfig
from .providers import AgentResult
from .quota_tracker import QuotaTracker
from .runner import _run_with_fallback


@dataclass
class PRCommentResult:
    pr_url: str
    branch: str
    provider: str
    committed: bool = False
    commit_message: str | None = None
    files_changed: list[str] = field(default_factory=list)


def address_pr_comments(
    *,
    repo: RepoConfig,
    pr_number: int,
    providers: list[str],
    quota_tracker: QuotaTracker | None,
    max_utilization: float,
    branch: str | None = None,
    dry_run: bool = False,
) -> PRCommentResult:
    """Run an executor over PR feedback and push any resulting code changes."""
    check_tools(providers=providers)
    metadata = _fetch_pr_metadata(repo.local_path, pr_number)
    review_comments = _fetch_review_comments(repo.local_path, repo.github, pr_number)
    issue_comments = _fetch_issue_comments(repo.local_path, repo.github, pr_number)

    target_branch = branch or metadata["headRefName"]
    _checkout_pr_branch(repo.local_path, pr_number, target_branch if branch else None)

    prompt = _build_prompt(
        repo=repo,
        pr_number=pr_number,
        metadata=metadata,
        review_comments=review_comments,
        issue_comments=issue_comments,
        dry_run=dry_run,
    )

    agent = _run_with_fallback(
        repo.local_path,
        prompt,
        capture_cost=True,
        budget_minutes=None,
        providers=providers,
        quota_tracker=quota_tracker,
        max_utilization=max_utilization,
    )
    if agent.exit_code != 0:
        raise RuntimeError(_agent_error(agent))

    files_changed = get_changed_files(repo.local_path)
    if dry_run:
        return PRCommentResult(
            pr_url=metadata["url"],
            branch=target_branch,
            provider=agent.provider,
            committed=False,
            files_changed=files_changed,
        )

    if is_dirty(repo.local_path):
        commit_message = f"Address PR #{pr_number} comments"
        commit(repo.local_path, commit_message)
        push(repo.local_path, target_branch)
        return PRCommentResult(
            pr_url=metadata["url"],
            branch=target_branch,
            provider=agent.provider,
            committed=True,
            commit_message=commit_message,
            files_changed=files_changed,
        )

    return PRCommentResult(
        pr_url=metadata["url"],
        branch=target_branch,
        provider=agent.provider,
        committed=False,
        files_changed=[],
    )


def _checkout_pr_branch(local_path: Path, pr_number: int, branch: str | None) -> None:
    _run_checked(["gh", "pr", "checkout", str(pr_number)], cwd=local_path)
    if branch:
        _run_checked(["git", "checkout", "-B", branch], cwd=local_path)


def _fetch_pr_metadata(local_path: Path, pr_number: int) -> dict[str, Any]:
    result = _run_checked(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "number,title,body,url,headRefName,baseRefName,author,reviews,comments",
        ],
        cwd=local_path,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _fetch_review_comments(local_path: Path, github: str, pr_number: int) -> list[dict[str, Any]]:
    return _gh_api_json(local_path, f"repos/{github}/pulls/{pr_number}/comments")


def _fetch_issue_comments(local_path: Path, github: str, pr_number: int) -> list[dict[str, Any]]:
    return _gh_api_json(local_path, f"repos/{github}/issues/{pr_number}/comments")


def _gh_api_json(local_path: Path, endpoint: str) -> list[dict[str, Any]]:
    result = _run_checked(
        ["gh", "api", endpoint, "--paginate", "--slurp"],
        cwd=local_path,
        capture_output=True,
    )
    if not result.stdout.strip():
        return []
    data = json.loads(result.stdout)
    if not isinstance(data, list):
        return [data]
    if data and all(isinstance(page, list) for page in data):
        return [item for page in data for item in page]
    return data


def _run_checked(
    cmd: list[str],
    *,
    cwd: Path,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=capture_output)
    if result.returncode != 0:
        detail = result.stderr if capture_output else ""
        raise RuntimeError(f"{' '.join(cmd)} failed" + (f":\n{detail}" if detail else ""))
    return result


def _build_prompt(
    *,
    repo: RepoConfig,
    pr_number: int,
    metadata: dict[str, Any],
    review_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    dry_run: bool,
) -> str:
    comments_json = json.dumps(
        {
            "pr": metadata,
            "issue_comments": _compact_issue_comments(issue_comments),
            "review_comments": _compact_review_comments(review_comments),
        },
        indent=2,
    )

    reply_instruction = (
        "Do not post GitHub replies because this is a dry run."
        if dry_run
        else (
            "If a comment only needs explanation or cannot be fixed in code, reply using "
            "`gh pr comment` for PR comments or the GitHub review-comment replies API "
            "for inline review comments."
        )
    )

    return f"""\
You are addressing GitHub PR review feedback in this repository.

PR: #{pr_number}
URL: {metadata.get("url")}
Branch: {metadata.get("headRefName")}
Base: {metadata.get("baseRefName")}
GitHub repo: {repo.github}

## Instructions

1. Read `AGENTS.md` and `CLAUDE.md` if present.
2. Read `.claude/memory/MEMORY.md` if present, then only relevant memory files.
3. Review the PR comments below and decide which require code changes versus replies.
4. For actionable code feedback, edit the files directly.
5. {reply_instruction}
6. Do not run git commit or git push. The factory will commit and push code changes after you stop.
7. If no code change is needed, post any necessary replies and stop.

## PR feedback context

```json
{comments_json}
```
"""


def _compact_issue_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": c.get("id"),
            "author": (c.get("user") or {}).get("login"),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "body": c.get("body"),
        }
        for c in comments
    ]


def _compact_review_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": c.get("id"),
            "author": (c.get("user") or {}).get("login"),
            "path": c.get("path"),
            "line": c.get("line") or c.get("original_line"),
            "side": c.get("side"),
            "diff_hunk": c.get("diff_hunk"),
            "body": c.get("body"),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "url": c.get("html_url"),
        }
        for c in comments
    ]


def _agent_error(agent: AgentResult) -> str:
    if agent.usage_limit_hit:
        return "All executor providers are quota-exhausted."
    if agent.timed_out:
        return "Executor timed out while addressing PR comments."
    return f"Executor failed with exit code {agent.exit_code}."
