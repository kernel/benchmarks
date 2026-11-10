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
            "rules": [{"path": "answer", "equals": "pinned-answer"}],
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
    # The agent's image has no answer rules or grader; they arrive with the tests.
    image = [path for path in (task_dir / "environment").rglob("*") if path.is_file()]
    assert sorted(path.name for path in image) == [
        "Dockerfile",
        "api.mjs",
        "finalize-mcp",
        "setup.mjs",
        "start.json",
    ]
    assert not any("pinned-answer" in path.read_text() for path in image)
    start = json.loads((task_dir / "environment/bench/start.json").read_text())
    assert start["intent"] == {
        "id": "test",
        "domain": "example.com",
        "start_url": "https://example.com",
        "entry_tool": None,
    }
    tests = task_dir / "steps/run/tests"
    assert "pinned-answer" in (tests / "task.json").read_text()
    assert (tests / "upstream/bench/grade.mjs").exists()
    assert {"score.mjs", "api.mjs"} <= {path.name for path in tests.iterdir()}
    assert "node /tests/score.mjs" in (tests / "test.sh").read_text()


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
    for code in (
        "return await webmcp.listTools()",
        "return await webmcp.invokeTool(ref, {})",
        "return await webmcp?.listTools?.()",
        "return await webmcp['listTools']()",
        "const list = webmcp.listTools.bind(webmcp); return await list()",
    ):
        violation = {**call, "arguments": {"session_id": "s", "code": code}}
        assert not check([violation], ["playwright"], "s", "playwright")["valid"]
    webmcp = {"tool": "webmcp", "arguments": {"session_id": "s", "action": "invoke"}}
    hybrid = ["playwright", "repl", "computer", "webmcp"]
    result = check([webmcp], hybrid, "s", "treatment")
    assert result["valid"] and result["webmcp_invoked"]
    # Calls the proxy rejected never reached the browser: counted, not invalidating.
    rejected = [
        {"tool": "webmcp", "arguments": {"session_id": "s", "action": "list_custom"}},
        {"tool": "execute_playwright_code", "arguments": {"code": "return 1"}},
        {"tool": "manage_browsers", "arguments": {"session_id": "s"}},
    ]
    result = check(
        [{**entry, "rejected": True} for entry in rejected] + [call], hybrid, "s", "control"
    )
    assert result["valid"] and result["rejected_calls"] == 3 and result["browser_calls"] == 1
    only_rejected = check([{**rejected[1], "rejected": True}], hybrid, "s", "control")
    assert not only_rejected["valid"]
    blocked = {**call, "arguments": {"session_id": "s", "code": "return await webmcp.listTools()"}}
    assert check([{**blocked, "rejected": True}, call], ["playwright"], "s", "playwright")["valid"]


@pytest.mark.parametrize("approach", ["playwright", None])
def test_malformed_logged_calls_are_checked_instead_of_crashing_the_verifier(approach):
    check = runpy.run_path(str(BIN / "kernel-mcp-verify"))["check_calls"]
    malformed = [
        {"tool": "execute_playwright_code", "arguments": {"session_id": "s", "code": None}},
        {"tool": "execute_playwright_code", "arguments": {"session_id": "s", "code": 7}},
        {"tool": "execute_playwright_code", "arguments": "return 1"},
        {"tool": "execute_playwright_code", "arguments": {"session_id": ["s"]}},
    ]
    result = check(malformed[:2], ["playwright"], "s", approach)
    assert result["valid"] and result["browser_calls"] == 2
    result = check(malformed, ["playwright"], "s", approach)
    assert not result["checks"]["used_assigned_browser"]
    assert result["called_session_ids"] == ["None", "s"]


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
            ("execute_playwright_code", {"session_id": "s", "code": "await webmcp.listTools()"}),
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
    assert "session-id" in responses[4]["content"][0]["text"]
    assert "list and invoke" in responses[5]["content"][0]["text"]
    # WebMCP from code is forwarded here because this arm has the webmcp tool.
    assert responses[7]["content"][0]["text"] == "forwarded"
    calls = list(map(json.loads, log.read_text().splitlines()))
    assert len(calls) == 6
    assert sum(call["rejected"] for call in calls) == 3


def test_call_proxy_blocks_webmcp_from_code_in_the_playwright_arm(tmp_path):
    server = tmp_path / "server.py"
    server.write_text("""import sys, json
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"content": [{"type": "text", "text": "forwarded"}]}}), flush=True)
""")
    codes = [
        "return await webmcp.listTools()",
        "return await webmcp?.listTools?.()",
        "const helper = webmcp; return await helper['invokeTool'](ref)",
        "return await page.title()",
    ]
    requests = [
        {
            "jsonrpc": "2.0",
            "id": index,
            "method": "tools/call",
            "params": {
                "name": "execute_playwright_code",
                "arguments": {"session_id": "s", "code": code},
            },
        }
        for index, code in enumerate(codes)
    ]
    log = tmp_path / "calls.jsonl"
    result = subprocess.run(
        [sys.executable, str(BIN / "mcp-call-log"), str(log), sys.executable, str(server)],
        input="\n".join(json.dumps(request) for request in requests) + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "KERNEL_BENCH_ALLOWED_TOOLS": "execute_playwright_code",
            "KERNEL_SESSION_ID": "s",
        },
    )
    assert result.returncode == 0, result.stderr
    responses = {
        response["id"]: response["result"]
        for response in map(json.loads, result.stdout.splitlines())
    }
    for index in range(3):
        assert responses[index]["isError"] and "WebMCP" in responses[index]["content"][0]["text"]
    assert responses[3]["content"][0]["text"] == "forwarded"
    calls = list(map(json.loads, log.read_text().splitlines()))
    assert [call["rejected"] for call in calls] == [True, True, True, False]
    check = runpy.run_path(str(BIN / "kernel-mcp-verify"))["check_calls"]
    assert check(calls, ["playwright"], "s", "playwright")["valid"]


