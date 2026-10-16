import inspect
import json

from harbor.environments.kernel import KernelEnvironment

from harness.environment import KernelTaskEnvironment


def test_task_browser_config_becomes_create_overrides(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        KernelEnvironment,
        "__init__",
        lambda self, environment_dir, **kwargs: captured.update(kwargs),
    )
    (tmp_path / "kernel-config.json").write_text(json.dumps({"stealth": False}))

    KernelTaskEnvironment(environment_dir=tmp_path, session_id="s")

    assert captured == {"session_id": "s", "browser_create_overrides": {"stealth": False}}


def test_harbor_kernel_environment_takes_create_overrides():
    # Harbor upgrades must keep the parameter the subclass passes.
    assert "browser_create_overrides" in inspect.signature(KernelEnvironment.__init__).parameters
