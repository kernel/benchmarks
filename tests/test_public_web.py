import json
import os
import runpy
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from harness.definitions import load_arm, load_benchmark, load_suite, load_toolset
from harness.run import PreparedArm, harbor_command, prepare, run

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "toolsets/kernel-mcp-local/image/bin"


def test_public_arms_change_only_the_approach():
    benchmark = load_benchmark("public-web")
    arms = [load_arm(name) for name in benchmark.arms]
    assert [arm.toolset_args["approach"] for arm in arms] == ["control", "treatment", "playwright"]
    assert len({(arm.agent, arm.agent_version, arm.model, arm.toolset_ref) for arm in arms}) == 1
    assert all(arm.browser.config == {"stealth": True} for arm in arms)
    assert all(arm.agent_kwargs == {"disable_web_search": True} for arm in arms)
    assert all(len(arm.toolset_ref) == 40 for arm in arms)
    source = tomllib.loads((ROOT / "suites/public-web/source.toml").read_text())
    assert len(source["revision"]) == 40
    assert len(source["tasks"]) == 8


def test_attempts_and_agent_options_reach_harbor(tmp_path):
    arm = load_arm("public-control")
    command = harbor_command(PreparedArm(arm, tmp_path, {}), tmp_path, "trial", 2, 5)
    assert command[command.index("--n-attempts") + 1] == "5"
    assert "disable_web_search=true" in command
    assert f"version={arm.agent_version}" in command
    with pytest.raises(SystemExit, match="positive"):
        run("public-web", None, None, tmp_path, tmp_path, "r", 1, attempts=0)


def test_public_suite_rejects_unknown_tasks_before_fetching(tmp_path, monkeypatch):
    suite = load_suite("public-web")
    monkeypatch.setattr(suite, "fetch_source", lambda *args: pytest.fail("should not fetch"))
    with pytest.raises(SystemExit, match="Unknown public-web tasks"):
        suite.generate(["unknown"], tmp_path)


def test_suite_writes_harbor_task_and_keeps_injection_outside_image(tmp_path):
    suite = load_suite("public-web")
    source = tmp_path / "source"
    files = ["bench/grade.mjs", "extension/lib/url-pattern.js", "LICENSE"]
    files += [f"bench/lib/{name}" for name in suite.GRADER_FILES]
    for name in files:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    task_source = tmp_path / "materialized"
    task_source.mkdir()
    task = {
        "intent": {
            "id": "test",
            "domain": "example.com",
            "start_url": "https://example.com",
            "stage": "find",
            "trial_timeout_sec": 600,
        },
        "prompt": "Return JSON.",
        "grading": "pinned rules",
        "registered": ["search"],
        "schema": {},
    }
    (task_source / "task.json").write_text(json.dumps(task))
    (task_source / "tools.js").write_text("[]")
    task_dir = suite._write(task_source, tmp_path / "tasks/test", source, "a" * 40)
    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["source"] == "public-web"
    assert config["metadata"]["xwebmcp_sha"] == "a" * 40
    assert config["environment"]["mcp_servers"][0]["name"] == "bench"
    assert (task_dir / "xwebmcp-tools.js").exists()
    assert not (task_dir / "environment/bench/tools.js").exists()
    assert "finalize" in (task_dir / "steps/run/instruction.md").read_text()
    assert "setup.mjs" in (task_dir / "steps/run/workdir/setup.sh").read_text()


def test_toolsets_preserve_finalize_and_inject_only_treatment(task_dir, tmp_path, monkeypatch):
    approach = load_toolset("browser-approach")
    kernel = load_toolset("kernel-mcp-local")

    def fetch(ref, dest):
        dest.mkdir()
        (dest / "package.json").write_text("{}")
        return ref

    monkeypatch.setattr(kernel, "_fetch_source", fetch)
    monkeypatch.setattr(approach, "load_toolset", lambda name: kernel)
    (task_dir / "environment/bench").mkdir()
    (task_dir / "xwebmcp-tools.js").write_text("[]")
    with (task_dir / "task.toml").open("a") as file:
        file.write(
            '\n[[environment.mcp_servers]]\nname = "bench"\ntransport = "stdio"\ncommand = "finalize"\n'
        )
    instructions = {}
    for mode in ("control", "treatment", "playwright"):
        dest = tmp_path / mode
        shutil.copytree(task_dir, dest)
        approach.apply([dest], ref="a" * 40, approach=mode)
        config = tomllib.loads((dest / "task.toml").read_text())
        assert [server["name"] for server in config["environment"]["mcp_servers"]] == [
            "bench",
            "kernel",
        ]
        assert (dest / "environment/bench/tools.js").exists() == (mode == "treatment")
        dockerfile = (dest / "environment/Dockerfile").read_text()
        expected = (
            "execute_playwright_code"
            if mode == "playwright"
            else "execute_playwright_code,browser_repl,computer_action,webmcp"
        )
        assert f"KERNEL_BENCH_ALLOWED_TOOLS={expected}\n" in dockerfile
        assert f"BROWSER_APPROACH={mode}" in dockerfile
        instructions[mode] = (dest / "steps/run/instruction.md").read_text()
    assert instructions["control"] == instructions["treatment"]
    assert "Do not discover or invoke WebMCP" in instructions["playwright"]


