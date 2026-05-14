# ai_factory Rules

1. **Work on one phase at a time.** The current phase spec's "Out of scope" list is a hard boundary. Do not implement Phase N+1 behavior while working on Phase N.

2. **Do not edit `docs/DECISIONS.md` in place.** If a decision needs to change, append a new ADR that supersedes the old one.

3. **Do not run `git push --force` on shared branches.**

4. **Do not commit `manifest.yaml`, `.env`, anything inside a target repo's `local_path`, or any credentials or tokens.**

5. **When the right shape of a thing is unclear, read `docs/DECISIONS.md` first.** Many patterns that look like open questions have already been settled there.

6. **Before doing any work related to a target project, read that project's `CLAUDE.md` and any relevant memory files first.** Do not assume conventions from `ai_factory` apply to target projects.

7. **Before pushing target-repo work, run the relevant validation commands and make sure they pass.** For code changes, this includes that repo's applicable build, lint, and test commands. If a check cannot be run, do not push until the blocker is explicit and accepted.

8. **Never push directly to `main` (or any default branch) in ai_factory or any target repo.** Always create a feature branch and push there. Direct pushes to `main` are only permitted when the user explicitly instructs it.
