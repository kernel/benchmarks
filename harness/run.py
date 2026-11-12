"""Prepare per-arm Harbor datasets and run them."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from harness import pairs, preflight
from harness.configure import configure_tasks
from harness.definitions import Arm, load_arm, load_benchmark, load_suite, load_toolset
from harness.tasks import read_config

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
    runner: str | None = None,
    model: str | None = None,
) -> list[PreparedArm]:
    benchmark = load_benchmark(benchmark_name)
    arms = [load_arm(name, runner=runner, model=model) for name in (arm_names or benchmark.arms)]
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
        load_toolset(arm.toolset).apply(
            arm_tasks, ref=toolset_ref or arm.toolset_ref, **arm.toolset_args
        )
        prepared.append(PreparedArm(arm, dataset, configurations))
    return prepared


def task_limits(dataset: Path) -> dict[str, dict]:
    """Each task's agent timeout and the limits its suite set, as they will run."""
    limits = {}
    for task_dir in sorted(path for path in dataset.iterdir() if path.is_dir()):
        config = read_config(task_dir).unwrap()
        agents = [step.get("agent", {}) for step in config.get("steps", [])] or [
            config.get("agent", {})
        ]
        limits[task_dir.name] = {
            "agent_timeout_sec": sum(agent.get("timeout_sec") or 0 for agent in agents) or None,
            **config.get("metadata", {}).get("limits", {}),
        }
    return limits


def run(
    benchmark_name: str,
    arm_names: list[str] | None,
    task_ids: list[str] | None,
    work_dir: Path,
    jobs_dir: Path,
    run_id: str,
    concurrency: int,
    toolset_ref: str | None = None,
    attempts: int = 1,
    runner: str | None = None,
    model: str | None = None,
    unpaired: bool = False,
) -> dict[str, int]:
    """Run every arm in parallel and return each arm's Harbor exit status.

    Arms the benchmark pairs run as matched pairs, `concurrency` pairs at a
    time, unless `unpaired` is set; every other arm is one Harbor job.

    `jobs_dir/run-<run_id>.json` maps each arm to its Harbor job directory,
    browser configurations, task limits, pair order and exit status. It is
    written before Harbor starts and updated as each arm finishes, so an
    interrupted run can still be reported.
    """
    if attempts < 1 or concurrency < 1:
        raise SystemExit("attempts and concurrency must be positive")
    preflight.check_kernel_project()
    benchmark = load_benchmark(benchmark_name)
    load_suite(benchmark.suite).preflight()
    prepared = prepare(
        benchmark_name, arm_names, task_ids, work_dir, toolset_ref, runner=runner, model=model
    )
    by_name = {item.arm.name: item for item in prepared}
    plans = {
        arms: pairs.plan(
            sorted(path.name for path in by_name[arms[0]].dataset.iterdir() if path.is_dir()),
            attempts,
            arms,
            f"{run_id}:{arms[0]}",
        )
        for arms in ([] if unpaired else benchmark.pairs)
        if all(name in by_name for name in arms)
    }
    orders = {
        name: {"with": arms[1 - index], "first": {pair["id"]: pair["first"] for pair in plan}}
        for arms, plan in plans.items()
        for index, name in enumerate(arms)
    }

    jobs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = jobs_dir / f"run-{run_id}.json"
    manifest = {
        item.arm.name: {
            "job_dir": str(jobs_dir / f"{item.arm.name}-{run_id}"),
            "status": None,
            "browser_configurations": item.configurations,
            "runner": {
                "preset": item.arm.runner,
                "agent": item.arm.agent,
                "model": item.arm.model,
                "agent_version": item.arm.agent_version,
                "agent_kwargs": item.arm.agent_kwargs,
            },
            "limits": task_limits(item.dataset),
            "pairs": orders.get(item.arm.name),
        }
        for item in prepared
    }
    try:
        with manifest_path.open("x") as output:
            json.dump(manifest, output, indent=2)
    except FileExistsError:
        raise SystemExit(
            f"{manifest_path} already exists; use a new --run-id for each experiment"
        ) from None

    processes = {}
    for item in prepared:
        if item.arm.name in orders:
            continue
        job_name = f"{item.arm.name}-{run_id}"
        with (jobs_dir / f"{job_name}.log").open("w") as log:
            processes[item.arm.name] = subprocess.Popen(
                harbor_command(item, jobs_dir, job_name, concurrency, attempts),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    with ThreadPoolExecutor(max_workers=max(1, len(plans))) as pool:
        running = []
        for arms, plan in plans.items():
            jobs = {name: pairs.ArmJob(name, jobs_dir, run_id, len(plan)) for name in arms}
            commands = {
                name: lambda task, directory, job_name, item=by_name[name]: harbor_command(
                    item, directory, job_name, 1, 1, task
                )
                for name in arms
            }
            running.append((pool.submit(pairs.run, plan, jobs, commands, concurrency), jobs))
        for name, process in processes.items():
            manifest[name]["status"] = process.wait()
            manifest_path.write_text(json.dumps(manifest, indent=2))
        for future, jobs in running:
            failed = future.exception() is not None
            for name, job in jobs.items():
                manifest[name]["status"] = 1 if failed else job.status
            manifest_path.write_text(json.dumps(manifest, indent=2))
    return {name: entry["status"] for name, entry in manifest.items()}


def harbor_command(
    item: PreparedArm,
    jobs_dir: Path,
    job_name: str,
    concurrency: int,
    attempts: int = 1,
    task: str | None = None,
) -> list[str]:
    arm = item.arm
    command = [
        str(Path(sys.executable).parent / "harbor"),
        "run",
        "--path", str(item.dataset / task if task else item.dataset),
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
    for key, value in arm.agent_kwargs.items():
        command += ["--agent-kwarg", f"{key}={json.dumps(value)}"]
    for exception in RETRY_EXCEPTIONS:
        command += ["--retry-include", exception]
    return command
