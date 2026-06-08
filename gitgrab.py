#!/usr/bin/env python3
"""
GitHub All-Branches Repository Puller.

Clones / updates all repositories of a GitHub user, fetching every branch
(main, develop, feature/*, etc.) — not just the default branch.

Environment variables:
    GITHUB_TOKEN        — Personal Access Token (required)
    GITHUB_USERNAME     — GitHub username (required)
    GIT_DIR             — Target directory for repos (default: /data/repos)
    PULL_INTERVAL       — Sleep seconds between sync cycles (default: 3600)
    EXCLUDE_PATTERNS    — Comma-separated regex patterns for repo names to skip
    PARALLEL_WORKERS    — Max parallel clone/update workers (default: 4)
    DISK_MIN_GB         — Minimum free disk space in GB before aborting (default: 1)
    GIT_DEPTH           — If set, pass --depth N to git fetch (shallow, all branches)

The script runs as a daemon: it syncs all repos, sleeps PULL_INTERVAL seconds,
then repeats indefinitely.

Security notes:
    - Authentication uses GIT_ASKPASS: a temporary helper script that reads the
      token from the process environment.  The token never appears in:
        * git command-line arguments (not visible via ps / proc/pid/cmdline)
        * git remote URLs (not stored in .git/config on disk)
        * any file on disk (the askpass script contains no secrets)
    - The token lives only in the process environment (GH_TOKEN env var
      passed to child git processes) and in the requests HTTP headers sent to
      the GitHub API.
    - All log messages containing tokens or credentials are redacted by
      CredentialFilter before being written to the log file.
"""

import atexit
import concurrent.futures
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

import requests

_LOG_DIR = Path.home() / '.local' / 'share' / 'gitgrab'
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / 'git-puller.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(str(_LOG_FILE)),
        logging.StreamHandler(sys.stdout)
    ]
)


class CredentialFilter(logging.Filter):
    """Redact credentials (tokens, passwords) from log records.

    Covers:
      - ``Authorization: Bearer <token>`` headers
      - ``https://user:pass@host`` embedded-credential URLs
      - ``http.extraHeader=Authorization: Bearer <token>`` (git -c syntax)
      - ``Bearer <token>`` with various boundary chars (|, \x00, etc.)
    """

    _BEARER_RE = re.compile(
        r'(?i)(Authorization|extraHeader)\s*[:=]\s*Bearer\s+\S+?(?=[\s\'\"\|,\]\)\x00]|$)'
    )
    _URL_CRED_RE = re.compile(
        r'https://[^/\s:]+:[^/\s@]+@'
    )

    def filter(self, record):
        msg = record.getMessage()
        msg = self._BEARER_RE.sub(
            r'\1: Bearer ***REDACTED***', msg
        )
        msg = self._URL_CRED_RE.sub(
            'https://***REDACTED***:***REDACTED***@', msg
        )
        if record.msg != msg or record.args:
            record.msg = msg
            record.args = ()
        return True


logging.getLogger().addFilter(CredentialFilter())

_BASE_ENV = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}


def _error_detail(exc):
    """Return stderr from a subprocess error, falling back to str(exc)."""
    stderr = getattr(exc, 'stderr', None)
    return stderr.strip() if stderr else str(exc)


