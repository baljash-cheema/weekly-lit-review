"""
educate.py — Writes the weekly lesson: one topic the reader should understand better,
chosen from this week's literature and taught from the fundamentals up.

Runs after curate.py. Three steps:
  1. Claude reads this week's review and picks the topic (never one already taught),
     and writes the PubMed searches needed to ground a lesson on it.
  2. Those searches are run against all of PubMed (no date limit) for the reviews,
     guidelines and landmark trials on the topic.
  3. Claude writes the lesson from those sources.

Reads   data/candidates.json, reports/review_*.md   (this week's papers and review)
        education/history.json                      (topics already taught)
Writes  reports/lesson_YYYY-MM-DD.md                (the lesson)
        education/history.json                      (adds this week's topic)

Safeguards against invented content:
  * Every citation, key trial and further-reading entry must be a PubMed record that
    the searches actually returned; anything else is removed and noted.
  * Titles, authors, journals, years and links come from PubMed, never from Claude.
  * Numbers in the Key Trials block are checked against each trial's abstract.
  * The teaching prose draws on the model's own knowledge as well as the sources, and
    the lesson says so.

Usage:
    python educate.py           # needs ANTHROPIC_API_KEY (environment or .env)
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from collect import EXCLUDE_TYPES, PUBMED_BASE, get, pubmed_fetch
from curate import ABSTRACT_CHARS, Curator, load_config, unsupported_numbers

BASE_DIR = Path(__file__).parent
CANDIDATES_PATH = BASE_DIR / "data" / "candidates.json"
REPORTS_DIR = BASE_DIR / "reports"
HISTORY_PATH = BASE_DIR / "education" / "history.json"

TOPIC_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "why_now": {"type": "string"},
        "trigger_pmids": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "searches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"purpose": {"type": "string"}, "query": {"type": "string"}},
                "required": ["purpose", "query"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topic", "why_now", "trigger_pmids", "questions", "searches"],
    "additionalProperties": False,
}

LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "bottom_line": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                    "table_columns": {"type": "array", "items": {"type": "string"}},
                    "table_rows": {"type": "array",
                                   "items": {"type": "array", "items": {"type": "string"}}},
                },
                "required": ["heading", "paragraphs", "table_columns", "table_rows"],
                "additionalProperties": False,
            },
        },
        "key_trials": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pmid": {"type": "string"},
                    "name": {"type": "string"},
                    "design": {"type": "string"},
                    "results": {"type": "string"},
                    "takeaway": {"type": "string"},
                },
                "required": ["pmid", "name", "design", "results", "takeaway"],
                "additionalProperties": False,
            },
        },
        "take_home": {"type": "array", "items": {"type": "string"}},
        "further_reading": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"pmid": {"type": "string"}, "why": {"type": "string"}},
                "required": ["pmid", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "bottom_line", "sections", "key_trials", "take_home",
                 "further_reading"],
    "additionalProperties": False,
}

TOPIC_SYSTEM = """You plan the continuing education of one physician. Each week, \
alongside his literature review, he receives a single lesson on a topic he should \
understand better. Your job in this request is to choose this week's topic and to write \
the PubMed searches that will gather the sources for it. A separate request writes the \
lesson.

<reader>
{reader_profile}
</reader>

<what_he_asked_for>
{guidance}
</what_he_asked_for>

How to choose. Read this week's review and the other papers published this week, and ask \
what a sharp clinician in his position would need to already understand to read those \
papers well, and probably does not: a drug class or technology that arrived after his \
training, a field next to his own that is moving fast, a body of trials whose story he \
has only seen in pieces, a method (statistical, genetic, computational) that the papers \
lean on. The best topic is one where a week's paper is the visible tip of something \
larger that is worth an organized twenty minutes. Rotate across his interests over the \
weeks (general medicine, cardiology, AI in medicine, AI in medical education, longevity) \
rather than staying in one, and look at the list of past lessons to see where the \
balance has been.

