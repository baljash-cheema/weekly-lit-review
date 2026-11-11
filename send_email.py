"""
send_email.py — Sends the weekly review report via Gmail SMTP.

Reads the latest markdown report from reports/, converts to HTML, and sends.

Usage:
    python send_email.py reports/review_2026-03-01.md
"""

import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import markdown
import yaml
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")


def load_config():
    with open(BASE_DIR / "config.yaml") as f:
        return yaml.safe_load(f)


def send_report(report_path):
    config = load_config()
    email_cfg = config["email"]
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    # The Gmail account that sends the report; it also receives it unless
    # REVIEW_EMAIL_TO names a different recipient.
    sender = os.getenv("REVIEW_EMAIL", "").strip()
    recipient = os.getenv("REVIEW_EMAIL_TO", "").strip() or sender
    if not sender:
        print("ERROR: REVIEW_EMAIL not set (environment or .env)")
        sys.exit(1)

    if not app_password:
        print("ERROR: GMAIL_APP_PASSWORD not set (environment or .env)")
        print("Setup: myaccount.google.com → Security → 2-Step Verification → App passwords")
        sys.exit(1)

    # Read markdown
    report_md = Path(report_path).read_text(encoding="utf-8")

    subject = "Weekly Lit Review Agent"

    # Convert to HTML
    html_body = markdown.markdown(
        report_md,
        extensions=["tables", "fenced_code"],
    )

    # Wrap in basic email styling
    html_full = f"""
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   max-width: 800px; margin: 0 auto; padding: 20px; color: #333; }}
            h1 {{ color: #1a5276; border-bottom: 2px solid #1a5276; padding-bottom: 8px; }}
            h2 {{ color: #1a5276; margin-top: 36px; border-bottom: 1px solid #d5dde5; padding-bottom: 4px; }}
            h3 {{ font-size: 16px; line-height: 1.35; margin: 26px 0 4px; }}
            h3 a {{ text-decoration: none; }}
            p {{ line-height: 1.5; margin: 6px 0; }}
            em {{ color: #5d6d7e; }}
            a {{ color: #2980b9; }}
            table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            blockquote {{ border-left: 3px solid #ccc; padding-left: 12px; color: #666; }}
        </style>
    </head>
    <body>
    {html_body}
    </body>
    </html>
    """

    # Build email
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    msg.attach(MIMEText(report_md, "plain", "utf-8"))
    msg.attach(MIMEText(html_full, "html", "utf-8"))

    # Send
    print("Sending report...")
    with smtplib.SMTP(email_cfg["smtp_server"], email_cfg["smtp_port"]) as server:
        server.starttls()
        server.login(sender, app_password)
        server.send_message(msg)
    print("Sent successfully.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python send_email.py <path-to-report.md>")
        sys.exit(1)
    send_report(sys.argv[1])
