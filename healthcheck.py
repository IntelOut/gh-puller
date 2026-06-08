"""Healthcheck script for the GitGrab Docker container.

Checks whether the parent process (the docker entrypoint) is still running
by inspecting ``/proc/<ppid>/cmdline`` for ``gitgrab.py``.
Exits with code 0 (healthy) if found, 1 (unhealthy) otherwise.
"""

import os
import sys


def _check_parent():
    """Return True if the parent process cmdline contains 'gitgrab.py'."""
    ppid = os.getppid()
    cmdline_path = f'/proc/{ppid}/cmdline'
    if not os.path.isfile(cmdline_path):
        return False
    with open(cmdline_path, 'rb') as f:
        cmdline = f.read()
    return b'gitgrab.py' in cmdline


def main():
    """Healthcheck entry point. Exit 0 if healthy, 1 otherwise."""
    sys.exit(0 if _check_parent() else 1)


if __name__ == '__main__':
    main()
