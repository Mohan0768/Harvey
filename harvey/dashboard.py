"""Harvey Dashboard — local web UI to set up, control, and monitor Harvey."""

import asyncio
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response,
)

logger = logging.getLogger("harvey.dashboard")

from harvey.paths import PROJECT_ROOT  # noqa: E402
DB_PATH = PROJECT_ROOT / "data" / "harvey.db"
ENV_FILE = PROJECT_ROOT / ".env"
CONFIG_FILE = PROJECT_ROOT / "harvey.yaml"
PID_FILE = PROJECT_ROOT / "data" / "harvey.pid"
LOG_FILE = PROJECT_ROOT / "data" / "harvey.log"


def _resolve_config_file() -> Path:
    """harvey.local.yaml wins when present — same resolution as config.py's
    loader, so the dashboard doesn't judge setup complete against the
    tracked harvey.yaml template while the real trained config sits in the
    gitignored local override."""
    from harvey.config import _find_config_file, ConfigFileNotFoundError
    try:
        return Path(_find_config_file())
    except ConfigFileNotFoundError:
        return CONFIG_FILE

app = FastAPI(title="Harvey Dashboard")


# ── The gate ──
#
# Off on a loopback bind with no password set, which is the laptop case.
# On the moment HARVEY_DASHBOARD_PASSWORD exists, and start_dashboard
# refuses any non-loopback bind without it. Private by default: a new
# route is behind the gate unless it is named in webauth.PUBLIC_PATHS.


@app.middleware("http")
async def _require_login(request: Request, call_next):
    from harvey import webauth

    if not webauth.is_enabled() or webauth.path_is_public(request.url.path):
        return await call_next(request)
    if webauth.cookie_is_valid(request.cookies.get(webauth.COOKIE_NAME, "")):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        # A page that is polling must be told plainly, not handed HTML.
        return JSONResponse(
            {"success": False, "message": "Session expired. Sign in again."},
            status_code=401)
    return RedirectResponse("/login", status_code=303)


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harvey</title><link rel="stylesheet" href="/static/app.css">
<style>
  body { display:flex; align-items:center; justify-content:center;
         min-height:100vh; margin:0; }
  .signin { width:min(380px, 92vw); }
  .signin h1 { font-size:22px; margin:0 0 4px; }
  .signin p { color:var(--text-2); font-size:13px; margin:0 0 18px; }
  .signin .err { display:none; margin-bottom:12px; }
  .signin .err.show { display:block; }
</style></head>
<body><form class="signin card" onsubmit="go(event)">
  <h1>Harvey</h1>
  <p>This console holds contact records and can send mail in your name.</p>
  <div class="test-result error err" id="err"></div>
  <div class="form-group">
    <label class="form-label" for="pw">Password</label>
    <input class="form-input" id="pw" type="password" autocomplete="current-password" autofocus>
  </div>
  <button class="btn btn-primary" type="submit">Sign in</button>
</form>
<script>
async function go(e) {
  e.preventDefault();
  const err = document.getElementById('err');
  const r = await fetch('/api/auth/login', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({password: document.getElementById('pw').value})
  });
  if (r.ok) { window.location = '/'; return; }
  let m = 'Could not sign in.';
  try { m = (await r.json()).message || m; } catch (_) {}
  err.textContent = m; err.classList.add('show');
}
</script></body></html>"""


@app.get("/healthz")
async def healthz():
    """Liveness for the platform. Public, and cheap on purpose: it must not
    query the database, or a slow query would read as a dead process and
    get the container restarted in the middle of real work."""
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    from harvey import webauth
    if not webauth.is_enabled():
        return RedirectResponse("/", status_code=303)
    return LOGIN_PAGE


@app.get("/api/auth/state")
async def auth_state():
    """Whether a password is required at all, for the UI to show Sign out."""
    from harvey import webauth
    return {"required": webauth.is_enabled()}


@app.post("/api/auth/login")
async def auth_login(request: Request):
    from harvey import webauth

    if not webauth.is_enabled():
        return {"success": True, "required": False}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not webauth.password_matches(str(body.get("password") or "")):
        # A deliberate pause: enough to make a script crawl, too small to
        # notice when you simply mistyped.
        await asyncio.sleep(0.5)
        return JSONResponse(
            {"success": False, "message": "That password is not right."},
            status_code=401)

    forwarded = request.headers.get("x-forwarded-proto", "")
    https = request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"
    response = JSONResponse({"success": True})
    response.set_cookie(
        webauth.COOKIE_NAME, webauth.issue_cookie(),
        max_age=webauth.SESSION_HOURS * 3600,
        httponly=True,        # JavaScript must never be able to read it
        samesite="lax",       # survives the OAuth return from Google
        secure=https,         # only withheld on plain http, i.e. localhost
        path="/")
    return response


@app.post("/api/auth/logout")
async def auth_logout():
    from harvey import webauth
    response = JSONResponse({"success": True})
    response.delete_cookie(webauth.COOKIE_NAME, path="/")
    return response

# Harvey process tracking
_harvey_process: subprocess.Popen | None = None
_harvey_started_at: datetime | None = None
_env_lock = asyncio.Lock()


# ── Helpers ──


async def query_db(sql: str, params: tuple = ()) -> list[dict]:
    """Run a query and return results as list of dicts.

    Never raises: a missing DB file, missing table, or malformed schema
    returns [] so no dashboard route can 500 on an empty install.
    """
    if not DB_PATH.exists():
        return []
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("query_db failed (%s): %s", sql.split(None, 4)[:4], e)
        return []


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 4 and last 4 chars."""
    if not key or len(key) < 10:
        return "****" if key else ""
    return key[:4] + "****" + key[-4:]


def _read_env_file() -> dict[str, str]:
    """Read .env file and return as dict."""
    env_vars = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip()
    return env_vars


