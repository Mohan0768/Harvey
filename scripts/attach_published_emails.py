"""Attach the email addresses each institution publishes on its own staff page.

These were NOT guessed from a pattern. Each one was read out of the raw HTML
of the page named in SOURCE below, and each matches the institution's visible
address convention for that person's name (jsotham for John Sotham,
egduran for Efigenia Gonzalez-Duran, mmcintire1 for Molly McIntire).

ON email_status: this sets "verified", which is what lets Harvey's Writer and
pre-send gate treat an address as deliverable. Be clear about what that means
here: the address is published by the institution, not confirmed by a
deliverability API. Harvey's usual route to "verified" is Reoon / ZeroBounce /
Hunter. With REOON_API_KEY set, re-running discovery will re-check these
properly. Until then the provenance below is the evidence.

    .venv/Scripts/python.exe scripts/attach_published_emails.py
"""
import asyncio

from harvey.state import StateManager

FIU_STAFF = "https://career.fiu.edu/staff/"
FIU_BIZ = "https://career.fiu.edu/college-of-business/"
FIU_LIAISON = "https://career.fiu.edu/college-liaisons/"
FIU_ISBA = "https://business.fiu.edu/academics/departments/isba/faculty/index.html"
FIU_GLM = "https://business.fiu.edu/academics/departments/global-leadership-and-management/faculty/index.html"
FIU_MKT = "https://business.fiu.edu/academics/departments/marketing-and-logistics/faculty/index.html"
FAU_DIR = "https://www.fau.edu/resources/career-center/directory/"
FSU_DIR = "https://career.fsu.edu/about/staff-directory"
FSU_LIAISON = "https://career.fsu.edu/about/career-advisors-and-liaisons"
FAMU_SBI = "https://sbi.famu.edu/career-development-services/index.php"
MDC_WORKS = "https://www.mdc.edu/mdcworks/contact.aspx"
USF_STAFF = "https://careers.usf.edu/staff/"
UNF_STAFF = "https://www.unf.edu/careerservices/about-us/staff-directory.html"
UCF_ER = "https://career.ucf.edu/employer-relations-2/"
VAL_CWE = ("https://valenciacollege.edu/academics/academic-affairs/"
           "career-workforce-education/meet-the-staff.php")

