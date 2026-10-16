"""Playwright MCP connected over CDP to the trial's Kernel browser.

The agent drives the browser with Playwright MCP's tools (`browser_navigate`,
`browser_snapshot`, `browser_click`, ...), the toolset to compare with Kernel
MCP's `browser_repl`. The server connects to the browser Harbor created for
the trial and runs inside the task container; it cannot call WebMCP tools.

Arm args:
    version: @playwright/mcp version to install, default 0.0.83.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import tomlkit

from harness.definitions import ROOT
from harness.tasks import append_instruction, append_test_command, read_config, write_config

IMAGE = Path(__file__).with_name("image")
CALL_LOG = ROOT / "toolsets" / "kernel-mcp-local" / "image" / "bin" / "mcp-call-log"

INSTRUCTION = """
## Browser tools

- Use the `playwright` MCP server's `browser_*` tools for all browser interaction. The server is already connected to your browser; wait for it to finish initializing before starting.
- Start with `browser_snapshot` to see the open page, and use the element refs it returns with the other tools.
- Do not open or connect to any other browser.
"""


def apply(task_dirs: list[Path], ref: str | None = None, version: str = "0.0.83") -> None:
    for task_dir in task_dirs:
        _apply(task_dir, version)


def _apply(task_dir: Path, version: str) -> None:
    context = task_dir / "environment" / "playwright-mcp"
    shutil.copytree(IMAGE / "bin", context / "bin")
    shutil.copy2(CALL_LOG, context / "bin" / "mcp-call-log")
    dockerfile = task_dir / "environment" / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text().rstrip()
        + "\n\n# playwright-mcp toolset.\n"
        + f"RUN npm install --global @playwright/mcp@{version}\n"
        + (IMAGE / "layer.Dockerfile").read_text()
    )

    config = read_config(task_dir)
    server = tomlkit.table()
    server.update(
        {
            "name": "playwright",
            "transport": "stdio",
            "command": "/usr/local/bin/playwright-mcp-stdio",
            "args": [],
        }
    )
    config["environment"].setdefault("mcp_servers", tomlkit.aot()).append(server)
    config.setdefault("metadata", tomlkit.table())["playwright_mcp_version"] = version
    write_config(task_dir, config)

    append_instruction(task_dir, INSTRUCTION)
    append_test_command(task_dir, "playwright-mcp-verify")
