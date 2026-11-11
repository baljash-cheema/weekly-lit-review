"""
retrospective_curate_prep.py — Pre-filters and ranks articles for curation.

Reads the full retrospective JSON, ranks by journal prestige, and writes
compact per-batch summary files for parallel curation agents.

Usage:
    python retrospective_curate_prep.py ai_medicine

Output: data/curate_batch_{N}.md (one per 6-month batch)
"""

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Journal prestige tiers (higher = more prestigious)
TIER_1 = {
    "The New England journal of medicine", "JAMA", "Lancet (London, England)",
    "The Lancet", "Nature medicine", "Nature", "Science",
    "BMJ (Clinical research ed.)", "The BMJ",
    "Annals of internal medicine",
}
TIER_2 = {
    "The Lancet. Digital health", "NEJM AI", "npj digital medicine",
    "JAMA internal medicine", "JAMA network open", "Nature reviews. Drug discovery",
    "JAMA oncology", "JAMA surgery", "JAMA cardiology", "JAMA neurology",
    "JAMA pediatrics", "JAMA dermatology", "JAMA ophthalmology",
    "The Lancet. Oncology", "The Lancet. Respiratory medicine",
    "Nature biotechnology", "Nature methods",
    "Radiology", "European heart journal",
    "Journal of clinical oncology : official journal of the American Society of Clinical Oncology",
    "Circulation",
}


def journal_tier(journal_name):
    jl = journal_name.lower().strip()
    for t1 in TIER_1:
        if t1.lower() in jl or jl in t1.lower():
            return 1
    for t2 in TIER_2:
        if t2.lower() in jl or jl in t2.lower():
            return 2
    return 3


def is_clinical_study(pub_types):
    """Boost clinical trials and RCTs."""
    clinical_types = {
        "Randomized Controlled Trial", "Clinical Trial", "Clinical Trial, Phase III",
        "Clinical Trial, Phase IV", "Multicenter Study", "Observational Study",
        "Meta-Analysis", "Systematic Review", "Practice Guideline",
    }
    for pt in pub_types:
        if pt in clinical_types:
            return True
    return False


def rank_articles(articles):
    """Sort articles by journal prestige + clinical relevance."""
    def sort_key(art):
        tier = journal_tier(art.get("journal", ""))
        clinical_boost = 0 if is_clinical_study(art.get("pub_types", [])) else 1
        return (tier, clinical_boost, art.get("title", ""))
    return sorted(articles, key=sort_key)


def write_batch(batch_num, months_data, out_dir):
    """Write a compact markdown summary for a batch of months."""
    lines = []
    for month_key, articles in sorted(months_data.items()):
        ranked = rank_articles(articles)
        top = ranked[:25]  # Keep top 25 candidates per month

        lines.append(f"# {month_key}")
        lines.append(f"({len(articles)} total articles, showing top {len(top)} by journal prestige)\n")

        for i, art in enumerate(top, 1):
            pmid = art.get("identifiers", {}).get("pmid", "")
            doi = art.get("identifiers", {}).get("doi", "")
            journal = art.get("journal", "Unknown")
            tier = journal_tier(journal)
            tier_label = {1: "T1", 2: "T2", 3: "T3"}[tier]

            # Compact abstract (first 300 chars)
            abstract = art.get("abstract", "")[:300]
            if len(art.get("abstract", "")) > 300:
                abstract += "..."

            pub_types = ", ".join(art.get("pub_types", []))

            lines.append(f"## {i}. [{tier_label}] {art['title']}")
            lines.append(f"- **Journal**: {journal}")
            lines.append(f"- **Authors**: {art.get('authors', '')}")
            lines.append(f"- **Date**: {art.get('date', '')}")
            lines.append(f"- **PMID**: {pmid} | **DOI**: {doi}")
            if pub_types:
                lines.append(f"- **Type**: {pub_types}")
            lines.append(f"- **Abstract**: {abstract}")
            lines.append("")

        lines.append("---\n")

    out_path = out_dir / f"curate_batch_{batch_num}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Batch {batch_num}: {len(months_data)} months → {out_path}")
    return out_path


def main(topic_key):
    json_path = BASE_DIR / "data" / f"retrospective_{topic_key}.json"
    if not json_path.exists():
        print(f"Not found: {json_path}")
        print("Run retrospective_collect.py first.")
        sys.exit(1)

    with open(json_path) as f:
        data = json.load(f)

    months = data["months"]
    month_keys = sorted(months.keys())
    print(f"Topic: {data['topic']}")
    print(f"Months: {month_keys[0]} to {month_keys[-1]} ({len(month_keys)} months)")

    out_dir = BASE_DIR / "data"

    # Split into 4 batches of 6 months
    batch_size = 6
    for batch_num in range(4):
        start = batch_num * batch_size
        end = start + batch_size
        batch_keys = month_keys[start:end]
        batch_data = {k: months[k] for k in batch_keys}
        write_batch(batch_num + 1, batch_data, out_dir)

    print("\nDone. Ready for parallel curation.")


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "ai_medicine"
    main(topic)
