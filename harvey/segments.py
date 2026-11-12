"""Which of the four target segments a contact belongs to.

The outreach brief splits the audience four ways, and the right email for a
cybersecurity chair is not the right email for a career services director.
Harvey has no notion of this, so it is derived here from what is already on
the record: title, department and institution.

Classification is deterministic and explainable on purpose. A rule you can
read beats a model call you cannot audit, and it costs nothing per contact.
"""
import re

TECHNICAL = "technical"
HR = "hr"
MARKETING = "marketing"
CAREER_CENTER = "career_center"
UNCLASSIFIED = ""

LABELS = {
    TECHNICAL: "Technical",
    HR: "HR",
    MARKETING: "Marketing",
    CAREER_CENTER: "Career Center",
    UNCLASSIFIED: "Unclassified",
}

# Ordered: the first pattern that matches wins. Academic departments are
# checked before career services, because "Career Liaison for the College of
# Engineering" is an engineering contact who happens to sit in careers, and
# the engineering angle is the one that lands.
RULES = (
    (TECHNICAL, r"""
        information\s+system | information\s+technolog |
        cyber | computer\s+scien | \bcomputing\b | informatics |
        software | engineering | technolog | \btech\b | data\s+scien |
        business\s+analytic
    """),
    (HR, r"""
        human\s+resource | \bHRM?\b | human\s+capital | organi[sz]ational\s+leader |
        organi[sz]ational\s+behav | people\s+management | talent\s+management |
        talent\s+acquisition | \bleadership\b
    """),
    (MARKETING, r"""
        marketing | logistics | brand | advertis | entrepreneur |
        communication\s+and\s+information | \bsales\b
    """),
    (CAREER_CENTER, r"""
        career | employer\s+(relations|engagement|success) | experien[ct]ial |
        internship | co-?op | work-?based\s+learning | placement |
        workforce | employab | student\s+success | professional\s+development
    """),
)

_COMPILED = tuple(
    (name, re.compile(pattern, re.IGNORECASE | re.VERBOSE))
    for name, pattern in RULES
)

# Acronyms are matched case-SENSITIVELY and checked first. Under IGNORECASE
# a bare \bIT\b matches the ordinary word "it", which would file half
# the database under Technical.
_ACRONYMS = re.compile(r"\b(IT|MIS|CIS|CS)\b")


def classify(title: str = "", department: str = "", company: str = "",
             notes: str = "") -> str:
    """Best segment for a contact, or "" when nothing matches.

    Title carries the most signal, then department. Company is last and
    weakest: nearly every record says "University", which says nothing
    about which department to write to. ``notes`` is accepted and ignored
    on purpose: it holds Claude's own research prose, and a note that
    merely mentions "the HR programmes" was filing marketing contacts
    under HR.
    """
    for text in (title, department, company):
        if not text:
            continue
        text = str(text)
        if _ACRONYMS.search(text):
            return TECHNICAL
        for name, pattern in _COMPILED:
            if pattern.search(text):
                return name
    return UNCLASSIFIED


def label(category: str) -> str:
    return LABELS.get(category or UNCLASSIFIED, LABELS[UNCLASSIFIED])


# What the writer should emphasise for each segment. These are angles, not
# claims: the product facts still come from skills/product_knowledge.md.
ANGLES = {
    TECHNICAL: ("Technical students can build; the gap at graduation is "
                "deciding with incomplete information under time pressure. "
                "Lead with strategic thinking and pressure response."),
    HR: ("Leadership style, blind spots and people decisions. The AI report "
         "on HOW someone decided is the part that matters to this reader."),
    MARKETING: ("Market validation and the investor pitch are two of the six "
                "stages, which maps onto what these students already study."),
    CAREER_CENTER: ("Career readiness for a whole cohort in one session, with "
                    "individual feedback. Position alongside internships, "
                    "never as a replacement for them."),
    UNCLASSIFIED: ("No segment identified, so stay general: a 60 minute "
                   "simulation with individual AI feedback on decision-making."),
}


def angle(category: str) -> str:
    return ANGLES.get(category or UNCLASSIFIED, ANGLES[UNCLASSIFIED])
