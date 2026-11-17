"""The War Room beta invitation: who it is for, and what it may never say.

Two things are pinned here. First, that classification reads a title the way
a person would: "Professor of Cybersecurity" is a professor, and an
"Associate VP, Center for Career Development" is a careers person, not a
vice president. Second, and more important, that the model can rewrite the
prose of an invitation and nothing else. A rewrite that moves a link, an
address, a promise or the word count is rejected and the composed wording
survives, because those are the four ways a generated email does real harm
to a real academic.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey import outreach, roles
from harvey.config import HarveyConfig, OutreachConfig, PersonaConfig
from harvey.gate import MAX_BODY_WORDS, pre_send_check

DEMO = "https://enterthewarroom.humanfirstbykk.com/"
REPLY = "kk@humanfirstbykk.com"
BOOKING = "https://calendly.com/kk-humanfirst/30min"


# The three blocks HarveyConfig requires, kept minimal: this file is about
# the invitation, not about configuration parsing.
BASE = {
    "persona": {"name": "Dana Real", "company": "KK's War Room",
                "role": "Education Partnerships", "email": "dana@example.com",
                "linkedin": "", "tone": "professional"},
    "product": {"name": "KK's War Room", "description": "A simulation.",
                "pricing": "", "key_benefits": [], "objection_responses": {}},
    "icp": {"industries": [], "company_size": "", "titles": [],
            "geography": []},
}


def make_config(**overrides):
    cfg = HarveyConfig(**BASE)
    cfg.outreach = OutreachConfig(
        organization="HLI America", demo_url=DEMO, reply_to=REPLY,
        **overrides)
    return cfg


CONTACT = {
    "id": "p1", "first_name": "Ruth", "last_name": "Lopez",
    "title": "Director of Career Development", "department": "Career Services",
    "company": "Miami Dade College", "email": "ruth@mdc.edu",
    "category": "career_center",
}


# ── classification ──

@pytest.mark.parametrize("title,expected", [
    ("Professor of Cybersecurity", roles.PROFESSOR),
    ("Professor, MIS Department", roles.PROFESSOR),
    ("Associate VP and Executive Director, Center for Career Development",
     roles.CAREER_SERVICES),
    ("Associate Dean for Accreditation and Technology Systems", roles.DEAN),
    ("Dean of Information Systems", roles.DEAN),
    ("Vice President of Student Affairs", roles.DEAN),
    ("VP of Marketing", roles.MARKETING_LEADER),
    ("Experiential Learning Coordinator", roles.CAREER_SERVICES),
    ("Career Counselor", roles.CAREER_COUNSELOR),
    ("Director of Information Security", roles.SECURITY),
    ("CISO", roles.SECURITY),
    ("CIO", roles.IT_LEADER),
    ("Director of Human Resources", roles.HR_LEADER),
    ("Chief Executive Officer", roles.EXECUTIVE),
    ("Program Director, Academic and Student Services", roles.ACADEMIC_HEAD),
])
def test_titles_classify_the_way_a_person_would_read_them(title, expected):
    assert roles.classify(title) == expected


def test_a_title_with_no_function_stays_unclassified():
    """Better an honest blank than a confident guess: an unclassified
    contact gets the general invitation, which is true for everyone."""
    assert roles.classify("Associate Director") == roles.UNCLASSIFIED


def test_resolve_falls_back_to_the_stored_segment():
    assert roles.resolve({"title": "Associate Director",
                          "category": "career_center"}) == roles.CAREER_SERVICES


def test_resolve_does_not_invent_a_role_from_a_discipline():
    """A segment says what someone teaches, not what their job is. Only
    career_center maps onto a role; the rest must stay blank."""
    assert roles.resolve({"title": "", "category": "marketing"}) == \
        roles.UNCLASSIFIED


# ── composition ──

@pytest.mark.parametrize("role", [key for key, _ in roles.known_roles()] + [""])
def test_every_role_composes_a_sendable_email(role):
    cfg = make_config()
    draft = outreach.compose(CONTACT, cfg, role=role)
    check = pre_send_check(CONTACT["email"], draft.subject, draft.body,
                           kind="manual")
    assert check.ok, check.reasons
    assert draft.word_count <= MAX_BODY_WORDS


# Markers every phrasing of a beat shares, in the order the beats appear.
BEATS = ("I am KK", "We are building HumanFirst", "beta users",
         "For your students that means", "resume", DEMO, REPLY)


@pytest.mark.parametrize("contact_id", ["p%d" % i for i in range(25)])
def test_every_email_carries_all_six_beats(contact_id):
    """Whichever phrasings a contact draws, the structure is the same."""
    draft = outreach.compose(dict(CONTACT, id=contact_id),
                             make_config(founder_name="KK"))
    for marker in BEATS:
        assert marker in draft.body, (marker, draft.body)


@pytest.mark.parametrize("contact_id", ["p%d" % i for i in range(25)])
def test_the_beats_keep_their_order(contact_id):
    body = outreach.compose(dict(CONTACT, id=contact_id),
                            make_config(founder_name="KK")).body
    positions = [body.index(m) for m in BEATS]
    assert positions == sorted(positions), body


def test_two_contacts_in_one_category_do_not_get_the_same_email():
    """The reason this file has variants at all. Forty-one of the real
    contacts are careers staff: one phrasing per beat meant forty-one
    near-identical emails, which is what a spam filter clusters on and what
    a reader spots the moment a colleague forwards them one."""
    cfg = make_config(founder_name="KK")
    drafts = [outreach.compose(
        dict(CONTACT, id="p%d" % i, first_name="Sam"), cfg, role=roles.CAREER_SERVICES)
        for i in range(20)]
    assert len({d.body for d in drafts}) >= 15
    assert len({d.subject for d in drafts}) >= 3


def test_variety_never_costs_correctness():
    """Every phrasing has to pass the gate, not just the one a given id
    happens to draw."""
    cfg = make_config(founder_name="KK")
    cfg.product.offer.booking_url = BOOKING
    for role, _ in roles.known_roles():
        for i in range(30):
            draft = outreach.compose(dict(CONTACT, id="p%d" % i), cfg,
                                     role=role)
            check = pre_send_check(CONTACT["email"], draft.subject,
                                   draft.body, kind="manual")
            assert check.ok, (role, i, check.reasons)


def test_every_link_comes_from_configuration():
    """The gate allows two. Both are config values, so nothing else may add
    one: a link the model invented is a link to somewhere we do not own."""
    import re
    from harvey.gate import MAX_LINKS

    cfg = make_config()
    links = re.findall(r"https?://\S+", outreach.compose(CONTACT, cfg).body)
    assert links == [DEMO]

    cfg.product.offer.booking_url = BOOKING
    links = re.findall(r"https?://\S+", outreach.compose(CONTACT, cfg).body)
    assert set(links) == {DEMO, BOOKING}
    assert len(links) <= MAX_LINKS


def test_a_booking_link_is_offered_only_when_one_is_configured():
    cfg = make_config()
    assert BOOKING not in outreach.compose(CONTACT, cfg).body
    cfg.product.offer.booking_url = BOOKING
    assert BOOKING in outreach.compose(CONTACT, cfg).body


def test_the_email_still_fits_the_gate_with_a_booking_link():
    cfg = make_config(founder_bio="KK has built products for a decade.")
    cfg.product.offer.booking_url = BOOKING
    for role, _ in roles.known_roles():
        draft = outreach.compose(CONTACT, cfg, role=role)
        check = pre_send_check(CONTACT["email"], draft.subject, draft.body,
                               kind="manual")
        assert check.ok, (role, check.reasons)


def test_a_name_without_a_biography_introduces_the_name_and_says_nothing_more():
    """An email that invents credentials is far worse than one that gives a
    name and stops, so the extra sentence simply goes. No note either: the
    introduction does name the person, and warning that it "says nothing
    about the person" under a line reading "I am KK" was plainly false."""
    draft = outreach.compose(CONTACT, make_config(founder_name="KK"))
    assert "I am KK, from HLI America." in draft.body
    assert draft.notes == []


