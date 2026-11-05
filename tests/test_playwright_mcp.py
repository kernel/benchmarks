import tomllib

from harness.definitions import load_toolset


def test_apply_adds_the_server_install_and_instruction(task_dir):
    load_toolset("playwright-mcp").apply([task_dir])

    environment = task_dir / "environment"
    assert (environment / "playwright-mcp/bin/browser-mcp").is_file()
    assert "npm install --global @playwright/mcp@0.0.84" in (environment / "Dockerfile").read_text()
    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["environment"]["mcp_servers"][-1]["command"] == "/usr/local/bin/browser-mcp"
    assert config["metadata"]["playwright_mcp_version"] == "0.0.84"
    assert "`browser` MCP server" in (task_dir / "steps/run/instruction.md").read_text()


def test_ref_picks_the_playwright_mcp_version(task_dir):
    load_toolset("playwright-mcp").apply([task_dir], ref="0.0.80")
    assert "@playwright/mcp@0.0.80" in (task_dir / "environment/Dockerfile").read_text()
