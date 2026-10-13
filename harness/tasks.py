"""Helpers for editing generated Harbor task directories."""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit.toml_document import TOMLDocument


def read_config(task_dir: Path) -> TOMLDocument:
    return tomlkit.parse((task_dir / "task.toml").read_text())


def write_config(task_dir: Path, config: TOMLDocument) -> None:
    (task_dir / "task.toml").write_text(tomlkit.dumps(config))


def instructions(task_dir: Path) -> list[Path]:
    """Instruction files for a single-step or multi-step task."""
    return sorted([*task_dir.glob("instruction.md"), *task_dir.glob("steps/*/instruction.md")])


def test_scripts(task_dir: Path) -> list[Path]:
    return sorted([*task_dir.glob("tests/test.sh"), *task_dir.glob("steps/*/tests/test.sh")])


def append_instruction(task_dir: Path, text: str) -> None:
    for path in instructions(task_dir):
        path.write_text(path.read_text().rstrip() + "\n\n" + text.strip() + "\n")


def append_test_command(task_dir: Path, command: str) -> None:
    for path in test_scripts(task_dir):
        path.write_text(path.read_text().rstrip() + "\n" + command + "\n")