def _write_env_file(updates: dict[str, str]):
    """Update .env file with new values, preserving existing entries."""
    existing = _read_env_file()
    existing.update(updates)
    lines = [f"{k}={v}" for k, v in existing.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    load_dotenv(str(ENV_FILE), override=True)


def _check_harvey_pid() -> int | None:
    """Check if there's a running Harvey process from a PID file."""
    global _harvey_process, _harvey_started_at
    if _harvey_process and _harvey_process.poll() is None:
        return _harvey_process.pid
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            return pid
        except (ValueError, ProcessLookupError, PermissionError):
            PID_FILE.unlink(missing_ok=True)
    return None


# ── Setup Status ──


@app.get("/api/setup-status")
async def get_setup_status():
    """Check what's configured and what still needs setup."""
    checks = []

    # 1. Venv
    checks.append({
        "id": "venv", "label": "Python virtual environment",
        "done": (PROJECT_ROOT / ".venv").is_dir(),
        "required": True,
        "help": "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -e .",
    })

    # 2. Env file
    env_vars = _read_env_file()
    env_exists = ENV_FILE.exists() and bool(env_vars)
    checks.append({
        "id": "env_file", "label": "Environment file (.env)",
        "done": env_exists,
        "required": True,
        "help": "Go to the Settings tab to enter your API keys.",
    })

    # 3. Email provider configured (matches channels.email.provider)
    provider = _current_provider()

    def _has(*keys):
        return all((env_vars.get(k, "") or os.getenv(k, "")).strip() for k in keys)

    if provider == "gmail":
        provider_done = _has("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET") and \
            (PROJECT_ROOT / "data" / "gmail_token.json").is_file()
        provider_help = ("Set GMAIL_CLIENT_ID/SECRET in Settings, then run "
                         "'harvey gmail auth' in your terminal.")
    elif provider == "microsoft":
        provider_done = _has("M365_CLIENT_ID") and \
            (PROJECT_ROOT / "data" / "m365_token.json").is_file()
        provider_help = ("Set M365_CLIENT_ID and M365_TENANT_ID in .env, then run "
                         "'harvey microsoft auth' in your terminal.")
    elif provider == "smtp":
        provider_done = _has("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")
        provider_help = "Enter your SMTP host, username, and password in Settings."
    else:
        instantly_key = env_vars.get("INSTANTLY_API_KEY", "") or os.getenv("INSTANTLY_API_KEY", "")
        provider_done = bool(instantly_key) and instantly_key != "your_instantly_api_key_here"
        provider_help = "Enter your Instantly API key in Settings."
    sending = _email_sending_enabled()
    if not sending:
        provider_help = ("Not needed: channels.email.enabled is false, so you "
                         "export prospects and send them yourself.")
    checks.append({
        "id": "email_provider",
        "label": (f"Email provider configured ({provider})" if sending
                  else "Email provider (not used, export-only setup)"),
        "done": provider_done,
        "required": sending,
        "help": provider_help,
    })

    # 4. Email verification (needed for addresses to be sendable, not 'guess')
    verifier_set = _has("REOON_API_KEY") or _has("ZEROBOUNCE_API_KEY") or _has("HUNTER_API_KEY")
    checks.append({
        "id": "verifier",
        "label": ("Email verification key" if sending
                  else "Email verification (not used, export-only setup)"),
        "done": verifier_set,
        "required": sending,
        "help": ("Add a Reoon (free 600/mo) key in Settings. Without one, "
                 "addresses Harvey guesses stay 'guess' and are never sent."
                 if sending else
                 "Not needed: you are exporting addresses you already have."),
    })

    # 5. Config valid
    config_valid = False
    config_path = _resolve_config_file()
    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            company = cfg.get("persona", {}).get("company", "")
            product = cfg.get("product", {}).get("name", "")
            config_valid = company not in ("Your Company", "") and product not in ("Your Product", "")
        except Exception:
            pass
    checks.append({
        "id": "config", "label": f"Harvey configured ({config_path.name})",
        "done": config_valid,
        "required": True,
        "help": "Train Harvey on your product. Use the trainer or set up manually through Claude.",
    })

    # 6. Product trained
    product_trained = (PROJECT_ROOT / "skills" / "product_knowledge.md").exists()
    checks.append({
        "id": "product_trained", "label": "Product knowledge trained",
        "done": product_trained,
        "required": True,
        "help": "Run: harvey train https://yourwebsite.com (or set up through Claude).",
    })

    # 7. LinkedIn (optional)
    linkedin_email = env_vars.get("LINKEDIN_EMAIL", "") or os.getenv("LINKEDIN_EMAIL", "")
    linkedin_pass = env_vars.get("LINKEDIN_PASSWORD", "") or os.getenv("LINKEDIN_PASSWORD", "")
    checks.append({
        "id": "linkedin", "label": "LinkedIn credentials",
        "done": bool(linkedin_email) and bool(linkedin_pass),
        "required": False,
        "help": "Optional. Enter your LinkedIn credentials in Settings to enable LinkedIn prospecting.",
    })

    # 8. Cloudflare (optional)
    cf_id = env_vars.get("CLOUDFLARE_ACCOUNT_ID", "") or os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    cf_token = env_vars.get("CLOUDFLARE_API_TOKEN", "") or os.getenv("CLOUDFLARE_API_TOKEN", "")
    checks.append({
        "id": "cloudflare", "label": "Cloudflare deep crawling",
        "done": bool(cf_id) and bool(cf_token),
        "required": False,
        "help": "Optional. For JavaScript-rendered website crawling during training.",
    })

    required_checks = [c for c in checks if c["required"]]
    completed_required = sum(1 for c in required_checks if c["done"])

    return {
        "checks": checks,
        "completed": completed_required,
        "total_required": len(required_checks),
        "percent": int(completed_required / len(required_checks) * 100) if required_checks else 0,
    }


# ── Settings ──


def _email_sending_enabled() -> bool:
    """Is Harvey itself sending, or is this an export-only setup?

    With channels.email.enabled false the Sender and Handler skip entirely
    (the Writer still drafts), so a mail provider and a verifier key are not
    part of the setup at all. Counting them as required made an
    export-only install look permanently unfinished.
    """
    try:
        with open(_resolve_config_file()) as f:
            cfg = yaml.safe_load(f) or {}
        email = (cfg.get("channels") or {}).get("email") or {}
        return bool(email.get("enabled", True))
    except Exception:
        return True


def _current_provider() -> str:
    """Read channels.email.provider from the active config (best-effort)."""
    try:
        with open(_resolve_config_file()) as f:
            cfg = yaml.safe_load(f) or {}
        return ((cfg.get("channels") or {}).get("email") or {}).get("provider", "instantly")
    except Exception:
        return "instantly"


@app.get("/api/settings")
async def get_settings():
    """Get current settings — presence flags only for secrets, never raw values."""
    env_vars = _read_env_file()
    all_keys = [
        "INSTANTLY_API_KEY", "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD",
        "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
        "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
        "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
        "IMAP_HOST", "IMAP_PORT", "IMAP_USERNAME", "IMAP_PASSWORD",
        "REOON_API_KEY", "ZEROBOUNCE_API_KEY", "HUNTER_API_KEY",
    ]
    for key in all_keys:
        if key not in env_vars:
            env_vars[key] = os.getenv(key, "")

    def is_set(k):
        return bool((env_vars.get(k) or "").strip())

    # Gmail is authorized once harvey gmail auth has stored a token file.
    gmail_token = (PROJECT_ROOT / "data" / "gmail_token.json").is_file()

    return {
        "provider": _current_provider(),
        # False in an export-only setup: the Settings tab hides the sending
        # cards entirely rather than prompting for credentials nothing reads.
        "email_sending_enabled": _email_sending_enabled(),
        # Non-secret values echo back so fields repopulate; secrets are
        # presence-only so keys never leave the box.
        "instantly_api_key_set": is_set("INSTANTLY_API_KEY"),
        "linkedin_email": env_vars.get("LINKEDIN_EMAIL", ""),
        "linkedin_password_set": is_set("LINKEDIN_PASSWORD"),
        "cloudflare_account_id": env_vars.get("CLOUDFLARE_ACCOUNT_ID", ""),
        "cloudflare_api_token_set": is_set("CLOUDFLARE_API_TOKEN"),
        "gmail_client_id": env_vars.get("GMAIL_CLIENT_ID", ""),
        "gmail_client_secret_set": is_set("GMAIL_CLIENT_SECRET"),
        "gmail_authorized": gmail_token,
        "smtp_host": env_vars.get("SMTP_HOST", ""),
        "smtp_port": env_vars.get("SMTP_PORT", ""),
        "smtp_username": env_vars.get("SMTP_USERNAME", ""),
        "smtp_password_set": is_set("SMTP_PASSWORD"),
        "imap_host": env_vars.get("IMAP_HOST", ""),
        "imap_port": env_vars.get("IMAP_PORT", ""),
        "reoon_api_key_set": is_set("REOON_API_KEY"),
        "zerobounce_api_key_set": is_set("ZEROBOUNCE_API_KEY"),
        "hunter_api_key_set": is_set("HUNTER_API_KEY"),
    }


@app.post("/api/settings/env")
async def save_env_settings(request: Request):
    """Save environment variables to .env file."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid request body."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"success": False, "message": "Invalid request body."}, status_code=400)
    async with _env_lock:
        updates = {}
        for key in ["INSTANTLY_API_KEY", "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD",
                     "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
                     "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
                     "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
                     "IMAP_HOST", "IMAP_PORT", "IMAP_USERNAME", "IMAP_PASSWORD",
                     "REOON_API_KEY", "ZEROBOUNCE_API_KEY", "HUNTER_API_KEY"]:
            if key in data and data[key] is not None:
                # Strip newlines so a crafted value can't inject extra .env entries
                updates[key] = str(data[key]).replace("\n", " ").replace("\r", " ").strip()
        if updates:
            try:
                _write_env_file(updates)
            except Exception as e:
                logger.warning("Failed to write .env: %s", e)
                return {"success": False, "message": "Could not write .env file."}
    return {"success": True}


@app.post("/api/settings/test-instantly")
async def test_instantly(request: Request):
    """Test an Instantly API key."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    api_key = str(data.get("api_key", "") or "")
    if not api_key:
        return {"success": False, "message": "No API key provided."}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.instantly.ai/api/v2/accounts",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                return {"success": True, "message": "Connected to Instantly."}
            else:
                return {"success": False, "message": f"API returned {resp.status_code}. Check your key."}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


# ── Companies ──


@app.get("/api/companies")
async def get_companies():
    """All companies with contact counts."""
    rows = await query_db("""
        SELECT c.*,
            (SELECT COUNT(*) FROM prospects p WHERE p.company_id = c.id) as contact_count
        FROM companies c ORDER BY c.created_at DESC LIMIT 200
    """)
    return rows


@app.get("/api/companies/{company_id}/contacts")
async def get_company_contacts(company_id: str):
    """Get all contacts for a specific company."""
    rows = await query_db(
        "SELECT * FROM prospects WHERE company_id = ? ORDER BY score DESC",
        (company_id,),
    )
    return rows


# ── Feedback ──


@app.post("/api/feedback")
async def add_feedback(request: Request):
    """Add a comment/feedback on any entity."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    entity_type = str(data.get("entity_type", "") or "")[:50]
    entity_id = str(data.get("entity_id", "") or "")[:100]
    comment = str(data.get("comment", "") or "").strip()[:4000]
    if not comment:
        return {"success": False, "message": "Comment is required."}
    feedback_id = uuid.uuid4().hex[:12]
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            # Ensure the table exists so feedback works even on a fresh install
            await db.execute(
                """CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    entity_id TEXT,
                    comment TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )"""
            )
            await db.execute(
                "INSERT INTO feedback (id, entity_type, entity_id, comment) VALUES (?, ?, ?, ?)",
                (feedback_id, entity_type, entity_id, comment),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to save feedback: %s", e)
        return {"success": False, "message": "Could not save feedback."}
    return {"success": True, "id": feedback_id}


@app.get("/api/feedback/{entity_type}/{entity_id}")
async def get_feedback(entity_type: str, entity_id: str):
    """Get feedback for an entity."""
    rows = await query_db(
        "SELECT * FROM feedback WHERE entity_type = ? AND entity_id = ? ORDER BY created_at DESC",
        (entity_type, entity_id),
    )
    return rows


# ── Harvey Controls ──


@app.get("/api/harvey/status")
async def get_harvey_status():
    """Check if Harvey is currently running."""
    pid = _check_harvey_pid()
    started = _harvey_started_at.isoformat() if _harvey_started_at else None
    return {"running": pid is not None, "pid": pid, "started_at": started}


@app.post("/api/harvey/start")
async def start_harvey():
    """Start Harvey's heartbeat loop as a subprocess."""
    global _harvey_process, _harvey_started_at

    if _check_harvey_pid():
        return {"success": False, "message": "Harvey is already running."}

    # Ensure data dir exists
    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)

    try:
        log_handle = open(LOG_FILE, "a")
        try:
            _harvey_process = subprocess.Popen(
                [sys.executable, "-m", "harvey"],
                cwd=str(PROJECT_ROOT),
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
            )
        finally:
            # Child holds its own copies of the fds; don't leak ours.
            log_handle.close()
    except Exception as e:
        logger.warning("Failed to start Harvey: %s", e)
        return {"success": False, "message": f"Failed to start Harvey: {e}"}
    _harvey_started_at = datetime.now()

    # Write PID file
    try:
        PID_FILE.write_text(str(_harvey_process.pid))
    except OSError as e:
        logger.warning("Could not write PID file: %s", e)

    return {"success": True, "pid": _harvey_process.pid}


@app.post("/api/harvey/stop")
async def stop_harvey():
    """Stop the Harvey subprocess."""
    global _harvey_process, _harvey_started_at

    pid = _check_harvey_pid()
    if not pid:
        return {"success": False, "message": "Harvey is not running."}

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for graceful shutdown
        for _ in range(10):
            try:
                os.kill(pid, 0)
                await asyncio.sleep(0.5)
            except ProcessLookupError:
                break
        else:
            # Force kill if still running
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except (ProcessLookupError, PermissionError):
        pass

    _harvey_process = None
    _harvey_started_at = None
    PID_FILE.unlink(missing_ok=True)

    return {"success": True}


@app.get("/api/harvey/logs")
async def get_harvey_logs():
    """Get recent log lines."""
    if not LOG_FILE.exists():
        return {"lines": []}
    try:
        # Tail only the last 64KB so a huge log file never blocks the UI
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            text = f.read().decode("utf-8", errors="replace")
        lines = text.strip().splitlines()[-100:]
        return {"lines": lines}
    except Exception:
        return {"lines": []}


# ── Pipeline Data (existing endpoints) ──


@app.get("/api/stats")
async def get_stats():
    """Pipeline overview stats."""
    try:
        prospects = await query_db(
            "SELECT status, COUNT(*) as count FROM prospects GROUP BY status"
        )
        prospect_total = sum(r["count"] for r in prospects)
        prospect_map = {r["status"]: r["count"] for r in prospects}

        campaigns = await query_db(
            "SELECT status, COUNT(*) as count FROM campaigns GROUP BY status"
        )
        campaign_map = {r["status"]: r["count"] for r in campaigns}

        conversations = await query_db(
            "SELECT status, COUNT(*) as count FROM conversations GROUP BY status"
        )
        convo_map = {r["status"]: r["count"] for r in conversations}

        actions = await query_db("SELECT COUNT(*) as count FROM actions")
        action_count = actions[0]["count"] if actions else 0

        # usage_log rows are written with Python's date.today(), which is
        # LOCAL. SQLite's date('now') is UTC, so east of Greenwich this read
        # missed the row every evening and the tab showed zero calls.
        usage = await query_db(
            "SELECT claude_calls, codex_calls FROM usage_log WHERE date = ?",
            (datetime.now().date().isoformat(),),
        )
        usage_today = (usage[0]["claude_calls"] or 0) if usage else 0
        codex_today = (usage[0]["codex_calls"] or 0) if usage else 0

        return {
            "prospects": {"total": prospect_total, "by_status": prospect_map},
            "campaigns": {"total": sum(campaign_map.values()), "by_status": campaign_map},
            "conversations": {"total": sum(convo_map.values()), "by_status": convo_map},
            "actions_total": action_count,
            "claude_calls_today": usage_today,
            "codex_calls_today": codex_today,
            "brain_provider": _active_provider(),
            "brain_status": await _brain_status(),
        }
    except Exception as e:
        return {"error": str(e)}


