import pytest
from pathlib import Path
from factory.ticket import parse_ticket, Ticket

VALID_TICKET = """\
---
id: THMS-42
title: Add CHANGELOG entry for AI factory test
target_repo: thms-platform
scope_paths:
  - CHANGELOG.md
budget_tokens: 10000
budget_minutes: 5
---

## Acceptance Criteria

- A new line is added to `CHANGELOG.md` under an "Unreleased" section.
- The line reads "Add AI factory test entry".
- All existing tests still pass.
- No other files are modified.

## Notes

This is a hello-world ticket to verify the executor pipeline works.
"""


def write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "ticket.md"
    p.write_text(content)
    return p


def test_happy_path(tmp_path: Path) -> None:
    t = parse_ticket(write(tmp_path, VALID_TICKET))
    assert t.id == "THMS-42"
    assert t.title == "Add CHANGELOG entry for AI factory test"
    assert t.target_repo == "thms-platform"
    assert "CHANGELOG.md" in t.scope_paths
    assert t.budget_tokens == 10000
    assert t.budget_minutes == 5
    assert "Unreleased" in t.acceptance_criteria
    assert "hello-world" in t.notes


def test_missing_id(tmp_path: Path) -> None:
    bad = VALID_TICKET.replace("id: THMS-42\n", "")
    with pytest.raises(ValueError, match="'id'"):
        parse_ticket(write(tmp_path, bad))


def test_missing_title(tmp_path: Path) -> None:
    bad = VALID_TICKET.replace("title: Add CHANGELOG entry for AI factory test\n", "")
    with pytest.raises(ValueError, match="'title'"):
        parse_ticket(write(tmp_path, bad))


def test_missing_target_repo(tmp_path: Path) -> None:
    bad = VALID_TICKET.replace("target_repo: thms-platform\n", "")
    with pytest.raises(ValueError, match="'target_repo'"):
        parse_ticket(write(tmp_path, bad))


def test_missing_acceptance_criteria(tmp_path: Path) -> None:
    bad = VALID_TICKET.replace("## Acceptance Criteria", "## Success Criteria")
    with pytest.raises(ValueError, match="Acceptance Criteria"):
        parse_ticket(write(tmp_path, bad))


def test_no_frontmatter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frontmatter"):
        parse_ticket(write(tmp_path, "# Just a heading\n\nSome text."))


def test_defaults_applied(tmp_path: Path) -> None:
    minimal = """\
---
id: THMS-1
title: My ticket
target_repo: my-repo
---

## Acceptance Criteria

- Something happens.
"""
    t = parse_ticket(write(tmp_path, minimal))
    assert t.budget_tokens == 50_000
    assert t.budget_minutes == 30
    assert t.scope_paths == []
    assert t.notes == ""
    assert t.linear_url is None


def test_notes_optional(tmp_path: Path) -> None:
    no_notes = VALID_TICKET.replace("\n## Notes\n\nThis is a hello-world ticket to verify the executor pipeline works.\n", "")
    t = parse_ticket(write(tmp_path, no_notes))
    assert t.notes == ""
