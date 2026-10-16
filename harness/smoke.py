"""Smoke-test an arm's agent harness on a local Docker task, without a browser.

`bench smoke <arm>` runs the arm's agent, model, version and agent kwargs
through Harbor on one tiny task whose only tool is the xwebmcp-tasks suite's
stdio MCP server, `bench`, logged by mcp-call-log. It checks what unit tests
cannot: that this agent version installs and runs under Harbor, reaches a
stdio MCP server, and still writes a session that Harbor converts into the
trajectory the report reads. Harness upgrades can change how tool calls are
nested or which built-in tools exist; the summary lists the tool names the
trajectory shows so two versions can be compared.

It costs one short model conversation and needs Docker and the agent's API
key, but no Kernel browser.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from harness.definitions import ROOT, Arm, load_arm
from harness.run import RETRY_EXCEPTIONS

BENCH = ROOT / "suites" / "xwebmcp-tasks" / "image" / "bench"
CALL_LOG = ROOT / "toolsets" / "kernel-mcp-local" / "image" / "bin" / "mcp-call-log"
TASK_NAME = "smoke"
STEP = "run"

DOCKERFILE = """\
FROM node:22-bookworm-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends ca-certificates curl git procps python3 \\
    && rm -rf /var/lib/apt/lists/*
COPY bench/ /opt/bench/
RUN chmod +x /opt/bench/*
WORKDIR /app
"""

TASK_TOML = """\
schema_version = "1.3"
source = "smoke"

[task]
name = "smoke/finalize"

[environment]
build_timeout_sec = 600.0
workdir = "/app"

[[environment.mcp_servers]]
name = "bench"
transport = "stdio"
command = "/opt/bench/mcp-call-log"
args = ["/logs/agent/bench-calls.jsonl", "/opt/bench/finalize-mcp"]

[[steps]]
name = "run"

[steps.agent]
timeout_sec = 300.0

[steps.verifier]
timeout_sec = 60.0
"""

INSTRUCTION = """\
This is a smoke test of your tools. Call the `finalize` tool of the `bench` MCP server exactly
once, with evidence "smoke test", reasoning "smoke test" and outcome "success". Do not run any
other command or tool, then stop.
"""

TEST_SH = """\
#!/bin/bash
set -euo pipefail
/opt/bench/score
"""


def write_task(out_dir: Path) -> Path:
    task = out_dir / TASK_NAME
    environment = task / "environment"
    shutil.copytree(BENCH, environment / "bench", ignore=shutil.ignore_patterns("open-start-url"))
    shutil.copy2(CALL_LOG, environment / "bench" / "mcp-call-log")
    (environment / "Dockerfile").write_text(DOCKERFILE)
    (task / "task.toml").write_text(TASK_TOML)
    step = task / "steps" / STEP
    (step / "tests").mkdir(parents=True)
    (step / "instruction.md").write_text(INSTRUCTION)
    (step / "tests" / "test.sh").write_text(TEST_SH)
    (step / "tests" / "test.sh").chmod(0o755)
    return task


def harbor_command(arm: Arm, task: Path, jobs_dir: Path, job_name: str) -> list[str]:
    command = [
        str(Path(sys.executable).parent / "harbor"),
        "run",
        "--path", str(task),
        "--agent", arm.agent,
        "--model", arm.model,
        "--env", "docker",
        "--job-name", job_name,
        "--jobs-dir", str(jobs_dir),
        "--max-retries", "2",
        "--delete",
        "--yes",
    ]  # fmt: skip
    if arm.agent_version:
        command += ["--agent-kwarg", f"version={arm.agent_version}"]
    for key, value in arm.agent_kwargs.items():
        command += ["--agent-kwarg", f"{key}={json.dumps(value)}"]
    for exception in RETRY_EXCEPTIONS:
        command += ["--retry-include", exception]
    return command


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _builtin_tools(agent_dir: Path) -> list[str] | None:
    """Tools Claude Code reports in its stream-json init event; Codex logs none."""
    for event in _read_jsonl(agent_dir / "claude-code.txt"):
        if event.get("type") == "system" and event.get("subtype") == "init":
            return sorted(str(tool) for tool in event.get("tools") or [])
    return None


def check_trial(trial_dir: Path, expected_version: str | None) -> dict[str, Any]:
    """Summarize one smoke trial and check it against what the benchmarks rely on."""
    result = _read_json(trial_dir / "result.json") or {}
    step_dir = trial_dir / "steps" / STEP
    agent_dir = step_dir / "agent"
    verifier_dir = step_dir / "verifier"

    calls = _read_jsonl(agent_dir / "bench-calls.jsonl")
    finalize_calls = [call for call in calls if call.get("tool") == "finalize"]
    rewards = _read_json(verifier_dir / "reward.json") or {}

    trajectory = _read_json(agent_dir / "trajectory.json")
    steps = trajectory.get("steps") if isinstance(trajectory, dict) else None
    steps = steps if isinstance(steps, list) else []
    tool_calls = [call for step in steps for call in step.get("tool_calls") or []]
    final_metrics = (trajectory or {}).get("final_metrics") or {}
    costs = [(step.get("metrics") or {}).get("cost_usd") for step in steps]
    cost = sum(value for value in costs if isinstance(value, int | float))
    agent_info = result.get("agent_info") or {}
    version = agent_info.get("version") or ((trajectory or {}).get("agent") or {}).get("version")

    checks = {
        "trial_without_exception": not result.get("exception_info"),
        "version_matches": expected_version is None or version == expected_version,
        "mcp_server_got_finalize": bool(finalize_calls) and not finalize_calls[0].get("is_error"),
        "verifier_reward_1": rewards.get("reward") == 1,
        "trajectory_converted": bool(steps),
        "trajectory_has_tool_calls": bool(tool_calls),
        "trajectory_has_token_counts": bool(final_metrics.get("total_prompt_tokens")),
        "trajectory_has_cost": cost > 0 or bool(final_metrics.get("total_cost_usd")),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "agent_version": version,
        "server_calls": sorted({str(call.get("tool")) for call in calls}),
        "trajectory_tools": sorted({str(call.get("function_name")) for call in tool_calls}),
        "builtin_tools": _builtin_tools(agent_dir),
        "steps": len(steps),
        "tool_calls": len(tool_calls),
        "cost_usd": round(cost, 6),
        "trial_dir": str(trial_dir),
    }


def smoke(arm: Arm, work_dir: Path, jobs_dir: Path, job_name: str) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    task = write_task(work_dir)
    # Harbor's progress goes to stderr so stdout is only the summary.
    process = subprocess.run(harbor_command(arm, task, jobs_dir, job_name), stdout=sys.stderr)
    job_dir = jobs_dir / job_name
    trials = sorted(path for path in job_dir.iterdir() if path.is_dir()) if job_dir.is_dir() else []
    summary = (
        check_trial(trials[-1], arm.agent_version)
        if trials
        else {"ok": False, "checks": {"trial_ran": False}}
    )
    summary["ok"] = summary["ok"] and process.returncode == 0
    return {
        "arm": arm.name,
        "agent": arm.agent,
        "model": arm.model,
        "requested_version": arm.agent_version,
        "agent_kwargs": arm.agent_kwargs,
        "harbor_exit": process.returncode,
        **summary,
    }


def distinct_harnesses(arms: list[Arm]) -> list[Arm]:
    """One arm per agent, version, model and agent kwargs; toolsets don't change the harness."""
    seen: dict[tuple[str, str | None, str, str], Arm] = {}
    for arm in arms:
        key = (
            arm.agent,
            arm.agent_version,
            arm.model,
            json.dumps(arm.agent_kwargs, sort_keys=True),
        )
        seen.setdefault(key, arm)
    return list(seen.values())


def all_arms() -> list[Arm]:
    return [load_arm(path.stem) for path in sorted((ROOT / "arms").glob("*.toml"))]


def smoke_many(
    arms: list[Arm], work_dir: Path, jobs_dir: Path, run_id: str
) -> list[dict[str, Any]]:
    def one(arm: Arm) -> dict[str, Any]:
        job_name = f"smoke-{arm.name}-{arm.agent_version or 'latest'}-{run_id}"
        return smoke(arm, work_dir / arm.name, jobs_dir, job_name)

    with ThreadPoolExecutor(max_workers=len(arms) or 1) as pool:
        return list(pool.map(one, arms))


def markdown(summaries: list[dict[str, Any]]) -> str:
    rows = [
        "| Arm | Agent | Version | Result | Trajectory tools | Server calls | Cost |",
        "|---|---|---|---|---|---|---:|",
    ]
    for summary in summaries:
        failed = [name for name, ok in summary["checks"].items() if not ok]
        rows.append(
            "| {arm} | {agent} | {version} | {result} | {tools} | {calls} | ${cost:.4f} |".format(
                arm=summary["arm"],
                agent=summary["agent"],
                version=summary.get("agent_version") or summary["requested_version"],
                result="pass" if summary["ok"] else "fail: " + ", ".join(failed or ["harbor"]),
                tools=", ".join(summary.get("trajectory_tools") or []) or "none",
                calls=", ".join(summary.get("server_calls") or []) or "none",
                cost=summary.get("cost_usd") or 0,
            )
        )
    return "\n".join(rows) + "\n"
