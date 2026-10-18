import json
import tomllib
from pathlib import Path

from harness import smoke
from harness.definitions import Arm


def write_trial(trial: Path, *, tool_calls: list[dict], calls: list[dict], reward: float) -> None:
    agent = trial / "steps" / "run" / "agent"
    verifier = trial / "steps" / "run" / "verifier"
    agent.mkdir(parents=True)
    verifier.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps({"agent_info": {"version": "1.2.3"}, "exception_info": None})
    )
    (verifier / "reward.json").write_text(json.dumps({"reward": reward}))
    (agent / "bench-calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
    steps = [
        {"step_id": 1, "source": "user", "message": "smoke"},
        {"step_id": 2, "source": "agent", "tool_calls": tool_calls, "metrics": {"cost_usd": 0.01}},
    ]
    (agent / "trajectory.json").write_text(
        json.dumps({"steps": steps, "final_metrics": {"total_prompt_tokens": 100}})
    )


def test_a_trial_that_called_finalize_and_converted_passes(tmp_path):
    write_trial(
        tmp_path,
        tool_calls=[{"tool_call_id": "1", "function_name": "exec", "arguments": {}}],
        calls=[{"tool": "finalize", "is_error": False}],
        reward=1.0,
    )
    summary = smoke.check_trial(tmp_path, "1.2.3")
    assert summary["ok"], summary["checks"]
    assert summary["trajectory_tools"] == ["exec"]
    assert summary["server_calls"] == ["finalize"]


def test_a_trial_whose_session_did_not_convert_fails(tmp_path):
    write_trial(tmp_path, tool_calls=[], calls=[{"tool": "finalize"}], reward=1.0)
    (tmp_path / "steps" / "run" / "agent" / "trajectory.json").unlink()
    summary = smoke.check_trial(tmp_path, "1.2.3")
    assert not summary["ok"]
    assert not summary["checks"]["trajectory_converted"]
    assert summary["checks"]["mcp_server_got_finalize"]


def test_a_trial_with_another_version_or_no_mcp_call_fails(tmp_path):
    write_trial(tmp_path, tool_calls=[], calls=[], reward=0.0)
    checks = smoke.check_trial(tmp_path, "9.9.9")["checks"]
    assert not checks["version_matches"]
    assert not checks["mcp_server_got_finalize"]
    assert not checks["verifier_reward_1"]


def test_distinct_harnesses_ignore_toolsets_and_browsers():
    arms = [
        Arm(name="a", agent="codex", model="m", toolset="x", agent_version="1"),
        Arm(name="b", agent="codex", model="m", toolset="y", agent_version="1"),
        Arm(name="c", agent="codex", model="m", toolset="x", agent_version="2"),
        Arm(
            name="d",
            agent="codex",
            model="m",
            toolset="x",
            agent_version="1",
            agent_kwargs={"disable_web_search": True},
        ),
    ]
    assert [arm.name for arm in smoke.distinct_harnesses(arms)] == ["a", "c", "d"]


def test_smoke_task_wraps_the_finalize_server_in_the_call_log(tmp_path):
    task = smoke.write_task(tmp_path)
    config = tomllib.loads((task / "task.toml").read_text())
    [server] = config["environment"]["mcp_servers"]
    assert server["command"] == "/opt/bench/mcp-call-log"
    assert server["args"][-1] == "/opt/bench/finalize-mcp"
    bench = task / "environment" / "bench"
    assert (bench / "finalize-mcp").is_file()
    assert (bench / "mcp-call-log").is_file()
    assert (bench / "score").is_file()
    assert (task / "steps" / "run" / "instruction.md").is_file()
