# Factory skills

This directory contains factory-level reusable skills that Claude Code agents can invoke when working inside `ai_factory`.

## Convention

Each skill lives in its own subdirectory: `.claude/skills/<skill-name>/`. The directory must contain:

- `SKILL.md` — description, inputs, outputs, and usage notes.
- Optional helper scripts or templates referenced by `SKILL.md`.

Skills are invoked as Claude Code slash commands: `/ideate`, `/ticket`, etc.

## Available skills

| Skill | Description | Status |
|---|---|---|
| `run` | Processes all tickets in `.factory/queue/` end-to-end — natively, no subprocess. Claude makes the changes, runs tests, opens PRs, writes back to Linear. | **Phase 5** |
| `ideate` | Starts a collaborative product discussion with Claude. No code — just questions and thinking. Run `/ticket` when done. | **Phase 5** |
| `ticket` | Reads the current conversation, drafts structured Linear ticket(s) for review, then creates them in Backlog state via `factory create-issue`. | **Phase 5** |

## Planned skills

| Skill | Description | Ships in |
|---|---|---|
| `scope-check` | Verifies a diff stays within a ticket's `scope_paths`. Ships once the pattern stabilises. | Phase 4+ |
| `work` | Orchestrates a single ticket → PR run. Currently implemented inline as the `factory run` pipeline. | Promote if needed |

## Usage

```
/ideate          — start a product discussion session
/ticket          — after /ideate, propose and create Linear tickets
```

The `/ticket` skill calls `uv run factory create-issue` to create each confirmed ticket. Make sure `manifest.yaml` and `.env` (with `LINEAR_API_KEY`) are in place.
