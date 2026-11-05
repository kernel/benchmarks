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
