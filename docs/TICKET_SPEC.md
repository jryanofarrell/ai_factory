# Ticket spec

This document defines the on-disk ticket format consumed by the executor. Tickets are Markdown files with YAML frontmatter. This format is the contract between the Linear pull step (Phase 2) and the executor (Phase 1).

Defining the format in Phase 0 anchors both ends: Phase 1 builds an executor that reads this format, and Phase 2 builds a pull step that writes it. Neither phase needs to coordinate on format details.

## File location

Tickets live in a queue directory inside the `ai_factory` repo:
- `examples/tickets/` — hand-written tickets for local testing (committed)
- `.factory/queue/` — tickets written by the Linear pull step at run time (gitignored, created by Phase 2; path configurable via `queue_dir` in `manifest.yaml`)

## Format

```markdown
---
id: THMS-42
title: Add CHANGELOG entry for AI factory test
target_repo: thms-platform
scope_paths:
  - CHANGELOG.md
budget_tokens: 10000
budget_minutes: 5
linear_url: https://linear.app/<workspace>/issue/THMS-42  # populated by Phase 2; absent for hand-written tickets
---

## Acceptance Criteria

- A new line is added to `CHANGELOG.md` under an "Unreleased" section.
- The line reads "Add AI factory test entry".
- All existing tests still pass.
- No other files are modified.

## Notes

This is a hello-world ticket to verify the executor pipeline works.
```

## Frontmatter fields

| Field | Type | Required | Source in Linear | Purpose |
|---|---|---|---|---|
| `id` | string | Yes | `issue.identifier` | Linear issue identifier (e.g., `THMS-42`). Used as the prefix for the branch name and in write-back calls to Linear. |
| `title` | string | Yes | `issue.title` | Human-readable title. Used in the branch name and PR title. |
| `target_repo` | string | Yes | `issue.target_repo` custom field, or derived from `issue.team.key` → manifest lookup | Key into `manifest.yaml`'s `repos` map. Tells the executor which local repo to operate in. |
| `scope_paths` | list of strings | No | `issue.scope_paths` custom field (one glob per line → parsed into list) | Glob patterns restricting which files the executor may modify. If absent, no scope restriction applies. Enforced in Phase 4. |
| `budget_tokens` | integer | No | `issue.budget_tokens` custom field | Maximum Claude API tokens for this run. If absent, the executor uses the manifest default (50 000). Enforced in Phase 4. |
| `budget_minutes` | integer | No | `issue.budget_minutes` custom field | Maximum wall-clock minutes for this run. If absent, the executor uses the manifest default (30). Enforced in Phase 4. |
| `linear_url` | string | No | `issue.url` | Full URL to the Linear issue. Populated automatically by the Phase 2 pull step. Absent on hand-written tickets. Used by the write-back step (Phase 3) to post the PR link as a comment. |

## Body sections

The ticket body follows the frontmatter and contains Markdown sections.

### `## Acceptance Criteria` (required)

A bulleted list of conditions the PR must satisfy. The executor treats this as its primary success definition: it does not open a PR until it believes all criteria are met. Each criterion should be concrete and verifiable (a file exists, a test passes, a specific string appears, no other files are modified).

### `## Notes` (optional)

Free-form context for the executor. Background information, constraints, hints, or links to relevant code. The executor reads this but it does not affect the success definition. Use it to provide context that would not fit naturally in the acceptance criteria.

## Linear field mapping

| Ticket frontmatter | Linear custom field | Notes |
|---|---|---|
| `id` | `issue.identifier` | Auto-assigned by Linear |
| `title` | `issue.title` | Standard Linear field |
| `target_repo` | `target_repo` custom property | Derived from team if absent |
| `scope_paths` | `scope_paths` custom property | Multiline text → YAML list |
| `budget_tokens` | `budget_tokens` custom property | Integer |
| `budget_minutes` | `budget_minutes` custom property | Integer |
| `linear_url` | `issue.url` | Populated by pull step; absent on hand-written tickets |
| Acceptance criteria body | `acceptance_criteria` custom property | Multiline text → Markdown section |
| Notes body | `issue.description` | Remaining description content |

## Naming convention

Ticket files are named `<id-lowercase>.md`, e.g., `thms-42.md`. The hello-world example is named `hello-world.md` for readability, but production tickets from the pull step will follow the identifier convention.
