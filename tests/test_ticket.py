from pathlib import Path

import pytest

from factory.ticket import find_scope_recipe_mismatches, parse_ticket

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

## Summary

Adds an "Add AI factory test entry" line to the CHANGELOG under Unreleased to
verify the executor pipeline end-to-end.

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


def test_summary_parsed(tmp_path: Path) -> None:
    t = parse_ticket(write(tmp_path, VALID_TICKET))
    assert "Add AI factory test entry" in t.summary


def test_missing_summary(tmp_path: Path) -> None:
    bad = VALID_TICKET.replace("## Summary", "## Overview")
    with pytest.raises(ValueError, match="Summary"):
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

## Summary

Does the minimal something for a defaults test.

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
    no_notes = VALID_TICKET.replace(
        "\n## Notes\n\nThis is a hello-world ticket to verify the executor pipeline works.\n", ""
    )
    t = parse_ticket(write(tmp_path, no_notes))
    assert t.notes == ""


def _scaffold_recipes(repo_root: Path, *recipes: str) -> None:
    for rel in recipes:
        p = repo_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# recipe\n")


def test_scope_recipe_mismatch_warns_when_recipe_missing_from_scope(tmp_path: Path) -> None:
    _scaffold_recipes(tmp_path, ".ai/recipes/backend/router.md")
    desc = "## Acceptance Criteria\n\n- Thing.\n\n## Scope Paths\n\nbackend/routers/threads.py\n"
    warnings = find_scope_recipe_mismatches(desc, tmp_path)
    assert warnings == [
        "scope touches backend/ but .ai/recipes/backend/router.md is not in Scope Paths"
    ]


def test_scope_recipe_mismatch_silent_when_recipe_listed(tmp_path: Path) -> None:
    _scaffold_recipes(tmp_path, ".ai/recipes/backend/router.md")
    desc = (
        "## Acceptance Criteria\n\n- Thing.\n\n"
        "## Scope Paths\n\nbackend/routers/threads.py\n.ai/recipes/backend/router.md\n"
    )
    assert find_scope_recipe_mismatches(desc, tmp_path) == []


def test_scope_recipe_mismatch_silent_when_area_not_touched(tmp_path: Path) -> None:
    _scaffold_recipes(tmp_path, ".ai/recipes/backend/router.md")
    desc = "## Acceptance Criteria\n\n- Thing.\n\n## Scope Paths\n\nfrontend/components/Foo.tsx\n"
    assert find_scope_recipe_mismatches(desc, tmp_path) == []


def test_scope_recipe_mismatch_ignores_ai_structure_and_top_level(tmp_path: Path) -> None:
    _scaffold_recipes(
        tmp_path,
        ".ai/recipes/ai-structure.md",
        ".ai/recipes/orphan.md",
        ".ai/recipes/backend/router.md",
    )
    desc = "## Acceptance Criteria\n\n- Thing.\n\n## Scope Paths\n\nbackend/routers/threads.py\n"
    warnings = find_scope_recipe_mismatches(desc, tmp_path)
    assert warnings == [
        "scope touches backend/ but .ai/recipes/backend/router.md is not in Scope Paths"
    ]


def test_scope_recipe_mismatch_no_recipes_dir(tmp_path: Path) -> None:
    desc = "## Acceptance Criteria\n\n- Thing.\n\n## Scope Paths\n\nbackend/routers/threads.py\n"
    assert find_scope_recipe_mismatches(desc, tmp_path) == []


SUBTASK_TICKET = """\
---
id: THM-99
title: Sample with subtasks
target_repo: thms-platform
---

## Summary

Adds the myVendor slice (schema, manager, routes) across three subtasks.

## Acceptance Criteria

- Feature works end to end.

## Subtasks

### 1. Add my_vendors Drizzle schema
- Files: apps/api/src/myVendor/schema.ts
- Recipe: .ai/recipes/api/schema.md
- Tier: local
- Depends on: (none)

Add the Drizzle table for my_vendors with fields vendor_id (nullable FK),
email, name, notes.

### 2. Add myVendor Manager
- Files: apps/api/src/myVendor/Manager.ts
- Recipe: .ai/recipes/api/manager.md
- Tier: local
- Depends on: 1

Standard CRUD manager: findById, findByUser, create, update, delete.

### 3. Add myVendor routes
- Files: apps/api/src/myVendor/route.ts, apps/api/src/myVendor/__tests__/route.test.ts
- Recipe: .ai/recipes/api/route.md
- Tier: hosted
- Depends on: 1, 2

Wire HTTP handlers and the wire+auth test.
"""


