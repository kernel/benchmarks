"""Public signed-out tasks and grading from a pinned xwebmcp revision."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

import tomlkit

from harness.sources import fetch_source

HERE = Path(__file__).parent
GRADER_FILES = ["agent.mjs", "intents.mjs", "page.mjs", "schema.mjs", "tools.mjs"]
SAFETY = """## Rules

- Never pay, place an order, complete a booking or reservation, log in, or sign up.
- Only add to a cart or select a fare when this task explicitly asks you to. Stop at the requested page; do not enter checkout or personal/payment details.
- Do not post, comment, send messages, star, follow, or change unrelated site state.
- Use only the assigned browser and the browser tools listed below. Do not use shell commands, HTTP clients, another browser, or the agent's own search/fetch for browser or website interaction.
- Do not read the verifier, task fixtures or tool source files on disk. Use the website as your evidence.

## Finishing

Call the bench MCP server's finalize tool exactly once, then stop. Put the JSON answer the task requests in evidence, explain what you checked in reasoning, and choose the outcome. Do not report success unless you verified the task's requirements in the browser.
"""


def preflight() -> None:
    for command in ("git", "node"):
        if not shutil.which(command):
            raise SystemExit(f"public-web needs {command} (Node.js 22.13 or later)")


def generate(task_ids: list[str] | None, out_dir: Path) -> list[Path]:
    preflight()
    source_config = tomllib.loads((HERE / "source.toml").read_text())
    selected = list(dict.fromkeys(task_ids or source_config["tasks"]))
    unknown = set(selected) - set(source_config["tasks"])
    if unknown:
        raise SystemExit(f"Unknown public-web tasks: {', '.join(sorted(unknown))}")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "source"
        sha = fetch_source(source_config["repository"], source_config["revision"], source)
        if sha != source_config["revision"]:
            raise SystemExit("public-web source revision must be an exact commit SHA")
        materialized = Path(tmp) / "tasks"
        subprocess.run(
            [
                "node",
                str(HERE / "materialize.mjs"),
                str(source),
                str(materialized),
                json.dumps(selected),
            ],
            check=True,
        )
        return [_write(materialized / name, out_dir / name, source, sha) for name in selected]


def _write(task_source: Path, task_dir: Path, source: Path, sha: str) -> Path:
    task = json.loads((task_source / "task.json").read_text())
    intent = task["intent"]
    task["source_sha"] = sha
    if task_dir.exists():
        shutil.rmtree(task_dir)
    environment = task_dir / "environment"
    shutil.copytree(HERE / "image", environment)
    # The agent has a shell in this image, so it holds only what setup needs.
    start = {
        "intent": {key: intent.get(key) for key in ("id", "domain", "start_url", "entry_tool")},
        "registered": task["registered"],
        "grading": task["grading"],
        "source_sha": sha,
    }
    (environment / "bench/start.json").write_text(json.dumps(start, indent=2) + "\n")
    # Only the treatment toolset copies this batch into its task image.
    shutil.copy2(task_source / "tools.js", task_dir / "xwebmcp-tools.js")

    step = task_dir / "steps/run"
    (step / "workdir").mkdir(parents=True)
    # The answer rules and the grader go in the step's tests, which Harbor uploads to
    # /tests only once the agent has finished.
    tests = step / "tests"
    tests.mkdir()
    (tests / "task.json").write_text(json.dumps(task, indent=2) + "\n")
    shutil.copy2(HERE / "verifier/score.mjs", tests / "score.mjs")
    shutil.copy2(HERE / "image/bench/api.mjs", tests / "api.mjs")
    upstream = tests / "upstream"
    (upstream / "bench/lib").mkdir(parents=True)
    shutil.copy2(source / "bench/grade.mjs", upstream / "bench/grade.mjs")
    for name in GRADER_FILES:
        shutil.copy2(source / "bench/lib" / name, upstream / "bench/lib" / name)
    (upstream / "extension/lib").mkdir(parents=True)
    shutil.copy2(source / "extension/lib/url-pattern.js", upstream / "extension/lib/url-pattern.js")
    shutil.copy2(source / "LICENSE", upstream / "LICENSE")

    (step / "instruction.md").write_text(
        task["prompt"] + "\n\n" + (intent.get("instructions") or "") + "\n\n" + SAFETY
    )
    (step / "workdir/setup.sh").write_text("#!/bin/bash\nset -euo pipefail\n/opt/bench/setup.mjs\n")
    (tests / "test.sh").write_text("#!/bin/bash\nset -euo pipefail\nnode /tests/score.mjs\n")
    config = {
        "schema_version": "1.3",
        "source": "public-web",
        "task": {"name": f"public-web/{intent['id']}", "keywords": [intent["domain"]]},
        "metadata": {
            "site": intent["domain"],
            "start_url": intent["start_url"],
            "xwebmcp_sha": sha,
            "grading": task["grading"],
            "stage": intent["stage"],
        },
        "environment": {
            "build_timeout_sec": 1200.0,
            "network_mode": "public",
            "workdir": "/",
            "env": {
                "KERNEL_API_KEY": "${KERNEL_API_KEY}",
                "KERNEL_BASE_URL": "${KERNEL_BASE_URL:-}",
            },
            "mcp_servers": [
                {
                    "name": "bench",
                    "transport": "stdio",
                    "command": "/opt/bench/finalize-mcp",
                    "args": [],
                }
            ],
        },
        "steps": [
            {
                "name": "run",
                "agent": {"timeout_sec": float(intent["trial_timeout_sec"])},
                "verifier": {"timeout_sec": 120.0},
            }
        ],
    }
    (task_dir / "task.toml").write_text(tomlkit.dumps(config))
    return task_dir