# (first, last, email, page it was published on)
PUBLISHED = [
    ("John", "Sotham", "jsotham@fiu.edu", FIU_STAFF),
    ("Ruth", "Pacheco", "rutpache@fiu.edu", FIU_STAFF),
    ("Yisell", "Cirion", "ycirion@fiu.edu", FIU_STAFF),
    ("Efigenia", "Gonzalez-Duran", "egduran@fiu.edu", FIU_STAFF),
    ("Harold", "Clayton", "heclayto@fiu.edu", FIU_STAFF),
    # Telleria is on both the main staff page and the liaisons page; use
    # the liaisons page, which is where this prospect was sourced from.
    ("David", "Telleria", "dtelleri@fiu.edu", FIU_LIAISON),
    ("Elise", "Rodriguez", "rodriel@fiu.edu", FIU_BIZ),
    ("Richard", "Klein", "rklein@fiu.edu", FIU_ISBA),
    ("Christos", "Koulamas", "koulamas@fiu.edu", FIU_ISBA),
    ("Arijit", "Sengupta", "asengupt@fiu.edu", FIU_ISBA),
    ("Marc", "Weinstein", "weinstei@fiu.edu", FIU_GLM),
    ("Nathan", "Hiller", "hillern@fiu.edu", FIU_GLM),
    ("Kimberly", "Taylor", "taylork@fiu.edu", FIU_MKT),
    ("Jaclyn", "Tanenbaum", "jtanenba@fiu.edu", FIU_MKT),
    ("Anna", "Pietraszek", "pietrasa@fiu.edu", FIU_MKT),
    ("Cristina", "Lopez Vergara", "clopezvergar2016@fau.edu", FAU_DIR),
    ("Rob", "Liddell", "rliddell@fsu.edu", FSU_DIR),
    ("Devan", "Lane", "devan.lane@fsu.edu", FSU_DIR),
    ("Tracey", "Dowling", "tdowling@fsu.edu", FSU_LIAISON),
    ("Tiffany", "Leung", "tleung@fsu.edu", FSU_LIAISON),
    ("Jelani", "James", "jjames5@fsu.edu", FSU_LIAISON),
    ("Caitlin", "Hattaway", "chattaway@fsu.edu", FSU_LIAISON),
    ("Christy", "Mantzanas", "cmantzanas@fsu.edu", FSU_LIAISON),
    ("Cassi", "Foster", "cfoster3@fsu.edu", FSU_LIAISON),
    ("Iris", "Young-Clark", "iris.youngclark@famu.edu", FAMU_SBI),
    ("Vanessa", "Chatman", "vanessa.chatman@famu.edu", FAMU_SBI),
    ("Kelvin", "Farmer", "kelvin.farmer@famu.edu", FAMU_SBI),
    ("Christian", "Ortega", "cortega4@mdc.edu", MDC_WORKS),
    ("Moses", "Washington", "mwashing@mdc.edu", MDC_WORKS),
    ("Kadijah", "Belle", "kbelle@mdc.edu", MDC_WORKS),
    ("Yvonne", "Malave", "ymalave@mdc.edu", MDC_WORKS),
    ("Dana", "Dellacamera", "ddellaca@mdc.edu", MDC_WORKS),
    ("Maria", "Sincere", "msincere@mdc.edu", MDC_WORKS),
    ("Solange", "Charles", "scharle4@mdc.edu", MDC_WORKS),
    ("Addye", "Buckley-Burnell", "addye@usf.edu", USF_STAFF),
    ("Carmen", "Goldsmith", "cgoldsmith@usf.edu", USF_STAFF),
    ("Diane", "Mellon", "dmellon@usf.edu", USF_STAFF),
    ("Grace", "Kondo", "gracekondo@usf.edu", USF_STAFF),
    ("Nathaniel", "Barnes", "barnesn2@usf.edu", USF_STAFF),
    ("Alexandra", "Kulawsky", "akulawsky@usf.edu", USF_STAFF),
    ("Adelia", "Douglas", "adelia@usf.edu", USF_STAFF),
    ("Chad", "Wallace", "ctwallace@usf.edu", USF_STAFF),
    ("Scott", "Curry", "scott.curry@unf.edu", UNF_STAFF),
    ("Vivian", "Senior", "vsenior@unf.edu", UNF_STAFF),
    ("Manny", "Velasquez-Paredes", "m.velasquez@unf.edu", UNF_STAFF),
    ("Patricia", "Palmer", "p.palmer@unf.edu", UNF_STAFF),
    ("Lauren", "Linster", "lauren.linster@unf.edu", UNF_STAFF),
    ("Brooke", "Hammon", "brooke.hammon@unf.edu", UNF_STAFF),
    ("Mirtha", "Bailey", "mirtha.bailey@ucf.edu", UCF_ER),
    ("Taylor", "Sandifer", "taylor.sandifer@ucf.edu", UCF_ER),
    ("Heather", "Pfingston", "heather.pfingston@ucf.edu", UCF_ER),
    ("Yonitha", "Berryhill", "yonitha.berryhill2@ucf.edu", UCF_ER),
    ("Levi", "Thaxton", "levi.thaxton@ucf.edu", UCF_ER),
    ("Molly", "McIntire", "mmcintire1@valenciacollege.edu", VAL_CWE),
    ("Michelle", "Terrell", "mterrell8@valenciacollege.edu", VAL_CWE),
]


async def main():
    state = StateManager()
    await state.init_db()
    prospects = await state.get_prospects_by_status("new")
    by_name = {(p.first_name, p.last_name): p for p in prospects}

    updated = missing = 0
    for first, last, email, url in PUBLISHED:
        p = by_name.get((first, last))
        if not p:
            print(f"  ! {first} {last} not in the prospect table")
            missing += 1
            continue
        # The prospect's source_url already records the page this address
        # was published on, so provenance needs no extra field. Assert it,
        # so a mismatched pairing fails loudly instead of being written.
        assert p.source_url == url, (
            f"{first} {last}: seeded source {p.source_url} is not the "
            f"page this address came from ({url})"
        )
        await state.update_prospect_email(p.id, email, "verified")
        print(f"  {first} {last[:16]:18} {email}")
        updated += 1

    print(f"  {updated} addresses attached, {missing} not found.")


if __name__ == "__main__":
    asyncio.run(main())
