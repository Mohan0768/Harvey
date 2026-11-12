"""Build the outreach workbook: one row per prospect, copy ready to send.

Every address is published by the institution on its own staff page (see the
source column). Every email obeys the rules Harvey's pre-send gate enforces:
under 75 words, exactly one link, no em dashes, no banned phrases, nothing
claimed that the website does not support.

The trial link goes in the first touch. The Calendly link goes in the REPLY,
never in cold outreach: two links in a first email is a known spam signal and
the gate rejects it.

    .venv/Scripts/python.exe scripts/build_outreach_sheet.py
"""
import asyncio
import csv
import os

from harvey.gate import pre_send_check
from harvey.state import StateManager

TRIAL = "https://warroom-demo-262374983592.us-central1.run.app/"
CALENDLY = "https://calendly.com/kk-humanfirst/30min"
NL = "\n\n"

COPY = [
    ("Sotham", "experiential learning, one session",
     "Hi John," + NL +
     "Your title covers experiential learning, so you have probably hit the "
     "same wall most programs do: the formats that work need weeks of "
     "curriculum time." + NL +
     "Ours runs in 60 minutes. Students take a venture through six stages, "
     "make 100+ decisions under time pressure, and the AI reports on how "
     "they decided rather than just what they picked." + NL +
     "Free to try:"),
    ("Pacheco", "what employers keep saying",
     "Hi Ruth," + NL +
     "You sit between employers and students, so you hear the gap first. "
     "Usually it is not skills. It is deciding without enough information." + NL +
     "We built a 60 minute simulation for that. Six stages, 100+ decisions, "
     "and an AI read on each student leadership style and blind spots." + NL +
     "Free to try:"),
    ("Cirion", "one class session",
     "Hi Yisell," + NL +
     "Integrated partnerships usually mean long lead times, so here is the "
     "short version. A 60 minute business simulation that fits one class "
     "session, with individual AI feedback for every student on how they "
     "made decisions under pressure." + NL +
     "Easiest way to judge it is to run it yourself:"),
    ("Gonzalez-Duran", "a question about employer feedback",
     "Hi Efigenia," + NL +
     "Quick question. When employers tell you what your graduates are "
     "missing, how often is it judgement rather than technical skill?" + NL +
     "That is the gap we built for. A 60 minute simulation, six stages, and "
     "an AI report on how each student decides when the stakes are high." + NL +
     "Free to try:"),
    ("Clayton", "works across colleges",
     "Hi Harold," + NL +
     "Running liaison across colleges means anything you bring in has to "
     "work for very different departments." + NL +
     "This does. The same 60 minute simulation justifies itself to a "
     "computing chair, an HR chair and a marketing chair for different "
     "reasons, because the six stages cover strategy, people and growth." + NL +
     "Free to try:"),
    ("Telleria", "they can build, can they decide",
     "Hi David," + NL +
     "Your computing students can build almost anything. The harder "
     "question at graduation is whether they can decide with incomplete "
     "information and a clock running." + NL +
     "We compress that into 60 minutes. Six stages, 100+ decisions, and an "
     "AI read on strategic thinking and pressure response." + NL +
     "Free to try:"),
    ("Rodriguez", "validation and the pitch",
     "Hi Elise," + NL +
     "Two of our six stages are market validation and the investor pitch, "
     "which maps straight onto what your marketing students already study." + NL +
     "Difference is they do it live in 60 minutes, making 100+ decisions, "
     "and get an AI report on how they reasoned rather than a grade on the "
     "output." + NL +
     "Free to try:"),
]

