"""Healthcheck script for the GitGrab Docker container.

Checks whether PID 1 (the docker entrypoint) is still running
by inspecting ``/proc/1/cmdline`` for ``gitgrab.py``.
Exits with code 0 (healthy) if found, 1 (unhealthy) otherwise.
"""

import os
import sys


def _check_entrypoint():
    """Return True if PID 1 cmdline contains 'gitgrab.py'."""
    cmdline_path = '/proc/1/cmdline'
    if not os.path.isfile(cmdline_path):
        return False
    with open(cmdline_path, 'rb') as f:
        cmdline = f.read()
    return b'gitgrab.py' in cmdline


def main():
    """Healthcheck entry point. Exit 0 if healthy, 1 otherwise."""
    sys.exit(0 if _check_entrypoint() else 1)


if __name__ == '__main__':
    main()