_USAGE_SUM = (
    "COUNT(DISTINCT CASE WHEN session_id != '' THEN session_id ELSE id END) AS calls, "
    "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
    "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
    "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
    "COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens, "
    "ROUND(COALESCE(SUM(cost_usd), 0), 4) AS cost_usd"
)

_quota_clients: dict = {}


def _active_brain():
    """The brain config harvey.yaml selects (None if unreadable)."""
    try:
        from harvey.config import load_brain_config
        return load_brain_config()
    except Exception:
        return None


async def _brain_status() -> dict:
    """Last recorded brain health transition ({} if none): lets the
    dashboard say "Codex paused: <why>" instead of looking healthy."""
    from harvey.brain import BRAIN_STATUS_KEY, active_brain_status
    rows = await query_db("SELECT value FROM settings WHERE key = ?", (BRAIN_STATUS_KEY,))
    # Only the configured provider's health (a stale Codex pause isn't
    # Claude's).
    return active_brain_status(rows[0]["value"], _active_provider()) if rows else {}


def _active_provider() -> str:
    brain = _active_brain()
    return brain.provider if brain else ""


def _quota_client_for(provider: str, model: str = ""):
    """One cached quota reader per provider (and Codex model). Each reads
    only its own subscription, so a Claude gauge is never shown as Codex or
    vice versa, and Codex shows the bucket metering the configured model."""
    key = (provider, model if provider == "codex" else "")
    if key not in _quota_clients:
        if provider == "codex":
            from harvey.codex import CodexClient, CodexQuotaClient
            _quota_clients[key] = CodexQuotaClient(CodexClient(model=model))
        elif provider == "claude":
            from harvey.integrations.quota import QuotaClient
            _quota_clients[key] = QuotaClient()
        else:
            return None
    return _quota_clients[key]


@app.get("/api/usage")
async def get_usage():
    """Token/cost accounting + live subscription quota for the Usage tab."""

    totals = {}
    for label, where in (
        ("today", "date(created_at) = date('now')"),
        ("week", "created_at >= datetime('now', '-7 days')"),
        ("month", "created_at >= datetime('now', '-30 days')"),
    ):
        rows = await query_db(f"SELECT {_USAGE_SUM} FROM usage_events WHERE {where}")
        totals[label] = rows[0] if rows else {}

    def grouped(expr, alias):
        return (
            f"SELECT {expr} AS {alias}, {_USAGE_SUM} FROM usage_events "
            f"WHERE created_at >= datetime('now', '-30 days') "
            f"GROUP BY {alias} ORDER BY output_tokens DESC LIMIT 25"
        )

    by_agent = await query_db(grouped("CASE WHEN agent = '' THEN 'other' ELSE agent END", "agent"))
    by_task = await query_db(grouped("CASE WHEN task = '' THEN 'other' ELSE task END", "task"))
    by_model = await query_db(grouped("CASE WHEN model = '' THEN 'unknown' ELSE model END", "model"))
    by_provider = await query_db(grouped("provider", "provider"))
    by_day = await query_db(
        f"SELECT date(created_at) AS day, {_USAGE_SUM} FROM usage_events "
        f"WHERE created_at >= datetime('now', '-30 days') "
        f"GROUP BY day ORDER BY day ASC"
    )

    brain = _active_brain()
    provider = brain.provider if brain else ""
    quota = None
    try:
        client = _quota_client_for(provider, brain.model if brain else "")
        if client is not None:
            quota = await client.get_utilization()
    except Exception as e:
        logger.debug("Quota lookup failed: %s", e)

    return {
        "provider": provider,
        "quota": quota,
        # Whose quota this is, even when it couldn't be read, so the UI shows
        # "unavailable" for the active provider instead of hiding the gauge.
        "quota_provider": provider,
        "quota_status": ("available" if quota else "unavailable") if provider else "",
        "by_provider": by_provider,
        "totals": totals,
        "by_day": by_day,
        "by_agent": by_agent,
        "by_task": by_task,
        "by_model": by_model,
    }


@app.get("/api/prospects")
async def get_prospects():
    # The Contacts tab filters and paginates client side, so it wants the
    # whole list rather than the first page of it.
    rows = await query_db(
        "SELECT * FROM prospects ORDER BY created_at DESC LIMIT 5000")
    return rows


# ── Contacts: import, manual entry, edit, delete ──
#
# DISCOVER can only find what its providers map. For an audience it cannot
# enumerate, these let a human bring the list instead. Both paths land in
# the same prospects table the rest of Harvey already reads.


def _contact_payload(body: dict) -> dict:
    """Normalise one contact from the UI or a parsed file row."""
    first = str(body.get("first_name") or "").strip()
    last = str(body.get("last_name") or "").strip()
    if not (first or last):
        from harvey.importer import split_name
        first, last = split_name(body.get("name") or "")
    return {
        "first_name": first,
        "last_name": last,
        "email": str(body.get("email") or "").strip().lower(),
        "phone": str(body.get("phone") or "").strip(),
        "title": str(body.get("title") or "").strip(),
        "company": str(body.get("company") or "").strip(),
        # department feeds both {{department}} in a custom template
        # and the segment classifier, so dropping it here made every
        # hand-added and imported contact read "your work in ." .
        "department": str(body.get("department") or "").strip(),
        "linkedin_url": str(body.get("linkedin_url") or "").strip(),
        "status": str(body.get("status") or "new").strip() or "new",
        "source": str(body.get("source") or "manual").strip() or "manual",
    }


async def _save_contact(state, data: dict, on_duplicate: str = "skip") -> str:
    """Insert a contact, or skip/update when the email already exists.

    Returns "added", "updated" or "skipped". Email is the matching field; a
    contact with no email is always inserted, because there is nothing to
    match it against.
    """
    from harvey.models.prospect import Prospect

    existing = None
    if data["email"]:
        existing = await state.get_prospect_by_email(data["email"])
    if existing:
        if on_duplicate != "update":
            return "skipped"
        await state.update_prospect_fields(existing.id, **data)
        return "updated"
    await state.add_prospect(Prospect(**data))
    return "added"


@app.post("/api/contacts/preview")
async def contacts_preview(file: UploadFile = File(...)):
    """Parse an upload and report what WOULD be imported. Writes nothing."""
    from harvey.importer import parse
    try:
        raw = await file.read()
        result = parse(file.filename or "", raw)
    except ValueError as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=400)
    except Exception as e:
        return JSONResponse(
            {"success": False, "message": "Could not read that file: " + str(e)},
            status_code=400)

    state = _state()
    await state.init_db()
    seen, dupes = set(), 0
    for row in result["rows"]:
        email = row.get("email") or ""
        row["duplicate"] = False
        if not email:
            continue
        if email in seen or await state.get_prospect_by_email(email):
            row["duplicate"] = True
            dupes += 1
        seen.add(email)

    return {
        "success": True,
        "filename": file.filename,
        "mapped": result["mapped"],
        "headers": result["headers"],
        "truncated": result["truncated"],
        "rows": result["rows"],
        "failed": result["failed"],
        "counts": {"valid": len(result["rows"]), "duplicates": dupes,
                   "failed": len(result["failed"])},
    }


@app.post("/api/contacts/import")
async def contacts_import(request: Request):
    """Save the rows the user confirmed in the preview."""
    try:
        body = await request.json()
        rows = body.get("rows") or []
        on_duplicate = body.get("on_duplicate") or "skip"
        if not rows:
            return JSONResponse({"success": False, "message": "Nothing to import."},
                                status_code=400)
        state = _state()
        await state.init_db()
        added = updated = skipped = failed = 0
        errors = []
        for row in rows:
            try:
                data = _contact_payload(row)
                if not (data["first_name"] or data["last_name"]):
                    failed += 1
                    continue
                outcome = await _save_contact(state, data, on_duplicate)
                added += outcome == "added"
                updated += outcome == "updated"
                skipped += outcome == "skipped"
            except Exception as e:
                failed += 1
                if len(errors) < 5:
                    errors.append(str(e))
        return {"success": True, "added": added, "updated": updated,
                "skipped": skipped, "failed": failed, "errors": errors}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/contacts")
async def contacts_create(request: Request):
    """Add one contact by hand."""
    try:
        body = await request.json()
        data = _contact_payload(body)
        if not (data["first_name"] or data["last_name"]):
            return JSONResponse({"success": False, "message": "A name is required."},
                                status_code=400)
        from harvey.importer import EMAIL_RE
        if data["email"] and not EMAIL_RE.match(data["email"]):
            return JSONResponse(
                {"success": False,
                 "message": data["email"] + " is not a valid email address."},
                status_code=400)
        state = _state()
        await state.init_db()
        outcome = await _save_contact(state, data, body.get("on_duplicate") or "skip")
        if outcome == "skipped":
            return JSONResponse(
                {"success": False,
                 "message": data["email"] + " is already in your contacts."},
                status_code=409)
        return {"success": True, "outcome": outcome}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.put("/api/contacts/{prospect_id}")
async def contacts_update(prospect_id: str, request: Request):
    """Edit one contact from the Contacts tab."""
    try:
        body = await request.json()
        from harvey.importer import EMAIL_RE
        email = str(body.get("email") or "").strip().lower()
        if email and not EMAIL_RE.match(email):
            return JSONResponse(
                {"success": False, "message": email + " is not a valid email address."},
                status_code=400)
        state = _state()
        await state.init_db()
        ok = await state.update_prospect_fields(prospect_id, **body)
        if not ok:
            return JSONResponse(
                {"success": False, "message": "Contact not found, or nothing changed."},
                status_code=404)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.delete("/api/contacts/{prospect_id}")
async def contacts_delete(prospect_id: str):
    """Delete a contact, and any mail still queued for them."""
    try:
        state = _state()
        await state.init_db()
        ok = await state.delete_prospect(prospect_id)
        if not ok:
            return JSONResponse({"success": False, "message": "Contact not found."},
                                status_code=404)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


# ── Gmail connection ──
#
# The OAuth flow itself already exists in harvey.integrations.gmail: an
# installed-app loopback on port 8765 with state validation, offline access
# and a refresh token. These endpoints put it behind the Settings tab so it
# does not need a terminal, and add disconnect and a test send.

_gmail_connect: dict = {"running": False, "ok": None, "message": ""}