class GitHubRepoPuller:
    """Mirrors every repository of a GitHub user, preserving all branches.

    Authentication is handled via GIT_ASKPASS: a temporary helper script is
    created at startup and passed to each git subprocess via the environment.
    The token is **never** embedded in git command-line arguments or remote URLs,
    which prevents leakage through ``/proc/*/cmdline`` or ``.git/config`` on disk.
    """

    _GIT_TIMEOUT = 300
    _API_MAX_RETRIES = 3
    _askpass_script_path: str | None = None
    _script_lock = Lock()
    shutdown_requested: bool = False

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        git_dir: str | None = None,
        github_token: str | None = None,
        exclude_patterns: str | None = None,
        parallel_workers: int | None = None,
        disk_min_gb: float | None = None,
        git_depth: int | None = None,
    ):
        resolved_dir: str | Path = (
            git_dir if git_dir is not None else (os.environ.get('GIT_DIR') or '')
        )
        if not resolved_dir:
            resolved_dir = Path('/data/repos')
        self.git_dir = Path(resolved_dir).expanduser().resolve()
        self.github_token = github_token or os.environ.get('GITHUB_TOKEN')
        self.github_username = os.environ.get('GITHUB_USERNAME')
        self.git_dir.mkdir(parents=True, exist_ok=True)

        raw_exclude = exclude_patterns or os.environ.get('EXCLUDE_PATTERNS', '')
        self.exclude_patterns: list = [
            re.compile(p) for p in raw_exclude.split(',') if p.strip()
        ] if raw_exclude else []

        self.parallel_workers: int = (
            parallel_workers or
            int(os.environ.get('PARALLEL_WORKERS', 4))
        )

        self.disk_min_gb: float = (
            disk_min_gb if disk_min_gb is not None
            else float(os.environ.get('DISK_MIN_GB', '1'))
        )

        self.git_depth: int | None = (
            git_depth if git_depth is not None
            else (int(os.environ['GIT_DEPTH']) if 'GIT_DEPTH' in os.environ else None)
        )

        self._repos_cache: list | None = None
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0

        logging.info(
            "Working directory: %s (exclude=%d patterns, workers=%d, "
            "disk_min_gb=%s, git_depth=%s)",
            self.git_dir, len(self.exclude_patterns), self.parallel_workers,
            self.disk_min_gb, self.git_depth or 'full'
        )

    @classmethod
    def _ensure_askpass_script(cls):
        """Create (once) a temporary GIT_ASKPASS helper script.

        The script reads credentials from ``GH_USER`` / ``GH_TOKEN``
        environment variables — it does **not** embed secrets in the file.
        Returns the absolute path to the script, or ``None`` on failure.
        """
        if cls._askpass_script_path is not None:
            if os.path.exists(cls._askpass_script_path):
                return cls._askpass_script_path
            cls._askpass_script_path = None

        with cls._script_lock:
            if cls._askpass_script_path is not None:
                if os.path.exists(cls._askpass_script_path):
                    return cls._askpass_script_path
                cls._askpass_script_path = None

            is_windows = sys.platform == 'win32'
            suffix = '.py'
            content = textwrap.dedent('''\
                import os, sys
                msg = sys.argv[1] if len(sys.argv) > 1 else ''
                if 'user' in msg.lower():
                    print(os.environ.get('GH_USER', ''))
                else:
                    print(os.environ.get('GH_TOKEN', ''))
            ''')
            if not is_windows:
                content = '#!/usr/bin/env python3\n' + content

            try:
                with tempfile.NamedTemporaryFile(
                    mode='w', suffix=suffix, prefix='gitgrab_askpass_',
                    delete=False
                ) as f:
                    f.write(content)
                    script_path = f.name

                if not is_windows:
                    os.chmod(script_path, 0o700)

                cls._askpass_script_path = script_path
                atexit.register(cls._cleanup_askpass_script)

                logging.debug("GIT_ASKPASS helper created: %s", script_path)
            except OSError:
                logging.warning(
                    "Could not create GIT_ASKPASS helper — "
                    "token will not be available for git"
                )
                cls._askpass_script_path = None

        return cls._askpass_script_path

    @classmethod
    def _cleanup_askpass_script(cls):
        """Remove the temporary GIT_ASKPASS helper script at process exit."""
        if cls._askpass_script_path and os.path.exists(cls._askpass_script_path):
            try:
                os.unlink(cls._askpass_script_path)
            except OSError:
                pass

    @staticmethod
    def _scrub_url_credentials(url):
        """Remove any embedded credentials from a URL."""
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            clean = parsed._replace(netloc=parsed.hostname if parsed.hostname else parsed.netloc)
            return clean.geturl()
        return url

    @staticmethod
    def _validate_clone_url(clone_url):
        """Warn if the clone URL uses a non-HTTPS scheme.

        SSH (scheme-less or git+ssh) and empty-scheme URLs are allowed for
        existing repos that use key-based auth. Raises ``ValueError`` only
        for explicitly non-HTTPS schemes like ``http://``.
        """
        parsed = urlparse(clone_url)
        if parsed.scheme and parsed.scheme not in ('https', 'ssh', 'git+ssh'):
            raise ValueError(
                f"Clone URL must use HTTPS (got scheme '{parsed.scheme}'): {clone_url}"
            )

    def _build_fetch_cmd(self, base_args):
        """Build a git fetch command, appending --depth if configured."""
        cmd = list(base_args)
        if self.git_depth is not None:
            cmd.extend(['--depth', str(self.git_depth)])
        return cmd

    def _run(self, cmd, check=True, extra_env=None, **kwargs):
        """Wrapper around subprocess.run with a default timeout.

        Automatically injects ``GIT_ASKPASS`` and credential environment
        variables into every subprocess so that git can authenticate without
        secrets ever appearing on the command line or in remote URLs.

        *extra_env* — optional dict of additional environment variable overrides.
        """
        kwargs.setdefault('timeout', self._GIT_TIMEOUT)
        kwargs.setdefault('capture_output', True)
        kwargs.setdefault('text', True)
        env = _BASE_ENV.copy()
        env.pop('GIT_DIR', None)
        if extra_env:
            env.update(extra_env)

        askpass = self._ensure_askpass_script()
        if askpass is not None and self.github_token:
            env['GIT_ASKPASS'] = askpass
            env['GH_USER'] = self.github_username or ''
            env['GH_TOKEN'] = self.github_token

        kwargs.setdefault('env', env)
        return subprocess.run(cmd, check=check, **kwargs)

    @classmethod
    def handle_signal(cls, _signum, _frame):
        """Set the shutdown flag on SIGTERM/SIGINT for graceful shutdown.
        Does NOT call logging — it is not async-signal-safe.
        """
        cls.shutdown_requested = True

    def _check_disk_space(self):
        """Raise OSError if free disk space falls below configured threshold."""
        usage = shutil.disk_usage(self.git_dir)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < self.disk_min_gb:
            raise OSError(
                f"Only {free_gb:.1f} GB free, need at least {self.disk_min_gb} GB"
            )

    def _get_default_branch(self, repo_path):
        """Detect the repository's default branch (origin/HEAD -> main/master).

        Falls back to 'main' if detection fails.
        """
        try:
            result = self._run(
                ['git', '-C', str(repo_path), 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                check=False
            )
            if result.returncode == 0:
                return result.stdout.strip().replace('refs/remotes/origin/', '')
        except (subprocess.CalledProcessError, OSError):
            pass
        for candidate in ('main', 'master'):
            result = self._run(
                ['git', '-C', str(repo_path), 'show-ref', f'refs/remotes/origin/{candidate}'],
                check=False
            )
            if result.returncode == 0:
                return candidate
        return 'main'

    def _iter_remote_branches(self, repo_path):
        """Yield (local_name, remote_ref) for every remote branch.

        Skips symbolic refs (e.g. origin/HEAD).
        """
        result = self._run(
            ['git', '-C', str(repo_path), 'branch', '-r'], check=True
        )
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line or ' -> ' in line or not line.startswith('origin/'):
                continue
            yield line.replace('origin/', '', 1), line

    def _create_local_tracking_branches(self, repo_path):
        """Create a local tracking branch for every remote branch that lacks one.

        Skips symbolic refs (e.g. origin/HEAD) and existing local branches.
        """
        for local_branch, remote_ref in self._iter_remote_branches(repo_path):
            exists = self._run(
                ['git', '-C', str(repo_path), 'show-ref', '--verify',
                 f'refs/heads/{local_branch}'],
                check=False
            )
            if exists.returncode != 0:
                self._run(
                    ['git', '-C', str(repo_path), 'branch', '--track',
                     local_branch, remote_ref]
                )

    def _clone_with_all_branches(self, repo_path, clone_url):
        """Perform a full clone of every remote branch into *repo_path*.

        Uses the clean (credential-free) clone URL.  Authentication is handled
        transparently via GIT_ASKPASS injected by ``_run``, so the token never
        appears in ``.git/config`` or on the command line.

        Strategy:
          1. git init + remote add origin
          2. git remote set-branches origin '*'   — track every branch
          3. git fetch --all --prune               — fetch all refs (via askpass)
          4. git checkout <default>                — working tree on default branch
          5. create local tracking branches for the rest

        Deleted on failure; the caller must handle cleanup.
        """
        self._check_disk_space()
        self._validate_clone_url(clone_url)
        self._run(['git', 'init', str(repo_path)], check=True)
        self._run(
            ['git', '-C', str(repo_path), 'remote', 'add', 'origin', clone_url],
            check=True
        )
        self._run(
            ['git', '-C', str(repo_path), 'remote', 'set-branches', 'origin', '*'],
            check=True
        )
        self._run(
            self._build_fetch_cmd(
                ['git', '-C', str(repo_path), 'fetch', '--all', '--prune']
            ),
            check=True
        )
        default_branch = self._get_default_branch(repo_path)
        self._run(['git', '-C', str(repo_path), 'checkout', default_branch], check=True)
        self._create_local_tracking_branches(repo_path)

    def _update_all_branches(self, repo_path, clone_url):
        """Fetch and force-update every local branch.

        The remote URL is already clean from the initial clone — no credential
        toggling is needed. Authentication is handled via GIT_ASKPASS.

        Returns a list of branch names that were updated.
        """
        self._validate_clone_url(clone_url)
        self._run(
            ['git', '-C', str(repo_path), 'checkout', '--detach'],
            check=False
        )
        self._run(
            self._build_fetch_cmd(
                ['git', '-C', str(repo_path), 'fetch', '--all', '--prune']
            ),
            check=True
        )
        result = self._run(
            ['git', '-C', str(repo_path), 'rev-parse', '--abbrev-ref', 'HEAD']
        )
        current_branch = result.stdout.strip()

        updated = []
        for lb, remote_ref in self._iter_remote_branches(repo_path):
            if lb == current_branch:
                continue
            exists = self._run(
                ['git', '-C', str(repo_path), 'show-ref', '--verify',
                 f'refs/heads/{lb}'],
                check=False
            )
            try:
                if exists.returncode == 0:
                    self._run(
                        ['git', '-C', str(repo_path), 'branch', '--force', lb, remote_ref],
                        check=True
                    )
                else:
                    self._run(
                        ['git', '-C', str(repo_path), 'branch', '--track', lb, remote_ref],
                        check=True
                    )
                updated.append(lb)
            except subprocess.CalledProcessError as e:
                logging.warning("  Could not update branch %s: %s", lb, e.stderr.strip())

        default_branch = self._get_default_branch(repo_path)
        try:
            self._run(
                ['git', '-C', str(repo_path), 'checkout', '--force', default_branch],
                check=True
            )
            merge_result = self._run(
                ['git', '-C', str(repo_path), 'merge', '--ff-only',
                 f'origin/{default_branch}'],
                check=False
            )
            if merge_result.returncode != 0:
                logging.warning(
                    "  Could not fast-forward %s (local changes may diverge): %s",
                    default_branch, merge_result.stderr.strip()
                )
        except subprocess.CalledProcessError as e:
            logging.warning(
                "  Could not checkout default branch %s: %s",
                default_branch, e.stderr.strip()
            )
        return updated

    def _update_all_branches_fallback(self, clone_url, repo_path):
        """Fallback: bare clone + conversion if the init-based flow fails.

        Uses the clean clone URL directly — the token is provided via
        GIT_ASKPASS so it never appears on the command line.
        """
        tmp_path = repo_path.with_suffix('.tmp')
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        self._validate_clone_url(clone_url)
        clone_cmd = ['git', 'clone', '--mirror', clone_url, str(tmp_path)]
        if self.git_depth is not None:
            clone_cmd.insert(2, '--depth')
            clone_cmd.insert(3, str(self.git_depth))
        self._run(clone_cmd, check=True)
        self._run(
            ['git', '-C', str(tmp_path), 'config', '--local', 'core.bare', 'false'],
            check=False
        )
        self._run(['git', '-C', str(tmp_path), 'reset', '--hard', 'HEAD'], check=False)
        if repo_path.exists():
            shutil.rmtree(repo_path, ignore_errors=True)
        tmp_path.rename(repo_path)

    def _is_excluded(self, repo_name):
        """Check if *repo_name* matches any exclude pattern."""
        return any(p.search(repo_name) for p in self.exclude_patterns)

    def clone_full_repo(self, repo_info):
        """Clone or update a single repository based on its GitHub API info dict.

        Expected keys: 'name', 'clone_url'.
        Returns a status dict: {'status': 'cloned'|'updated'|'up_to_date'|'error'|'skipped',
        'message': ...}
        """
        repo_name = repo_info['name']
        if self._is_excluded(repo_name):
            logging.info("  Skipped (excluded): %s", repo_name)
            return {'status': 'skipped', 'message': 'Skipped by exclude pattern'}
        repo_path = self.git_dir / repo_name
        clone_url = repo_info['clone_url']

        if repo_path.exists():
            return self._update(repo_path, repo_name)

        logging.info("Cloning repository (all branches): %s", repo_name)
        try:
            self._clone_with_all_branches(repo_path, clone_url)
            self.log_all_branches(repo_path)
            logging.info("Cloned all branches of %s", repo_name)
            return {'status': 'cloned', 'message': f"All branches cloned for {repo_name}"}
        except (subprocess.CalledProcessError, OSError) as e:
            logging.error("Failed to clone %s: %s", repo_name, _error_detail(e))
            if repo_path.exists():
                shutil.rmtree(repo_path, ignore_errors=True)
            try:
                logging.info("Retrying with fallback method for %s", repo_name)
                self._update_all_branches_fallback(clone_url, repo_path)
                self.log_all_branches(repo_path)
                return {
                    'status': 'cloned',
                    'message': f"All branches cloned for {repo_name} (fallback)"
                }
            except Exception as fallback_err:
                logging.error(
                    "Fallback also failed for %s: %s",
                    repo_name, _error_detail(fallback_err)
                )
                if repo_path.exists():
                    shutil.rmtree(repo_path, ignore_errors=True)
                return {'status': 'error', 'message': str(fallback_err)}

    def _update(self, repo_path, repo_name):
        """Update all existing local branches from the remote origin."""
        clone_url = self._get_remote_url(repo_path)
        if not clone_url:
            return {'status': 'error', 'message': 'No remote origin found'}
        clone_url = self._scrub_url_credentials(clone_url)

        logging.info("Updating repository: %s", repo_name)
        try:
            updated = self._update_all_branches(repo_path, clone_url)
            if updated:
                logging.info("Updated %d branches in %s", len(updated), repo_name)
                preview = ', '.join(updated[:5])
                if len(updated) > 5:
                    preview += '...'
                return {'status': 'updated', 'message': f"Updated branches: {preview}"}
            return {'status': 'up_to_date', 'message': 'All branches up to date'}
        except subprocess.CalledProcessError as e:
            logging.error("Failed to update %s: %s", repo_name, e.stderr)
            return {'status': 'error', 'message': e.stderr}
        except Exception as e:
            logging.error("Failed to update %s: %s", repo_name, e)
            return {'status': 'error', 'message': str(e)}

    def _get_remote_url(self, repo_path):
        """Return the 'origin' remote URL of the repository at *repo_path*, or None."""
        try:
            result = self._run(
                ['git', '-C', str(repo_path), 'remote', 'get-url', 'origin']
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def log_all_branches(self, repo_path):
        """Log the total number of branches (local + remote) for a repository."""
        try:
            result = self._run(
                ['git', '-C', str(repo_path), 'branch', '-a'], check=True
            )
            branches = [b for b in result.stdout.strip().split('\n') if b.strip()]
            logging.info("  Branches in %s: %d branches", repo_path.name, len(branches))
        except Exception as e:
            logging.warning("  Could not list branches for %s: %s", repo_path.name, e)

    def _fetch_page(self, params, page):
        """Fetch one page from the GitHub API with retry logic.

        Returns (data, stop) where *data* is the JSON list (empty list = no more
        pages) and *stop* is True when the caller should abort the entire scan.
        """
        url = 'https://api.github.com/user/repos'
        headers = {
            'Authorization': f'Bearer {self.github_token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        for attempt in range(self._API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=30)
                if response.status_code == 401:
                    logging.error("GitHub API authentication failed — check GITHUB_TOKEN")
                    return [], True
                if response.status_code == 403:
                    remaining = response.headers.get('X-RateLimit-Remaining', '0')
                    logging.error("GitHub API rate limit exceeded (remaining: %s)", remaining)
                    return [], True
                if response.status_code == 200:
                    return response.json(), False
                logging.warning(
                    "API returned HTTP %d for page %d (attempt %d/%d)",
                    response.status_code, page, attempt + 1, self._API_MAX_RETRIES
                )
            except requests.RequestException as e:
                logging.error(
                    "GitHub API error (attempt %d/%d): %s",
                    attempt + 1, self._API_MAX_RETRIES, e
                )
            if attempt < self._API_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
        return [], True

    def get_user_repos_from_api(self):
        """Fetch the list of repositories for the configured GitHub user via REST API.

        Handles pagination (100 per page) and rate-limit errors.
        Implements retry with backoff on transient failures.
        Results are cached for ``_cache_ttl`` seconds.
        Returns a list of dicts as returned by the GitHub API.
        """
        if not self.github_token or not self.github_username:
            return []
        now = time.time()
        if self._repos_cache is not None and (now - self._cache_ts) < self._cache_ttl:
            logging.debug("Using cached repository list (%d entries)", len(self._repos_cache))
            return self._repos_cache
        repos = []
        page = 1
        while True:
            data, stop = self._fetch_page(
                {'page': page, 'per_page': 100, 'type': 'all'}, page
            )
            if stop:
                break
            if not data:
                break
            repos.extend(data)
            page += 1
        self._repos_cache = repos
        self._cache_ts = now
        logging.info("Found %d repositories via GitHub API", len(repos))
        return repos

    def _aggregate_stats(self, results):
        """Accumulate results from all repos into a stats dict."""
        stats = {'cloned': 0, 'updated': 0, 'up_to_date': 0, 'skipped': 0, 'errors': 0}
        for repo_info, result in results:
            if result['status'] == 'cloned':
                stats['cloned'] += 1
                logging.info("  Cloned: %s (all branches)", repo_info['name'])
            elif result['status'] == 'updated':
                stats['updated'] += 1
                logging.info("  Updated: %s - %s", repo_info['name'], result['message'])
            elif result['status'] == 'up_to_date':
                stats['up_to_date'] += 1
                logging.info("  Up to date: %s", repo_info['name'])
            elif result['status'] == 'skipped':
                stats['skipped'] += 1
            else:
                stats['errors'] += 1
                logging.error("  Error: %s - %s", repo_info['name'], result['message'])
        return stats

    def run(self):
        """Execute one full sync cycle: list repos, clone/update each, print summary."""
        logging.info("=" * 50)
        logging.info("Starting sync at %s", datetime.now().isoformat())
        if not self.github_token or not self.github_username:
            logging.error("GITHUB_TOKEN and GITHUB_USERNAME must be set!")
            return
        repos_from_api = self.get_user_repos_from_api()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.parallel_workers
        ) as pool:
            futures = {
                pool.submit(self.clone_full_repo, repo_info): repo_info
                for repo_info in repos_from_api
            }
            results = []
            for future in concurrent.futures.as_completed(futures):
                repo_info = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {'status': 'error', 'message': str(e)}
                results.append((repo_info, result))
        stats = self._aggregate_stats(results)
        logging.info(
            "Summary - Cloned: %d, Updated: %d, Up to date: %d, "
            "Skipped: %d, Errors: %d",
            stats['cloned'], stats['updated'], stats['up_to_date'],
            stats['skipped'], stats['errors']
        )


def main():
    """Entry point: read env, create puller, loop forever."""
    signal.signal(signal.SIGTERM, GitHubRepoPuller.handle_signal)
    signal.signal(signal.SIGINT, GitHubRepoPuller.handle_signal)

    git_dir = os.environ.get('GIT_DIR')
    interval = int(os.environ.get('PULL_INTERVAL', 3600))
    exclude_patterns = os.environ.get('EXCLUDE_PATTERNS')
    parallel_workers = os.environ.get('PARALLEL_WORKERS')
    kwargs: dict = {}
    if exclude_patterns is not None:
        kwargs['exclude_patterns'] = exclude_patterns
    if parallel_workers is not None:
        kwargs['parallel_workers'] = int(parallel_workers)
    puller = GitHubRepoPuller(git_dir, **kwargs)
    while not GitHubRepoPuller.shutdown_requested:
        try:
            puller.run()
            if GitHubRepoPuller.shutdown_requested:
                break
            logging.info("Waiting %d seconds until next run...", interval)
            for _ in range(interval):
                if GitHubRepoPuller.shutdown_requested:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Shutting down...")
            break
        except Exception as e:
            if GitHubRepoPuller.shutdown_requested:
                logging.info("Shutting down after signal...")
                break
            logging.error("Unexpected error: %s", e)
            time.sleep(interval)
    logging.info("Shutdown complete.")


if __name__ == '__main__':
    main()
