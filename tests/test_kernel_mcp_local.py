import json
import os
import subprocess
import tomllib

from harness.definitions import ROOT, load_toolset


def test_apply_adds_server_image_layer_instruction_and_check(task_dir, monkeypatch):
    toolset = load_toolset("kernel-mcp-local")

    def fake_fetch(ref, dest):
        dest.mkdir()
        (dest / "package.json").write_text("{}")
        return "abc123"

    monkeypatch.setattr(toolset, "_fetch_source", fake_fetch)
    toolset.apply([task_dir], ref="main")

    environment = task_dir / "environment"
    assert (environment / "kernel-mcp-local" / "source" / "package.json").is_file()
    assert (environment / "kernel-mcp-local" / "bin" / "kernel-mcp").is_file()
    assert "ENTRYPOINT" in (environment / "Dockerfile").read_text()

    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["environment"]["mcp_servers"] == [
        {"name": "kernel", "transport": "stdio", "command": "/usr/local/bin/kernel-mcp", "args": []}
    ]
    assert config["metadata"]["kernel_mcp_server_sha"] == "abc123"
    assert "execute_playwright_code" in (task_dir / "steps/run/instruction.md").read_text()
    assert (task_dir / "steps/run/tests/test.sh").read_text().endswith("kernel-mcp-verify\n")


def test_repl_toolset_enables_the_repl_and_keeps_other_servers(task_dir, monkeypatch):
    toolset = load_toolset("kernel-mcp-local")
    monkeypatch.setattr(toolset, "_fetch_source", lambda ref, dest: dest.mkdir() or "abc123")
    (task_dir / "task.toml").write_text(
        '[task]\nname = "example"\n\n[environment]\n\n'
        '[[environment.mcp_servers]]\nname = "bench"\ntransport = "stdio"\n'
        'command = "/opt/bench/finalize-mcp"\nargs = []\n'
    )
    toolset.apply([task_dir], toolsets=["repl"])

    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert [server["name"] for server in config["environment"]["mcp_servers"]] == [
        "bench",
        "kernel",
    ]
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    assert dockerfile.endswith("ENV KERNEL_MCP_ENABLED_TOOLSETS=repl\n")
    instruction = (task_dir / "steps/run/instruction.md").read_text()
    assert "`browser_repl` tool" in instruction
    assert "webmcp.listTools()" in instruction
    assert "execute_playwright_code" not in instruction


def test_unknown_toolset_is_refused(task_dir):
    toolset = load_toolset("kernel-mcp-local")
    try:
        toolset.apply([task_dir], toolsets=["shell"])
    except SystemExit as error:
        assert "shell" in str(error)
    else:
        raise AssertionError("expected SystemExit")


def test_verify_reads_the_server_call_log(tmp_path):
    logs, verifier = tmp_path / "kernel-mcp", tmp_path / "verifier"
    logs.mkdir()
    verifier.mkdir()
    (verifier / "reward.json").write_text('{"reward": 1.0}')
    calls = [
        {
            "tool": "browser_repl",
            "arguments": {"session_id": "s1", "code": "await webmcp.invokeTool(r, {})"},
            "duration_ms": 900,
        },
        {
            "tool": "browser_repl",
            "arguments": {"session_id": "s1", "code": "repl.write(1)"},
            "duration_ms": 100,
            "is_error": True,
        },
        {"tool": "finalize", "arguments": {}},
    ]
    (logs / "calls.jsonl").write_text("\n".join(json.dumps(call) for call in calls) + "\n")
    script = (
        (ROOT / "toolsets/kernel-mcp-local/image/bin/kernel-mcp-verify")
        .read_text()
        .replace('Path("/var/log/kernel-mcp")', f"Path({str(logs)!r})")
        .replace('Path("/logs/verifier")', f"Path({str(verifier)!r})")
    )
    env = {**os.environ, "KERNEL_MCP_ENABLED_TOOLSETS": "repl", "KERNEL_SESSION_ID": "s1"}
    subprocess.run(["python3", "-c", script], check=True, env=env)

    assert json.loads((verifier / "reward.json").read_text()) == {
        "reward": 1.0,
        "kernel_mcp_valid": 1.0,
        "webmcp_invoked": 1.0,
    }
    result = json.loads((verifier / "kernel-mcp-result.json").read_text())
    assert result["browser_calls"] == 2
    assert result["browser_call_ms"] == 1000
    assert result["browser_call_errors"] == 1


def test_call_log_passes_messages_through_and_records_calls(tmp_path):
    server = tmp_path / "server.py"
    server.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'isError': m['id'] == 2}}), flush=True)\n"
    )
    log = tmp_path / "calls.jsonl"
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "browser_repl", "arguments": {"code": "1"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "browser_repl", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ]
    result = subprocess.run(
        [
            str(ROOT / "toolsets/kernel-mcp-local/image/bin/mcp-call-log"),
            str(log),
            "python3",
            str(server),
        ],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True,
        text=True,
        check=True,
    )
    assert [json.loads(line)["id"] for line in result.stdout.splitlines()] == [1, 2, 3]
    logged = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(call["tool"], call["is_error"]) for call in logged] == [
        ("browser_repl", False),
        ("browser_repl", True),
    ]
    assert logged[0]["arguments"] == {"code": "1"}
