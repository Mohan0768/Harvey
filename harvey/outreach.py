"""The War Room beta invitation: the one email strategy Harvey sends.

This is not a sales pitch and must never become one. The ask is that an
educator lets their students run the simulation and tells us what to fix.
Everything here serves that: the structure is the six sections of the
outreach brief, in order, and the only variation between two emails is the
part that genuinely differs between two readers, their role and their
institution.

Why a composer and not a bare prompt. Three of the brief's rules are
absolute: never fabricate anything about KK, the organisation or the
recipient; keep the demo URL and reply address exact; never drift into a
generic pitch. A prompt can ask for those. Only code can guarantee them. So
the email is assembled here from verified facts, and the model, when it is
available at all, is allowed to rewrite the prose but never to introduce a
fact, a link or an address that did not come from this file.

What is deliberately missing. Section one introduces KK and the
organisation. The project knows the product cold and the person not at all,
so that sentence is built from configuration and simply omitted when the
configuration is empty. An omitted sentence is a smaller problem than an
invented biography, and compose() reports the omission in `notes` so it is
visible rather than silent.
"""
from __future__ import annotations

import asyncio
import random
import re
import zlib
from dataclasses import dataclass, field

from harvey import roles

KIND = "manual"

# Section 2 and the product description. Every claim traces to
# skills/product_knowledge.md, which traces to the public site.
# Every block below is a tuple of phrasings that say exactly the same thing.
# Only the wording differs: no variant claims anything another does not.
#
# Why bother. Forty-one of the contacts on this list are careers staff, so
# they share a role, an angle and a subject pool. Writing one sentence per
# block meant forty-one near-identical emails, which is what spam filters
# cluster on and what a reader recognises the moment a colleague forwards
# them one. Each block is chosen by its own hash of the contact id, so the
# blocks move independently and two people at one institution get visibly
# different mail.

BUILDING = (
    "We are building HumanFirst to close the gap between what business "
    "programs teach and what running a company actually demands: deciding "
    "under pressure, with incomplete information.",

    "We are building HumanFirst because the wall most graduates hit is not "
    "knowledge. It is making decisions with incomplete information while a "
    "clock runs.",

    "We are building HumanFirst for the part of business that is hard to "
    "teach from a textbook: choosing well when the information is thin and "
    "the pressure is real.",

    "We are building HumanFirst around a simple observation. Students can "
    "explain how a business works long before they can run one under "
    "pressure.",
)

INVITE = (
    "As a first step, our product War Room is built and ready for beta "
    "users to try and give feedback on.",

    "As a first step, War Room is built and open to beta users, and we are "
    "after honest feedback on it.",

    "As a first step, we have finished War Room and it is ready for beta "
    "users to put it through its paces.",

    "As a first step, War Room is built. It is ready for beta users, and "
    "their feedback is what we need next.",
)

WHAT_IT_IS = (
    "It runs 60 minutes. Students take a venture through six stages, make "
    "over 100 decisions, and an AI reports on how they decided, not only "
    "what they chose.",

    "In 60 minutes a student takes a venture through six stages and makes "
    "over 100 decisions, while an AI tracks how they decided rather than "
    "just what they picked.",

    "It takes 60 minutes. Six stages, over 100 decisions, and an AI that "
    "reports back on how the student decided, not only on the choices "
    "themselves.",
)

STUDENT_CLOSE = (
    "It is a chance for them to take part in real product testing, and to "
    "add that experience to their resume.",

    "They get to take part in testing a real product, which is worth "
    "something on a resume.",

    "Taking part in real product testing also gives them something "
    "concrete to put on a resume.",

    "For them it is real product testing, and a line on a resume that is "
    "actually theirs.",
)

DEMO_LEAD = (
    "You can try it yourself to get a glimpse:",
    "You can try it yourself here:",
    "If you would like a glimpse of it first:",
    "You can run it yourself in a few minutes:",
)

CTA_WITH_BOOKING = (
    "If you are interested, you can schedule a call at {booking} or email "
    "{reply}.",

    "If this is of interest, there is a calendar at {booking}, or you can "
    "email {reply}.",

    "If you would like to talk it through, you can book a time at "
    "{booking} or write to {reply}.",
)

CTA_PLAIN = (
    "If you are interested, you can schedule a call or email {reply}.",
    "If this is of interest, just reply here or write to {reply}.",
    "If you would like to talk it through, email {reply}.",
)

