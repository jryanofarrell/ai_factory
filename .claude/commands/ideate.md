# Skill: /ideate

## Purpose

Start a collaborative product discussion to explore and refine an idea before it becomes a Linear ticket. This recipe puts Claude in discovery mode — no code, no file edits, just thinking.

## Behavior when invoked

When the user runs `/ideate`, Claude must:

1. **Acknowledge the mode.** Say something like: "I'm in ideation mode. Tell me what you're thinking — I'll ask questions and help shape it into something concrete. No code until we're done."

2. **Ask, don't assume.** For each idea the user shares, ask at least one clarifying question before proposing anything. Good questions to ask:
   - What does "done" look like? How would you know this works?
   - Which files or parts of the codebase does this touch?
   - Is there a simpler version that delivers most of the value?
   - Any constraints — things that must not change, or dependencies?
   - How long should this realistically take?

   When the work touches more than one file, also surface the **per-file decomposition**:
   - Walk the user through "which files would change, in what order" — schema first, then manager, then service, then routes, then UI.
   - For each file, identify the **recipe** that captures the relevant pattern (or flag that the recipe needs to be written first — see `/ticket`).
   - **Every subtask must end up executable by a small local model.** Each one runs on the local executor by default. That means by the end of decomposition every subtask has:
     - The exact file(s) it touches.
     - The recipe it follows (read the recipe during ideation if you haven't — don't guess at its contents).
     - The specifics the recipe cannot know: exact column names, function signatures, branching algorithms in pseudocode, layout grids, copy strings, edge-case behavior.
   - **If any subtask would require the executor to make a design decision, the ideation isn't done.** Push the decision into the discussion now — pseudocode it, name the alternatives and pick one, specify the exact behavior at edge cases. The expensive reasoning happens here, once, not at execution time.
   - **Question every AC's "why".** "X can be filtered by Y" isn't a requirement unless someone can name the user reason. Symmetric ACs ("filter the global list, also filter the curated list") are suspicious — one is usually real, the other often residual scope from the brain dump. Push back.
   - **Read the recipe files during the discussion**, not just their names. Codebase conventions (e.g., "all reads go through the manager", "BaseManager.get is findById, don't redefine it") are in the recipes and easy to violate by pattern-matching from training data. Pulling the recipe in catches violations before they become subtasks.

   The goal is that by the end of ideation, both you and the user can name the subtasks the ticket will contain, each one is detailed enough for a local model to execute, and every subtask follows its recipe rather than inventing a new pattern.

3. **Stay in planning mode.** Do NOT:
   - Read or edit files
   - Run commands
   - Write code snippets as a solution
   - Open PRs or branches
   - Create any Linear issues

   DO:
   - Summarise what you've heard back to the user to check understanding
   - Suggest breaking a large idea into smaller tickets
   - Flag scope that seems risky or unclear
   - Help the user write crisp acceptance criteria

4. **Signal when ready.** When the discussion feels complete, say: "I think we have enough to create a ticket. Run `/ticket` and I'll draft the Linear issue(s) for your review."

## What "complete" looks like

A good ideation session ends with the user understanding:
- What the ticket title would be
- What the acceptance criteria are (specific and testable)
- Which files or paths are in scope
- **The per-file subtask list** — one subtask per file, each pointing at the recipe it follows, with a clear tier hint where the file is mechanical enough for the local tier
- **Any missing recipes** that must be written before this ticket can be decomposed cleanly
- Roughly how long it should take

Claude does not need to produce any artifact during `/ideate` — the conversation itself is the output. `/ticket` turns it into structured tickets, including the `## Subtasks` section.
