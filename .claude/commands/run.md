# Skill: /run

## Purpose

Process Ready For AI tickets from Linear end-to-end — natively, without spawning a subprocess. Linear is the source of truth for what is runnable. Claude makes the code changes directly using its own tools, runs tests via Bash, and opens PRs. Linear write-back and memory are handled by calling `factory record-result` at the end of each ticket.

## Before starting

1. Read `manifest.yaml` to confirm which repos are registered and where they live.
2. Pull the latest ready tickets from Linear:
   ```bash
   uv run factory pull-tickets
   ```
3. Treat the freshly pulled Ready For AI tickets as the runnable work list. Do not run stale local queue files that were not returned by the current Linear pull.
4. If there are no Ready For AI tickets, say so and stop.
5. Print the list of tickets you're about to process and confirm the count.

## For each ticket, in order

Work through the queue sequentially. A failure on one ticket must not stop the rest.

### Step 1 — Parse the ticket

Read the ticket file. Extract:
- `id`, `title`, `target_repo`, `scope_paths`, `budget_minutes`, `linear_id`, `linear_url`
- The `## Acceptance Criteria` section (required)
- The `## Notes` section (optional)

If the ticket is malformed (missing required fields), skip it, print a clear error, and continue.

Resolve the repo's `local_path` from `manifest.yaml`.

### Step 2 — Sync the repo

```bash
cd <local_path>
git fetch origin
git checkout <default_branch>
git pull --ff-only
# Clean any stale untracked .claude/memory/ files left from a previous run
git clean -fd .claude/memory/ 2>/dev/null || true
git status --porcelain
```

Ensure the repo has a `.claude/settings.json` with `bypassPermissions` — Claude Code treats each git repo as its own project boundary, so the ai_factory settings don't carry over:

```bash
mkdir -p .claude
cat > .claude/settings.json <<'EOF'
{
  "permissions": {
    "defaultMode": "bypassPermissions"
  }
}
EOF
```

This file is gitignored (`.claude/settings.json` is local-only) so it won't pollute the repo.

If the working tree has any changes (tracked or untracked, including `.claude/`), stop this ticket, report the dirty state, and continue to the next.

### Step 3 — Create a branch

```bash
SHORT_UUID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:8])")
BRANCH="factory/<ticket-id-lowercase>-${SHORT_UUID}"
git checkout -b $BRANCH
```

Record the branch name — you'll need it for error reporting.

### Step 4 — Understand the codebase, then implement

Before writing any code, read the target repo's `CLAUDE.md` and explore the existing structure relevant to the ticket. Specifically:
- Identify existing models, modules, and naming conventions that the ticket touches
- Do not create a new module or model if one already exists for the same concept under a different name — extend the existing one
- If the ticket mentions a domain concept (e.g. "vendor", "service provider"), check whether it already exists under another name (e.g. "contractor") before creating anything new
- **Before writing any file whose type matches an existing skill**, check `.ai/skills/` for a `<area>/<task>.md` that describes how files of that shape are written in this repo. If one exists, read it first and conform. If the ticket adds the first file of a previously-unseen canonical type (router, model, sprite, dag, etc.) and no matching skill exists, create the skill alongside the file as part of this ticket's work — even if the ticket body didn't anticipate it. The skill should describe the actual pattern you just landed, not a TODO.

Then read the acceptance criteria carefully and make all necessary file changes using your Edit and Write tools, working inside `<local_path>`.

**Rules:**
- Do not run `git commit` or `git push` yourself during this step.
- Only modify files — no running the app, no starting servers.
- Respect `scope_paths` if set: only touch files matching those globs.
- If you realise mid-implementation that the acceptance criteria require touching files outside `scope_paths`, keep going and record the out-of-scope files as a scope advisory in the final Linear comment.

### Step 5 — Scope check

If `scope_paths` is non-empty:
```bash
git status --porcelain
```
Compare every changed/added file against the `scope_paths` globs using:
```bash
python3 -c "
import pathspec, sys
changed = sys.argv[1:]
spec = pathspec.PathSpec.from_lines('gitignore', <scope_paths>)
violations = [f for f in changed if not spec.match_file(f)]
print('\n'.join(violations))
" <changed files>
```
If there are violations, do not fail the ticket for that reason. Record the paths as a scope advisory in the final Linear comment.

### Step 6 — Install dependencies

Detect and run the install command:
- `package.json` present → `npm install`
- `pyproject.toml` present → `uv sync`
- `requirements.txt` present → `pip install -r requirements.txt`

Run inside `<local_path>`.

### Step 7 — Run tests

Detect and run the test command:
- `Makefile` with a `test` target → `make test` (requires Docker to be running)
- `package.json` with a `test` script → `npm test`
- `pyproject.toml` → `uv run pytest`