# Per role: subject lines, the sentences that make the invitation theirs,
# and what the students in front of THEM get out of it. No role is offered
# anything another is not; only the emphasis moves.
ANGLES: dict[str, dict] = {
    roles.DEAN: {
        "subjects": (
            "Gift your students the experience of running a business",
            "A real-world business experience for your students",
            "An hour of running a business, for your students",
            "Experiential learning that fits in one class session",
        ),
        "invites": (
            "Your view on where this fits in an experiential learning track "
            "at {company} would be especially useful.",
            "Your read on whether this belongs in an experiential learning "
            "track at {company} would be especially useful.",
            "If you see a place for it at {company}, your view on where it "
            "fits would be especially useful.",
        ),
        "values": (
            "experiential learning that fits inside one class session",
            "a venture to run end to end without leaving the timetable",
            "decision-making practice that a lecture cannot give them",
        ),
    },
    roles.ACADEMIC_HEAD: {
        "subjects": (
            "A real-world business experience for your students",
            "Give your students a business to run for an hour",
            "An hour of running a venture, inside your program",
            "A practical business experience for your program",
        ),
        "invites": (
            "Your view on where this could sit inside a program at "
            "{company} would be especially useful.",
            "Your read on whether this fits a program at {company} would be "
            "especially useful.",
            "If it suits a module at {company}, your view on where it fits "
            "would be especially useful.",
        ),
        "values": (
            "a full venture lifecycle inside one session",
            "the decision-making a syllabus rarely has room for",
            "a venture to run end to end in a single session",
        ),
    },
    roles.PROFESSOR: {
        "subjects": (
            "Let your students run a business for 60 minutes",
            "A practical business experience for your class",
            "An hour of running a venture, for your class",
            "Something practical to put in front of your students",
        ),
        "invites": (
            "If it fits what you teach at {company}, your students could "
            "run it as a class session and tell us what to change.",
            "If it suits your teaching at {company}, your students could "
            "run it in class and tell us what needs fixing.",
            "If it maps onto what you teach at {company}, a class could run "
            "it and tell us what to improve.",
        ),
        "values": (
            "a full venture lifecycle inside one class session",
            "the decision-making a lecture cannot really rehearse",
            "a venture to run end to end between two classes",
        ),
    },
    roles.CAREER_COUNSELOR: {
        "subjects": (
            "An opportunity for your students to experience entrepreneurship",
            "Practical business experience for the students you advise",
            "Something practical for the students you advise",
            "An hour of running a business, for your students",
        ),
        "invites": (
            "Your read on whether this helps the students you advise would "
            "be especially useful.",
            "Your view on whether this is worth recommending to the students "
            "you advise would be especially useful.",
            "Your read on whether this is useful to the students you sit "
            "with would be especially useful.",
        ),
        "values": (
            "decision-making practice under real time pressure",
            "something concrete to talk about in an interview",
            "experience of a decision that actually had stakes",
        ),
    },
    roles.CAREER_SERVICES: {
        "subjects": (
            "Invite your students to test a new business simulation",
            "A practical business experience for your students",
            "An hour of running a business, for your students",
            "Something practical to offer your students this term",
            "Real product testing your students can put on a resume",
        ),
        "invites": (
            "Your read on whether this adds anything alongside the "
            "internships you already run would be especially useful.",
            "Your view on whether this sits well next to the placements you "
            "already run would be especially useful.",
            "Your read on whether this is worth offering alongside what "
            "{company} already runs would be especially useful.",
            "Your view on whether this earns a place in what you already "
            "offer students would be especially useful.",
        ),
        "values": (
            "decision-making practice under real time pressure",
            "something concrete to talk about with an employer",
            "experience of running a venture, not reading about one",
        ),
    },
    roles.HR_LEADER: {
        "subjects": (
            "Give your people the experience of running a business",
            "A practical decision-making experience for your team",
            "An hour of running a business, for your team",
        ),
        "invites": (
            "Your view on how the leadership feedback reads to an employer "
            "would be especially useful.",
            "Your read on whether the leadership feedback is useful to an "
            "employer would be especially useful.",
            "Your view on how this lands with someone hiring would be "
            "especially useful.",
        ),
        "values": (
            "leadership patterns and blind spots made visible",
            "a readout on how someone decides under pressure",
            "decision-making you can actually observe",
        ),
    },
    roles.MARKETING_LEADER: {
        "subjects": (
            "Let your students run a venture to the investor pitch",
            "A real-world business experience for your students",
            "From validation to the investor pitch, in an hour",
        ),
        "invites": (
            "Your view on how students handle validation and the investor "
            "pitch would be especially useful.",
            "Your read on how students cope with validation and pitching "
            "would be especially useful.",
            "Your view on whether the validation stages ring true would be "
            "especially useful.",
        ),
        "values": (
            "market validation and an investor pitch to run",
            "a pitch to make once the numbers are their own",
            "validation against a market that pushes back",
        ),
    },
    roles.IT_LEADER: {
        "subjects": (
            "A business decision experience for technical students",
            "Give technical students a venture to run",
            "The business side, for students who can already build",
        ),
        "invites": (
            "Your view on how technical students handle the decision side "
            "would be especially useful.",
            "Your read on how technical students cope with the business "
            "decisions would be especially useful.",
            "Your view on whether this fills a gap for technical students "
            "would be especially useful.",
        ),
        "values": (
            "the decision side that technical study rarely covers",
            "business judgement to go with what they can already build",
            "decisions where the right answer is not in the spec",
        ),
    },
    roles.SECURITY: {
        "subjects": (
            "A business decision experience for technical students",
            "Give technical students a venture to run",
            "Decisions under pressure, for technical students",
        ),
        "invites": (
            "Your view on how technical students handle decisions under "
            "pressure would be especially useful.",
            "Your read on how your students cope with deciding on thin "
            "information would be especially useful.",
            "Your view on whether the pressure feels real would be "
            "especially useful.",
        ),
        "values": (
            "the decision side that technical study rarely covers",
            "judgement calls made on incomplete information",
            "decisions where the right answer is not in the spec",
        ),
    },
    roles.EXECUTIVE: {
        "subjects": (
            "An invitation to test a new business simulation",
            "The experience of running a business, in 60 minutes",
            "A business simulation we would like you to break",
        ),
        "invites": (
            "Your read on whether the decisions feel true to running a "
            "company would be especially useful.",
            "Your view on whether the decisions ring true to someone who "
            "has run a company would be especially useful.",
            "Your read on whether we have the decisions right would be "
            "especially useful.",
        ),
        "values": (
            "decisions that mirror running a real venture",
            "the trade-offs an operator actually faces",
            "choices with consequences that follow them",
        ),
    },
    roles.UNCLASSIFIED: {
        "subjects": (
            "Gift your students the experience of running a business",
            "An opportunity for your students to experience entrepreneurship",
            "An hour of running a business, for your students",
        ),
        "invites": (
            "Your feedback on whether this is useful for your students "
            "would be especially valuable.",
            "Your read on whether this is worth offering your students "
            "would be especially valuable.",
            "Your view on whether this is useful to your students would be "
            "especially valuable.",
        ),
        "values": (
            "decision-making practice under real time pressure",
            "experience of running a venture, not reading about one",
            "something concrete to show for an hour of their time",
        ),
    },
}


