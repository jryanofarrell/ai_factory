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


def test_recipes_subtasks_depends_on_survive_linear_pull():
    """Regression: Recipes / Subtasks / Depends On must round-trip from a
    Linear description, or pulled tickets run single-shot, recipe-less, and
    unchained (the BIL-4..9 incident, 2026-07-13)."""
    issue = dict(FIXTURE_ISSUE)
    issue["description"] = (
        "## Acceptance Criteria\n\n- works\n\n"
        "## Subtasks\n\n"
        "### 1. Create the model\n"
        "- Files: src/app/models.py\n"
        "- Recipe: .ai/recipes/core/module.md\n"
        "- Depends on: (none)\n\n"
        "Add a `Widget` dataclass with fields `id: int` and `name: str`.\n\n"
        "### 2. Test the model\n"
        "- Files: tests/test_models.py\n"
        "- Recipe: .ai/recipes/core/testing.md\n"
        "- Depends on: 1\n\n"
        "Round-trip test for `Widget`.\n\n"
        "## Depends On\n\n"
        "THM-4\n\n"
        "## Recipes\n\n"
        ".ai/recipes/core/module.md\n"
        ".ai/recipes/core/testing.md\n"
    )
    ticket = _issue_to_ticket(issue, MANIFEST, "thms-platform")

    assert ticket.depends_on == ["THM-4"]
    assert ticket.recipes == [
        ".ai/recipes/core/module.md",
        ".ai/recipes/core/testing.md",
    ]
    assert len(ticket.subtasks) == 2
    assert ticket.subtasks[0].files == ["src/app/models.py"]
    assert ticket.subtasks[0].recipe == ".ai/recipes/core/module.md"
    assert ticket.subtasks[0].depends_on == []
    assert ticket.subtasks[1].depends_on == ["1"]
    assert "Widget" in ticket.subtasks[0].changes
    assert ticket.raw_body == issue["description"]


def test_depends_on_none_sentinel_from_linear():
    issue = dict(FIXTURE_ISSUE)
    issue["description"] = "## Acceptance Criteria\n\n- works\n\n## Depends On\n\n(none)\n"
    ticket = _issue_to_ticket(issue, MANIFEST, "thms-platform")
    assert ticket.depends_on == []


def test_depends_on_survives_linear_autolinking():
    """Linear rewrites bare issue IDs into markdown links when saving a
    description; the parsed dep must be the bare ID, not the link."""
    issue = dict(FIXTURE_ISSUE)
    issue["description"] = (
        "## Acceptance Criteria\n\n- works\n\n"
        "## Depends On\n\n"
        "[BIL-6](https://linear.app/jryanofarrell-ai-factory/issue/BIL-6/web-pipeline)\n"
        "[BIL-7](https://linear.app/jryanofarrell-ai-factory/issue/BIL-7/pdf-pipeline)\n"
    )
    ticket = _issue_to_ticket(issue, MANIFEST, "thms-platform")
    assert ticket.depends_on == ["BIL-6", "BIL-7"]


def _linear_issue(identifier: str, deps: list[str], linkify: bool = True) -> dict:
    """A Linear-shaped issue whose Depends On is autolinkified the way Linear
    saves it (inconsistently — some IDs stay bare, which is also real)."""
    if deps:
        dep_lines = "\n".join(
            f"[{d}](https://linear.app/test/issue/{d.lower()}/some-title)" if linkify else d
            for d in deps
        )
        dep_section = f"## Depends On\n\n{dep_lines}\n\n"
    else:
        dep_section = ""
    return {
        "identifier": identifier,
        "title": f"{identifier} work",
        "description": (
            "## Acceptance Criteria\n\n- works\n\n"
            "## Subtasks\n\n"
            "### 1. Do the thing\n"
            "- Files: src/x.py\n"
            "- Recipe: .ai/recipes/core/module.md\n"
            "- Depends on: (none)\n\n"
            "Change src/x.py.\n\n"
            f"{dep_section}"
            "## Recipes\n\n.ai/recipes/core/module.md\n"
        ),
        "url": f"https://linear.app/test/issue/{identifier}",
        "state": {"name": "Ready for Agent"},
        "team": {"key": "THM"},
        "labels": {"nodes": []},
    }


def test_full_queue_scenario_groups_into_one_chain():
    """End-to-end regression for the BIL-4..9 incident: six Linear issues in a
    diamond (4 <- 5 <- {6,7} <- 8 <- 9), pulled newest-first with autolinkified
    AND bare deps mixed, must group into ONE topo-ordered chain with subtasks
    and recipes intact — nothing skipped, nothing running out of order."""
    from factory.chains import group_into_chains

    issues = [
        _linear_issue("BIL-9", ["BIL-8"], linkify=False),   # bare, like the real data
        _linear_issue("BIL-8", ["BIL-6", "BIL-7"]),
        _linear_issue("BIL-7", ["BIL-5"]),
        _linear_issue("BIL-6", ["BIL-5"]),
        _linear_issue("BIL-5", ["BIL-4"], linkify=False),
        _linear_issue("BIL-4", []),
    ]
    tickets = [_issue_to_ticket(i, MANIFEST, "thms-platform") for i in issues]
    for t in tickets:
        assert t.subtasks, f"{t.id} lost its subtasks"
        assert t.recipes, f"{t.id} lost its recipes"

    grouped = group_into_chains(tickets)

    assert grouped.skipped_unsatisfied == []
    assert grouped.skipped_cross_repo == []
    assert len(grouped.chains) == 1
    order = [t.id for t in grouped.chains[0]]
    assert len(order) == 6
    pos = {tid: i for i, tid in enumerate(order)}
    assert pos["BIL-4"] < pos["BIL-5"]
    assert pos["BIL-5"] < pos["BIL-6"] and pos["BIL-5"] < pos["BIL-7"]
    assert pos["BIL-6"] < pos["BIL-8"] and pos["BIL-7"] < pos["BIL-8"]
    assert pos["BIL-8"] < pos["BIL-9"]
