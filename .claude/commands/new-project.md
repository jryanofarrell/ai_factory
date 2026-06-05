# Skill: /new-project

## Purpose

Provision a brand-new target project end to end: create its Linear team (with the
"Ready For AI" label), create its GitHub repository, clone it into `repos/<key>/`,
and register it in `manifest.yaml`. After this, the project is ready for the normal
`/ticket` → `/run` flow. This is the one-time onboarding step for a new repo. It
does **not** scaffold code — that is the project's first ticket. See ADR-020.

## Behavior when invoked

### Step 1 — Gather the inputs

You need five things. Take any the user already gave you; ask for the rest. Do not guess.

- **Team name** — the Linear team's display name (e.g. "Billy AI").
- **Team key** — its short uppercase abbreviation; issues are prefixed with it
  (e.g. `BIL` → BIL-1, BIL-2). 2–5 letters.
- **GitHub owner + repo name** — as `owner/name` (e.g. `jryanofarrell/billy-ai`).
  Confirm the owner; the default is the authenticated `gh` account
  (`gh api user --jq .login`).
- **Visibility** — `public` or `private`. Ask explicitly; default to `private` for
  anything touching credentials, customer data, or internal integrations.
- **Repo key** (optional) — the manifest key and `repos/<key>/` directory name.
  Defaults to the repo name.

### Step 2 — Confirm

Show the user exactly what will be created, then ask before doing anything:

```
Provision new project:
  Linear team:  <name>  (key <KEY>)  + "Ready For AI" label
  GitHub repo:  <owner>/<name>  (<visibility>)  — empty, seeded with a stub main
  Clone to:     repos/<key>/
  Manifest:     register '<key>' (github, default_branch=main, linear_team=<KEY>)

Proceed? [y/N]
```

If the user says anything other than `y`/`yes`, stop.

### Step 3 — Run it

Optionally dry-run first to show the plan with no side effects:

```bash
uv run factory new-project --name "<name>" --key <KEY> \
  --repo <owner>/<name> --visibility <public|private> --dry-run
```

Then for real (drop `--dry-run`):

```bash
uv run factory new-project --name "<name>" --key <KEY> \
  --repo <owner>/<name> --visibility <public|private>
```

The command is idempotent — every step checks for the existing artifact first, so it
is safe to re-run if one step fails partway through.

### Step 4 — Wrap up

Report what was created vs. what already existed (the command prints this). Then point
at the next step:

```
Project <key> is provisioned. Next: draft its first ticket(s) with /ticket — the first
ticket scaffolds the repo (conventions, .ai/ files, build/deploy skeleton). Mark them
"Ready For AI" in Linear, then run `factory run`.
```

## Rules

- **Always confirm before creating anything.** Linear teams and GitHub repos are
  outward-facing side effects.
- **Ask for visibility explicitly.** Never assume public. Default private when the
  project touches credentials, customer data, or internal integrations.
- **Idempotent by design.** If a team / repo / label / manifest entry already exists,
  the command leaves it alone — re-running is safe.
- **This command does not scaffold code.** The new repo is empty (stub `main` only).
  Its first ticket establishes conventions and the `.ai/` layout (see `/ai-files`,
  ADR-013/015). Do not put scaffolding in this step.
- **Linear plan limits.** Team creation can fail on Linear's Free tier (team cap). If
  it does, create the team in the UI and re-run — the command picks it up idempotently.
