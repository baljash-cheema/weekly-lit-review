# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A weekly literature review for Josh Cheema (heart failure / advanced transplant
cardiologist). Every Friday morning it emails him the most important new
studies in five areas. It runs unattended on GitHub Actions and calls the Claude API
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
- Delivered **Friday 7:30 am Central** to the address in the `REVIEW_EMAIL` secret.

## Pipeline

```
collect.py   PubMed searches per section (last 7 days by PubMed entry date)
             -> filter out editorials/letters/case reports, rank by journal tier + design
             -> data/candidates.json  (+ data/candidates_<section>.md, human-readable)
curate.py    One Claude API call per section: choose + write up; one more for the opening
             -> reports/review_YYYY-MM-DD.md
send_email.py  Markdown -> HTML, Gmail SMTP
```

Scheduled by `.github/workflows/weekly-review.yml` (two cron lines, one per
daylight-saving state; a gate step keeps the one that is 6:55 am in Chicago).

### Guards against invented content (keep these if you change curate.py)
- Claude can only pick PMIDs from the candidate list; others are dropped and noted.
- Title, authors, journal, links are filled from PubMed data, never model output.
- Every number in a Design or Results line must appear in the abstract; misses are listed in the
  report's Verification Notes.
- Context lines come from model knowledge and are labelled as not machine-verified.
- Each paper appears in one section only (curation order: cardiology, AI, AI med ed,
  longevity, general medicine).

## Running it

**Normal:** it runs itself on Fridays. To run on demand: GitHub -> Actions ->
"Weekly literature review" -> Run workflow, or `gh workflow run weekly-review.yml`
(add `-f send_email=false` to skip the email; the report is saved as a run artifact).

**Locally:** `collect.py` works in the existing `.venv` (Python 3.9):
```bash
.venv/bin/python collect.py            # or --days 14
.venv/bin/python collect.py --show 43001763   # full record for a PMID
```
`curate.py` needs Python 3.10+ (the `anthropic` library requires it) and
`ANTHROPIC_API_KEY`; the Mac's system Python is 3.9, so curate normally runs only in
the cloud.

## Configuration (all in `config.yaml`)
- `reader_profile`, and per-section `guidance`: plain-English instructions to Claude.
  **This is where to change what gets picked or how it is written.**
- Per-section `queries`: the PubMed searches. `journal_tiers`: ranking.
- `model`, `effort`, `max_per_section`, `max_also_notable`, `lookback_days`.
- `email`: SMTP server only. The address is the `REVIEW_EMAIL` secret, never in the repo.

## Secrets
GitHub repo secrets: `ANTHROPIC_API_KEY`, `GMAIL_APP_PASSWORD`, `REVIEW_EMAIL`. Locally the
same values live in `.env` (gitignored; see `.env.example`). Never commit them.

**This repository is public.** Do not commit email addresses, credentials, local file
paths, reports, or collected data. Git commits use the GitHub no-reply address.

## Failure signals
- GitHub emails Josh when a workflow run fails.
- Partial failures (one section, a PubMed query) do not stop the email; they appear in
  the report's Verification Notes.
- Cost is roughly $1-2 of API usage per weekly run.

## Also in this repo (older, unchanged)
`retrospective_collect.py` and `retrospective_curate_prep.py`: a month-by-month PubMed
retrospective pipeline (topics `ai_medicine`, `cardiology`, `longevity`,
`heart_failure`) that prepares batch files in `data/` for curation inside a Claude Code
session. Not part of the weekly job.

## History
The original version (Mar-Jun 2026) ran `claude -p` from a launchd job on the Mac. It
failed repeatedly: unattended permission prompts, then macOS blocking background access
to `~/Documents`. Replaced 2026-11-11 with the API + GitHub Actions design above.
