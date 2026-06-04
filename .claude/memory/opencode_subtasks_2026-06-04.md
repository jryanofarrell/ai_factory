---
name: opencode tier + per-file subtask decomposition
description: Third executor tier (opencode + ollama + qwen3:8b-16k) plus per-file subtask architecture in tickets; runner executes subtasks one-at-a-time
type: project
---

Landed on `feat/opencode-provider` (PR #20).

## What was added

- **Third executor provider** `src/factory/providers/opencode.py`. Wraps `opencode run --format json -m ollama/<model>` with the same streaming-JSON parser shape as codex. Default model `ollama/qwen3:8b-16k`. Slotted into `_run_with_fallback` after claude and codex.
- **Per-file subtask decomposition.** `Subtask` dataclass in `src/factory/ticket.py`, parser for `## Subtasks` markdown section. `_run_subtask_loop` in `src/factory/runner.py` executes each subtask as its own agent invocation, sees prior subtasks' diff in its prompt, no tests between. Backward compat: tickets without subtasks use the legacy single-shot path.
- **`scripts/bootstrap.sh`** — idempotent setup for macOS (brew) and Debian/Ubuntu (apt). Installs ollama, opencode, the qwen3:8b model, the 16k Modelfile variant, opencode.json config, plus claude/codex/gh/gitleaks/uv/node.
- **`/ticket` and `/ideate` skill updates** — decomposition discipline: every subtask must be executable by a small local model; subtasks follow their skill rather than redefining patterns; read skill files during ideation, don't pattern-match from training data; question every AC's "why".

## Key decisions

- **Three silent-failure cliffs for opencode + ollama tool calling.** All three must hold or the model emits text describing tool calls instead of invoking them: (1) model must advertise `tools` capability via `ollama show`, (2) model must be registered with `"tools": true` in `~/.config/opencode/opencode.json`, (3) `num_ctx` must be ≥16384 — the ollama 4096 default truncates the tool schema. Encoded in the provider docstring.
- **Subtasks reference skills, not files.** No exemplar field. If the skill doesn't have enough detail for an agent to follow, fix the skill instead of pointing at an example file (which will drift).
- **Missing skill → write the skill first.** Subtask N+1 can be a skill-write step before subtask N that needs it. Skill catalog grows organically as features are decomposed.
- **Tier hints exist in the `Subtask` dataclass** but the `/ticket` skill prompt no longer mentions them. All subtasks default to local-first. Reasoning happens at ideation, not at execution.
- **Tests run once at end of ticket, not between subtasks.** Subtasks intentionally leave the codebase in a partially-broken state — mirrors how a human engineer works.

## Hardware ceiling

- **16 GB Mac (M2):** `qwen3:8b-16k` (~5 GB resident) fits comfortably. `gpt-oss:20b` thrashes swap.
- **24 GB+ host (gaming PC):** Can run `gpt-oss:20b` or `qwen3-coder:30b`. Override with `OPENCODE_MODEL=ollama/gpt-oss:20b` env var.

## Validation

- 84 tests pass (74 prior + 10 new for subtask parser and runner sequencing).
- ruff clean.
- Opencode provider end-to-end tested against a throwaway repo on macOS — `qwen3:8b-16k` invoked the edit tool, README.md actually modified, real `tool_use` event observed.
- Bootstrap not yet tested on a real Linux host.
- THM-12 in Linear updated to new subtask shape (separate from this PR but part of the same effort).
- No real ticket run yet — first end-to-end validation will be running the updated THM-12.

## Open / deferred

- **No ADR.** Worth one — this is a non-trivial architecture shift. Append to `docs/DECISIONS.md` per CLAUDE.md rule 2.
- **No skill-creation gating in the runner.** If a subtask references a non-existent skill, the runner just loads an empty string. `/ticket` skill prompt asks for skill-write subtasks upfront, but there's no enforcement.
- **No cross-subtask revision.** If subtask 5 needs schema changes from subtask 1, the design has to be right at decomposition time.
- **`tier_hint` field still in the dataclass** but unused by `/ticket` instructions. Dead code candidate for cleanup if it stays unused.