COPY += [
    ("Bailey", "career readiness in 60 minutes",
     "Hi Mirtha," + NL +
     "Employer relations means you hear the readiness complaints before "
     "anyone else does." + NL +
     "We built a 60 minute simulation that puts students through six stages "
     "of running a venture, 100+ decisions, with an AI read on their "
     "leadership style and how they hold up under pressure." + NL +
     "Free to try:"),
    ("Sandifer", "worth 60 minutes of your time",
     "Hi Taylor," + NL +
     "You evaluate outside programs for UCF students regularly, so you will "
     "judge this faster than I can describe it." + NL +
     "A 60 minute business simulation, six stages, 100+ decisions, and "
     "individual AI feedback on how each student decided rather than what "
     "they chose." + NL +
     "Free to try:"),
    ("Pfingston", "fits one class session",
     "Hi Heather," + NL +
     "The usual blocker for experiential formats is curriculum time. This "
     "one runs in 60 minutes, so it fits a single class session for a whole "
     "cohort." + NL +
     "Students take a venture through six stages and get an AI report on "
     "their decision patterns and blind spots." + NL +
     "Free to try:"),
    ("Berryhill", "leadership style, measured",
     "Hi Yonitha," + NL +
     "With your SHRM background you will care about the part most "
     "simulations skip: not the result, but how the person got there." + NL +
     "Ours runs 60 minutes across six stages and reports each student "
     "dominant leadership style, blind spots and pressure response." + NL +
     "Free to try:"),
    ("Thaxton", "career readiness at scale",
     "Hi Levi," + NL +
     "The size of UCF makes anything one to one hard, which is usually "
     "where career readiness programs stall." + NL +
     "This runs for a whole cohort in 60 minutes and still gives every "
     "student individual AI feedback on how they made decisions under "
     "pressure." + NL +
     "Free to try:"),
    ("McIntire", "workforce readiness, measured",
     "Hi Molly," + NL +
     "Workforce education gets judged on whether graduates can actually "
     "operate, not on what they covered." + NL +
     "We built a 60 minute simulation that puts students through six stages "
     "of running a business and reports on how each one decided when the "
     "pressure was on." + NL +
     "Free to try:"),
    ("Terrell", "alongside work-based learning",
     "Hi Michelle," + NL +
     "Work-based learning is the strongest thing you offer and it does not "
     "scale to everyone. That is the gap we fill rather than compete with." + NL +
     "A 60 minute simulation a whole cohort can do in one session, with "
     "individual AI feedback on decision-making under pressure." + NL +
     "Free to try:"),
]

