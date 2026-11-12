"""Password gate for the dashboard.

The dashboard has no accounts and no roles: it is one person's console. So
this is one shared password, set as HARVEY_DASHBOARD_PASSWORD, compared in
constant time and exchanged for a signed cookie.

It is OFF when no password is set, because the dashboard binds to loopback
by default and a password on your own laptop is friction with no gain. The
moment it binds anywhere else, start_dashboard refuses to run without one.
That pairing is the whole design: what sits behind this gate is real
people's contact details and a mailbox that can send in your name.
"""

import hashlib
import hmac
import os
import time

COOKIE_NAME = "harvey_session"
SESSION_HOURS = 12

# Reachable without a session: the login page and exactly what it needs to
# render and submit. Nothing else, so a new route is private by default
# rather than public by oversight.
PUBLIC_PATHS = frozenset(
    {"/login", "/api/auth/login", "/api/auth/state", "/healthz"})
PUBLIC_FILES = frozenset({"/static/app.css", "/favicon.ico"})


def configured_password() -> str:
    return (os.getenv("HARVEY_DASHBOARD_PASSWORD") or "").strip()


def is_enabled() -> bool:
    """The gate is on exactly when a password exists."""
    return bool(configured_password())


def _secret() -> bytes:
    """Key the cookies are signed with.

    Set HARVEY_SESSION_SECRET to keep sessions alive across restarts.
    Without it the key is derived from the password, so changing the
    password invalidates every existing cookie, which is the behaviour you
    want from a password change.
    """
    explicit = (os.getenv("HARVEY_SESSION_SECRET") or "").strip()
    if explicit:
        return explicit.encode()
    return hashlib.sha256(
        b"harvey-session:" + configured_password().encode()).digest()


def issue_cookie() -> str:
    """A cookie carrying only its own expiry, signed. No user data in it."""
    payload = str(int(time.time()) + SESSION_HOURS * 3600)
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + sig


def cookie_is_valid(raw: str) -> bool:
    if not raw or "." not in raw:
        return False
    payload, _, sig = raw.rpartition(".")
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(payload) > time.time()
    except ValueError:
        return False


def password_matches(attempt: str) -> bool:
    """Constant time: a wrong guess costs the same whatever it is, so the
    comparison leaks nothing about how much of it was right."""
    return hmac.compare_digest(
        (attempt or "").encode(), configured_password().encode())


def path_is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path in PUBLIC_FILES


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})


def is_loopback(host: str) -> bool:
    """Whether this bind address is reachable only from this machine."""
    return (host or "").strip().lower() in LOOPBACK_HOSTS
