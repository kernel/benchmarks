"""Harbor's Kernel environment with per-task browser settings.

`harness.configure` writes each task's browser create params to
`environment/kernel-config.json`. Harbor applies one set of environment
kwargs to a whole job, so this subclass reads the file per trial and passes
it to the provider as `browser_create_overrides`.

A `proxy_create` entry in the file is a POST /proxies body rather than a
browser param. Each trial creates its own proxy from it before the browser,
and deletes it after the browser, so no two trials share a proxy's IP. The
proxy's name starts with `proxy_prefix(run_id)` when `BENCH_RUN_ID` is set,
so `delete_run_proxies` can remove any a crashed trial left behind. A
trial whose proxy cannot be created fails with `ProxyCreationError`, so
reports can count those trials apart from other infrastructure failures.

Two entries change the browser's VM right after it is created, before the
task container starts and before the first page load. `chromium_env` is a set
of `KERNEL_*` launcher variables, or a list of sets of which each trial picks
one at random: Chromium is restarted with them and the page is checked for
the persona or WebGL renderer they ask for. `vm_setup` is a list of shell
commands then run as root. A trial where either fails fails with
`VmSetupError`.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shlex
import uuid
from pathlib import Path
from typing import Any

from harbor.environments.kernel import KernelEnvironment
from kernel import AsyncKernel, Kernel

CONFIG_FILE = "kernel-config.json"
PROXY_CREATE = "proxy_create"
CHROMIUM_ENV = "chromium_env"
VM_SETUP = "vm_setup"
RUN_ID_ENV = "BENCH_RUN_ID"


class ProxyCreationError(RuntimeError):
    """The trial's proxy could not be created, so no browser was started."""


class VmSetupError(RuntimeError):
    """The browser's VM could not be set up for the trial before the first page load."""


SUPERVISOR_PROGRAM = """[program:chromium]
command=/usr/bin/env {env} /usr/local/bin/chromium-launcher
autostart=false
autorestart=true
startsecs=0
stopsignal=KILL
stopasgroup=true
killasgroup=true
stopwaitsecs=1
stdout_logfile=/var/log/supervisord/chromium
redirect_stderr=true
"""

RESTART_CHROMIUM = """#!/bin/bash
set -euo pipefail
mv /tmp/bench-chromium.conf /etc/supervisor/conf.d/services/chromium.conf
supervisorctl reread
supervisorctl update
sleep 2
supervisorctl start chromium
sleep 3
supervisorctl status chromium
"""

PAGE_IDENTITY = """return await page.evaluate(() => {
  const gl = document.createElement("canvas").getContext("webgl");
  const info = gl && gl.getExtension("WEBGL_debug_renderer_info");
  return {
    userAgent: navigator.userAgent,
    renderer: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : null,
  };
});"""


def proxy_prefix(run_id: str) -> str:
    return f"bench-{run_id}-"


def delete_run_proxies(run_id: str) -> list[str]:
    """Delete the proxies trials of `run_id` created and did not delete themselves."""
    client = Kernel()
    prefix = proxy_prefix(run_id)
    leftovers = [
        proxy for proxy in client.proxies.list(query=prefix) if proxy.name.startswith(prefix)
    ]
    for proxy in leftovers:
        client.proxies.delete(proxy.id)
    return [proxy.name for proxy in leftovers]