COPY += [
    ("Buckley-Burnell", "60 minutes, whole cohort",
     "Hi Addye," + NL +
     "Most things that genuinely build judgement do not scale past a "
     "handful of students. That is the tradeoff you are managing." + NL +
     "We built a 60 minute simulation a whole cohort runs at once. Six "
     "stages, 100+ decisions, and an AI report on each student leadership "
     "style and pressure response." + NL +
     "Free to try:"),
    ("Goldsmith", "what the AI actually reports",
     "Hi Carmen," + NL +
     "Most simulations grade the outcome. Ours reports the process: "
     "dominant leadership style, blind spots, and how the student held up "
     "when the clock was running." + NL +
     "60 minutes, six stages, 100+ decisions, one class session." + NL +
     "Free to try:"),
    ("Mellon", "experiential, in one session",
     "Hi Diane," + NL +
     "Experiential learning is your title, so you know the constraint "
     "better than I do: the formats that work need weeks you do not have." + NL +
     "Ours runs in 60 minutes. Six stages, 100+ decisions, and individual "
     "AI feedback on how each student decided." + NL +
     "Free to try:"),
    ("Kondo", "fits a single class slot",
     "Hi Grace," + NL +
     "Coordinating experiential learning usually means fighting the "
     "calendar. This one needs a single 60 minute slot and nothing from "
     "the faculty member beyond the room." + NL +
     "Students run a venture through six stages and get an AI read on "
     "their decision patterns." + NL +
     "Free to try:"),
    ("Barnes", "no prep, no setup",
     "Hi Nathaniel," + NL +
     "Worth knowing what this costs you to run: 60 minutes, no prep, no "
     "software for students to install, no facilitator script." + NL +
     "They take a venture through six stages, make 100+ decisions, and get "
     "an AI report on how they held up under pressure." + NL +
     "Free to try:"),
    ("Kulawsky", "alongside internships",
     "Hi Alexandra," + NL +
     "Internships are the strongest thing you offer and they do not reach "
     "everyone. That is the gap we fill rather than compete with." + NL +
     "A 60 minute simulation a whole cohort can do in one session, with "
     "individual AI feedback on decision-making under pressure." + NL +
     "Free to try:"),
    ("Douglas", "career development at volume",
     "Hi Adelia," + NL +
     "At USF scale, anything one to one is the bottleneck. That is usually "
     "where career development stalls." + NL +
     "This runs for a whole cohort in 60 minutes and still gives every "
     "student an individual AI report on how they made decisions." + NL +
     "Free to try:"),
    ("Wallace", "individual feedback, group format",
     "Hi Chad," + NL +
     "The usual tradeoff is group format or individual feedback, not both." + NL +
     "A 60 minute simulation runs for the whole room at once, and each "
     "student still gets their own AI read on leadership style, blind "
     "spots and pressure response." + NL +
     "Free to try:"),
    ("Curry", "what employers actually flag",
     "Hi Scott," + NL +
     "You hear the employer feedback first. In our experience it is rarely "
     "technical skill. It is deciding without enough information." + NL +
     "We built a 60 minute simulation for exactly that, with an AI report "
     "on how each student decided rather than what they chose." + NL +
     "Free to try:"),
    ("Senior", "one session, whole cohort",
     "Hi Vivian," + NL +
     "Student engagement at scale usually means choosing between depth and "
     "reach. This does not force that choice." + NL +
     "60 minutes, a whole cohort at once, six stages, and individual AI "
     "feedback for every student on their decision patterns." + NL +
     "Free to try:"),
    ("Velasquez-Paredes", "a curriculum question",
     "Hi Manny," + NL +
     "Career education is a curriculum problem more than a placement one, "
     "which is the harder half." + NL +
     "Our 60 minute simulation drops into one class session and reports "
     "back on how each student reasoned through six stages of running a "
     "venture." + NL +
     "Free to try:"),
    ("Palmer", "the gap employers describe",
     "Hi Patricia," + NL +
     "Industry engagement means you translate employer expectations back "
     "into student preparation. The gap they describe is usually judgement." + NL +
     "A 60 minute simulation, six stages, 100+ decisions, and an AI read "
     "on how each student performs under pressure." + NL +
     "Free to try:"),
    ("Linster", "measuring the soft part",
     "Hi Lauren," + NL +
     "The skills employers complain about are the ones nobody measures: "
     "judgement, pressure, deciding with incomplete information." + NL +
     "We measure them. 60 minutes, six stages, and an individual AI report "
     "per student." + NL +
     "Free to try:"),
    ("Hammon", "does it reflect real pressure",
     "Hi Brooke," + NL +
     "You are close enough to employers to judge whether a simulation "
     "reflects real pressure or just looks like it. I would value that "
     "read." + NL +
     "60 minutes, six stages, 100+ decisions, with an AI report on how the "
     "student decided." + NL +
     "Free to try:"),
]

