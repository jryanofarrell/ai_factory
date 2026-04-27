# Phase 2 — Linear Read Path

## Brief for Claude Code

You are building Phase 2 of `ai_factory`. Phases 0 and 1 produced the docs, the CLI scaffold, and a working single-shot executor (`factory run-ticket`). Phase 2 adds the Linear read path: a `factory pull-tickets` command that queries Linear for issues in the "Ready for Agent" state and writes them as on-disk ticket files in the format the executor already understands.

The executor itself is not modified in this phase. Phase 2 is upstream of Phase 1: it produces the same kind of input (a ticket file) that you have already shown the executor can consume.

**Before starting, read these existing files in this order:**
- `CLAUDE.md`
- `docs/DECISIONS.md` (especially ADR-003, ADR-004, ADR-007, ADR-008)
- `docs/ARCHITECTURE.md`
- `docs/LINEAR_SCHEMA.md`
- `docs/MANIFEST.md`
- `docs/TICKET_SPEC.md`
- `phases/PHASE_1_SPEC.md` (so you understand exactly what shape the executor consumes)

If anything is ambiguous for the work below, fix the doc before writing code.

## Goal

After Phase 2, this command works:

```bash
uv run factory pull-tickets
```

It queries every Linear team named in `manifest.yaml`, finds issues in the "Ready for Agent" workflow state, and writes one ticket file per issue into the configured queue directory (default `.factory/queue/`). Tickets that fail validation are reported and not written. Re-running the command is idempotent.

Phase 2 does **not** execute any tickets. The executor is invoked manually in this phase via `factory run-ticket <queue-file> --repo <name>`. Phase 3 wires the two together.

## Spike first — verify Linear's API shape before writing code

The shape of Linear's GraphQL response for custom fields and workflow states is not always what the docs imply. Before writing the Linear client module:

