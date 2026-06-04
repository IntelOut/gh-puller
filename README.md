# gh-puller

[![CI](https://github.com/IntelOut/gh-puller/actions/workflows/ci.yml/badge.svg)](https://github.com/IntelOut/gh-puller/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/IntelOut/gh-puller?logo=github)](https://github.com/IntelOut/gh-puller/releases/latest)
[![Docker image](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/IntelOut/gh-puller/pkgs/container/gh-puller)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**gh-puller** — a daemon that clones and periodically updates every repository of a GitHub user, preserving **all branches** — not just the default one.

## Features

- Clones all user repositories (public and private) via the GitHub API
- Fetches **every remote branch** (main, develop, feature/*, hotfix/*, etc.)
- Creates local tracking branches for each remote branch
- Default branch auto-detection via `origin/HEAD` → remote refs → fallback to `main`
- Periodic sync loop (configurable interval, default 1 hour)
- **Parallel** clone/update with configurable worker count
- **Exclude patterns** to skip repositories by name regex
- **Cached** repository list to avoid redundant API calls
- Docker image ready with healthcheck
- Rate-limit aware GitHub API client with retry and backoff
- Token embedded in clone URL (`USERNAME:TOKEN@`) — **never stored in `.git/config`**
- Fallback clone method (`git clone --mirror`) if primary flow fails

## Prerequisites

- **GitHub Classic Personal Access Token** with the **`repo`** scope.
  Fine-grained PATs are not recommended — they require manual `Contents: Read` permission per repository.

  Create one at: [GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)](https://github.com/settings/tokens)

## Quick start

```bash
pip install -r requirements.txt

export GITHUB_TOKEN="ghp_..."
export GITHUB_USERNAME="your-username"

python3 pull_repos.py
```

## Docker

Pull the pre-built image (recommended):

```bash
docker pull ghcr.io/intelout/gh-puller:latest

mkdir -p repos

docker run -d --name gh-puller \
  -e GITHUB_TOKEN="ghp_xxx" \
  -e GITHUB_USERNAME="your-username" \
  -e PULL_INTERVAL=3600 \
  -v "/data/repos:/data/repos" \
  ghcr.io/intelout/gh-puller:latest
```

Or build and run locally:

```bash
docker build -t gh-puller .

mkdir -p repos

docker run -d --name gh-puller \
  -e GITHUB_TOKEN="ghp_xxx" \
  -e GITHUB_USERNAME="your-username" \
  -e PULL_INTERVAL=3600 \
  -v "/data/repos:/data/repos" \
  gh-puller
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub Classic PAT with `repo` scope (required) |
| `GITHUB_USERNAME` | — | GitHub username (required) |
| `GIT_DIR` | `/data/repos` | Directory where repos are stored |
| `PULL_INTERVAL` | `3600` | Seconds between sync cycles |
| `EXCLUDE_PATTERNS` | `` | Comma-separated regex patterns for repo names to skip |
| `PARALLEL_WORKERS` | `4` | Max parallel clone/update workers |

> **Note on `GIT_DIR`:** This is the application's own env var (where to store repos), not git's `GIT_DIR`.
> The application strips it from git subprocess environments to avoid conflicts.

## How it works

1. Fetches the repository list via `GET /user/repos` (paginated, 100 per page, cached for 5 min).
   Returns all repos the token has access to — both public and private.
2. Filters repositories against `EXCLUDE_PATTERNS`
3. For each repo (in parallel, up to `PARALLEL_WORKERS`):
   - **Not cloned yet**: `git init` → `git remote add` → `git fetch --all` → checkout default branch → tracking branches for every remote branch
   - **Already exists**: `git fetch --all --prune` → force-update all local branches → fast-forward default branch
4. If the initial `git init`-based clone fails, falls back to `git clone --mirror` + conversion
5. Default branch detection: checks `origin/HEAD` → remote refs (`origin/main`, `origin/master`) → falls back to `main`
6. Sleeps `PULL_INTERVAL` seconds and repeats

## Security

- The token is embedded in the clone URL (`https://USERNAME:TOKEN@github.com/...`) for git authentication.
  After `fetch` / `clone` completes, the remote URL is immediately cleaned — **the token is never stored in `.git/config`**.
- A logging filter redacts `USERNAME:TOKEN@` patterns from log output.

## Troubleshooting

### Permission denied (`cannot mkdir`)

When running in Docker on Windows, the volume directory may be owned by root.
Redeploy with tag `v1.0.9+` — the container runs as root, avoiding permission issues.

### Invalid credentials

If git operations fail with `remote: invalid credentials`:

1. Ensure you're using a **Classic PAT** (not fine-grained) with the **`repo`** scope
2. Verify the token in the container matches the one with `repo` scope:
   ```bash
   docker exec gh-puller printenv GITHUB_TOKEN | head -c 15
   ```
3. The token is embedded in the clone URL as `https://USERNAME:TOKEN@github.com/...`.
   Only **one** Authorization header is sent (from the URL) — `http.extraHeader` is not used.

### Pathspec 'main' did not match

If you see `error: pathspec 'main' did not match any file(s) known to git`:

The repository's default branch is not `main` or `master`. Use tag `v1.0.9+` which
checks remote refs (`origin/main`, `origin/master`) instead of local refs after fetch.

### No repos found (only 6 instead of 30+)

The API endpoint `/users/{username}/repos` returns only **public** repos.
Use tag `v1.0.4+` which uses `/user/repos` — returns all repos the token can access.

## Development

```bash
pip install -r requirements-dev.txt

# Run all checks
make check

# Or individually
make lint        # ruff
make typecheck   # mypy
make pylint       # pylint
make test        # pytest
```

## License

[MIT](LICENSE)