COPY += [
    ("Ortega", "they can build, can they decide",
     "Hi Christian," + NL +
     "Tech students can build almost anything by the time they finish. The "
     "harder question is whether they can decide with incomplete "
     "information and a clock running." + NL +
     "We compress that into 60 minutes. Six stages, 100+ decisions, and an "
     "AI read on strategic thinking and pressure response." + NL +
     "Free to try:"),
    ("Washington", "validation and the pitch",
     "Hi Moses," + NL +
     "Two of our six stages are market validation and the investor pitch, "
     "which maps onto what your business students already study." + NL +
     "Difference is they do it live in 60 minutes, making 100+ decisions, "
     "and get an AI report on how they reasoned rather than a grade on the "
     "output." + NL +
     "Free to try:"),
    ("Belle", "alongside internships",
     "Hi Kadijah," + NL +
     "Internships are the strongest thing you place students into and they "
     "cannot reach everyone. That is the gap we fill rather than compete "
     "with." + NL +
     "A 60 minute simulation a whole cohort can do in one session, with "
     "individual AI feedback on decision-making under pressure." + NL +
     "Free to try:"),
    ("Malave", "60 minutes, whole cohort",
     "Hi Yvonne," + NL +
     "At MDC scale, anything one to one is the bottleneck. That is usually "
     "where workforce readiness stalls." + NL +
     "This runs for a whole cohort in 60 minutes and still gives every "
     "student an individual AI report on how they made decisions." + NL +
     "Free to try:"),
    ("Dellacamera", "fits one class session",
     "Hi Dana," + NL +
     "The usual blocker for anything experiential is curriculum time. This "
     "runs in 60 minutes, so it fits a single class slot across campuses." + NL +
     "Students take a venture through six stages and get an AI report on "
     "their decision patterns and blind spots." + NL +
     "Free to try:"),
    ("Sincere", "workforce readiness, measured",
     "Hi Maria," + NL +
     "Workforce services gets judged on whether graduates can actually "
     "operate, not on what they covered." + NL +
     "We built a 60 minute simulation that puts students through six "
     "stages of running a business and reports on how each one decided "
     "when the pressure was on." + NL +
     "Free to try:"),
    ("Charles", "what employers keep flagging",
     "Hi Solange," + NL +
     "Quick question. When employers tell you what MDC graduates are "
     "missing, how often is it judgement rather than technical skill?" + NL +
     "That is the gap we built for. A 60 minute simulation, six stages, "
     "and an AI report on how each student decides when the stakes are "
     "high." + NL +
     "Free to try:"),
]

COPY += [
    ("Young-Clark", "validation and the pitch",
     "Hi Iris," + NL +
     "Two of our six stages are market validation and the investor pitch, "
     "which maps onto what your business students already study." + NL +
     "Difference is they do it live in 60 minutes, making 100+ decisions, "
     "and get an AI report on how they reasoned rather than a grade on the "
     "output." + NL +
     "Free to try:"),
    ("Chatman", "how it measures, not what",
     "Hi Vanessa," + NL +
     "With a GCDF background you will judge this on method rather than "
     "marketing, so here is the method." + NL +
     "60 minutes, six stages, 100+ tracked decisions, and a report on each "
     "student dominant leadership style, blind spots and pressure "
     "response." + NL +
     "Free to try:"),
    ("Farmer", "leadership development, measured",
     "Hi Kelvin," + NL +
     "Leadership development is hard to assess because the interesting "
     "part is how someone decides, not what they chose." + NL +
     "That is exactly what our AI reports on, across six stages of running "
     "a venture, in a single 60 minute session." + NL +
     "Free to try:"),
]

