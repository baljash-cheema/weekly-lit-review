# Weekly Literature Review Agent

A small, unattended program that emails one physician a curated review of the week's
most important medical literature every Friday morning.

It searches PubMed for everything published that week in a set of topic areas, asks
Claude to pick the handful of studies that genuinely matter and explain why, and sends
the result as a formatted email. It runs on GitHub's servers on a schedule, so no
computer needs to be on.

I'm a heart failure and transplant cardiologist, not a software developer. I built this
with [Claude Code](https://claude.com/claude-code), and the repository is public so
others can copy it and point it at their own field.

## What the email looks like

Five sections, each opening with a short paragraph on how the field is shifting. The
strongest studies get a full write-up:

> ### Title of the paper (linked to PubMed)
> *Journal · Authors · Study design and size* · DOI
>
> **Area:** the disease area or technology under study
>
> **Design:** who was studied, what was compared, the primary endpoint, follow-up
>
> **Results:** the primary result with event rates and effect size, then the secondary
> and safety findings that matter, all taken from the abstract
>
> **Context:** the major takeaway, then how it sits against what was already known,
> what it settles or reopens, its main limitation, and what it means for practice

Studies worth knowing about but of more modest quality or impact get two lines instead.
A section can also be set to a "themes" style, which gives a paragraph on the week's
major themes and one line per study.

My sections are cardiology (with extra weight on heart failure, transplant and
hypertrophic cardiomyopathy), AI in medicine, AI in medical education, longevity
medicine, and a themes digest of general medicine. All of that is configuration, not
code.

## Design choices

**The most important studies, not a quota.** An earlier version returned a fixed top 10
every week, which meant filler in slow weeks. Now each section has a ceiling of five
full write-ups and no floor. A quiet week in a thin field produces one paper or none.

**Context is the point.** A list of titles is easy to get anywhere. What I wanted was a
few lines on each study placing it against the prior evidence, the way a well-read
colleague would.

**Breadth first, then my own niche.** The review covers all of medicine and all of
cardiology, with only a modest thumb on the scale for my subspecialty, so that I keep
seeing what is happening outside it.

**Guardrails against invented content.** Language models can produce confident errors,
so the program limits where that can happen:

- Claude can only choose papers from the list PubMed returned that week. Anything else
  is dropped.
- Titles, authors, journals and links are copied from PubMed, never written by the model.
- Every number in a Design or Results line is checked against the abstract. Any that don't appear
  there are listed at the bottom of the email.
- The Context lines come from the model's own knowledge of the literature and are
  **not** machine-verified. The email says so. Treat them as a colleague's take and
  check anything you plan to act on.

**PubMed only.** Earlier versions also pulled from news, social media and preprint
feeds. That added noise without adding important studies, so it was removed.

**Runs in the cloud, on the API.** The first version ran on my laptop through a
scheduled Claude Code session. It failed for months for reasons that had nothing to do
with the review itself: the laptop was asleep, a permission prompt was waiting with
nobody there, the operating system blocked background access to a folder. This version
is a plain Python program that calls the Claude API and runs on GitHub Actions.

**Instructions in plain English.** Who the reader is and what each section should favor
are ordinary paragraphs in `config.yaml`. Changing what gets picked means editing a
sentence, not code.

## How it works

```
collect.py      Searches PubMed for each section (papers added in the last 7 days),
                drops editorials, letters and case reports, and ranks what is left by
                journal and study design.
curate.py       Sends each section's candidates to Claude, which selects and writes
                up the studies. Runs the checks above and assembles the report.
send_email.py   Converts the report to a formatted email and sends it through Gmail.
```

`.github/workflows/weekly-review.yml` runs those three steps every Friday, then saves a
PDF copy of the review to `archive/`.

## Set up your own

You need a GitHub account, a Claude API key, and a Gmail account. Running it costs
roughly $1 to $2 of Claude API usage per week at the default settings; GitHub Actions
is free for public repositories.

### 1. Copy the repository

Fork it on GitHub (the **Fork** button), or clone it and push it to a new repository
of your own:

```bash
git clone https://github.com/baljash-cheema/weekly-lit-review.git
cd weekly-lit-review
```

### 2. Get the three values it needs

| Name | What it is | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Lets the program call Claude | [console.anthropic.com](https://console.anthropic.com) → API keys → Create key |
| `REVIEW_EMAIL` | The Gmail address that sends the review, and receives it | Your own address |
| `GMAIL_APP_PASSWORD` | Lets the program send mail from that account | [myaccount.google.com](https://myaccount.google.com) → Security → 2-Step Verification (must be on) → App passwords |

An app password is a separate 16-character password for one program. It is not your
normal Gmail password, and you can revoke it at any time.

To send to a different address than the one sending, also set `REVIEW_EMAIL_TO`.

### 3. Add them to GitHub as secrets

In your repository: **Settings → Secrets and variables → Actions → New repository
secret**, once for each name above. Secrets are encrypted, hidden from logs, and not
shared with forks. Never put these values in a file you commit.

### 4. Make it yours

Open `config.yaml` and edit:

- `reader_profile`: who the review is for.
- `sections`: each has a `title`, a `guidance` paragraph telling Claude what to favor,
  an optional `style` (`detailed` or `themes`), and one or more PubMed `queries`. The
  report prints sections in the order they appear. Add, remove or rewrite sections freely. PubMed
  queries use the same syntax as the PubMed website's search box.
- `journal_tiers`: which journals rank highest.
- `max_per_section`, `lookback_days`, `model`.

The schedule is in `.github/workflows/weekly-review.yml`. It is set for Friday at about
7:30 am US Central time. GitHub schedules use UTC and ignore daylight saving, which is
why there are two schedule lines and a first step that picks the right one. For a
different time zone, change both the `cron` lines and the zone names in that step.

### 5. Run it

In your repository: **Actions → Weekly literature review → Run workflow**. In a fork,
GitHub asks you to enable Actions first. The run takes a few minutes and the email
arrives at the end. After that it runs by itself every week, and GitHub emails you if a
run fails.

### Running on your own computer (optional)

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the three values; .env is never committed
python collect.py
python curate.py
python send_email.py reports/review_*.md
```

## Things to know

- **It reads abstracts, not full papers.** Findings are limited to what the abstract
  reports.
- **It is a reading aid, not medical advice**, and not a substitute for reading the
  papers that matter to your practice.
- **Every review is archived as a PDF** in [`archive/`](archive/), committed by the
  weekly run. If your repository is public, so is the archive. To keep reviews private,
  make the repository private or remove the "Save a PDF copy" step from the workflow.
- GitHub may start scheduled runs several minutes late.
- `retrospective_collect.py` and `retrospective_curate_prep.py` are an older, separate
  tool for month-by-month look-backs and are not part of the weekly run.

## License

MIT. See [LICENSE](LICENSE). You are free to use, copy and adapt this.
