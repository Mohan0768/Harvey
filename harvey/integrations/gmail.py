"""Gmail / Google Workspace mailbox provider — raw REST, no SDK weight.

Setup (one-time):
1. Google Cloud Console → create a project → enable the Gmail API.
2. OAuth consent screen (internal or testing) → create an OAuth client of
   type "Desktop app". Put its id/secret in .env as GMAIL_CLIENT_ID /
   GMAIL_CLIENT_SECRET.
3. Run ``harvey gmail auth`` — opens a browser, stores the refresh token
   in data/gmail_token.json. Done: Harvey can send and read.

Deliverability notes: use a mailbox on a dedicated SECONDARY domain (never
your main one), configure SPF/DKIM/DMARC, ramp volume slowly, and watch
Google Postmaster Tools. Harvey's outbox pacing + kill switches handle the
rest.
"""

import base64
import json
import logging
import os
import time
import webbrowser
from email.message import EmailMessage
from email.utils import parseaddr

import httpx

from harvey.paths import PROJECT_ROOT
from harvey.integrations.mail_provider import (
    InboundMessage,
    MailProvider,
    SendResult,
    looks_like_bounce,
)

logger = logging.getLogger("harvey.gmail")

TOKEN_FILE = PROJECT_ROOT / "data" / "gmail_token.json"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/gmail.modify"
LOOPBACK_PORT = 8765
# How long the loopback listener waits for Google to come back. Named so a
# test can shorten it; five minutes is the real budget for a human who has
# to pick an account and read a consent screen.
CALLBACK_TIMEOUT = 300
# Two different hosts on purpose.
#
# REDIRECT is the string Google compares, character for character, against
# the client's registered list. "localhost" and "127.0.0.1" are different
# strings to Google even though they are the same machine, so this has to
# be whichever one was registered — changing it to the literal IP was
# enough on its own to produce redirect_uri_mismatch on a client that had
# been set up correctly.
#
# BIND is where the listener actually sits. Staying on the IPv4 address
# avoids the case where "localhost" resolves to ::1 and the server is not
# there; browsers fall back to 127.0.0.1 after a refused connection.
LOOPBACK_REDIRECT_HOST = "localhost"
LOOPBACK_BIND_HOST = "127.0.0.1"

TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


def _load_token() -> dict | None:
    try:
        if TOKEN_FILE.is_file():
            data = json.loads(TOKEN_FILE.read_text())
            if isinstance(data, dict) and data.get("refresh_token"):
                return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not read Gmail token file: {e}")
    return None


def _save_token(data: dict):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