COPY += [
    ("Liddell", "60 minutes, whole cohort",
     "Hi Rob," + NL +
     "Most things that genuinely build judgement do not scale past a "
     "handful of students. That is the tradeoff a centre your size keeps "
     "hitting." + NL +
     "We built a 60 minute simulation a whole cohort runs at once, with an "
     "AI report per student on leadership style and pressure response." + NL +
     "Free to try:"),
    ("Lane", "one slot, no prep",
     "Hi Devan," + NL +
     "Worth knowing what this costs you to run: one 60 minute slot, no "
     "prep, nothing for students to install, no facilitator script." + NL +
     "They take a venture through six stages, make 100+ decisions, and get "
     "an AI read on how they held up." + NL +
     "Free to try:"),
    ("Dowling", "experiential, in one session",
     "Hi Tracey," + NL +
     "You sit across career development and experiential learning, which "
     "is where this either fits or does not." + NL +
     "It runs in 60 minutes. Six stages, 100+ decisions, individual AI "
     "feedback on how each student decided rather than what they chose." + NL +
     "Free to try:"),
    ("Leung", "they can build, can they decide",
     "Hi Tiffany," + NL +
     "Engineering students can build almost anything by the time they "
     "finish. The harder question is deciding with incomplete information "
     "and a clock running." + NL +
     "We compress that into 60 minutes across six stages, with an AI read "
     "on strategic thinking and pressure response." + NL +
     "Free to try:"),
    ("James", "what recruiters flag",
     "Hi Jelani," + NL +
     "You cover science and technology majors and sit on employer "
     "relations, so you hear the gap twice. It is rarely technical." + NL +
     "A 60 minute simulation, six stages, 100+ decisions, and an AI report "
     "on how each student decides when the stakes are high." + NL +
     "Free to try:"),
    ("Hattaway", "validation and the pitch",
     "Hi Caitlin," + NL +
     "Two of our six stages are market validation and the investor pitch, "
     "which sits close to what your communication students already do." + NL +
     "Difference is they do it live in 60 minutes and get an AI report on "
     "how they reasoned rather than a grade on the output." + NL +
     "Free to try:"),
    ("Mantzanas", "the gap employers describe",
     "Hi Christy," + NL +
     "On the employer relations side you see the gap between what "
     "employers expect and what graduates actually do. Usually it is "
     "judgement." + NL +
     "We measure it. 60 minutes, six stages, and an individual AI report "
     "per student." + NL +
     "Free to try:"),
    ("Foster", "measuring the soft part",
     "Hi Cassi," + NL +
     "The skills employers complain about are the ones nobody measures: "
     "judgement, pressure, deciding without enough information." + NL +
     "Ours reports on exactly those, across six stages of running a "
     "venture, in a single 60 minute session." + NL +
     "Free to try:"),
]

COPY += [
    ("Lopez Vergara", "alongside internships and co-ops",
     "Hi Cristina," + NL +
     "Internships and co-ops are the strongest thing you place students "
     "into, and they cannot reach everyone. That is the gap we fill rather "
     "than compete with." + NL +
     "A 60 minute simulation a whole cohort can do in one session, with "
     "individual AI feedback on decision-making under pressure." + NL +
     "Free to try:"),
]

COPY += [
    ("Klein", "one session, three departments",
     "Hi Richard," + NL +
     "Anything adopted at college level has to justify itself to very "
     "different departments. This does." + NL +
     "The same 60 minute simulation reads as decision-making to "
     "information systems, as leadership to management, and as validation "
     "and pitching to marketing, because the six stages cover all three." + NL +
     "Free to try:"),
    ("Koulamas", "they can build, can they decide",
     "Hi Christos," + NL +
     "MIS students can build almost anything by the time they graduate. "
     "The harder question is whether they can decide with incomplete "
     "information and a clock running." + NL +
     "We compress that into 60 minutes. Six stages, 100+ decisions, and an "
     "AI read on strategic thinking under pressure." + NL +
     "Free to try:"),
    ("Sengupta", "evidence for assurance of learning",
     "Hi Arijit," + NL +
     "Accreditation needs evidence that students can actually reason, "
     "which is the hardest thing to document." + NL +
     "Each run produces a per-student record: 100+ tracked decisions "
     "across six stages, with an AI read on how they decided. One 60 "
     "minute session." + NL +
     "Free to try:"),
    ("Weinstein", "leadership style, measured",
     "Hi Marc," + NL +
     "Your HR students study leadership style. Measuring their own is "
     "harder, because the interesting part is how someone decides, not "
     "what they chose." + NL +
     "That is exactly what our AI reports on, across six stages, in one 60 "
     "minute session." + NL +
     "Free to try:"),
    ("Hiller", "how it measures, not what",
     "Hi Nathan," + NL +
     "Leadership assessment is your field, so you will judge this on "
     "method. Here is the method." + NL +
     "60 minutes, six stages, 100+ tracked decisions, and a report on each "
     "participant dominant style, blind spots and pressure response." + NL +
     "Free to try:"),
    ("Taylor", "validation and the pitch",
     "Hi Kimberly," + NL +
     "Two of our six stages are market validation and the investor pitch, "
     "which maps onto what your department already teaches." + NL +
     "Difference is students do it live in 60 minutes, making 100+ "
     "decisions, and get an AI report on how they reasoned rather than a "
     "grade on the output." + NL +
     "Free to try:"),
    ("Tanenbaum", "fits an existing module",
     "Hi Jaclyn," + NL +
     "The practical question for a programme director is whether something "
     "fits without rebuilding a module. This runs in 60 minutes, so it "
     "drops into one class session." + NL +
     "Six stages, 100+ decisions, individual AI feedback per student." + NL +
     "Free to try:"),
    ("Pietraszek", "a venture lifecycle in 60 minutes",
     "Hi Anna," + NL +
     "Ideation, vision, validation, growth, scale, investor pitch. That is "
     "a venture lifecycle, and your students run all six in 60 minutes." + NL +
     "The AI reports on how each one decided under pressure rather than "
     "scoring the final pitch." + NL +
     "Free to try:"),
]

