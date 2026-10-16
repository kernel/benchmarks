import json
import os
import subprocess
import tomllib

from harness.definitions import ROOT, load_suite, load_toolset

toolset = load_toolset("playwright-mcp")


def test_apply_adds_the_server_image_layer_instruction_and_check(tmp_path):
    task_dirs = load_suite("xwebmcp-tasks").generate(["target-earbuds-two-pages"], tmp_path)
    toolset.apply(task_dirs, version="0.0.83")
    task_dir = task_dirs[0]
    environment = task_dir / "environment"
    assert (environment / "playwright-mcp" / "bin" / "mcp-call-log").is_file()
    dockerfile = (environment / "Dockerfile").read_text()
    assert "npm install --global @playwright/mcp@0.0.83" in dockerfile
    assert "playwright-mcp-entrypoint" in dockerfile
    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert [server["name"] for server in config["environment"]["mcp_servers"]] == [
        "bench",
        "playwright",
    ]
    instruction = (task_dir / "steps/run/instruction.md").read_text()
    assert "`browser_snapshot`" in instruction
    assert "webmcp" not in instruction
    assert (task_dir / "steps/run/tests/test.sh").read_text().endswith("playwright-mcp-verify\n")


def test_verify_counts_browser_calls(tmp_path):
    logs, verifier = tmp_path / "logs", tmp_path / "verifier"
    logs.mkdir()
    verifier.mkdir()
    (verifier / "reward.json").write_text('{"reward": 0.0}')
    calls = [
        {"tool": "browser_snapshot", "arguments": {}, "duration_ms": 200},
        {"tool": "browser_click", "arguments": {"ref": "e1"}, "duration_ms": 300, "is_error": True},
        {"tool": "finalize", "arguments": {}},
    ]
    (logs / "calls.jsonl").write_text("\n".join(json.dumps(call) for call in calls) + "\n")
    script = (
        (ROOT / "toolsets/playwright-mcp/image/bin/playwright-mcp-verify")
        .read_text()
        .replace('Path("/var/log/playwright-mcp")', f"Path({str(logs)!r})")
        .replace('Path("/logs/verifier")', f"Path({str(verifier)!r})")
    )
    subprocess.run(["python3", "-c", script], check=True, env=os.environ)
    assert json.loads((verifier / "reward.json").read_text()) == {
        "reward": 0.0,
        "playwright_mcp_valid": 1.0,
    }
    result = json.loads((verifier / "playwright-mcp-result.json").read_text())
    assert result["browser_calls"] == 2
    assert result["tools"] == ["browser_click", "browser_snapshot"]
