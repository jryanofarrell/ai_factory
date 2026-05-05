# ai_factory — project memory

## What this is

`ai_factory` is a personal AI factory that turns Linear tickets into GitHub PRs, running entirely on the user's local machine. It is a control plane: it reads a queue of ready tickets from Linear, spins up a Claude Code executor against the appropriate target repository, opens a PR, and writes back to Linear. See `docs/ARCHITECTURE.md` for the system design and `docs/DECISIONS.md` for why it is shaped the way it is.

## Read these first

- `docs/ARCHITECTURE.md` — system overview and component boundaries
- `docs/DECISIONS.md` — architectural decisions and their rationale (append-only; read before introducing any new pattern)
- `docs/PHASES.md` — phase summary and current status
- `phases/PHASE_0_SPEC.md` — current phase spec (update this pointer when advancing phases)

## Rules

Read `.ai/rules/factory.md` before writing any code. These always apply.

## Stack

- Python 3.12+
- `uv` for environment and dependency management (`uv sync` to install)
- Typer for the CLI (`src/factory/cli.py`)
- `ruff` for lint and format (`uv run ruff check .` / `uv run ruff format .`)
- `pytest` for tests (`uv run pytest`)
- `pyyaml` for reading `manifest.yaml`

## Conventions

- Small, focused modules — one concern per file.
- Type hints on all public functions.
- No dependencies beyond what is listed in `pyproject.toml`; add to `[project.dependencies]` (runtime) or `[dependency-groups].dev` (dev-only).
- No secrets in code. API keys, tokens, and local paths live in `manifest.yaml` (gitignored) or environment variables.
- `manifest.yaml` is gitignored. `manifest.example.yaml` is the checked-in template.
- The executor reads tickets from disk (`examples/tickets/` for local tests; a future Linear-pull step writes them there). It does not call Linear directly.

## Target repositories

Projects that the factory manages live under `repos/`. Each project has its own `CLAUDE.md`, `AGENTS.md`, and `.ai/` directory. **Before doing any work related to a target project — including ideation, ticketing, or code changes — read that project's `CLAUDE.md` and any relevant memory files first.** Do not assume conventions from `ai_factory` apply to target projects.

## Skills

Factory-level reusable skills live under `.claude/skills/`. See `.claude/skills/README.md` for the index and conventions.
