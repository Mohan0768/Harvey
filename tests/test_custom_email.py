"""The Custom Email button: one template, filled in per contact.

The rule these pin down is who a template run is allowed to overwrite. A
draft still awaiting review is yours to replace. A draft you APPROVED has
cleared the one gate that lets mail out, so nothing here may rewrite it.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash

EM_DASH = "\u2014"

TEMPLATE = {
    "subject": "A question about {{department}}",
    "body": "Hi {{first_name}},\n\nSaw your work at {{company}}.\n\nThanks,\n{{sender_name}}",
}


@pytest.fixture
def client(monkeypatch):
    """A dashboard bound to a throwaway database."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "harvey.db"
        monkeypatch.setattr(dash, "DB_PATH", db)
        monkeypatch.setattr(
            dash, "WEB_DIR",
            Path(__file__).resolve().parent.parent / "harvey" / "web")
        with TestClient(dash.app) as c:
            c.db_path = db
            yield c


def add_contact(client, first, email, company="Flagler College",
                department="Marketing"):
    r = client.post("/api/contacts", json={
        "first_name": first, "last_name": "Tester", "email": email,
        "company": company, "department": department, "title": "Dean"})
    assert r.status_code == 200, r.text
    return r


def prospect_ids(client):
    con = sqlite3.connect(client.db_path)
    try:
        return [r[0] for r in con.execute(
            "SELECT id FROM prospects ORDER BY rowid")]
    finally:
        con.close()


def outbox(client):
    con = sqlite3.connect(client.db_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM outbox")]
    finally:
        con.close()


def draft_for(client, email, subject="Earlier draft"):
    """Queue a pending_review draft the way the Write Email button does.
    compose resolves the contact from the address, as the UI does."""
    r = client.post("/api/outbox/compose", json={
        "to_email": email, "subject": subject,
        "body": "An earlier message that is still waiting for review."})
    assert r.status_code == 200, r.text
    return r.json()


# -- the audience rule --


def test_scope_new_skips_contacts_that_already_have_a_draft(client):
    """The reported bug: once everyone has a draft, the button had nothing
    to do and said only "no eligible contacts". It now names the number and
    the way out."""
    add_contact(client, "Ann", "ann@example.edu")
    draft_for(client, "ann@example.edu")

    r = client.post("/api/contacts/custom-preview",
                    json=dict(TEMPLATE, scope="new"))
    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False
    assert "1 contact(s) already have a draft" in body["message"]
    assert "Everyone" in body["message"]
    assert body["skipped"]["already_queued"] == 1


def test_scope_all_includes_them_and_counts_the_replacements(client):
    add_contact(client, "Ann", "ann@example.edu")
    add_contact(client, "Ben", "ben@example.edu")
    draft_for(client, "ann@example.edu")

    r = client.post("/api/contacts/custom-preview",
                    json=dict(TEMPLATE, scope="all"))
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] == 2
    assert body["replacing"] == 1
    assert body["skipped"]["already_queued"] == 0


def test_scope_defaults_to_new(client):
    """An absent scope must not silently replace anything."""
    add_contact(client, "Ann", "ann@example.edu")
    draft_for(client, "ann@example.edu")

    r = client.post("/api/contacts/custom-preview", json=TEMPLATE)
    assert r.status_code == 400
    assert r.json()["skipped"]["already_queued"] == 1


# -- what a run actually writes --


def test_scope_all_replaces_the_pending_draft(client):
    add_contact(client, "Ann", "ann@example.edu")
    draft_for(client, "ann@example.edu")

    r = client.post("/api/contacts/custom-generate",
                    json=dict(TEMPLATE, scope="all"))
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    assert r.json()["replaced"] == 1

    rows = outbox(client)
    pending = [x for x in rows if x["status"] == "pending_review"]
    cancelled = [x for x in rows if x["status"] == "cancelled"]
    assert len(pending) == 1, "exactly one live draft per contact"
    assert pending[0]["subject"] == "A question about Marketing"
    assert pending[0]["body"].startswith("Hi Ann,")
    assert len(cancelled) == 1
    assert cancelled[0]["error"] == "replaced_by_custom_template"
    assert cancelled[0]["subject"] == "Earlier draft"


