# Setup

Harvey is running in **export-only** mode: it finds the people and writes the
copy, you send from your own mailbox. That needs **no API keys at all**.

## What is already done

| Step | State |
|---|---|
| Virtual environment | done, `harvey/.venv` |
| `.env` file | created, intentionally empty |
| Product config | `harvey.local.yaml` (gitignored) |
| Product knowledge | `skills/product_knowledge.md`, `skills/competitive_intel.md` |
| Institutions | 40 Florida colleges, `scripts/seed_florida_institutions.py` |
| People | 14 decision-makers, `scripts/seed_prospects.py` |
| Addresses | published on each institution's own staff page |
| Outreach copy | `outreach/warroom_outreach.xlsx` |

## Running it

Two commands, from the `harvey` folder.

    .venv\Scripts\harvey.exe dashboard      # web UI on localhost:5555
    .venv\Scripts\harvey.exe run            # the 15-minute loop

The loop is optional in export-only mode. It profiles company sites and keeps
the database current; it will not send anything.

## Rebuilding the spreadsheet

After adding or editing prospects:

    .venv\Scripts\python.exe scripts\seed_prospects.py
    .venv\Scripts\python.exe scripts\attach_published_emails.py
    .venv\Scripts\python.exe scripts\build_outreach_sheet.py

The last one re-checks every email against Harvey's pre-send gate and refuses
to write a row that fails. Output: `outreach/warroom_outreach.xlsx` and `.csv`.

## How to send

One link per email. This is not a style preference: two links in a cold email
is a known spam signal, and the gate rejects it.

1. **First touch** - the `email_1` column. Carries the free trial link.
2. **No reply after ~5 working days** - the `email_2_followup` column. No link.
3. **They reply with interest** - the `reply_when_interested` column. This is
   where the Calendly link goes.

Paste into your own mailbox, or import the CSV into any sequencer.

## Settings you do NOT need

The Settings tab used to show eight credential forms. Six are gone, because
nothing in this workflow uses them:

- **Instantly** - a paid sending platform. You send yourself.
- **LinkedIn** - automated prospecting. Also disabled in `harvey.local.yaml`,
  because with it on Scout spent every cycle running searches that returned
  nothing.
- **Cloudflare** - JavaScript crawling during product training. Training is done.
- **ZeroBounce, Hunter** - duplicate verifiers. Reoon is the one worth having.

What is left: Gmail, SMTP and Reoon. All three are optional and all three are
only read when `channels.email.enabled` is `true`.

## Turning on automatic sending later

1. `claude login` - required before Harvey can write a single word.
2. Add a mailbox: Gmail OAuth, or SMTP host/username/password.
3. Add a Reoon key so Harvey can verify addresses it works out itself.
4. In `harvey.local.yaml` set `channels.email.enabled: true`.

Leave `require_approval: true` until you have read a dozen drafts you would
have sent yourself. Every email then waits in the Outbox for your approval.
