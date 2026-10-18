import json
import os
import subprocess
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from harness.definitions import ROOT, load_suite

suite = load_suite("xwebmcp-tasks")
BENCH = ROOT / "suites" / "xwebmcp-tasks" / "image" / "bench"


def test_every_site_has_two_train_one_validation_and_one_test_task():
    tasks = suite.load_tasks()
    assert len({task.id for task in tasks}) == len(tasks)
    by_site: dict[str, Counter] = {}
    for task in tasks:
        by_site.setdefault(task.site, Counter())[task.split] += 1
    for site, splits in by_site.items():
        assert splits == Counter(train=2, validation=1, test=1), site


def test_every_fixture_placeholder_resolves():
    for task in suite.load_tasks():
        suite.render(task.instruction, task.fixture, date(2026, 10, 16))


def test_render_turns_days_from_start_into_a_date():
    text = suite.render(
        "on {{date}} from {{origin}}",
        {"date": {"days_from_start": 20}, "origin": "DAL"},
        date(2026, 10, 16),
    )
    assert text == "on Thursday, November 5, 2026 from DAL"


def test_generate_writes_the_split_as_harbor_tasks(tmp_path):
    task_dirs = suite.generate(None, tmp_path, split="test", today=date(2026, 10, 16))
    assert {path.name for path in task_dirs} == {
        task.id for task in suite.load_tasks() if task.split == "test"
    }
    task_dir = tmp_path / "southwest-morning-nonstop-choice"
    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["metadata"]["site"] == "southwest.com"
    assert config["metadata"]["split"] == "test"
    assert config["environment"]["mcp_servers"][0]["name"] == "bench"
    assert config["steps"][0]["name"] == "run"
    instruction = (task_dir / "steps/run/instruction.md").read_text()
    assert "Thursday, November 5, 2026" in instruction
    assert "`finalize` tool exactly once" in instruction
    assert "https://www.southwest.com/" in (task_dir / "steps/run/workdir/setup.sh").read_text()
    assert (task_dir / "environment" / "bench" / "finalize-mcp").is_file()
    assert json.loads((task_dir / "environment" / "bench" / "task.json").read_text()) == {
        "id": "southwest-morning-nonstop-choice",
        "site": "southwest.com",
        "split": "test",
    }


def test_explicit_task_ids_ignore_the_split(tmp_path):
    task_dirs = suite.generate(["delta-trip-summary-seats"], tmp_path, split="test")
    config = tomllib.loads((task_dirs[0] / "task.toml").read_text())
    assert config["metadata"]["split"] == "validation"
    assert config["metadata"]["xwebmcp_actions"] == [
        "select_fare",
        "select_fare_experience",
        "open_seat_map",
        "skip_seat_selection",
    ]
    with pytest.raises(SystemExit):
        suite.generate(["no-such-task"], tmp_path)


def run_finalize(tmp_path: Path, *messages: dict) -> list[dict]:
    report = tmp_path / "finalize.json"
    script = (
        (BENCH / "finalize-mcp")
        .read_text()
        .replace('Path("/logs/agent/finalize.json")', f"Path({str(report)!r})")
    )
    result = subprocess.run(
        ["python3", "-c", script],
        input="".join(json.dumps(message) + "\n" for message in messages),
        capture_output=True,
        text=True,
        check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


def call(call_id: int, **arguments) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": "finalize", "arguments": arguments},
    }


def test_finalize_records_the_first_report_only(tmp_path):
    responses = run_finalize(
        tmp_path,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "x"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        call(3, evidence="Price $22", reasoning="Shown on the page", outcome="success"),
        call(4, evidence="again", reasoning="again", outcome="failed"),
    )
    assert [response["id"] for response in responses] == [1, 2, 3, 4]
    assert responses[0]["result"]["protocolVersion"] == "x"
    schema = responses[1]["result"]["tools"][0]["inputSchema"]
    assert list(schema["properties"]) == ["evidence", "reasoning", "outcome"]
    assert schema["properties"]["outcome"]["enum"] == [
        "success",
        "failed",
        "website_error",
        "kernel_error",
    ]
    assert responses[2]["result"]["isError"] is False
    assert responses[3]["result"]["isError"] is True
    report = json.loads((tmp_path / "finalize.json").read_text())
    assert report["outcome"] == "success"
    assert report["evidence"] == "Price $22"


def test_finalize_refuses_an_incomplete_report(tmp_path):
    responses = run_finalize(
        tmp_path,
        call(1, evidence="", reasoning="r", outcome="success"),
        call(2, evidence="e", reasoning="r", outcome="maybe"),
    )
    assert [response["result"]["isError"] for response in responses] == [True, True]
    assert not (tmp_path / "finalize.json").exists()


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("success", {"reward": 1.0, "finalized": 1.0}),
        ("website_error", {"reward": 0.0, "finalized": 1.0, "outcome_website_error": 1.0}),
        ("kernel_error", {"reward": 0.0, "finalized": 1.0, "outcome_kernel_error": 1.0}),
        (None, {"reward": 0.0, "finalized": 0.0}),
    ],
)
def test_score_rewards_reported_success(tmp_path, outcome, expected):
    agent, verifier = tmp_path / "agent", tmp_path / "verifier"
    agent.mkdir()
    if outcome is not None:
        report = {"outcome": outcome, "evidence": "e", "reasoning": "r"}
        (agent / "finalize.json").write_text(json.dumps(report))
    script = (
        (BENCH / "score")
        .read_text()
        .replace('Path("/logs/agent/finalize.json")', f"Path({str(agent / 'finalize.json')!r})")
        .replace('Path("/logs/verifier")', f"Path({str(verifier)!r})")
    )
    subprocess.run(["python3", "-c", script], check=True, env=os.environ)
    failures = {"outcome_failed": 0.0, "outcome_website_error": 0.0, "outcome_kernel_error": 0.0}
    assert json.loads((verifier / "reward.json").read_text()) == {**failures, **expected}