@app.get("/api/gmail/status")
async def gmail_status():
    """Real connection state. Never guessed from the presence of a key."""
    try:
        from harvey.integrations.gmail import SCOPE, TOKEN_FILE, LOOPBACK_PORT
        from harvey.config import load_env

        env = load_env()
        token = None
        if TOKEN_FILE.is_file():
            try:
                token = json.loads(TOKEN_FILE.read_text())
            except Exception:
                token = None

        address = ""
        reachable = None
        detail = ""
        if token and token.get("refresh_token"):
            try:
                from harvey.config import load_config
                from harvey.integrations.gmail import GmailProvider
                ok, msg = await GmailProvider(load_config(), env).test_connection()
                reachable, detail = bool(ok), str(msg or "")
                if ok and "@" in detail:
                    address = detail.split()[-1].strip("<>")
            except Exception as e:
                reachable, detail = False, str(e)

        return {
            "success": True,
            "configured": bool(env.gmail_client_id and env.gmail_client_secret),
            "authorized": bool(token and token.get("refresh_token")),
            "reachable": reachable,
            "detail": detail,
            "address": address,
            "scope": (token or {}).get("scope") or SCOPE,
            "redirect_uri": "http://localhost:" + str(LOOPBACK_PORT),
            "token_path": str(TOKEN_FILE),
            "connecting": _gmail_connect,
        }
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


# Outstanding OAuth states for the hosted flow: issued here, returned by
# Google, used once. Without this check anyone could hand the callback a
# code of their choosing.
_oauth_states: dict[str, float] = {}
OAUTH_STATE_TTL = 900


def _prune_oauth_states():
    cutoff = time.time() - OAUTH_STATE_TTL
    for key in [k for k, born in _oauth_states.items() if born < cutoff]:
        _oauth_states.pop(key, None)


@app.get("/oauth/google/callback")
async def gmail_oauth_callback(request: Request):
    """Where Google sends the person back after the consent screen.

    Behind the login gate like everything else: the browser arriving here
    is the one that started the flow, so it still carries the session.
    """
    from harvey.config import load_env
    from harvey.integrations.gmail import exchange_code, hosted_redirect_uri

    def done(ok: bool, message: str):
        return RedirectResponse(
            "/?gmail=" + ("connected" if ok else "failed") +
            "&detail=" + urllib.parse.quote(message[:200]), status_code=303)

    params = request.query_params
    state = params.get("state", "")
    _prune_oauth_states()
    if not state or _oauth_states.pop(state, None) is None:
        return done(False, "That sign-in link has expired. Press Connect Gmail again.")
    if params.get("error"):
        return done(False, "Google said: " + params["error"])
    code = params.get("code", "")
    if not code:
        return done(False, "Google returned no authorization code.")

    env = load_env()
    ok, message = await asyncio.to_thread(
        exchange_code, env.gmail_client_id, env.gmail_client_secret,
        code, hosted_redirect_uri())
    return done(ok, message)


@app.post("/api/gmail/connect")
async def gmail_connect():
    """Start the Google consent flow. Opens the browser on this machine."""
    global _gmail_connect
    try:
        from harvey.config import load_env
        from harvey.integrations.gmail import run_auth_flow

        from harvey.integrations.gmail import build_auth_url, hosted_redirect_uri

        env = load_env()
        if not (env.gmail_client_id and env.gmail_client_secret):
            return JSONResponse(
                {"success": False,
                 "message": "Set the client ID and secret first, then save."},
                status_code=400)

        # Deployed: Google must redirect to a public URL, because the
        # loopback flow below opens a browser on the machine running this
        # code and listens on its localhost. On a server that is the
        # server, and the consent screen can never find its way back.
        hosted = hosted_redirect_uri()
        if hosted:
            state = secrets.token_urlsafe(16)
            _oauth_states[state] = time.time()
            _prune_oauth_states()
            return {"success": True, "mode": "redirect",
                    "url": build_auth_url(env.gmail_client_id, hosted, state)}

        if _gmail_connect.get("running"):
            return JSONResponse({"success": False,
                                 "message": "A connection attempt is already open."},
                                status_code=409)

        _gmail_connect = {"running": True, "ok": None,
                          "message": "Waiting for Google sign-in in your browser."}

        async def _go():
            global _gmail_connect
            try:
                ok = await asyncio.to_thread(
                    run_auth_flow, env.gmail_client_id, env.gmail_client_secret)
                _gmail_connect = {
                    "running": False, "ok": bool(ok),
                    "message": ("Connected." if ok else
                                "Google did not return a refresh token. Remove "
                                "Harvey from your account's third-party access "
                                "list and try again."),
                }
            except Exception as exc:
                logger.exception("gmail connect failed")
                _gmail_connect = {"running": False, "ok": False,
                                  "message": str(exc)}

        asyncio.create_task(_go())
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.post("/api/gmail/disconnect")
async def gmail_disconnect():
    """Revoke the refresh token at Google, then delete it locally.

    Deleting the file alone would leave Harvey authorised on the Google
    account with nothing here to show for it.
    """
    try:
        import httpx

        from harvey.integrations.gmail import TOKEN_FILE

        revoked = False
        detail = ""
        if TOKEN_FILE.is_file():
            try:
                token = json.loads(TOKEN_FILE.read_text())
                rt = token.get("refresh_token")
                if rt:
                    async with httpx.AsyncClient(timeout=20) as c:
                        r = await c.post("https://oauth2.googleapis.com/revoke",
                                         data={"token": rt})
                    revoked = r.status_code == 200
                    if not revoked:
                        detail = "Google returned " + str(r.status_code) + " on revoke."
            except Exception as e:
                detail = str(e)
            try:
                TOKEN_FILE.unlink()
            except OSError as e:
                return JSONResponse(
                    {"success": False,
                     "message": "Could not remove the token file: " + str(e)},
                    status_code=500)
        return {"success": True, "revoked": revoked, "detail": detail}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.post("/api/gmail/test")
