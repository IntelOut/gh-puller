"""Tests for gh-puller."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pull_repos import GitHubRepoPuller


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
