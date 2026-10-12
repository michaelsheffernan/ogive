"""Repo hygiene: secrets, environments and build output stay out of git."""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path", [".env", ".venv/", "src/ogive/__pycache__/", "out/", "src/ogive.egg-info/"]
)
def test_gitignore_excludes(path):
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO)
    assert result.returncode == 0, f"{path} is not gitignored"
