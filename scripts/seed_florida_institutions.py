"""Seed Florida higher-education institutions as Harvey companies.

Harvey's free discovery provider (OpenStreetMap) only covers nine mapped
trades, plumber, roofer, dentist and the like, so it cannot find colleges.
Florida higher education is a knowable list, so it is curated here rather
than bought from a SERP API.

Every domain below was checked with a live HTTP request: all 40 resolve to
the institution named. Entries marked ``blocks_crawlers`` sit behind
Cloudflare or return 403 to non-browser clients, so Harvey's PROFILE stage
will collect little or nothing from their homepage, they are still valid
targets, they just need their contacts found another way.

Re-runnable: ``add_company`` dedups on the normalised domain.

    .venv/Scripts/python.exe scripts/seed_florida_institutions.py
"""
import asyncio

from harvey.models.company import Company
from harvey.state import StateManager, _new_id

PUBLIC_UNIVERSITY = "Higher Education, Public University"
PRIVATE_UNIVERSITY = "Higher Education, Private University"
STATE_COLLEGE = "Higher Education, State/Community College"

# (name, domain, city, institution type, blocks_crawlers)
INSTITUTIONS = [
    # State University System of Florida
    ("University of Florida", "ufl.edu", "Gainesville, FL", PUBLIC_UNIVERSITY, False),
    ("Florida State University", "fsu.edu", "Tallahassee, FL", PUBLIC_UNIVERSITY, True),
    ("University of Central Florida", "ucf.edu", "Orlando, FL", PUBLIC_UNIVERSITY, True),
    ("University of South Florida", "usf.edu", "Tampa, FL", PUBLIC_UNIVERSITY, False),
    ("Florida International University", "fiu.edu", "Miami, FL", PUBLIC_UNIVERSITY, False),
    ("Florida Atlantic University", "fau.edu", "Boca Raton, FL", PUBLIC_UNIVERSITY, False),
    ("University of North Florida", "unf.edu", "Jacksonville, FL", PUBLIC_UNIVERSITY, False),
    ("University of West Florida", "uwf.edu", "Pensacola, FL", PUBLIC_UNIVERSITY, False),
    ("Florida Gulf Coast University", "fgcu.edu", "Fort Myers, FL", PUBLIC_UNIVERSITY, False),
    ("Florida A&M University", "famu.edu", "Tallahassee, FL", PUBLIC_UNIVERSITY, False),
    ("New College of Florida", "ncf.edu", "Sarasota, FL", PUBLIC_UNIVERSITY, True),
    ("Florida Polytechnic University", "floridapoly.edu", "Lakeland, FL", PUBLIC_UNIVERSITY, True),
    # Private universities and colleges
    ("University of Miami", "miami.edu", "Coral Gables, FL", PRIVATE_UNIVERSITY, False),
    ("Nova Southeastern University", "nova.edu", "Fort Lauderdale, FL", PRIVATE_UNIVERSITY, False),
    ("Rollins College", "rollins.edu", "Winter Park, FL", PRIVATE_UNIVERSITY, False),
    ("Stetson University", "stetson.edu", "DeLand, FL", PRIVATE_UNIVERSITY, True),
    ("Embry-Riddle Aeronautical University", "erau.edu", "Daytona Beach, FL", PRIVATE_UNIVERSITY, False),
    ("Florida Institute of Technology", "fit.edu", "Melbourne, FL", PRIVATE_UNIVERSITY, False),
    ("Lynn University", "lynn.edu", "Boca Raton, FL", PRIVATE_UNIVERSITY, True),
    ("Barry University", "barry.edu", "Miami Shores, FL", PRIVATE_UNIVERSITY, False),
    ("Jacksonville University", "ju.edu", "Jacksonville, FL", PRIVATE_UNIVERSITY, False),
    ("Florida Southern College", "flsouthern.edu", "Lakeland, FL", PRIVATE_UNIVERSITY, False),
    ("Saint Leo University", "saintleo.edu", "Saint Leo, FL", PRIVATE_UNIVERSITY, True),
    ("Palm Beach Atlantic University", "pba.edu", "West Palm Beach, FL", PRIVATE_UNIVERSITY, True),
    # Florida College System
    ("Miami Dade College", "mdc.edu", "Miami, FL", STATE_COLLEGE, False),
    ("Valencia College", "valenciacollege.edu", "Orlando, FL", STATE_COLLEGE, False),
    ("Broward College", "broward.edu", "Fort Lauderdale, FL", STATE_COLLEGE, False),
    ("Hillsborough Community College", "hccfl.edu", "Tampa, FL", STATE_COLLEGE, True),
    ("Santa Fe College", "sfcollege.edu", "Gainesville, FL", STATE_COLLEGE, False),
    ("Seminole State College of Florida", "seminolestate.edu", "Sanford, FL", STATE_COLLEGE, False),
    ("St. Petersburg College", "spcollege.edu", "St. Petersburg, FL", STATE_COLLEGE, False),
    # Renamed from Tallahassee Community College; tcc.fl.edu redirects here.
    ("Tallahassee State College", "tsc.fl.edu", "Tallahassee, FL", STATE_COLLEGE, False),
    # palmbeachstate.edu redirects to the canonical pbsc.edu.
    ("Palm Beach State College", "pbsc.edu", "Lake Worth, FL", STATE_COLLEGE, False),
    ("Daytona State College", "daytonastate.edu", "Daytona Beach, FL", STATE_COLLEGE, False),
    ("Florida State College at Jacksonville", "fscj.edu", "Jacksonville, FL", STATE_COLLEGE, False),
    ("Polk State College", "polk.edu", "Winter Haven, FL", STATE_COLLEGE, False),
    ("Indian River State College", "irsc.edu", "Fort Pierce, FL", STATE_COLLEGE, True),
    ("State College of Florida Manatee-Sarasota", "scf.edu", "Bradenton, FL", STATE_COLLEGE, True),
    ("Eastern Florida State College", "easternflorida.edu", "Cocoa, FL", STATE_COLLEGE, False),
    ("Pasco-Hernando State College", "phsc.edu", "New Port Richey, FL", STATE_COLLEGE, True),
]

