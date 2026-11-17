# Deploying Harvey

## What a Vercel deployment is, and is not

The repository is configured for Vercel: `vercel.json` routes every path to
`api/index.py`, which exports the dashboard's FastAPI app.

Be clear about what that gets you. Vercel is serverless, and three things
follow that no amount of configuration changes:

| | On Vercel |
|---|---|
| The database | **Does not persist.** Each invocation gets a fresh container and an empty `/tmp`. `data/` is gitignored, so nothing ships with the deploy either: the dashboard comes up empty, and anything written is gone by the next cold start. |
| The heartbeat (`harvey run`) | **Cannot run.** It is a long-lived loop; a function billed per request and frozen between them cannot host one. Nothing is prospected, written or sent. |
| Playwright | **Absent.** LinkedIn prospecting and deep crawling need a browser that does not fit in a serverless bundle. `api/requirements.txt` leaves it out deliberately. |

So the Vercel deployment is the console shell — proof the app builds and
serves, with a login in front of it. It is not a working Harvey.

For a Harvey that actually works, you need a host with a disk and a process
that stays up. The `Dockerfile` is ready for that: any platform that runs a
container with a mounted volume for `/app/data` will do, running
`python -m harvey.dashboard` for the console and `python -m harvey.main` for
the heartbeat.

## Environment variables

Set these in **Project Settings → Environment Variables** before the first
deploy.

| Variable | Required | Why |
|---|---|---|
| `HARVEY_DASHBOARD_PASSWORD` | **Yes** | `api/index.py` refuses to serve without it. Every route exposes contact records and can send mail in your name, and a deployment is a public URL. |
| `HARVEY_SESSION_SECRET` | Strongly recommended | Without it the cookie key is derived from the password, so every cold start signs you out. |
| `HARVEY_DATA_DIR` | No | Defaults to `/tmp/harvey-data`, which is the only writable space available. |
| `ANTHROPIC_API_KEY` | If Claude should write | Needed by `brain.provider: anthropic`. |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | Only for Gmail | Of limited use here: with no persistent database there is no outbox to send from. |

Never commit any of these. `.env` is gitignored and must stay that way.

## Deploying

1. Sign in at [vercel.com/new](https://vercel.com/new) and authorize GitHub.
2. Import `Mohan0768/Harvey`.
3. Leave the framework preset as **Other** — `vercel.json` already says what
   to do, and there is no build step.
4. Add `HARVEY_DASHBOARD_PASSWORD` and `HARVEY_SESSION_SECRET` under
   Environment Variables **before** deploying. Without the first one the
   build succeeds and the function fails on every request, by design.
5. Deploy, then open the URL. You should get the login screen, and the
   password you set should let you in to an empty console.

### If it is not the login screen

The deployment tells you what is wrong rather than returning a blank error.
A **503** with *"Set a dashboard password"* means `HARVEY_DASHBOARD_PASSWORD`
is missing or empty — the guard working, not a bug. A 503 saying *"Harvey
failed to start"* or *"failed to load"* names the exception instead, which
means the problem is an import, usually a dependency missing from
`api/requirements.txt`.

Either way no route serves data while that page is showing.

## A caution about the repository

`Mohan0768/Harvey` is **public**, and it already tracks real contact data:

- `prospects.csv` — 56 named people with email addresses
- `outreach/warroom_outreach.csv` — 879 rows of names, addresses and titles
- `outreach/warroom_outreach.xlsx`

Those are real university staff who did not consent to publication. Removing
them from the working tree is not enough — they stay in the git history, so
it takes a history rewrite (`git filter-repo`) and a force push, or making
the repository private.

This predates the deployment work and is not caused by it, but deploying
draws attention to a repository that currently publishes those files.
