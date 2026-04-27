# Manifest

`manifest.yaml` is the local configuration file that lists every target repository registered with the factory. Because it contains host-specific filesystem paths, it is **gitignored** and must be created manually on each machine. `manifest.example.yaml` at the repo root is the checked-in template.

## File location

`manifest.yaml` lives at the root of the `ai_factory` repository, alongside `manifest.example.yaml`.

## Schema

```yaml
version: 1
queue_dir: .factory/queue
repos:
  <repo-key>:
    github: <owner>/<repo>
    local_path: <absolute-or-tilde-path>
    default_branch: <branch>
    test_command: "<shell command>"
    build_command: "<shell command>"
    linear_team: <TEAM_KEY>
```

## Top-level fields

| Field | Type | Required | Default | Purpose |
|---|---|---|---|---|
| `version` | integer | Yes | — | Schema version. Currently `1`. The factory rejects manifests with unknown versions. |
| `queue_dir` | string | No | `.factory/queue` | Path (relative to `ai_factory` repo root, or absolute) where the Phase 2 pull step writes ticket files. The directory is created automatically on first pull. Gitignored by default (`.factory/` is in `.gitignore`). |
| `repos` | map | Yes | — | Map of repo key → repo config. The key is a short identifier used to reference the repo in CLI commands and logs. By convention it matches the repository name (without the owner prefix). |

## Per-repo fields

| Field | Type | Required | Default | First read in | Purpose |
|---|---|---|---|---|---|
| `github` | string | Yes | — | Phase 1 | The GitHub repository in `owner/repo` format. Used to construct clone URLs and API calls. |
| `local_path` | string | Yes | — | Phase 1 | Absolute or `~`-prefixed path to the local clone of the target repository. The executor runs inside this directory. Must be an existing directory at run time. |
| `default_branch` | string | Yes | — | Phase 1 | The default branch name (typically `main` or `master`). The executor checks out from this branch and opens PRs against it. |
| `test_command` | string | No | `""` | Phase 1 | Shell command to run the test suite inside the target repo. The executor runs this after making changes to verify correctness. If absent, no test step is run (not recommended). |
| `build_command` | string | No | `""` | Phase 1 | Shell command to build the project. Run before tests if present. If absent, no build step is run. |
| `linear_team` | string | No | (derived from repo key) | Phase 2 | Linear team key for the team that owns this repository. Used when the team key does not match the repo key by convention. If absent, the factory uses the repo key uppercased (e.g., `thms-platform` → `THMS-PLATFORM`). Best practice is to set this explicitly. |

## Sensitive fields

`local_path` is the only field that varies by machine. All other fields can, in principle, be shared. The manifest is gitignored as a whole (not field-by-field) because `local_path` makes the entire file non-portable.

API keys and tokens are never stored in the manifest. They are read from environment variables at run time (e.g., `LINEAR_API_KEY`, `GITHUB_TOKEN`).

## Setup on a new machine

1. Clone `ai_factory`.
2. Copy `manifest.example.yaml` to `manifest.yaml`.
3. Edit `local_path` for each registered repo to point at the correct local directory.
4. Clone each target repo to the path specified in `local_path` if not already present.
5. Set required environment variables (`LINEAR_API_KEY`, `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`).
6. Run `uv sync` to install dependencies.

## Example

```yaml
version: 1
queue_dir: .factory/queue
repos:
  thms-platform:
    github: toms-hms/thms-platform
    local_path: ~/factory/repos/thms-platform
    default_branch: main
    test_command: "npm test"
    build_command: "npm run build"
    linear_team: THMS
```