class GmailProvider(MailProvider):
    name = "gmail"

    def __init__(self, config, env):
        self.config = config
        self.client_id = getattr(env, "gmail_client_id", "")
        self.client_secret = getattr(env, "gmail_client_secret", "")
        self._access_token = ""
        self._access_expires = 0.0

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and _load_token())

    def readiness(self) -> str:
        """Empty when ready to send; otherwise the one step still missing.

        is_configured() folds three different situations into one False,
        which leaves a person staring at "not connected" with no idea
        whether they forgot to paste a key or forgot to press Connect.
        """
        missing = []
        if not self.client_id:
            missing.append("client ID")
        if not self.client_secret:
            missing.append("client secret")
        if missing:
            return ("Gmail has no " + " and no ".join(missing) +
                    " yet. Open Settings, Email Integration, paste them and "
                    "press Save credentials.")
        if not _load_token():
            return ("Gmail has its credentials but has never been "
                    "authorised. Open Settings, Email Integration and press "
                    "Connect Gmail, then finish the Google consent screen.")
        return ""

    async def _token(self) -> str | None:
        """Valid access token, refreshing via the stored refresh token."""
        if self._access_token and time.time() < self._access_expires - 60:
            return self._access_token
        stored = _load_token()
        if not stored:
            logger.error("Gmail: not authorized. Run 'harvey gmail auth'.")
            return None
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.post(TOKEN_URL, data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": stored["refresh_token"],
                    "grant_type": "refresh_token",
                })
        except httpx.HTTPError as e:
            logger.error(f"Gmail token refresh failed: {e}")
            return None
        if resp.status_code != 200:
            logger.error(
                f"Gmail token refresh rejected ({resp.status_code}). "
                "Re-run 'harvey gmail auth'."
            )
            return None
        data = resp.json()
        self._access_token = data.get("access_token", "")
        self._access_expires = time.time() + float(data.get("expires_in", 3600))
        return self._access_token or None

    async def _request(self, method: str, path: str, **kwargs):
        token = await self._token()
        if not token:
            return None
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.request(
                    method, f"{API_BASE}{path}", headers=headers, **kwargs
                )
        except httpx.HTTPError as e:
            logger.error(f"Gmail request failed ({path}): {e}")
            return None
        if resp.status_code >= 400:
            logger.error(f"Gmail API {resp.status_code} on {path}: {resp.text[:200]}")
            return None
        try:
            return resp.json()
        except ValueError:
            return {}

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        thread_ref: str = "",
        in_reply_to: str = "",
    ) -> SendResult:
        msg = EmailMessage()
        persona = self.config.persona
        msg["To"] = to_email
        msg["From"] = f"{persona.name} <{persona.email}>" if persona.email else persona.name
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload: dict = {"raw": raw}
        if thread_ref:
            payload["threadId"] = thread_ref

        data = await self._request("POST", "/messages/send", json=payload)
        if data is None:
            return SendResult(ok=False, error="gmail send failed")

        # Fetch the assigned RFC Message-ID for bounce/thread matching later.
        message_id = ""
        gmail_id = data.get("id", "")
        if gmail_id:
            meta = await self._request(
                "GET", f"/messages/{gmail_id}",
                params={"format": "metadata", "metadataHeaders": "Message-ID"},
            )
            for h in ((meta or {}).get("payload") or {}).get("headers", []):
                if h.get("name", "").lower() == "message-id":
                    message_id = h.get("value", "")
        return SendResult(
            ok=True,
            message_id=message_id or gmail_id,
            thread_ref=data.get("threadId", ""),
        )

    async def get_replies(self, limit: int = 50) -> list[InboundMessage]:
        listing = await self._request(
            "GET", "/messages",
            params={"q": "in:inbox -from:me newer_than:7d", "maxResults": limit},
        )
        if not listing:
            return []
        out = []
        for stub in listing.get("messages", [])[:limit]:
            data = await self._request(
                "GET", f"/messages/{stub['id']}", params={"format": "full"}
            )
            if not data:
                continue
            msg = self._parse_message(data)
            if msg:
                out.append(msg)
        return out

    @staticmethod
    def _parse_message(data: dict) -> InboundMessage | None:
        payload = data.get("payload") or {}
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in payload.get("headers", [])
        }
        from_email = parseaddr(headers.get("from", ""))[1].lower()
        if not from_email:
            return None
        subject = headers.get("subject", "")
        body = GmailProvider._extract_text(payload) or data.get("snippet", "")
        return InboundMessage(
            provider_id=data.get("id", ""),
            from_email=from_email,
            subject=subject,
            body=body[:5000],
            message_id=headers.get("message-id", ""),
            in_reply_to=headers.get("in-reply-to", ""),
            thread_ref=data.get("threadId", ""),
            date=headers.get("date", ""),
            is_bounce=looks_like_bounce(from_email, subject),
            headers=headers,
        )

    @staticmethod
    def _extract_text(payload: dict) -> str:
        """Depth-first hunt for the text/plain part of a MIME tree."""
        def decode(part) -> str:
            data = (part.get("body") or {}).get("data", "")
            if not data:
                return ""
            try:
                return base64.urlsafe_b64decode(data + "===").decode(errors="replace")
            except Exception:
                return ""

        if payload.get("mimeType", "").startswith("text/plain"):
            return decode(payload)
        for part in payload.get("parts", []) or []:
            text = GmailProvider._extract_text(part)
            if text:
                return text
        if payload.get("mimeType", "").startswith("text/html"):
            import re
            html = decode(payload)
            return re.sub(r"<[^>]+>", " ", html)
        return ""

    async def test_connection(self) -> tuple[bool, str]:
        if not self.client_id or not self.client_secret:
            return False, "GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET missing from .env"
        if not _load_token():
            return False, "Not authorized yet — run 'harvey gmail auth'"
        data = await self._request("GET", "/profile")
        if data and data.get("emailAddress"):
            return True, f"Connected as {data['emailAddress']}"
        return False, "Token refresh or profile fetch failed"


