import pytest

from harness import run
from harness.definitions import Arm, Benchmark


def test_toolset_args_override_only_the_keys_an_arm_sets(tmp_path, monkeypatch):
    applied = {}

    class Suite:
        @staticmethod
        def generate(task_ids, out_dir, split="test"):
            (out_dir / f"task-{split}").mkdir(parents=True)
            return [out_dir / f"task-{split}"]

    class Toolset:
        def __init__(self, name):
            self.name = name

        def apply(self, task_dirs, ref=None, **args):
            applied[self.name] = ({path.name for path in task_dirs}, args)

    arms = {
        "control": Arm(name="control", agent="codex", model="m", toolset="plain"),
        "treatment": Arm(
            name="treatment",
            agent="codex",
            model="m",
            toolset="extra",
            toolset_args={"xwebmcp_ref": "main"},
        ),
    }
    monkeypatch.setattr(
        run,
        "load_benchmark",
        lambda name: Benchmark(name=name, suite="s", arms=list(arms), suite_args={"split": "test"}),
    )
    monkeypatch.setattr(run, "load_arm", lambda name: arms[name])
    monkeypatch.setattr(run, "load_suite", lambda name: Suite)
    monkeypatch.setattr(run, "load_toolset", Toolset)
    monkeypatch.setattr(run, "configure_tasks", lambda task_dirs, browser: {})

    run.prepare("b", None, None, tmp_path, None, {"split": "train"}, {"xwebmcp_ref": "branch"})
    assert applied == {
        "plain": ({"task-train"}, {}),
        "extra": ({"task-train"}, {"xwebmcp_ref": "branch"}),
    }

    applied.clear()
    run.prepare("b", ["control"], None, tmp_path / "control", None, None, {"xwebmcp_ref": "x"})
    assert applied == {"plain": ({"task-test"}, {})}

    with pytest.raises(SystemExit, match="unknown_arg"):
        run.prepare("b", None, None, tmp_path / "again", None, None, {"unknown_arg": "x"})


def test_harbor_command_passes_agent_kwargs_as_json(tmp_path):
    arm = Arm(
        name="a",
        agent="codex",
        model="gpt-6-luna",
        toolset="t",
        agent_version="0.163.0",
        agent_kwargs={"disable_web_search": True},
    )
    command = run.harbor_command(run.PreparedArm(arm, tmp_path, {}), tmp_path, "job", 2, 3)
    assert "version=0.163.0" in command
    assert "disable_web_search=true" in command
    assert command[command.index("--n-attempts") + 1] == "3"
