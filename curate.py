"""
curate.py — Asks Claude (via the API) to pick and write up the week's most important
studies in each section, then assembles the report.

Reads   data/candidates.json            (written by collect.py)
Writes  reports/review_YYYY-MM-DD.md

Safeguards against invented content:
  * Claude may only choose PMIDs from the candidate list; anything else is dropped.
  * Titles, authors, journals and links are filled in from PubMed data, never by Claude.
  * Every number in a Design or Results line is checked against the abstract; mismatches are
    listed under Verification Notes.

Usage:
    python curate.py            # needs ANTHROPIC_API_KEY (environment or .env)
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

CANDIDATES_PATH = BASE_DIR / "data" / "candidates.json"
REPORTS_DIR = BASE_DIR / "reports"

# Sections are curated in this order so each paper lands in its most specific home;
# the report itself is printed in the order sections appear in config.yaml.
CURATE_ORDER = ["cardiology", "ai_medicine", "ai_meded", "longevity", "general_medicine"]

ABSTRACT_CHARS = 3500

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "landscape": {"type": "string"},
        "studies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pmid": {"type": "string"},
                    "depth": {"type": "string", "enum": ["full", "brief"]},
                    "area": {"type": "string"},
                    "study_type": {"type": "string"},
                    "design": {"type": "string"},
                    "results": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["pmid", "depth", "area", "study_type", "design", "results", "context"],
                "additionalProperties": False,
            },
        },
        "also_notable": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"pmid": {"type": "string"}, "note": {"type": "string"}},
                "required": ["pmid", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["landscape", "studies", "also_notable"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are the editor of a weekly literature review written for one physician.

<reader>
{reader_profile}
</reader>

Each request gives you one section of the review and the candidate papers PubMed added \
this week for it, already filtered and roughly ranked by journal and study design. That \
ranking is mechanical; your judgment about importance overrides it.

Your job is to choose what genuinely matters and say why. The reader wants the most \
important studies, not a fixed quota. Select at most {max_studies}. That number is a \
ceiling for an exceptional week, not a target: an ordinary week has two to four studies \
worth his time in a busy field and one or two in a thin one, and an empty list is the \
right answer when nothing clears the bar. Prefer primary research that changes practice \
or understanding: randomized trials, landmark cohorts, guidelines, first-in-class \
results, and rigorous negative trials. Secondary analyses, narrative reviews and small \
single-center studies rarely belong unless they answer a question the field has been \
asking.

Each selected study gets one of two treatments, and choosing between them is part of \
your editorial judgment.

depth "full" is for studies that are both rigorous and consequential: the ones he would \
want to be able to discuss in detail. He reads trials critically and wants the actual \
data, so be specific and quantitative. Fill in:

- area: the clinical disease area or the technology under study, in a short phrase.
- study_type: the design in a few words with total size, such as "Double-blind RCT, \
8,350 patients".
- design: one or two sentences, 50 words at most, on who was studied, what was compared \
(with doses or targets), the primary endpoint, and follow-up duration.
- results: two to four sentences, 100 words at most. Give the primary endpoint with event \
rates or absolute values in each arm, the effect estimate with its confidence interval \
or P value, then the secondary, subgroup or safety results that matter for \
interpretation, each with its numbers. Prefer numbers to adjectives.
- context: two or three sentences, 65 words at most, placing the study against what was \
already known and saying how it should change his thinking. Choose the two or three \
points that matter most from: the question it settles or reopens, the earlier trials or \
guideline positions it confirms or contradicts, its main limitation, and what it means \
for practice.

depth "brief" is for studies worth knowing about whose quality or impact is more \
modest: a small or single-center trial, an observational or preclinical finding, a \
secondary analysis, a survey, an early-phase result. Fill in area and study_type as \
above, put a streamlined summary in results (one or two sentences, 45 words at most, \
saying what was done, the headline number, and the caveat that keeps it from being a \
bigger deal), and leave design and context as empty strings.

Rules for design and results at either depth: use only what the abstract states. Every \
number you give must appear in the abstract exactly as written there; your numbers are \
checked automatically against it, so do not round, convert units or compute differences \
yourself. If the abstract provided is cut off before the results, say what was tested \
and that the results were not available in the abstract rather than supplying them from \
memory.

The context field is the one place that draws on your own knowledge of the literature. \
Name an earlier trial only when you are confident of its name and of what it showed; \
when you are less sure, describe the prior evidence in general terms. Wrong context is \
worse than general context. Do not restate the results there.

landscape: two or three sentences, 70 words at most, opening the section, on what this \
week's papers taken together say about where the field is heading. Write it for someone \
who will read only this paragraph. If nothing was selected, say in a sentence that it \
was a quiet week and what kind of work was published instead.

also_notable: up to {max_notable} further papers worth one line each, only for weeks with \
more genuinely important work than fits above, such as a major conference week. Each note \
is a single sentence of 25 words at most giving the result. Leave the list empty in an \
ordinary week, and never use it for papers that simply did not make the cut.

A request may include a section_format block that changes these instructions for that \
section; where it does, follow it.

Write in plain, direct clinical prose, as one colleague briefing another, in American \
English. No hype, no filler, no markdown formatting inside the fields. The word limits \
are firm. The pmid fields must hold PMIDs from the candidate list; do not write PMIDs \
anywhere in the prose, and refer to studies there by drug, trial name or topic."""

