"""Repeat discovery runs must find something new.

The queries are a cross-product of terms and places, so pressing Start
Discovery again re-issued the identical searches: every result came back
already known and the contact list never grew. These pin the fix.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey.collectors.discover import DiscoveryQuery, build_queries


@pytest.fixture
def client(monkeypatch):
    # ignore_cleanup_errors: a discovery task started by the endpoint can
    # still hold the SQLite file when the block exits, and on Windows an
    # open handle makes rmtree raise. The test has already asserted by then.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        monkeypatch.setattr(dash, "DB_PATH", Path(tmp) / "harvey.db")
        monkeypatch.setattr(
            dash, "WEB_DIR",
            Path(__file__).resolve().parent.parent / "harvey" / "web")
        monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
        try:
            with TestClient(dash.app) as c:
                yield c
        finally:
            task = getattr(dash, "_discovery_task", None)
            if task is not None and not task.done():
                task.cancel()
            dash._discovery_task = None


class Cfg:
    class icp:
        industries = ["colleges and universities"]
        geography = ["Miami, FL"]
        geo_coordinates = {}

    class product:
        name = "thing"


def run_once(client, payload):
    """One discovery run. _discovery_task is cleared first because the
    endpoint refuses a second run while one is in flight, and these tests
    care about the page counter rather than concurrency."""
    dash._discovery_task = None
    return client.post("/api/discover/run", json=payload)


# -- the page reaches the query and the provider --


def test_build_queries_defaults_to_page_one():
    q = build_queries(Cfg)[0]
    assert q.page == 1


def test_build_queries_carries_the_page():
    q = build_queries(Cfg, page=3)[0]
    assert q.page == 3


def test_a_page_below_one_is_clamped():
    assert build_queries(Cfg, page=0)[0].page == 1


@pytest.mark.asyncio
async def test_serper_sends_the_page():
    from harvey.collectors.discover import PROVIDERS

    sent = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"organic": []}

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            sent["url"] = url
            sent["json"] = json
            return FakeResponse()

    provider = PROVIDERS["serper"]
    await provider.fetch(
        FakeClient(), {"serper_api_key": "k"},
        DiscoveryQuery(term="colleges", location="Miami, FL", page=4))
    assert sent["json"]["page"] == 4
    assert sent["json"]["q"] == "colleges Miami, FL"


# -- the same search, pressed twice, goes deeper --


def test_the_same_search_twice_advances_the_page(client, monkeypatch):
    seen = []

    def capture(body, config, page=1):
        seen.append(page)
        return []

    monkeypatch.setattr(dash, "_discovery_queries", capture)
    payload = {"provider": "osm", "cities": ["Miami, FL"], "terms": ["colleges"]}
    for _ in range(3):
        run_once(client, payload)
    assert seen == [1, 2, 3], "a repeat must not re-issue page 1"


def test_an_explicit_page_is_obeyed(client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        dash, "_discovery_queries",
        lambda body, config, page=1: seen.append(page) or [])
    run_once(client, {
        "provider": "osm", "cities": ["Miami, FL"], "terms": ["colleges"],
        "page": 7})
    assert seen == [7]


def test_a_different_search_starts_its_own_count(client, monkeypatch):
    """Changing the location or the designation is a new search, so it
    starts at page one rather than inheriting someone else's depth."""
    seen = []
    monkeypatch.setattr(
        dash, "_discovery_queries",
        lambda body, config, page=1: seen.append(page) or [])

    run_once(client, {"provider": "osm", "cities": ["Miami, FL"], "terms": ["colleges"]})
    run_once(client, {"provider": "osm", "cities": ["Miami, FL"], "terms": ["colleges"]})
    run_once(client, {"provider": "osm", "cities": ["Orlando, FL"], "terms": ["colleges"]})
    assert seen == [1, 2, 1]


def test_the_signature_ignores_order_and_case():
    a = dash._discovery_signature({"cities": ["Miami, FL", "Tampa, FL"],
                                   "terms": ["Colleges"]})
    b = dash._discovery_signature({"cities": ["tampa, fl", "miami, fl"],
                                   "terms": ["colleges"]})
    assert a == b
