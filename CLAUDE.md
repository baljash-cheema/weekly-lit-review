# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A weekly literature review for Josh Cheema (heart failure / advanced transplant
cardiologist). Every Friday morning it emails him the most important new
studies in five areas, plus a second email, the weekly lesson, that teaches one topic
in depth (added 2026-11-23). It runs unattended on GitHub Actions and calls the Claude API
directly; it does **not** depend on his Mac or on a Claude Code session.

## The five sections

| Section | Scope |
|---|---|
| General Medicine | All of medicine via NEJM, JAMA, Lancet, BMJ, Annals, Nat Med, JAMA IM |
| Cardiology | The whole field, with modest extra weight on HF, advanced HF, transplant, MCS, shock, HCM |
| AI in Medicine | Clinically meaningful AI: trials, deployments, rigorous validations |
| AI in Medical Education | AI in training physicians (students, residents, fellows, CME) |
| Longevity Medicine | Aging biology and translation; high bar |

Rules Josh set (2026-11-11):
- **No quota.** Only the most important studies. At most 5 full write-ups per section;
  fewer, or none, is fine (especially longevity and AI in medical education).
- Section order: Cardiology, AI in Medicine, AI in Medical Education, Longevity, then
  General Medicine last.
- The first four are detailed: strong studies get **Area**, **Design**, **Results**
  (numbers-heavy) and **Context**; studies of modest quality or impact get a short
  streamlined summary instead (the model chooses `depth: full | brief`).
- General Medicine is a themes digest (`style: themes`): a paragraph on the week's major
  themes plus one-line studies.
- Each section opens with a short "landscape" paragraph on how the field is shifting.
- Delivered early **Friday** to the address in the `REVIEW_EMAIL` secret. Josh wants it
  waiting when he wakes up, so the job starts from **midnight Central** (changed from
  7:30 am on 2026-11-23, after the 2026-11-20 email arrived at 1:16 pm).

## Pipeline

```
collect.py   PubMed searches per section (last 7 days by PubMed entry date)
             -> filter out editorials/letters/case reports, rank by journal tier + design
             -> data/candidates.json  (+ data/candidates_<section>.md, human-readable)
curate.py    One Claude API call per section: choose + write up; one more for the opening
             -> reports/review_YYYY-MM-DD.md
send_email.py  Markdown -> HTML, Gmail SMTP
educate.py     The weekly lesson (see below) -> reports/lesson_YYYY-MM-DD.md
```

Scheduled by `.github/workflows/weekly-review.yml`. GitHub's scheduler is unreliable
here: on 2026-11-13 it started none of the morning entries, and on 2026-11-20 it started
every entry about 7 hours late. So there are about 20 cron entries, from Thursday
evening Chicago time through Friday morning, and a gate step lets the first one that
actually starts on a Friday in Chicago do the work; later ones stop when they find that
day's PDF in `archive/`. The Thursday-evening entries are deliberate: on time they stop
at the gate, and when GitHub is hours late they land after midnight. The job sets
`TZ=America/Chicago`, so report dates and file names are Chicago dates. Do not cut the
schedule back to a single cron line.

### Guards against invented content (keep these if you change curate.py)
- Claude can only pick PMIDs from the candidate list; others are dropped and noted.
- Title, authors, journal, links are filled from PubMed data, never model output.
- Every number in a Design or Results line must appear in the abstract; misses are listed in the
  report's Verification Notes.
- Context lines come from model knowledge and are labelled as not machine-verified.
- Each paper appears in one section only (curation order: cardiology, AI, AI med ed,
  longevity, general medicine).

## The weekly lesson (`educate.py`)

Josh's request (2026-11-23): a separate email, sent at the same time, for continuing
education. Each week find a gap in his knowledge suggested by what is being published
and teach it from the fundamentals: what it is, how it works, why it matters, what the
trials show, what is available, caveats. His examples: inflammation in cardiovascular
disease (after a negative trial at grand rounds), antisense oligonucleotides and RNA
therapeutics, gene therapy.

How it works, three steps after `curate.py`:
1. Claude reads the finished review, titles of the unselected candidates, past topics
   (`education/history.json`) and `education.requested_topics`, and returns a topic,
   the questions to answer, and up to 8 PubMed searches.
