"""Resolve each task's browser settings from an arm's [browser] section.

A static `config` is written as-is. A `configurator` is an executable in
configurators/<name>/ that reads the task on stdin and prints JSON:

    {"browser": {...}, "steering": "markdown" | null, "provenance": {...}}

`browser` is merged over the static config. `steering` is appended to the
task instruction. Configurators only see the task config and instruction,
never the verifier's files.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from harness.definitions import ROOT, Browser
from harness.environment import CONFIG_FILE
from harness.tasks import append_instruction, instructions

STEERING_HEADING = "## Site guidance"


def configure_tasks(task_dirs: list[Path], browser: Browser) -> dict[str, Any]:
    """Configure every task and return each task's resolved configuration."""
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda task_dir: configure(task_dir, browser), task_dirs))
    return {task_dir.name: result for task_dir, result in zip(task_dirs, results, strict=True)}


def configure(task_dir: Path, browser: Browser) -> dict[str, Any]:
    config = dict(browser.config)
    steering = None
    provenance = None
    if browser.configurator:
        output = run_configurator(browser.configurator, task_dir, browser.configurator_args)
        config.update(output.get("browser") or {})
        steering = output.get("steering")
        provenance = output.get("provenance")
    # Harbor's Kernel environment always runs headful browsers.
    config.pop("headless", None)

    (task_dir / "environment" / CONFIG_FILE).write_text(json.dumps(config, indent=2))
    if steering:
        append_instruction(task_dir, f"{STEERING_HEADING}\n\n{steering}")
    return {"browser": config, "steering": steering, "provenance": provenance}


def run_configurator(name: str, task_dir: Path, args: dict[str, Any]) -> dict[str, Any]:
    script = ROOT / "configurators" / name / "configure"
    payload = {
        "task": {
            "config": (task_dir / "task.toml").read_text(),
            "instruction": "\n\n".join(path.read_text() for path in instructions(task_dir)),
        },
        "args": args,
    }
    # Run scripts with this project's Python first on PATH so they can use its packages.
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        [script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"configurator {name} failed for {task_dir.name}: {result.stderr.strip()[-2000:]}"
        )
    output = json.loads(result.stdout)
    if not isinstance(output, dict) or not isinstance(output.get("browser") or {}, dict):
        raise RuntimeError(f"configurator {name} returned invalid output for {task_dir.name}")
    return output
