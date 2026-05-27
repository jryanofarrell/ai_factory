from pathlib import Path

import pytest

from factory.manifest import Manifest, RepoConfig
from factory.sync import _hash, _issue_to_ticket, pull_tickets

# Fixture capturing real Linear API response shape (from Phase 2 spike)
FIXTURE_ISSUE = {
    "identifier": "THM-5",
    "title": "Add health check endpoint",
    "description": (
        "## Acceptance Criteria\n\n"
        "- GET /health returns 200\n"
        '- Response body is `{"status": "ok"}`\n\n'
        "## Scope Paths\n\n"
        "apps/api/src/**\n\n"
        "## Budget\n\n"
        "tokens: 20000\n"
        "minutes: 15\n\n"
        "## Notes\n\n"
        "Keep it simple — just a static response."
    ),
    "url": "https://linear.app/test/issue/THM-5/add-health-check",
    "state": {"name": "Ready for Agent"},
    "team": {"key": "THM"},
    "labels": {"nodes": []},
}

MANIFEST = Manifest(
    version=1,
    repos={
        "thms-platform": RepoConfig(
            github="toms-hms/thms-platform",
            local_path=Path("/tmp/thms-platform"),
            default_branch="main",
            linear_team="THM",
        )
    },
)


def test_happy_path():
    ticket = _issue_to_ticket(FIXTURE_ISSUE, MANIFEST, "thms-platform")
    assert ticket.id == "THM-5"
    assert ticket.title == "Add health check endpoint"
    assert ticket.target_repo == "thms-platform"
    assert "GET /health" in ticket.acceptance_criteria
    assert "apps/api/src/**" in ticket.scope_paths
    assert ticket.budget_tokens == 20000
    assert ticket.budget_minutes == 15
    assert "static response" in ticket.notes
    assert ticket.linear_url == "https://linear.app/test/issue/THM-5/add-health-check"


def test_target_repo_override():
    issue = {
        **FIXTURE_ISSUE,
        "description": "## Acceptance Criteria\n\n- done\n\n## Target Repo\n\nthms-platform",
    }
    ticket = _issue_to_ticket(issue, MANIFEST, "wrong-default")
    assert ticket.target_repo == "thms-platform"


def test_missing_acceptance_criteria_raises():
    issue = {**FIXTURE_ISSUE, "description": "Just some text with no sections."}
    with pytest.raises(ValueError, match="Acceptance Criteria"):
        _issue_to_ticket(issue, MANIFEST, "thms-platform")


def test_defaults_applied_when_no_budget_section():
    issue = {**FIXTURE_ISSUE, "description": "## Acceptance Criteria\n\n- done"}
    ticket = _issue_to_ticket(issue, MANIFEST, "thms-platform")
    assert ticket.budget_tokens == 50_000
    assert ticket.budget_minutes == 30


def test_scope_paths_strips_comments_and_blanks():
    issue = {
        **FIXTURE_ISSUE,
        "description": (
            "## Acceptance Criteria\n\n- done\n\n"
            "## Scope Paths\n\n"
            "# this is a comment\n\n"
            "apps/api/src/**\n"
            "CHANGELOG.md\n"
        ),
    }
    ticket = _issue_to_ticket(issue, MANIFEST, "thms-platform")
    assert ticket.scope_paths == ["apps/api/src/**", "CHANGELOG.md"]


def test_to_markdown_round_trips(tmp_path):
    from factory.ticket import parse_ticket

    ticket = _issue_to_ticket(FIXTURE_ISSUE, MANIFEST, "thms-platform")
    md = ticket.to_markdown()
    p = tmp_path / "ticket.md"
    p.write_text(md)
    parsed = parse_ticket(p)
    assert parsed.id == ticket.id
    assert parsed.title == ticket.title
    assert parsed.acceptance_criteria == ticket.acceptance_criteria
    assert parsed.scope_paths == ticket.scope_paths
    assert parsed.budget_tokens == ticket.budget_tokens


def test_idempotency_hash():
    content = "same content"
    assert _hash(content) == _hash(content)
    assert _hash(content) != _hash("different content")


def test_pull_tickets_can_return_ready_tickets_without_writing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        "\n".join(
            [
                "version: 1",
                "queue_dir: .factory/queue",
                "repos:",
                "  thms-platform:",
                "    github: toms-hms/thms-platform",
                "    default_branch: main",
                "    linear_team: THM",
            ]
        )
    )

    class FakeLinearClient:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def get_ready_issues(self, team_key: str) -> list[dict]:
            assert team_key == "THM"
            return [FIXTURE_ISSUE]

    monkeypatch.setattr("factory.sync.LinearClient", FakeLinearClient)

    result = pull_tickets(manifest_path=manifest_path, api_key="test", write_files=False)

    assert [ticket.id for ticket in result.tickets] == ["THM-5"]
    assert result.written == ["THM-5"]
    assert not (tmp_path / ".factory" / "queue" / "thm-5.md").exists()