If tests fail: **preserve the branch**, record the failure with the test output, and continue to the next ticket. Do not commit or push.

### Step 8 — Write memory then commit

Before committing, write the memory file so it gets committed into the PR and lands in the repo permanently:

```bash
# Create .claude/memory/ if it doesn't exist
mkdir -p .claude/memory
```

Write `.claude/memory/<ticket-id-lower>_<YYYY-MM-DD>.md` using the standard memory format below. **Do not touch `.claude/memory/MEMORY.md`** — it is rebuilt in a separate session PR after all tickets complete.

Then commit everything including the memory file:

```bash
git add -A
git commit -m "<ticket-id>: <ticket-title>"
```

**Memory file format:**
```markdown
---
name: <short descriptive name of what was built, e.g. "AiSession data model">
description: <one sentence describing the key architectural fact — what was added/changed and the most important decision, e.g. "AiSession stored as JSONB on Job; summary union discriminated by `intent` field">
type: project
---

Factory ran ticket **<TICKET-ID>** on <YYYY-MM-DD>.

**PR:** (pending — will be updated after push)
**Files changed:**
- <file1>
- <file2>

**Key decisions:**
- <most important non-obvious architectural choice made during implementation>
- <any naming conventions, field names, or patterns future work must follow>
```

The `name` and `description` must describe **what was built**, not the fact that the factory ran. A future Claude session will use these to decide whether to load the file. "AiSession data model" is good; "THM-5 run 2026-04-28" is useless.

### Step 9 — Secret scan (optional)

If `gitleaks` is on PATH:
```bash
gitleaks detect --source . --log-opts HEAD~1..HEAD --no-banner
```
If it fires: `git reset --hard HEAD~1`, delete the branch, record secret-scan failure, continue.

### Step 10 — Push and open PR

```bash
git push -u origin $BRANCH
gh pr create \
  --title "<ticket-id>: <ticket-title>" \
  --body "## Acceptance Criteria

<acceptance criteria text>

---

_Generated by ai\_factory_" \
  --base <default_branch> \
  --head $BRANCH
```

Capture the PR URL printed by `gh pr create`.

### Step 11 — Record result

```bash
cd <ai_factory root>
uv run factory record-result \
  .factory/queue/<ticket-file> \
  --pr-url "<pr_url>" \
  --files "<comma-separated changed files>" \
  --duration <elapsed seconds> \
  --cost <cost if known, else omit>
```

This posts the PR URL as a Linear comment, transitions the issue to "In Review", writes a memory entry to the target repo, and moves the ticket to `.factory/queue/processed/`.

On failure, call instead:
```bash
uv run factory record-result \
  .factory/queue/<ticket-file> \
  --failed \
  --error "<reason>" \
  --branch "<branch name if preserved>"
```

### Step 12 — Session memory PR (once, after all tickets)

After all tickets have been processed, open one memory index PR per repo that had at least one successful ticket. This is the only place MEMORY.md is written.

For each such repo:

```bash
cd <local_path>
git checkout <default_branch>
git pull --ff-only

SHORT_UUID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:8])")
BRANCH="factory/memory-$(date +%Y-%m-%d)-${SHORT_UUID}"
git checkout -b $BRANCH
```

Rebuild MEMORY.md by reading the `name` and `description` frontmatter from every `.md` file in `.claude/memory/` (excluding MEMORY.md itself), sorted alphabetically:

```python
# Pseudocode — implement inline or via a small script
entries = []
for f in sorted(glob(".claude/memory/*.md")):
    if f == "MEMORY.md": continue
    fm = parse_frontmatter(f)
    if fm.get("name") and fm.get("description"):
        entries.append(f"- [{fm['name']}]({filename}) — {fm['description']}")
write(".claude/memory/MEMORY.md", "# Memory Index\n\n" + "\n".join(entries) + "\n")
```

If MEMORY.md has no changes, delete the branch and skip. Otherwise:

```bash
git add .claude/memory/MEMORY.md
git commit -m "chore: rebuild memory index"
git push -u origin $BRANCH
gh pr create \
  --title "chore: update memory index" \
  --body "Rebuilds \`.claude/memory/MEMORY.md\` from individual memory files added this run session.

_Generated by ai\_factory_" \
  --base <default_branch> \
  --head $BRANCH
```

## Summary

After all tickets are processed, print:

```
Run complete: N succeeded, M failed.
  ✓ THM-5  → https://github.com/.../pull/8  (45s)
  ✗ THM-14 → FAILED: tests failed — branch factory/thm-14-abc123 preserved
```

## Rules

- **Never auto-merge.** Open the PR, stop.
- **Failures are isolated** — always continue to the next ticket.
- **Respect scope_paths** — a scope violation is a hard stop for that ticket, not a warning.
- **budget_minutes is a guide** — be efficient; if a ticket is taking far longer than its budget suggests, note it in the PR body.
