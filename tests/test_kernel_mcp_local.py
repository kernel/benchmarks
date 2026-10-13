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
