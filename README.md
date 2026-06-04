# gh-puller

[![CI](https://github.com/YOUR_USER/YOUR_REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USER/YOUR_REPO/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**gh-puller** — a daemon that clones and periodically updates every repository of a GitHub user, preserving **all branches** — not just the default one.

## Features

- Clones all user repositories from the GitHub API
- Fetches **every remote branch** (main, develop, feature/*, hotfix/*, etc.)
- Creates local tracking branches for each remote branch
- Periodic sync loop (configurable interval)
- Docker image ready
- Rate-limit aware GitHub API client with retry
- Token passed via HTTP header — **never written to disk**

## Quick start

```bash
pip install -r requirements.txt

export GITHUB_TOKEN="ghp_..."
export GITHUB_USERNAME="your-username"

python3 pull_repos.py
```

## Docker

```bash
docker build -t gh-puller .

mkdir -p repos

docker run -d --name gh-puller \
  -e GITHUB_TOKEN="ghp_xxx" \
  -e GITHUB_USERNAME="your-username" \
  -e PULL_INTERVAL=3600 \
  -v "$(pwd)/repos:/home/user/git" \
  gh-puller
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | — | GitHub Personal Access Token (required) |
| `GITHUB_USERNAME` | — | GitHub username (required) |
| `GIT_DIR` | `~/git` | Directory where repos are stored |
| `PULL_INTERVAL` | `3600` | Seconds between sync cycles |

## How it works

1. Fetches the repository list via `GET /users/{username}/repos` (paginated, 100 per page)
2. For each repo:
   - **Not cloned yet**: `git init` → `git remote add` → `git fetch --all` → tracking branches for every remote branch
   - **Already exists**: `git fetch --all --prune` → force-update all local branches to match remote → reset default branch
3. Sleeps `PULL_INTERVAL` seconds and repeats

## Security

The GitHub token is passed to git via `http.extraHeader` (`Authorization: Bearer ...`).
It exists only in process memory and is **never** written to `.git/config`, disk, or any
credential store.

## License

[MIT](LICENSE)
