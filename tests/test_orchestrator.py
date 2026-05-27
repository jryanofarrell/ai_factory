from factory.orchestrator import _write_back
from factory.runner import RunResult
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
