#!/usr/bin/env python3
"""
KMIT Result Notifier (Cloud Version)
=====================================
Checks the KMIT exam results page for B.Tech 2 Year 2 Sem KR24 results
and sends notifications via Telegram + Gmail when found.

Designed to run as a GitHub Actions cron job.
"""

import os
import sys
import re
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError
from html.parser import HTMLParser


# ─────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────

RESULTS_URL = "https://portal.teleuniv.in/exam/resultshome"

# Telegram config (from environment / GitHub Secrets)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Gmail config (from environment / GitHub Secrets)
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_RECIPIENTS = os.environ.get("GMAIL_RECIPIENTS", "")  # comma-separated

# Target regulation
TARGET_REGULATION = "KR24"

# Flexible patterns — matches any result with "2 Year" and "2 Sem"
YEAR_PATTERN = re.compile(r'\b2\s*(year|yr)\b', re.IGNORECASE)
SEM_PATTERN = re.compile(r'\b2\s*(sem|semester)\b', re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────
# SIMPLE HTML PARSER (no external dependencies needed)
# ─────────────────────────────────────────────────────────────────

class ResultsParser(HTMLParser):
    """Parse the KMIT results page HTML to extract exam entries."""

    def __init__(self):
        super().__init__()
        self.results = []
        self._current_row = None
        self._in_row = False
        self._capture_text = False
        self._current_text = ""
        self._current_exam_name = ""
        self._current_date = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        # Detect table rows with regulation data
        if tag == "tr" and "data-regulation" in attrs_dict:
            self._current_row = {
                "regulation": attrs_dict.get("data-regulation", ""),
                "publish_id": attrs_dict.get("data-publish-id", ""),
            }
            self._in_row = True
            self._current_exam_name = ""
            self._current_date = ""

        # Capture exam name from hidden input
        if self._in_row and tag == "input":
            if attrs_dict.get("name") == "examname":
                self._current_exam_name = attrs_dict.get("value", "")

        # Capture date from badge span
        if self._in_row and tag == "span":
            classes = attrs_dict.get("class", "")
            if "date-badge" in classes:
                self._capture_text = True
                self._current_text = ""

    def handle_data(self, data):
        if self._capture_text:
            self._current_text += data

    def handle_endtag(self, tag):
        if tag == "span" and self._capture_text:
            self._capture_text = False
            # Extract date (remove icon text)
            self._current_date = re.sub(r'^[^\d]*', '', self._current_text).strip()

        if tag == "tr" and self._in_row and self._current_row:
            self._current_row["name"] = self._current_exam_name
            self._current_row["published_date"] = self._current_date
            self.results.append(self._current_row)
            self._current_row = None
            self._in_row = False


# ─────────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def matches_target(exam_name: str) -> bool:
    """Check if the exam name matches: any KR24 result with 2 Year + 2 Sem."""
    has_year = bool(YEAR_PATTERN.search(exam_name))
    has_sem = bool(SEM_PATTERN.search(exam_name))
    return has_year and has_sem


def fetch_page() -> str:
    """Fetch the results page HTML."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    req = Request(RESULTS_URL, headers=headers)
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_results(html: str) -> list:
    """Parse HTML and return list of result dicts."""
    parser = ResultsParser()
    parser.feed(html)
    return parser.results


def send_telegram(message: str):
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Telegram credentials not set. Skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }).encode("utf-8")

    try:
        req = Request(url, data=data, method="POST")
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print("✅ Telegram notification sent!")
                return True
            else:
                print(f"❌ Telegram API error: {result}")
                return False
    except Exception as e:
        print(f"❌ Failed to send Telegram message: {e}")
        return False


def send_email(exam_name: str, published_date: str):
    """Send email notification via Gmail SMTP."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD or not GMAIL_RECIPIENTS:
        print("⚠️  Gmail credentials not set. Skipping email.")
        return False

    recipients = [r.strip() for r in GMAIL_RECIPIENTS.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["From"] = f"KMIT Result Notifier <{GMAIL_ADDRESS}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"🎓 RESULTS OUT: {exam_name}"

    text_body = (
        f"KMIT Results Released!\n\n"
        f"Exam: {exam_name} Examination Results\n"
        f"Published: {published_date}\n\n"
        f"View Results: {RESULTS_URL}\n\n"
        f"Go check your results!"
    )

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #ffa000, #14777f); color: white; padding: 20px; border-radius: 10px 10px 0 0; text-align: center;">
            <h1 style="margin: 0;">🎓 Results Released!</h1>
        </div>
        <div style="background: #f9f9f9; padding: 20px; border: 1px solid #ddd; border-radius: 0 0 10px 10px;">
            <h2 style="color: #333; margin-top: 0;">{exam_name}</h2>
            <p style="color: #666;">📅 Published: <strong>{published_date}</strong></p>
            <a href="{RESULTS_URL}" style="display: inline-block; background: #007bff; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; font-weight: bold; margin-top: 10px;">View Results →</a>
            <p style="color: #999; margin-top: 20px; font-size: 12px;">Sent by KMIT Result Notifier</p>
        </div>
    </div>
    """

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, recipients, msg.as_string())
        print(f"✅ Email sent to {', '.join(recipients)}!")
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False


def check_results() -> bool:
    """
    Main check function.
    Returns True if target result was found.
    """
    print("🔍 Checking KMIT results page...")

    try:
        html = fetch_page()
    except Exception as e:
        print(f"❌ Failed to fetch page: {e}")
        return False

    all_results = parse_results(html)
    kr24_results = [r for r in all_results if r["regulation"] == TARGET_REGULATION]

    print(f"   Found {len(all_results)} total results, {len(kr24_results)} KR24 results")

    # Log current KR24 results
    if kr24_results:
        print("   Current KR24 results:")
        for r in kr24_results:
            marker = "👉" if matches_target(r["name"]) else "  "
            print(f"   {marker} • {r['name']} (Published: {r['published_date']})")

    # Check for target
    matching = [r for r in kr24_results if matches_target(r["name"])]

    if matching:
        print(f"\n🎉 FOUND {len(matching)} matching result(s)!")

        for r in matching:
            # Send Telegram notification
            tg_message = (
                "🎓 <b>KMIT Results Released!</b>\n\n"
                f"📋 <b>{r['name']} Examination Results</b>\n"
                f"📅 Published: {r['published_date']}\n\n"
                f"🔗 <a href='{RESULTS_URL}'>View Results</a>\n\n"
                "Go check your results! 🚀"
            )
            send_telegram(tg_message)

            # Send email notification
            send_email(r['name'], r['published_date'])

        return True
    else:
        print("   ⏳ Target result (2 Year 2 Sem KR24) not yet published.")
        return False


# ─────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    found = check_results()

    if found:
        print("\n✅ Result found! Notification sent.")
        # Write flag file so GitHub Actions can detect and disable the workflow
        flag_file = os.environ.get("GITHUB_OUTPUT", "")
        if flag_file:
            with open(flag_file, "a") as f:
                f.write("result_found=true\n")
    else:
        print("\n⏳ Not yet. Will check again next cycle.")

    sys.exit(0)
