"""
collect.py — Gathers this week's candidate studies from PubMed for each section.

For every section in config.yaml it runs the section's PubMed searches, drops
editorials/letters/case reports, ranks what is left (journal tier, study type,
emphasis topics) and writes:

    data/candidates.json            every candidate, full abstract
    data/candidates_<section>.md    ranked short-list per section, for curation

Usage:
    python collect.py                 # last 7 days (lookback_days in config.yaml)
    python collect.py --days 14       # a longer window
    python collect.py --show 12345 67890   # print full records for these PMIDs
"""

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import requests
import yaml

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"
DATA_DIR = BASE_DIR / "data"
JSON_PATH = DATA_DIR / "candidates.json"

HEADERS = {"User-Agent": "LitReviewAgent/2.0 (weekly academic literature review)"}
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Publication types that are never "a study"
EXCLUDE_TYPES = {
    "Comment", "Editorial", "Letter", "News", "Published Erratum", "Case Reports",
    "Retraction of Publication", "Biography", "Historical Article",
    "Personal Narrative", "Interview", "Expression of Concern",
    "Patient Education Handout", "Portrait",
}

RCT_RE = re.compile(r"\brandomi[sz]ed\b|\bRCT\b|\bplacebo\b|\bdouble-blind", re.I)
SYNTH_RE = re.compile(r"meta-analysis|systematic review|guideline|scientific statement|consensus", re.I)
TRIAL_RE = re.compile(r"\btrial\b|\bphase [1-3I]+\b|\bprospective\b|\bcohort\b|\bregistry\b", re.I)


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get(url, params, timeout=40, tries=4):
    """GET/POST with retries (NCBI occasionally rate-limits or hiccups)."""
    for attempt in range(tries):
        try:
            resp = requests.post(url, data=params, headers=HEADERS, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt == tries - 1:
                raise
            wait = 2 * (attempt + 1)
            print(f"    retry in {wait}s ({e})")
            time.sleep(wait)


# ---------------------------------------------------------------------------
# PubMed
# ---------------------------------------------------------------------------

def pubmed_search(query, days, retmax, sort=None):
    """Return PMIDs that entered PubMed in the last `days` days."""
    params = {
        "db": "pubmed", "retmode": "json", "retmax": retmax, "term": query,
        "datetype": "edat", "reldate": days,
    }
    if sort:
        params["sort"] = sort
    result = get(f"{PUBMED_BASE}/esearch.fcgi", params).json().get("esearchresult", {})
    if "ERROR" in result:
        raise RuntimeError(result["ERROR"])
    for w in (result.get("warninglist") or {}).get("quotedphrasenotfound", []):
        print(f"    note: no PubMed match for phrase {w}")
    time.sleep(0.4)
    return result.get("idlist", []), int(result.get("count", 0))


def _text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_article(art):
    medline = art.find("MedlineCitation")
    article = medline.find("Article")
    pmid = medline.findtext("PMID", "")

    authors = []
    for au in article.findall("AuthorList/Author"):
        last = au.findtext("LastName", "")
        if last:
            authors.append(f"{last} {au.findtext('Initials', '')}".strip())
        elif au.findtext("CollectiveName"):
            authors.append(au.findtext("CollectiveName"))
    author_str = ", ".join(authors[:3]) + (", et al." if len(authors) > 3 else "")

    parts = []
    for ab in article.findall("Abstract/AbstractText"):
        label, text = ab.get("Label", ""), _text(ab)
        parts.append(f"{label}: {text}" if label else text)

    doi = ""
    for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = aid.text or ""

    return {
        "pmid": pmid,
        "title": _text(article.find("ArticleTitle")),
        "authors": author_str,
        "journal": medline.findtext("MedlineJournalInfo/MedlineTA", "")
                   or article.findtext("Journal/ISOAbbreviation", ""),
        "pub_types": [_text(pt) for pt in article.findall("PublicationTypeList/PublicationType")],
        "abstract": " ".join(parts),
        "doi": doi,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def pubmed_fetch(pmids):
    """Fetch full records for a list of PMIDs (batched)."""
    out = []
    for i in range(0, len(pmids), 100):
        batch = pmids[i:i + 100]
        resp = get(f"{PUBMED_BASE}/efetch.fcgi",
                   {"db": "pubmed", "retmode": "xml", "id": ",".join(batch)}, timeout=90)
        root = ET.fromstring(resp.content)
        for art in root.findall("PubmedArticle"):
            try:
                out.append(parse_article(art))
            except Exception as e:
                print(f"    parse error: {e}")
        time.sleep(0.4)
    return out


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def make_scorer(config):
    tiers = config.get("journal_tiers", {})
    t1 = {j.lower() for j in tiers.get("tier1", [])}
    t2 = {j.lower() for j in tiers.get("tier2", [])}

    def score(a, emphasis_re=None):
        j = a["journal"].lower()
        a["tier"] = 1 if j in t1 else 2 if j in t2 else 3
        s = {1: 100, 2: 50, 3: 0}[a["tier"]]
        types = set(a["pub_types"])
        blob = f"{a['title']} {a['abstract']}"
        if "Randomized Controlled Trial" in types or RCT_RE.search(blob):
            s += 30
        elif types & {"Meta-Analysis", "Practice Guideline", "Guideline", "Systematic Review"} \
                or SYNTH_RE.search(a["title"]):
            s += 20
        elif any("Clinical Trial" in t for t in types) or TRIAL_RE.search(blob):
            s += 12
        if emphasis_re and emphasis_re.search(blob):
            s += 25
            a["emphasis"] = True
        return s

    return score


def expand(query, fragments):
    for name, text in fragments.items():
        query = query.replace("{" + name + "}", " ".join(text.split()))
    return " ".join(query.split())


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_section_md(section, arts, max_candidates, window, signals=None):
    path = DATA_DIR / f"candidates_{section['key']}.md"
    lines = [
        f"# Candidates — {section['title']}",
        f"Window: {window}. {len(arts)} candidates after filtering; "
        f"top {min(len(arts), max_candidates)} shown, strongest first.",
        "Abstracts are truncated here. Full text of any abstract: "
        "`python collect.py --show <PMID> [<PMID> ...]`",
        "",
    ]
    for a in arts[:max_candidates]:
        flags = [f"T{a['tier']}"]
        if a.get("emphasis"):
            flags.append("EMPHASIS")
        if a.get("also_in"):
            flags.append("also in: " + ", ".join(a["also_in"]))
        types = [t for t in a["pub_types"] if t not in ("Journal Article",)
                 and not t.startswith("Research Support")]
        abstract = a["abstract"]
        if len(abstract) > 750:
            abstract = abstract[:750].rsplit(" ", 1)[0] + " […]"
        lines += [
            f"## PMID {a['pmid']} — {a['title']}",
            f"*{a['journal']}* · {a['authors']} · [{' | '.join(flags)}]"
            + (f" · {', '.join(types)}" if types else ""),
            abstract,
            "",
        ]
    if signals:
        lines += ["---", "## Commentary signals (not studies; do not cite as studies)"]
        lines += [f"- {s['source']}: [{s['title']}]({s['url']}) ({s['date']})" for s in signals]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def fetch_signals(feeds, days):
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for feed in feeds or []:
        try:
            parsed = feedparser.parse(feed["url"], request_headers=HEADERS)
        except Exception as e:
            print(f"  signal feed error ({feed['name']}): {e}")
            continue
        for entry in parsed.entries:
            when = entry.get("published_parsed") or entry.get("updated_parsed")
            if not when or datetime(*when[:6]) < cutoff:
                continue
            out.append({"source": feed["name"], "title": entry.get("title", "").strip(),
                        "url": entry.get("link", ""),
                        "date": datetime(*when[:6]).strftime("%Y-%m-%d")})
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect(days=None):
    config = load_config()
    days = days or config.get("lookback_days", 7)
    max_candidates = config.get("max_candidates", 45)
    fragments = config.get("fragments", {})
    score = make_scorer(config)
    today = datetime.now()
    window = f"{(today - timedelta(days=days)):%b %d} – {today:%b %d, %Y}"

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    results, membership, failures = {}, {}, []

    for section in config["sections"]:
        print(f"\n=== {section['title']} ===")
        pmids = []
        for q in section["queries"]:
            try:
                ids, total = pubmed_search(expand(q["query"], fragments), days,
                                           q.get("retmax", 100), q.get("sort"))
                note = f" (of {total} matches)" if total > len(ids) else ""
                print(f"  {q['name']}: {len(ids)}{note}")
                pmids += [i for i in ids if i not in pmids]
            except Exception as e:
                print(f"  {q['name']}: FAILED — {e}")
                failures.append(f"{section['title']} / {q['name']}")
        try:
            arts = pubmed_fetch(pmids) if pmids else []
        except Exception as e:
            print(f"  fetch FAILED — {e}")
            failures.append(f"{section['title']} / fetch")
            arts = []

        arts = [a for a in arts if a["abstract"] and not (set(a["pub_types"]) & EXCLUDE_TYPES)]
        emphasis_re = re.compile(section["emphasis_regex"], re.I) if section.get("emphasis_regex") else None
        relevance_re = re.compile(section["relevance_regex"], re.I) if section.get("relevance_regex") else None
        for a in arts:
            a["score"] = score(a, emphasis_re)
            # Push papers that matched the search but look off-topic to the bottom.
            if relevance_re and not relevance_re.search(f"{a['title']} {a['abstract']}"):
                a["score"] -= 80
            membership.setdefault(a["pmid"], []).append(section["title"])
        arts.sort(key=lambda a: -a["score"])
        results[section["key"]] = arts
        print(f"  -> {len(arts)} candidates")

    signals = fetch_signals(config.get("signal_feeds"), days)

    for section in config["sections"]:
        arts = results[section["key"]]
        for a in arts:
            a["also_in"] = [t for t in membership[a["pmid"]] if t != section["title"]]
        sig = signals if section["key"] == "ai_medicine" else None
        path = write_section_md(section, arts, max_candidates, window, sig)
        print(f"wrote {path.relative_to(BASE_DIR)}")

    JSON_PATH.write_text(json.dumps(
        {"window": window, "days": days, "generated": today.isoformat(timespec="minutes"),
         "failures": failures, "sections": results},
        indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {JSON_PATH.relative_to(BASE_DIR)}")
    print(f"\nWindow: {window}")

    if failures:
        print("\nWARNING — some searches failed: " + "; ".join(failures))
    if not any(results.values()):
        print("ERROR: no candidates collected at all.")
        sys.exit(1)


def show(pmids):
    """Print full records. Looks in candidates.json first, then asks PubMed."""
    known = {}
    if JSON_PATH.exists():
        for arts in json.loads(JSON_PATH.read_text(encoding="utf-8"))["sections"].values():
            known.update({a["pmid"]: a for a in arts})
    missing = [p for p in pmids if p not in known]
    if missing:
        known.update({a["pmid"]: a for a in pubmed_fetch(missing)})
    for p in pmids:
        a = known.get(p)
        if not a:
            print(f"PMID {p}: NOT FOUND\n")
            continue
        print(f"PMID {a['pmid']} — {a['title']}\n{a['journal']} · {a['authors']}\n"
              f"Types: {', '.join(a['pub_types'])}\nDOI: {a['doi']}\n{a['url']}\n\n{a['abstract']}\n"
              + "-" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, help="look back this many days")
    parser.add_argument("--show", nargs="+", metavar="PMID", help="print full records")
    args = parser.parse_args()
    if args.show:
        show(args.show)
    else:
        collect(args.days)
