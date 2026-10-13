"""Prepare per-arm Harbor datasets and run them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from harness import preflight
from harness.configure import configure_tasks
from harness.definitions import Arm, load_arm, load_benchmark, load_suite, load_toolset

# Failures worth retrying as a whole trial: Kernel API hiccups, browser
# placement, and agent installs. Task setup failures are not retried.
RETRY_EXCEPTIONS = (
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
    "ConnectionRefusedError",
    "AgentSetupTimeoutError",
    "RuntimeError",
)


@dataclass
class PreparedArm:
    arm: Arm
    dataset: Path
    configurations: dict


def prepare(
    benchmark_name: str,
    arm_names: list[str] | None,
    task_ids: list[str] | None,
    work_dir: Path,
    toolset_ref: str | None = None,
) -> list[PreparedArm]:
    benchmark = load_benchmark(benchmark_name)
    arms = [load_arm(name) for name in (arm_names or benchmark.arms)]
    suite = load_suite(benchmark.suite)

    base = work_dir / "tasks"
    task_dirs = suite.generate(task_ids, base)
    if not task_dirs:
        raise SystemExit("The suite generated no tasks")

    prepared = []
    for arm in arms:
        dataset = work_dir / "arms" / arm.name
        shutil.copytree(base, dataset)
        arm_tasks = sorted(path for path in dataset.iterdir() if path.is_dir())
        configurations = configure_tasks(arm_tasks, arm.browser)
        load_toolset(arm.toolset).apply(arm_tasks, ref=toolset_ref)
        prepared.append(PreparedArm(arm, dataset, configurations))
    return prepared


def run(
    benchmark_name: str,
    arm_names: list[str] | None,
    task_ids: list[str] | None,
    work_dir: Path,
    jobs_dir: Path,
    run_id: str,
    concurrency: int,
    toolset_ref: str | None = None,
) -> dict[str, int]:
    """Run every arm in parallel and return each arm's Harbor exit status."""
    preflight.check_kernel_project()
    load_suite(load_benchmark(benchmark_name).suite).preflight()
    prepared = prepare(benchmark_name, arm_names, task_ids, work_dir, toolset_ref)

    jobs_dir.mkdir(parents=True, exist_ok=True)
    processes = {}
    for item in prepared:
        log = (jobs_dir / f"{item.arm.name}-{run_id}.log").open("w")
        processes[item.arm.name] = (
            subprocess.Popen(
                harbor_command(item, jobs_dir, f"{item.arm.name}-{run_id}", concurrency),
                stdout=log,
                stderr=subprocess.STDOUT,
            ),
            log,
        )
    statuses = {}
    for name, (process, log) in processes.items():
        statuses[name] = process.wait()
        log.close()

    for item in prepared:
        job_dir = jobs_dir / f"{item.arm.name}-{run_id}"
        if job_dir.is_dir():
            (job_dir / "browser-configurations.json").write_text(
                json.dumps(item.configurations, indent=2)
            )
    (jobs_dir / f"run-{run_id}.json").write_text(
        json.dumps(
            {
                name: {"status": status, "job_dir": str(jobs_dir / f"{name}-{run_id}")}
                for name, status in statuses.items()
            },
            indent=2,
        )
    )
    return statuses


def harbor_command(item: PreparedArm, jobs_dir: Path, job_name: str, concurrency: int) -> list[str]:
    arm = item.arm
    command = [
        str(Path(sys.executable).parent / "harbor"),
        "run",
        "--path", str(item.dataset),
        "--agent", arm.agent,
        "--model", arm.model,
        "--env", "harness.environment:KernelTaskEnvironment",
        "--job-name", job_name,
        "--jobs-dir", str(jobs_dir),
        "--n-concurrent", str(concurrency),
        "--max-retries", os.environ.get("HARBOR_MAX_RETRIES", "5"),
        "--delete",
        "--yes",
    ]  # fmt: skip
    if arm.agent_version:
        command += ["--agent-kwarg", f"version={arm.agent_version}"]
    for exception in RETRY_EXCEPTIONS:
        command += ["--retry-include", exception]
    return command
