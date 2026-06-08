import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gitgrab import GitHubRepoPuller


@pytest.fixture(autouse=True)
def reset_class_state():
    GitHubRepoPuller.shutdown_requested = False
    yield
    GitHubRepoPuller.shutdown_requested = False
