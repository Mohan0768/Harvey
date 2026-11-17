"""Which professional role a contact holds, and the angle that fits it.

segments.py answers one question: which discipline does this person teach or
serve (technical, HR, marketing, careers)? That is the wrong axis for a beta
invitation. A dean and a professor in the same department need different
emails, while a dean of engineering and a dean of business need much the
same one. This module answers the other axis, what is this person's job,
and the two are meant to be used together.

Classification is deterministic and explainable, for the same reasons
segments.py is: a rule you can read beats a model call you cannot audit, it
costs nothing per contact, and it can never invent a category for someone.
"""
import re

SECURITY = "security"
CAREER_COUNSELOR = "career_counselor"
CAREER_SERVICES = "career_services"
DEAN = "dean"
ACADEMIC_HEAD = "academic_head"
PROFESSOR = "professor"
HR_LEADER = "hr_leader"
MARKETING_LEADER = "marketing_leader"
IT_LEADER = "it_leader"
EXECUTIVE = "executive"
UNCLASSIFIED = ""

LABELS = {
    DEAN: "Dean / Academic Director",
    ACADEMIC_HEAD: "Department or Program Head",
    PROFESSOR: "Professor / Faculty",
    CAREER_COUNSELOR: "Career Counselor",
    CAREER_SERVICES: "University Career Center",
    HR_LEADER: "HR Manager / Director",
    MARKETING_LEADER: "Marketing Manager",
    IT_LEADER: "IT / Technology Director",
    SECURITY: "Cybersecurity Professional",
    EXECUTIVE: "Business Owner / CEO",
    UNCLASSIFIED: "Unclassified",
}

# Ordered: the first rule that matches wins, so the order encodes every
# judgement call in this file.
#
# Academic rank is tested first. "Professor of Cybersecurity" is a professor,
# and the email that lands for them is about their classroom, not about
# incident response. Testing rank first also stops the acronym pass below
# from filing "Professor, MIS Department" under Technology.
#
# Both career rules sit above the seniority rules on purpose. A title names a
# function and a rank, and here the function picks the email: "Associate VP,
# Center for Career and Professional Development" is a careers person who
# happens to be a VP, and the careers angle is the one that lands. "Associate
# Dean for Technology Systems" mentions no career work, so it falls through
# to DEAN, which is correct.
#
# Each rule may also carry a case-SENSITIVE acronym pattern, tested just
# before its prose pattern. Case matters: under IGNORECASE a bare \bIT\b
# matches the ordinary word "it" and files half the database under
# Technology.
RULES = (
    (PROFESSOR, None, r"""
        professor | \bfaculty\b | lecturer | instructor | adjunct | \bprof\b
    """),
    (CAREER_COUNSELOR, None, r"""
        career\s+(counsel|advis|coach) | counselor | counsellor
    """),
    (CAREER_SERVICES, None, r"""
        \bcareer\b | employer\s+(relations|engagement|success|support) |
        experien[ct]ial | internship | co-?op\b | work-?based\s+learning |
        placement | workforce | employab | student\s+success |
        professional\s+development | talent\s+pipeline
    """),
    (DEAN, None, r"""
        \bdean\b | provost | chancellor | \brector\b
    """),
    (ACADEMIC_HEAD, None, r"""
        \bchair\b | department\s+head | head\s+of\s+(department|school) |
        program\s+(director|chair|head|coordinator) |
        academic\s+director | director\s+of\s+(the\s+)?(school|program|studies) |
        \bprincipal\b | academic\s+(affairs|services)
    """),
    (SECURITY, r"\b(CISO)\b", r"""
        cyber | information\s+security | infosec |
        security\s+(studies|program|analyst|operations)
    """),
    (HR_LEADER, r"\b(HR|HRM|CHRO)\b", r"""
        human\s+resource | human\s+capital | people\s+operations |
        chief\s+people | talent\s+(management|acquisition|development)
    """),
    (MARKETING_LEADER, r"\b(CMO)\b", r"""
        marketing | brand\s+(manager|director) |
        communications\s+(manager|director)
    """),
    (IT_LEADER, r"\b(IT|CIO|CTO|MIS|CIS)\b", r"""
        information\s+technolog | information\s+system |
        technology\s+(director|officer|lead) | director\s+of\s+technology |
        systems\s+(director|manager)
    """),
    (EXECUTIVE, r"\b(CEO|COO)\b", r"""
        founder | \bowner\b | managing\s+director |
        chief\s+executive | proprietor
    """),
    # Bare institutional seniority, tested last on purpose. "VP of Marketing"
    # is a marketing lead and matches above; "Vice President of Student
    # Affairs" has no functional word to match and lands here, which is the
    # result we want. An explicitly academic marker (dean, provost) never
    # waits this long: it is matched high up, so "Dean of Information
    # Systems" cannot be mistaken for a technology lead.
    (DEAN, r"\b(VP)\b", r"""
        vice\s+president | \bpresident\b
    """),
)

_COMPILED = tuple(
    (name,
     re.compile(acronym) if acronym else None,
     re.compile(pattern, re.IGNORECASE | re.VERBOSE))
    for name, acronym, pattern in RULES
)

# When the title says nothing useful, the discipline segment already stored
# on the record is still a real signal, so it is used rather than wasted.
# Only career_center maps cleanly onto a role; the rest describe a subject
# taught, not a job held, so they stay unclassified rather than guess.
_FROM_SEGMENT = {"career_center": CAREER_SERVICES}


def classify(title: str = "", department: str = "", company: str = "") -> str:
    """Best role for a contact, or "" when nothing matches.

    Title carries the most signal, then department. Company is last and
    weakest: nearly every record here says "University", which says nothing
    about what one person inside it does.
    """
    for text in (title, department, company):
        if not text:
            continue
        text = str(text)
        for name, acronym, pattern in _COMPILED:
            if acronym is not None and acronym.search(text):
                return name
            if pattern.search(text):
                return name
    return UNCLASSIFIED


def resolve(prospect) -> str:
    """Role for a prospect record, falling back to its stored segment.

    Accepts anything with the attributes or keys, so it works on both the
    Prospect dataclass and a plain row dict out of the database.
    """
    def field(name: str) -> str:
        if isinstance(prospect, dict):
            return str(prospect.get(name) or "")
        return str(getattr(prospect, name, "") or "")

    role = classify(field("title"), field("department"), field("company"))
    if role:
        return role
    return _FROM_SEGMENT.get(field("category"), UNCLASSIFIED)


def label(role: str) -> str:
    return LABELS.get(role or UNCLASSIFIED, LABELS[UNCLASSIFIED])


def known_roles() -> list[tuple[str, str]]:
    """(key, label) for every real role, for filter menus. Stable order."""
    seen, out = set(), []
    for key, _, _ in RULES:
        if key not in seen:
            seen.add(key)
            out.append((key, LABELS[key]))
    return out