@dataclass
class Draft:
    """One composed email, plus what had to be left out of it."""
    subject: str
    body: str
    role: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.body.split())


def _pick(options, seed: str, salt: str = "", variant: int = 0):
    """Stable choice from a block's phrasings.

    Two properties matter. The same contact always gets the same wording,
    so regenerating an untouched draft does not silently reshuffle it. And
    each block is salted with its own name, so the blocks do not move in
    lockstep: without the salt every contact who drew phrasing two for the
    subject would draw phrasing two for all the rest, and twelve variants
    would produce four distinct emails.
    """
    options = tuple(options)
    if not options:
        return ""
    # variant 0 is the stable draw, so an email composed today reads the
    # same tomorrow. A higher variant is an explicit request for different
    # wording, which is what Regenerate asks for: clicking it and seeing
    # the identical paragraph back is indistinguishable from a dead button.
    key = str(seed) + "|" + salt
    if variant:
        key += "|" + str(variant)
    return options[zlib.crc32(key.encode("utf-8")) % len(options)]


def _field(record, name: str) -> str:
    if isinstance(record, dict):
        return str(record.get(name) or "").strip()
    return str(getattr(record, name, "") or "").strip()


def settings(config) -> dict:
    """The verified facts this email is allowed to state, from config."""
    outreach = getattr(config, "outreach", None)
    product = getattr(config, "product", None)
    persona = getattr(config, "persona", None)
    return {
        "organization": getattr(outreach, "organization", "") or "",
        "founder_name": getattr(outreach, "founder_name", "") or "",
        "founder_bio": getattr(outreach, "founder_bio", "") or "",
        "demo_url": getattr(outreach, "demo_url", "") or "",
        "reply_to": getattr(outreach, "reply_to", "") or "",
        "booking_url": getattr(getattr(product, "offer", None),
                               "booking_url", "") or "",
        "product": getattr(product, "name", "") or "",
        "persona_name": getattr(persona, "name", "") or "",
        "persona_role": getattr(persona, "role", "") or "",
        "persona_company": getattr(persona, "company", "") or "",
    }