async def gmail_test(request: Request):
    """Send one real email to an address you choose, to prove delivery."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        to = str(body.get("to") or "").strip().lower()
        from harvey.importer import EMAIL_RE
        if not EMAIL_RE.match(to):
            return JSONResponse(
                {"success": False, "message": (to or "That address") +
                 " is not a valid email address."}, status_code=400)

        from harvey.config import load_config, load_env
        from harvey.integrations.gmail import GmailProvider

        provider = GmailProvider(load_config(), load_env())
        if not provider.is_configured():
            return JSONResponse({"success": False,
                                 "message": "Gmail is not connected yet."},
                                status_code=400)
        result = await provider.send_email(
            to_email=to,
            subject="Harvey test email",
            body=("This is a test from Harvey. If you are reading it, the "
                  "Gmail connection works and approved mail can go out.\n\n"
                  "Nothing else was sent."))
        ok = bool(getattr(result, "ok", False) or getattr(result, "success", False))
        return {
            "success": ok,
            "message": getattr(result, "error", "") or ("Sent to " + to),
            "message_id": getattr(result, "message_id", "") or "",
        }
    except Exception as e:
        logger.exception("gmail test send failed")
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


def _state():
    from harvey.state import StateManager
    return StateManager(db_path=str(DB_PATH))


# kind is how the row was written; the review screen shows it in the words
# the spec uses, so a person can tell a template run from one they typed.
_EMAIL_TYPE = {"manual": "Custom", "sequence": "Automated", "reply": "Reply"}


async def _decorate_outbox(rows: list) -> list:
    """Attach the recipient's name to each row.

    The address alone is a poor thing to approve against: "ymalave@mdc.edu"
    does not tell you who you are writing to, and the whole point of this
    screen is an informed yes or no.
    """
    rows = list(rows or [])
    ids = {r.get("prospect_id") for r in rows if r.get("prospect_id")}
    names = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for p in await query_db(
            "SELECT id, first_name, last_name, company, department "
            "FROM prospects WHERE id IN (" + marks + ")", tuple(ids)
        ):
            names[p["id"]] = p
    for r in rows:
        who = names.get(r.get("prospect_id")) or {}
        full = ((who.get("first_name") or "") + " " +
                (who.get("last_name") or "")).strip()
        r["recipient_name"] = full
        r["recipient_company"] = who.get("company") or ""
        r["recipient_department"] = who.get("department") or ""
        r["email_type"] = _EMAIL_TYPE.get(r.get("kind") or "", "Automated")
    return rows


@app.get("/api/outbox")
async def get_outbox_api():
    """Outbox queue + kill-switch state for the Outbox tab."""
    try:
        state = _state()
        await state.init_db()
        return {
            "paused": await state.get_setting("sending_paused"),
            "drafts": await _decorate_outbox(
                await state.get_outbox(status="draft", limit=50)),
            "pending": await _decorate_outbox(
                await state.get_outbox(status="pending_review", limit=100)),
            "approved": await _decorate_outbox(
                await state.get_outbox(status="approved", limit=50)),
            "sending": await _decorate_outbox(
                await state.get_outbox(status="sending", limit=25)),
            "sent": await _decorate_outbox(await query_db(
                "SELECT * FROM outbox WHERE status = 'sent' "
                "ORDER BY sent_at DESC LIMIT 25")),
            "failed": await _decorate_outbox(await query_db(
                "SELECT * FROM outbox WHERE status IN "
                "('failed','rejected','cancelled') "
                "ORDER BY updated_at DESC LIMIT 25")),
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/outbox/approve-all")
async def outbox_approve_all():
    try:
        state = _state()
        await state.init_db()
        n = await state.approve_outbox()
        return {"success": True, "approved": n}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


# ── Bulk draft generation ──
#
# One click writes a personalised draft for every eligible contact, straight
# into the SAME outbox as everything else, at pending_review. Generation and
# sending stay separate: nothing here sends, and nothing here approves.

_BULK_SKIP_STATUSES = {"replied", "opted_out", "lost", "meeting", "closed"}


async def _bulk_eligibility(state):
    """Split every contact into generate / skip, with the reason for each skip."""
    from harvey.importer import EMAIL_RE

    rows = await query_db("SELECT * FROM prospects ORDER BY created_at DESC")
    queued_rows = await query_db(
        "SELECT DISTINCT prospect_id FROM outbox "
        "WHERE status IN ('pending_review', 'approved')")
    queued = {r["prospect_id"] for r in queued_rows if r["prospect_id"]}

    eligible = []
    skipped = {"no_email": 0, "bad_email": 0, "suppressed": 0,
               "already_queued": 0}
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        if not email:
            skipped["no_email"] += 1
        elif not EMAIL_RE.match(email):
            skipped["bad_email"] += 1
        elif (r.get("status") or "") in _BULK_SKIP_STATUSES:
            skipped["suppressed"] += 1
        elif r["id"] in queued:
            skipped["already_queued"] += 1
        else:
            eligible.append(r)
    return rows, eligible, skipped


@app.get("/api/contacts/generate-preview")
async def contacts_generate_preview():
    """What a bulk run would do. Writes nothing, generates nothing."""
    try:
        state = _state()
        await state.init_db()
        rows, eligible, skipped = await _bulk_eligibility(state)

        by_category = {}
        for r in eligible:
            key = r.get("category") or ""
            by_category[key] = by_category.get(key, 0) + 1

        return {
            "success": True,
            "total": len(rows),
            "to_generate": len(eligible),
            "skipped": skipped,
            "by_category": by_category,
        }
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.get("/api/contacts/generate-status")
async def contacts_generate_status():
    return {
        "running": bool(_generate_task and not _generate_task.done()),
        "progress": _generate_progress,
    }


@app.post("/api/contacts/generate")
async def contacts_generate(request: Request):
    """Write one personalised draft per eligible contact, into the outbox.

    Runs in the background: each draft is a model call, so fifty of them is
    minutes. Poll /api/contacts/generate-status for progress. Nothing here
    sends and nothing here approves.
    """
    global _generate_task, _generate_progress

    if _generate_task and not _generate_task.done():
        return JSONResponse({"success": False, "message": "a run is already going"},
                            status_code=409)
    try:
        raw = await request.body()
        body = await request.json() if raw else {}
        limit = int(body.get("limit") or 0)

        state = _state()
        await state.init_db()
        _, eligible, _ = await _bulk_eligibility(state)
        if limit:
            eligible = eligible[:limit]
        if not eligible:
            return JSONResponse(
                {"success": False, "message": "No eligible contacts to write for."},
                status_code=400)

        started = time.time()
        _generate_progress = {"done": 0, "total": len(eligible), "contact": "",
                              "created": 0, "failed": 0, "blocked": 0,
                              "started_at": started, "errors": []}

        async def _go():
            try:
                await _generate_all()
            except Exception as exc:
                # Without this an exception here dies with the task and the
                # UI just shows a run that did nothing, with no reason.
                logger.exception("bulk generate crashed")
                _generate_progress["errors"].append("run failed: " + str(exc))
                _generate_progress["crashed"] = True
                _generate_progress["done"] = _generate_progress["total"]

        async def _generate_all():
            from datetime import datetime, timezone

            from harvey.agents.writer import Writer
            from harvey.brain import Brain
            from harvey.config import load_config, load_env
            from harvey.gate import pre_send_check
            from harvey.models.prospect import Prospect

            cfg = load_config()
            writer = Writer(Brain(state, cfg.brain), state, cfg, load_env())
            writer.skills = writer.brain.load_skills_for_agent("writer")
            provider = _current_provider()

            for i, row in enumerate(eligible):
                _generate_progress["done"] = i
                name = ((row.get("first_name") or "") + " " +
                        (row.get("last_name") or "")).strip()
                _generate_progress["contact"] = name or row.get("email") or ""
                try:
                    fields = {k: v for k, v in row.items()
                              if k in Prospect.model_fields}
                    prospect = Prospect(**fields)
                    draft = await writer._write_personal_email(prospect)
                    if not draft:
                        _generate_progress["failed"] += 1
                        if len(_generate_progress["errors"]) < 8:
                            _generate_progress["errors"].append(
                                prospect.email + ": the model returned nothing")
                        continue

                    check = pre_send_check(prospect.email, draft["subject"],
                                           draft["body"], prospect=prospect,
                                           kind="sequence")
                    if not check.ok:
                        _generate_progress["blocked"] += 1
                        if len(_generate_progress["errors"]) < 8:
                            _generate_progress["errors"].append(
                                prospect.email + ": " + "; ".join(check.reasons))
                        continue

                    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                    item = await state.add_outbox_item(
                        prospect_id=prospect.id, to_email=prospect.email,
                        subject=draft["subject"], body=draft["body"],
                        send_at=now, status="pending_review",
                        kind="sequence", provider=provider)
                    if item:
                        _generate_progress["created"] += 1
                    else:
                        _generate_progress["failed"] += 1
                except Exception as exc:
                    logger.exception("bulk generate failed for one contact")
                    _generate_progress["failed"] += 1
                    if len(_generate_progress["errors"]) < 8:
                        _generate_progress["errors"].append(str(exc))
            _generate_progress["done"] = len(eligible)

        _generate_task = asyncio.create_task(_go())
        return {"success": True, "to_generate": len(eligible),
                "started_at": started}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.post("/api/contacts/generate-stop")
async def contacts_generate_stop():
    """Cancel a bulk run. Drafts already written stay in the outbox.

    A run is one model call per contact, so a fifty-contact run is tens of
    minutes. Starting one with no way out is not a real control.
    """
    global _generate_task
    if not (_generate_task and not _generate_task.done()):
        return {"success": True, "stopped": False}
    _generate_task.cancel()
    if _generate_progress is not None:
        _generate_progress["stopped"] = True
        _generate_progress["done"] = _generate_progress.get("total", 0)
    return {"success": True, "stopped": True}


@app.post("/api/outbox/bulk")
async def outbox_bulk(request: Request):
    """Approve or reject several drafts at once.

    Approving does not send: it moves rows to approved, and the sender
    drains them on its own pacing, through the same gate as everything else.
    """
    try:
        body = await request.json()
        ids = [str(x) for x in (body.get("ids") or [])]
        action = body.get("action")
        if action not in ("approve", "reject"):
            return JSONResponse({"success": False, "message": "Unknown action."},
                                status_code=400)
        if not ids:
            return JSONResponse({"success": False, "message": "Nothing selected."},
                                status_code=400)

        state = _state()
        await state.init_db()
        rows = await state.get_outbox(status="pending_review", limit=1000)
        pending = {r["id"] for r in rows}
        status = "approved" if action == "approve" else "rejected"
        changed = 0
        for item_id in ids:
            if item_id not in pending:
                continue
            await state.update_outbox_item(item_id, status=status)
            changed += 1
        return {"success": True, "changed": changed,
                "skipped": len(ids) - changed, "status": status}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


# ── Custom template emails ──
#
# A template the user writes, filled in per contact. No model call, so this
# works whether or not the Claude CLI is signed in, and the result is
# predictable: what you typed, with that person's details in it.

CUSTOM_FIELDS = ("first_name", "last_name", "name", "title", "company",
                 "department", "category", "trial_link", "calendly_link",
                 "sender_name", "sender_company")


def _custom_values(row: dict, cfg) -> dict:
    from harvey.segments import label as cat_label

    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    offer = cfg.product.offer
    trial = offer.entry or ""
    # entry reads "Free trial of X: https://..." — the link is what a
    # template wants, not the sentence around it.
    match = re.search(r"https?://\S+", trial)
    return {
        "first_name": first,
        "last_name": last,
        "name": (first + " " + last).strip(),
        "title": (row.get("title") or "").strip(),
        "company": (row.get("company") or "").strip(),
        "department": (row.get("department") or "").strip(),
        "category": cat_label(row.get("category") or ""),
        "trial_link": match.group(0) if match else "",
        "calendly_link": offer.booking_url or "",
        "sender_name": cfg.persona.name or "",
        "sender_company": cfg.persona.company or "",
    }


def _render_template(text: str, values: dict) -> str:
    """Replace {{field}} with the contact's value. Unknown fields are left
    alone so a typo shows up in the preview instead of vanishing."""
    out = text or ""
    for key in CUSTOM_FIELDS:
        val = values.get(key, "")
        out = out.replace("{{" + key + "}}", val)
        out = out.replace("{{ " + key + " }}", val)
    return out


async def _custom_audience(scope: str):
    """Who a custom-template run would write to, and what it would replace.

    scope "new" - only contacts with nothing queued. Same rule the bulk
        generate button uses, so the two buttons never fight over a contact.
    scope "all" - everyone on the list. A draft still awaiting review is
        replaced, because you have not acted on it yet. A draft you already
        APPROVED is left alone and its contact skipped: approval is the one
        gate that lets mail out, and rewriting past it would quietly undo a
        decision you made.
    """
    from harvey.importer import EMAIL_RE

    rows = await query_db("SELECT * FROM prospects ORDER BY created_at DESC")
    pending = {r["prospect_id"] for r in await query_db(
        "SELECT DISTINCT prospect_id FROM outbox "
        "WHERE status = 'pending_review'") if r["prospect_id"]}
    approved = {r["prospect_id"] for r in await query_db(
        "SELECT DISTINCT prospect_id FROM outbox "
        "WHERE status = 'approved'") if r["prospect_id"]}

    targets, replacing = [], set()
    skipped = {"no_email": 0, "bad_email": 0, "suppressed": 0,
               "already_queued": 0, "approved_locked": 0}
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        if not email:
            skipped["no_email"] += 1
        elif not EMAIL_RE.match(email):
            skipped["bad_email"] += 1
        elif (r.get("status") or "") in _BULK_SKIP_STATUSES:
            skipped["suppressed"] += 1
        elif r["id"] in approved:
            skipped["approved_locked"] += 1
        elif scope != "all" and r["id"] in pending:
            skipped["already_queued"] += 1
        else:
            if r["id"] in pending:
                replacing.add(r["id"])
            targets.append(r)
    return rows, targets, skipped, replacing


def _no_targets_message(scope: str, skipped: dict) -> str:
    """Say which rule emptied the list, and what to do about it. A bare
    "no eligible contacts" sends people hunting through the UI."""
    if scope != "all" and skipped.get("already_queued"):
        return (str(skipped["already_queued"]) + " contact(s) already have a "
                "draft waiting. Switch the audience to Everyone to replace "
                "those drafts with this template.")
    if skipped.get("approved_locked"):
        return (str(skipped["approved_locked"]) + " contact(s) have an "
                "approved draft, which this will not overwrite. Send or "
                "cancel those in the Outbox first.")
    if skipped.get("no_email") or skipped.get("bad_email"):
        return ("No contact has a usable email address. Add one, or import a "
                "sheet that includes them.")
    if skipped.get("suppressed"):
        return (str(skipped["suppressed"]) + " contact(s) are suppressed "
                "(replied, opted out or closed), so none are left to write to.")
    return "There are no contacts to write to yet."


@app.post("/api/contacts/custom-preview")
async def contacts_custom_preview(request: Request):
    """Render the template against one real contact. Writes nothing."""
    try:
        body = await request.json()
        from harvey.config import load_config

        scope = "all" if (body.get("scope") or "new") == "all" else "new"
        state = _state()
        await state.init_db()
        _, targets, skipped, replacing = await _custom_audience(scope)
        if not targets:
            return JSONResponse(
                {"success": False, "message": _no_targets_message(scope, skipped),
                 "skipped": skipped},
                status_code=400)

        cfg = load_config()
        # Preview against a contact whose fields are actually filled in, so
        # the sample is representative. Previewing against the emptiest
        # record makes a good template look broken.
        fields_used = set(re.findall(r"\{\{\s*(\w+)\s*\}\}",
                                     (body.get("subject") or "") +
                                     " " + (body.get("body") or "")))
        contact_fields = [f for f in fields_used
                          if f in ("company", "department", "title",
                                   "first_name", "category")]

        def completeness(r):
            vals = _custom_values(r, cfg)
            return sum(1 for f in contact_fields if (vals.get(f) or "").strip())

        row = max(targets, key=completeness) if contact_fields else targets[0]
        incomplete = [r for r in targets if completeness(r) < len(contact_fields)]
        values = _custom_values(row, cfg)
        subject = _render_template(body.get("subject") or "", values)
        text = _render_template(body.get("body") or "", values)

        from harvey.gate import pre_send_check
        check = pre_send_check(row.get("email") or "", subject, text, kind="manual")
        leftovers = sorted(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", subject + " " + text)))

        return {
            "success": True,
            "to": row.get("email"),
            "for": values["name"],
            "subject": subject,
            "body": text,
            "scope": scope,
            "eligible": len(targets),
            "replacing": len(replacing),
            "skipped": skipped,
            "unknown_fields": leftovers,
            "incomplete": len(incomplete),
            "incomplete_sample": [
                ((r.get("first_name") or "") + " " + (r.get("last_name") or "")).strip()
                or r.get("email") for r in incomplete[:4]],
            "gate_ok": check.ok,
            "gate_reasons": check.reasons,
            "fields": list(CUSTOM_FIELDS),
        }
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.post("/api/contacts/custom-generate")
async def contacts_custom_generate(request: Request):
    """Create one draft per contact from the template. Never sends."""
    try:
        body = await request.json()
        subject_t = body.get("subject") or ""
        body_t = body.get("body") or ""
        if not subject_t.strip() or not body_t.strip():
            return JSONResponse(
                {"success": False, "message": "Subject and body are both required."},
                status_code=400)

        from datetime import datetime, timezone

        from harvey.config import load_config
        from harvey.gate import pre_send_check

        scope = "all" if (body.get("scope") or "new") == "all" else "new"
        state = _state()
        await state.init_db()
        _, targets, skipped, replacing = await _custom_audience(scope)
        if not targets:
            return JSONResponse(
                {"success": False, "message": _no_targets_message(scope, skipped)},
                status_code=400)

        cfg = load_config()
        provider = _current_provider()
        created = blocked = failed = replaced = 0
        errors = []
        for row in targets:
            try:
                values = _custom_values(row, cfg)
                subject = _render_template(subject_t, values)
                text = _render_template(body_t, values)
                email = row.get("email") or ""
                check = pre_send_check(email, subject, text, kind="manual")
                if not check.ok:
                    # Checked BEFORE anything is cancelled, so a blocked
                    # template never costs you the draft you already had.
                    blocked += 1
                    if len(errors) < 6:
                        errors.append(email + ": " + "; ".join(check.reasons))
                    continue
                now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
                item = await state.add_outbox_item(
                    prospect_id=row["id"], to_email=email, subject=subject,
                    body=text, send_at=now, status="pending_review",
                    kind="manual", provider=provider)
                if item:
                    created += 1
                    if row["id"] in replacing:
                        # add_outbox_item returns the new row id.
                        replaced += await state.cancel_pending_review_for_prospect(
                            row["id"], "replaced_by_custom_template",
                            except_id=item)
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                if len(errors) < 6:
                    errors.append(str(exc))
        return {"success": True, "created": created, "blocked": blocked,
                "failed": failed, "replaced": replaced, "scope": scope,
                "skipped": skipped, "errors": errors}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.post("/api/outbox/compose")
async def outbox_compose(request: Request):
    """Queue a hand-written email. Never sends.

    This is the Write Email / Custom Mail composer. It lands in exactly the
    same outbox the Writer uses, so the one approval step stays the only
    way anything leaves. kind is "manual" so it sits outside the campaign
    double-send guard and outside the gate's deliverability rule: you may
    legitimately write to an address Harvey has not verified, as long as
    you are the one choosing to.

    status "draft" parks it before the review queue; "pending_review" puts
    it straight on the approval desk. Several recipients produce several
    separate emails, one per person, never a CC list: a cold email with
    four strangers in the To line is a different and worse message.
    """
    try:
        body = await request.json()
        subject = str(body.get("subject") or "").strip()
        text = str(body.get("body") or "").strip()
        status = "draft" if (body.get("status") or "") == "draft" \
            else "pending_review"

        raw = body.get("to_emails")
        if not isinstance(raw, list) or not raw:
            raw = [body.get("to_email") or ""]
        seen, recipients = set(), []
        for entry in raw:
            addr = str(entry or "").strip().lower()
            if addr and addr not in seen:
                seen.add(addr)
                recipients.append(addr)

        from harvey.importer import EMAIL_RE
        if not recipients:
            return JSONResponse(
                {"success": False, "message": "A recipient is required."},
                status_code=400)
        bad = [a for a in recipients if not EMAIL_RE.match(a)]
        if bad:
            return JSONResponse(
                {"success": False,
                 "message": bad[0] + " is not a valid email address."},
                status_code=400)
        if not subject or not text:
            return JSONResponse(
                {"success": False, "message": "Subject and body are both required."},
                status_code=400)

        state = _state()
        await state.init_db()

        from datetime import datetime, timezone

        from harvey.gate import pre_send_check

        created, linked, failures = [], 0, []
        for addr in recipients:
            # Link it to the contact when we already know them, so the email
            # shows up against the right person later.
            prospect = await state.get_prospect_by_email(addr)
            prospect_id = prospect.id if prospect else ""

            check = pre_send_check(addr, subject, text, kind="manual")
            if not check.ok:
                failures.append(addr + ": " + "; ".join(check.reasons))
                continue

            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            item_id = await state.add_outbox_item(
                prospect_id=prospect_id, to_email=addr, subject=subject,
                body=text, send_at=now, status=status,
                kind="manual", provider=_current_provider(),
            )
            if item_id:
                created.append(item_id)
                linked += 1 if prospect_id else 0
            else:
                failures.append(addr + ": could not be queued")

        if not created:
            return JSONResponse(
                {"success": False,
                 "message": "That email would not pass the pre-send gate: "
                            + "; ".join(failures)[:400]},
                status_code=400)

        return {"success": True, "id": created[0], "ids": created,
                "created": len(created), "status": status,
                "linked_contact": bool(linked), "failures": failures}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.post("/api/outbox/{item_id}/to-outbox")
async def outbox_promote(item_id: str):
    """Move a saved draft onto the approval desk. Still does not send."""
    try:
        state = _state()
        await state.init_db()
        item = await state.get_outbox_item(item_id)
        if item is None:
            return JSONResponse(
                {"success": False, "message": "That draft no longer exists."},
                status_code=404)
        if (item.get("status") or "") != "draft":
            return JSONResponse(
                {"success": False,
                 "message": "Only a saved draft can be moved to the Outbox; "
                            "this one is " + (item.get("status") or "unknown") + "."},
                status_code=400)
        await state.update_outbox_item(item_id, status="pending_review")
        return {"success": True, "status": "pending_review"}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.put("/api/outbox/{item_id}")
async def outbox_edit(item_id: str, request: Request):
    """Edit a draft's subject or body before it is approved.

    Only while it is still pending_review: an approved item may already be
    mid-flight, and a sent one cannot be unsent, so editing either would be
    a lie about what went out.
    """
    try:
        body = await request.json()
        subject = str(body.get("subject") or "").strip()
        text = str(body.get("body") or "").strip()
        if not subject or not text:
            return JSONResponse(
                {"success": False, "message": "Subject and body are both required."},
                status_code=400)

        state = _state()
        await state.init_db()
        rows = await state.get_outbox(status="pending_review", limit=500)
        item = next((r for r in rows if r["id"] == item_id), None)
        if not item:
            return JSONResponse(
                {"success": False,
                 "message": "That draft is no longer pending review."},
                status_code=404)

        # The same deterministic gate the sender runs, so a human edit cannot
        # walk an email past the checks Harvey applies to its own drafts.
        from harvey.gate import pre_send_check
        check = pre_send_check(item["to_email"], subject, text,
                               kind=item.get("kind") or "sequence")
        blocking = [r for r in check.reasons
                    if "email_status" not in r and "recipient" not in r]
        if blocking:
            return JSONResponse(
                {"success": False,
                 "message": "That edit would not pass the pre-send gate: "
                            + "; ".join(blocking)},
                status_code=400)

        await state.update_outbox_item(item_id, subject=subject, body=text)
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.post("/api/outbox/{item_id}/approve")
async def outbox_approve(item_id: str):
    """Approve this one email and send it, now, through the mail provider.

    Approving IS the authorisation to send, so the two are one action and
    one audit entry. Bulk approve-all deliberately does not send: approving
    fifty emails in a click is a staging decision, sending them is not.

    Sending is claimed atomically in the database, so a double-click, a
    refresh, or a second browser tab cannot put the same email out twice.
    """
    try:
        from harvey.config import load_config, load_env
        from harvey.sending import send_outbox_item

        state = _state()
        await state.init_db()
        outcome = await send_outbox_item(
            state, load_config(), load_env(), item_id)
        payload = {
            "success": outcome.ok,
            "status": outcome.status,
            "message": outcome.message,
            "message_id": outcome.message_id,
            "thread_ref": outcome.thread_ref,
            "already": outcome.already,
        }
        if outcome.ok:
            return payload
        return JSONResponse(payload, status_code=400)
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/outbox/{item_id}/reject")
async def outbox_reject(item_id: str):
    try:
        state = _state()
        await state.init_db()
        await state.update_outbox_item(item_id, status="rejected")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/sending/{action}")
async def sending_toggle(action: str):
    if action not in ("pause", "resume"):
        return JSONResponse({"success": False, "message": "unknown action"}, status_code=400)
    try:
        state = _state()
        await state.init_db()
        if action == "pause":
            await state.set_setting("sending_paused", "paused from dashboard")
        else:
            await state.set_setting("sending_paused", "")
            await state.set_setting("bounce_count", "0")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/export/prospects.csv")
async def export_prospects(all: bool = False, min_score: int = 0, email_status: str = ""):
    """Sequencer-ready CSV download of the prospect list."""
    from harvey.state import StateManager
    from harvey.export import export_prospects_csv

    state = StateManager(db_path=str(DB_PATH))
    try:
        await state.init_db()
        statuses = [s.strip() for s in email_status.split(",") if s.strip()] or None
        _, text = await export_prospects_csv(
            state, email_statuses=statuses, min_score=min_score, include_all=all,
        )
    except Exception as e:
        logger.warning("Prospect export failed: %s", e)
        text = ""
    return PlainTextResponse(
        text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="prospects.csv"'},
    )


@app.get("/api/campaigns")
async def get_campaigns():
    rows = await query_db("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 100")
    for row in rows:
        try:
            row["sequence"] = json.loads(row.get("sequence_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["sequence"] = []
        try:
            row["prospect_ids"] = json.loads(row.get("prospect_ids_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["prospect_ids"] = []
    return rows


@app.get("/api/conversations")
async def get_conversations():
    rows = await query_db("""
        SELECT c.*, p.first_name, p.last_name, p.email as prospect_email, p.company
        FROM conversations c
        LEFT JOIN prospects p ON c.prospect_id = p.id
        ORDER BY c.updated_at DESC LIMIT 100
    """)
    for row in rows:
        try:
            row["thread"] = json.loads(row.get("thread_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["thread"] = []
    return rows


@app.post("/api/conversations/refresh")
async def conversations_refresh():
    """Pull new replies from the mailbox and file them on their threads.

    Grouping is by the provider thread id, which is the same id stored on
    the outbox row when the email went out, so a reply lands in the
    conversation its original message started.

    The dedup keys are prefixed on purpose. The Handler agent uses the same
    processed-reply store to decide what it still has to read; consuming a
    bare key here would make the agent skip a reply it has never seen.
    """
    try:
        from harvey.config import load_config, load_env
        from harvey.integrations.mail_provider import get_mail_provider
        from harvey.models.conversation import Message

        provider = get_mail_provider(load_config(), load_env())
        if provider is None or not provider.is_configured():
            detail = provider.readiness() if (
                provider is not None and hasattr(provider, "readiness")) else ""
            name = provider.name.capitalize() if provider else "No provider"
            return JSONResponse(
                {"success": False,
                 "message": detail or (name + " is not connected yet. Connect "
                                       "it under Settings, Email Integration.")},
                status_code=400)

        state = _state()
        await state.init_db()
        replies = await provider.get_replies(limit=50)

        added = 0
        touched = set()
        unmatched = 0
        for msg in replies:
            key = "convo:" + (msg.provider_id or msg.message_id or "")
            if key == "convo:" or await state.is_reply_processed(key):
                continue
            convo = await state.get_conversation_by_thread(
                getattr(msg, "thread_ref", "") or "")
            if convo is None:
                # No thread match: fall back to the sender's address, so a
                # reply from a client that breaks threading is not lost.
                prospect = await state.get_prospect_by_email(msg.from_email)
                if prospect is not None:
                    rows = await query_db(
                        "SELECT id FROM conversations WHERE prospect_id = ? "
                        "ORDER BY updated_at DESC LIMIT 1", (prospect.id,))
                    if rows:
                        convo = await state.get_conversation(rows[0]["id"])
            if convo is None:
                unmatched += 1
                continue

            convo.thread.append(Message(
                sender="prospect", content=msg.body or msg.subject or ""))
            await state.update_conversation(
                convo.id, thread_json=convo.thread_json(), status="replied")
            if convo.prospect_id:
                await state.update_prospect_status(convo.prospect_id, "replied")
            await state.mark_reply_processed(key)
            added += 1
            touched.add(convo.id)

        await state.log_action(
            action_type="conversations_refreshed", agent="dashboard",
            details={"fetched": len(replies), "added": added,
                     "unmatched": unmatched})
        return {"success": True, "fetched": len(replies), "added": added,
                "conversations": len(touched), "unmatched": unmatched}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.get("/api/activity")
async def get_activity():
    rows = await query_db("SELECT * FROM actions ORDER BY created_at DESC LIMIT 100")
    for row in rows:
        try:
            row["details"] = json.loads(row.get("details_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            row["details"] = {}
    return rows


# ── Dashboard UI ──


# ── Discovery: the provider menu, an estimate, and a run ──
#
# This is the only stage that spends money, so the UI never starts one without
# showing what it will cost first.

_discovery_task: asyncio.Task | None = None
_discovery_report: dict | None = None
# Live position of a running discovery, so the dashboard can show real
# progress instead of a spinner that says nothing.
_discovery_progress: dict | None = None
_generate_task: asyncio.Task | None = None
_generate_progress: dict | None = None


def _discovery_queries(body: dict, config):
    from harvey.collectors.discover import build_queries

    cities = [c.strip() for c in (body.get("cities") or []) if c.strip()] or None
    terms = [t.strip() for t in (body.get("terms") or []) if t.strip()] or None
    return build_queries(
        config, cities=cities, terms=terms,
        depth=int(body.get("depth") or 30),
        limit=int(body.get("limit") or 100),
    )


@app.get("/api/discover/configs")
async def discover_configs_list():
    """Saved search configurations, newest first."""
    try:
        state = _state()
        await state.init_db()
        raw = await state.get_setting("discovery_configs")
        return {"success": True, "configs": json.loads(raw) if raw else []}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.post("/api/discover/configs")
async def discover_configs_save(request: Request):
    """Save or replace one configuration by name, or delete it.

    Stored as a settings row rather than a new table: it is a short list of
    user preferences, not a relation anything joins against.
    """
    try:
        body = await request.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return JSONResponse({"success": False, "message": "A name is required."},
                                status_code=400)

        state = _state()
        await state.init_db()
        raw = await state.get_setting("discovery_configs")
        configs = json.loads(raw) if raw else []
        configs = [c for c in configs if c.get("name") != name]

        if not body.get("delete"):
            configs.insert(0, {
                "name": name,
                "cities": [c.strip() for c in (body.get("cities") or []) if c.strip()],
                "terms": [t.strip() for t in (body.get("terms") or []) if t.strip()],
                "titles": [t.strip() for t in (body.get("titles") or []) if t.strip()],
                "depth": int(body.get("depth") or 30),
                "limit": int(body.get("limit") or 100),
            })
        configs = configs[:30]
        await state.set_setting("discovery_configs", json.dumps(configs))
        return {"success": True, "configs": configs}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)},
                            status_code=500)


@app.get("/api/discover/providers")
async def get_discovery_providers():
    """What each source does, what it costs, and whether it's ready to use."""
    try:
        from harvey.collectors.discover import DEFAULT_PROVIDER, provider_menu
        from harvey.config import load_env

        state = _state()
        await state.init_db()
        return {
            "providers": provider_menu(load_env().model_dump()),
            "default": DEFAULT_PROVIDER,
            "selected": await state.get_setting("discovery_provider") or DEFAULT_PROVIDER,
            "paused": await state.get_setting("discovery_paused"),
            "running": bool(_discovery_task and not _discovery_task.done()),
            "progress": _discovery_progress,
            "last_report": _discovery_report,
        }
    except Exception as e:
        logger.exception("discovery providers failed")
        return {"providers": [], "error": str(e)}


