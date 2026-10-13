import tomllib

from harness.definitions import load_suite

TASK = "v2-1134-chapter-finder-redcross"


def test_generate_runs_on_the_harbor_browser_without_tool_wiring(tmp_path):
    [task] = load_suite("clawbench").generate([TASK], tmp_path / "tasks")

    environment = task / "environment"
    assert "kernel-mcp" not in (environment / "Dockerfile").read_text()
    assert (environment / "harbor" / "harbor-browser.py").is_file()
    for script in ("workdir/setup.sh", "tests/test.sh"):
        text = (task / "steps" / "run" / script).read_text()
        assert "harbor-browser.py" in text
        assert "kernel-browser.py" not in text

    config = tomllib.loads((task / "task.toml").read_text())
    assert "mcp_servers" not in config["environment"]
    instruction = (task / "steps" / "run" / "instruction.md").read_text()
    assert "Harbor browser runtime" not in instruction
    assert "9223" not in instruction
