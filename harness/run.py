"""Prepare per-arm Harbor datasets and run them."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness import preflight
from harness.configure import configure_tasks
from harness.definitions import Arm, load_arm, load_benchmark, load_suite, load_toolset

# Transient failures worth retrying as a whole trial: Kernel API hiccups and
# slow agent installs. Image build and task setup failures are not retried.
RETRY_EXCEPTIONS = (
    "APITimeoutError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
    "ConnectionRefusedError",
    "AgentSetupTimeoutError",
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
    suite_args: dict[str, Any] | None = None,
    toolset_args: dict[str, Any] | None = None,
) -> list[PreparedArm]:
    """Generate the suite's tasks and apply each arm's browser settings and toolset.

    `suite_args` overrides the benchmark's `suite_args`. `toolset_args`
    overrides the keys an arm's `toolset_args` already sets, so an arm whose
    toolset does not take an argument keeps running without it. A key no arm
    of the benchmark sets is refused as a likely typo.
    """
    benchmark = load_benchmark(benchmark_name)
    arms = [load_arm(name) for name in (arm_names or benchmark.arms)]
    known = {key for arm in [*arms, *map(load_arm, benchmark.arms)] for key in arm.toolset_args}
    unknown = set(toolset_args or {}) - known
    if unknown:
        raise SystemExit(f"No arm takes toolset args: {', '.join(sorted(unknown))}")
    suite = load_suite(benchmark.suite)

    base = work_dir / "tasks"
    task_dirs = suite.generate(task_ids, base, **{**benchmark.suite_args, **(suite_args or {})})
    if not task_dirs:
        raise SystemExit("The suite generated no tasks")

    prepared = []
    for arm in arms:
        dataset = work_dir / "arms" / arm.name
        shutil.copytree(base, dataset)
        arm_tasks = sorted(path for path in dataset.iterdir() if path.is_dir())
        configurations = configure_tasks(arm_tasks, arm.browser)
        args = {
            key: (toolset_args or {}).get(key, value) for key, value in arm.toolset_args.items()
        }
        load_toolset(arm.toolset).apply(arm_tasks, ref=toolset_ref, **args)
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
    suite_args: dict[str, Any] | None = None,
    toolset_args: dict[str, Any] | None = None,
    attempts: int = 1,
) -> dict[str, int]:
    """Run every arm in parallel and return each arm's Harbor exit status.

    `jobs_dir/run-<run_id>.json` maps each arm to its Harbor job directory,
    browser configurations, and exit status. It is written before Harbor
    starts and updated as each arm finishes, so an interrupted run can still
    be reported.
    """
    preflight.check_kernel_project()
    load_suite(load_benchmark(benchmark_name).suite).preflight()
    prepared = prepare(
        benchmark_name, arm_names, task_ids, work_dir, toolset_ref, suite_args, toolset_args
    )

    jobs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = jobs_dir / f"run-{run_id}.json"
    manifest = {
        item.arm.name: {
            "job_dir": str(jobs_dir / f"{item.arm.name}-{run_id}"),
            "status": None,
            "browser_configurations": item.configurations,
        }
        for item in prepared
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    processes = {}
    for item in prepared:
        job_name = f"{item.arm.name}-{run_id}"
        with (jobs_dir / f"{job_name}.log").open("w") as log:
            processes[item.arm.name] = subprocess.Popen(
                harbor_command(item, jobs_dir, job_name, concurrency, attempts),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    for name, process in processes.items():
        manifest[name]["status"] = process.wait()
        manifest_path.write_text(json.dumps(manifest, indent=2))
    return {name: entry["status"] for name, entry in manifest.items()}


def harbor_command(
    item: PreparedArm, jobs_dir: Path, job_name: str, concurrency: int, attempts: int = 1
) -> list[str]:
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
        "--n-attempts", str(attempts),
        "--max-retries", "5",
        "--delete",
        "--yes",
    ]  # fmt: skip
    if arm.agent_version:
        command += ["--agent-kwarg", f"version={arm.agent_version}"]
    for exception in RETRY_EXCEPTIONS:
        command += ["--retry-include", exception]
    return command