@app.post("/api/discover/estimate")
async def estimate_discovery(request: Request):
    """Projected spend and the exact query list, before anything is called."""
    try:
        from harvey.collectors.discover import PROVIDERS, estimate_cost
        from harvey.config import load_config

        body = await request.json()
        provider = body.get("provider") or ""
        if provider not in PROVIDERS:
            return JSONResponse({"error": f"unknown provider {provider!r}"},
                                status_code=400)

        queries = _discovery_queries(body, load_config())
        return {
            "provider": provider,
            "queries": [q.keyword() for q in queries],
            "query_count": len(queries),
            "estimated_cost": round(estimate_cost(provider, queries), 4),
            "free": PROVIDERS[provider].estimate(queries) == 0,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/discover/run")
async def start_discovery(request: Request):
    """Kick off a run in the background and hand back immediately.

    Discovery takes minutes, not milliseconds — holding the request open
    would just time out. Progress shows up in the run log.
    """
    global _discovery_task, _discovery_report, _discovery_progress

    if _discovery_task and not _discovery_task.done():
        return JSONResponse({"success": False, "message": "a run is already going"},
                            status_code=409)
    try:
        from harvey.collectors.discover import PROVIDERS
        from harvey.config import load_config
        from harvey.pipeline import run_prospecting

        body = await request.json()
        provider = body.get("provider") or ""
        if provider not in PROVIDERS:
            return JSONResponse({"success": False,
                                 "message": f"unknown provider {provider!r}"},
                                status_code=400)

        config = load_config()
        queries = _discovery_queries(body, config)
        max_spend = float(body.get("max_spend") or 1.0)

        state = _state()
        await state.init_db()
        await state.set_setting("discovery_provider", provider)
        await state.set_setting("discovery_paused", "")

        def _progress(done: int, total: int, keyword: str):
            global _discovery_progress
            _discovery_progress = {
                "done": done, "total": total, "query": keyword,
                "started_at": started,
            }

        async def _go():
            global _discovery_report, _discovery_progress
            try:
                # Discovery chains straight into profiling: reading the sites
                # is free, and it is what makes the results worth anything.
                result = await run_prospecting(state, config, provider, queries,
                                               max_spend=max_spend,
                                               on_progress=_progress)
                _discovery_report = {
                    **(result.discover or {}),
                    "profiled_companies": result.profiled_companies,
                    "profile_observations": result.profile_observations,
                    "errors": result.errors,
                }
            except Exception as exc:
                logger.exception("discovery run failed")
                _discovery_report = {"errors": [str(exc)], "stopped": "failed"}

        _discovery_report = None
        started = time.time()
        _discovery_progress = {"done": 0, "total": len(queries), "query": "",
                               "started_at": started}
        _discovery_task = asyncio.create_task(_go())
        return {"success": True, "queries": len(queries),
                "started_at": started}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/profile/run")
async def start_profile():
    """Read the websites of everything discovered but not yet looked at.

    Free and model-free, so there is nothing to estimate and no cap to set.
    """
    global _discovery_task, _discovery_report

    if _discovery_task and not _discovery_task.done():
        return JSONResponse({"success": False, "message": "a run is already going"},
                            status_code=409)
    try:
        from harvey.pipeline import run_profile_stage

        state = _state()
        await state.init_db()
        pending = await state.count_companies_needing_profile()
        if not pending:
            return {"success": True, "pending": 0}

        async def _go():
            global _discovery_report
            try:
                companies, observations, _ = await run_profile_stage(state, limit=200)
                _discovery_report = {
                    "profiled_companies": companies,
                    "profile_observations": observations,
                }
            except Exception as exc:
                logger.exception("profile run failed")
                _discovery_report = {"errors": [str(exc)], "stopped": "failed"}

        _discovery_report = None
        _discovery_task = asyncio.create_task(_go())
        return {"success": True, "pending": pending}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/discover/stop")
async def stop_discovery():
    """Kill switch. Read between batches, so an in-flight run stops cleanly."""
    try:
        state = _state()
        await state.init_db()
        await state.set_setting("discovery_paused", "stopped from dashboard")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/today")
async def get_today():
    """What needs a human, right now.

    The dashboard opens here rather than on a setup checklist: the question a
    returning user actually has is "is anything waiting on me?", and the
    answer is usually a short list or nothing at all.
    """
    items: list[dict] = []
    stats: dict = {}
    try:
        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        await seed_signal_catalog(state)

        codes = await state.get_signal_codes()
        proposed = [c for c in codes if c.get("status") == "proposed"]
        confirmed = [c for c in codes if c.get("status") == "confirmed"]
        pending = await state.get_outbox(status="pending_review", limit=200)
        approved = await state.get_outbox(status="approved", limit=200)
        paused = await state.get_setting("sending_paused")
        counts = await state.count_prospects_by_status()

        convos = await query_db(
            "SELECT COUNT(*) AS n FROM conversations WHERE status = 'open'"
        )
        open_convos = convos[0]["n"] if convos else 0
        companies = await query_db("SELECT COUNT(*) AS n FROM companies")
        n_companies = companies[0]["n"] if companies else 0

        unprofiled_count = await state.count_companies_needing_profile()
        stats = {
            "companies": n_companies,
            "prospects": sum(counts.values()),
            "signals_confirmed": len(confirmed),
            "outbox_pending": len(pending),
            "outbox_approved": len(approved),
            "open_conversations": open_convos,
            "unprofiled": unprofiled_count,
        }

        # Ordered by how much it blocks Harvey from doing anything at all.
        if paused:
            items.append({
                "key": "paused", "tone": "bad",
                "title": "Sending is paused",
                "detail": str(paused) + ". Nothing will go out until you resume it.",
                "action": "Review and resume", "tab": "outbox",
            })

        setup = await get_setup_status()
        if isinstance(setup, dict) and setup.get("percent", 100) < 100:
            missing = [c["label"] for c in setup.get("checks", [])
                       if c.get("required") and not c.get("done")]
            items.append({
                "key": "setup", "tone": "warn",
                "title": "Finish setting Harvey up",
                "detail": ", ".join(missing[:3]) or "Some required steps are incomplete.",
                "action": "Open setup", "tab": "settings",
            })

        if proposed:
            items.append({
                "key": "signals", "tone": "warn",
                "title": (f"{len(proposed)} signal waiting for your confirmation"
                          if len(proposed) == 1
                          else f"{len(proposed)} signals waiting for your confirmation"),
                "detail": ("Harvey won't collect anything you haven't approved. "
                           "Confirm which signals define a good prospect for you."),
                "action": "Review signals", "tab": "signals",
            })
        elif not confirmed:
            items.append({
                "key": "signals-none", "tone": "warn",
                "title": "No signals confirmed",
                "detail": "Every signal is rejected, so prospecting has nothing to collect.",
                "action": "Review signals", "tab": "signals",
            })

        if not n_companies and confirmed:
            items.append({
                "key": "discover", "tone": "good",
                "title": "No companies yet",
                "detail": ("Signals are confirmed but nothing has been collected. "
                           "Discovery is free to try — no account needed."),
                "action": "Find businesses", "tab": "discover",
            })

        unprofiled = unprofiled_count
        if unprofiled:
            items.append({
                "key": "profile", "tone": "good",
                "title": f"{unprofiled} companies not looked at yet",
                "detail": ("Reading their websites is free and it is what makes "
                           "an email specific — who their agency is, what they "
                           "are missing, whether they are spending on ads."),
                "action": "Read their sites", "tab": "discover",
            })

        if pending:
            items.append({
                "key": "outbox", "tone": "warn",
                "title": (f"{len(pending)} email waiting for approval" if len(pending) == 1
                          else f"{len(pending)} emails waiting for approval"),
                "detail": "Nothing sends until you approve it. Read them one at a time.",
                "action": "Open the decisions desk", "tab": "outbox",
            })

        if open_convos:
            items.append({
                "key": "replies", "tone": "good",
                "title": (f"{open_convos} live conversation" if open_convos == 1
                          else f"{open_convos} live conversations"),
                "detail": "People replied. Check how Harvey is handling them.",
                "action": "Read conversations", "tab": "conversations",
            })

        return {"items": items, "stats": stats}
    except Exception as e:
        logger.exception("today load failed")
        return {"items": [], "stats": stats, "error": str(e)}


# ── Signals: Harvey proposes, the user confirms ──
#
# Nothing is collected until a human has said yes to it. This is the gate the
# whole prospecting pipeline hangs off: collectors ask `state.confirmed_signal_codes()`
# and skip anything that isn't in the set.

CATEGORY_META = {
    "discovery": {
        "label": "Discovery — who exists",
        "blurb": "How Harvey finds businesses at all, and how visible they are. "
                 "This is the only stage that costs money.",
    },
    "profile": {
        "label": "Profile — what they are",
        "blurb": "Read from the pages a business already publishes. Free, no AI "
                 "tokens, three HTTP requests per company. These are the signals "
                 "that make an email specific.",
    },
    "people": {
        "label": "People — who decides",
        "blurb": "Named humans and whether they're the one who can say yes.",
    },
    "verification": {
        "label": "Contactability — can you reach them",
        "blurb": "Whether the address will actually deliver, and what to do when "
                 "it won't.",
    },
}
CATEGORY_ORDER = ["discovery", "profile", "people", "verification"]


@app.get("/api/signals")
async def get_signals():
    """The signal vocabulary, grouped for review, with live cohort sizes."""
    try:
        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        # Seeding is idempotent and never overrides a decision the user made,
        # so it is safe to run on every load — new signals shipped in an
        # upgrade show up as `proposed` without any migration step.
        await seed_signal_catalog(state)

        codes = await state.get_signal_codes()
        counts = {c["signal_code"]: c for c in await state.signal_counts()}

        groups, summary = [], {"proposed": 0, "confirmed": 0, "rejected": 0}
        for cat in CATEGORY_ORDER:
            rows = []
            for sig in codes:
                if sig.get("category") != cat:
                    continue
                seen = counts.get(sig["code"], {})
                rows.append({
                    **sig,
                    "companies": seen.get("companies", 0),
                    "observations": seen.get("observations", 0),
                })
            if rows:
                meta = CATEGORY_META.get(cat, {})
                groups.append({
                    "key": cat,
                    "label": meta.get("label", cat.title()),
                    "blurb": meta.get("blurb", ""),
                    "signals": rows,
                })
        for sig in codes:
            summary[sig.get("status", "proposed")] = (
                summary.get(sig.get("status", "proposed"), 0) + 1
            )

        return {"summary": summary, "groups": groups, "total": len(codes)}
    except Exception as e:
        logger.exception("signals load failed")
        return {"error": str(e), "groups": [], "summary": {}}


@app.post("/api/signals/status")
async def set_signals_status(request: Request):
    """Confirm or reject one signal, or a whole category at once."""
    try:
        body = await request.json()
        status = (body.get("status") or "").strip()
        codes = body.get("codes") or ([body["code"]] if body.get("code") else [])
        if status not in ("proposed", "confirmed", "rejected"):
            return JSONResponse(
                {"success": False, "message": f"invalid status: {status!r}"},
                status_code=400,
            )
        if not codes:
            return JSONResponse(
                {"success": False, "message": "no signal codes given"}, status_code=400
            )

        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        # Seed first: a confirm that arrives before anything has loaded the
        # catalog would otherwise report success while changing nothing.
        await seed_signal_catalog(state)

        changed, unknown = 0, []
        for code in codes:
            if await state.set_signal_status(code, status):
                changed += 1
            else:
                unknown.append(code)
        return {
            "success": True, "changed": changed,
            "status": status, "unknown": unknown,
        }
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/cohort")
async def preview_cohort(request: Request):
    """How many companies carry ALL these signals and none of those.

    The payoff for confirming signals: a cohort is a query, not a list. Set
    intersection happens in SQL — intersecting in JS over a capped fetch
    silently returns the wrong answer.
    """
    try:
        body = await request.json()
        require = [c for c in (body.get("require") or []) if c]
        exclude = [c for c in (body.get("exclude") or []) if c]
        if not require:
            return {"size": 0, "companies": []}

        state = _state()
        await state.init_db()
        ids = await state.cohort(require, exclude, limit=1000)
        if not ids:
            return {"size": 0, "companies": []}

        placeholders = ",".join("?" for _ in ids[:200])
        rows = await query_db(
            f"SELECT id, name, domain, industry, location FROM companies "
            f"WHERE id IN ({placeholders})",
            tuple(ids[:200]),
        )
        return {"size": len(ids), "companies": rows}
    except Exception as e:
        return JSONResponse({"size": 0, "companies": [], "error": str(e)}, status_code=500)


@app.get("/api/runs")
async def get_runs_api():
    """The collector run log — what ran, when, what it produced and cost."""
    try:
        state = _state()
        await state.init_db()
        await state.sweep_stale_runs()
        return await state.get_runs(limit=25)
    except Exception as e:
        return {"error": str(e)}


WEB_DIR = (Path(__file__).resolve().parent / "web")


TEXT_TYPES = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}
BINARY_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".png": "image/png"}