THEMES_FORMAT = """This section is a themes digest, not a set of detailed write-ups. The \
reader wants to know what is happening across medicine without the trial-level detail.

landscape: this is the heart of the section. Write up to 160 words organized around the \
two to four major themes in this week's important papers, naming the studies that carry \
each theme and saying in a clause what each showed. Qualitative is fine here; give a \
number only where it is the point.

studies: mark every one depth "brief". In results give a single sentence, 30 words at \
most, saying what the study is and its headline result. Leave design and context empty. \
Leave also_notable empty."""

OVERVIEW_PROMPT = """Below is this week's finished literature review. Write the short \
opening that goes above it: three or four sentences, 100 words at most, telling the \
reader what mattered most this week and why, leading with the single most important \
development. It is a headline, not a summary of every section. Mention only studies that appear in the review, and add no numbers that are \
not in it. Plain prose in one paragraph, no heading, no markdown.

<review>
{review}
</review>"""


def load_config():
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


class Curator:
    def __init__(self, config):
        self.client = anthropic.Anthropic()
        self.model = config.get("model", "claude-opus-5-5")
        self.effort = config.get("effort", "high")
        self.usage = {"input": 0, "output": 0}
        self.models_used = set()

    def ask(self, system, user, schema=None):
        """One streamed request. Returns the text of the reply (JSON text if schema given)."""
        output_config = {"effort": self.effort}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=32000,
            thinking={"type": "adaptive"},
            output_config=output_config,
            # If a safety classifier declines a request (medical abstracts occasionally
            # trip one), the API retries it on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            message = stream.get_final_message()

        self.usage["input"] += message.usage.input_tokens
        self.usage["output"] += message.usage.output_tokens
        self.models_used.add(message.model)
        if message.stop_reason == "refusal":
            raise RuntimeError("the request was declined by the model")
        if message.stop_reason == "max_tokens":
            raise RuntimeError("the reply was cut off (max_tokens)")
        return "".join(b.text for b in message.content if b.type == "text")


def candidate_block(a):
    abstract = a["abstract"]
    if len(abstract) > ABSTRACT_CHARS:
        abstract = abstract[:ABSTRACT_CHARS] + " [abstract cut off here]"
    types = ", ".join(t for t in a["pub_types"]
                      if t != "Journal Article" and not t.startswith("Research Support"))
    return (f'<paper pmid="{a["pmid"]}">\n'
            f"Title: {a['title']}\nJournal: {a['journal']}\nAuthors: {a['authors']}\n"
            + (f"PubMed publication types: {types}\n" if types else "")
            + f"Abstract: {abstract}\n</paper>")


