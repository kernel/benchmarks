"""Kernel MCP server, built from source and run inside the task container.

The agent connects over stdio and drives the trial's browser with
`execute_playwright_code`. Browser lifecycle tools are disabled, so the agent
can only use the browser Harbor created for the trial.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import tomlkit

from harness.tasks import append_instruction, append_test_command, read_config, write_config

REPOSITORY = "https://github.com/kernel/kernel-mcp-server"
IMAGE = Path(__file__).with_name("image")

INSTRUCTION = """
## Browser tools

- Use the `kernel` MCP server's `execute_playwright_code` tool for all browser interaction. Wait for the server to finish initializing before starting; in Claude Code, call `WaitForMcpServers` if it is still pending.
- Before your first browser call, read your browser's session ID from the file `/run/kernel-mcp/session-id` and pass it as `session_id` on every call.
- Do not create, list, update, or delete browsers, and do not use Kernel managed auth.
- Interact through page navigation and DOM/UI actions. Do not call `fetch`, `XMLHttpRequest`, Playwright request APIs, or other HTTP clients inside `execute_playwright_code`.
"""


def apply(task_dirs: list[Path], ref: str | None = None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "source"
        sha = _fetch_source(ref or "main", source)
        for task_dir in task_dirs:
            _apply(task_dir, source, sha)


def _fetch_source(ref: str, dest: Path) -> str:
    dest.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(dest), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("fetch", "-q", "--depth", "1", REPOSITORY, ref)
    git("checkout", "-q", "FETCH_HEAD")
    sha = git("rev-parse", "HEAD")
    shutil.rmtree(dest / ".git")
    (dest / "SOURCE_SHA").write_text(sha + "\n")
    return sha


def _apply(task_dir: Path, source: Path, sha: str) -> None:
    context = task_dir / "environment" / "kernel-mcp-local"
    shutil.copytree(IMAGE / "bin", context / "bin")
    shutil.copytree(source, context / "source")
    dockerfile = task_dir / "environment" / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text().rstrip() + "\n\n" + (IMAGE / "layer.Dockerfile").read_text()
    )

    config = read_config(task_dir)
    server = tomlkit.table()
    server.update(
        {"name": "kernel", "transport": "stdio", "command": "/usr/local/bin/kernel-mcp", "args": []}
    )
    servers = tomlkit.aot()
    servers.append(server)
    config["environment"]["mcp_servers"] = servers
    config.setdefault("metadata", tomlkit.table())["kernel_mcp_server_sha"] = sha
    write_config(task_dir, config)

    append_instruction(task_dir, INSTRUCTION)
    append_test_command(task_dir, "kernel-mcp-verify")
