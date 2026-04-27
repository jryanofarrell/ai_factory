# Factory skills

This directory contains factory-level reusable skills that Claude Code agents can invoke when working inside `ai_factory`.

## Convention

Each skill lives in its own subdirectory: `.claude/skills/<skill-name>/`. The directory must contain:

- `SKILL.md` — description, inputs, outputs, and usage notes.
- Optional helper scripts or templates referenced by `SKILL.md`.

Skills are invoked by referencing their `SKILL.md` in a prompt. They are not CLI commands; they are context documents that tell the agent what to do.

## Status

**No skills are implemented in Phase 0.** The directory and this index are created now so that the convention is established before any skills are added.

## Planned skills

| Skill | Description | Ships in |
|---|---|---|
| `work` | Orchestrates a single ticket → PR run: clone/update target repo, load ticket from disk, run Claude Code executor, open PR, write back to Linear. Implemented inline as part of Phase 1's executor; may be promoted to a formal skill once the shape stabilizes. | Phase 1 |
| `scope-check` | Verifies that a completed diff touches only files matching the ticket's `scope_paths` globs. Rejects the run if out-of-scope files were modified. | Phase 4 |
| `ideation` | Converts a free-form brain dump into a well-structured Linear ticket with all required custom fields populated. | Phase 5 |
