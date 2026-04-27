# `phases/`

This directory holds the detailed specifications for each phase of `ai_factory`'s build. Each spec is the contract handed to a Claude Code session: it states the goal, the work, the acceptance criteria, and what is explicitly out of scope.

## How this differs from `docs/PHASES.md`

`docs/PHASES.md` is the **map** — a one-paragraph summary of every phase, useful for a human or agent to orient themselves to where the project is and where it's going.

`phases/PHASE_<N>_SPEC.md` is the **work order** — the detailed brief that produces a phase's deliverables. Specs live here, summaries live in `docs/`.

## Convention

Each phase spec is a self-contained markdown file with these sections in this order:

- **Brief for Claude Code** — what to read first, what's already done, what this phase produces.
- **Goal** — one-sentence outcome.
- **Behavior / Required content** — the actual work, in enough detail that a fresh agent session can execute without follow-up questions.
- **Implementation notes** — stack, module layout, conventions, anything pinned.
- **Acceptance criteria** — checklist; the phase is done when every box can be ticked.
- **Out of scope** — explicit list of things the agent must not build, even if tempted.
- **Verification** (where applicable) — manual steps a human runs after the session to confirm the work.

Specs are **append-only in spirit**. Once a phase has been executed, its spec is preserved as the historical record. If the phase needs to be redone differently, write a new spec (`PHASE_4_REVISED_SPEC.md` or similar) rather than editing the original.

## How to use a spec

1. Save the spec into this directory and commit it before starting the session, so the work is traceable to a specific spec version.
2. From inside the `ai_factory` checkout, start a fresh Claude Code session.
3. Prompt with something like: *"Read `phases/PHASE_<N>_SPEC.md` and execute it. When the acceptance criteria are met, stop and summarize what you built — do not start the next phase."*
4. Watch the session. If the agent strays into the "Out of scope" list, stop and re-prompt; do not let drift accumulate.
5. After the session, run the acceptance-criteria checklist yourself before committing.

Each phase is meant to fit in roughly one focused session. If a phase is sprawling enough to need multiple sessions, that is a signal the spec should be split.

## Current specs

| Phase | File | Status |
|------:|------|--------|
| 0 | `PHASE_0_SPEC.md` | Ready to execute |
| 1 | `PHASE_1_SPEC.md` | Ready to execute (after Phase 0) |
| 2 | — | Not yet written |
| 3 | — | Not yet written |
| 4 | — | Not yet written |
| 5 | — | Not yet written |
| 6 | — | Not yet written |
| 7 | — | Not yet written |

Future phase specs are written just before they're executed, not all at once up front. Writing them too early risks specifying behavior that contradicts what we learn from the phases that come before.

## Discipline

Two rules that keep this system honest:

**Work on one phase at a time.** The "Out of scope" list in each spec exists because adjacent work is the most tempting drift. Respect it.

**Don't revise decisions mid-phase.** If during execution you find a decision in `docs/DECISIONS.md` is wrong, surface it; don't quietly change it in the agent session. Decisions are append-only — supersede, don't edit.