Scope it like a good grand rounds: narrow enough to teach properly in about 1,800 words, \
broad enough to be a subject and not a single paper. "RNA-targeted therapeutics: \
antisense oligonucleotides and siRNA" is the right size; "genetics" is too broad and \
"the phase 3 trial of drug X" is too narrow. Do not choose something at the center of \
his own daily practice, such as routine guideline-directed therapy for heart failure, \
unless the ground under it has genuinely shifted.

Never repeat a past lesson. A neighboring topic is fine when it is clearly a different \
lesson. If he has asked for specific topics and one is still untaught, prefer it when it \
connects to this week's papers, and use one in a week when nothing in the literature \
suggests a better topic.

Fields:
- topic: the lesson's subject as a short title, 12 words at most.
- why_now: two or three sentences, 60 words at most, addressed to him as "you", saying \
which of this week's papers raised the topic and what gap the lesson fills.
- trigger_pmids: the PMIDs of the papers from this week that prompted the topic, at most \
three, taken only from the PMIDs shown to you. Empty if the topic came from his list.
- questions: five to eight questions the lesson should answer, in teaching order, \
starting from the fundamentals (what is it, how does it work), through why it matters \
for patients, what the trials show, what is available now, to the caveats and what is \
coming.
- searches: {max_searches} PubMed searches, or fewer, that together retrieve the sources \
a careful teacher would want: the landmark trials (search them by trial name or drug \
name), authoritative recent reviews, guidelines and scientific statements, the \
foundational mechanism papers, and the newest developments. Each search returns only its \
top {per_search} results by PubMed's best-match ranking, so make each one specific. Use \
ordinary PubMed syntax with field tags such as [tiab], [ti], [pt] and [dp]. Keep each \
query simple enough to be sure it runs; an over-constrained query that returns nothing \
is wasted. Today is {today}; your own knowledge stops earlier, so make at least two of \
the searches catch recent work you may not know about, for example by adding \
AND ("{recent_year}"[dp] : "3000"[dp]). In purpose, say in a few words what the search \
is for."""

