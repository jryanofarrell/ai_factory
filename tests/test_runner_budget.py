import subprocess
from pathlib import Path
from unittest.mock import patch

from factory.manifest import RepoConfig
from factory.providers.base import AgentResult
from factory.runner import run_ticket
from factory.ticket import Ticket


def test_run_ticket_does_not_enforce_ticket_budget_minutes(tmp_path: Path) -> None:
    ticket = Ticket(
        id="THM-1",
        title="Do work",
        target_repo="thms-platform",
        acceptance_criteria="- Done",
        budget_minutes=5,
    )
    repo = RepoConfig(
        github="owner/repo",
        local_path=tmp_path,
        default_branch="main",
    )

    with (
        patch("factory.runner.check_tools"),
        patch("factory.runner.is_dirty", return_value=False),
        patch("factory.runner.sync_repo"),
        patch("factory.runner.create_branch"),
        patch("factory.runner.has_changes", return_value=False),
        patch("factory.runner.delete_branch"),
        patch(
            "factory.runner._run_with_fallback",
            return_value=AgentResult(exit_code=0, provider="codex"),
        ) as run_with_fallback,
    ):
        run_ticket(ticket, repo)

    assert run_with_fallback.call_args.kwargs["budget_minutes"] is None


def test_run_ticket_attempts_one_test_repair(tmp_path: Path) -> None:
    ticket = Ticket(
        id="THM-2",
        title="Fix tests",
        target_repo="thms-platform",
        acceptance_criteria="- Tests pass",
    )
    repo = RepoConfig(
        github="owner/repo",
        local_path=tmp_path,
        default_branch="main",
        test_command="npm test",
    )

    with (
        patch("factory.runner.check_tools"),
        patch("factory.runner.is_dirty", return_value=False),
        patch("factory.runner.sync_repo"),
        patch("factory.runner.create_branch"),
        patch("factory.runner.has_changes", return_value=True),
        patch("factory.runner.get_changed_files", return_value=["src/file.ts"]),
        patch("factory.runner.check_scope", return_value=[]),
        patch("factory.runner.detect_install_command", return_value=None),
        patch(
            "factory.runner.run_shell_command",
            side_effect=[
                subprocess.CompletedProcess("npm test", 1),
                subprocess.CompletedProcess("npm test", 0),
            ],
        ) as run_shell,
        patch("factory.runner._write_and_verify_memory"),
        patch("factory.runner.commit"),
        patch("factory.runner.secret_scan", return_value=[]),
        patch(
            "factory.runner._run_with_fallback",
            side_effect=[
                AgentResult(exit_code=0, provider="codex"),
                AgentResult(exit_code=0, provider="codex"),
            ],
        ) as run_with_fallback,
    ):
        result = run_ticket(ticket, repo, dry_run=True, executor_providers=["codex"])

    assert result.success is True
    assert run_shell.call_count == 2
    assert run_with_fallback.call_count == 2
    assert "verification command failed" in run_with_fallback.call_args.args[1]


def test_run_ticket_treats_scope_violations_as_advisory(tmp_path: Path) -> None:
    ticket = Ticket(
        id="THM-3",
        title="Do scoped work",
        target_repo="thms-platform",
        acceptance_criteria="- Done",
        scope_paths=["src/**"],
    )
    repo = RepoConfig(
        github="owner/repo",
        local_path=tmp_path,
        default_branch="main",
    )

    with (
        patch("factory.runner.check_tools"),
        patch("factory.runner.is_dirty", return_value=False),
        patch("factory.runner.sync_repo"),
        patch("factory.runner.create_branch"),
        patch("factory.runner.has_changes", return_value=True),
        patch("factory.runner.get_changed_files", return_value=["src/file.ts", "README.md"]),
        patch("factory.runner.check_scope", return_value=["README.md"]),
        patch("factory.runner.detect_install_command", return_value=None),
        patch("factory.runner.detect_test_command", return_value=None),
        patch("factory.runner._write_and_verify_memory"),
        patch("factory.runner.commit") as commit,
        patch("factory.runner.secret_scan", return_value=[]),
        patch(
            "factory.runner._run_with_fallback",
            return_value=AgentResult(exit_code=0, provider="codex"),
        ),
    ):
        result = run_ticket(ticket, repo, dry_run=True, executor_providers=["codex"])

    assert result.success is True
    assert result.scope_violations == ["README.md"]
    commit.assert_called_once()
