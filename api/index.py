"""Vercel entry point for the Harvey dashboard.

Vercel's Python runtime serves an ASGI application exported as ``app``.
This module hands over the dashboard's FastAPI app with the adjustments a
serverless host needs — and when it cannot, it says why on the page
instead of crashing.

What this deployment is, honestly
---------------------------------
Vercel is serverless. Three consequences follow, and none of them has a
workaround inside this file:

* **The database does not persist.** Each invocation gets a fresh
  container with an empty ``/tmp``. ``data/`` is gitignored, so nothing
  ships with the deploy either: the dashboard comes up with an empty
  database, and anything written to it is gone by the next cold start.
* **The heartbeat cannot run.** ``harvey run`` is a long-lived loop.
  A function that is billed per request and frozen between them cannot
  host one, so nothing is prospected, written or sent here.
* **Playwright is absent.** LinkedIn prospecting and deep crawling need a
  browser that does not fit in the bundle.

So this is the console shell, not a working Harvey. Sending, prospecting
and anything that must remember something belong on a host with a disk
and a process that stays up.

Why it refuses without a password
---------------------------------
Every route serves real people's contact records, and an approved email
sends from a real mailbox. ``check_bind_is_safe`` already refuses a
public bind with no password, but that check lives in ``start_dashboard``,
which a serverless host never calls — it imports ``app`` directly and
would sail straight past it. This restores the same rule on the path the
host actually takes.

Why it does not raise
---------------------
Raising here is safe but useless. The function dies at import, every path
returns a blank 500, and nothing distinguishes a missing password from a
missing dependency — the only way to tell is the platform's runtime log,
which is exactly what you do not have when someone else deployed it.
Refusing with a page that names the cause costs nothing and keeps the
same promise: while it is showing, no route serves data.
"""

import os
import traceback

# Writable space. The checkout is read-only here, so the default
# ``data/`` beside the package cannot be opened for writing at all.
os.environ.setdefault("HARVEY_DATA_DIR", "/tmp/harvey-data")

_TITLE = "Harvey is not configured"
_STYLE = (
    "font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "max-width:46rem;margin:12vh auto;padding:0 1.5rem;color:#1a1a1a;"
    "background:#fff"
)


def _page(heading: str, detail: str, fix: str) -> str:
    import html

    return (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>" + html.escape(_TITLE) + "</title>"
        "<body style=\"" + _STYLE + "\">"
        "<h1 style='font-size:1.4rem;margin:0 0 .5rem'>"
        + html.escape(heading) + "</h1>"
        "<p style='color:#555;margin:0 0 1.5rem'>"
        + html.escape(detail) + "</p>"
        "<pre style='background:#f5f5f5;padding:1rem;border-radius:6px;"
        "white-space:pre-wrap;font-size:14px'>"
        + html.escape(fix) + "</pre>"
        "<p style='color:#777;font-size:14px'>No data is served while this "
        "message is showing.</p>"
        "</body></html>"
    )


def _refusing_app(heading: str, detail: str, fix: str):
    """A tiny app that explains itself and serves nothing else.

    Built with Starlette rather than a bare ASGI callable. A host decides
    what to do with ``app`` by inspecting it, and a plain function is not
    reliably recognised as an application; being unrecognised fails the
    same way the original crash did, with a blank 500, which would hide
    the very message this exists to show. Starlette already sits under
    FastAPI, so it costs no new dependency.
    """
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, PlainTextResponse
    from starlette.routing import Route

    html_body = _page(heading, detail, fix)
    text_body = heading + "\n\n" + detail + "\n\n" + fix + "\n"
    headers = {"cache-control": "no-store", "x-harvey-state": "unconfigured"}

    async def explain(request):
        path = request.url.path
        wants_text = path.startswith("/api/") or path.endswith(
            (".json", ".csv", ".js", ".css"))
        # 503: the service exists and is not ready, which is exactly true.
        if wants_text:
            return PlainTextResponse(text_body, status_code=503,
                                     headers=headers)
        return HTMLResponse(html_body, status_code=503, headers=headers)

    return Starlette(routes=[
        Route("/{path:path}", explain,
              methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]),
    ])


def _build():
    """The real app, or something that explains why there isn't one."""
    try:
        os.makedirs(os.environ["HARVEY_DATA_DIR"], exist_ok=True)
    except OSError as exc:
        return _refusing_app(
            "Harvey has nowhere to write",
            "HARVEY_DATA_DIR could not be created: " + str(exc)[:200],
            "Set HARVEY_DATA_DIR to a writable path. On Vercel only /tmp is\n"
            "writable, so /tmp/harvey-data is the usual answer.")

    try:
        from harvey import webauth
    except Exception as exc:
        return _refusing_app(
            "Harvey failed to load",
            type(exc).__name__ + ": " + str(exc)[:200],
            "This is an import failure, not a configuration one. A missing\n"
            "dependency is the usual cause: check api/requirements.txt\n"
            "against what the dashboard imports.")

    if not webauth.is_enabled():
        return _refusing_app(
            "Set a dashboard password",
            "HARVEY_DASHBOARD_PASSWORD is not set, and this deployment is a "
            "public URL. Every route serves real people's contact records, "
            "and an approved email sends from a real mailbox, so Harvey "
            "will not serve anything until there is a password in front "
            "of it.",
            "In the Vercel project:\n"
            "  Settings -> Environment Variables\n"
            "    HARVEY_DASHBOARD_PASSWORD   the password the login asks for\n"
            "    HARVEY_SESSION_SECRET       any long random string, so a\n"
            "                                cold start does not sign you out\n"
            "  Deployments -> latest -> Redeploy")

    try:
        from harvey.dashboard import app as dashboard_app
        return dashboard_app
    except Exception as exc:
        return _refusing_app(
            "Harvey failed to start",
            type(exc).__name__ + ": " + str(exc)[:200],
            "The password is set, so this is not the gate. The first line of\n"
            "the traceback:\n\n"
            + "".join(traceback.format_exception_only(type(exc), exc))[:300])


app = _build()
