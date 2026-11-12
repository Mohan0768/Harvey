"""Seed real, verified decision-makers at Florida institutions.

Every person here was read off the institution's own public staff page on
2026-11-02; the page is recorded in ``source_url`` so any claim can be
checked. Nothing is inferred and nothing is invented, the brief's rule is
no fabricated names, titles or contact details.

EMAIL IS DELIBERATELY BLANK. Harvey resolves an address from the company's
pattern and then verifies it; with no verifier key configured every guess
would be marked ``guess`` and is never sendable. An invented address would
be worse than none, so the field stays empty until a verifier is in place.

Re-runnable: the id is derived from institution + name, so a second run
inserts nothing.

    .venv/Scripts/python.exe scripts/seed_prospects.py
"""
import asyncio
import hashlib

from harvey.models.prospect import Prospect
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

# (domain, first, last, title, department, seniority, source_url, note)
PEOPLE = [
    ("fiu.edu", "Ruth", "Pacheco",
     "Executive Director Industry Partnerships & Career Readiness",
     "Career and Talent Development", "vp", FIU_STAFF,
     "Owns industry partnerships and career readiness for the whole "
     "university, the institutional-partnership route rather than a "
     "single course."),
    ("fiu.edu", "John", "Sotham",
     "Associate Director, Career Readiness, Integrated Partnership & Experiential Learning",
     "Career and Talent Development", "director", FIU_STAFF,
     "His job title literally contains Experiential Learning. The War Room "
     "is a 60-minute experiential format that fits inside one class "
     "session, which is the usual blocker for experiential programming."),
    ("fiu.edu", "Yisell", "Cirion",
     "Director, Career Development and Integrated Partnerships",
     "Career and Talent Development", "director", FIU_STAFF,
     "Runs career development and integrated partnerships, the person who "
     "would scope a cohort-wide pilot."),
    ("fiu.edu", "Efigenia", "Gonzalez-Duran",
     "Director of Employer Engagement and Support",
     "Career and Talent Development", "director", FIU_STAFF,
     "Employer engagement: cares what employers say graduates cannot do. "
     "Decision-making under pressure is a common gap."),
    ("fiu.edu", "Harold", "Clayton",
     "Director, FIU Career Liaison Program and Campus Engagement",
     "Career and Talent Development", "director", FIU_STAFF,
     "Runs the liaison programme connecting career services to individual "
     "colleges, a route into several departments at once."),
    ("fiu.edu", "Elise", "Rodriguez",
     "Associate Director",
     "College of Business", "director", FIU_BIZ,
     "Career development lead for the College of Business, which covers "
     "the marketing and HR programmes in the target list. Stages 3 and 6 "
     "(validation, investor pitch) map onto that curriculum."),
    ("fiu.edu", "David", "Telleria",
     "Associate Director, College of Engineering and Computing",
     "College of Engineering and Computing", "director", FIU_LIAISON,
     "Covers the IT, IS and cybersecurity student population. Those "
     "students can build; the gap is deciding under ambiguity."),
    # FAU publishes 25 career centre staff with names and titles, and an
    # email for exactly one of them. The individual profile pages carry no
    # contact details at all, so this is the whole of FAU.
    # Category 1 of the brief: academic decision-makers, not career services.
    ("fiu.edu", "Richard", "Klein",
     "Senior Associate Dean, College of Business",
     "Information Systems and Business Analytics", "vp", FIU_ISBA,
     "Senior associate dean sitting in the information systems department. "
     "Decides at college level for the exact programmes in the target list."),
    ("fiu.edu", "Christos", "Koulamas",
     "Ryder MIS Eminent Scholar Endowment Chair",
     "Information Systems and Business Analytics", "director", FIU_ISBA,
     "Endowed chair in MIS. His students can build; the gap at graduation "
     "is deciding with incomplete information."),
    ("fiu.edu", "Arijit", "Sengupta",
     "Associate Dean for Accreditation and Technology Systems",
     "Information Systems and Business Analytics", "vp", FIU_ISBA,
     "Owns accreditation, so assurance-of-learning evidence is his problem. "
     "The simulation produces a per-student record of decision-making."),
    ("fiu.edu", "Marc", "Weinstein",
     "Director, Master of Science in Human Resource Management",
     "Global Leadership and Management", "director", FIU_GLM,
     "Runs the MS in HR Management. Leadership style and blind spots are "
     "the exact categories the AI reports on."),
    ("fiu.edu", "Nathan", "Hiller",
     "Executive Director, Center for Leadership",
     "Global Leadership and Management", "vp", FIU_GLM,
     "Runs FIU's Center for Leadership. Leadership assessment is his "
     "field, so he will judge the method rather than the pitch."),
    ("fiu.edu", "Kimberly", "Taylor",
     "Chair, Department of Marketing and Logistics",
     "Marketing and Logistics", "director", FIU_MKT,
     "Department chair for marketing. Stages three and six, validation and "
     "the investor pitch, map onto her curriculum directly."),
    ("fiu.edu", "Jaclyn", "Tanenbaum",
     "Director, Master of Science Marketing Program",
     "Marketing and Logistics", "director", FIU_MKT,
     "Directs the MS Marketing programme, so a single-session format that "
     "fits an existing module is the practical question."),
    ("fiu.edu", "Anna", "Pietraszek",
     "Director, Eugenio Pino and Family Global Entrepreneurship Center",
     "Marketing and Logistics", "director", FIU_MKT,
     "Runs the entrepreneurship centre. Six stages from ideation to "
     "investor pitch is a venture lifecycle in one session."),
    ("fau.edu", "Cristina", "Lopez Vergara",
     "Director, Career Center Internships and Co-ops",
     "Career Center", "director", FAU_DIR,
     "Directs internships and co-ops at FAU. Those place a few students "
     "well and cannot reach a cohort, which is the gap a 60 minute "
     "session fills rather than competes with."),
    ("fsu.edu", "Rob", "Liddell",
     "Assistant Vice President, Career Services",
     "Career Center", "vp", FSU_DIR,
     "Runs career services for the whole university. The institutional "
     "route rather than a single course."),
    ("fsu.edu", "Devan", "Lane",
     "Director, Administration and Operations",
     "Career Center", "director", FSU_DIR,
     "Operations director at a career centre large enough to run 28 staff, "
     "so scheduling a cohort is a solved problem there."),
    ("fsu.edu", "Tracey", "Dowling",
     "Program Director, Career Development Services",
     "Career Center", "director", FSU_LIAISON,
     "Program director for career development and sits on the experiential "
     "learning team. Both halves of the decision in one person."),
    ("fsu.edu", "Tiffany", "Leung",
     "Program Manager, Career Liaison for the FAMU-FSU College of Engineering",
     "Career Center", "manager", FSU_LIAISON,
     "Liaison to the engineering college, so her students are the IT and "
     "computing population. They can build; deciding under ambiguity is "
     "the gap."),
    ("fsu.edu", "Jelani", "James",
     "Career Liaison, College of Arts and Sciences: Science, Technology and Mathematics Majors",
     "Career Center", "manager", FSU_LIAISON,
     "Covers science and technology majors and sits on employer relations, "
     "so he hears what recruiters say those students lack."),
    ("fsu.edu", "Caitlin", "Hattaway",
     "Career Liaison, College of Communication and Information",
     "Career Center", "manager", FSU_LIAISON,
     "Career liaison on the experiential learning team. Communication "
     "students sit closest to the marketing programmes in the target list."),
    ("fsu.edu", "Christy", "Mantzanas",
     "Program Director, Academic and Student Services",
     "Career Center", "director", FSU_LIAISON,
     "Program director on the employer relations side, where the gap "
     "between what employers expect and what graduates do shows up first."),
    ("fsu.edu", "Cassi", "Foster",
     "Career Liaison, Employer Relations",
     "Career Center", "manager", FSU_LIAISON,
     "Employer relations liaison. Employers notice judgement under "
     "pressure long before they notice technical gaps."),
    ("famu.edu", "Iris", "Young-Clark",
     "Director, Office of Career Development Services",
     "School of Business and Industry", "director", FAMU_SBI,
     "Directs career development inside FAMU's business school, so the "
     "marketing and HR programmes in the target list are her students."),
    ("famu.edu", "Vanessa", "Chatman",
     "Assistant Director, Office of Career Development Services",
     "School of Business and Industry", "director", FAMU_SBI,
     "Assistant director with a GCDF credential, so she assesses career "
     "tools on methodology rather than on marketing."),
    ("famu.edu", "Kelvin", "Farmer",
     "Relationship Manager, Internship and Career Professional Leadership Development Programs",
     "School of Business and Industry", "manager", FAMU_SBI,
     "Runs leadership development and internship programmes. Leadership "
     "development is the exact category the simulation reports on."),
    ("mdc.edu", "Christian", "Ortega",
     "Workforce Manager, Tech and Innovation",
     "MDC Works", "manager", MDC_WORKS,
     "Owns tech and innovation workforce services at MDC. His students can "
     "build; the gap at graduation is deciding under ambiguity."),
    ("mdc.edu", "Moses", "Washington",
     "Workforce Services Manager, Business",
     "MDC Works", "manager", MDC_WORKS,
     "Covers the business student population, which is where the marketing "
     "and HR programmes in the target list sit."),
    ("mdc.edu", "Kadijah", "Belle",
     "Internship Services Manager",
     "MDC Works", "manager", MDC_WORKS,
     "Runs internships. They do not scale to a whole cohort; a 60 minute "
     "session does. Position as complementary, never as a replacement."),
    ("mdc.edu", "Yvonne", "Malave",
     "Director, MDC Works",
     "MDC Works", "director", MDC_WORKS,
     "Directs MDC Works across campuses at one of the largest colleges in "
     "the country, so anything adopted reaches very large numbers."),
    ("mdc.edu", "Dana", "Dellacamera",
     "Director, MDC Works",
     "MDC Works", "director", MDC_WORKS,
     "Second MDC Works director. Decisions here affect a multi-campus "
     "workforce programme rather than a single department."),
    ("mdc.edu", "Maria", "Sincere",
     "Workforce Services Manager",
     "MDC Works", "manager", MDC_WORKS,
     "Workforce services manager at MDC, judged on whether graduates can "
     "actually operate rather than what they covered."),
    ("mdc.edu", "Solange", "Charles",
     "Workforce Services Manager, Employment",
     "MDC Works", "manager", MDC_WORKS,
     "Employment-focused manager, so she hears directly what employers say "
     "is missing when they interview MDC graduates."),
    ("usf.edu", "Addye", "Buckley-Burnell",
     "Associate VP and Executive Director, Center for Career and Professional Development",
     "Center for Career and Professional Development", "vp", USF_STAFF,
     "Runs the whole career centre at USF. The institutional-partnership "
     "route rather than a single course."),
    ("usf.edu", "Carmen", "Goldsmith",
     "Assistant Vice President",
     "Center for Career and Professional Development", "vp", USF_STAFF,
     "Second in the career centre leadership. Decides what gets adopted "
     "across the whole operation."),
    ("usf.edu", "Diane", "Mellon",
     "Assistant Director of Experiential Learning",
     "Center for Career and Professional Development", "director", USF_STAFF,
     "Owns experiential learning by title. The War Room is a 60 minute "
     "experiential format that fits one class session, which is the usual "
     "blocker for experiential programming."),
    ("usf.edu", "Grace", "Kondo",
     "Experiential Learning Coordinator",
     "Center for Career and Professional Development", "manager", USF_STAFF,
     "Coordinates experiential learning at USF. Scheduling a cohort into "
     "one 60 minute session is the practical problem she solves."),
    ("usf.edu", "Nathaniel", "Barnes",
     "Experiential Learning Coordinator",
     "Center for Career and Professional Development", "manager", USF_STAFF,
     "Second experiential learning coordinator at USF, so the programme is "
     "resourced and actively run rather than aspirational."),
    ("usf.edu", "Alexandra", "Kulawsky",
     "Assistant Director of Internship Development",
     "Center for Career and Professional Development", "director", USF_STAFF,
     "Internships do not scale to a whole cohort. A 60 minute simulation "
     "does. Position as complementary, never as a replacement."),
    ("usf.edu", "Adelia", "Douglas",
     "Assistant Director of Career Development",
     "Center for Career and Professional Development", "director", USF_STAFF,
     "Career development lead at a very large public university, where "
     "anything one to one is the bottleneck."),
    ("usf.edu", "Chad", "Wallace",
     "Assistant Director of Career Development",
     "Center for Career and Professional Development", "director", USF_STAFF,
     "Second career development assistant director at USF. Readiness at "
     "volume is the recurring problem."),
    ("unf.edu", "Scott", "Curry",
     "Senior Director, Industry Engagement",
     "Career Services", "vp", UNF_STAFF,
     "Leads industry engagement at UNF, so he hears directly what employers "
     "say graduates cannot do. Judgement under pressure is a common gap."),
    ("unf.edu", "Vivian", "Senior",
     "Senior Director, Student Engagement",
     "Career Services", "vp", UNF_STAFF,
     "Owns the student side of UNF career services, which is where a "
     "cohort-wide format would actually be scheduled."),
    ("unf.edu", "Manny", "Velasquez-Paredes",
     "Director, Career Education",
     "Career Services", "director", UNF_STAFF,
     "Director of career education: the curriculum question, not the "
     "placement question, which is exactly what the AI feedback addresses."),
    ("unf.edu", "Patricia", "Palmer",
     "Associate Director, Industry Engagement",
     "Career Services", "director", UNF_STAFF,
     "Associate director on the industry engagement team at UNF, connecting "
     "employer expectations back to student preparation."),
    ("unf.edu", "Lauren", "Linster",
     "Associate Director, Industry Engagement",
     "Career Services", "director", UNF_STAFF,
     "Industry engagement associate director. Employers notice decision "
     "making long before they notice technical gaps."),
    ("unf.edu", "Brooke", "Hammon",
     "Assistant Director, Industry Engagement",
     "Career Services", "director", UNF_STAFF,
     "Assistant director for industry engagement at UNF, close enough to "
     "employers to judge whether the simulation reflects real pressure."),
    ("ucf.edu", "Mirtha", "Bailey",
     "Assistant Director, Employer Relations",
     "Dixon Career Development Center", "director", UCF_ER,
     "Assistant director on UCF's employer relations team. Employer "
     "relations staff hear directly what recruiters say graduates cannot "
     "do, and decision-making under pressure is a common gap."),
    ("ucf.edu", "Taylor", "Sandifer",
     "Assistant Director, Employer Relations",
     "Dixon Career Development Center", "director", UCF_ER,
     "Assistant director handling career services sponsorship at UCF, so "
     "used to evaluating outside programmes brought to students."),
    ("ucf.edu", "Heather", "Pfingston",
     "Assistant Director, Employer Relations",
     "Dixon Career Development Center", "director", UCF_ER,
     "Third assistant director on the same team: UCF runs employer "
     "relations at a scale where a cohort-wide format is practical."),
    ("ucf.edu", "Yonitha", "Berryhill",
     "Coordinator, Employer Relations",
     "Dixon Career Development Center", "manager", UCF_ER,
     "Employer relations coordinator with SHRM-CP certification, so the "
     "HR and people-management angle of the simulation lands directly."),
    ("ucf.edu", "Levi", "Thaxton",
     "Coordinator, Employer Relations",
     "Dixon Career Development Center", "manager", UCF_ER,
     "Employer relations coordinator at UCF, one of the largest student "
     "bodies in the country; career readiness at volume is the problem."),
    ("valenciacollege.edu", "Molly", "McIntire",
     "Assistant Vice President, Workforce Education",
     "Career and Workforce Education", "vp", VAL_CWE,
     "Leads workforce education at a large state college, workforce "
     "readiness framing rather than the academic-credit framing."),
    ("valenciacollege.edu", "Michelle", "Terrell",
     "Internship & Placement Office, Director, Work-Based Learning",
     "Career and Workforce Education", "director", VAL_CWE,
     "Runs work-based learning. Internships do not scale to a whole "
     "cohort; a 60-minute simulation does. Position as complementary, "
     "never as a replacement."),
]