def _invite_line(text: str, company: str) -> str:
    """The role sentence, with the institution named only when we know it."""
    if company:
        return text.format(company=company)
    # No company on the record, so every phrasing that leans on one has to
    # degrade to something that still reads like a sentence.
    for pattern, plain in (
        (" track at {company} would", " track would"),
        (" inside a program at {company}", " inside a program"),
        (" at {company},", ","),
        (" at {company}", ""),
        ("{company}", "your institution"),
    ):
        text = text.replace(pattern, plain)
    return text


def compose(prospect, config, role: str = "", variant: int = 0) -> Draft:
    """Assemble the invitation for one person. Never invents a fact.

    The order is the brief's own: who is writing, what we are building and
    why, the beta invitation, what it is and what their students get, the
    demo link, and the ask. Only two things change between two emails, the
    sentence that makes the invitation theirs and the one that names what
    their students get, because those are the only parts that genuinely
    differ between two readers.
    """
    s = settings(config)
    notes: list[str] = []
    role = role or roles.resolve(prospect)
    angle = ANGLES.get(role) or ANGLES[roles.UNCLASSIFIED]

    first = _field(prospect, "first_name")
    company = _field(prospect, "company")
    seed = _field(prospect, "id") or _field(prospect, "email") or first

    parts: list[str] = ["Hi " + first + "," if first else "Hello,"]

    # 1. Who is writing. Built only from configured facts: with none set the
    # sentence disappears rather than inventing a founder or a credential.
    who = ""
    if s["founder_name"] and s["organization"]:
        who = "I am " + s["founder_name"] + ", from " + s["organization"] + "."
    elif s["organization"]:
        who = "I am reaching out from " + s["organization"] + "."
    elif s["founder_name"]:
        who = "I am " + s["founder_name"] + "."
    else:
        notes.append("No organisation or founder name configured, so the "
                     "email does not say who it is from.")
    if s["founder_bio"]:
        who = (who + " " + s["founder_bio"]).strip()
    elif not s["founder_name"]:
        # Only worth saying when the email can name nobody at all. With a
        # founder name the introduction already reads "I am KK, from ...",
        # so reporting that it "says nothing about the person" was false.
        notes.append("No founder name or bio configured, so the introduction "
                     "says nothing about who is writing.")
    if who:
        parts.append(who)

    # 2. What we are building, and the problem it addresses.
    parts.append(_pick(BUILDING, seed, "building", variant))

    # 3. The invitation, made theirs.
    parts.append(_pick(INVITE, seed, "invite", variant) + " "
                 + _invite_line(_pick(angle["invites"], seed, "role", variant), company))

    # 4. What it is, and what their students get from it.
    parts.append(_pick(WHAT_IT_IS, seed, "what", variant)
                 + " For your students that means "
                 + _pick(angle["values"], seed, "value", variant) + ".")
    parts.append(_pick(STUDENT_CLOSE, seed, "close", variant))

    # 5. The demo. Its URL comes from config and is never guessed.
    if s["demo_url"]:
        parts.append(_pick(DEMO_LEAD, seed, "demo", variant) + "\n" + s["demo_url"])
    else:
        notes.append("No demo URL configured, so the email cannot link to "
                     "the product.")

    # 6. The ask. A booking link is offered only when one is configured;
    # the gate allows two links and this is the second, so nothing else may
    # add one.
    booking = s["booking_url"]
    if booking and s["reply_to"]:
        parts.append(_pick(CTA_WITH_BOOKING, seed, "cta", variant).format(
            booking=booking, reply=s["reply_to"]))
    elif s["reply_to"]:
        parts.append(_pick(CTA_PLAIN, seed, "cta", variant).format(
            reply=s["reply_to"]))
    else:
        parts.append("If you are interested, just reply to this email.")
        notes.append("No reply address configured, so the email asks for a "
                     "plain reply.")

    sign = [v for v in (s["persona_name"],
                        ", ".join(x for x in (s["persona_role"],
                                              s["persona_company"]) if x))
            if v]
    if sign:
        parts.append("Best,\n" + "\n".join(sign))

    return Draft(subject=_pick(angle["subjects"], seed, "subject", variant),
                 body="\n\n".join(parts), role=role, notes=notes)


