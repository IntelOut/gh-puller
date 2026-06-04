"""Tests for gh-puller."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from pull_repos import CredentialFilter, GitHubRepoPuller, _error_detail


@pytest.fixture
def puller():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['GITHUB_TOKEN'] = 'test-token'
        os.environ['GITHUB_USERNAME'] = 'test-user'
        yield GitHubRepoPuller(git_dir=tmp)
    os.environ.pop('GITHUB_TOKEN', None)
    os.environ.pop('GITHUB_USERNAME', None)


class TestGitHubRepoPuller:
    def test_init_expands_home(self):
        puller = GitHubRepoPuller(git_dir='~/myrepos', github_token='t', parallel_workers=2)
        assert 'myrepos' in str(puller.git_dir)
        assert puller.git_dir.is_absolute()

    def test_init_defaults(self, puller):
        assert puller.github_token == 'test-token'
        assert puller.github_username == 'test-user'
        assert puller.git_dir.exists()
        assert puller.parallel_workers == 4
        assert puller.exclude_patterns == []

    def test_init_exclude_patterns(self):
        puller = GitHubRepoPuller(
            git_dir='/tmp', github_token='t', exclude_patterns=r'\.archive$,\.deprecated$'
        )
        assert len(puller.exclude_patterns) == 2

    def test_is_excluded(self, puller):
        puller.exclude_patterns = [__import__('re').compile(r'\.archive$')]
        assert puller._is_excluded('myrepo.archive')
        assert not puller._is_excluded('myrepo')

    def test_check_disk_space_ok(self, puller):
        puller._check_disk_space(min_gb=0)
        assert True

    def test_check_disk_space_fail(self, puller):
        with pytest.raises(OSError, match='GB free'):
            puller._check_disk_space(min_gb=1e9)

    @patch('pull_repos.subprocess.run')
    def test_run_returns_stdout(self, mock_run, puller):
        mock_run.return_value = MagicMock(stdout='main\n', returncode=0)
        result = puller._run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
        assert result.stdout == 'main\n'

    def test_get_user_repos_from_api_no_token(self):
        puller = GitHubRepoPuller(git_dir='/tmp', github_token=None)
        assert puller.get_user_repos_from_api() == []

    def test_get_user_repos_from_api_no_username(self):
        puller = GitHubRepoPuller(git_dir='/tmp', github_token='t')
        assert puller.get_user_repos_from_api() == []

    @patch('pull_repos.requests.get')
    def test_fetch_page_200(self, mock_get, puller):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{'name': 'repo1'}, {'name': 'repo2'}],
            headers={}
        )
        data, stop = puller._fetch_page({'page': 1, 'per_page': 100, 'type': 'all'}, 1)
        assert not stop
        assert len(data) == 2

    @patch('pull_repos.requests.get')
    def test_fetch_page_401(self, mock_get, puller):
        mock_get.return_value = MagicMock(status_code=401, headers={})
        data, stop = puller._fetch_page({'page': 1, 'per_page': 100, 'type': 'all'}, 1)
        assert stop
        assert data == []

    @patch('pull_repos.requests.get')
    def test_fetch_page_403(self, mock_get, puller):
        mock_get.return_value = MagicMock(
            status_code=403, headers={'X-RateLimit-Remaining': '0'}
        )
        data, stop = puller._fetch_page({'page': 1, 'per_page': 100, 'type': 'all'}, 1)
        assert stop
        assert data == []

    def test_get_default_branch_fallback(self, puller):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'test-repo'
            repo.mkdir()
            result = puller._get_default_branch(repo)
            assert result == 'main'

    def test_cache_hit(self, puller):
        puller._repos_cache = [{'name': 'cached'}]
        puller._cache_ts = __import__('time').time()
        result = puller.get_user_repos_from_api()
        assert result == [{'name': 'cached'}]


class TestModLevel:
    def test_error_detail_with_stderr(self):
        exc = subprocess.CalledProcessError(1, ['git'], stderr='fatal: repository not found\n')
        assert _error_detail(exc) == 'fatal: repository not found'

    def test_error_detail_without_stderr(self):
        exc = RuntimeError('something went wrong')
        assert 'something went wrong' in _error_detail(exc)

    def test_error_detail_oserror(self):
        exc = OSError(13, 'Permission denied')
        assert _error_detail(exc) == str(exc)


class TestGitRemoteCmd:
    def test_with_token(self, puller):
        cmd = puller._git_remote_cmd('fetch', 'origin')
        assert 'git' in cmd
        assert '-c' in cmd
        auth_idx = cmd.index('-c') + 1
        assert 'http.extraHeader=Authorization: Bearer test-token' == cmd[auth_idx]
        assert cmd[-2:] == ['fetch', 'origin']

    def test_without_token(self):
        puller = GitHubRepoPuller(git_dir='/tmp', github_token=None)
        cmd = puller._git_remote_cmd('fetch', 'origin')
        assert cmd == ['git', 'fetch', 'origin']


class TestRunExtraEnv:
    @patch('pull_repos.subprocess.run')
    def test_extra_env_overrides(self, mock_run, puller):
        mock_run.return_value = MagicMock(stdout='', returncode=0)
        puller._run(['git', 'status'], extra_env={'GIT_TERMINAL_PROMPT': '1'})
        env_passed = mock_run.call_args.kwargs['env']
        assert env_passed['GIT_TERMINAL_PROMPT'] == '1'

    @patch('pull_repos.subprocess.run')
    def test_base_env_present(self, mock_run, puller):
        mock_run.return_value = MagicMock(stdout='', returncode=0)
        puller._run(['git', 'status'])
        env_passed = mock_run.call_args.kwargs['env']
        assert env_passed['GIT_TERMINAL_PROMPT'] == '0'

    @patch('pull_repos.subprocess.run')
    def test_run_passes_check(self, mock_run, puller):
        mock_run.return_value = MagicMock(stdout='', returncode=0)
        puller._run(['git', 'status'], check=False)
        assert mock_run.call_args.kwargs['check'] is False

    @patch('pull_repos.subprocess.run')
    def test_git_dir_removed(self, mock_run, puller):
        mock_run.return_value = MagicMock(stdout='', returncode=0)
        import os
        os.environ['GIT_DIR'] = '/some/path'
        try:
            puller._run(['git', 'status'])
            env_passed = mock_run.call_args.kwargs['env']
            assert 'GIT_DIR' not in env_passed
        finally:
            os.environ.pop('GIT_DIR', None)


class TestGetRemoteUrl:
    @patch('pull_repos.subprocess.run')
    def test_returns_url(self, mock_run, puller):
        mock_run.return_value = MagicMock(stdout='https://github.com/user/repo.git\n', returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            result = puller._get_remote_url(Path(tmp))
        assert result == 'https://github.com/user/repo.git'

    @patch('pull_repos.subprocess.run')
    def test_no_origin(self, mock_run, puller):
        mock_run.side_effect = subprocess.CalledProcessError(128, ['git'])
        with tempfile.TemporaryDirectory() as tmp:
            result = puller._get_remote_url(Path(tmp))
        assert result is None


class TestGetDefaultBranch:
    def test_symbolic_ref_success(self, puller):
        with (
            patch.object(GitHubRepoPuller, '_run') as mock_run,
            tempfile.TemporaryDirectory() as tmp,
        ):
            mock_run.return_value = MagicMock(stdout='refs/remotes/origin/main\n', returncode=0)
            result = puller._get_default_branch(Path(tmp))
        assert result == 'main'

    def test_symbolic_ref_fallback_master(self, puller):
        with (
            patch.object(GitHubRepoPuller, '_run') as mock_run,
            tempfile.TemporaryDirectory() as tmp,
        ):
            def side_effect(cmd, **kwargs):
                if 'symbolic-ref' in cmd:
                    return MagicMock(stdout='', returncode=1)
                if 'show-ref' in cmd and cmd[-1].endswith('/master'):
                    return MagicMock(stdout='abc123 refs/heads/master', returncode=0)
                return MagicMock(stdout='', returncode=1)
            mock_run.side_effect = side_effect
            result = puller._get_default_branch(Path(tmp))
        assert result == 'master'

    def test_symbolic_ref_exception(self, puller):
        with (
            patch.object(GitHubRepoPuller, '_run') as mock_run,
            tempfile.TemporaryDirectory() as tmp,
        ):
            calls = []
            def side_effect(cmd, **kwargs):
                calls.append(cmd)
                if len(calls) == 1:
                    raise RuntimeError('unexpected')
                return MagicMock(stdout='', returncode=1)
            mock_run.side_effect = side_effect
            result = puller._get_default_branch(Path(tmp))
        assert result == 'main'


class TestIterRemoteBranches:
    @patch.object(GitHubRepoPuller, '_run')
    def test_yields_tracking_branches(self, mock_run, puller):
        mock_run.return_value = MagicMock(stdout=(
            '  origin/HEAD -> origin/main\n'
            '  origin/main\n'
            '  origin/develop\n'
            '  origin/feature/new-stuff\n'
        ), returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            branches = list(puller._iter_remote_branches(Path(tmp)))
        assert branches == [
            ('main', 'origin/main'),
            ('develop', 'origin/develop'),
            ('feature/new-stuff', 'origin/feature/new-stuff'),
        ]

    @patch.object(GitHubRepoPuller, '_run')
    def test_empty_output(self, mock_run, puller):
        mock_run.return_value = MagicMock(stdout='', returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            branches = list(puller._iter_remote_branches(Path(tmp)))
        assert branches == []


class TestCreateLocalTrackingBranches:
    @patch.object(GitHubRepoPuller, '_run')
    def test_creates_missing_branches(self, mock_run, puller):
        show_ref_results = {
            'refs/heads/main': MagicMock(stdout='', returncode=0),
            'refs/heads/develop': MagicMock(stdout='', returncode=1),
            'refs/heads/feature/new-stuff': MagicMock(stdout='', returncode=1),
        }
        def side_effect(cmd, **kwargs):
            if 'branch' in cmd and '-r' in cmd:
                return MagicMock(stdout=(
                    '  origin/main\n  origin/develop\n  origin/feature/new-stuff\n'
                ), returncode=0)
            if 'show-ref' in cmd:
                ref = cmd[-1]
                return show_ref_results.get(ref, MagicMock(stdout='', returncode=1))
            return MagicMock(stdout='', returncode=0)
        mock_run.side_effect = side_effect
        with tempfile.TemporaryDirectory() as tmp:
            puller._create_local_tracking_branches(Path(tmp))
        branch_calls = [
            c for c in mock_run.call_args_list
            if 'branch' in c[0][0] and '--track' in c[0][0]
        ]
        assert len(branch_calls) == 2
        assert 'develop' in branch_calls[0][0][0]
        assert 'feature/new-stuff' in branch_calls[1][0][0]


class TestCloneFullRepo:
    def test_skipped(self, puller):
        puller.exclude_patterns = [__import__('re').compile(r'^skip-')]
        result = puller.clone_full_repo({'name': 'skip-me', 'clone_url': 'https://x.com/x.git'})
        assert result['status'] == 'skipped'

    @patch.object(GitHubRepoPuller, '_clone_with_all_branches')
    @patch.object(GitHubRepoPuller, 'log_all_branches')
    def test_clone_success(self, mock_log, mock_clone, puller):
        mock_clone.return_value = None
        result = puller.clone_full_repo({'name': 'new-repo', 'clone_url': 'https://x.com/x.git'})
        assert result['status'] == 'cloned'

    @patch.object(GitHubRepoPuller, '_clone_with_all_branches')
    @patch.object(GitHubRepoPuller, '_clone_fallback')
    @patch.object(GitHubRepoPuller, 'log_all_branches')
    def test_clone_fallback_success(self, mock_log, mock_fallback, mock_clone, puller):
        mock_clone.side_effect = subprocess.CalledProcessError(
            128, ['git', 'init'], stderr='permission denied\n'
        )
        mock_fallback.return_value = None
        result = puller.clone_full_repo({'name': 'new-repo', 'clone_url': 'https://x.com/x.git'})
        assert result['status'] == 'cloned'
        assert '(fallback)' in result['message']

    @patch.object(GitHubRepoPuller, '_clone_with_all_branches')
    @patch.object(GitHubRepoPuller, '_clone_fallback')
    def test_clone_both_fail(self, mock_fallback, mock_clone, puller):
        mock_clone.side_effect = subprocess.CalledProcessError(128, ['git', 'init'])
        mock_fallback.side_effect = subprocess.CalledProcessError(
            1, ['git', 'clone'], stderr='auth failed\n'
        )
        result = puller.clone_full_repo({'name': 'new-repo', 'clone_url': 'https://x.com/x.git'})
        assert result['status'] == 'error'

    @patch.object(GitHubRepoPuller, '_update')
    def test_existing_repo(self, mock_update, puller):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / 'existing-repo'
            repo_path.mkdir()
            puller.git_dir = Path(tmp)
            mock_update.return_value = {'status': 'updated', 'message': 'Updated branches: main'}
            result = puller.clone_full_repo({'name': 'existing-repo', 'clone_url': 'https://x.com/x.git'})
            assert result['status'] == 'updated'


class TestUpdate:
    @patch.object(GitHubRepoPuller, '_get_remote_url')
    def test_no_remote(self, mock_get_url, puller):
        mock_get_url.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            result = puller._update(Path(tmp) / 'repo', 'test-repo')
        assert result['status'] == 'error'
        assert 'No remote origin' in result['message']

    @patch.object(GitHubRepoPuller, '_update_all_branches')
    @patch.object(GitHubRepoPuller, '_get_remote_url')
    def test_updated(self, mock_get_url, mock_update, puller):
        mock_get_url.return_value = 'https://github.com/user/repo.git'
        mock_update.return_value = ['main', 'develop']
        with tempfile.TemporaryDirectory() as tmp:
            result = puller._update(Path(tmp) / 'repo', 'test-repo')
        assert result['status'] == 'updated'
        assert 'main, develop' in result['message']

    @patch.object(GitHubRepoPuller, '_update_all_branches')
    @patch.object(GitHubRepoPuller, '_get_remote_url')
    def test_up_to_date(self, mock_get_url, mock_update, puller):
        mock_get_url.return_value = 'https://github.com/user/repo.git'
        mock_update.return_value = []
        with tempfile.TemporaryDirectory() as tmp:
            result = puller._update(Path(tmp) / 'repo', 'test-repo')
        assert result['status'] == 'up_to_date'

    @patch.object(GitHubRepoPuller, '_update_all_branches')
    @patch.object(GitHubRepoPuller, '_get_remote_url')
    def test_calledprocesserror(self, mock_get_url, mock_update, puller):
        mock_get_url.return_value = 'https://github.com/user/repo.git'
        mock_update.side_effect = subprocess.CalledProcessError(
            128, ['git'], stderr='fetch failed\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = puller._update(Path(tmp) / 'repo', 'test-repo')
        assert result['status'] == 'error'

    @patch.object(GitHubRepoPuller, '_update_all_branches')
    @patch.object(GitHubRepoPuller, '_get_remote_url')
    def test_generic_exception(self, mock_get_url, mock_update, puller):
        mock_get_url.return_value = 'https://github.com/user/repo.git'
        mock_update.side_effect = RuntimeError('disk full')
        with tempfile.TemporaryDirectory() as tmp:
            result = puller._update(Path(tmp) / 'repo', 'test-repo')
        assert result['status'] == 'error'


class TestAggregateStats:
    def test_all_statuses(self):
        puller = GitHubRepoPuller(git_dir='/tmp', github_token='t')
        results = [
            ({'name': 'r1'}, {'status': 'cloned'}),
            ({'name': 'r2'}, {'status': 'updated', 'message': 'main'}),
            ({'name': 'r3'}, {'status': 'up_to_date'}),
            ({'name': 'r4'}, {'status': 'skipped'}),
            ({'name': 'r5'}, {'status': 'error', 'message': 'fail'}),
            ({'name': 'r6'}, {'status': 'error', 'message': 'fail2'}),
        ]
        stats = puller._aggregate_stats(results)
        assert stats == {'cloned': 1, 'updated': 1, 'up_to_date': 1, 'skipped': 1, 'errors': 2}


class TestFetchPage:
    @patch('pull_repos.requests.get')
    def test_transient_http_error_retry(self, mock_get, puller):
        mock_get.return_value = MagicMock(status_code=500, headers={}, json=lambda: [])
        data, stop = puller._fetch_page({'page': 1}, 1)
        assert stop
        assert mock_get.call_count == 3

    @patch('pull_repos.requests.get')
    def test_request_exception_retry(self, mock_get, puller):
        mock_get.side_effect = requests.RequestException('connection refused')
        data, stop = puller._fetch_page({'page': 1}, 1)
        assert stop
        assert mock_get.call_count == 3


class TestAuthUrl:
    def test_auth_url_with_token(self, puller):
        result = puller._auth_url('https://github.com/user/repo.git')
        assert result == 'https://x-access-token:test-token@github.com/user/repo.git'

    def test_auth_url_no_token(self):
        puller = GitHubRepoPuller(git_dir='/tmp', github_token=None)
        result = puller._auth_url('https://github.com/user/repo.git')
        assert result == 'https://github.com/user/repo.git'

    def test_clean_url_with_token(self, puller):
        dirty = 'https://x-access-token:test-token@github.com/user/repo.git'
        result = puller._clean_url(dirty)
        assert result == 'https://github.com/user/repo.git'

    def test_clean_url_no_token(self):
        puller = GitHubRepoPuller(git_dir='/tmp', github_token=None)
        result = puller._clean_url('https://github.com/user/repo.git')
        assert result == 'https://github.com/user/repo.git'


class TestCredentialFilter:
    def test_redact_keeps_surrounding_quotes(self):
        log_message = (
            "Command '['git', '-c', 'http.extraHeader=Authorization: "
            "Bearer ghp_test12345', 'clone', '--mirror', 'url']'"
        )
        record = logging.LogRecord(
            name='test', level=logging.ERROR,
            pathname='', lineno=0, msg=log_message, args=(), exc_info=None
        )
        filtr = CredentialFilter()
        filtr.filter(record)
        assert "***REDACTED***" in record.msg
        assert "***REDACTED***'" in record.msg
        assert "'clone'" in record.msg
        assert "Bearer ***REDACTED***" in record.msg

    def test_redact_plain_string(self):
        record = logging.LogRecord(
            name='test', level=logging.ERROR,
            pathname='', lineno=0,
            msg='Authorization: Bearer my-secret-token in the log',
            args=(), exc_info=None
        )
        filtr = CredentialFilter()
        filtr.filter(record)
        assert '***REDACTED***' in record.msg
        assert 'my-secret-token' not in record.msg

    def test_redact_end_of_string(self):
        record = logging.LogRecord(
            name='test', level=logging.ERROR,
            pathname='', lineno=0,
            msg='Token is Authorization: Bearer ghp_abc123',
            args=(), exc_info=None
        )
        filtr = CredentialFilter()
        filtr.filter(record)
        assert '***REDACTED***' in record.msg
        assert 'ghp_abc123' not in record.msg

    def test_redact_url_token(self):
        record = logging.LogRecord(
            name='test', level=logging.ERROR,
            pathname='', lineno=0,
            msg="clone --mirror 'https://x-access-token:ghp_secret123@github.com/user/repo.git'",
            args=(), exc_info=None
        )
        filtr = CredentialFilter()
        filtr.filter(record)
        assert '***REDACTED***' in record.msg
        assert 'ghp_secret123' not in record.msg
        assert 'x-access-token:***REDACTED***@' in record.msg

    def test_no_match_passes_through(self):
        msg = 'Just a normal log message without any token'
        record = logging.LogRecord(
            name='test', level=logging.INFO,
            pathname='', lineno=0, msg=msg, args=(), exc_info=None
        )
        filtr = CredentialFilter()
        filtr.filter(record)
        assert record.msg == msg
