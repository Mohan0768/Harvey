"""Deploy hardening: the gate, the bind refusal, and the hosted OAuth leg.

These exist because the dashboard has no accounts and no roles, serves real
people's contact details, and can send mail in your name. The rules they
pin down are the ones that make it safe to put anywhere but a laptop.
"""

import tempfile
import urllib.parse
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey import webauth

PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(dash, "DB_PATH", Path(tmp) / "harvey.db")
        monkeypatch.setattr(
            dash, "WEB_DIR",
            Path(__file__).resolve().parent.parent / "harvey" / "web")
        with TestClient(dash.app) as c:
            yield c


@pytest.fixture
def locked(monkeypatch):
    monkeypatch.setenv("HARVEY_DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("HARVEY_SESSION_SECRET", "test-secret")


# -- the gate --


def test_no_password_leaves_everything_open(client, monkeypatch):
    """The laptop case: a password on your own machine is friction for
    nothing, so the gate stays off until you set one."""
    monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
    assert client.get("/api/outbox").status_code == 200


def test_a_password_closes_the_api(client, locked):
    r = client.get("/api/outbox")
    assert r.status_code == 401
    assert "Sign in" in r.json()["message"]


def test_a_password_sends_pages_to_the_login_screen(client, locked):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_the_login_page_and_its_stylesheet_stay_reachable(client, locked):
    assert client.get("/login").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_the_app_bundle_stays_behind_the_gate(client, locked):
    """Only the stylesheet is public. Everything else waits for a session.

    follow_redirects=False matters: the gate answers with a redirect to
    /login, and a client that follows it sees a cheerful 200.
    """
    r = client.get("/static/app.js", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_the_health_check_answers_without_a_session(client, locked):
    """The platform calls this before anyone can sign in, so it has to be
    public, and it must not touch the database."""
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_the_right_password_opens_the_door(client, locked):
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    assert webauth.COOKIE_NAME in r.cookies
    assert client.get("/api/outbox").status_code == 200


def test_the_wrong_password_does_not(client, locked):
    r = client.post("/api/auth/login", json={"password": "hunter2"})
    assert r.status_code == 401
    assert webauth.COOKIE_NAME not in r.cookies
    assert client.get("/api/outbox").status_code == 401


def test_signing_out_closes_the_door_again(client, locked):
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert client.get("/api/outbox").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/outbox").status_code == 401


def test_the_session_cookie_is_http_only(client, locked):
    """Script on the page must never be able to read it."""
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert "httponly" in r.headers["set-cookie"].lower()


# -- the cookie itself --


def test_a_forged_cookie_is_rejected(locked):
    assert webauth.cookie_is_valid(webauth.issue_cookie()) is True
    forged = str(int(time.time()) + 3600) + ".deadbeef"
    assert webauth.cookie_is_valid(forged) is False
    assert webauth.cookie_is_valid("") is False
    assert webauth.cookie_is_valid("nonsense") is False


def test_an_expired_cookie_is_rejected(locked, monkeypatch):
    monkeypatch.setattr(webauth, "SESSION_HOURS", -1)
    assert webauth.cookie_is_valid(webauth.issue_cookie()) is False


def test_changing_the_password_invalidates_old_cookies(monkeypatch):
    """With no explicit session secret the key comes from the password, so
    changing it logs everyone out."""
    monkeypatch.delenv("HARVEY_SESSION_SECRET", raising=False)
    monkeypatch.setenv("HARVEY_DASHBOARD_PASSWORD", "first")
    cookie = webauth.issue_cookie()
    assert webauth.cookie_is_valid(cookie) is True
    monkeypatch.setenv("HARVEY_DASHBOARD_PASSWORD", "second")
    assert webauth.cookie_is_valid(cookie) is False


# -- refusing an unsafe bind --


def test_public_bind_without_a_password_is_refused(monkeypatch):
    monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
    with pytest.raises(dash.InsecureBindError) as e:
        dash.check_bind_is_safe("0.0.0.0")
    assert "password" in str(e.value)


def test_public_bind_with_a_password_is_allowed(locked):
    dash.check_bind_is_safe("0.0.0.0")       # must not raise


def test_loopback_never_needs_a_password(monkeypatch):
    monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
    for host in ("127.0.0.1", "localhost", "::1"):
        dash.check_bind_is_safe(host)


def test_bind_reads_the_hosts_port(monkeypatch):
    monkeypatch.delenv("HARVEY_HOST", raising=False)
    monkeypatch.setenv("PORT", "8080")
    assert dash.resolve_bind() == ("127.0.0.1", 8080)
    monkeypatch.setenv("HARVEY_HOST", "0.0.0.0")
    assert dash.resolve_bind() == ("0.0.0.0", 8080)
    assert dash.resolve_bind("1.2.3.4", 99) == ("1.2.3.4", 99)


# -- the hosted OAuth leg --


def test_callback_refuses_an_unknown_state(client, monkeypatch):
    """Without this check anyone could hand the callback a code."""
    monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
    r = client.get("/oauth/google/callback?code=abc&state=never-issued",
                   follow_redirects=False)
    assert r.status_code == 303
    assert "gmail=failed" in r.headers["location"]


def test_callback_state_is_single_use(client, monkeypatch):
    monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("HARVEY_SESSION_SECRET", "oauth-test-secret")
    state = dash._issue_oauth_state()

    first = client.get("/oauth/google/callback?error=access_denied&state=" + state,
                       follow_redirects=False)
    assert "gmail=failed" in first.headers["location"]

    second = client.get("/oauth/google/callback?code=x&state=" + state,
                        follow_redirects=False)
    detail = urllib.parse.unquote(second.headers["location"])
    assert "already been used" in detail


def test_a_restart_does_not_invalidate_a_sign_in_in_progress(client, monkeypatch):
    """The state used to live in a dict. Any restart between pressing
    Connect and returning from Google silently killed a sign-in that had
    actually worked."""
    monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("HARVEY_SESSION_SECRET", "oauth-test-secret")
    state = dash._issue_oauth_state()

    # Everything a restart would clear.
    dash._used_oauth_states.clear()

    accepted, why = dash._oauth_state_ok(state)
    assert accepted is True, why


def test_a_state_this_harvey_did_not_issue_is_refused(monkeypatch):
    monkeypatch.setenv("HARVEY_SESSION_SECRET", "oauth-test-secret")
    accepted, why = dash._oauth_state_ok("1700000000.abc.deadbeef")
    assert accepted is False
    assert "did not come from this Harvey" in why

    accepted, why = dash._oauth_state_ok("")
    assert accepted is False


def test_an_old_state_is_refused(monkeypatch):
    monkeypatch.setenv("HARVEY_SESSION_SECRET", "oauth-test-secret")
    monkeypatch.setattr(dash, "OAUTH_STATE_TTL", -1)
    accepted, why = dash._oauth_state_ok(dash._issue_oauth_state())
    assert accepted is False
    assert "expired" in why


def test_connect_returns_a_redirect_when_hosted(client, monkeypatch):
    monkeypatch.delenv("HARVEY_DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("GMAIL_REDIRECT_URI",
                       "https://harvey.example.com/oauth/google/callback")

    class Env:
        gmail_client_id = "cid.apps.googleusercontent.com"
        gmail_client_secret = "shh"

    import harvey.config as cfg
    monkeypatch.setattr(cfg, "load_env", lambda: Env())

    r = client.post("/api/gmail/connect")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "redirect"
    assert body["url"].startswith("https://accounts.google.com/")
    assert "harvey.example.com%2Foauth%2Fgoogle%2Fcallback" in body["url"]
    assert "access_type=offline" in body["url"]


def test_hosted_redirect_uri_is_empty_by_default(monkeypatch):
    from harvey.integrations.gmail import hosted_redirect_uri
    monkeypatch.delenv("GMAIL_REDIRECT_URI", raising=False)
    assert hosted_redirect_uri() == ""