def readiness(config) -> dict:
    """What is still missing before these emails should go out.

    Separate from compose() on purpose: the dashboard needs to warn once, up
    front, rather than after fifty drafts already carry the same gap.
    """
    s = settings(config)
    missing, warnings = [], []
    if not s["organization"]:
        missing.append("outreach.organization")
    if not s["demo_url"]:
        missing.append("outreach.demo_url")
    if not s["reply_to"]:
        missing.append("outreach.reply_to")
    if not s["founder_bio"] and not s["founder_name"]:
        warnings.append("No verified founder details, so no email will "
                        "introduce the person behind the product.")
    if "placeholder" in s["persona_name"].lower() or not s["persona_name"]:
        warnings.append("The sender persona is still the placeholder identity, "
                        "so every email would be signed by a person who does "
                        "not exist.")
    return {"ready": not missing, "missing": missing, "warnings": warnings}


# How much the model is allowed to change, and what it may never touch. The
# checks below are the enforcement; this text is only the request.
REFINE_PROMPT = """You are rewriting one invitation email so it reads as though a
person wrote it for this individual, not as though a template produced it.

THE EMAIL AS COMPOSED:
Subject: {subject}

{body}

WHO IT IS GOING TO:
- Name: {name}
- Title: {title}
- Organisation: {company}
- Role category: {role_label}

WHAT YOU MAY DO:
- Fix grammar, spelling and punctuation.
- Improve how the existing sentences read, so they sound like a person
  wrote them for this reader rather than a template.
- Vary the subject line.

WHAT YOU MAY NOT DO, EVER:
- Add a fact, a sentence or a section. Every claim must already appear
  above. You do not know this person's achievements, their programme,
  their students or their institution beyond the four lines listed.
- Remove any of the six beats, or reorder them.
- Add, remove or alter any URL or email address.
- Turn the invitation into a sales pitch, or pitch anything else.
- Promise certifications, placements, jobs or any student outcome.
- Claim the product is established, proven or widely adopted.
- Use an em dash or an en dash anywhere.
- Exceed {max_words} words in the body.

The six beats, in this order: who is writing, what we are building and
the problem it addresses, the beta invitation, what it is and what
students get, the demo link, and the ask.

Return ONLY JSON: {{"subject": "...", "body": "..."}}"""


def _addresses(text: str) -> set:
    return set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text or ""))


def _urls(text: str) -> set:
    return set(re.findall(r"https?://[^\s<>\"\')]+", text or ""))


def check_refinement(original: Draft, subject: str, body: str,
                     to_email: str, config) -> list:
    """Why a rewrite must be rejected, or [] when it may be used.

    The model is trusted with prose and with nothing else. Anything it could
    get catastrophically wrong, a link, an address, a promise, a length, is
    verified here against the draft that code built.
    """
    from harvey.gate import MAX_BODY_WORDS, pre_send_check

    s = settings(config)
    problems = []
    if not subject.strip() or not body.strip():
        return ["model returned an empty subject or body"]

    if len(body.split()) > MAX_BODY_WORDS:
        problems.append("rewrite ran to " + str(len(body.split())) + " words")

    # Links and addresses must match the composed draft exactly. Not "looks
    # similar": a single altered character in a URL is a phishing link and a
    # single altered character in an address is mail to a stranger.
    if _urls(body) != _urls(original.body):
        problems.append("rewrite changed the links in the email")
    if _addresses(body) != _addresses(original.body):
        problems.append("rewrite changed an email address in the copy")
    if s["demo_url"] and body.count(s["demo_url"]) != 1:
        problems.append("rewrite did not keep exactly one demo link")

    # Claims the brief forbids outright, whatever wording the model found.
    lowered = (subject + " " + body).lower()
    for phrase in ("guarantee", "guaranteed", "placement rate", "job offer",
                   "certification", "certified", "proven results",
                   "industry leading", "industry-leading", "trusted by"):
        if phrase in lowered:
            problems.append("rewrite claims " + repr(phrase))
            break

    check = pre_send_check(to_email, subject, body, kind=KIND)
    if not check.ok:
        problems.extend(check.reasons)
    return problems


