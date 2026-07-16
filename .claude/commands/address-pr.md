# Skill: /address-pr

## Purpose

Address GitHub PR review feedback with a human in the loop, in two hard-gated steps: pull the reviewer's comments and propose a change for each one in the conversation (no writes), then — only after explicit approval — apply the accepted changes as **one commit**, push to the PR branch, and reply to each comment thread with a summary of what was decided.

This is the interactive counterpart to `factory address-pr-comments`, which runs an executor over the feedback autonomously. Use the CLI when the fixes are mechanical and trusted; use this command when the feedback deserves discussion before anything lands.

## Invocation

```
/address-pr <pr-url>
/address-pr [<repo-key>] <pr-number>
```

- **Preferred: a GitHub PR URL** (e.g. `https://github.com/jryanofarrell/billy-ai/pull/6`). Infer everything from it: extract `<owner>/<repo>` and the PR number, then resolve the working copy by matching `<owner>/<repo>` against each repo's `github:` field in `manifest.yaml` → work in `repos/<repo-key>/`. If it matches ai_factory's own remote instead, work in the current repo. If it matches neither, say so and stop — don't guess at a checkout location.
- Alternatively `<repo-key>` (a repo from `manifest.yaml`) + PR number, or a bare PR number for the current repo (ai_factory itself).
- Whenever the PR lives in a target repo: **read that repo's `CLAUDE.md` before proposing anything.**

## Step 1 — Fetch and propose (read-only; no file writes, no git writes)

1. Resolve the repo and refuse to proceed if the PR is closed or merged (`gh pr view <n> --json state,title,headRefName,url,body`), or if the repo's working tree is dirty.
2. Fetch the feedback:
   - **Unresolved inline review threads** via GraphQL (`gh api graphql` — `pullRequest.reviewThreads` with `isResolved`, comment `id`/`databaseId`, `path`, `line`, `body`, author). Resolved threads are skipped.
   - **Review summary bodies** and **issue comments** (`gh pr view --json reviews,comments`) — these may carry feedback with no inline anchor.
3. Present each piece of feedback as a numbered item (`C1`, `C2`, …):
   - file:line and the reviewer's comment, quoted
   - the current code it points at (from the PR branch — read via `git show origin/<headRefName>:<path>` or a local checkout, without switching branches yet)
   - a concrete **proposed change as a diff block**, or — when the right answer is "don't change it" — a drafted reply explaining why, marked `reply-only`
4. Discuss. The user pushes back per item; revise proposals until each item has a settled disposition: **apply**, **reply-only**, or **skip**.
5. End Step 1 by listing the final disposition of every item and asking for explicit approval to proceed. **Do not touch the working tree, branches, or GitHub until the user approves.** If the user approves only a subset, apply only that subset.

## Step 2 — Apply, commit, push, reply (only after approval)

1. Check out the PR branch (`gh pr checkout <n>` in the repo's working copy) and confirm a clean state.
2. Apply every **apply**-disposition change exactly as agreed in Step 1. If applying reveals the agreed diff no longer fits (branch moved, conflict), stop and bring it back to the conversation — don't improvise silently.
3. Run the repo's lint and tests per its own `CLAUDE.md` conventions. Failures go back to the conversation before committing.
4. Commit **everything as one commit**:

   ```
   Address PR review feedback

   - C1: <one-line summary of the change>
   - C3: <one-line summary>
   (C2 reply-only, C4 skipped per discussion)
   ```

   Then push to the PR branch. Never force-push.
5. Reply in **each** comment thread — including reply-only and skipped items — with a short summary of the discussion's outcome, e.g.:
   - applied: "Changed `<what>` to `<what>` as suggested — see `<short-sha>`."
   - reply-only: the drafted explanation of why the code stays as-is
   - skipped: why it was deliberately deferred (and where it's tracked, if anywhere)

   Use `gh api repos/<owner>/<repo>/pulls/<n>/comments/<comment-id>/replies -f body='…'` for inline threads; feedback that came from a review body or issue comment gets one summarizing PR comment instead.
6. Report back: commit SHA, PR URL, and the per-item outcome list.

## Rules

- **The step boundary is hard.** No file, branch, commit, push, or GitHub mutation before the user's explicit approval at the end of Step 1.
- **One commit per invocation**, covering the whole approved batch. Re-running the command for a second review round produces a second commit.
- **Reply to every thread, resolve none.** Resolution belongs to the reviewer — replies give them what they need to resolve.
- **Never merge the PR, never force-push, never rebase the PR branch.**
- **Dirty working tree → refuse** and tell the user what's dirty.
- For target repos, the target repo's own `CLAUDE.md`/`.ai/` conventions govern the proposed code — not ai_factory's.