LESSON_SYSTEM = """You are a physician-educator writing a weekly lesson for one \
colleague. He is the learner here: on this topic, assume the intelligence and clinical \
experience of a senior academic physician but not the background knowledge. Teach it the \
way the best grand rounds speaker would, building from first principles to the current \
frontier, so that at the end he could explain the subject to a fellow and read the next \
paper on it critically.

<reader>
{reader_profile}
</reader>

<what_he_asked_for>
{guidance}
</what_he_asked_for>

The request gives you the topic, why it was chosen, the questions the lesson should \
answer, and a set of PubMed records (title, journal, year, abstract) retrieved for it. \
Today is {today}.

Write a lesson of roughly 1,500 to 2,000 words in total across all fields. Fill in:

- title: the lesson's title, 12 words at most. No "Weekly Lesson:" prefix.
- bottom_line: three sentences at most, 70 words at most. If he reads nothing else, what \
should he now know?
- sections: four to seven sections that carry the teaching, in an order that builds. \
Choose headings that fit the topic rather than a fixed template; phrasing a heading as \
the question it answers often works. Between them the sections should cover: the \
fundamentals, defined plainly (what the thing is and how it works, with the mechanism \
explained rather than named); why it matters for patients, especially his; how the \
evidence unfolded, told as a story with the negative and neutral trials given the same \
attention as the positive ones; what is actually available or in use now; the caveats, \
controversies and common misreadings; and what is coming next. Each section has one to \
four paragraphs of prose. Define every term of art the first time it appears. Use an \
analogy when it truly clarifies a mechanism, not for decoration.
- table_columns and table_rows: a section may carry one table when the content is \
naturally a comparison, such as the agents in a class with their target, route, \
indication and status. Use at most two tables in the lesson and at most five columns; \
keep cells short. Leave both arrays empty for a section with no table.
- key_trials: the three to six studies from the supplied records that he most needs to \
know, in the order that tells the story. For each give pmid; name (the trial's acronym \
or a short label); design (one sentence, 40 words at most: population, comparison, \
primary endpoint); results (one or two sentences, 60 words at most, with the primary \
result and its numbers); takeaway (one sentence, 30 words at most, on what it \
established). Design and results must use only what that record's abstract states, and \
every number in them must appear in the abstract exactly as written there; they are \
checked automatically, so do not round, convert or compute. If a trial that belongs in \
the story is not among the supplied records, discuss it in the sections instead.
- take_home: three to five single-sentence points to carry into practice or into \
reading the next paper.
- further_reading: two to four of the supplied records worth reading in full, review \
articles or guidelines where possible, each with a clause in why saying what it offers.

Sources and honesty. The supplied records are abstracts only. You may and should draw on \
your own knowledge to teach, but he will act on what you write, so:
- When a statement rests on one of the supplied records, cite it by putting its PMID in \
square brackets right after the statement, in exactly this form: [PMID 12345678], or \
[PMID 12345678; PMID 23456789] for two. Cite only PMIDs from the supplied records. \
Citations belong in the sections; do not put them in the title, bottom_line or key_trials.
- Where a supplied record is more recent than, or conflicts with, what you remember, \
trust the record and say what changed.
- Name a trial, a drug or a guideline recommendation from memory only when you are \
confident of it. When you are less sure, describe it in general terms. Never invent a \
trial name, a number or a regulatory status.
- Regulatory approvals and guideline positions change. State them as of what the sources \
or your knowledge support, and where your knowledge may be out of date, say so in the \
sentence.
- Say plainly what is unknown or contested. A negative trial is a finding, not a failure \
to be explained away.

Write in plain, direct prose, as one colleague teaching another, in American English. \
No hype and no filler. No markdown inside the fields: no asterisks, headings, bullets or \
pipes."""


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def latest(pattern):
    files = sorted(REPORTS_DIR.glob(pattern))
    return files[-1] if files else None


def load_history():
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    return []