# A model call that never returns is worse than no model at all: the person
# clicking Regenerate is left watching a spinner with no way to tell whether
# anything is happening. The composed draft is always ready, so waiting past
# this is never worth it.
REFINE_TIMEOUT_SECONDS = 45


async def refine(draft: Draft, prospect, config, brain,
                 timeout: float = REFINE_TIMEOUT_SECONDS) -> Draft:
    """Rewrite a composed draft with the model, or return it untouched.

    Every failure path keeps the composed draft: no model, a model that is
    unavailable, malformed JSON, or a rewrite that breaks one of the rules
    above. The email always exists and is always sendable; the model only
    ever improves how it reads.
    """
    from harvey.gate import MAX_BODY_WORDS

    if brain is None:
        return draft

    name = " ".join(x for x in (_field(prospect, "first_name"),
                                _field(prospect, "last_name")) if x)
    prompt = REFINE_PROMPT.format(
        subject=draft.subject, body=draft.body,
        name=name or "(not recorded)",
        title=_field(prospect, "title") or "(not recorded)",
        company=_field(prospect, "company") or "(not recorded)",
        role_label=roles.label(draft.role),
        max_words=MAX_BODY_WORDS,
    )

    try:
        result = await asyncio.wait_for(
            brain.think_json(prompt, session_id="harvey-outreach",
                             agent="writer", task="beta_invite"),
            timeout=timeout)
    except asyncio.TimeoutError:
        draft.notes.append("Model did not answer within "
                           + str(int(timeout)) + "s, so the composed wording "
                           "was kept.")
        return draft
    except Exception as exc:
        draft.notes.append("Model unavailable, so the composed wording was "
                           "kept: " + str(exc)[:160])
        return draft

    if not isinstance(result, dict):
        draft.notes.append("Model did not return usable JSON, so the composed "
                           "wording was kept.")
        return draft

    subject = str(result.get("subject") or "").strip()
    body = str(result.get("body") or "").strip()
    problems = check_refinement(draft, subject, body,
                                _field(prospect, "email"), config)
    if problems:
        draft.notes.append("Model rewrite rejected, composed wording kept: "
                           + "; ".join(problems)[:200])
        return draft

    return Draft(subject=subject, body=body, role=draft.role,
                 notes=draft.notes + ["Wording refined by the model."])


# How many different draws to try before accepting a repeat. The blocks
# multiply out to thousands of combinations, so a difference turns up in
# the first attempt or two; this is only a guard against looping forever on
# a contact whose every block happens to have one option.
MAX_VARIANTS = 24


def recompose(prospect, config, previous: str, role: str = "") -> Draft:
    """Compose wording that differs from what is already there.

    This is what Regenerate wants. compose() is deliberately stable, so
    asking it again for the same contact returns the same paragraphs, and a
    button that returns its own output looks broken however well it works.
    """
    previous = (previous or "").strip()
    draft = None
    # Try the variants in a random order. Walking them in sequence and
    # taking the first difference settles into a two-draft cycle: click,
    # click, click and you see A, B, A again. Picking at random means a
    # fresh draw each time, which is the whole point of the button.
    #
    # compose() itself stays deterministic at variant 0, so ordinary
    # generation is still reproducible; only an explicit rewrite is random.
    order = list(range(1, MAX_VARIANTS + 1))
    random.shuffle(order)
    for variant in order:
        draft = compose(prospect, config, role=role, variant=variant)
        if draft.body.strip() != previous:
            return draft
    # Every draw matched, which means there is genuinely nothing else to
    # say. Returning the last one is honest; pretending otherwise is not.
    if draft is not None:
        draft.notes.append("Every phrasing of this email reads the same for "
                           "this contact, so the wording is unchanged.")
    return draft or compose(prospect, config, role=role)