def test_naming_nobody_at_all_is_worth_reporting():
    cfg = make_config()
    cfg.outreach.founder_name = ""
    draft = outreach.compose(CONTACT, cfg)
    assert any("says nothing about who is writing" in n for n in draft.notes)


def test_a_configured_founder_sentence_is_used_verbatim():
    cfg = make_config(founder_name="KK",
                      founder_bio="KK has run product teams for a decade.")
    draft = outreach.compose(CONTACT, cfg)
    assert "KK has run product teams for a decade." in draft.body
    assert not any("founder_bio" in n for n in draft.notes)


def test_a_missing_demo_url_removes_the_link_rather_than_guessing_one():
    cfg = make_config()
    cfg.outreach.demo_url = ""
    draft = outreach.compose(CONTACT, cfg)
    assert "http" not in draft.body
    assert any("demo URL" in n for n in draft.notes)


def test_a_contact_with_no_company_still_reads_as_a_sentence():
    nameless = dict(CONTACT, company="", id="p2")
    for role, _ in roles.known_roles():
        body = outreach.compose(nameless, make_config(), role=role).body
        assert "{company}" not in body
        assert " at ." not in body
        assert " at  " not in body


def test_subjects_are_stable_per_contact_and_differ_between_contacts():
    """Regenerating must not reshuffle a subject line, and two people at one
    institution must not get identical mail."""
    cfg = make_config()
    first = outreach.compose(CONTACT, cfg).subject
    assert outreach.compose(CONTACT, cfg).subject == first

    subjects = {outreach.compose(dict(CONTACT, id="p%d" % i), cfg).subject
                for i in range(12)}
    assert len(subjects) > 1


