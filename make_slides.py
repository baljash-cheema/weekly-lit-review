"""
make_slides.py — Builds a plain PowerPoint deck from the week's review: one slide for
each study that got a full write-up in the sections listed under `slides` in config.yaml.

Reads   reports/selection_YYYY-MM-DD.json   (written by curate.py)
Writes  slides/<year>/review-slides-YYYY-MM-DD.pptx
        (nothing is written in a week with no full write-ups in those sections)

Usage:
    python make_slides.py                      # latest selection file
    python make_slides.py path/to/selection.json --out deck.pptx
"""

import argparse
import json
import sys
from pathlib import Path

import yaml
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

BASE_DIR = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"

INK = RGBColor(0x22, 0x22, 0x22)
GRAY = RGBColor(0x66, 0x66, 0x66)
SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)   # standard widescreen
MARGIN = Inches(0.6)


def add_text(slide, top, height, paragraphs):
    """paragraphs: list of runs-lists; each run is (text, size_pt, bold, color)."""
    box = slide.shapes.add_textbox(MARGIN, top, SLIDE_W - 2 * MARGIN, height)
    frame = box.text_frame
    frame.word_wrap = True
    for i, runs in enumerate(paragraphs):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        para.space_after = Pt(10)
        for text, size, bold, color in runs:
            run = para.add_run()
            run.text = text
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = color
            run.font.name = "Calibri"
    return box


def add_study_slide(prs, study, section_title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])   # the built-in blank layout

    title = study["title"].rstrip(".")
    title_size = 26 if len(title) <= 90 else 22 if len(title) <= 150 else 18
    add_text(slide, Inches(0.4), Inches(1.3), [[(title, title_size, True, INK)]])
    add_text(slide, Inches(1.65), Inches(0.4),
             [[(f"{study['journal']}  ·  {study['study_type']}", 14, False, GRAY)]])

    parts = [("Design", study.get("design", "")), ("Results", study.get("results", "")),
             ("Takeaway", study.get("context", ""))]
    parts = [(label, text) for label, text in parts if text]
    words = sum(len(text.split()) for _, text in parts)
    size = 18 if words <= 150 else 16 if words <= 210 else 14
    add_text(slide, Inches(2.2), Inches(4.4),
             [[(f"{label}:  ", size, True, INK), (text, size, False, INK)] for label, text in parts])

    source = f"{study['authors']} {study['journal']}. PMID {study['pmid']}."
    if study.get("doi"):
        source += f" doi:{study['doi']}"
    add_text(slide, Inches(6.85), Inches(0.4), [[(source, 10, False, GRAY)]])

    slide.notes_slide.notes_text_frame.text = (
        f"{section_title}\n{study['url']}\n\n"
        "Design and Results are drawn from the PubMed abstract. The Takeaway reflects the "
        "model's reading of the prior literature and should be checked before presenting.")


def build(selection_path, out_path=None):
    config = yaml.safe_load((BASE_DIR / "config.yaml").read_text())
    wanted = (config.get("slides") or {}).get("sections", [])
    data = json.loads(Path(selection_path).read_text(encoding="utf-8"))

    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    count = 0
    for section in data["sections"]:
        if section["key"] not in wanted:
            continue
        for study in section["studies"]:
            if study["depth"] == "full":
                add_study_slide(prs, study, section["title"])
                count += 1

    if count == 0:
        print("No full write-ups in the slide sections this week; no deck made.")
        return None

    if out_path is None:
        day = data["date"]
        out_path = BASE_DIR / "slides" / day[:4] / f"review-slides-{day}.pptx"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    print(f"Wrote {out_path} ({count} slide{'s' if count != 1 else ''})")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the weekly slide deck.")
    parser.add_argument("selection", nargs="?", help="selection JSON (default: latest in reports/)")
    parser.add_argument("--out", help="where to save the .pptx")
    args = parser.parse_args()

    path = args.selection
    if not path:
        found = sorted(REPORTS_DIR.glob("selection_*.json"))
        if not found:
            sys.exit("No reports/selection_*.json found — run curate.py first.")
        path = found[-1]
    build(path, args.out)