def hosted_redirect_uri() -> str:
    """The redirect URI for a deployed Harvey, or "" for the laptop case.

    Set GMAIL_REDIRECT_URI to the public callback, e.g.
    https://harvey.example.com/oauth/google/callback, and add exactly that
    string to the OAuth client in Google Cloud Console. The loopback flow
    below cannot work off your own machine: localhost there is the server.
    """
    return (os.getenv("GMAIL_REDIRECT_URI") or "").strip()


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Google's consent URL. access_type=offline with prompt=consent is
    what makes Google return a refresh token rather than only an access
    token that dies in an hour."""
    import urllib.parse

    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


def exchange_code(client_id: str, client_secret: str, code: str,
                  redirect_uri: str) -> tuple[bool, str]:
    """Swap an authorization code for a refresh token and store it.

    Returns (ok, message). The message is shown to a person, so it says
    what to do rather than just what failed.
    """
    try:
        resp = httpx.post(TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }, timeout=30)
    except httpx.HTTPError as e:
        return False, f"Could not reach Google: {e}"
    if resp.status_code != 200:
        return False, f"Token exchange failed ({resp.status_code}): {resp.text[:200]}"
    data = resp.json()
    if not data.get("refresh_token"):
        return False, ("Google returned no refresh token. Remove Harvey from "
                       "your account's third-party access list and retry.")
    _save_token({
        "refresh_token": data["refresh_token"],
        "scope": data.get("scope", SCOPE),
        "obtained_at": int(time.time()),
    })
    return True, "Gmail authorized."


def run_auth_flow(client_id: str, client_secret: str, on_url=None,
                  on_message=None) -> bool:
    """One-time OAuth (installed-app loopback). Blocks until the browser
    round-trip completes; stores the refresh token for GmailProvider.

    ``on_message`` receives the reason for a failure, so a caller with a
    screen can show what actually went wrong. Without it every failure
    looks the same from outside, which is how a state mismatch spent days
    being mistaken for a missing refresh token.
    """
    def report(text: str):
        if on_message is not None:
            try:
                on_message(text)
            except Exception:
                pass
    import http.server
    import secrets
    import socket
    import threading
    import urllib.parse

    if not client_id or not client_secret:
        report("Set the client ID and secret in .env first.")
        print("  Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env first.")
        print("  (Google Cloud Console → APIs → Credentials → OAuth client, Desktop app)")
        return False

    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://{LOOPBACK_REDIRECT_HOST}:{LOOPBACK_PORT}"
    auth_url = build_auth_url(client_id, redirect_uri, state)

    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            # Logged the moment anything arrives. Without this a
            # failed round-trip is indistinguishable from a browser
            # that never knocked, and those need opposite fixes.
            logger.info(
                "Gmail callback hit: code=%s error=%s state_ok=%s",
                bool(query.get("code")), query.get("error", ["none"])[0],
                query.get("state", [""])[0] == state)
            if query.get("state", [""])[0] != state:
                logger.warning("Gmail callback rejected: state mismatch")
                self.send_response(400); self.end_headers()
                return
            result["code"] = query.get("code", [""])[0]
            result["error"] = query.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            # Deliberately does NOT say "connected": the token
            # exchange has not run yet and can still fail. Claiming
            # success here sends people back saying it worked while
            # Harvey is still waiting.
            self.wfile.write(
                b"<h2>Code received.</h2>Go back to Harvey to see "
                b"whether the connection completed.")
            done.set()

        def log_message(self, *args):
            pass

    # Listen on BOTH loopback addresses, because the redirect says
    # "localhost" and that is not one address. Windows resolves it to ::1
    # before 127.0.0.1, so a server on IPv4 alone can simply not be there
    # when the browser arrives: Google completes, the consent screen looks
    # fine, and the code never comes home. Two loopback-only listeners
    # share one handler, and whichever the browser reaches wins. Binding
    # "::" instead would cover both with one socket, but that is every
    # interface, not just this machine.
    class _Server(http.server.HTTPServer):
        # Windows allows a second socket to bind a port that is already in
        # use when SO_REUSEADDR is set, and then hands each connection to
        # whichever server bound first. A stale listener left over from an
        # abandoned attempt would therefore swallow the callback and reject
        # it for carrying a state it has never seen, which looks exactly
        # like Google never redirecting at all. Refusing to reuse the
        # address turns that silent failure into an immediate, legible one.
        allow_reuse_address = False

    class _V6Server(_Server):
        address_family = socket.AF_INET6

    servers = []
    for family_server, host in ((_Server, "127.0.0.1"),
                                (_V6Server, "::1")):
        try:
            servers.append(family_server((host, LOOPBACK_PORT), Handler))
        except OSError as exc:
            # A machine with IPv6 disabled is fine; one listener is enough.
            logger.debug("Gmail callback could not bind %s: %s", host, exc)
    if not servers:
        report("Could not listen on port " + str(LOOPBACK_PORT)
               + ". Something else is holding it.")
        print(f"  Could not listen on port {LOOPBACK_PORT}. Is it in use?")
        return False

    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()

    # Hand the URL out before opening anything. webbrowser.open uses the
    # system default, which may not be the browser you are signed into
    # Google with, and a consent link you cannot see is a dead end.
    if on_url is not None:
        try:
            on_url(auth_url)
        except Exception:
            pass
    logger.info("Gmail consent URL: %s", auth_url)
    print(f"\n  Opening browser for Google sign-in...\n  {auth_url}\n")
    webbrowser.open(auth_url)
    try:
        if not done.wait(timeout=CALLBACK_TIMEOUT):
            report("Timed out after " + str(int(CALLBACK_TIMEOUT))
                   + "s waiting for Google to redirect back.")
            print("  Timed out waiting for the browser round-trip.")
            return False
    finally:
        # shutdown() only stops serve_forever; the listening socket stays
        # bound until server_close(). A port still held after the flow ends
        # is what made every RETRY fail: the next attempt bound the same
        # port, the stale listener kept winning the callback, and the new
        # flow waited five minutes for a redirect it had already lost.
        for server in servers:
            try:
                server.shutdown()
            finally:
                server.server_close()

    if result.get("error") or not result.get("code"):
        report("Google returned " + str(result.get("error")
                                       or "no authorization code") + ".")
        print(f"  Authorization failed: {result.get('error') or 'no code returned'}")
        return False

    ok, message = exchange_code(
        client_id, client_secret, result["code"], redirect_uri)
    if not ok:
        report(message)
        print(f"  {message}")
        return False
    print(f"  Gmail authorized. Token stored at {TOKEN_FILE}")
    return True