def test_call_proxy_refuses_batches_and_does_not_log_server_requests_as_results(tmp_path):
    server = tmp_path / "server.py"
    server.write_text("""import sys, json
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "method": "ping"}), flush=True)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"isError": True, "content": []}}), flush=True)
""")
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "execute_playwright_code",
            "arguments": {"session_id": "s", "code": "return 1"},
        },
    }
    disallowed = {**call, "id": 2, "params": {"name": "manage_browsers", "arguments": {}}}
    log = tmp_path / "calls.jsonl"
    result = subprocess.run(
        [sys.executable, str(BIN / "mcp-call-log"), str(log), sys.executable, str(server)],
        input=json.dumps([disallowed]) + "\n" + json.dumps(call) + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        env={
            **os.environ,
            "KERNEL_BENCH_ALLOWED_TOOLS": "execute_playwright_code",
            "KERNEL_SESSION_ID": "s",
        },
    )
    assert result.returncode == 0, result.stderr
    refusal, ping, response = map(json.loads, result.stdout.splitlines())
    assert refusal["id"] is None and refusal["error"]["code"] == -32600
    assert ping["method"] == "ping" and response["result"]["isError"]
    (logged,) = map(json.loads, log.read_text().splitlines())
    assert logged["tool"] == "execute_playwright_code" and logged["is_error"]


PLAYWRIGHT_TOOL = {
    "name": "execute_playwright_code",
    "description": (
        "execute arbitrary playwright code in a fresh execution context against the browser. "
        "for reusable site actions, check `webmcp.listTools()` first and prefer a suitable "
        "structured tool; use playwright when none is exposed. does not create or delete "
        "browsers -- use manage_browsers for session lifecycle. it has access to `page`, "
        "`context`, `browser`, and `webmcp` variables.\n\n"
        "every call runs in an executor: a dedicated node.js process. calls on different "
        "executors run concurrently."
    ),
    "inputSchema": {
        "properties": {
            "code": {
                "description": (
                    "playwright/typescript code with `page`, `context`, `browser`, and "
                    "browser-wide `webmcp` helpers in scope; the value you `return` is sent "
                    "back as the tool result. return a focused `ariaSnapshot()`, e.g. `await "
                    "page.locator('main').ariaSnapshot()`. a global webmcp object is available "
                    "for discovering tools. check the invocation status. do not invoke the "
                    "tool again to submit it."
                )
            },
            "session_id": {"description": "browser session id or name."},
        }
    },
}


@pytest.mark.parametrize(
    "allowed", ["", "execute_playwright_code,webmcp", "execute_playwright_code"]
)
def test_call_proxy_withholds_guidance_about_tools_the_arm_lacks(tmp_path, allowed):
    server = tmp_path / "server.py"
    server.write_text(f"""import sys, json
tool = {PLAYWRIGHT_TOOL!r}
for line in sys.stdin:
    request = json.loads(line)
    result = {{"instructions": "1. webmcp: prefer this. create sessions with manage_browsers."}} if request["method"] == "initialize" else {{"tools": [tool]}}
    print(json.dumps({{"jsonrpc": "2.0", "id": request["id"], "result": result}}), flush=True)
""")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    result = subprocess.run(
        [
            sys.executable,
            str(BIN / "mcp-call-log"),
            str(tmp_path / "calls.jsonl"),
            sys.executable,
            str(server),
        ],
        input="\n".join(json.dumps(request) for request in requests) + "\n",
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "KERNEL_BENCH_ALLOWED_TOOLS": allowed, "KERNEL_SESSION_ID": "s"},
    )
    assert result.returncode == 0, result.stderr
    responses = {
        response["id"]: response["result"]
        for response in map(json.loads, result.stdout.splitlines())
    }
    # Arms without an allowlist, such as ClawBench's, get the server's messages as sent.
    assert ("instructions" in responses[1]) == (allowed == "")
    (tool,) = responses[2]["tools"]
    if "webmcp" in allowed or not allowed:
        assert tool == PLAYWRIGHT_TOOL
        return
    assert "webmcp" not in json.dumps(tool).lower()
    assert tool["description"] == (
        "execute arbitrary playwright code in a fresh execution context against the browser."
        "\n\nevery call runs in an executor: a dedicated node.js process. calls on different "
        "executors run concurrently."
    )
    properties = tool["inputSchema"]["properties"]
    assert properties["code"]["description"] == (
        "playwright/typescript code with `page`, `context`, `browser` in scope; the value you "
        "`return` is sent back as the tool result. return a focused `ariaSnapshot()`, e.g. "
        "`await page.locator('main').ariaSnapshot()`."
    )
    assert properties["session_id"] == PLAYWRIGHT_TOOL["inputSchema"]["properties"]["session_id"]
