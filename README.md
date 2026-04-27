# ai_factory

Personal AI factory that turns Linear tickets into GitHub PRs, running locally on your machine.

## What it does

`ai_factory` reads a ticket file, invokes a Claude Code executor inside the target repository, and opens a GitHub pull request. A human reviews and merges the PR; the factory never merges automatically.

See `docs/ARCHITECTURE.md` for the system design and `docs/PHASES.md` for build status.

## Setup

**Prerequisites:** Python 3.12+, [`uv`](https://github.com/astral-sh/uv).

```sh
# Install dependencies
uv sync

# Copy the manifest template and edit local_path for each registered repo
cp manifest.example.yaml manifest.yaml
$EDITOR manifest.yaml
```

### Auth requirements

The factory does not manage credentials. Before running, ensure:

- **`git`** installed and configured (`git config user.email` etc.)
- **`gh`** CLI installed and authenticated: `gh auth status` must pass with push access to the target repo
- **`claude`** CLI installed and authenticated. Set the model to Sonnet: `claude config set model claude-sonnet-4-5` (or similar)

If any of these are missing or not on PATH, `factory run-ticket` will exit early with a clear error.

## Usage

```sh
uv run factory --help
uv run factory run-ticket examples/tickets/hello-world.md --repo thms-platform
```

## Docs

- `docs/ARCHITECTURE.md` — system overview and component boundaries
- `docs/PHASES.md` — build phases and current status
- `docs/DECISIONS.md` — architectural decisions and rationale
- `docs/LINEAR_SCHEMA.md` — Linear workspace configuration
- `docs/MANIFEST.md` — manifest.yaml field reference
- `docs/TICKET_SPEC.md` — on-disk ticket format

## Current status

Phase 1 — single-shot executor. Runs a ticket file end-to-end and opens a PR. No Linear integration yet. See `docs/PHASES.md` for the full plan.
