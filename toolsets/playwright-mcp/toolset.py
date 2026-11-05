"""Playwright MCP, attached over CDP to a browser from any provider.

The same tools drive every provider's browser, so arms that differ only in
provider compare the browsers rather than the tools. Needs an arm with
`environment = "remote-browser"`, which writes the browser's CDP URL into the
task container. The agent works in the browser's existing page and context.

Toolset ref: the @playwright/mcp version to install, default `VERSION`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import tomlkit

from harness.tasks import append_instruction, read_config, write_config

VERSION = "0.0.84"
IMAGE = Path(__file__).with_name("image")

INSTRUCTION = """
## Browser tools

- Use the `browser` MCP server's tools for all browser interaction. They drive a browser that is already open on the start page; keep working in it.
- Do not install or launch another browser.
"""


def apply(task_dirs: list[Path], ref: str | None = None) -> None:
    for task_dir in task_dirs:
        _apply(task_dir, ref or VERSION)


def _apply(task_dir: Path, version: str) -> None:
    context = task_dir / "environment" / "playwright-mcp"
    shutil.copytree(IMAGE / "bin", context / "bin")
    dockerfile = task_dir / "environment" / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text().rstrip()
        + "\n\n"
        + (IMAGE / "layer.Dockerfile").read_text()
        + f"RUN npm install --global @playwright/mcp@{version}\n"
    )

    config = read_config(task_dir)
    server = tomlkit.table()
    server.update(
        {
            "name": "browser",
            "transport": "stdio",
            "command": "/usr/local/bin/browser-mcp",
            "args": [],
        }
    )
    servers = config["environment"].setdefault("mcp_servers", tomlkit.aot())
    servers.append(server)
    config.setdefault("metadata", tomlkit.table())["playwright_mcp_version"] = version
    write_config(task_dir, config)
    append_instruction(task_dir, INSTRUCTION)
