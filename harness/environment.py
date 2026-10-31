"""Harbor's Kernel environment with per-task browser settings.

`harness.configure` writes each task's browser create params to
`environment/kernel-config.json`. Harbor applies one set of environment
kwargs to a whole job, so this subclass reads the file per trial and passes
it to the provider as `browser_create_overrides`.

A `proxy_create` entry in the file is a POST /proxies body rather than a
browser param. Each trial creates its own proxy from it before the browser,
and deletes it after the browser, so no two trials share a proxy's IP. The
proxy's name starts with `proxy_prefix(run_id)` when `BENCH_RUN_ID` is set,
so `delete_run_proxies` can remove any a crashed trial left behind.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from harbor.environments.kernel import KernelEnvironment
from kernel import AsyncKernel, Kernel

CONFIG_FILE = "kernel-config.json"
PROXY_CREATE = "proxy_create"
RUN_ID_ENV = "BENCH_RUN_ID"


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
        config_path = Path(environment_dir) / CONFIG_FILE
        if config_path.is_file():
            overrides = json.loads(config_path.read_text())
            self._proxy_create = overrides.pop(PROXY_CREATE, None)
            kwargs["browser_create_overrides"] = overrides
        super().__init__(environment_dir, *args, **kwargs)

    async def start(self, force_build: bool) -> None:
        if self._proxy_create is not None:
            run_id = os.environ.get(RUN_ID_ENV) or "local"
            name = f"{proxy_prefix(run_id)}{uuid.uuid4().hex[:12]}"
            async with AsyncKernel(api_key=self._api_key) as client:
                proxy = await client.proxies.create(**self._proxy_create, name=name)
            self._proxy_id = proxy.id
            self._browser_create_overrides["proxy_id"] = proxy.id
        await super().start(force_build)

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
