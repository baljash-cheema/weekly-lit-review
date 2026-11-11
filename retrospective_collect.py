"""
retrospective_collect.py — Fetches PubMed articles month-by-month for retrospective reviews.

Reusable for any topic. Pre-configured profiles: ai_medicine, cardiology, longevity, heart_failure.

Usage:
    python retrospective_collect.py ai_medicine
    python retrospective_collect.py cardiology
    python retrospective_collect.py longevity
    python retrospective_collect.py heart_failure

Output: data/retrospective_{topic}.json
"""

import json
import sys
import time
import xml.etree.ElementTree as ET
from calendar import monthrange
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

BASE_DIR = Path(__file__).parent
HEADERS = {
    "User-Agent": "LitReviewAgent/1.0 (academic literature review)"
}
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# ---------------------------------------------------------------------------
# Topic profiles — add new ones here for future reviews
# ---------------------------------------------------------------------------

TOPIC_PROFILES = {
    "ai_medicine": {
        "name": "AI in Medicine",
        "queries": [
            {
                "name": "AI in Clinical Medicine (Broad)",
                "query": (
                    '(("artificial intelligence"[Title/Abstract] OR "machine learning"[Title/Abstract]'
                    ' OR "deep learning"[Title/Abstract] OR "large language model"[Title/Abstract]'
                    ' OR "natural language processing"[Title/Abstract] OR "neural network"[Title/Abstract]'
                    ' OR "generative AI"[Title/Abstract] OR "foundation model"[Title/Abstract]'
                    ' OR "clinical decision support"[Title/Abstract])'
                    ' AND ("clinical"[Title/Abstract] OR "diagnosis"[Title/Abstract]'
                    ' OR "patient"[Title/Abstract] OR "treatment"[Title/Abstract]'
                    ' OR "prognosis"[Title/Abstract] OR "screening"[Title/Abstract]'
                    ' OR "electronic health record"[Title/Abstract] OR "EHR"[Title/Abstract]'
                    ' OR "medical imaging"[Title/Abstract] OR "radiology"[Title/Abstract]'
                    ' OR "pathology"[Title/Abstract] OR "surgery"[Title/Abstract]'
                    ' OR "primary care"[Title/Abstract] OR "emergency"[Title/Abstract]'
                    ' OR "hospital"[Title/Abstract] OR "healthcare"[Title/Abstract]'
                    ' OR "physician"[Title/Abstract] OR "nursing"[Title/Abstract]))'
                ),
                "retmax": 80,
            },
            {
                "name": "High-Impact Journals — Any AI",
                "query": (
                    '(("artificial intelligence"[Title/Abstract] OR "machine learning"[Title/Abstract]'
                    ' OR "deep learning"[Title/Abstract] OR "large language model"[Title/Abstract]'
                    ' OR "generative AI"[Title/Abstract] OR "ChatGPT"[Title/Abstract]'
                    ' OR "foundation model"[Title/Abstract] OR "neural network"[Title/Abstract])'
                    ' AND ("N Engl J Med"[Journal] OR "JAMA"[Journal]'
                    ' OR "Lancet"[Journal] OR "BMJ"[Journal]'
                    ' OR "Nat Med"[Journal] OR "Ann Intern Med"[Journal]'
                    ' OR "Lancet Digit Health"[Journal] OR "NEJM AI"[Journal]'
                    ' OR "npj Digit Med"[Journal] OR "Nat Rev Drug Discov"[Journal]'
                    ' OR "JAMA Intern Med"[Journal] OR "JAMA Netw Open"[Journal]'
                    ' OR "Nature"[Journal] OR "Science"[Journal]))'
                ),
                "retmax": 50,
            },
        ],
    },
    "cardiology": {
        "name": "Cardiology",
        "queries": [
            {
                "name": "Cardiology — High-Impact Journals",
                "query": (
                    '(("heart"[Title/Abstract] OR "cardiac"[Title/Abstract]'
                    ' OR "cardiovascular"[Title/Abstract] OR "coronary"[Title/Abstract]'
                    ' OR "myocardial"[Title/Abstract] OR "atrial fibrillation"[Title/Abstract]'
                    ' OR "heart failure"[Title/Abstract] OR "valvular"[Title/Abstract]'
                    ' OR "arrhythmia"[Title/Abstract] OR "percutaneous coronary"[Title/Abstract]'
                    ' OR "aortic stenosis"[Title/Abstract] OR "hypertension"[Title/Abstract])'
                    ' AND ("N Engl J Med"[Journal] OR "JAMA"[Journal] OR "Lancet"[Journal]'
                    ' OR "Circulation"[Journal] OR "J Am Coll Cardiol"[Journal]'
                    ' OR "Eur Heart J"[Journal] OR "JAMA Cardiol"[Journal]'
                    ' OR "Circ Res"[Journal] OR "JACC Cardiovasc Interv"[Journal]'
                    ' OR "Nat Med"[Journal] OR "BMJ"[Journal]))'
                ),
                "retmax": 80,
            },
        ],
    },
    "longevity": {
        "name": "Longevity Medicine",
        "queries": [
            {
                "name": "Longevity & Aging Science",
                "query": (
                    '(("aging"[Title/Abstract] OR "ageing"[Title/Abstract]'
                    ' OR "longevity"[Title/Abstract] OR "senescence"[Title/Abstract]'
                    ' OR "senolytic"[Title/Abstract] OR "epigenetic clock"[Title/Abstract]'
                    ' OR "healthspan"[Title/Abstract] OR "lifespan"[Title/Abstract]'
                    ' OR "biological age"[Title/Abstract] OR "geroscience"[Title/Abstract]'
                    ' OR "caloric restriction"[Title/Abstract] OR "rapamycin"[Title/Abstract]'
                    ' OR "metformin aging"[Title/Abstract] OR "NAD+"[Title/Abstract])'
                    ' AND ("Nature"[Journal] OR "Science"[Journal] OR "Cell"[Journal]'
                    ' OR "Nat Med"[Journal] OR "Nat Aging"[Journal] OR "N Engl J Med"[Journal]'
                    ' OR "Aging Cell"[Journal] OR "GeroScience"[Journal]'
                    ' OR "Lancet"[Journal] OR "JAMA"[Journal] OR "Cell Metab"[Journal]'
                    ' OR "Nat Rev Mol Cell Biol"[Journal]))'
                ),
                "retmax": 80,
            },
        ],
    },
    "heart_failure": {
        "name": "Heart Failure",
        "queries": [
            {
                "name": "Heart Failure — High-Impact Journals",
                "query": (
                    '(("heart failure"[Title/Abstract] OR "HFrEF"[Title/Abstract]'
                    ' OR "HFpEF"[Title/Abstract] OR "cardiomyopathy"[Title/Abstract]'
                    ' OR "ventricular assist"[Title/Abstract] OR "LVAD"[Title/Abstract]'
                    ' OR "cardiac transplant"[Title/Abstract] OR "GDMT"[Title/Abstract]'
                    ' OR "ejection fraction"[Title/Abstract])'
                    ' AND ("N Engl J Med"[Journal] OR "JAMA"[Journal] OR "Lancet"[Journal]'
                    ' OR "Circulation"[Journal] OR "J Am Coll Cardiol"[Journal]'
                    ' OR "Eur Heart J"[Journal] OR "JACC Heart Fail"[Journal]'
                    ' OR "Eur J Heart Fail"[Journal] OR "Nat Med"[Journal]'
                    ' OR "JAMA Cardiol"[Journal] OR "BMJ"[Journal]))'
                ),
                "retmax": 80,
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# PubMed fetcher (adapted for specific month ranges)
# ---------------------------------------------------------------------------

def fetch_pubmed_month(query, year, month, retmax=80):
    """Search PubMed for a specific month and return structured articles."""
    articles = []
    last_day = monthrange(year, month)[1]
    date_from = f"{year}/{month:02d}/01"
    date_to = f"{year}/{month:02d}/{last_day}"

    search_url = (
        f"{PUBMED_BASE}/esearch.fcgi?db=pubmed&retmode=json&retmax={retmax}"
        f"&term={quote(query)}&datetype=pdat&mindate={date_from}&maxdate={date_to}"
        f"&sort=relevance"
    )
    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        print(f"    Search error: {e}")
        return articles

    if not ids:
        return articles

    time.sleep(0.35)

    fetch_url = (
        f"{PUBMED_BASE}/efetch.fcgi?db=pubmed&retmode=xml&id={','.join(ids)}"
    )
    try:
        resp = requests.get(fetch_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except Exception as e:
        print(f"    Fetch error: {e}")
        return articles

    for art in root.findall(".//PubmedArticle"):
        try:
            medline = art.find(".//MedlineCitation")
            article_el = medline.find("Article")
            pmid = medline.findtext("PMID", "")
            title = article_el.findtext("ArticleTitle", "").strip()

            # Journal
            journal_el = article_el.find("Journal")
            journal = ""
            if journal_el is not None:
                journal = journal_el.findtext("Title", "")
                if not journal:
                    journal = journal_el.findtext("ISOAbbreviation", "")

            # Authors
            author_list = article_el.find("AuthorList")
            authors = []
            if author_list is not None:
                for au in author_list.findall("Author"):
                    last = au.findtext("LastName", "")
                    init = au.findtext("Initials", "")
                    if last:
                        authors.append(f"{last} {init}".strip())
            author_str = ", ".join(authors[:3])
            if len(authors) > 3:
                author_str += ", et al."

            # Abstract
            abstract_parts = []
            abstract_el = article_el.find("Abstract")
            if abstract_el is not None:
                for ab_text in abstract_el.findall("AbstractText"):
                    label = ab_text.get("Label", "")
                    text = "".join(ab_text.itertext()).strip()
                    if label:
                        abstract_parts.append(f"{label}: {text}")
                    else:
                        abstract_parts.append(text)
            abstract = " ".join(abstract_parts)

            # Date
            pub_date = article_el.find(".//PubDate")
            yr = pub_date.findtext("Year", "") if pub_date is not None else ""
            mo = pub_date.findtext("Month", "01") if pub_date is not None else "01"
            dy = pub_date.findtext("Day", "01") if pub_date is not None else "01"
            try:
                mo_num = datetime.strptime(mo, "%b").month if not mo.isdigit() else int(mo)
            except ValueError:
                mo_num = 1
            date_str = f"{yr}-{mo_num:02d}-{int(dy):02d}" if yr else ""

            # DOI
            doi = ""
            for eid in article_el.findall(".//ELocationID"):
                if eid.get("EIdType") == "doi":
                    doi = eid.text or ""

            # Publication types
            pub_types = []
            for pt in article_el.findall(".//PublicationType"):
                pub_types.append(pt.text or "")

            articles.append({
                "title": title,
                "authors": author_str,
                "journal": journal,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "abstract": abstract[:2000],
                "date": date_str,
                "pub_types": pub_types,
                "identifiers": {"pmid": pmid, "doi": doi},
            })
        except Exception as e:
            print(f"    Parse error: {e}")
            continue

    time.sleep(0.35)
    return articles


def deduplicate(articles):
    """Remove duplicates by PMID."""
    seen = set()
    unique = []
    for art in articles:
        pmid = art.get("identifiers", {}).get("pmid", "")
        key = pmid if pmid else art["title"].lower().strip()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(art)
    return unique


def run_retrospective(topic_key, months_back=24):
    """Fetch PubMed month-by-month for a topic profile."""
    if topic_key not in TOPIC_PROFILES:
        print(f"Unknown topic: {topic_key}")
        print(f"Available: {', '.join(TOPIC_PROFILES.keys())}")
        sys.exit(1)

    profile = TOPIC_PROFILES[topic_key]
    print(f"\n{'='*60}")
    print(f"Retrospective Review: {profile['name']}")
    print(f"{'='*60}")

    # Generate month list (earliest first)
    now = datetime.now()
    months = []
    for i in range(months_back, 0, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))

    print(f"Covering {len(months)} months: "
          f"{months[0][0]}-{months[0][1]:02d} to {months[-1][0]}-{months[-1][1]:02d}\n")

    results = {}
    total = 0

    for year, month in months:
        month_key = f"{year}-{month:02d}"
        print(f"--- {month_key} ---")
        month_articles = []

        for q in profile["queries"]:
            print(f"  {q['name']}...", end=" ", flush=True)
            arts = fetch_pubmed_month(q["query"], year, month, q.get("retmax", 80))
            print(f"{len(arts)} articles")
            month_articles.extend(arts)

        month_articles = deduplicate(month_articles)
        results[month_key] = month_articles
        total += len(month_articles)
        print(f"  Total: {len(month_articles)} (after dedup)\n")

    # Save
    output = {
        "topic": profile["name"],
        "topic_key": topic_key,
        "collected_at": datetime.now().isoformat(),
        "date_range": {
            "from": f"{months[0][0]}-{months[0][1]:02d}",
            "to": f"{months[-1][0]}-{months[-1][1]:02d}",
        },
        "total_articles": total,
        "months": results,
    }

    out_path = BASE_DIR / "data" / f"retrospective_{topic_key}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"{'='*60}")
    print(f"Done. {total} articles across {len(months)} months")
    print(f"Saved to {out_path}")
    return output


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "ai_medicine"
    months = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    run_retrospective(topic, months)
