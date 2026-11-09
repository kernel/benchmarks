"""Cloud browser providers that a trial can drive over CDP.

Each provider creates one browser session from an arm's create params and
releases it when the trial ends. The params are the provider's own session
create body, so an arm states exactly which stealth, proxy and CAPTCHA
settings it asked for. Sessions must outlive a CDP disconnect: the start page
is opened, the agent connects, and the verifier connects, each separately.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from kernel import AsyncKernel


@dataclass(frozen=True)
class Session:
    id: str
    cdp_url: str


class Provider:
    env: tuple[str, ...] = ()

    async def create(self, params: dict[str, Any]) -> Session:
        raise NotImplementedError

    async def release(self, session_id: str) -> None:
        raise NotImplementedError


class KernelProvider(Provider):
    """A Kernel browser. A `proxy_create` param, a POST /proxies body, creates a
    proxy for the session and deletes it after the browser, as in
    harness.environment."""

    env = ("KERNEL_API_KEY",)

    def __init__(self) -> None:
        self._proxies: dict[str, str] = {}

    async def _create_browser(
        self, client: AsyncKernel, params: dict[str, Any], proxy: dict[str, Any] | None
    ) -> Session:
        from harness.environment import RUN_ID_ENV, proxy_prefix

        if proxy is None:
            browser = await client.browsers.create(**params)
            return Session(browser.session_id, browser.cdp_ws_url)
        run_id = os.environ.get(RUN_ID_ENV) or "local"
        name = f"{proxy_prefix(run_id)}{uuid.uuid4().hex[:12]}"
        kernel_proxy = await client.proxies.create(**proxy, name=name)
        try:
            browser = await client.browsers.create(**params, proxy_id=kernel_proxy.id)
        except Exception:
            await client.proxies.delete(kernel_proxy.id)
            raise
        self._proxies[browser.session_id] = kernel_proxy.id
        return Session(browser.session_id, browser.cdp_ws_url)

    async def create(self, params: dict[str, Any]) -> Session:
        params = dict(params)
        proxy = params.pop("proxy_create", None)
        async with AsyncKernel() as client:
            return await self._create_browser(client, params, proxy)

    async def release(self, session_id: str) -> None:
        # The browser goes first: without its proxy it would reach sites directly.
        async with AsyncKernel() as client:
            await client.browsers.delete_by_id(session_id)
            proxy_id = self._proxies.pop(session_id, None)
            if proxy_id is not None:
                await client.proxies.delete(proxy_id)


class KernelEvervaultProvider(KernelProvider):
    """A Kernel browser whose whole session goes through Evervault's browser proxy.

    Each session mints its own Evervault Browser Tokens session, which carries
    the proxy credentials, and creates a Kernel custom proxy from them with
    Evervault's CA bundle, since the proxy terminates TLS. The mint holds one
    placeholder credential: the session is only used for its proxy, and no
    real value is ever revealed. `params` holds the Kernel browser create
    params, plus an `evervault` table with the mint's `ttl`, the `hosts` it
    may reveal to, optionally `relay_host` to use another region's relay than
    the one the mint returns (the credentials work on any), and optionally
    `proxy_settings` for Evervault's egress. A residential `proxy_create`, as
    the `geo` configurator writes it, becomes Evervault's residential egress in
    that country instead of a Kernel proxy.
    """

    env = ("KERNEL_API_KEY", "EV_APP_UUID", "EV_API_KEY")
    mint_url = "https://api.evervault.com/browser-tokens"
    ca_url = "https://ca.evervault.com"

    def __init__(self) -> None:
        super().__init__()
        self._ca_bundle: str | None = None

    async def _mint(self, settings: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ttl": settings.get("ttl", 900),
            "data": [{"id": "bench", "category": "credential.password", "value": "bench"}],
            "permissions": [{"action": "decrypt", "target": host} for host in settings["hosts"]],
        }
        if "proxy_settings" in settings:
            body["proxySettings"] = settings["proxy_settings"]
        async with httpx.AsyncClient(timeout=30) as client:
            if self._ca_bundle is None:
                response = await client.get(self.ca_url)
                response.raise_for_status()
                self._ca_bundle = response.text
            response = await client.post(
                self.mint_url,
                json=body,
                auth=(os.environ["EV_APP_UUID"], os.environ["EV_API_KEY"]),
            )
        # The response body carries the proxy password, so it is never logged.
        if response.is_error:
            raise RuntimeError(f"Evervault mint failed with HTTP {response.status_code}")
        return response.json()["proxyConfiguration"]

    async def create(self, params: dict[str, Any]) -> Session:
        browser_params = dict(params)
        settings = dict(browser_params.pop("evervault"))
        egress = browser_params.pop("proxy_create", None)
        if egress is not None:
            if egress.get("type") != "residential":
                raise ValueError(
                    f"Evervault egress can only be residential, not {egress.get('type')}"
                )
            settings["proxy_settings"] = {"type": "residential", **(egress.get("config") or {})}
        proxy = await self._mint(settings)
        config = {
            "host": settings.get("relay_host") or proxy["hostname"],
            "port": proxy["port"],
            "username": proxy["username"],
            "password": proxy["password"],
            "ca_bundle": self._ca_bundle,
        }
        async with AsyncKernel() as client:
            return await self._create_browser(
                client, browser_params, {"type": "custom", "protocol": "https", "config": config}
            )


class HttpProvider(Provider):
    base_url: str
    key_header: str

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={self.key_header: os.environ[self.env[0]]},
            timeout=120,
        )


class BrowserbaseProvider(HttpProvider):
    env = ("BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID")
    base_url = "https://api.browserbase.com/v1"
    key_header = "X-BB-API-Key"

    async def create(self, params: dict[str, Any]) -> Session:
        body = {"projectId": os.environ["BROWSERBASE_PROJECT_ID"], **params}
        async with self._client() as client:
            response = await client.post("/sessions", json=body)
            response.raise_for_status()
            session = response.json()
        return Session(session["id"], session["connectUrl"])

    async def release(self, session_id: str) -> None:
        body = {"projectId": os.environ["BROWSERBASE_PROJECT_ID"], "status": "REQUEST_RELEASE"}
        async with self._client() as client:
            (await client.post(f"/sessions/{session_id}", json=body)).raise_for_status()


class SteelProvider(HttpProvider):
    env = ("STEEL_API_KEY",)
    base_url = "https://api.steel.dev/v1"
    key_header = "steel-api-key"

    async def create(self, params: dict[str, Any]) -> Session:
        async with self._client() as client:
            response = await client.post("/sessions", json=params)
            response.raise_for_status()
            session = response.json()
        url = f"wss://connect.steel.dev?apiKey={os.environ['STEEL_API_KEY']}&sessionId={session['id']}"
        return Session(session["id"], url)

    async def release(self, session_id: str) -> None:
        async with self._client() as client:
            (await client.post(f"/sessions/{session_id}/release")).raise_for_status()


class HyperbrowserProvider(HttpProvider):
    env = ("HYPERBROWSER_API_KEY",)
    base_url = "https://api.hyperbrowser.ai/api"
    key_header = "x-api-key"

    async def create(self, params: dict[str, Any]) -> Session:
        async with self._client() as client:
            response = await client.post("/session", json=params)
            response.raise_for_status()
            session = response.json()
        return Session(session["id"], session["wsEndpoint"])

    async def release(self, session_id: str) -> None:
        async with self._client() as client:
            (await client.put(f"/session/{session_id}/stop")).raise_for_status()


class AnchorProvider(HttpProvider):
    env = ("ANCHOR_API_KEY",)
    base_url = "https://api.anchorbrowser.io/v1"
    key_header = "anchor-api-key"

    async def create(self, params: dict[str, Any]) -> Session:
        async with self._client() as client:
            response = await client.post("/sessions", json=params)
            response.raise_for_status()
            session = response.json()["data"]
        return Session(session["id"], session["cdp_url"])

    async def release(self, session_id: str) -> None:
        async with self._client() as client:
            (await client.delete(f"/sessions/{session_id}")).raise_for_status()


PROVIDERS: dict[str, Provider] = {
    "kernel": KernelProvider(),
    "kernel-evervault": KernelEvervaultProvider(),
    "browserbase": BrowserbaseProvider(),
    "steel": SteelProvider(),
    "hyperbrowser": HyperbrowserProvider(),
    "anchor": AnchorProvider(),
}


def get(name: str) -> Provider:
    if name not in PROVIDERS:
        raise SystemExit(f"Unknown browser provider {name!r}; known: {', '.join(PROVIDERS)}")
    return PROVIDERS[name]
