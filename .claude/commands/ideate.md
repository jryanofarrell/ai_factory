# Skill: /ideate

## Purpose

Start a collaborative product discussion to explore and refine an idea before it becomes a Linear ticket. This skill puts Claude in discovery mode — no code, no file edits, just thinking.

## Behavior when invoked

When the user runs `/ideate`, Claude must:

1. **Acknowledge the mode.** Say something like: "I'm in ideation mode. Tell me what you're thinking — I'll ask questions and help shape it into something concrete. No code until we're done."

2. **Ask, don't assume.** For each idea the user shares, ask at least one clarifying question before proposing anything. Good questions to ask:
   - What does "done" look like? How would you know this works?
   - Which files or parts of the codebase does this touch?
   - Is there a simpler version that delivers most of the value?
   - Any constraints — things that must not change, or dependencies?
   - How long should this realistically take?

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
- Roughly how long it should take

Claude does not need to produce any artifact during `/ideate` — the conversation itself is the output. `/ticket` turns it into structured tickets.
