import asyncio
import json

from harbor.environments.kernel import KernelEnvironment

from harness import environment
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


class FakeProxies:
    def __init__(self, calls):
        self.calls = calls

    async def create(self, **body):
        self.calls.append(("create", body))
        return type("Proxy", (), {"id": "p1"})()

    async def delete(self, proxy_id):
        self.calls.append(("delete", proxy_id))


class FakeKernel:
    calls: list = []

    def __init__(self, **kwargs):
        self.proxies = FakeProxies(FakeKernel.calls)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _environment(tmp_path, monkeypatch, calls):
    def init(self, environment_dir, **kwargs):
        self._api_key = "k"
        self._browser_create_overrides = kwargs["browser_create_overrides"]

    async def start(self, force_build):
        calls.append(("browser", dict(self._browser_create_overrides)))

    async def stop(self, delete):
        calls.append(("stop browser", None))

    monkeypatch.setattr(KernelEnvironment, "__init__", init)
    monkeypatch.setattr(KernelEnvironment, "start", start)
    monkeypatch.setattr(KernelEnvironment, "stop", stop)
    monkeypatch.setattr(environment, "AsyncKernel", FakeKernel)
    FakeKernel.calls = calls
    config = {"stealth": True, "proxy_create": {"type": "isp", "config": {"country": "US"}}}
    (tmp_path / "kernel-config.json").write_text(json.dumps(config))
    return KernelTaskEnvironment(environment_dir=tmp_path)


def test_each_trial_creates_its_own_proxy_and_deletes_it_after_the_browser(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("BENCH_RUN_ID", "r1")
    env = _environment(tmp_path, monkeypatch, calls)

    asyncio.run(env.start(force_build=False))
    asyncio.run(env.stop(delete=True))

    (kind, body), browser, stop, delete = calls
    assert kind == "create"
    assert body["type"] == "isp" and body["name"].startswith("bench-r1-")
    assert browser == ("browser", {"stealth": True, "proxy_id": "p1"})
    assert stop == ("stop browser", None)
    assert delete == ("delete", "p1")


def test_proxy_names_differ_between_trials(tmp_path, monkeypatch):
    calls = []
    for _ in range(2):
        asyncio.run(_environment(tmp_path, monkeypatch, calls).start(force_build=False))
    names = [body["name"] for kind, body in calls if kind == "create"]
    assert len(set(names)) == 2
