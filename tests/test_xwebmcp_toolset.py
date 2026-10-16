import json
import tomllib

import pytest

from harness.definitions import load_suite, load_toolset

toolset = load_toolset("xwebmcp")


@pytest.fixture
def task_dirs(tmp_path, monkeypatch):
    kernel_mcp = load_toolset("kernel-mcp-local")
    monkeypatch.setattr(kernel_mcp, "_fetch_source", lambda ref, dest: dest.mkdir() or "abc123")
    monkeypatch.setattr(toolset, "load_toolset", lambda name: kernel_mcp)

    def fake_fetch(ref, dest):
        (dest / "domains" / "southwest.com").mkdir(parents=True)
        for name in ("search_flights", "select_fare", "old_tool"):
            (dest / "domains" / "southwest.com" / f"{name}.js").write_text("[]")
        registry = [
            {
                "domain": "southwest.com",
                "tool": name,
                "file": f"domains/southwest.com/{name}.js",
                "status": status,
            }
            for name, status in (
                ("search_flights", "qa_passed"),
                ("select_fare", "draft"),
                ("old_tool", "broken"),
            )
        ]
        (dest / "registry.json").write_text(json.dumps({"tools": registry}))
        return "def456"

    monkeypatch.setattr(toolset, "_fetch_source", fake_fetch)
    return load_suite("xwebmcp-tasks").generate(["southwest-round-trip-total"], tmp_path / "tasks")


def test_apply_adds_the_sites_tools_and_registers_them_in_setup(task_dirs):
    toolset.apply(task_dirs, toolsets=["repl"], xwebmcp_ref="my-branch")
    task_dir = task_dirs[0]
    manifest = json.loads((task_dir / "environment/xwebmcp/tools.json").read_text())
    assert manifest["namespace"] == "southwest.com"
    assert manifest["actions"] == ["select_fare", "continue_checkout"]
    assert [tool["name"] for tool in manifest["tools"]] == ["search_flights", "select_fare"]
    assert sorted(path.name for path in (task_dir / "environment/xwebmcp/tools").iterdir()) == [
        "search_flights.js",
        "select_fare.js",
    ]
    setup = (task_dir / "steps/run/workdir/setup.sh").read_text().splitlines()
    assert setup[-2].startswith("/opt/bench/open-start-url") and setup[-1] == "xwebmcp-register"
    tests = (task_dir / "steps/run/tests/test.sh").read_text().splitlines()
    assert tests[-2:] == ["kernel-mcp-verify", "xwebmcp-verify"]
    config = tomllib.loads((task_dir / "task.toml").read_text())
    assert config["metadata"]["xwebmcp_sha"] == "def456"
    assert "browser_repl" in (task_dir / "steps/run/instruction.md").read_text()


def test_a_site_without_tools_is_refused(task_dirs):
    config = (task_dirs[0] / "task.toml").read_text().replace("southwest.com", "example.com")
    (task_dirs[0] / "task.toml").write_text(config)
    with pytest.raises(SystemExit, match="example.com"):
        toolset.apply(task_dirs)
