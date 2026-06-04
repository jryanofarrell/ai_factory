from pathlib import Path
from unittest.mock import patch

from factory.orchestrator import _write_back, run
from factory.runner import RunResult
from factory.sync import PullResult
from factory.ticket import Ticket


class FakeLinearClient:
    def __init__(self) -> None:
        self.comments: list[tuple[str, str]] = []

    def comment_on_issue(self, issue_id: str, body: str) -> None:
        self.comments.append((issue_id, body))

    def get_state_id(self, team_key: str, state_name: str) -> None:
        return None

    def get_label_id(self, team_key: str, label_name: str) -> None:
        return None


def test_write_back_includes_scope_advisory_on_success() -> None:
    client = FakeLinearClient()
    ticket = Ticket(
        id="THM-1",
        title="Do work",
        target_repo="thms-platform",
        acceptance_criteria="- Done",
        linear_id="linear-1",
    )
    result = RunResult(
        ticket_id="THM-1",
        success=True,
        pr_url="https://github.com/owner/repo/pull/1",
        scope_violations=["README.md"],
    )

    _write_back(client, ticket, "THMS", result)  # type: ignore[arg-type]

    assert client.comments == [
        (
            "linear-1",
            "PR opened: https://github.com/owner/repo/pull/1\n"
            "Duration: 0s · Cost: n/a\n\n"
            "Scope advisory: changed files outside ticket scope:\n"
            "- `README.md`",
        )
    ]


def test_run_uses_fresh_linear_tickets_not_stale_local_queue(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    repo_path = tmp_path / "target"
    queue_dir = tmp_path / ".factory" / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "hel-1.md").write_text(
        "---\n"
        "id: HEL-1\n"
        "title: stale\n"
        "target_repo: hello\n"
        "---\n\n"
        "## Acceptance Criteria\n\n"
        "- stale\n"
    )
    manifest_path.write_text(
        "\n".join(
            [
                "version: 1",
                "queue_dir: .factory/queue",
                "repos:",
                "  hello:",
                "    github: owner/hello",
                "    default_branch: main",
                "    linear_team: HEL",
                f"    local_path: {repo_path}",
            ]
        )
    )
    fresh_ticket = Ticket(
        id="HEL-2",
        title="fresh",
        target_repo="hello",
        acceptance_criteria="- fresh",
    )

    from factory.runner import ChainResult

    with (
        patch(
            "factory.orchestrator.pull_tickets",
            return_value=PullResult(tickets=[fresh_ticket], written=["HEL-2"]),
        ) as pull,
        patch(
            "factory.orchestrator.run_chain",
            return_value=ChainResult(
                chain_id="HEL-2",
                branch=None,
                pr_url=None,
                per_ticket=[RunResult(ticket_id="HEL-2", success=True, dry_run=True)],
                success=True,
            ),
        ) as run_chain_mock,
    ):
        run(manifest_path=manifest_path, no_cleanup=True, dry_run=True, api_key="test")

    pull.assert_called_once()
    # run_chain receives a single-ticket chain.
    chain_arg = run_chain_mock.call_args.args[0]
    assert len(chain_arg) == 1
    assert chain_arg[0].id == "HEL-2"
