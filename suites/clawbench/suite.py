"""ClawBench: real-website tasks scored by intercepting the final request.

ClawBench's Harbor adapter generates the tasks. This module then makes them
run on the browser Harbor's Kernel environment provides, and removes
ClawBench's own tool wiring so a toolset can supply the agent's tools.

The adapter has no mode for a browser that already exists, so this module
replaces the generated Dockerfile and swaps ClawBench's browser lifecycle
script for image/harbor-browser.py. Adding that mode to the adapter would
reduce this suite to preflight, generation, and removing tool wiring.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from pathlib import Path

from clawbench.eval.harbor_adapter import main as adapt
from clawbench.runner.run_support.email import purelymail_request

from harness.preflight import require_env
from harness.tasks import instructions, read_config, test_scripts, write_config

IMAGE = Path(__file__).with_name("image")
CLAWBENCH_BROWSER = "/app/src/harbor/kernel-browser.py "
HARBOR_BROWSER = "/app/src/harbor/harbor-browser.py "
RUNTIME_BLOCK = re.compile(r"\n---\nHarbor browser runtime:\n.*?\n---\n", re.DOTALL)
# ClawBench forbids command-line tools, but agents without a file-read tool
# need a shell to read the task's files.
FILE_ACCESS = (
    "\n---\nReading files with shell commands (for example under `./my-info/`) is "
    "allowed. Do everything else through the browser.\n---\n"
)


def preflight() -> None:
    """Create and delete one PurelyMail account.

    Every task creates a disposable account during setup. Checking first stops
    a bad credential from turning the whole run into setup failures.
    """
    require_env("PURELY_MAIL_API_KEY", "PURELY_MAIL_DOMAIN")
    api_key = os.environ["PURELY_MAIL_API_KEY"]
    domain = os.environ["PURELY_MAIL_DOMAIN"]
    user = f"cbpreflight{secrets.token_hex(6)}"
    purelymail_request(
        "createUser",
        {
            "userName": user,
            "domainName": domain,
            "password": secrets.token_urlsafe(18),
            "enablePasswordReset": False,
            "sendWelcomeEmail": False,
        },
        api_key,
    )
    purelymail_request("deleteUser", {"userName": f"{user}@{domain}"}, api_key)


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
        path.write_text(RUNTIME_BLOCK.sub(FILE_ACCESS, path.read_text()))