def test_readiness_flags_a_placeholder_sender():
    cfg = make_config()
    cfg.persona = PersonaConfig(
        **dict(BASE["persona"], name="Sam Placeholder",
               email="placeholder@example.invalid"))
    report = outreach.readiness(cfg)
    assert any("placeholder" in w for w in report["warnings"])


def test_readiness_reports_what_is_missing():
    cfg = HarveyConfig(**BASE)
    report = outreach.readiness(cfg)
    assert not report["ready"]
    assert "outreach.demo_url" in report["missing"]


# ── the model may rewrite prose and nothing else ──

class FakeBrain:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def think_json(self, prompt, **kwargs):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.asyncio
async def test_a_clean_rewrite_is_accepted():
    cfg = make_config()
    draft = outreach.compose(CONTACT, cfg)
    better = draft.body.replace("For your students that means",
                                "For your students this means")
    assert better != draft.body, "the mutation must actually change the body"
    brain = FakeBrain({"subject": "A business to run for an hour",
                       "body": better})
    out = await outreach.refine(draft, CONTACT, cfg, brain)
    assert out.subject == "A business to run for an hour"
    assert "For your students this means" in out.body


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate,why", [
    (lambda b: b.replace(DEMO, "https://enterthewarr00m.example.com/"), "link"),
    (lambda b: b.replace(REPLY, "kk@humanfirstbykk.co"), "address"),
    (lambda b: b + " guaranteed placement for every student", "claim"),
    (lambda b: b + (" padding" * MAX_BODY_WORDS), "length"),
    (lambda b: b.replace("beta users", "beta users — really"), "em dash"),
    (lambda b: b.replace("beta users", "beta users – really"), "en dash"),
])
async def test_a_rewrite_that_changes_what_matters_is_rejected(mutate, why):
    """The model is trusted with wording. A moved link is a phishing link, a
    moved address is mail to a stranger, and a promise is a promise."""
    cfg = make_config()
    draft = outreach.compose(CONTACT, cfg)
    brain = FakeBrain({"subject": draft.subject, "body": mutate(draft.body)})
    out = await outreach.refine(draft, CONTACT, cfg, brain)
    assert out.body == draft.body, why
    assert any("rejected" in n for n in out.notes), why


@pytest.mark.asyncio
async def test_an_unavailable_model_keeps_the_composed_email():
    """This is the normal case while the CLI is signed out, so it has to
    produce a complete, sendable email rather than an error."""
    cfg = make_config()
    draft = outreach.compose(CONTACT, cfg)
    out = await outreach.refine(draft, CONTACT, cfg,
                                FakeBrain(RuntimeError("not logged in")))
    assert out.body == draft.body
    check = pre_send_check(CONTACT["email"], out.subject, out.body, kind="manual")
    assert check.ok, check.reasons


@pytest.mark.asyncio
async def test_a_model_that_never_answers_does_not_hang_the_caller():
    """The signed-out CLI exits 0 with a sign-in notice, so the brain retries
    and can block for minutes. A person clicking Regenerate must not wait on
    that: the composed draft is already complete."""
    import asyncio

    class Hanging:
        async def think_json(self, prompt, **kwargs):
            await asyncio.sleep(30)

    cfg = make_config()
    draft = outreach.compose(CONTACT, cfg)
    out = await asyncio.wait_for(
        outreach.refine(draft, CONTACT, cfg, Hanging(), timeout=0.05),
        timeout=5)
    assert out.body == draft.body
    assert any("did not answer" in n for n in out.notes)


