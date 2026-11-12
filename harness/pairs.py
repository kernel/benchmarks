"""Run two arms as matched pairs: one trial of each, back to back, per task and attempt.

Harbor runs an arm's trials in its own order, so two arms in separate Harbor jobs
reach the same task minutes or hours apart. Here each trial is its own one-trial
Harbor job, and the finished trials are gathered into one job directory per arm
that reads like a Harbor job, so reporting does not change.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path


def balanced_orders(count: int, rng: random.Random) -> list[bool]:
    """Whether the baseline runs first in each pair: half each way, shuffled."""
    orders = [index % 2 == 0 for index in range(count)]
    if count % 2 and rng.random() < 0.5:
        orders[-1] = False
    rng.shuffle(orders)
    return orders


def plan(tasks: list[str], attempts: int, arms: tuple[str, str], seed: str) -> list[dict]:
    """One pair per task and attempt. Attempts interleave across tasks to spread site drift."""
    rng = random.Random(seed)
    baseline_first = {task: balanced_orders(attempts, rng) for task in tasks}
    return [
        {
            "id": f"{task}#{attempt + 1}",
            "task": task,
            "baseline": arms[0],
            "candidate": arms[1],
            "first": arms[0] if baseline_first[task][attempt] else arms[1],
        }
        for attempt in range(attempts)
        for task in tasks
    ]


class ArmJob:
    """An arm's job directory, filled one trial at a time."""

    def __init__(self, name: str, jobs_dir: Path, run_id: str, total: int):
        self.dir = jobs_dir / f"{name}-{run_id}"
        self.scratch = jobs_dir / f"{name}-{run_id}.trials"
        self.status = 0
        self._lock = threading.Lock()
        self._result = {
            "id": str(uuid.uuid4()),
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "n_total_trials": total,
            "stats": {"n_completed_trials": 0, "n_errored_trials": 0, "n_retries": 0},
        }
        self.dir.mkdir(parents=True)
        self.scratch.mkdir()
        (self.dir / "config.json").write_text(json.dumps({"job_name": self.dir.name}))
        self._write()

    def _write(self) -> None:
        (self.dir / "result.json").write_text(json.dumps(self._result, indent=2))

    def run_trial(self, pair: dict, command: Callable[[Path, str], list[str]]) -> None:
        job_name = pair["id"].replace("#", "-")
        with (self.scratch / f"{job_name}.log").open("w") as log:
            status = subprocess.run(
                command(self.scratch, job_name), stdout=log, stderr=subprocess.STDOUT
            ).returncode
        job = self.scratch / job_name
        with self._lock:
            self.status = max(self.status, status)
            stats = self._result["stats"]
            for result in sorted(job.glob("*/result.json")):
                trial = self.dir / result.parent.name
                shutil.move(result.parent, trial)
                (trial / "pair.json").write_text(json.dumps(pair))
                stats["n_completed_trials"] += 1
                stats["n_errored_trials"] += bool(
                    json.loads((trial / "result.json").read_text()).get("exception_info")
                )
            if (job / "result.json").exists():
                harbor = json.loads((job / "result.json").read_text())
                stats["n_retries"] += (harbor.get("stats") or {}).get("n_retries") or 0
            self._write()

    def finish(self) -> None:
        with self._lock:
            self._result["finished_at"] = datetime.now(UTC).isoformat()
            self._write()


def run(
    pairs: list[dict],
    jobs: dict[str, ArmJob],
    commands: dict[str, Callable[[str, Path, str], list[str]]],
    slots: int,
) -> None:
    """Run each pair's two trials back to back, `slots` pairs at a time."""

    def run_pair(pair: dict) -> None:
        second = pair["candidate"] if pair["first"] == pair["baseline"] else pair["baseline"]
        for arm in (pair["first"], second):
            jobs[arm].run_trial(
                pair, lambda jobs_dir, name, arm=arm: commands[arm](pair["task"], jobs_dir, name)
            )

    with ThreadPoolExecutor(max_workers=slots) as pool:
        list(pool.map(run_pair, pairs))
    for job in jobs.values():
        job.finish()
