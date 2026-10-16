"""Everyday tasks on the sites kernel/xwebmcp has tools for, self-reported.

Each site in `tasks/<site>.toml` has four tasks split train, train,
validation and test. Benchmarks run the test split, and only the test split,
to compare arms. Tool authors improve tools against the train tasks and check
a change on the validation tasks before it ships, so the test score stays
unbiased.

The agent ends each task by calling the `finalize` tool of the `bench` MCP
server with its evidence, its reasoning and whether it succeeded. The
verifier turns that report into the reward; no judge checks it.

Every task starts on the site's start URL, opened before the agent starts,
and records the site and split in `[metadata]` for toolsets and reports.

Args:
    split: train, validation, test or all. Task IDs passed explicitly are
        generated whatever their split.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import tomlkit

HERE = Path(__file__).parent
IMAGE = HERE / "image"
SPLITS = ("train", "validation", "test")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

INSTRUCTION = """{instruction}

## Rules

- The browser is already open on {start_url}. Work on that site only.
- Never pay, place an order, or complete a booking or reservation. Stop before any button that submits a payment, order, booking or reservation.
- Do not add anything to a cart unless the task asks you to, and then go no further than the task asks.
- Do not log in, sign up, or enter personal or payment details.
- Do not post, review, send messages, follow, favorite, or change anything else on the site.

## Finishing

When the task is done, or you are certain it cannot be done, call the `bench` MCP server's `finalize` tool exactly once, then stop. Your `finalize` report is the result of the task: put everything the task asks you to report in `evidence`.
"""


@dataclass(frozen=True)
class Task:
    id: str
    site: str
    split: str
    start_url: str
    instruction: str
    fixture: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    timeout_sec: int = 900


def preflight() -> None:
    """Nothing to check: tasks need only the Kernel browser Harbor creates."""


def load_tasks() -> list[Task]:
    tasks = []
    for path in sorted((HERE / "tasks").glob("*.toml")):
        data = tomllib.loads(path.read_text())
        for entry in data["tasks"]:
            tasks.append(
                Task(
                    site=data["site"], start_url=entry.pop("start_url", data["start_url"]), **entry
                )
            )
    return tasks


def generate(
    task_ids: list[str] | None, out_dir: Path, split: str = "test", today: date | None = None
) -> list[Path]:
    if split not in (*SPLITS, "all"):
        raise SystemExit(f"split must be one of {', '.join(SPLITS)} or all, got {split!r}")
    tasks = load_tasks()
    if task_ids:
        known = {task.id: task for task in tasks}
        missing = [task_id for task_id in task_ids if task_id not in known]
        if missing:
            raise SystemExit(f"Unknown tasks: {', '.join(missing)}")
        selected = [known[task_id] for task_id in task_ids]
    else:
        selected = [task for task in tasks if split in ("all", task.split)]
    today = today or datetime.now(UTC).date()
    out_dir.mkdir(parents=True, exist_ok=True)
    return [_write(task, out_dir / task.id, today) for task in selected]


def render(text: str, fixture: dict[str, Any], today: date) -> str:
    """Fill `{{name}}` from the fixture; `{days_from_start = n}` becomes a date n days from today."""

    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in fixture:
            raise SystemExit(f"No fixture value for {{{{{name}}}}}")
        item = fixture[name]
        if isinstance(item, dict):
            day = today + timedelta(days=item["days_from_start"])
            return f"{day:%A, %B} {day.day}, {day.year}"
        return str(item)

    return PLACEHOLDER.sub(value, text)


def _write(task: Task, task_dir: Path, today: date) -> Path:
    if task_dir.exists():
        shutil.rmtree(task_dir)
    environment = task_dir / "environment"
    shutil.copytree(IMAGE, environment)
    (environment / "bench" / "task.json").write_text(
        json.dumps({"id": task.id, "site": task.site, "split": task.split})
    )
    step = task_dir / "steps" / "run"
    (step / "tests").mkdir(parents=True)
    (step / "workdir").mkdir()

    instruction = render(task.instruction, task.fixture, today).strip()
    (step / "instruction.md").write_text(
        INSTRUCTION.format(instruction=instruction, start_url=task.start_url)
    )
    (step / "workdir" / "setup.sh").write_text(
        f"#!/bin/bash\nset -euo pipefail\n/opt/bench/open-start-url {json.dumps(task.start_url)}\n"
    )
    (step / "tests" / "test.sh").write_text("#!/bin/bash\nset -euo pipefail\n/opt/bench/score\n")

    config = tomlkit.document()
    config["schema_version"] = "1.3"
    config["source"] = "xwebmcp-tasks"
    config["task"] = {"name": f"xwebmcp-tasks/{task.id}", "keywords": [task.site, task.split]}
    config["metadata"] = {
        "site": task.site,
        "split": task.split,
        "start_url": task.start_url,
        "xwebmcp_actions": task.actions,
    }
    config["environment"] = {
        "build_timeout_sec": 1200.0,
        "network_mode": "public",
        "workdir": "/",
        "env": {"KERNEL_API_KEY": "${KERNEL_API_KEY}", "KERNEL_BASE_URL": "${KERNEL_BASE_URL:-}"},
    }
    servers = tomlkit.aot()
    servers.append(
        {"name": "bench", "transport": "stdio", "command": "/opt/bench/finalize-mcp", "args": []}
    )
    config["environment"]["mcp_servers"] = servers
    steps = tomlkit.aot()
    steps.append(
        {
            "name": "run",
            "agent": {"timeout_sec": float(task.timeout_sec)},
            "verifier": {"timeout_sec": 120.0},
        }
    )
    config["steps"] = steps
    (task_dir / "task.toml").write_text(tomlkit.dumps(config))
    return task_dir
