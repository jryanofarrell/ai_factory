# Linear schema

This document specifies the Linear workspace configuration that `ai_factory` expects. Phase 0 documents this; nothing reads it until Phase 2.

## Teams

One Linear team per registered target repository. By convention the team **key** matches the repository name in upper snake case:

| Repository | Team key |
|---|---|
| `toms-hms/thms-platform` | `THMS` |

The factory reads the team↔repo mapping from Linear's native GitHub integration (see ADR-003), not from the manifest. The `linear_team` field in `manifest.yaml` is a local override for cases where the key does not follow the convention.

## Workflow states

Standard Linear workflow states plus one addition:

| State | Category | Purpose |
|---|---|---|
| Backlog | Unstarted | Default for new issues |
| Todo | Unstarted | Triaged but not started |
| In Progress | Started | Human is actively working on it |
| **Ready for Agent** | **Started** | Human has finished specifying the ticket; factory will pick it up on the next poll |
| In Review | Started | Factory has opened a PR; awaiting human review |
| Done | Completed | PR merged |
| Cancelled | Cancelled | Abandoned |

The factory polls for issues in the **Ready for Agent** state. When a run completes successfully, the factory transitions the issue to **In Review**. When a run fails, the factory transitions the issue back to **In Progress** (or a custom **Agent Failed** state if added) and adds a comment with the failure reason.

**Setup:** Add "Ready for Agent" as a custom workflow state in the "Started" category in your Linear workspace settings. This state applies to all teams that use the factory.

## Custom fields on issues

These custom properties must be created at the workspace level in Linear and will be available on all issues across all teams.

| Field | Linear type | Required | Default | Purpose |
|---|---|---|---|---|
| `acceptance_criteria` | Text (multiline) | Yes | — | Criteria the PR must satisfy. The executor reads this as its primary success definition. Required — do not mark a ticket ready without it. |
| `scope_paths` | Text (multiline) | No | (no restriction) | Glob patterns (one per line) restricting which files the executor may modify. If absent, no scope restriction is enforced. Enforced in Phase 4. |
| `budget_tokens` | Number | No | 50 000 | Maximum Claude API tokens for this run. The executor is aborted if it exceeds this. Enforced in Phase 4. |
| `budget_minutes` | Number | No | 30 | Maximum wall-clock minutes for this run. The executor is aborted if it exceeds this. Enforced in Phase 4. |
| `target_repo` | Text | No | (derived from team) | Explicit repository override. Use when a ticket in team `THMS` should target a repo other than `thms-platform`. Format: `owner/repo`. |

**Setup:** Create these as custom properties in your Linear workspace settings (Settings → Properties). Use the field names exactly as listed above — the factory reads them by name.

## Labels

| Label | Purpose |
|---|---|
| `factory:ready` | Alternative trigger to the "Ready for Agent" state. The factory can be configured to poll by label instead of (or in addition to) workflow state. Useful for workflows where the state machine is managed separately. |
| `factory:in-progress` | Applied by the factory when it starts a run. Cleared on completion or failure. |
| `factory:failed` | Applied by the factory when a run fails after exhausting retries. Cleared when the issue is manually reset to "Ready for Agent". |

Labels are additive — they do not replace the workflow state mechanism. The recommended approach is to use workflow states as the primary trigger and labels for observability.

## Branch name template

Configure this at the workspace level in Linear (Settings → Integrations → GitHub → Branch format):

```
{username}/{issue.identifier}-{issue.title-kebab}
```

Example: `jryanofarrell/thms-42-add-changelog-entry-for-ai-factory-test`

The factory appends a short UUID suffix to the branch name at run time to ensure idempotency (see ADR-005, Phase 1 notes). The Linear template is used as the prefix; the factory never relies on the full branch name being unique across runs.
