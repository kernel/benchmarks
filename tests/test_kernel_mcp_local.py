import tomllib

from harness.definitions import load_toolset


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
