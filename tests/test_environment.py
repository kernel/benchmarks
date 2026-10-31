import asyncio
import json

import pytest
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


def test_a_proxy_that_cannot_be_created_fails_the_trial_with_proxy_creation_error(
    tmp_path, monkeypatch
):
    calls = []
    env = _environment(tmp_path, monkeypatch, calls)

    async def refuse(self, **body):
        raise ValueError("403")

    monkeypatch.setattr(FakeProxies, "create", refuse)

    with pytest.raises(environment.ProxyCreationError, match="isp proxy: 403"):
        asyncio.run(env.start(force_build=False))
    assert calls == []


class FakeBrowsers:
    def __init__(self, calls, identity):
        self.calls = calls
        self.fs = type("Fs", (), {"write_file": self._write})()
        self.process = type("Process", (), {"exec": self._exec})()
        self.playwright = type("Playwright", (), {"execute": self._execute})()
        self.identity = identity

    async def _write(self, session_id, contents, path):
        self.calls.append(("write", path, contents.decode()))

    async def _exec(self, session_id, **kwargs):
        self.calls.append(("exec", kwargs["args"], kwargs["as_root"]))
        return type("Result", (), {"exit_code": 0})()

    async def _execute(self, session_id, code):
        return type("Result", (), {"success": True, "result": self.identity})()


def _chromium_environment(tmp_path, monkeypatch, chromium_env, identity, calls):
    async def create(self):
        return type("Response", (), {"session_id": "s1"})()

    def init(self, environment_dir, **kwargs):
        self._client = type("Client", (), {"browsers": FakeBrowsers(calls, identity)})()
        self.logger = type("Logger", (), {"info": lambda self, message: None})()

    monkeypatch.setattr(KernelEnvironment, "__init__", init)
    monkeypatch.setattr(KernelEnvironment, "_create_browser", create)
    (tmp_path / "kernel-config.json").write_text(json.dumps({"chromium_env": chromium_env}))
    return KernelTaskEnvironment(environment_dir=tmp_path)


def test_chromium_restarts_with_the_trials_launcher_variables(tmp_path, monkeypatch):
    calls = []
    personas = [
        {"KERNEL_WEBGL_RENDERER": "ANGLE (NVIDIA)", "KERNEL_CANVAS_AA_OFFSET": "-2"},
        {"KERNEL_WEBGL_RENDERER": "ANGLE (NVIDIA)", "KERNEL_CANVAS_AA_OFFSET": "3"},
    ]
    identity = {"userAgent": "X11; Linux", "renderer": "ANGLE (NVIDIA)"}
    env = _chromium_environment(tmp_path, monkeypatch, personas, identity, calls)

    asyncio.run(env._create_browser())

    program = next(contents for kind, path, *contents in calls if path.endswith(".conf"))
    assert "KERNEL_WEBGL_RENDERER='ANGLE (NVIDIA)'" in program[0]
    assert ("exec", ["/tmp/bench-restart-chromium.sh"], True) in calls


def test_a_persona_that_did_not_apply_fails_the_trial(tmp_path, monkeypatch):
    identity = {"userAgent": "X11; Linux x86_64", "renderer": "ANGLE (Mesa)"}
    env = _chromium_environment(tmp_path, monkeypatch, {"KERNEL_MAC_PERSONA": "1"}, identity, [])

    with pytest.raises(environment.ChromiumEnvError, match="macOS user agent"):
        asyncio.run(env._create_browser())
