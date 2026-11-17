"""Prospect-list export — the list itself is the product.

Produces a CSV that drops straight into Instantly, Smartlead, or any
sequencer (email / first_name / last_name / company_name / personalization
columns), plus Harvey's own quality columns (score, email_status, signals)
so a human can eyeball what they're getting.
"""

import csv
import io
import logging

from harvey.state import StateManager

logger = logging.getLogger("harvey.export")

# Column order chosen so the file imports cleanly into cold-email tools
# without remapping; extra columns are simply available as custom variables.
CSV_COLUMNS = [
    "email", "first_name", "last_name", "company_name", "title",
    "website", "linkedin_url", "industry", "personalization",
    "email_status", "score", "seniority", "source", "status", "created_at",
    # Everything else the Contacts table shows, appended rather than
    # inserted: the order above is what sequencers auto-map against, and
    # moving those columns would break an import that works today.
    "name", "department", "category", "phone", "phone_verified",
    "email_verified", "company_size", "updated_at",
]

DEFAULT_EMAIL_STATUSES = ["verified", "risky"]


async def collect_prospects(
    state: StateManager,
    email_statuses: list[str] | None = None,
    min_score: int = 0,
    statuses: list[str] | None = None,
    include_all: bool = False,
) -> list[dict]:
    """Fetch prospects matching the filters as export-ready dicts.

    Default view is the *deliverable* list (verified + risky emails).
    ``include_all`` disables every filter for a full dump.
    """
    import aiosqlite

    where = []
    params: list = []
    if not include_all:
        wanted = email_statuses if email_statuses is not None else DEFAULT_EMAIL_STATUSES
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            where.append(f"email != '' AND email_status IN ({placeholders})")
            params.extend(wanted)
        if min_score > 0:
            where.append("score >= ?")
            params.append(min_score)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)

    sql = "SELECT * FROM prospects"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY score DESC, created_at DESC"

    async with aiosqlite.connect(state.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

    from harvey.segments import label as category_label

    out = []
    for r in rows:
        first = r.get("first_name", "") or ""
        last = r.get("last_name", "") or ""
        out.append({
            "email": r.get("email", ""),
            "first_name": first,
            "last_name": last,
            "company_name": r.get("company", ""),
            "title": r.get("title", ""),
            "website": r.get("source_url", ""),
            "linkedin_url": r.get("linkedin_url", ""),
            "industry": r.get("industry", ""),
            "personalization": r.get("personalization_notes", ""),
            "email_status": r.get("email_status", ""),
            "score": r.get("score", 0),
            "seniority": r.get("seniority", ""),
            "source": r.get("source", ""),
            "status": r.get("status", ""),
            "created_at": r.get("created_at", ""),
            # The columns the Contacts tab shows on screen.
            "name": (first + " " + last).strip(),
            "department": r.get("department", ""),
            # Stored as a key; exported as the words on the screen.
            "category": category_label(r.get("category", "") or ""),
            "phone": r.get("phone", ""),
            "phone_verified": int(bool(r.get("phone_verified", 0))),
            "email_verified": int(bool(r.get("email_verified", 0))),
            "company_size": r.get("company_size", ""),
            "updated_at": r.get("updated_at", ""),
        })
    return out


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


async def export_prospects_csv(
    state: StateManager,
    out_path: str | None = None,
    email_statuses: list[str] | None = None,
    min_score: int = 0,
    statuses: list[str] | None = None,
    include_all: bool = False,
) -> tuple[int, str]:
    """Export prospects to CSV. Returns (row_count, csv_text).

    Writes to ``out_path`` when given; caller handles printing/serving.
    """
    rows = await collect_prospects(
        state,
        email_statuses=email_statuses,
        min_score=min_score,
        statuses=statuses,
        include_all=include_all,
    )
    text = to_csv(rows)
    if out_path:
        # utf-8-sig, not the platform default: on Windows that default is
        # cp1252, which mangles any accented name or em dash into bytes no
        # UTF-8 importer can read. The BOM keeps Excel happy too.
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            f.write(text)
        logger.info(f"Exported {len(rows)} prospects to {out_path}")
    return len(rows), text


# ── Outbox export ──
#
# Separate from the prospect export on purpose. That one answers "who is on
# the list"; this one answers "what exactly would be sent to them", which is
# a different question and a different file. The subject and body are the
# whole point, so they are exported verbatim from the database rather than
# re-composed: an export that regenerates is not a record of anything.

OUTBOX_COLUMNS = [
    "recipient_name", "recipient_email", "company", "department", "title",
    "category", "subject", "body", "status", "email_type", "created_at",
    "updated_at", "sent_at", "message_id", "thread_ref", "error",
]


def outbox_rows_to_csv(rows: list[dict]) -> str:
    """Serialise decorated outbox rows. Newlines inside a body survive:
    csv quotes them, and every spreadsheet reads them back as one cell."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=OUTBOX_COLUMNS,
                            extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "recipient_name": row.get("recipient_name") or "",
            "recipient_email": row.get("to_email") or "",
            "company": row.get("recipient_company") or "",
            "department": row.get("recipient_department") or "",
            "title": row.get("recipient_title") or "",
            "category": row.get("role_label") or "",
            "subject": row.get("subject") or "",
            "body": row.get("body") or "",
            "status": row.get("status") or "",
            "email_type": row.get("email_type") or "",
            "created_at": row.get("created_at") or "",
            "updated_at": row.get("updated_at") or "",
            "sent_at": row.get("sent_at") or "",
            "message_id": row.get("message_id") or "",
            "thread_ref": row.get("thread_ref") or "",
            "error": row.get("error") or "",
        })
    return buf.getvalue()
