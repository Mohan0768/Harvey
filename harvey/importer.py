"""Parse an uploaded contact list into prospect records.

Harvey's own DISCOVER stage only knows how to find businesses its providers
map (local trades, SERP results). For an audience it cannot enumerate, the
honest path is to let a human bring the list. This module turns an uploaded
.xlsx / .xls / .csv into validated rows, and says precisely why each rejected
row was rejected.

Nothing here touches the database. The caller previews first, then imports,
so a bad file costs nothing.
"""
import csv
import io
import re

MAX_BYTES = 5 * 1024 * 1024        # a contact list this big is a data dump
MAX_ROWS = 5000

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}$")

# Header synonyms, lowercased and stripped of non-letters before matching.
FIELD_ALIASES = {
    "first_name": ("firstname", "first", "givenname", "forename"),
    "last_name": ("lastname", "last", "surname", "familyname"),
    "name": ("name", "fullname", "contact", "contactname", "person"),
    "title": ("title", "jobtitle", "position", "role", "designation"),
    "company": ("company", "organisation", "organization", "institution",
                "employer", "account", "school", "college", "university"),
    "email": ("email", "emailaddress", "mail", "workemail", "e"),
    "phone": ("phone", "telephone", "mobile", "cell", "phonenumber", "tel"),
    "status": ("status", "stage", "state"),
    "source": ("source", "origin", "channel", "via"),
    # "school" and "college" are deliberately absent here: they
    # already mean company above, and the first match wins.
    "department": ("department", "dept", "division", "faculty",
                   "programme", "program", "discipline", "unit"),
    "linkedin_url": ("linkedin", "linkedinurl", "linkedinprofile",
                     "profileurl"),
}


def _norm_header(value) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def map_columns(headers: list) -> dict:
    """Header index -> prospect field. Unknown columns are simply ignored."""
    mapping = {}
    for i, raw in enumerate(headers):
        key = _norm_header(raw)
        if not key:
            continue
        for field, aliases in FIELD_ALIASES.items():
            if key in aliases:
                # First column wins, so a later "name" cannot clobber an
                # earlier explicit "first name".
                mapping.setdefault(i, field)
                break
    return mapping


def split_name(full: str) -> tuple:
    """"Kimberly Taylor" and "Taylor, Kimberly" both give (Kimberly, Taylor).

    A comma means the export put the surname first, which is common enough
    that treating it as a space would silently swap every name in the file.
    """
    text = str(full or "").strip()
    if "," in text:
        last, _, first = text.partition(",")
        last, first = last.strip(), first.strip()
        if last and first:
            return first.split()[0], last
    parts = [p for p in text.split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _read_table(filename: str, raw: bytes) -> list:
    """Return a list of rows (each a list of cell values), header first."""
    name = (filename or "").lower()
    if name.endswith(".csv") or name.endswith(".tsv") or name.endswith(".txt"):
        text = raw.decode("utf-8-sig", errors="replace")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [r for r in csv.reader(io.StringIO(text), dialect)]
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    if name.endswith(".xls"):
        import xlrd
        book = xlrd.open_workbook(file_contents=raw)
        sheet = book.sheet_by_index(0)
        return [sheet.row_values(i) for i in range(sheet.nrows)]
    raise ValueError(
        "Unsupported file type. Upload a .xlsx, .xls or .csv file.")


def parse(filename: str, raw: bytes) -> dict:
    """Parse an upload into {rows, failed, headers, mapped, truncated}.

    A row lands in `rows` only if it has a usable name. Everything else goes
    to `failed` with the reason, so the preview can show exactly what will
    not be imported and why.
    """
    if not raw:
        raise ValueError("That file is empty.")
    if len(raw) > MAX_BYTES:
        raise ValueError(
            f"That file is {len(raw) // 1024}KB. The limit is "
            f"{MAX_BYTES // 1024 // 1024}MB.")

    table = [r for r in _read_table(filename, raw) if any(
        str(c or "").strip() for c in r)]
    if not table:
        raise ValueError("That file has no rows in it.")

    headers = table[0]
    mapping = map_columns(headers)
    if not mapping:
        raise ValueError(
            "No recognisable columns. The first row should be a header with "
            "at least a Name or Email column.")

    rows, failed = [], []
    body = table[1:]
    truncated = len(body) > MAX_ROWS
    for n, raw_row in enumerate(body[:MAX_ROWS], start=2):
        rec = {}
        for i, field in mapping.items():
            if i < len(raw_row):
                rec[field] = str(raw_row[i] if raw_row[i] is not None else "").strip()

        first = rec.pop("first_name", "")
        last = rec.pop("last_name", "")
        if not (first or last):
            first, last = split_name(rec.get("name", ""))
        rec.pop("name", None)
        rec["first_name"], rec["last_name"] = first, last

        email = (rec.get("email") or "").strip().lower()
        rec["email"] = email

        if not (first or last):
            failed.append({"row": n, "reason": "no name", "data": rec})
            continue
        if email and not EMAIL_RE.match(email):
            failed.append({"row": n, "reason": f"invalid email '{email}'",
                           "data": rec})
            continue
        rec["row"] = n
        rows.append(rec)

    return {"rows": rows, "failed": failed, "truncated": truncated,
            "headers": [str(h or "") for h in headers],
            "mapped": {str(headers[i]): f for i, f in mapping.items()
                       if i < len(headers)}}