@pytest.mark.asyncio
async def test_no_brain_at_all_is_not_an_error():
    cfg = make_config()
    draft = outreach.compose(CONTACT, cfg)
    assert (await outreach.refine(draft, CONTACT, cfg, None)).body == draft.body


@pytest.mark.asyncio
async def test_junk_from_the_model_keeps_the_composed_email():
    cfg = make_config()
    draft = outreach.compose(CONTACT, cfg)
    out = await outreach.refine(draft, CONTACT, cfg, FakeBrain("not a dict"))
    assert out.body == draft.body


# ── the Outbox screen ──

@pytest.fixture
def client(monkeypatch):
    """A dashboard with its own database and its own configuration.

    Pinning the config matters as much as pinning the database. The routes
    call load_config(), which finds whatever harvey.local.yaml the machine
    happens to have, so without this the assertions below test the
    developer's own setup: they passed on a checkout carrying a real
    config and failed on a fresh clone, where the same code composes an
    email signed by the untrained template instead.
    """
    import harvey.config as config_module

    cfg = make_config(founder_name="KK")
    monkeypatch.setattr(config_module, "load_config", lambda *a, **k: cfg)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "harvey.db"
        monkeypatch.setattr(dash, "DB_PATH", db)
        monkeypatch.setattr(
            dash, "WEB_DIR",
            Path(__file__).resolve().parent.parent / "harvey" / "web")
        with TestClient(dash.app) as c:
            yield c