def topic_request(review_md, data, sections, history, requested):
    review = review_md.split("## Verification Notes")[0].strip() if review_md else ""
    selected = set(re.findall(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", review))
    lines = ["<this_weeks_review>", review or "(No review was produced this week.)",
             "</this_weeks_review>", "",
             "<selected_paper_pmids>", ", ".join(sorted(selected)) or "none",
             "</selected_paper_pmids>", "",
             "<other_papers_this_week>",
             "Published this week but not selected for the review. Titles only."]
    for s in sections:
        arts = [a for a in data["sections"].get(s["key"], []) if a["pmid"] not in selected]
        if arts:
            lines.append(f"{s['title']}:")
            lines += [f"- PMID {a['pmid']}: {a['title']} ({a['journal']})" for a in arts[:25]]
    lines += ["</other_papers_this_week>", "", "<past_lessons>"]
    lines += [f"- {h['date']}: {h['topic']}" for h in history] or ["None yet; this is the first lesson."]
    lines += ["</past_lessons>", "", "<topics_he_has_asked_for>"]
    lines += [f"- {t}" for t in requested] or ["None listed."]
    lines += ["</topics_he_has_asked_for>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

def search_all_years(query, retmax):
    """Top PMIDs for a query by PubMed's best-match ranking, with no date limit."""
    result = get(f"{PUBMED_BASE}/esearch.fcgi",
                 {"db": "pubmed", "retmode": "json", "retmax": retmax, "term": query,
                  "sort": "relevance"}).json().get("esearchresult", {})
    if "ERROR" in result:
        raise RuntimeError(result["ERROR"])
    time.sleep(0.4)
    return result.get("idlist", [])


def gather_sources(plan, known, per_search, max_sources, notes):
    """Run the planned searches and return the records, the week's trigger papers first."""
    hits = []
    for s in plan["searches"]:
        try:
            ids = search_all_years(" ".join(s["query"].split()), per_search)
            print(f"  {len(ids):2d}  {s['purpose']}")
            if not ids:
                notes.append(f"A background search returned nothing ({s['purpose']}).")
            hits.append(ids)
        except Exception as e:
            print(f"  FAILED  {s['purpose']}: {e}")
            notes.append(f"A background search failed ({s['purpose']}).")

    # Take results in turn from each search so every search is represented under the cap.
    order = [p for p in plan["trigger_pmids"] if p in known]
    for rank in range(per_search):
        for ids in hits:
            if rank < len(ids) and ids[rank] not in order:
                order.append(ids[rank])
    order = order[:max_sources]

    need = [p for p in order if p not in known]
    fetched = {a["pmid"]: a for a in pubmed_fetch(need)} if need else {}
    sources = []
    for p in order:
        a = known.get(p) or fetched.get(p)
        if a and a["abstract"] and not (set(a["pub_types"]) & EXCLUDE_TYPES):
            sources.append(a)
    return sources


def source_block(a):
    abstract = a["abstract"]
    if len(abstract) > ABSTRACT_CHARS:
        abstract = abstract[:ABSTRACT_CHARS] + " [abstract cut off here]"
    types = ", ".join(t for t in a["pub_types"]
                      if t != "Journal Article" and not t.startswith("Research Support"))
    return (f'<record pmid="{a["pmid"]}">\n'
            f"Title: {a['title']}\nJournal: {a['journal']}\nYear: {a.get('year') or 'not given'}\n"
            f"Authors: {a['authors']}\n"
            + (f"PubMed publication types: {types}\n" if types else "")
            + f"Abstract: {abstract}\n</record>")


def lesson_request(plan, sources, triggers):
    lines = [f"Topic: {plan['topic']}", "", "Why it was chosen: " + tidy(plan["why_now"]), ""]
    if triggers:
        lines += ["Papers from this week's review that prompted it (also among the records):"]
        lines += [f"- PMID {a['pmid']}: {a['title']}" for a in triggers] + [""]
    lines += ["Questions the lesson should answer:"]
    lines += [f"- {tidy(q)}" for q in plan["questions"]]
    lines += ["", f'<records count="{len(sources)}">']
    lines += [source_block(a) for a in sources]
    lines += ["</records>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

CITE_RE = re.compile(r"\s*\[[^\[\]]*\bPMIDs?\b[^\[\]]*\]", re.I)
BARE_PMID_RE = re.compile(r"\s*\(?\bPMIDs?:?\s*\d{6,8}(?:\s*[,;]\s*(?:PMID:?\s*)?\d{6,8})*\)?", re.I)


def tidy(text):
    return " ".join(str(text).split())


class References:
    """Turns [PMID n] markers into numbered links and keeps the reference list."""

    def __init__(self, sources, notes):
        self.sources = {a["pmid"]: a for a in sources}
        self.order = []
        self.notes = notes
        self.dropped = set()

    def number(self, pmid):
        if pmid not in self.order:
            self.order.append(pmid)
        return self.order.index(pmid) + 1

    def _swap(self, match):
        links = []
        for pmid in re.findall(r"\d{6,8}", match.group(0)):
            if pmid in self.sources:
                link = f"[[{self.number(pmid)}]]({self.sources[pmid]['url']})"
                if link not in links:
                    links.append(link)
            elif pmid not in self.dropped:
                self.dropped.add(pmid)
                self.notes.append(f"Removed a citation to PMID {pmid}, which was not among "
                                  "the retrieved sources.")
        return (" " + "".join(links)) if links else ""

    def cite(self, text):
        return BARE_PMID_RE.sub("", CITE_RE.sub(self._swap, tidy(text))).strip()

    def plain(self, text):
        """For fields that should carry no citations at all."""
        return BARE_PMID_RE.sub("", CITE_RE.sub("", tidy(text))).strip()

    def listing(self):
        out = []
        for i, pmid in enumerate(self.order, 1):
            a = self.sources[pmid]
            year = f" {a['year']}." if a.get("year") else ""
            out.append(f"{i}. {a['authors'].rstrip('.')}. [{a['title'].rstrip('.')}]({a['url']}). "
                       f"*{a['journal']}*.{year}")
        return out


def cell(text):
    return text.replace("|", "/")


def render_lesson(lesson, plan, sources, triggers, notes, model_label, day):
    refs = References(sources, notes)
    by_pmid = refs.sources

    out = [f"# Weekly Lesson: {refs.plain(lesson['title'])}", "", "", "", "## Why this topic", "",
           refs.plain(plan["why_now"]), ""]
    if triggers:
        out += ["Prompted by this week's: " + "; ".join(
            f"[{a['title'].rstrip('.')}]({a['url']}) (*{a['journal']}*)" for a in triggers), ""]
    out += ["## The bottom line", "", refs.plain(lesson["bottom_line"]), ""]

    for s in lesson["sections"]:
        out += [f"## {refs.plain(s['heading'])}", ""]
        for p in s["paragraphs"]:
            if tidy(p):
                out += [refs.cite(p), ""]
        cols = [tidy(c) for c in s["table_columns"] if tidy(c)]
        rows = [r for r in s["table_rows"] if any(tidy(c) for c in r)]
        if cols and rows:
            out += ["| " + " | ".join(cell(refs.plain(c)) for c in cols) + " |",
                    "|" + "---|" * len(cols)]
            for r in rows:
                r = (list(r) + [""] * len(cols))[:len(cols)]
                out.append("| " + " | ".join(cell(refs.cite(c)) for c in r) + " |")
            out.append("")

    trials = []
    for t in lesson["key_trials"]:
        pmid = re.sub(r"\D", "", t["pmid"])
        if pmid not in by_pmid:
            notes.append(f"Dropped a key trial citing PMID {t['pmid']!r}, which was not among "
                         "the retrieved sources.")
            continue
        if pmid not in [x[0] for x in trials]:
            trials.append((pmid, t))
    if trials:
        out += ["## The key trials", ""]
        for pmid, t in trials:
            a = by_pmid[pmid]
            refs.number(pmid)
            design, results = refs.plain(t["design"]), refs.plain(t["results"])
            bad = unsupported_numbers(f"{design} {results}", a["abstract"])
            if bad:
                notes.append(f"Key trial {refs.plain(t['name'])} (PMID {pmid}): the summary cites "
                             f"{', '.join(bad)}, not found verbatim in the PubMed abstract. "
                             "Check against the paper.")
            year = f" · {a['year']}" if a.get("year") else ""
            out += [f"### [{refs.plain(t['name'])}]({a['url']})",
                    f"*{a['title'].rstrip('.')}. {a['journal']}{year} · {a['authors']}*", "",
                    f"**Design:** {design}", "", f"**Results:** {results}", "",
                    f"**Takeaway:** {refs.plain(t['takeaway'])}", ""]

    points = [refs.plain(p) for p in lesson["take_home"] if tidy(p)]
    if points:
        out += ["## Take-home points", ""] + [f"- {p}" for p in points] + [""]

    reading = []
    for r in lesson["further_reading"]:
        pmid = re.sub(r"\D", "", r["pmid"])
        if pmid in by_pmid and pmid not in [x[0] for x in reading]:
            reading.append((pmid, r))
    if reading:
        out += ["## Further reading", ""]
        for pmid, r in reading:
            a = by_pmid[pmid]
            year = f", {a['year']}" if a.get("year") else ""
            out.append(f"- [{a['title'].rstrip('.')}]({a['url']}) (*{a['journal']}*{year}). "
                       f"{refs.plain(r['why'])}")
        out.append("")

    listing = refs.listing()
    if listing:
        out += ["## References", ""] + listing + [""]

    body = "\n".join(out)
    words = len(re.sub(r"\]\([^)]*\)", "]", body).split())
    out[2] = f"**{datetime.strptime(day, '%Y-%m-%d'):%B %-d, %Y}** · about a {max(1, round(words / 230))}-minute read"

    out += ["## About this lesson", "",
            f"- The topic was chosen by {model_label} from this week's review and the list of "
            "past lessons. " + f"{len(sources)} PubMed records were retrieved for it.",
            "- Every reference, key trial and further-reading entry is a PubMed record that the "
            "searches returned; titles, authors, journals and years come from PubMed. Numbers in "
            "The Key Trials are checked automatically against each abstract.",
            "- The teaching text draws on the model's own knowledge as well as these abstracts "
            "and is not machine-verified. Confirm drug approvals, doses and guideline positions "
            "against a current source before acting on them."]
    out += [f"- {n}" for n in notes] or []
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def educate():
    config = load_config()
    edu = config.get("education") or {}
    if not CANDIDATES_PATH.exists():
        sys.exit("No data/candidates.json — run collect.py first.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set (environment or .env).")

    data = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    known = {a["pmid"]: a for arts in data["sections"].values() for a in arts}
    review_path = latest("review_*.md")
    review_md = review_path.read_text(encoding="utf-8") if review_path else ""
    history = load_history()
    today = datetime.now()
    day = f"{today:%Y-%m-%d}"
    per_search = edu.get("per_search", 10)
    profile = " ".join(config.get("reader_profile", "").split())
    guidance = " ".join(edu.get("guidance", "").split())
    notes = []

    curator = Curator(config)

    print("=== Choosing the topic ===")
    plan = json.loads(curator.ask(
        TOPIC_SYSTEM.format(reader_profile=profile, guidance=guidance,
                            max_searches=edu.get("max_searches", 8), per_search=per_search,
                            today=f"{today:%B %-d, %Y}", recent_year=today.year - 1),
        topic_request(review_md, data, config["sections"], history,
                      edu.get("requested_topics") or []),
        TOPIC_SCHEMA))
    plan["searches"] = plan["searches"][:edu.get("max_searches", 8)]
    plan["trigger_pmids"] = [p for p in (re.sub(r"\D", "", x) for x in plan["trigger_pmids"])
                             if p in known][:3]
    print(f"Topic: {plan['topic']}")

    print("=== Gathering sources from PubMed ===")
    sources = gather_sources(plan, known, per_search, edu.get("max_sources", 50), notes)
    print(f"{len(sources)} records")
    if len(sources) < 5:
        notes.append(f"Only {len(sources)} PubMed records could be retrieved, so this lesson "
                     "rests more than usual on the model's own knowledge.")
    have = {a["pmid"] for a in sources}
    triggers = [known[p] for p in plan["trigger_pmids"] if p in have]

    print("=== Writing the lesson ===")
    lesson = json.loads(curator.ask(
        LESSON_SYSTEM.format(reader_profile=profile, guidance=guidance,
                             today=f"{today:%B %-d, %Y}"),
        lesson_request(plan, sources, triggers), LESSON_SCHEMA))

    model_label = ", ".join(sorted(curator.models_used)) or curator.model
    text = render_lesson(lesson, plan, sources, triggers, notes, model_label, day)

    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"lesson_{day}.md"
    path.write_text(text, encoding="utf-8")

    # Remember the topic so it is not taught again. The workflow commits this file.
    history = [h for h in history if h["date"] != day]
    history.append({"date": day, "topic": tidy(plan["topic"]), "title": tidy(lesson["title"])})
    HISTORY_PATH.parent.mkdir(exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=1, ensure_ascii=False) + "\n",
                            encoding="utf-8")

    print(f"\nwrote {path.relative_to(BASE_DIR)}")
    print(f"API usage: {curator.usage['input']:,} input + {curator.usage['output']:,} output tokens")
    for n in notes:
        print(f"NOTE: {n}")
    return path


if __name__ == "__main__":
    educate()
