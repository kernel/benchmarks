from pathlib import Path

import pytest


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    """A minimal multi-step Harbor task."""
    task = tmp_path / "example-task"
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text("FROM debian:bookworm-slim\n")
    (task / "steps" / "run" / "tests").mkdir(parents=True)
    (task / "steps" / "run" / "instruction.md").write_text("Book a table.\n")
    (task / "steps" / "run" / "tests" / "test.sh").write_text(
        "#!/bin/bash\necho 1 >/logs/verifier/reward.txt\n"
    )
    (task / "steps" / "run" / "tests" / "answer.json").write_text('{"secret": "expected request"}')
    (task / "task.toml").write_text(
        '[task]\nname = "example"\n\n[environment]\n\n[environment.env]\n'
    )
    return task