def section_request(section, candidates, taken):
    lines = [f"Section: {section['title']}", "", "<section_guidance>",
             " ".join(section.get("guidance", "").split()), "</section_guidance>", ""]
    if section.get("style") == "themes":
        lines += ["<section_format>", THEMES_FORMAT, "</section_format>", ""]
    if taken:
        lines += ["These papers were already selected for other sections of this week's "
                  "review and must not be selected again here:",
                  ", ".join(sorted(taken)), ""]
    lines += [f"<candidates count=\"{len(candidates)}\">"]
    lines += [candidate_block(a) for a in candidates]
    lines += ["</candidates>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _normalise(text):
    # Lancet-style mid-dot decimals; thousands split by comma or any kind of space
    text = text.replace("\u00b7", ".")
    return re.sub(r"(?<=\d)[,\u0020\u00a0\u2009\u202f](?=\d{3}(?!\d))", "", text)


def unsupported_numbers(finding, abstract):
    """Numbers in the finding that do not appear in the abstract."""
    have = set(NUMBER_RE.findall(_normalise(abstract)))
    return [n for n in NUMBER_RE.findall(_normalise(finding)) if n not in have]


def clean(text):
    # Drop stray "(12345678)" PMID references from prose
    text = re.sub(r"\s*\((?:PMIDs?:?\s*)?\d{8}(?:\s*[,;]\s*\d{8})*\)", "", str(text))
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def render_section(title, result, by_pmid):
    out = [f"## {title}", "", clean(result["landscape"]), ""]
    for s in result["studies"]:
        a = by_pmid[s["pmid"]]
        name = a["title"].rstrip(".")
        if s["depth"] == "full":
            doi = f" · [DOI](https://doi.org/{a['doi']})" if a["doi"] else ""
            out += [f"### [{name}]({a['url']})",
                    f"*{a['journal']} · {a['authors']} · {clean(s['study_type'])}*{doi}", "",
                    f"**Area:** {clean(s['area'])}", ""]
            if clean(s["design"]):
                out += [f"**Design:** {clean(s['design'])}", ""]
            out += [f"**Results:** {clean(s['results'])}", ""]
            if clean(s["context"]):
                out += [f"**Context:** {clean(s['context'])}", ""]
        else:
            tail = " ".join(x for x in (clean(s["results"]), clean(s["context"])) if x)
            out += [f"**[{name}]({a['url']})**  ",
                    f"*{a['journal']} · {clean(s['study_type'])} · {clean(s['area'])}*  ",
                    tail, ""]
    if result["also_notable"]:
        out += ["**Also notable**", ""]
        for n in result["also_notable"]:
            a = by_pmid[n["pmid"]]
            out.append(f"- [{a['title'].rstrip('.')}]({a['url']}) (*{a['journal']}*). {clean(n['note'])}")
        out.append("")
    return "\n".join(out)


def curate():
    config = load_config()
    if not CANDIDATES_PATH.exists():
        sys.exit("No data/candidates.json — run collect.py first.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set (environment or .env).")

    data = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    sections = {s["key"]: s for s in config["sections"]}
    max_studies = config.get("max_per_section", 5)
    max_notable = config.get("max_also_notable", 5)
    max_candidates = config.get("max_candidates", 60)

    curator = Curator(config)
    system = SYSTEM_PROMPT.format(
        reader_profile=" ".join(config.get("reader_profile", "").split()),
        max_studies=max_studies, max_notable=max_notable)

    results, notes, taken = {}, [], set()
    notes += [f"PubMed search failed for: {f}." for f in data.get("failures", [])]

    order = [k for k in CURATE_ORDER if k in sections] + [k for k in sections if k not in CURATE_ORDER]
    for key in order:
        section = sections[key]
        candidates = [a for a in data["sections"].get(key, []) if a["pmid"] not in taken][:section.get("max_candidates", max_candidates)]
        by_pmid = {a["pmid"]: a for a in candidates}
        print(f"\n=== {section['title']}: {len(candidates)} candidates ===")
        if not candidates:
            results[key] = ({"landscape": "No candidate papers were found this week.",
                             "studies": [], "also_notable": []}, by_pmid)
            continue
        try:
            result = json.loads(curator.ask(system, section_request(section, candidates, taken),
                                            SECTION_SCHEMA))
        except Exception as e:
            print(f"  FAILED: {e}")
            notes.append(f"{section['title']}: selection failed this week ({type(e).__name__}: {e}).")
            results[key] = ({"landscape": "This section could not be generated this week; "
                                          "see Verification Notes.",
                             "studies": [], "also_notable": []}, by_pmid)
            continue

        # Drop anything not in the candidate list, duplicates, and overflow.
        for field, limit in (("studies", max_studies), ("also_notable", max_notable)):
            kept = []
            for item in result[field]:
                pmid = re.sub(r"\D", "", item["pmid"])
                if pmid not in by_pmid:
                    notes.append(f"{section['title']}: dropped an entry citing PMID "
                                 f"{item['pmid']!r}, which was not among the candidates.")
                    continue
                if pmid in taken:
                    continue
                item["pmid"] = pmid
                taken.add(pmid)
                kept.append(item)
            result[field] = kept[:limit]

        for s in result["studies"]:
            a = by_pmid[s["pmid"]]
            bad = unsupported_numbers(f"{s['design']} {s['results']}", a["abstract"])
            if bad:
                notes.append(f"{section['title']}, PMID {s['pmid']}: the write-up cites "
                             f"{', '.join(bad)}, not found verbatim in the PubMed abstract. "
                             "Check against the paper.")
            print(f"  + [{s['depth']}] {a['journal']}: {a['title'][:90]}")
        results[key] = (result, by_pmid)

    body = "\n".join(render_section(sections[k]["title"], *results[k])
                     for k in sections if k in results)

    n_studies = sum(len(r["studies"]) for r, _ in results.values())
    n_full = sum(s["depth"] == "full" for r, _ in results.values() for s in r["studies"])
    overview = ""
    if n_studies:
        try:
            overview = clean(curator.ask(
                "You write the opening paragraph of a weekly literature review for a "
                "heart failure and transplant cardiologist. Plain, direct clinical prose.",
                OVERVIEW_PROMPT.format(review=body)))
        except Exception as e:
            notes.append(f"Opening summary could not be generated ({type(e).__name__}: {e}).")

    total = sum(len(v) for v in data["sections"].values())
    verification = [
        f"{total} candidate papers screened from PubMed; {n_studies} selected "
        f"({n_full} with a full write-up).",
        "Every selected PMID, title, author list and journal is taken directly from PubMed. "
        "Design and Results lines are drawn from the abstract and their numbers checked against it "
        "automatically. Context lines reflect the model's knowledge of the prior literature "
        "and are not machine-verified.",
    ] + (notes or ["No discrepancies flagged."])

    report = "\n".join([
        "# Weekly Literature Review",
        f"**Period**: {data['window']}",
        "",
        "## This Week in Brief",
        "",
        overview or "No studies met the bar for inclusion this week.",
        "",
        body,
        "## Verification Notes",
        "",
        *[f"- {n}" for n in verification],
        "",
        f"*Selected and written by {', '.join(sorted(curator.models_used)) or curator.model} "
        f"from PubMed records added {data['window']}.*",
        "",
    ])

    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"review_{datetime.now():%Y-%m-%d}.md"
    path.write_text(report, encoding="utf-8")
    print(f"\nwrote {path.relative_to(BASE_DIR)}")
    print(f"API usage: {curator.usage['input']:,} input + {curator.usage['output']:,} output tokens")
    for n in notes:
        print(f"NOTE: {n}")

    if n_studies == 0 and any("failed" in n for n in notes):
        sys.exit("ERROR: no section could be generated.")
    return path


if __name__ == "__main__":
    curate()
