# Ticket spec

This document defines the Markdown ticket format used for local testing, snapshots, and explicit `--no-pull` runs. Normal `factory run` execution pulls Ready For AI issues from Linear and executes those parsed tickets directly; stale local files are not authoritative.

Defining the format in Phase 0 anchored both ends: Phase 1 built an executor that reads this format, and Phase 2 built a pull step that writes it. Current normal runs use Linear as the source of truth and keep this format as a debug/offline contract.

## File location

Tickets live in a queue directory inside the `ai_factory` repo:
- `examples/tickets/` — hand-written tickets for local testing (committed)
- `.factory/queue/` — optional ticket snapshots written by `factory pull-tickets` and explicit `factory run --no-pull` inputs (gitignored; path configurable via `queue_dir` in `manifest.yaml`)

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
| `budget_tokens` | integer | No | `## Budget` section, if present | Soft token estimate for planning. The executor does not abort solely because this value is exceeded. |
| `budget_minutes` | integer | No | `## Budget` section, if present | Soft time estimate for planning. The executor does not abort solely because this value is exceeded. |
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
| `budget_tokens` | `## Budget` body section | Integer, soft estimate |
| `budget_minutes` | `## Budget` body section | Integer, soft estimate |
| `linear_url` | `issue.url` | Populated by pull step; absent on hand-written tickets |
| Acceptance criteria body | `acceptance_criteria` custom property | Multiline text → Markdown section |
| Notes body | `issue.description` | Remaining description content |

## Naming convention

Ticket files are named `<id-lowercase>.md`, e.g., `thms-42.md`. The hello-world example is named `hello-world.md` for readability, but production tickets from the pull step will follow the identifier convention.