FOLLOWUP = ("Hi {first}," + NL +
            "No worries if the timing is wrong. If it helps, the trial takes "
            "60 minutes and needs nothing from your team." + NL +
            "Worth a look for next semester planning?")

BOOKING_REPLY = ("Hi {first}," + NL +
                 "Glad it was useful. Happy to walk through what a cohort "
                 "pilot would look like for {company}." + NL +
                 "Grab 30 minutes whenever suits:" + "\n" + CALENDLY)

HEADERS = ["first_name", "last_name", "email", "title", "institution",
           "department", "subject", "email_1", "email_2_followup",
           "reply_when_interested", "words_email_1", "gate", "why_them",
           "source"]


async def main():
    state = StateManager()
    prospects = {p.last_name: p
                 for p in await state.get_prospects_by_status("new")}

    rows, failures = [], []
    for last, subject, body in COPY:
        p = prospects.get(last)
        if not p:
            failures.append(last + ": not in prospect table")
            continue
        full = body + " " + TRIAL
        check = pre_send_check(p.email, subject, full, prospect=p,
                               allow_risky=True)
        if not check.ok:
            failures.append(last + ": " + "; ".join(check.reasons))
        rows.append({
            "first_name": p.first_name,
            "last_name": p.last_name,
            "email": p.email,
            "title": p.title,
            "institution": p.company,
            "department": p.department,
            "subject": subject,
            "email_1": full,
            "email_2_followup": FOLLOWUP.format(first=p.first_name),
            "reply_when_interested": BOOKING_REPLY.format(
                first=p.first_name, company=p.company),
            "words_email_1": len(full.split()),
            "gate": "pass" if check.ok else "FAIL",
            "why_them": p.personalization_notes,
            "source": p.source_url,
        })

    os.makedirs("outreach", exist_ok=True)
    with open("outreach/warroom_outreach.csv", "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)
    build_xlsx(rows)

    print("  " + str(len(rows)) + " rows written")
    print("  longest email 1: " +
          str(max(r["words_email_1"] for r in rows)) + " words")
    print("  gate failures: " + str(len(failures)))
    for f_ in failures:
        print("    ! " + f_)


def build_xlsx(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Outreach"
    ws.append([h.replace("_", " ") for h in HEADERS])

    head_fill = PatternFill("solid", fgColor="1F3864")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.alignment = Alignment(vertical="center")
        c.fill = head_fill
    ws.freeze_panes = "A2"

    for r in rows:
        ws.append([r[h] for h in HEADERS])

    widths = {"first_name": 12, "last_name": 17, "email": 31, "title": 34,
              "institution": 28, "department": 26, "subject": 26,
              "email_1": 64, "email_2_followup": 40,
              "reply_when_interested": 44, "words_email_1": 9, "gate": 9,
              "why_them": 46, "source": 40}
    for i, h in enumerate(HEADERS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 18)

    wrap = Alignment(wrap_text=True, vertical="top")
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = wrap
        ws.row_dimensions[row[0].row].height = 160
    wb.save("outreach/warroom_outreach.xlsx")


if __name__ == "__main__":
    asyncio.run(main())
