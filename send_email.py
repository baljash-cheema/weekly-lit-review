"""
send_email.py — Sends the weekly review by Gmail, or saves it as a web page.

Usage:
    python send_email.py reports/review_2026-11-13.md                 # send the email
    python send_email.py reports/review_2026-11-13.md --attach deck.pptx
    python send_email.py reports/review_2026-11-13.md --html out.html # just save the HTML
                                                                      # (used to make the PDF)

Needs REVIEW_EMAIL and GMAIL_APP_PASSWORD (environment or .env) to send.
"""

import argparse
import os
import smtplib
import sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import markdown
import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

SUBJECT = "Weekly Lit Review Agent"

STYLE = """
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
           max-width: 800px; margin: 0 auto; padding: 20px; color: #333; }
    h1 { color: #1a5276; border-bottom: 2px solid #1a5276; padding-bottom: 8px; }
    h2 { color: #1a5276; margin-top: 36px; border-bottom: 1px solid #d5dde5; padding-bottom: 4px; }
    h3 { font-size: 16px; line-height: 1.35; margin: 26px 0 4px; }
    h3 a { text-decoration: none; }
    p { line-height: 1.5; margin: 6px 0; }
    em { color: #5d6d7e; }
    a { color: #2980b9; }
    table { border-collapse: collapse; width: 100%; margin: 15px 0; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background-color: #f2f2f2; }
    blockquote { border-left: 3px solid #ccc; padding-left: 12px; color: #666; }
    @page { margin: 18mm 16mm; }
    @media print { body { padding: 0; font-size: 11pt; } h2 { break-after: avoid; } h3 { break-after: avoid; } }
"""


def load_config():
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def render_html(report_md):
    body = markdown.markdown(report_md, extensions=["tables", "fenced_code"])
    return (f'<html>\n<head>\n<meta charset="utf-8">\n<title>Weekly Literature Review</title>\n'
            f"<style>{STYLE}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n")


def send_report(report_md, attachments=()):
    email_cfg = load_config()["email"]
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    # The Gmail account that sends the report; it also receives it unless
    # REVIEW_EMAIL_TO names a different recipient.
    sender = os.getenv("REVIEW_EMAIL", "").strip()
    recipient = os.getenv("REVIEW_EMAIL_TO", "").strip() or sender
    if not sender:
        sys.exit("ERROR: REVIEW_EMAIL not set (environment or .env)")
    if not app_password:
        sys.exit("ERROR: GMAIL_APP_PASSWORD not set (environment or .env)\n"
                 "Setup: myaccount.google.com → Security → 2-Step Verification → App passwords")

    body = MIMEMultipart("alternative")
    body.attach(MIMEText(report_md, "plain", "utf-8"))
    body.attach(MIMEText(render_html(report_md), "html", "utf-8"))

    msg = MIMEMultipart("mixed")
    msg["Subject"] = SUBJECT
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(body)
    for path in attachments:
        part = MIMEApplication(Path(path).read_bytes(), Name=Path(path).name)
        part["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
        msg.attach(part)
        print(f"Attached {Path(path).name}")

    print("Sending report...")
    with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
        server.starttls()
        server.login(sender, app_password)
        server.send_message(msg)
    print("Sent successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the weekly review, or save it as HTML.")
    parser.add_argument("report", help="path to the markdown report")
    parser.add_argument("--html", metavar="PATH", help="save the formatted page here instead of emailing")
    parser.add_argument("--attach", metavar="FILE", nargs="*", default=[],
                        help="files to attach (missing files are skipped)")
    args = parser.parse_args()

    text = Path(args.report).read_text(encoding="utf-8")
    if args.html:
        Path(args.html).write_text(render_html(text), encoding="utf-8")
        print(f"Saved {args.html}")
    else:
        send_report(text, [f for f in args.attach if Path(f).is_file()])