def test_subtasks_parsed(tmp_path: Path) -> None:
    t = parse_ticket(write(tmp_path, SUBTASK_TICKET))
    assert len(t.subtasks) == 3

    s1, s2, s3 = t.subtasks
    assert s1.id == "1"
    assert s1.title == "Add my_vendors Drizzle schema"
    assert s1.files == ["apps/api/src/myVendor/schema.ts"]
    assert s1.recipe == ".ai/recipes/api/schema.md"
    assert s1.tier_hint == "local"
    assert s1.depends_on == []
    assert "my_vendors" in s1.changes

    assert s2.depends_on == ["1"]
    assert s2.tier_hint == "local"
    assert "CRUD" in s2.changes

    assert s3.tier_hint == "hosted"
    assert s3.depends_on == ["1", "2"]
    assert s3.files == [
        "apps/api/src/myVendor/route.ts",
        "apps/api/src/myVendor/__tests__/route.test.ts",
    ]


def test_subtasks_empty_when_section_absent(tmp_path: Path) -> None:
    t = parse_ticket(write(tmp_path, VALID_TICKET))
    assert t.subtasks == []


def test_subtask_section_with_only_whitespace(tmp_path: Path) -> None:
    body = VALID_TICKET.replace(
        "## Acceptance Criteria",
        "## Subtasks\n\n   \n\n## Acceptance Criteria",
    )
    t = parse_ticket(write(tmp_path, body))
    assert t.subtasks == []


DEPENDS_TICKET = """\
---
id: THM-19
title: Depends on two others
target_repo: thms-platform
---

## Summary

Depends on two other tickets.

## Acceptance Criteria

- x

## Depends On

THM-17
THM-18
"""


def test_depends_on_parsed(tmp_path: Path) -> None:
    t = parse_ticket(write(tmp_path, DEPENDS_TICKET))
    assert t.depends_on == ["THM-17", "THM-18"]


def test_depends_on_empty_when_section_absent(tmp_path: Path) -> None:
    t = parse_ticket(write(tmp_path, VALID_TICKET))
    assert t.depends_on == []


def test_depends_on_none_sentinel(tmp_path: Path) -> None:
    body = DEPENDS_TICKET.replace("THM-17\nTHM-18", "(none)")
    t = parse_ticket(write(tmp_path, body))
    assert t.depends_on == []


def test_depends_on_comma_separated_one_line(tmp_path: Path) -> None:
    body = DEPENDS_TICKET.replace("THM-17\nTHM-18", "THM-17, THM-18")
    t = parse_ticket(write(tmp_path, body))
    assert t.depends_on == ["THM-17", "THM-18"]


def test_depends_on_bullets_and_dedupe(tmp_path: Path) -> None:
    body = DEPENDS_TICKET.replace(
        "THM-17\nTHM-18",
        "- THM-17\n- THM-18\n- THM-17",  # dup
    )
    t = parse_ticket(write(tmp_path, body))
    assert t.depends_on == ["THM-17", "THM-18"]


def test_subtask_minimal_fields(tmp_path: Path) -> None:
    body = """\
---
id: THM-1
title: Minimal
target_repo: r
---

## Summary

Minimal subtask ticket.

## Acceptance Criteria

- x

## Subtasks

### A. Trivial
- Files: x.md

just edit x.md.
"""
    t = parse_ticket(write(tmp_path, body))
    assert len(t.subtasks) == 1
    s = t.subtasks[0]
    assert s.id == "A"
    assert s.title == "Trivial"
    assert s.files == ["x.md"]
    assert s.recipe is None
    assert s.tier_hint is None
    assert s.depends_on == []
    assert "edit x.md" in s.changes


def test_scope_recipe_mismatch_multiple_areas(tmp_path: Path) -> None:
    _scaffold_recipes(
        tmp_path,
        ".ai/recipes/backend/router.md",
        ".ai/recipes/backend/service.md",
        ".ai/recipes/frontend/component.md",
    )
    desc = (
        "## Acceptance Criteria\n\n- Thing.\n\n"
        "## Scope Paths\n\nbackend/routers/threads.py\nfrontend/components/Foo.tsx\n"
        ".ai/recipes/frontend/component.md\n"
    )
    warnings = find_scope_recipe_mismatches(desc, tmp_path)
    assert warnings == [
        "scope touches backend/ but .ai/recipes/backend/router.md is not in Scope Paths",
        "scope touches backend/ but .ai/recipes/backend/service.md is not in Scope Paths",
    ]