TARGET_DEPARTMENTS = (
    "Target departments: IT / Information Systems / Cybersecurity / Computer "
    "Science; Human Resource Management / Organizational Leadership; "
    "Marketing / Digital Marketing. Plus the career services centre."
)


async def main():
    state = StateManager()
    await state.init_db()

    added = existing = 0
    for name, domain, city, kind, blocks in INSTITUTIONS:
        notes = TARGET_DEPARTMENTS
        if blocks:
            notes += (" NOTE: homepage blocks automated clients (403 / "
                      "Cloudflare), site profiling will return little; find "
                      "contacts via the directory or LinkedIn instead.")
        company = Company(
            name=name,
            domain=domain,
            website=f"https://{domain}",
            description=f"{kind.split(', ')[1].strip()} in {city}.",
            industry=kind,
            location=city,
            source="curated_seed",
            source_url=f"https://{domain}",
            external_id=f"curated:fl-highered:{domain}",
            notes=notes,
        )
        # add_company overwrites company.id in place when the domain is
        # already recorded, so the id has to be pinned before the call
        # comparing afterwards would always look like a fresh insert.
        company.id = _new_id()
        fresh_id = company.id
        company_id = await state.add_company(company)
        inserted = company_id == fresh_id
        added += inserted
        existing += not inserted
        print(f"  {'+' if inserted else '='} {name[:45]:45} {domain}")

    blocked = sum(1 for i in INSTITUTIONS if i[4])
    print(f"\n  {added} added, {existing} already known "
          f"({blocked} of {len(INSTITUTIONS)} block crawlers).")


if __name__ == "__main__":
    asyncio.run(main())
