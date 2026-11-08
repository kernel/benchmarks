import asyncio
import json

import httpx
import pytest

from harness import providers


def _serve(monkeypatch, provider, handler):
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        provider,
        "_client",
        lambda: httpx.AsyncClient(
            base_url=provider.base_url,
            headers={provider.key_header: "key"},
            transport=httpx.MockTransport(record),
        ),
    )
    return requests


def test_browserbase_sends_the_project_and_releases_the_session(monkeypatch):
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "proj")
    provider = providers.get("browserbase")
    requests = _serve(
        monkeypatch,
        provider,
        lambda request: httpx.Response(200, json={"id": "bb1", "connectUrl": "wss://bb/1"}),
    )

    session = asyncio.run(provider.create({"keepAlive": True}))
    asyncio.run(provider.release(session.id))

    assert session == providers.Session("bb1", "wss://bb/1")
    assert json.loads(requests[0].content) == {"projectId": "proj", "keepAlive": True}
    assert requests[1].url.path == "/v1/sessions/bb1"
    assert json.loads(requests[1].content)["status"] == "REQUEST_RELEASE"


def test_steel_connects_with_the_session_and_key(monkeypatch):
    monkeypatch.setenv("STEEL_API_KEY", "sk")
    provider = providers.get("steel")
    requests = _serve(monkeypatch, provider, lambda request: httpx.Response(200, json={"id": "s1"}))

    session = asyncio.run(provider.create({"useProxy": True}))
    asyncio.run(provider.release("s1"))

    assert session.cdp_url == "wss://connect.steel.dev?apiKey=sk&sessionId=s1"
    assert [request.url.path for request in requests] == ["/v1/sessions", "/v1/sessions/s1/release"]


def test_hyperbrowser_and_anchor_read_their_cdp_urls(monkeypatch):
    hyperbrowser = providers.get("hyperbrowser")
    _serve(
        monkeypatch,
        hyperbrowser,
        lambda request: httpx.Response(200, json={"id": "h1", "wsEndpoint": "wss://hb/1"}),
    )
    anchor = providers.get("anchor")
    _serve(
        monkeypatch,
        anchor,
        lambda request: httpx.Response(200, json={"data": {"id": "a1", "cdp_url": "wss://a/1"}}),
    )

    assert asyncio.run(hyperbrowser.create({})) == providers.Session("h1", "wss://hb/1")
    assert asyncio.run(anchor.create({})) == providers.Session("a1", "wss://a/1")


def test_failed_create_raises(monkeypatch):
    provider = providers.get("hyperbrowser")
    _serve(monkeypatch, provider, lambda request: httpx.Response(402, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.create({}))


def test_unknown_provider_is_refused():
    with pytest.raises(SystemExit, match="nope"):
        providers.get("nope")


class _FakeKernel:
    def __init__(self, calls):
        self.calls = calls
        self.proxies = self
        self.browsers = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def create(self, **kwargs):
        if "type" in kwargs:
            self.calls.append(("proxy.create", kwargs))
            return type("Proxy", (), {"id": "p1"})
        self.calls.append(("browser.create", kwargs))
        return type("Browser", (), {"session_id": "b1", "cdp_ws_url": "wss://k/1"})

    async def delete(self, proxy_id):
        self.calls.append(("proxy.delete", proxy_id))

    async def delete_by_id(self, session_id):
        self.calls.append(("browser.delete", session_id))


def test_kernel_evervault_proxies_the_browser_through_a_minted_session(monkeypatch):
    monkeypatch.setenv("EV_APP_UUID", "app_1")
    monkeypatch.setenv("EV_API_KEY", "ev:key:1")
    monkeypatch.setenv("BENCH_RUN_ID", "r1")
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "ca.evervault.com":
            return httpx.Response(200, text="PEM")
        proxy = {"hostname": "us-va.browser.relay.evervault.app", "port": 443}
        return httpx.Response(
            200, json={"proxyConfiguration": {**proxy, "username": "app_1", "password": "jwe"}}
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    calls = []
    monkeypatch.setattr(providers, "AsyncKernel", lambda: _FakeKernel(calls))
    provider = providers.KernelEvervaultProvider()

    session = asyncio.run(
        provider.create({"stealth": True, "evervault": {"ttl": 1800, "hosts": ["a.example"]}})
    )
    asyncio.run(provider.release(session.id))

    assert session == providers.Session("b1", "wss://k/1")
    mint = json.loads(requests[1].content)
    assert mint["ttl"] == 1800
    assert mint["permissions"] == [{"action": "decrypt", "target": "a.example"}]
    proxy = calls[0][1]
    assert proxy["type"] == "custom" and proxy["protocol"] == "https"
    assert proxy["name"].startswith("bench-r1-")
    assert proxy["config"] == {
        "host": "us-va.browser.relay.evervault.app",
        "port": 443,
        "username": "app_1",
        "password": "jwe",
        "ca_bundle": "PEM",
    }
    assert calls[1] == ("browser.create", {"stealth": True, "proxy_id": "p1"})
    assert calls[2:] == [("browser.delete", "b1"), ("proxy.delete", "p1")]


def test_kernel_evervault_mint_failure_does_not_leak_the_body(monkeypatch):
    monkeypatch.setenv("EV_APP_UUID", "app_1")
    monkeypatch.setenv("EV_API_KEY", "ev:key:1")

    def handler(request):
        if request.url.host == "ca.evervault.com":
            return httpx.Response(200, text="PEM")
        return httpx.Response(403, text="secret detail")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(RuntimeError, match="HTTP 403") as error:
        asyncio.run(providers.KernelEvervaultProvider().create({"evervault": {"hosts": ["a"]}}))
    assert "secret" not in str(error.value)


def test_kernel_creates_and_deletes_a_proxy_per_session(monkeypatch):
    calls = []
    monkeypatch.setattr(providers, "AsyncKernel", lambda: _FakeKernel(calls))
    provider = providers.KernelProvider()
    proxy = {"type": "residential", "config": {"country": "GB"}}

    session = asyncio.run(provider.create({"stealth": True, "proxy_create": proxy}))
    asyncio.run(provider.release(session.id))

    assert calls[0][0] == "proxy.create" and calls[0][1]["config"] == {"country": "GB"}
    assert calls[1] == ("browser.create", {"stealth": True, "proxy_id": "p1"})
    assert calls[2:] == [("browser.delete", "b1"), ("proxy.delete", "p1")]
