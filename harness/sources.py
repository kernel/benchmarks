"""Fetch an exact source revision for a generated task image."""

import shutil
import subprocess
from pathlib import Path


def fetch_source(repository: str, ref: str, dest: Path) -> str:
    dest.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(dest), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("fetch", "-q", "--depth", "1", repository, ref)
    git("checkout", "-q", "FETCH_HEAD")
    sha = git("rev-parse", "HEAD")
    shutil.rmtree(dest / ".git")
    (dest / "SOURCE_SHA").write_text(sha + "\n")
    return sha