@app.get("/static/{path:path}")
async def static_file(path: str):
    """Serve the dashboard's own assets from disk.

    Read per-request rather than cached at import: editing app.css and hitting
    reload is the whole point of having them as real files. Fonts are vendored
    rather than fetched from a CDN — this is a local tool and it should work
    with the network off.
    """
    target = (WEB_DIR / path).resolve()
    root = WEB_DIR.resolve()
    if not target.is_file() or not target.is_relative_to(root):
        return PlainTextResponse("not found", status_code=404)

    if target.suffix in BINARY_TYPES:
        return Response(
            target.read_bytes(),
            media_type=BINARY_TYPES[target.suffix],
            headers={"Cache-Control": "public, max-age=604800"},
        )
    return PlainTextResponse(
        target.read_text(encoding="utf-8"),
        media_type=TEXT_TYPES.get(target.suffix, "text/plain"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


class InsecureBindError(RuntimeError):
    """Raised rather than serve real contact data to the open internet."""


def resolve_bind(host: str | None = None, port: int | None = None):
    """Where to listen. Arguments win, then the environment, then loopback.

    PORT with no prefix is read because that is what every container host
    sets; HARVEY_PORT exists for the case where something else owns PORT.
    """
    resolved_host = host or os.getenv("HARVEY_HOST") or "127.0.0.1"
    raw_port = port or os.getenv("PORT") or os.getenv("HARVEY_PORT") or 5555
    try:
        resolved_port = int(raw_port)
    except (TypeError, ValueError):
        raise InsecureBindError(f"PORT is not a number: {raw_port!r}") from None
    return resolved_host, resolved_port


def check_bind_is_safe(host: str):
    """Refuse to listen beyond this machine without a password.

    There is no accident here worth being relaxed about: every route serves
    real people's contact details, and an approved email sends from your
    mailbox. A bind to 0.0.0.0 with no password is that, published.
    """
    from harvey import webauth

    if webauth.is_loopback(host) or webauth.is_enabled():
        return
    raise InsecureBindError(
        f"Refusing to serve on {host} with no password set.\n"
        "  This console exposes contact records and can send mail in your "
        "name.\n"
        "  Set HARVEY_DASHBOARD_PASSWORD (and HARVEY_SESSION_SECRET for "
        "sessions\n"
        "  that survive restarts), then start again."
    )


def start_dashboard(host: str | None = None, port: int | None = None):
    """Start the dashboard server."""
    import uvicorn

    from harvey import webauth

    host, port = resolve_bind(host, port)
    check_bind_is_safe(host)

    gate = "password required" if webauth.is_enabled() else "no password (local only)"
    print(f"\n  Harvey Dashboard running at http://{host}:{port}  [{gate}]")
    print("  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    # `python -m harvey.dashboard` — the container entry point. The image's
    # own CMD runs the heartbeat agent; a web deployment overrides it with
    # this, and the two can also run as separate services off one image.
    try:
        start_dashboard()
    except InsecureBindError as exc:
        print("\n  " + str(exc) + "\n")
        raise SystemExit(2)
