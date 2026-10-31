"""Guest add-to-cart tasks on retail and industrial-supply sites with heavy bot protection.

Every task starts on a site's home page, opened before the agent starts, and
asks the agent to search for one item, open its product page, add it to the
cart without logging in, and report the cart. The flow crosses the pages
where bot protection usually acts: the first request, search, the product
page and the cart.

The agent ends each task by calling the `finalize` tool of the `bench` MCP
server with its evidence, its reasoning and an outcome. The verifier turns
that report into the reward and records the outcome, so a bot block is
counted apart from other failures. The browser's final URL and title are
saved for checking reported successes.
"""

from __future__ import annotations

import json
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomlkit

HERE = Path(__file__).parent
IMAGE = HERE / "image"

INSTRUCTION = """Add {item} to the {site} cart, then open the cart. Search for it from the site's search box, as a shopper would, open its product page, and add one to the cart. Report the cart line for it with its quantity and price, and the cart subtotal.

## Rules

- The browser is already open on {start_url}. Work on that site only.
- Do not log in, sign up, or enter personal or payment details. Continue as a guest.
- Never check out, pay, or place an order. Stop at the cart.
- If the site shows a bot check, CAPTCHA or press-and-hold challenge, try to get past it as a person would. If you cannot, or the site denies access, report the task as blocked.

## Finishing

When the task is done, or you are certain it cannot be done, call the `bench` MCP server's `finalize` tool exactly once, then stop. Your `finalize` report is the result of the task: put everything the task asks you to report in `evidence`.
"""


@dataclass(frozen=True)
class Task:
    id: str
    site: str
    start_url: str
    item: str
    timeout_sec: int = 900


def preflight() -> None:
    """Nothing to check: tasks need only the Kernel browser Harbor creates."""


def load_tasks() -> list[Task]:
    return [Task(**entry) for entry in tomllib.loads((HERE / "tasks.toml").read_text())["tasks"]]


def generate(task_ids: list[str] | None, out_dir: Path) -> list[Path]:
    tasks = load_tasks()
    if task_ids:
        known = {task.id: task for task in tasks}
        missing = [task_id for task_id in task_ids if task_id not in known]
        if missing:
            raise SystemExit(f"Unknown tasks: {', '.join(missing)}")
        tasks = [known[task_id] for task_id in task_ids]
    out_dir.mkdir(parents=True, exist_ok=True)
    return [_write(task, out_dir / task.id) for task in tasks]


def _write(task: Task, task_dir: Path) -> Path:
    if task_dir.exists():
        shutil.rmtree(task_dir)
    environment = task_dir / "environment"
    shutil.copytree(IMAGE, environment)
    (environment / "bench" / "task.json").write_text(json.dumps({"id": task.id, "site": task.site}))
    step = task_dir / "steps" / "run"
    (step / "tests").mkdir(parents=True)
    (step / "workdir").mkdir()

    (step / "instruction.md").write_text(
        INSTRUCTION.format(item=task.item, site=task.site, start_url=task.start_url)
    )
    (step / "workdir" / "setup.sh").write_text(
        f"#!/bin/bash\nset -euo pipefail\n/opt/bench/open-start-url {json.dumps(task.start_url)}\n"
    )
    (step / "tests" / "test.sh").write_text("#!/bin/bash\nset -euo pipefail\n/opt/bench/score\n")

    config = tomlkit.document()
    config["schema_version"] = "1.3"
    config["source"] = "retail-cart"
    config["task"] = {"name": f"retail-cart/{task.id}", "keywords": [task.site]}
    config["metadata"] = {"site": task.site, "start_url": task.start_url}
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