def test_generated_draft_is_pending_not_sent(client):
    """Approval stays the only way anything leaves."""
    add_contact(client, "Ann", "ann@example.edu")
    client.post("/api/contacts/custom-generate",
                json=dict(TEMPLATE, scope="all"))
    assert {x["status"] for x in outbox(client)} == {"pending_review"}


def test_an_approved_draft_is_never_replaced(client):
    add_contact(client, "Ann", "ann@example.edu")
    draft_for(client, "ann@example.edu")
    # approve-all stages without sending, which is what this test needs:
    # the single Approve button now sends, and there is no provider here.
    assert client.post("/api/outbox/approve-all").status_code == 200
    assert outbox(client)[0]["status"] == "approved"

    r = client.post("/api/contacts/custom-generate",
                    json=dict(TEMPLATE, scope="all"))
    assert r.status_code == 400
    assert "approved draft" in r.json()["message"]

    rows = outbox(client)
    assert len(rows) == 1
    assert rows[0]["status"] == "approved"
    assert rows[0]["subject"] == "Earlier draft"


def test_a_blocked_template_does_not_cost_you_the_old_draft(client):
    """The gate is checked before anything is cancelled, so a template the
    gate rejects leaves the queue exactly as it was."""
    add_contact(client, "Ann", "ann@example.edu")
    draft_for(client, "ann@example.edu")

    r = client.post("/api/contacts/custom-generate", json={
        "subject": "A question " + EM_DASH + " about {{department}}",
        "body": "Hi {{first_name}}," + EM_DASH + " thanks.",
        "scope": "all"})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0
    assert r.json()["blocked"] == 1
    assert r.json()["replaced"] == 0

    rows = outbox(client)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending_review"
    assert rows[0]["subject"] == "Earlier draft"


# -- template rendering --


def test_unknown_field_stays_visible(client):
    """A typo must show up in the preview, not vanish into an empty string."""
    add_contact(client, "Ann", "ann@example.edu")
    r = client.post("/api/contacts/custom-preview", json={
        "subject": "Hello", "body": "Hi {{frist_name}}, at {{company}}.",
        "scope": "all"})
    assert r.status_code == 200
    body = r.json()
    assert body["unknown_fields"] == ["frist_name"]
    assert "{{frist_name}}" in body["body"]
    assert "Flagler College" in body["body"]


def test_preview_picks_the_most_complete_contact(client):
    """Previewing against the emptiest record makes a good template look
    broken, so the sample is the one with the fields actually filled in."""
    add_contact(client, "Ann", "ann@example.edu", company="", department="")
    add_contact(client, "Ben", "ben@example.edu",
                company="Rollins College", department="Analytics")

    r = client.post("/api/contacts/custom-preview", json={
        "subject": "About {{department}}",
        "body": "Hi {{first_name}}, at {{company}}.", "scope": "all"})
    assert r.status_code == 200
    body = r.json()
    assert body["to"] == "ben@example.edu"
    assert body["incomplete"] == 1
    assert body["incomplete_sample"] == ["Ann Tester"]


# -- the field that makes a template personal --


def test_department_survives_a_hand_added_contact(client):
    """_contact_payload used to drop department, so {{department}} rendered
    empty for every contact added by hand or imported from a sheet."""
    add_contact(client, "Ann", "ann@example.edu", department="Human Resources")

    r = client.post("/api/contacts/custom-preview", json={
        "subject": "About {{department}}", "body": "Hi {{first_name}}.",
        "scope": "all"})
    assert r.json()["subject"] == "About Human Resources"


def test_department_column_is_recognised_in_a_sheet():
    from harvey.importer import map_columns

    headers = ["First Name", "Last Name", "Email", "Institution",
               "Department", "LinkedIn"]
    mapping = map_columns(headers)
    assert mapping[3] == "company"
    assert mapping[4] == "department"
    assert mapping[5] == "linkedin_url"


def test_school_column_still_means_company_not_department():
    """"school" is listed under company; department must not steal it."""
    from harvey.importer import map_columns

    assert map_columns(["School"])[0] == "company"
