"""Tests for healthcheck.py."""

from unittest.mock import mock_open, patch

import pytest

from healthcheck import _check_entrypoint


class TestCheckEntrypoint:
    def test_entrypoint_running(self):
        with (
            patch('healthcheck.os.path.isfile', return_value=True),
            patch('builtins.open', mock_open(read_data=b'/usr/bin/python3 gitgrab.py')),
        ):
            assert _check_entrypoint() is True

    def test_entrypoint_not_gitgrab(self):
        with (
            patch('healthcheck.os.path.isfile', return_value=True),
            patch('builtins.open', mock_open(read_data=b'/usr/bin/python3 other.py')),
        ):
            assert _check_entrypoint() is False

    def test_no_proc_file(self):
        with (
            patch('healthcheck.os.path.isfile', return_value=False),
        ):
            assert _check_entrypoint() is False

    def test_main_exit_0(self):
        with patch('healthcheck._check_entrypoint', return_value=True):
            with pytest.raises(SystemExit) as exc:
                from healthcheck import main
                main()
            assert exc.value.code == 0

    def test_main_exit_1(self):
        with patch('healthcheck._check_entrypoint', return_value=False):
            with pytest.raises(SystemExit) as exc:
                from healthcheck import main
                main()
            assert exc.value.code == 1
