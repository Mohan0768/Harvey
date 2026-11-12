"""Approve-and-send: the one place email actually leaves the building.

Every test here is about a promise the spec makes to the person clicking
Approve: it sends once, it never claims success it did not get, and a
failure leaves something they can retry and a reason they can read.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
import harvey.sending as sending
from harvey.integrations.mail_provider import SendResult


class FakeProvider:
    """Stands in for Gmail. Counts sends, so "exactly once" is observable."""

    name = "gmail"

    def __init__(self, *, configured=True, result=None):
        self._configured = configured
        self._result = result or SendResult(
            ok=True, message_id="msg-1", thread_ref="thread-1")
        self.sent = []

    def is_configured(self):
        return self._configured

    async def send_email(self, to, subject, body, thread_ref="", in_reply_to=""):
        self.sent.append({"to": to, "subject": subject, "body": body})
        return self._result


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "harvey.db"
        monkeypatch.setattr(dash, "DB_PATH", db)
        monkeypatch.setattr(
            dash, "WEB_DIR",
            Path(__file__).resolve().parent.parent / "harvey" / "web")
        with TestClient(dash.app) as c:
            c.db_path = db
            yield c


def use_provider(monkeypatch, provider):
    monkeypatch.setattr(sending, "get_mail_provider", lambda cfg, env: provider)
    return provider


def seed_draft(client, email="ann@example.edu", subject="A quick question",
               body="Hi Ann,\n\nWorth a look?\n\nThanks."):
    client.post("/api/contacts", json={
        "first_name": "Ann", "last_name": "Tester", "email": email,
        "company": "Flagler College", "department": "Marketing"})
    r = client.post("/api/outbox/compose", json={
        "to_email": email, "subject": subject, "body": body})
    assert r.status_code == 200, r.text
    con = sqlite3.connect(client.db_path)
    try:
        return con.execute("SELECT id FROM outbox").fetchone()[0]
    finally:
        con.close()


def row(client, item_id):
    con = sqlite3.connect(client.db_path)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT * FROM outbox WHERE id = ?", (item_id,)).fetchone())
    finally:
        con.close()


# -- the happy path --


def test_approve_sends_and_records_the_gmail_ids(client, monkeypatch):
    p = use_provider(monkeypatch, FakeProvider())
    item = seed_draft(client)

    r = client.post("/api/outbox/" + item + "/approve")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["message_id"] == "msg-1"
    assert body["thread_ref"] == "thread-1"

    saved = row(client, item)
    assert saved["status"] == "sent"
    assert saved["message_id"] == "msg-1"
    assert saved["thread_ref"] == "thread-1"
    assert saved["sent_at"]
    assert len(p.sent) == 1
    assert p.sent[0]["to"] == "ann@example.edu"


def test_a_sent_email_opens_a_conversation_on_its_thread(client, monkeypatch):
    use_provider(monkeypatch, FakeProvider())
    item = seed_draft(client)
    client.post("/api/outbox/" + item + "/approve")

    con = sqlite3.connect(client.db_path)
    con.row_factory = sqlite3.Row
    try:
        convos = [dict(r) for r in con.execute("SELECT * FROM conversations")]
    finally:
        con.close()
    assert len(convos) == 1
    assert convos[0]["title"] == "A quick question"
    assert "Worth a look?" in convos[0]["thread_json"]
    assert row(client, item)["conversation_id"] == convos[0]["id"]


# -- exactly once --


def test_clicking_approve_twice_sends_once(client, monkeypatch):
    p = use_provider(monkeypatch, FakeProvider())
    item = seed_draft(client)

    first = client.post("/api/outbox/" + item + "/approve")
    second = client.post("/api/outbox/" + item + "/approve")

    assert first.json()["success"] is True
    assert second.json()["success"] is True      # idempotent, not an error
    assert second.json()["already"] is True
    assert "Already sent" in second.json()["message"]
    assert len(p.sent) == 1, "the provider must be called exactly once"


def test_a_rejected_email_is_never_sent(client, monkeypatch):
    p = use_provider(monkeypatch, FakeProvider())
    item = seed_draft(client)
    assert client.post("/api/outbox/" + item + "/reject").status_code == 200

    r = client.post("/api/outbox/" + item + "/approve")
    assert r.status_code == 400
    assert r.json()["success"] is False
    assert p.sent == []
    assert row(client, item)["status"] == "rejected"


# -- never claim a success you did not get --


def test_a_provider_failure_is_reported_and_stays_retryable(client, monkeypatch):
    p = use_provider(monkeypatch, FakeProvider(
        result=SendResult(ok=False, error="Rate limit exceeded")))
    item = seed_draft(client)

    r = client.post("/api/outbox/" + item + "/approve")
    assert r.status_code == 400
    assert "Rate limit exceeded" in r.json()["message"]

    saved = row(client, item)
    assert saved["status"] == "failed"
    assert saved["error"] == "Rate limit exceeded"
    assert not saved["message_id"], "no id, because nothing was accepted"
    assert not saved["sent_at"]

    # retryable: a later attempt, with the provider working, goes out.
    p._result = SendResult(ok=True, message_id="msg-2", thread_ref="t-2")
    again = client.post("/api/outbox/" + item + "/approve")
    assert again.json()["success"] is True
    assert row(client, item)["status"] == "sent"
    assert len(p.sent) == 2


def test_an_unconnected_provider_says_so_and_sends_nothing(client, monkeypatch):
    p = use_provider(monkeypatch, FakeProvider(configured=False))
    item = seed_draft(client)

    r = client.post("/api/outbox/" + item + "/approve")
    assert r.status_code == 400
    assert "not connected" in r.json()["message"]
    assert p.sent == []
    assert row(client, item)["status"] == "failed"


def test_a_crash_does_not_strand_the_row_in_sending(client, monkeypatch):
    class Exploding(FakeProvider):
        async def send_email(self, *a, **k):
            raise RuntimeError("socket died")

    use_provider(monkeypatch, Exploding())
    item = seed_draft(client)

    r = client.post("/api/outbox/" + item + "/approve")
    assert r.status_code == 400
    assert "socket died" in r.json()["message"]
    assert row(client, item)["status"] == "failed"


# -- approving in bulk is not sending in bulk --


def test_approve_all_stages_without_sending(client, monkeypatch):
    p = use_provider(monkeypatch, FakeProvider())
    seed_draft(client, email="a@example.edu")
    seed_draft(client, email="b@example.edu")

    r = client.post("/api/outbox/approve-all")
    assert r.status_code == 200
    assert p.sent == [], "approve-all must never put mail out by itself"


# -- what the review screen is given --


def test_outbox_payload_names_the_recipient(client, monkeypatch):
    use_provider(monkeypatch, FakeProvider())
    seed_draft(client)

    data = client.get("/api/outbox").json()
    pending = data["pending"][0]
    assert pending["recipient_name"] == "Ann Tester"
    assert pending["recipient_company"] == "Flagler College"
    assert pending["email_type"] == "Custom"
    assert pending["created_at"]


# -- the composer: drafts, several recipients, and the move to the desk --


def test_several_recipients_become_separate_emails(client):
    """One To line with four strangers in it is a different, worse message,
    so each recipient gets their own row."""
    r = client.post("/api/outbox/compose", json={
        "to_emails": ["a@example.edu", "b@example.edu", "a@example.edu"],
        "subject": "hello", "body": "Hi there, worth a look?"})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2, "the duplicate address is collapsed"

    con = sqlite3.connect(client.db_path)
    try:
        rows = con.execute(
            "SELECT to_email, status FROM outbox ORDER BY to_email").fetchall()
    finally:
        con.close()
    assert [x[0] for x in rows] == ["a@example.edu", "b@example.edu"]
    assert {x[1] for x in rows} == {"pending_review"}


def test_a_draft_waits_off_the_approval_desk(client):
    r = client.post("/api/outbox/compose", json={
        "to_email": "a@example.edu", "subject": "hello",
        "body": "Hi there.", "status": "draft"})
    assert r.json()["status"] == "draft"

    data = client.get("/api/outbox").json()
    assert len(data["drafts"]) == 1
    assert data["pending"] == []


def test_move_to_outbox_promotes_a_draft_only(client):
    r = client.post("/api/outbox/compose", json={
        "to_email": "a@example.edu", "subject": "hello",
        "body": "Hi there.", "status": "draft"})
    item = r.json()["id"]

    assert client.post("/api/outbox/" + item + "/to-outbox").status_code == 200
    assert row(client, item)["status"] == "pending_review"

    # Already promoted: a second move is refused rather than silently redone.
    again = client.post("/api/outbox/" + item + "/to-outbox")
    assert again.status_code == 400
    assert "saved draft" in again.json()["message"]


def test_a_draft_is_not_sendable_until_it_is_moved(client, monkeypatch):
    """A draft is a scratch pad. It must not be claimable by the sender."""
    p = use_provider(monkeypatch, FakeProvider())
    item = client.post("/api/outbox/compose", json={
        "to_email": "a@example.edu", "subject": "hello",
        "body": "Hi there.", "status": "draft"}).json()["id"]

    r = client.post("/api/outbox/" + item + "/approve")
    assert r.status_code == 400
    assert p.sent == []
    assert row(client, item)["status"] == "draft"


def test_a_bad_address_among_good_ones_stops_the_whole_compose(client):
    r = client.post("/api/outbox/compose", json={
        "to_emails": ["good@example.edu", "not-an-address"],
        "subject": "hello", "body": "Hi there."})
    assert r.status_code == 400
    assert "not-an-address" in r.json()["message"]
    assert client.get("/api/outbox").json()["pending"] == []
