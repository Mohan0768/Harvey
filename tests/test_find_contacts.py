"""Find contacts: the step between discovering a college and knowing a dean.

Discovery writes to companies. Only Scout writes to prospects, and the
heartbeat reaches Scout only when the pipeline is thin, so 40 discovered
institutions could sit unmined for ever. This is the on-demand pass.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        monkeypatch.setattr(dash, "DB_PATH", Path(tmp) / "harvey.db")
        monkeypatch.setattr(
            dash, "WEB_DIR",
            Path(__file__).resolve().parent.parent / "harvey" / "web")
        monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
        dash._find_contacts = {"running": False, "created": 0, "companies": 0,
                               "message": "", "error": ""}
        with TestClient(dash.app) as c:
            yield c


def test_nothing_to_mine_says_so(client):
    """An empty company list is not an error to shrug at: it tells you to
    run Discovery, which is the actual next step."""
    r = client.post("/api/contacts/find", json={})
    assert r.status_code == 400
    assert "Run Discovery" in r.json()["message"]


def test_status_starts_idle(client):
    s = client.get("/api/contacts/find-status").json()
    assert s["success"] is True
    assert s["running"] is False


def test_a_run_in_flight_is_not_started_twice(client, monkeypatch):
    dash._find_contacts = {"running": True, "created": 0, "companies": 3,
                           "message": "", "error": ""}
    r = client.post("/api/contacts/find", json={})
    assert r.status_code == 409
    assert "Already running" in r.json()["message"]


def test_the_limit_is_clamped(client, monkeypatch):
    """A limit is a batch size, not an invitation to read ten thousand
    sites in one request."""
    seen = {}

    # Patched onto the class, so it is bound: self comes first.
    async def fake(self, limit=10, stale_days=14):
        seen["limit"] = limit
        return []

    import harvey.state as hstate
    monkeypatch.setattr(hstate.StateManager, "companies_needing_prospects", fake)

    client.post("/api/contacts/find", json={"limit": 9999})
    assert seen["limit"] == 100, "clamped to the ceiling"

    client.post("/api/contacts/find", json={"limit": 0})
    assert seen["limit"] == 25, "zero falls back to the default"


def test_scout_backfill_accepts_a_bigger_batch():
    """The per-cycle cap paces the heartbeat; an on-demand run may ask for
    more, so the strategy had to stop hard-coding the constant."""
    import inspect

    from harvey.agents.scout import Scout

    sig = inspect.signature(Scout._prospect_via_known_companies)
    assert "limit" in sig.parameters
    assert sig.parameters["limit"].default is None