def test_prepare_passes_pinned_refs_and_toolset_args(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import harness.run as runner

    def generate(ids, output):
        (output / "one").mkdir(parents=True)
        return [output / "one"]

    received = []
    monkeypatch.setattr(runner, "load_suite", lambda name: SimpleNamespace(generate=generate))
    monkeypatch.setattr(runner, "configure_tasks", lambda tasks, browser: {})
    monkeypatch.setattr(
        runner,
        "load_toolset",
        lambda name: SimpleNamespace(apply=lambda tasks, **kwargs: received.append(kwargs)),
    )
    prepare("public-web", None, None, tmp_path)
    assert [call["approach"] for call in received] == ["control", "treatment", "playwright"]
    assert len({call["ref"] for call in received}) == 1
    assert len(received[0]["ref"]) == 40


def test_finalize_validates_outcomes_and_keeps_the_first_report(tmp_path, monkeypatch):
    finalize = runpy.run_path(str(ROOT / "suites/public-web/image/bench/finalize-mcp"))["finalize"]
    path = tmp_path / "finalize.json"
    monkeypatch.setitem(finalize.__globals__, "REPORT", path)
    arguments = {
        "evidence": '{"answer": 42}',
        "reasoning": "Read from the page.",
        "outcome": "success",
    }
    assert finalize({**arguments, "outcome": []})[1] is True
    assert not path.exists()
    assert finalize(arguments)[1] is False
    assert finalize({**arguments, "evidence": "changed"})[1] is True
    assert json.loads(path.read_text())["evidence"] == arguments["evidence"]


def test_call_checks_detect_session_tool_and_playwright_violations():
    check = runpy.run_path(str(BIN / "kernel-mcp-verify"))["check_calls"]
    call = {
        "tool": "execute_playwright_code",
        "arguments": {"session_id": "s", "code": "return await page.title()"},
    }
    assert check([call], ["playwright"], "s", "playwright")["valid"]
    assert not check([], ["playwright"], "s", "playwright")["valid"]
    assert not check([call], ["playwright"], "other", "playwright")["valid"]
    assert not check([call, {"tool": "computer_action"}], ["playwright"], "s", "playwright")[
        "valid"
    ]
    for code in ("return await webmcp.listTools()", "return await webmcp.invokeTool(ref, {})"):
        violation = {**call, "arguments": {"session_id": "s", "code": code}}
        assert not check([violation], ["playwright"], "s", "playwright")["valid"]
    webmcp = {"tool": "webmcp", "arguments": {"session_id": "s", "action": "invoke"}}
    result = check([webmcp], ["playwright", "repl", "computer", "webmcp"], "s", "treatment")
    assert result["valid"] and result["webmcp_invoked"]


@pytest.mark.parametrize("approach,expected_reward", [("playwright", 0), (None, 1)])
def test_verifier_enforces_new_arms_without_changing_legacy_rewards(
    tmp_path, monkeypatch, approach, expected_reward
):
    main = runpy.run_path(str(BIN / "kernel-mcp-verify"))["main"]
    logs, verifier = tmp_path / "calls", tmp_path / "verifier"
    logs.mkdir()
    verifier.mkdir()
    (logs / "calls.jsonl").write_text(
        json.dumps({"tool": "execute_playwright_code", "arguments": {"session_id": "wrong"}}) + "\n"
    )
    (verifier / "reward.json").write_text('{"reward": 1}')
    monkeypatch.setitem(main.__globals__, "LOGS", logs)
    monkeypatch.setitem(main.__globals__, "VERIFIER", verifier)
    monkeypatch.setenv("KERNEL_SESSION_ID", "assigned")
    monkeypatch.setenv("KERNEL_MCP_ENABLED_TOOLSETS", "playwright")
    if approach:
        monkeypatch.setenv("BROWSER_APPROACH", approach)
    else:
        monkeypatch.delenv("BROWSER_APPROACH", raising=False)
    assert main() == 0
    rewards = json.loads((verifier / "reward.json").read_text())
    assert rewards["kernel_mcp_valid"] == 0
    assert rewards["reward"] == expected_reward


def test_call_proxy_filters_tool_list_and_rejects_disallowed_calls(tmp_path):
    server = tmp_path / "server.py"
    server.write_text("""import sys, json
for line in sys.stdin:
    request = json.loads(line)
    result = {"tools": [{"name": name} for name in ["execute_playwright_code", "browser_repl", "webmcp", "get_connection"]]} if request["method"] == "tools/list" else {"content": [{"type": "text", "text": "forwarded"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""")
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
    for index, (name, arguments) in enumerate(
        [
            ("execute_playwright_code", {"session_id": "s"}),
            ("browser_repl", {"session_id": "s"}),
            ("execute_playwright_code", {"session_id": "other"}),
            ("webmcp", {"session_id": "s", "action": "add_custom"}),
            ("webmcp", {"session_id": "s", "action": "list"}),
        ],
        2,
    ):
        requests.append(
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
    log = tmp_path / "calls.jsonl"
    result = subprocess.run(
        [sys.executable, str(BIN / "mcp-call-log"), str(log), sys.executable, str(server)],
        input="\n".join(json.dumps(request) for request in requests) + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "KERNEL_BENCH_ALLOWED_TOOLS": "execute_playwright_code,webmcp",
            "KERNEL_SESSION_ID": "s",
        },
    )
    assert result.returncode == 0, result.stderr
    responses = {
        response["id"]: response["result"]
        for response in map(json.loads, result.stdout.splitlines())
    }
    assert [tool["name"] for tool in responses[1]["tools"]] == ["execute_playwright_code", "webmcp"]
    assert responses[2]["content"][0]["text"] == responses[6]["content"][0]["text"] == "forwarded"
    assert all(responses[index]["isError"] for index in (3, 4, 5))
    calls = list(map(json.loads, log.read_text().splitlines()))
    assert len(calls) == 5
    assert sum(call["rejected"] for call in calls) == 3
