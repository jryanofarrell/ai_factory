# ai_factory

Personal AI factory that turns Linear tickets into GitHub PRs.

## What it does

`ai_factory` reads ready tickets from a Linear queue, runs a Claude Code executor inside the appropriate target repository, opens a pull request, and writes the result back to Linear. A human reviews and merges the PR; the factory never merges automatically.

See `docs/ARCHITECTURE.md` for the system design and `docs/PHASES.md` for build status.

## Setup

**Prerequisites:** Python 3.12+, [`uv`](https://github.com/astral-sh/uv).

```sh
# Install dependencies
uv sync

# Copy the manifest template and edit local_path for each registered repo
cp manifest.example.yaml manifest.yaml
$EDITOR manifest.yaml

# Verify the CLI works
uv run factory --help
uv run factory version
```

## Docs

- `docs/ARCHITECTURE.md` — system overview and component boundaries
- `docs/PHASES.md` — build phases and current status
- `docs/DECISIONS.md` — architectural decisions and rationale
- `docs/LINEAR_SCHEMA.md` — Linear workspace configuration
- `docs/MANIFEST.md` — manifest.yaml field reference
- `docs/TICKET_SPEC.md` — on-disk ticket format

## Current status

Phase 0 — scaffolding only. The only working command is `factory version`. See `docs/PHASES.md` for the build plan.
