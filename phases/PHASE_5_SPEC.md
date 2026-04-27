# Phase 5 — Ideation

## Brief for Claude Code

You are building Phase 5 of `ai_factory`, the final phase of v1. Phases 0–4 produced a hardened closed loop: ready Linear tickets are pulled, executed, and PRs come back. Phase 5 adds the front end: a `factory ideate` command that takes a brain dump from the user and turns it into a structured Linear issue (in a Draft/Backlog state, never directly Ready) that the user reviews and promotes.

After Phase 5, the user's full daily flow is:

1. `factory ideate notes.md` (or pipe text via stdin) → drafts a Linear issue.
2. User opens the issue in Linear, reviews, edits as needed, marks "Ready for Agent".
3. `factory run` → executes ready tickets, PRs appear.
4. User reviews PRs.

**Before starting, read these existing files in this order:**
- `CLAUDE.md`
- `docs/DECISIONS.md` (especially ADR-002 — single model)
- `docs/ARCHITECTURE.md`
- `docs/LINEAR_SCHEMA.md`
- `docs/TICKET_SPEC.md`
- `phases/PHASE_2_SPEC.md` (you'll reuse the Linear client)
- `src/factory/linear.py`

## Goal

After Phase 5, this command works:

```bash
uv run factory ideate path/to/brain-dump.md --repo thms-platform
# or
cat brain-dump.md | uv run factory ideate --repo thms-platform
```

It calls Sonnet to draft a structured Linear issue, creates the issue via the Linear API in the target team's Backlog/Draft state, and prints the issue URL. The issue is **never** created in the "Ready for Agent" state — promotion to ready is always a manual human action.

## Spike first — pick the model invocation strategy

There are two reasonable ways to call Sonnet for ideation:

1. **Anthropic SDK** (`anthropic` Python package) calling the Messages API directly. Requires `ANTHROPIC_API_KEY` in `.env`. Returns structured JSON cleanly via the API.
2. **`claude -p`** (same headless CLI used for execution). Already authenticated; no new credentials. Output is text, so structured extraction depends on prompt discipline (or `--output-format json` if available).

Spike both for ten minutes each on the same brain-dump → ticket task. Pick the one that gives you more reliable structured output. Document your choice and reasoning at the top of `src/factory/ideate.py`. The Anthropic SDK is the default expectation; only fall back to `claude -p` if the SDK adds meaningful friction (extra auth setup, etc.).

## Behavior — the pipeline, step by step

The `ideate` command must:

1. **Read the brain dump** — from the file path argument or, if absent, from stdin. Strip leading/trailing whitespace. Refuse to run on empty input.
2. **Resolve the target repo** — from `--repo`. If absent, attempt to infer from the brain dump (look for explicit mentions of repo names that exist in `manifest.yaml`). If still ambiguous, error and ask the user to pass `--repo` explicitly. Never guess silently.
3. **Load context for the model**:
   - The manifest entry for the target repo (so the model knows the repo's purpose, build/test commands).
   - The target repo's `CLAUDE.md` if it exists at `<local_path>/CLAUDE.md` (read once, include in the prompt context — this is *information* about the repo, not auto-loaded the way Phase 1's executor uses it).
   - The contents of `docs/LINEAR_SCHEMA.md` (so the model knows what fields it's filling and what formats are valid).
4. **Call Sonnet** with a prompt that asks for a structured ticket response. Output must be JSON conforming to:

   ```json
   {
     "title": "Add CHANGELOG entry for AI factory test",
     "description_markdown": "## Context\n\n...\n\n## Acceptance Criteria\n\n- ...\n- ...\n\n## Notes\n\n...",
     "scope_paths": ["CHANGELOG.md"],
     "budget_tokens": 10000,
     "budget_minutes": 5,
     "rationale": "One-paragraph reasoning the model wrote about why these scope/budget values."
   }
   ```

   Validate the JSON schema. If parsing fails, retry once with a re-prompt asking for valid JSON. If it still fails, error out — do not create a malformed ticket.
5. **Confirm with the user** (interactive by default; suppressible with `--yes`):
   - Print the proposed title, scope, budgets, and acceptance criteria.
   - Ask: `Create this issue in Linear? [y/N/edit]`.
   - On `edit`, drop into `$EDITOR` with the full proposed JSON for tweaking, then re-validate before proceeding.
   - On `y`, proceed. On anything else, abort cleanly.
6. **Create the Linear issue** via the API:
   - Team: the manifest's `linear_team` for the target repo.
   - Title: from the model output.
   - Description: from the model output (markdown).
   - Custom fields: `target_repo`, `scope_paths` (joined with newlines), `budget_tokens`, `budget_minutes`, `acceptance_criteria` (extracted from the description's `## Acceptance Criteria` section).
   - **Workflow state**: the team's Backlog or Draft state (whichever exists). Explicitly **not** "Ready for Agent". Document this rule in `LINEAR_SCHEMA.md` as part of this phase: ideation only writes to Backlog/Draft.
7. **Print the result** — the new issue's identifier (e.g. `THMS-47`) and full Linear URL.

## Implementation notes

- New module `src/factory/ideate.py` for the pipeline.
- Extend `src/factory/linear.py` with `create_issue()` if not already present.
- Use Pydantic (or a small dataclass + manual validation) for the model-output schema. The retry-on-malformed-JSON loop should live in `ideate.py`.
- The model prompt should be a single template with placeholders for `{brain_dump}`, `{repo_metadata}`, `{repo_claude_md}`, `{linear_schema}`. Keep the prompt versioned in code, not in a config file.
- Default to `claude-sonnet-4-6` (or whatever Sonnet model string is current). Make the model name a constant in one place so it's swappable later.
- Keep the prompt short. Tell the model: it is filling out a structured ticket; acceptance criteria should be testable; scope should be the smallest plausible set of files; budgets default to 10000 tokens / 5 minutes for trivial work, scaling up for larger asks. Provide one one-shot example.
- Tests: a unit test that mocks the model call with a fixed JSON response and verifies the Linear `create_issue` call payload is correct.
- The `--yes` flag bypasses confirmation. Useful for scripting; document that interactive review is the default.

## Auth and prerequisites

- `ANTHROPIC_API_KEY` in `.env` (if Anthropic SDK is used).
- `LINEAR_API_KEY` in `.env` (already required by Phase 2).
- `EDITOR` env var (used for the `edit` confirmation option; falls back to `$VISUAL` then `vi`).

## Acceptance criteria

- [ ] `factory ideate <brain-dump.md> --repo thms-platform` creates a Linear issue in the THMS team's Backlog/Draft state.
- [ ] The issue is **never** created in "Ready for Agent" — verified with a unit test that fails the build if the workflow-state argument is set to that value.
- [ ] The created issue has `acceptance_criteria`, `scope_paths`, `budget_tokens`, `budget_minutes`, and `target_repo` populated.
- [ ] Issue title and description are derived from the brain dump and reference relevant repo context.
- [ ] Stdin input works (`cat notes.md | factory ideate --repo thms-platform`).
- [ ] If `--repo` is omitted and not unambiguous from the brain dump, the command errors with an actionable message instead of guessing.
- [ ] Interactive confirmation defaults on; `--yes` bypasses it; `edit` opens `$EDITOR`.
- [ ] If the model emits malformed JSON, the command retries once with a re-prompt and errors out cleanly if still malformed.
- [ ] The model invocation strategy chosen during the spike is documented at the top of `src/factory/ideate.py`.
- [ ] `LINEAR_SCHEMA.md` is updated to document that ideation writes only to Backlog/Draft states.
- [ ] On completion, the new issue's identifier and full Linear URL are printed to stdout.

## Out of scope — do not build

- Auto-promoting issues to "Ready for Agent" — never. ADR-006 is a hard rule.
- Bulk ideation (one brain dump → multiple tickets). v1 is one dump → one ticket; if a dump should be multiple tickets, the model should say so in the rationale and the user runs `ideate` again.
- A web UI or chat interface; `ideate` is CLI-only in v1.
- Cross-repo ticket creation (a single brain dump producing tickets in multiple repos). Forbidden by current architecture.
- Ideation that reads existing Linear issues for context. Useful, but defer.
- Cost tracking for the ideation call itself. Phase 4's cost capture is for execution; ideation cost is small enough to ignore for v1.
- Anything in Phase 6 (scheduling) or Phase 7 (multi-repo).

## Verification (manual, after Claude Code finishes)

1. Write a short brain dump in `notes.md`: e.g. "We should add a CHANGELOG entry under Unreleased noting the AI factory integration is live. Should be a tiny change, just one file."
2. Run `uv run factory ideate notes.md --repo thms-platform`. Confirm it prints a proposed ticket and asks for confirmation.
3. Choose `edit`. Confirm `$EDITOR` opens with the JSON; modify the title slightly; save and exit. Confirm the modified title is reflected in the next confirmation prompt.
4. Choose `y`. Confirm a new Linear issue is created. Open it in Linear and verify: state is Backlog/Draft (not Ready), acceptance criteria are sensible, custom fields are populated, target_repo is `thms-platform`.
5. Manually promote the issue to "Ready for Agent" in Linear.
6. Run `uv run factory run`. Confirm the ideated ticket flows through Phase 3, executes, and produces a PR.
7. Confirm the full daily flow works end-to-end: ideate → review/promote → run → PR.
8. Run `factory ideate < notes.md --repo thms-platform` (stdin input). Confirm the same behavior.
9. Run `factory ideate notes.md` (no `--repo`). Confirm a clear error is printed asking for `--repo`.

If steps 1–7 all pass, you have completed v1 of `ai_factory`. Phases 6 (local scheduling) and 7 (multi-repo) are deferred until a concrete need arises.
