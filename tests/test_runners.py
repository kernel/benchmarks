import json
from types import SimpleNamespace

import pytest

from harness import cli, definitions
from harness.definitions import load_arm, load_benchmark, load_runner
from harness.run import PreparedArm, harbor_command, prepare, run


@pytest.mark.parametrize(
    "runner,agent,model",
    [
        ("codex", "codex", "gpt-6-luna"),
        ("claude", "claude-code", "claude-sonnet-5"),
        ("pi", "pi", "anthropic/claude-sonnet-5"),
    ],
)
def test_runner_applies_to_every_browser_approach(runner, agent, model):
    for name in load_benchmark("public-web").arms:
        baseline = load_arm(name)
        arm = load_arm(name, runner=runner)
        assert (arm.runner, arm.agent, arm.model) == (runner, agent, model)
        assert arm.browser == baseline.browser
        assert (arm.toolset, arm.toolset_ref, arm.toolset_args) == (
            baseline.toolset,
            baseline.toolset_ref,
            baseline.toolset_args,
        )
    assert load_arm("public-control").agent == "codex"


def test_model_override_does_not_change_runner_or_leak_old_agent_options():
    arm = load_arm("public-control", runner="pi", model="openai/gpt-5.4")
    assert (arm.agent, arm.agent_version, arm.model) == ("pi", "0.85.1", "openai/gpt-5.4")
    assert arm.agent_kwargs == {"thinking": "medium"}
    assert load_runner("pi").model == "anthropic/claude-sonnet-5"
    assert load_arm("public-control", model="gpt-5.4").agent == "codex"
    # Overrides also replace inline agent settings on existing benchmarks.
    legacy = load_arm("claude-kernel-mcp", runner="pi", model="openai/gpt-5.4")
    assert (legacy.agent, legacy.agent_version) == ("pi", "0.85.1")
    assert load_arm("claude-kernel-mcp").agent == "claude-code"


def test_runner_options_match_the_pinned_harbor_adapters():
    from harbor.agents.installed.claude_code import ClaudeCode, ClaudeCodeOptions
    from harbor.agents.installed.codex import Codex, CodexOptions
    from harbor.agents.installed.pi import _PI_MCP_ADAPTER_PI_VERSIONS, Pi, PiOptions
    from packaging.version import Version

    for name, agent, options in [
        ("codex", Codex, CodexOptions),
        ("claude", ClaudeCode, ClaudeCodeOptions),
        ("pi", Pi, PiOptions),
    ]:
        runner = load_runner(name)
        options.model_validate({"version": runner.agent_version, **runner.agent_kwargs})
        assert agent.capabilities.mcp_servers
    assert Version(load_runner("pi").agent_version) in _PI_MCP_ADAPTER_PI_VERSIONS


@pytest.mark.parametrize(
    "model", ["", "--help", "openai/model name", "openai/$(id)", "openai/a;echo", "openai/a\nb"]
)
def test_model_ids_cannot_become_shell_commands(model):
    with pytest.raises(SystemExit, match="Model must be an ID"):
        load_arm("public-control", runner="pi", model=model)


def test_invalid_runner_and_pi_model_fail_before_task_generation():
    with pytest.raises(SystemExit, match="Not found"):
        load_arm("public-control", runner="missing")
    with pytest.raises(SystemExit, match="Runner names"):
        load_arm("public-control", runner="../codex")
    for model in ["claude-sonnet-5", "openai/", "openai/--help"]:
        with pytest.raises(SystemExit, match="provider/model"):
            load_arm("public-control", runner="pi", model=model)


def test_runner_cannot_be_combined_with_inline_agent_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(definitions, "ROOT", tmp_path)
    (tmp_path / "arms").mkdir()
    (tmp_path / "arms/mixed.toml").write_text('runner = "pi"\nagent = "codex"\n')
    with pytest.raises(SystemExit, match="not both"):
        load_arm("mixed")


def test_prepare_resolves_selected_runner_for_all_arms(tmp_path, monkeypatch):
    import harness.run as module

    def generate(task_ids, output):
        (output / "one").mkdir(parents=True)
        return [output / "one"]

    monkeypatch.setattr(module, "load_suite", lambda name: SimpleNamespace(generate=generate))
    monkeypatch.setattr(module, "configure_tasks", lambda tasks, browser: {})
    monkeypatch.setattr(
        module, "load_toolset", lambda name: SimpleNamespace(apply=lambda *args, **kwargs: None)
    )
    prepared = prepare("public-web", None, None, tmp_path, runner="claude", model="claude-opus-5")
    assert len(prepared) == 3
    for item in prepared:
        command = harbor_command(item, tmp_path, item.arm.name, 1)
        assert command[command.index("--agent") + 1] == "claude-code"
        assert command[command.index("--model") + 1] == "claude-opus-5"
        assert "disable_web_search=true" in command


@pytest.mark.parametrize("command", ["prepare", "run"])
def test_cli_passes_runner_and_model_to_the_harness(command, tmp_path, monkeypatch):
    calls = []

    def capture(*args, **kwargs):
        calls.append(kwargs)
        return [] if command == "prepare" else {}

    monkeypatch.setattr(cli, command, capture)
    args = [command, "public-web", "--runner", "pi", "--model", "openai/gpt-5.4"]
    if command == "prepare":
        args += ["--out", str(tmp_path)]
    assert cli.main(args) == 0
    assert calls == [{"runner": "pi", "model": "openai/gpt-5.4"}]


def test_run_records_resolved_runner_and_refuses_reusing_an_experiment_id(tmp_path, monkeypatch):
    import harness.run as module

    monkeypatch.setattr(module.preflight, "check_kernel_project", lambda: None)
    monkeypatch.setattr(module, "load_suite", lambda name: SimpleNamespace(preflight=lambda: None))
    prepared = [
        PreparedArm(load_arm(name, runner="pi", model="openai/gpt-5.4"), tmp_path / name, {})
        for name in load_benchmark("public-web").arms
    ]
    monkeypatch.setattr(module, "prepare", lambda *args, **kwargs: prepared)
    commands = []

    def start(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(module.subprocess, "Popen", start)
    statuses = run(
        "public-web",
        None,
        None,
        tmp_path,
        tmp_path,
        "model-test",
        1,
        runner="pi",
        model="openai/gpt-5.4",
    )
    assert len(statuses) == len(commands) == 3
    manifest = json.loads((tmp_path / "run-model-test.json").read_text())
    for entry in manifest.values():
        assert entry["runner"] == {
            "preset": "pi",
            "agent": "pi",
            "model": "openai/gpt-5.4",
            "agent_version": "0.85.1",
            "agent_kwargs": {"thinking": "medium"},
        }
    with pytest.raises(SystemExit, match="new --run-id"):
        run("public-web", None, None, tmp_path, tmp_path, "model-test", 1)
    assert len(commands) == 3
