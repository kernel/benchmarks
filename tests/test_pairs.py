import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import definitions, pairs
from harness.definitions import load_arm, load_benchmark
from harness.run import PreparedArm, run

ARMS = ("public-control", "public-treatment")


def test_pair_order_is_balanced_within_each_task_and_fixed_by_the_run_id():
    assert load_benchmark("public-web").pairs == [ARMS]
    plan = pairs.plan(["a", "b"], 5, ARMS, "run-1")
    assert [pair["id"] for pair in plan[:3]] == ["a#1", "b#1", "a#2"]
    for task in ("a", "b"):
        first = [pair["first"] for pair in plan if pair["task"] == task]
        assert sorted(first.count(arm) for arm in ARMS) == [2, 3]
    assert plan == pairs.plan(["a", "b"], 5, ARMS, "run-1")
    assert plan != pairs.plan(["a", "b"], 5, ARMS, "run-2")
    assert pairs.balanced_orders(4, random.Random(0)).count(True) == 2


@pytest.mark.parametrize(
    "text,message",
    [
        ('pairs = [["a"]]', "two of the benchmark"),
        ('pairs = [["a", "c"]]', "two of the benchmark"),
        ('pairs = [["a", "b"], ["b", "a"]]', "only one pair"),
    ],
)
def test_benchmark_pairs_must_name_its_own_arms_once(tmp_path, monkeypatch, text, message):
    monkeypatch.setattr(definitions, "ROOT", tmp_path)
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks/bad.toml").write_text(f'suite = "s"\narms = ["a", "b"]\n{text}\n')
    with pytest.raises(SystemExit, match=message):
        load_benchmark("bad")


def test_paired_arms_run_back_to_back_and_fill_harbor_shaped_job_directories(tmp_path, monkeypatch):
    import harness.run as module

    monkeypatch.setattr(module.preflight, "check_kernel_project", lambda: None)
    monkeypatch.setattr(module, "load_suite", lambda name: SimpleNamespace(preflight=lambda: None))
    prepared = [
        PreparedArm(load_arm(name), tmp_path / "datasets" / name, {})
        for name in load_benchmark("public-web").arms
    ]
    for item in prepared:
        for task in ("alpha", "beta"):
            (item.dataset / task).mkdir(parents=True)
            (item.dataset / task / "task.toml").write_text(
                'source = "public-web"\n[metadata]\ngrading = "pinned rules"\n'
            )
    monkeypatch.setattr(module, "prepare", lambda *args, **kwargs: prepared)
    started = []

    def harbor(command, stdout, stderr):
        """A one-trial Harbor job: a job directory holding one trial directory."""
        option = lambda name: command[command.index(name) + 1]  # noqa: E731
        task, job = Path(option("--path")), Path(option("--jobs-dir")) / option("--job-name")
        assert (option("--n-attempts"), option("--n-concurrent")) == ("1", "1")
        task_plan = json.loads(
            (tmp_path / "jobs" / f"{task.parent.name}-r1/task-plan.json").read_text()
        )
        assert task_plan == {
            name: {"attempts": 2, "source": "public-web", "metadata": {"grading": "pinned rules"}}
            for name in ("alpha", "beta")
        }
        started.append((task.parent.name, option("--job-name")))
        trial = job / f"{task.name}__{len(started)}"
        trial.mkdir(parents=True)
        failed = task.parent.name == "public-treatment" and task.name == "beta"
        (trial / "result.json").write_text(
            json.dumps({"exception_info": {"exception_type": "X"} if failed else None})
        )
        (job / "result.json").write_text(json.dumps({"stats": {"n_retries": 1}}))
        return SimpleNamespace(returncode=int(failed))

    monkeypatch.setattr(pairs.subprocess, "run", harbor)
    unpaired = []
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: unpaired.append(command) or SimpleNamespace(wait=lambda: 0),
    )
    jobs = tmp_path / "jobs"
    statuses = run("public-web", None, None, tmp_path, jobs, "r1", 1, attempts=2)
    assert statuses == {"public-control": 0, "public-treatment": 1, "public-playwright": 0}
    # The Playwright-only arm is outside the pair and stays one Harbor job.
    assert len(unpaired) == 1 and "--n-attempts" in unpaired[0]

    manifest = json.loads((jobs / "run-r1.json").read_text())
    order = manifest["public-control"]["pairs"]["first"]
    assert manifest["public-control"]["pairs"]["with"] == "public-treatment"
    assert order == manifest["public-treatment"]["pairs"]["first"]
    assert manifest["public-playwright"]["pairs"] is None
    # One slot: each pair's two trials run consecutively, the planned arm first.
    assert len(started) == 8
    for first, second in zip(started[::2], started[1::2], strict=True):
        assert first[1] == second[1] and {first[0], second[0]} == set(ARMS)
        assert order[first[1].replace("-", "#")] == first[0]

    for arm in ARMS:
        job = jobs / f"{arm}-r1"
        result = json.loads((job / "result.json").read_text())
        assert result["n_total_trials"] == 4 and result["finished_at"]
        assert result["stats"] == {
            "n_completed_trials": 4,
            "n_errored_trials": 2 if arm == "public-treatment" else 0,
            "n_retries": 4,
        }
        assert json.loads((job / "config.json").read_text())["job_name"] == f"{arm}-r1"
        trials = [path for path in job.iterdir() if path.is_dir()]
        assert len(trials) == 4
        recorded = [json.loads((trial / "pair.json").read_text()) for trial in trials]
        assert sorted(pair["id"] for pair in recorded) == ["alpha#1", "alpha#2", "beta#1", "beta#2"]
        assert all((pair["baseline"], pair["candidate"]) == ARMS for pair in recorded)
