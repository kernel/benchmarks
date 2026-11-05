"""Cloud browser providers that a trial can drive over CDP.

Each provider creates one browser session from an arm's create params and
releases it when the trial ends. The params are the provider's own session
create body, so an arm states exactly which stealth, proxy and CAPTCHA
settings it asked for. Sessions must outlive a CDP disconnect: the start page
is opened, the agent connects, and the verifier connects, each separately.
"""

from __future__ import annotations

import os
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
    env = ("KERNEL_API_KEY",)

    async def create(self, params: dict[str, Any]) -> Session:
        async with AsyncKernel() as client:
            browser = await client.browsers.create(**params)
        return Session(browser.session_id, browser.cdp_ws_url)

    async def release(self, session_id: str) -> None:
        async with AsyncKernel() as client:
            await client.browsers.delete_by_id(session_id)


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
    "browserbase": BrowserbaseProvider(),
    "steel": SteelProvider(),
    "hyperbrowser": HyperbrowserProvider(),
    "anchor": AnchorProvider(),
}


def get(name: str) -> Provider:
    if name not in PROVIDERS:
        raise SystemExit(f"Unknown browser provider {name!r}; known: {', '.join(PROVIDERS)}")
    return PROVIDERS[name]
