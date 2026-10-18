"""Kernel MCP plus the task site's kernel/xwebmcp tools, registered in the browser.

Applies `kernel-mcp-local` with the arm's Kernel MCP toolsets, then registers
the site's custom WebMCP tools from kernel/xwebmcp in the trial's browser
during step setup, after the suite has opened the start page and before the
agent starts. The agent reaches them the same way it reaches a page's own
WebMCP tools, so the instruction is the same as in an arm without them.

The site comes from the task's `metadata.site`. Every tool registry.json lists
for that domain, except `broken` ones, is registered; those that may change
site state (not `readOnlyHint`) are then removed unless the task names them
in `metadata.xwebmcp_actions`.

Arm args:
    toolsets: Kernel MCP toolsets to enable, passed to kernel-mcp-local.
    xwebmcp_ref: kernel/xwebmcp git ref to take the tools from, default main.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from harness.definitions import load_toolset
from harness.tasks import append_setup_command, append_test_command, read_config, write_config

REPOSITORY = "https://github.com/kernel/xwebmcp"
IMAGE = Path(__file__).with_name("image")


def apply(
    task_dirs: list[Path],
    ref: str | None = None,
    toolsets: Sequence[str] = ("repl",),
    xwebmcp_ref: str = "main",
) -> None:
    load_toolset("kernel-mcp-local").apply(task_dirs, ref=ref, toolsets=toolsets)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "xwebmcp"
        sha = _fetch_source(xwebmcp_ref, source)
        registry = json.loads((source / "registry.json").read_text())["tools"]
        for task_dir in task_dirs:
            _apply(task_dir, source, registry, sha)


def _fetch_source(ref: str, dest: Path) -> str:
    dest.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(dest), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("fetch", "-q", "--depth", "1", REPOSITORY, ref)
    git("checkout", "-q", "FETCH_HEAD")
    return git("rev-parse", "HEAD")


def _apply(task_dir: Path, source: Path, registry: list[dict], sha: str) -> None:
    config = read_config(task_dir)
    metadata = config.get("metadata") or {}
    site = metadata.get("site")
    if not site:
        raise SystemExit(f"{task_dir.name} has no metadata.site for the xwebmcp toolset")
    entries = [
        entry for entry in registry if entry["domain"] == site and entry["status"] != "broken"
    ]
    if not entries:
        raise SystemExit(f"kernel/xwebmcp@{sha[:8]} has no usable tools for {site}")

    context = task_dir / "environment" / "xwebmcp"
    (context / "tools").mkdir(parents=True)
    for entry in entries:
        shutil.copy2(source / entry["file"], context / "tools" / Path(entry["file"]).name)
    manifest = {
        "repository": REPOSITORY,
        "sha": sha,
        "namespace": site,
        "actions": list(metadata.get("xwebmcp_actions") or []),
        "tools": [
            {"name": entry["tool"], "file": Path(entry["file"]).name, "status": entry["status"]}
            for entry in entries
        ],
    }
    (context / "tools.json").write_text(json.dumps(manifest, indent=2))
    shutil.copytree(IMAGE / "bin", context / "bin")

    dockerfile = task_dir / "environment" / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text().rstrip() + "\n\n" + (IMAGE / "layer.Dockerfile").read_text()
    )
    config.setdefault("metadata", {})["xwebmcp_sha"] = sha
    write_config(task_dir, config)
    append_setup_command(task_dir, "xwebmcp-register")
    append_test_command(task_dir, "xwebmcp-verify")
