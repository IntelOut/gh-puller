# gh-puller

[![CI](https://github.com/IntelOut/gh-puller/actions/workflows/ci.yml/badge.svg)](https://github.com/IntelOut/gh-puller/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/IntelOut/gh-puller?logo=github)](https://github.com/IntelOut/gh-puller/releases/latest)
[![Docker image](https://img.shields.io/badge/docker-ghcr.io-blue?logo=docker)](https://github.com/IntelOut/gh-puller/pkgs/container/gh-puller)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**gh-puller** — a daemon that clones and periodically updates every repository of a GitHub user, preserving **all branches** — not just the default one.

## Features

- Clones all user repositories from the GitHub API
- Fetches **every remote branch** (main, develop, feature/*, hotfix/*, etc.)
- Creates local tracking branches for each remote branch
- Periodic sync loop (configurable interval)
- **Parallel** clone/update with configurable worker count
- **Exclude patterns** to skip repositories by name regex
- **Cached** repository list to avoid redundant API calls
- Docker image ready with healthcheck
- Rate-limit aware GitHub API client with retry and backoff
- Token passed via HTTP header — **never written to disk**

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
  -v "${PWD}/repos:/home/user/git" \
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
  -v "${PWD}/repos:/home/user/git" \
  gh-puller
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub Personal Access Token (required) |
| `GITHUB_USERNAME` | — | GitHub username (required) |
| `GIT_DIR` | `~/git` | Directory where repos are stored |
| `PULL_INTERVAL` | `3600` | Seconds between sync cycles |
| `EXCLUDE_PATTERNS` | `` | Comma-separated regex patterns for repo names to skip |
| `PARALLEL_WORKERS` | `4` | Max parallel clone/update workers |

## How it works

1. Fetches the repository list via `GET /users/{username}/repos` (paginated, 100 per page, cached for 5 min)
2. Filters repositories against `EXCLUDE_PATTERNS`
3. For each repo (in parallel, up to `PARALLEL_WORKERS`):
   - **Not cloned yet**: `git init` → `git remote add` → `git fetch --all` → tracking branches for every remote branch
   - **Already exists**: `git fetch --all --prune` → force-update all local branches → fast-forward default branch
4. Sleeps `PULL_INTERVAL` seconds and repeats

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

## Security

The GitHub token is passed to git via `http.extraHeader` (`Authorization: Bearer ...`).
It exists only in process memory and is **never** written to `.git/config`, disk, or any
credential store. A logging filter redacts Bearer tokens from log output.

## License

[MIT](LICENSE)
