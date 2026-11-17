"""Vercel entry point for the Harvey dashboard.

Vercel's Python runtime serves an ASGI application exported as ``app``,
so this module's job is to hand over the dashboard's FastAPI app with the
two adjustments a serverless host needs, and to refuse the deploy outright
when it would be unsafe.

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
"""

import os

# Writable space. The checkout is read-only here, so the default
# ``data/`` beside the package cannot be opened for writing at all.
os.environ.setdefault("HARVEY_DATA_DIR", "/tmp/harvey-data")
os.makedirs(os.environ["HARVEY_DATA_DIR"], exist_ok=True)

from harvey import webauth  # noqa: E402

if not webauth.is_enabled():
    raise RuntimeError(
        "Refusing to serve: HARVEY_DASHBOARD_PASSWORD is not set.\n"
        "  This console exposes contact records and can send mail in your\n"
        "  name, and a Vercel deployment is a public URL. Set\n"
        "  HARVEY_DASHBOARD_PASSWORD in the project's environment\n"
        "  variables, and HARVEY_SESSION_SECRET so sessions survive a\n"
        "  cold start, then redeploy."
    )

from harvey.dashboard import app  # noqa: E402,F401