class KernelTaskEnvironment(KernelEnvironment):
    def __init__(self, environment_dir: Path, *args: Any, **kwargs: Any) -> None:
        self._proxy_create: dict[str, Any] | None = None
        self._proxy_id: str | None = None
        self._chromium_env: dict[str, str] | None = None
        self._vm_setup: list[str] = []
        config_path = Path(environment_dir) / CONFIG_FILE
        if config_path.is_file():
            overrides = json.loads(config_path.read_text())
            self._proxy_create = overrides.pop(PROXY_CREATE, None)
            chromium_env = overrides.pop(CHROMIUM_ENV, None)
            if isinstance(chromium_env, list):
                chromium_env = random.choice(chromium_env)
            self._chromium_env = chromium_env
            self._vm_setup = overrides.pop(VM_SETUP, None) or []
            kwargs["browser_create_overrides"] = overrides
        super().__init__(environment_dir, *args, **kwargs)

    async def start(self, force_build: bool) -> None:
        if self._proxy_create is not None:
            run_id = os.environ.get(RUN_ID_ENV) or "local"
            name = f"{proxy_prefix(run_id)}{uuid.uuid4().hex[:12]}"
            try:
                async with AsyncKernel(api_key=self._api_key) as client:
                    proxy = await client.proxies.create(**self._proxy_create, name=name)
            except Exception as error:
                raise ProxyCreationError(
                    f"Could not create {self._proxy_create.get('type')} proxy: {error}"
                ) from error
            self._proxy_id = proxy.id
            self._browser_create_overrides["proxy_id"] = proxy.id
        await super().start(force_build)

    def _browser_create_kwargs(self) -> dict[str, Any]:
        kwargs = super()._browser_create_kwargs()
        # GPU browsers have a fixed memory size and reject a memory request.
        if kwargs.get("gpu"):
            kwargs.pop("memory", None)
        return kwargs

    async def _create_browser(self) -> Any:
        response = await super()._create_browser()
        try:
            if self._chromium_env:
                await self._restart_chromium(response.session_id)
            for command in self._vm_setup:
                await self._run_as_root(response.session_id, command)
        except Exception as error:
            raise VmSetupError(f"Could not set up the browser VM: {error}") from error
        return response

    async def _run_as_root(self, session_id: str, command: str) -> None:
        result = await self._client.browsers.process.exec(
            session_id, command="bash", args=["-c", command], as_root=True, timeout_sec=60
        )
        if result.exit_code != 0:
            raise RuntimeError(f"{command!r} exited {result.exit_code}")
        self.logger.info(f"Ran in the browser VM: {command}")

    async def _restart_chromium(self, session_id: str) -> None:
        env = " ".join(
            f"{key}={shlex.quote(str(value))}" for key, value in self._chromium_env.items()
        )
        browsers = self._client.browsers
        await browsers.fs.write_file(
            session_id, SUPERVISOR_PROGRAM.format(env=env).encode(), path="/tmp/bench-chromium.conf"
        )
        await browsers.fs.write_file(
            session_id, RESTART_CHROMIUM.encode(), path="/tmp/bench-restart-chromium.sh"
        )
        result = await browsers.process.exec(
            session_id,
            command="bash",
            args=["/tmp/bench-restart-chromium.sh"],
            as_root=True,
            timeout_sec=60,
        )
        if result.exit_code != 0:
            raise RuntimeError(f"restart script exited {result.exit_code}")
        identity = None
        for _ in range(15):
            check = await browsers.playwright.execute(session_id, code=PAGE_IDENTITY)
            if check.success:
                identity = check.result or {}
                break
            await asyncio.sleep(2)
        if identity is None:
            raise RuntimeError("Chromium did not come back after the restart")
        if (
            self._chromium_env.get("KERNEL_MAC_PERSONA")
            and "Macintosh" not in identity["userAgent"]
        ):
            raise RuntimeError(f"expected a macOS user agent, got {identity['userAgent']}")
        renderer = self._chromium_env.get("KERNEL_WEBGL_RENDERER")
        if renderer and identity["renderer"] != renderer:
            raise RuntimeError(f"expected WebGL renderer {renderer}, got {identity['renderer']}")
        self.logger.info(f"Chromium restarted with {sorted(self._chromium_env)}: {identity}")

    async def stop(self, delete: bool) -> None:
        try:
            await super().stop(delete)
        finally:
            if self._proxy_id is not None:
                try:
                    async with AsyncKernel(api_key=self._api_key) as client:
                        await client.proxies.delete(self._proxy_id)
                    self._proxy_id = None
                except Exception as error:
                    self.logger.error(f"Failed to delete Kernel proxy {self._proxy_id}: {error}")
