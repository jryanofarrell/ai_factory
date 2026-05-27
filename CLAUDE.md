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

## Rules
1. **Work on one phase at a time.** The current phase spec's "Out of scope" list is a hard boundary. Do not implement Phase 1+ behavior while working on Phase 0, and so on.
2. **Do not edit `docs/DECISIONS.md` in place.** If a decision needs to change, append a new ADR that supersedes the old one, or pause and ask the user before modifying anything.
3. **Do not run `git push --force` on shared branches.**
4. **Do not commit `manifest.yaml`, `.env`, anything inside a target repo's `local_path`, or any credentials or tokens.**
5. **When the right shape of a thing is unclear, read `docs/DECISIONS.md` first.** Many patterns that look like open questions have already been settled there.

## Target repositories
Projects that the factory manages live under `repos/` (e.g. `repos/thms-platform/`). **This is the canonical working copy** — the executor checks out the active ticket's branch here, and any code changes for a target project must happen in this path. The user may have other clones of the same repo elsewhere on disk (e.g. `~/thms-platform/`); ignore those — they will be on unrelated branches and edits there won't reach the PR.

Each project has its own `CLAUDE.md` and may have its own memory files. **Before doing any work related to a target project — including ideation, ticketing, or code changes — read that project's `CLAUDE.md` (at `repos/<project>/CLAUDE.md`) and any relevant memory files first.** Do not assume conventions from `ai_factory` apply to target projects.

Before editing inside a target repo, run `git -C repos/<project> branch --show-current` and `git -C repos/<project> status -s` to confirm you're on the expected ticket branch with a clean state.

**Canonical AI file layout in target repos.** Every target repo should have `CLAUDE.md`, `AGENTS.md`, and a `.ai/` tree (`rules/core.md`, `context/<area>.md`, `skills/ai-structure.md`). Both `CLAUDE.md` and `AGENTS.md` point at the same `.ai/` content. If a target repo is missing these files, the first action is to invoke `/ai-files` to bootstrap them with content drawn from the actual code. Per-ticket work keeps `.ai/context/<area>.md` fresh by updating it alongside the code changes. See ADR-013.

## Slash commands

Factory-level workflows live in `.claude/commands/` as slash commands (`/ideate`, `/ticket`, `/run`, `/ai-files`). They are user-triggered — Claude does not auto-invoke them. If a genuinely auto-invokable capability is ever needed, add it under `.claude/skills/<name>/SKILL.md` (separate from commands).
