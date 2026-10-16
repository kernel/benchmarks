"""Command line entry point: `bench prepare` and `bench run`."""

from __future__ import annotations

import argparse
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from harness.run import prepare, run


def _split(value: str | None) -> list[str] | None:
    if not value or value == "all":
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _pairs(values: list[str]) -> dict[str, str]:
    pairs = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise SystemExit(f"Expected key=value, got {value!r}")
        pairs[key] = item
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench")
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("prepare", "run"):
        command = commands.add_parser(name)
        command.add_argument("benchmark", help="name of a file in benchmarks/")
        command.add_argument(
            "--arms", help="comma-separated arm names (default: all in the benchmark)"
        )
        command.add_argument("--tasks", default="all", help="comma-separated task IDs, or all")
        command.add_argument("--toolset-ref", help="git ref for toolsets built from source")
        command.add_argument(
            "--suite-arg",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="override one of the benchmark's suite_args, e.g. split=validation",
        )
        command.add_argument(
            "--toolset-arg",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="override a toolset_args key in the arms that set it, e.g. xwebmcp_ref=my-branch",
        )

    commands.choices["prepare"].add_argument("--out", type=Path, required=True)
    run_command = commands.choices["run"]
    run_command.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    run_command.add_argument(
        "--concurrency", type=int, default=10, help="concurrent trials per arm"
    )
    run_command.add_argument("--attempts", type=int, default=1, help="trials per task and arm")
    run_command.add_argument("--run-id", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))

    args = parser.parse_args(argv)
    arms = _split(args.arms)
    tasks = _split(args.tasks)
    suite_args = _pairs(args.suite_arg)
    toolset_args = _pairs(args.toolset_arg)

    if args.command == "prepare":
        prepared = prepare(
            args.benchmark, arms, tasks, args.out, args.toolset_ref, suite_args, toolset_args
        )
        for item in prepared:
            print(f"{item.arm.name}: {item.dataset}")
        return 0

    with tempfile.TemporaryDirectory() as work_dir:
        statuses = run(
            args.benchmark,
            arms,
            tasks,
            Path(work_dir),
            args.jobs_dir,
            args.run_id,
            args.concurrency,
            args.toolset_ref,
            suite_args,
            toolset_args,
            args.attempts,
        )
    for arm, status in statuses.items():
        print(f"{arm}: harbor exited {status} -> {args.jobs_dir / f'{arm}-{args.run_id}'}")
    return max(statuses.values(), default=0)
