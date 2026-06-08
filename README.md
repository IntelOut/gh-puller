# GitGrab

[![CI](https://github.com/IntelOut/gitgrab/actions/workflows/ci.yml/badge.svg)](https://github.com/IntelOut/gitgrab/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/IntelOut/gitgrab?logo=github)](https://github.com/IntelOut/gitgrab/releases/latest)
[![Docker image](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/IntelOut/gitgrab/pkgs/container/gitgrab)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**GitGrab** — a daemon that clones and periodically updates every repository of a GitHub user, preserving **all branches** — not just the default one.

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
- **GIT_ASKPASS** authentication — token never touches cmdline or `.git/config`
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

python3 gitgrab.py
```

## Docker

Pull the pre-built image (recommended):

```bash
docker pull ghcr.io/intelout/gitgrab:latest

mkdir -p repos

docker run -d --name gitgrab \
  -e GITHUB_TOKEN="ghp_xxx" \
  -e GITHUB_USERNAME="your-username" \
  -e PULL_INTERVAL=3600 \
  -v "/data/repos:/data/repos" \
  ghcr.io/intelout/gitgrab:latest
```

Or build and run locally:

```bash
docker build -t gitgrab .

mkdir -p repos

docker run -d --name gitgrab \
  -e GITHUB_TOKEN="ghp_xxx" \
  -e GITHUB_USERNAME="your-username" \
  -e PULL_INTERVAL=3600 \
  -v "/data/repos:/data/repos" \
  gitgrab
```

## Configuration

| Variable | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | — | GitHub Classic PAT with `repo` scope (required) |
| `GITHUB_USERNAME` | — | GitHub username (required) |
| `GIT_DIR` | `/data/repos` | Directory where repos are stored |
| `PULL_INTERVAL` | `3600` | Seconds between sync cycles |
| `EXCLUDE_PATTERNS` | `` | Comma-separated regex patterns for repo names to skip |
| `PARALLEL_WORKERS` | `4` | Max parallel clone/update workers |
| `DISK_MIN_GB` | `1` | Minimum free disk space in GB before aborting |
| `GIT_DEPTH` | — | If set, pass `--depth N` to git fetch (shallow, preserves all branches) |

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

- Authentication uses **`GIT_ASKPASS`**: a temporary helper script is created at
  startup and passed to each git subprocess via the environment. The script reads
  the token from the `GITGUB_TOKEN` env var — **the token never appears in**:
  - **git command-line arguments** (not visible via `ps` / `/proc/*/cmdline`)
  - **git remote URLs** (not stored in `.git/config` on disk)
  - **any file on disk** (the askpass script contains no secrets)
- The GitHub API token is sent as an `Authorization: Bearer` header via the
  `requests` library — it never leaves process memory.
- A `CredentialFilter` redacts Bearer tokens and embedded-credential URL
  patterns from all log output before writing to disk.

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