1. Set `LINEAR_API_KEY` in `.env`.
2. Run a small probe (Python REPL or a one-off script) that issues a GraphQL query against `https://api.linear.app/graphql` for a single test issue, requesting: `id`, `identifier`, `title`, `description`, `state.name`, `team.key`, `labels.nodes.name`, `url`, and the custom fields described in `LINEAR_SCHEMA.md`. Capture the raw response.
3. Note where each field actually lives in the response (Linear's custom fields have moved between `customFields`, `attributes`, and other shapes over time). Document what you found in a short comment at the top of `src/factory/linear.py`.
4. Only then start writing the client.

Custom field discovery is the single most likely place to lose an hour. Spike first.

## Behavior — the pipeline, step by step

The `pull-tickets` command must do the following:

1. **Load `manifest.yaml`**. Read `queue_dir` (default `.factory/queue`) and the `repos` map. Collect the set of `linear_team` keys.
2. **Load `LINEAR_API_KEY`** from `.env` (use `python-dotenv` if it isn't already in scope; the factory already runs locally so `.env` is fine). If missing, error with a clear instruction.
3. **For each team key in the manifest**, query Linear's GraphQL API for issues:
   - In a workflow state named "Ready for Agent" (case-sensitive match; document this in `LINEAR_SCHEMA.md` if not already explicit).
   - Belonging to the team whose `key` matches.
   - Return the fields needed to populate a ticket: `identifier`, `title`, `description`, `url`, custom fields (`target_repo`, `scope_paths`, `acceptance_criteria`, `budget_tokens`, `budget_minutes`), labels.
4. **For each issue returned**, translate to a `Ticket` object:
   - `id` = `identifier` (e.g. `THMS-42`).
   - `title` = `title`.
   - `target_repo` = custom field if set; otherwise resolve from the manifest by looking up the entry whose `linear_team` matches the issue's `team.key`. Error if neither resolves.
   - `scope_paths` = parsed from the multiline custom field (one glob per line, blank lines and `#`-prefixed comments stripped).
   - `budget_tokens`, `budget_minutes` = custom fields with the documented defaults if absent.
   - `acceptance_criteria` = the `acceptance_criteria` custom field if non-empty, else parsed from the issue description's `## Acceptance Criteria` section. Error if neither is present.
   - `linear_url` = `url`.
5. **Validate** each `Ticket` against the same rules `parse_ticket()` enforces (Phase 1 already implements this — reuse it). Skip invalid tickets, log them with a clear reason, but do not abort the run.
6. **Write each valid ticket** to `<queue_dir>/<ticket-id>.md` in the format defined in `TICKET_SPEC.md` (frontmatter + acceptance criteria section). Create `queue_dir` if it doesn't exist.
7. **Idempotency**: before writing, compute a content hash of the would-be file. If a file already exists at that path with the same hash, skip the write. If the hash differs, overwrite (the Linear issue changed). Do not delete files for issues that have left the "Ready for Agent" state — that's a Phase 3 concern when the runner moves them along.
8. **Print a summary**: `Pulled N tickets across M teams. K written, J skipped (unchanged), L failed validation.` List the failures.

## Implementation notes

- New module `src/factory/linear.py`: the GraphQL client. Use `httpx` (sync) or `requests`. Don't pull in `gql`; the queries are small enough for raw GraphQL strings.
- New module `src/factory/sync.py`: the pull-tickets pipeline above.
- Extend `src/factory/cli.py` to register the new command.
- The Linear API key is read at the boundary (sync.py) and passed into the client; never fetched from inside the client itself.
- Use the existing `Ticket` dataclass from `ticket.py`. Add a `to_markdown()` method if not already present, which serializes back to the on-disk format.
- Custom field handling: pull what the spike showed actually lives in the response. If a field is missing entirely (user hasn't created it in Linear yet), treat as absent and use defaults; do not fail the whole pull.
- Tests: unit tests for the issue→ticket translation against a fixture JSON payload (capture one from your spike). Don't try to hit Linear's API in tests.
- Add a `--team <key>` flag for filtering to a single team, useful for development. Default behavior is "all teams in the manifest".
- Add a `--dry-run` flag that prints the would-be writes without touching disk.

## Auth and prerequisites

- `LINEAR_API_KEY` in `.env`. Personal API key from Linear settings → API.
- `.env` must already be in `.gitignore` (Phase 0 set this up).
- Update `README.md` to document this prerequisite.

## Error handling

- Missing `LINEAR_API_KEY` → clear error, pointer to where to get it.
- Linear API returns 401/403 → key is invalid; print Linear's error and exit.
- Linear API returns 5xx → retry with exponential backoff up to 3 times, then fail.
- A team in the manifest has no matching team in Linear → log a warning, skip, continue.
- An issue's `target_repo` resolves to a repo not in the manifest → log a warning, skip the issue.
- Write failure → log, continue with the next ticket.

## Acceptance criteria

- [ ] `factory pull-tickets` writes one valid ticket file per ready Linear issue, conforming to `TICKET_SPEC.md`.
- [ ] Ticket frontmatter is populated from Linear custom fields and the issue description.
- [ ] Tickets that fail validation are reported with a clear, actionable message and are not written to disk.
- [ ] Issues with `target_repo` set use it; issues without inherit from the team→repo mapping in the manifest.
- [ ] Re-running `pull-tickets` is idempotent: same Linear state produces the same files; unchanged files are not rewritten.
- [ ] `LINEAR_API_KEY` is read from `.env`; missing key produces a clear error.
- [ ] `--dry-run` prints the would-be writes without touching disk.
- [ ] `--team <key>` restricts the pull to a single team.
- [ ] Unit tests cover the issue → ticket translation against a fixture payload from the spike.
- [ ] After running `pull-tickets`, the resulting files in `.factory/queue/` can be passed to `factory run-ticket` and produce PRs.
- [ ] The Linear API spike's findings are captured in a comment at the top of `src/factory/linear.py`.

## Out of scope — do not build

- Executing the tickets (Phase 3 wires `run-ticket` and `pull-tickets` together as `factory run`).
- Updating Linear (writing comments, transitioning states) — that's Phase 3.
- Budget enforcement, scope enforcement, secret scanning (Phase 4).
- Webhook ingestion or push-style updates from Linear; pull-only in v1.
- Any caching layer beyond the simple file-hash idempotency above.
- Cleaning up stale ticket files for issues that have left the ready state (Phase 3).

## Verification (manual, after Claude Code finishes)

1. Confirm `LINEAR_API_KEY` is set in `.env`.
2. In Linear, create the "Ready for Agent" workflow state in the THMS team (per `LINEAR_SCHEMA.md`).
3. Create a test issue in the THMS team. Fill in the custom fields (`scope_paths`, `acceptance_criteria`, `budget_tokens`, `budget_minutes`). Move it to "Ready for Agent".
4. Run `uv run factory pull-tickets`. Confirm a file appears at `.factory/queue/THMS-XX.md` with valid frontmatter and acceptance criteria.
5. Run `uv run factory pull-tickets` again. Confirm the file is reported as "skipped (unchanged)".
6. Edit the Linear issue's title. Re-run `pull-tickets`. Confirm the file is rewritten.
7. Run `uv run factory run-ticket .factory/queue/THMS-XX.md --repo thms-platform`. Confirm the executor produces a PR end-to-end (Phase 1 still works against the new ticket source).
8. Create an issue with no `acceptance_criteria` field and an empty description. Re-run `pull-tickets`. Confirm it fails validation and is reported, but the rest of the pull succeeds.
