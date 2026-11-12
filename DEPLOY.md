# Deploying Harvey

Harvey is a long-running process with a database on disk. It deploys as a
**container with a persistent volume**, not to a serverless platform.

Vercel, Netlify Functions and Lambda cannot run it: the filesystem is
read-only and ephemeral, so `data/harvey.db` and the Gmail token would not
survive a single cold start; there is no long-running process for the
heartbeat loop; and the OAuth loopback cannot complete. This is not a
configuration problem, it is a shape mismatch.

## Before you expose it

The dashboard has no accounts and no roles. Everything behind it is real
people's contact details and a mailbox that can send in your name.

So `start_dashboard` **refuses to bind to anything but loopback unless
`HARVEY_DASHBOARD_PASSWORD` is set**. That is deliberate and there is no
flag to override it. Set the password, and set `HARVEY_SESSION_SECRET` too
if you would rather not be signed out by every redeploy.

```
HARVEY_DASHBOARD_PASSWORD=<something long>
HARVEY_SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

## The two processes

One image, two entry points. Run either or both:

| Command | What it is |
| --- | --- |
| `python -m harvey.dashboard` | The web console |
| `python -m harvey.main` | The heartbeat agent (the image's default CMD) |

The dashboard reads `PORT` with no prefix, which is what container hosts
set, and `HARVEY_HOST`, which must be `0.0.0.0` to be reachable.

## Railway

1. New Project, Deploy from GitHub repo. It builds the Dockerfile.
2. **Settings, Start Command**: `python -m harvey.dashboard`
3. **Variables**: `HARVEY_HOST=0.0.0.0`, `HARVEY_DASHBOARD_PASSWORD`,
   `HARVEY_SESSION_SECRET`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`,
   and `GMAIL_REDIRECT_URI=https://<your-domain>/oauth/google/callback`.
   Set them here rather than shipping a `.env`: the Settings page writes
   to disk, and on a container that write does not outlive the instance.
4. **Volume**: mount at `/app/data`. Without it the database and the Gmail
   token are gone on every redeploy.
5. In Google Cloud Console, add that same `GMAIL_REDIRECT_URI` string to
   the OAuth client's Authorized redirect URIs.

Render and Fly.io are the same four ingredients: Dockerfile, a disk at
`/app/data`, the environment variables, and the start command.

## What does not work on a server

`docker-compose.yml` mounts `~/.claude` so the Brain can use your Claude
subscription through the CLI. You cannot mount your laptop's home
directory onto a cloud host, and copying those credentials up is a bad
trade. On a hosted Harvey, expect:

- **Working**: discovery, contacts, import/export, the Custom Email
  template flow, the Outbox approval ladder, Gmail sending, Conversations.
- **Not working**: AI-written drafts, which need the Claude CLI.

Template-rendered emails need no model call, so bulk personalised drafts
still work.

## Just showing someone

A tunnel is often the honest answer, and changes no code:

```
cloudflared tunnel --url http://localhost:5555
```

Set `HARVEY_DASHBOARD_PASSWORD` first and restart the dashboard. A tunnel
is a public URL like any other.
