"""ClawBench: real-website tasks scored by intercepting the final request.

ClawBench's Harbor adapter generates the tasks. This module then makes them
run on the browser Harbor's Kernel environment provides, and removes
ClawBench's own tool wiring so a toolset can supply the agent's tools.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from pathlib import Path

import httpx
from clawbench.eval.harbor_adapter import main as adapt

from harness.preflight import require_env
from harness.tasks import instructions, read_config, test_scripts, write_config

IMAGE = Path(__file__).with_name("image")
CLAWBENCH_BROWSER = "/app/src/harbor/kernel-browser.py "
HARBOR_BROWSER = "/app/src/harbor/harbor-browser.py "
RUNTIME_BLOCK = re.compile(r"\n---\nHarbor browser runtime:\n.*?\n---\n", re.DOTALL)
PURELYMAIL_API = "https://purelymail.com/api/v0"


def preflight() -> None:
    """Create and delete one PurelyMail account.

    Every task creates a disposable account during setup. Checking first stops
    a bad credential from turning the whole run into setup failures.
    """
    require_env("PURELY_MAIL_API_KEY", "PURELY_MAIL_DOMAIN")
    user = f"cbpreflight{secrets.token_hex(6)}"
    domain = os.environ["PURELY_MAIL_DOMAIN"]
    _purelymail(
        "createUser",
        {
            "userName": user,
            "domainName": domain,
            "password": secrets.token_urlsafe(18),
            "enablePasswordReset": False,
            "sendWelcomeEmail": False,
        },
    )
    _purelymail("deleteUser", {"userName": f"{user}@{domain}"})


def _purelymail(endpoint: str, body: dict) -> None:
    response = httpx.post(
        f"{PURELYMAIL_API}/{endpoint}",
        headers={"Purelymail-Api-Token": os.environ["PURELY_MAIL_API_KEY"]},
        json=body,
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("type") == "error":
        raise SystemExit(f"PurelyMail {endpoint} failed: {result.get('code')}")


def generate(task_ids: list[str] | None, out_dir: Path) -> list[Path]:
    argv = ["--output-dir", str(out_dir), "--browser-runtime", "kernel", "--overwrite"]
    if task_ids:
        argv += ["--task-ids", ",".join(task_ids)]
    if adapt(argv) != 0:
        raise SystemExit("ClawBench could not generate tasks")
    task_dirs = sorted(path for path in out_dir.iterdir() if path.is_dir())
    for task_dir in task_dirs:
        _use_harbor_browser(task_dir)
        _remove_tool_wiring(task_dir)
    return task_dirs


def _use_harbor_browser(task_dir: Path) -> None:
    environment = task_dir / "environment"
    shutil.copy2(IMAGE / "Dockerfile", environment / "Dockerfile")
    shutil.copy2(IMAGE / "harbor-browser.py", environment / "harbor" / "harbor-browser.py")
    for script in [*task_dir.glob("steps/*/workdir/setup.sh"), *test_scripts(task_dir)]:
        text = script.read_text()
        if CLAWBENCH_BROWSER in text:
            script.write_text(text.replace(CLAWBENCH_BROWSER, HARBOR_BROWSER))


def _remove_tool_wiring(task_dir: Path) -> None:
    config = read_config(task_dir)
    config["environment"].pop("mcp_servers", None)
    write_config(task_dir, config)
    for path in instructions(task_dir):
        path.write_text(RUNTIME_BLOCK.sub("\n", path.read_text()))