2. The searches run against all of PubMed (no date limit, best match, top 10 each).
3. Claude writes the lesson from those abstracts as structured JSON; `render_lesson`
   turns it into markdown with numbered references.

Guards: citations, key trials and further reading must be PMIDs the searches returned
(others are removed and noted); bibliographic details come from PubMed; Key Trials
numbers are checked against the abstract; the teaching prose is labelled as model
knowledge, not machine-verified.

The lesson step is `continue-on-error`, so a failure never blocks the review email; a
final step then turns the run red so GitHub notifies Josh. The workflow commits
`education/history.json` and `archive/<year>/weekly-lesson-YYYY-MM-DD.pdf`.

To test without sending the review: Run workflow with "Lesson only" ticked
(`gh workflow run weekly-review.yml -f lesson_only=true`; add `-f send_email=false
-f archive=false` for a dry run whose lesson is saved only as a run artifact).

## Running it

**Normal:** it runs itself on Fridays. To run on demand: GitHub -> Actions ->
"Weekly literature review" -> Run workflow, or `gh workflow run weekly-review.yml`
(add `-f send_email=false` to skip the email; the report is saved as a run artifact).

**Locally:** `collect.py` works in the existing `.venv` (Python 3.9):
```bash
.venv/bin/python collect.py            # or --days 14
.venv/bin/python collect.py --show 43001763   # full record for a PMID
```
`curate.py` and `educate.py` need Python 3.10+ (the `anthropic` library requires it) and
`ANTHROPIC_API_KEY`; the Mac's system Python is 3.9 and the local `.env` has no API key, so they normally run
only in the cloud.

## Configuration (all in `config.yaml`)
- `reader_profile`, and per-section `guidance`: plain-English instructions to Claude.
  **This is where to change what gets picked or how it is written.**
- Per-section `queries`: the PubMed searches. `journal_tiers`: ranking.
- `model`, `effort`, `max_per_section`, `max_also_notable`, `lookback_days`.
- `education`: the lesson's `guidance` and `requested_topics` (each taught once).
- `email`: SMTP server only. The address is the `REVIEW_EMAIL` secret, never in the repo.

## Secrets
GitHub repo secrets: `ANTHROPIC_API_KEY`, `GMAIL_APP_PASSWORD`, `REVIEW_EMAIL`. Locally the
same values live in `.env` (gitignored; see `.env.example`). Never commit them.

**This repository is public.** Do not commit email addresses, credentials, local file
paths, reports, or collected data. Git commits use the GitHub no-reply address.

## Archive
Each run prints the email's HTML to PDF with headless Chrome and commits it to
`archive/<year>/weekly-review-YYYY-MM-DD.pdf`, and the lesson alongside it as
`weekly-lesson-YYYY-MM-DD.pdf` (Josh asked for this 2026-11-11; these, the slides
and `education/history.json` are the generated outputs committed on purpose). Because the workflow pushes to
`main`, run `git pull --rebase` before pushing local changes.

## Slides
`make_slides.py` builds a plain blank-template deck (python-pptx), one slide per `full`
write-up in the sections listed under `slides.sections` in `config.yaml` (Josh chose AI
in Medicine and AI in Medical Education only, 2026-11-11). It reads
`reports/selection_YYYY-MM-DD.json`, which `curate.py` writes alongside the report. The
deck is attached to the email and committed to `slides/<year>/`. No deck in a week with
no full write-ups in those sections.

## Failure signals
- GitHub emails Josh when a workflow run fails.
- Partial failures (one section, a PubMed query) do not stop the email; they appear in
  the report's Verification Notes.
- Cost was roughly $1-2 of API usage per weekly run before the lesson; the lesson adds
  two more Claude calls (estimate $1-2; check the token counts printed in the run log).

## Also in this repo (older, unchanged)
`retrospective_collect.py` and `retrospective_curate_prep.py`: a month-by-month PubMed
retrospective pipeline (topics `ai_medicine`, `cardiology`, `longevity`,
`heart_failure`) that prepares batch files in `data/` for curation inside a Claude Code
session. Not part of the weekly job.

## History
The original version (Mar-Jun 2026) ran `claude -p` from a launchd job on the Mac. It
failed repeatedly: unattended permission prompts, then macOS blocking background access
to `~/Documents`. Replaced 2026-11-11 with the API + GitHub Actions design above.