def seed(client, email="ruth@mdc.edu", title="Director of Career Development"):
    r = client.post("/api/contacts", json={
        "first_name": "Ruth", "last_name": "Lopez", "email": email,
        "company": "Miami Dade College", "title": title,
        "department": "Career Services"})
    assert r.status_code == 200, r.text
    r = client.post("/api/outbox/compose", json={
        "to_emails": [email], "subject": "A practical business experience",
        "body": "Hi Ruth,\n\nA line.\n\nBest,\nDana"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_preview_returns_the_stored_email_with_its_category(client):
    item_id = seed(client)
    r = client.get("/api/outbox/item/" + item_id)
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["subject"] == "A practical business experience"
    assert item["body"].startswith("Hi Ruth,")
    assert item["role_label"] == "University Career Center"
    assert item["recipient_company"] == "Miami Dade College"


def test_preview_does_not_change_what_it_previews(client):
    item_id = seed(client)
    first = client.get("/api/outbox/item/" + item_id).json()["item"]
    second = client.get("/api/outbox/item/" + item_id).json()["item"]
    assert first["subject"] == second["subject"]
    assert first["body"] == second["body"]


def test_regenerate_replaces_the_body_with_a_composed_invitation(client):
    item_id = seed(client)
    r = client.post("/api/outbox/" + item_id + "/regenerate")
    assert r.status_code == 200, r.text
    body = client.get("/api/outbox/item/" + item_id).json()["item"]["body"]
    assert "beta users" in body


def test_regenerate_refuses_an_approved_email(client):
    """Approval is the decision that lets mail out. Rewriting past it would
    quietly change what a human already said yes to."""
    item_id = seed(client)
    client.post("/api/outbox/approve-all")
    r = client.post("/api/outbox/" + item_id + "/regenerate")
    assert r.status_code == 400
    assert "draft" in r.json()["message"]


def test_delete_removes_a_draft(client):
    item_id = seed(client)
    assert client.delete("/api/outbox/" + item_id).status_code == 200
    assert client.get("/api/outbox/item/" + item_id).status_code == 404


def test_a_rejected_draft_is_still_deletable(client):
    """Rejecting records a decision; deleting removes the row. Both are fine
    for something that never left. Nothing sent is deletable, which the
    state layer enforces in its SQL."""
    item_id = seed(client)
    client.post("/api/outbox/" + item_id + "/reject")
    assert client.delete("/api/outbox/" + item_id).status_code == 200
    assert client.get("/api/outbox/item/" + item_id).status_code == 404


def test_export_carries_the_exact_body(client):
    item_id = seed(client)
    r = client.get("/api/export/outbox.csv?ids=" + item_id)
    assert r.status_code == 200
    text = r.text
    assert "A practical business experience" in text
    assert "Hi Ruth," in text
    assert "University Career Center" in text


def add_contact(client, email, title):
    r = client.post("/api/contacts", json={
        "first_name": "Pat", "last_name": "Tester", "email": email,
        "company": "Miami Dade College", "title": title})
    assert r.status_code == 200, r.text


def test_generate_preview_narrows_to_one_role(client):
    add_contact(client, "ruth@mdc.edu", "Director of Career Development")
    add_contact(client, "dean@mdc.edu", "Dean of Business")
    everyone = client.get("/api/contacts/generate-preview").json()
    deans = client.get("/api/contacts/generate-preview?role=dean").json()
    assert deans["to_generate"] < everyone["to_generate"]
    assert any(r["key"] == "dean" and r["count"] == 1
               for r in everyone["roles"])


def test_generate_preview_reports_readiness(client):
    r = client.get("/api/contacts/generate-preview").json()
    assert "readiness" in r
    assert "missing" in r["readiness"]


def test_export_can_single_out_the_unclassified(client):
    """An empty role means "no filter", so the contacts whose title named no
    role need a value of their own or they are unreachable from the screen."""
    add_contact(client, "vague@mdc.edu", "Associate Director")
    add_contact(client, "dean@mdc.edu", "Dean of Business")
    client.post("/api/outbox/compose", json={
        "to_emails": ["vague@mdc.edu"], "subject": "One", "body": "Body one."})
    client.post("/api/outbox/compose", json={
        "to_emails": ["dean@mdc.edu"], "subject": "Two", "body": "Body two."})

    everything = client.get("/api/export/outbox.csv").text
    assert "vague@mdc.edu" in everything and "dean@mdc.edu" in everything

    only_vague = client.get("/api/export/outbox.csv?role=__none__").text
    assert "vague@mdc.edu" in only_vague
    assert "dean@mdc.edu" not in only_vague


# ── Reject all ──
#
# These pin the rule the buttons depend on: a bulk action touches the ids it
# was given and nothing else. The Outbox can be filtered to three emails
# while fifty-one are waiting, and a button labelled "Reject 3 shown" that
# rejected all fifty-one would be a disaster dressed as a convenience.

def test_bulk_reject_touches_only_the_ids_it_was_given(client):
    keep = seed(client, "keep@mdc.edu")
    drop = seed(client, "drop@mdc.edu")

    r = client.post("/api/outbox/bulk", json={"ids": [drop], "action": "reject"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 1

    assert client.get("/api/outbox/item/" + drop).json()["item"]["status"] ==         "rejected"
    assert client.get("/api/outbox/item/" + keep).json()["item"]["status"] ==         "pending_review"


def test_bulk_approve_touches_only_the_ids_it_was_given(client):
    keep = seed(client, "keep@mdc.edu")
    take = seed(client, "take@mdc.edu")

    r = client.post("/api/outbox/bulk", json={"ids": [take], "action": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 1

    assert client.get("/api/outbox/item/" + take).json()["item"]["status"] ==         "approved"
    assert client.get("/api/outbox/item/" + keep).json()["item"]["status"] ==         "pending_review"


def test_rejecting_everything_leaves_nothing_on_the_review_desk(client):
    ids = [seed(client, "a@mdc.edu"), seed(client, "b@mdc.edu"),
           seed(client, "c@mdc.edu")]
    r = client.post("/api/outbox/bulk", json={"ids": ids, "action": "reject"})
    assert r.json()["changed"] == 3
    assert client.get("/api/outbox").json()["pending"] == []


def test_a_rejected_email_cannot_then_be_approved(client):
    """Rejecting is one-way, which is why the button asks first."""
    item_id = seed(client)
    client.post("/api/outbox/bulk", json={"ids": [item_id], "action": "reject"})
    again = client.post("/api/outbox/bulk",
                        json={"ids": [item_id], "action": "approve"})
    assert again.json()["changed"] == 0
    assert client.get("/api/outbox/item/" + item_id).json()["item"]["status"] ==         "rejected"


# ── Regenerate all ──
#
# The button is labelled with a count, so it has to act on exactly those
# drafts. Rewriting something a reviewer already approved would quietly
# change what a human said yes to.

def test_regenerate_all_rewrites_the_drafts_it_is_given(client):
    one = seed(client, "a@mdc.edu")
    two = seed(client, "b@mdc.edu")
    r = client.post("/api/outbox/regenerate-all", json={"ids": [one, two]})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 2
    for item_id in (one, two):
        body = client.get("/api/outbox/item/" + item_id).json()["item"]["body"]
        assert "beta users" in body


def test_regenerate_all_leaves_untouched_what_it_was_not_given(client):
    keep = seed(client, "keep@mdc.edu")
    redo = seed(client, "redo@mdc.edu")
    before = client.get("/api/outbox/item/" + keep).json()["item"]["body"]
    client.post("/api/outbox/regenerate-all", json={"ids": [redo]})
    after = client.get("/api/outbox/item/" + keep).json()["item"]["body"]
    assert after == before


def test_regenerate_all_refuses_an_approved_email(client):
    item_id = seed(client)
    client.post("/api/outbox/approve-all")
    r = client.post("/api/outbox/regenerate-all", json={"ids": [item_id]})
    assert r.json()["changed"] == 0
    assert r.json()["skipped"] == 1


def test_regenerate_all_skips_mail_with_no_contact_behind_it(client):
    """A hand-written email to someone not on the list has nothing to
    personalise from, so its wording is left alone rather than replaced."""
    r = client.post("/api/outbox/compose", json={
        "to_emails": ["stranger@example.com"], "subject": "Hand written",
        "body": "A note I typed myself."})
    item_id = r.json()["id"]
    out = client.post("/api/outbox/regenerate-all", json={"ids": [item_id]})
    assert out.json()["skipped"] == 1
    body = client.get("/api/outbox/item/" + item_id).json()["item"]["body"]
    assert body == "A note I typed myself."


def test_regenerate_all_with_no_ids_takes_everything_pending(client):
    seed(client, "a@mdc.edu")
    seed(client, "b@mdc.edu")
    r = client.post("/api/outbox/regenerate-all", json={})
    assert r.json()["changed"] == 2


# ── not paying for a model that is not there ──
#
# Every failed refine costs the full timeout. On a per-draft button that is
# fifteen seconds of nothing, every click, for as long as the CLI is signed
# out. One failure is enough to learn from.

def test_a_silent_model_is_left_alone_for_a_while():
    import harvey.dashboard as dash_mod

    dash_mod._model_spoke()
    assert not dash_mod._model_is_quiet()
    dash_mod._remember_model_silence()
    assert dash_mod._model_is_quiet()
    dash_mod._model_spoke()
    assert not dash_mod._model_is_quiet()


def test_the_silence_expires(monkeypatch):
    """It is a pause, not a verdict: signing in should start working again
    without restarting the dashboard."""
    import harvey.dashboard as dash_mod

    monkeypatch.setattr(dash_mod, "_MODEL_SILENCE_SECONDS", 0.01)
    dash_mod._remember_model_silence()
    import time as _t
    _t.sleep(0.05)
    assert not dash_mod._model_is_quiet()
    dash_mod._model_spoke()


def test_regenerating_gives_wording_that_actually_changed(client):
    """A button that returns its own output looks broken however correctly
    it ran. compose() stays deterministic for ordinary generation; asking
    again is an explicit request for something different."""
    item_id = seed(client)
    client.post("/api/outbox/" + item_id + "/regenerate")
    first = client.get("/api/outbox/item/" + item_id).json()["item"]["body"]
    client.post("/api/outbox/" + item_id + "/regenerate")
    second = client.get("/api/outbox/item/" + item_id).json()["item"]["body"]
    assert first != second


def test_repeated_regeneration_keeps_finding_new_wording(client):
    """Flipping between two drafts forever is not what anyone means by
    new wording."""
    item_id = seed(client)
    bodies = set()
    for _ in range(8):
        client.post("/api/outbox/" + item_id + "/regenerate")
        bodies.add(client.get(
            "/api/outbox/item/" + item_id).json()["item"]["body"])
    assert len(bodies) >= 4


def test_every_variant_states_the_same_facts(client):
    """Variety is allowed in the wording and nowhere else. A variant that
    moved a link or dropped the resume line would be a different offer."""
    item_id = seed(client)
    for _ in range(8):
        client.post("/api/outbox/" + item_id + "/regenerate")
        body = client.get("/api/outbox/item/" + item_id).json()["item"]["body"]
        assert "I am KK" in body or "HLI America" in body
        assert "beta users" in body
        assert "resume" in body
        assert DEMO in body
        assert REPLY in body