def stable_id(domain: str, first: str, last: str) -> str:
    """Deterministic id so re-running this script inserts nothing new."""
    key = f"{domain}|{first}|{last}".lower().encode()
    return hashlib.md5(key).hexdigest()[:12]


async def main():
    state = StateManager()
    await state.init_db()

    added = 0

    for domain, first, last, title, dept, seniority, url, note in PEOPLE:
        company = await state.get_company_by_domain(domain)
        if not company:
            print(f"  ! no company row for {domain}, "
                  f"run seed_florida_institutions.py first")
            continue
        pid = stable_id(domain, first, last)
        prospect = Prospect(
            id=pid,
            company_id=company.id,
            first_name=first,
            last_name=last,
            title=title,
            department=dept,
            seniority=seniority,
            # Left blank on purpose, see the module docstring.
            email="",
            email_status="",
            source="web_research",
            source_url=url,
            status="new",
            personalization_notes=note,
            company=company.name,
            industry=company.industry,
        )
        await state.add_prospect(prospect)
        print(f"  {first} {last[:18]:20} {title[:52]}")
        added += 1

    print(f"\n  {len(PEOPLE)} people processed. Emails unresolved: Harvey needs a")
    print("  verifier key (Reoon/ZeroBounce/Hunter) before any address is sendable.")



if __name__ == "__main__":
    asyncio.run(main())
