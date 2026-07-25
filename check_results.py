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
import ssl
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

# Flexible patterns — matches any result with "2 Year" and "2 Sem" or "II Year II Sem" or "2-2"
YEAR_PATTERN = re.compile(r'(\b2\s*(year|yr|nd)\b|\bii\s*(year|yr)\b|\b2-2\b)', re.IGNORECASE)
SEM_PATTERN = re.compile(r'(\b2\s*(sem|semester|nd)\b|\bii\s*(sem|semester)\b|\b2-2\b)', re.IGNORECASE)


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
        self._capture_date_text = False
        self._capture_title_text = False
        self._current_date_text = ""
        self._current_title_text = ""
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
            self._current_title_text = ""

        # Capture exam name from hidden input
        if self._in_row and tag == "input":
            if attrs_dict.get("name") == "examname":
                self._current_exam_name = attrs_dict.get("value", "")

        # Capture text inside exam title elements as fallback
        if self._in_row and (tag == "h4" or "exam-title" in attrs_dict.get("class", "")):
            self._capture_title_text = True
            self._current_title_text = ""

        # Capture date from badge span
        if self._in_row and tag == "span":
            classes = attrs_dict.get("class", "")
            if "date-badge" in classes:
                self._capture_date_text = True
                self._current_date_text = ""

    def handle_data(self, data):
        if self._capture_date_text:
            self._current_date_text += data
        if self._capture_title_text:
            self._current_title_text += data

    def handle_endtag(self, tag):
        if tag == "span" and self._capture_date_text:
            self._capture_date_text = False
            self._current_date = re.sub(r'^[^\d]*', '', self._current_date_text).strip()

        if (tag == "h4" or tag == "button") and self._capture_title_text:
            self._capture_title_text = False

        if tag == "tr" and self._in_row and self._current_row:
            exam_name = self._current_exam_name or self._current_title_text.strip()
            self._current_row["name"] = exam_name
            self._current_row["published_date"] = self._current_date
            self.results.append(self._current_row)
            self._current_row = None
            self._in_row = False


# ─────────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def matches_target(exam_name: str) -> bool:
    """Check if the exam name matches: any KR24 result with 2 Year + 2 Sem."""
    if re.search(r'\b2-2\b', exam_name, re.IGNORECASE):
        return True
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


def get_all_telegram_chats() -> list:
    """Fetch all unique chat IDs that have interacted with the bot."""
    chat_ids = set()
    if TELEGRAM_CHAT_ID:
        for cid in TELEGRAM_CHAT_ID.split(","):
            if cid.strip():
                chat_ids.add(cid.strip())

    if not TELEGRAM_BOT_TOKEN:
        return list(chat_ids)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        ctx = ssl._create_unverified_context()
        req = Request(url)
        with urlopen(req, context=ctx, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                for update in data.get("result", []):
                    msg = update.get("message") or update.get("edited_message") or {}
                    chat = msg.get("chat", {})
                    if "id" in chat:
                        chat_ids.add(str(chat["id"]))
    except Exception as e:
        print(f"⚠️  Could not fetch Telegram getUpdates: {e}")

    return list(chat_ids)


def send_telegram(message: str):
    """Send a message via Telegram Bot API to all active bot chats."""
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️  Telegram bot token not set. Skipping notification.")
        return False

    chat_ids = get_all_telegram_chats()
    if not chat_ids:
        print("⚠️  No Telegram chat IDs found. Skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ctx = ssl._create_unverified_context()
    success_count = 0

    for cid in chat_ids:
        data = urlencode({
            "chat_id": cid,
            "text": message,
            "parse_mode": "HTML",
        }).encode("utf-8")

        try:
            req = Request(url, data=data, method="POST")
            with urlopen(req, context=ctx, timeout=15) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    print(f"✅ Telegram notification sent to chat {cid}!")
                    success_count += 1
                else:
                    print(f"❌ Telegram API error for {cid}: {result}")
        except Exception as e:
            print(f"❌ Failed to send Telegram message to {cid}: {e}")

    return success_count > 0


def send_email(exam_name: str, published_date: str):
    """Send email notification via Gmail SMTP."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD or not GMAIL_RECIPIENTS:
        print("⚠️  Gmail credentials not set. Skipping email.")
        return False

    recipients = [r.strip() for r in GMAIL_RECIPIENTS.split(",") if r.strip()]

    text_body = (
        f"{exam_name} Examination Results have been published on {published_date}.\n\n"
        f"Check your results here:\n"
        f"{RESULTS_URL}\n\n"
        f"— KMIT Exam Portal"
    )

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 520px; margin: 20px auto; color: #1a1a1a;">
        <div style="background: #14777f; padding: 16px 24px; border-radius: 8px 8px 0 0;">
            <h2 style="margin: 0; color: #fff; font-size: 18px; font-weight: 600;">KMIT Exam Results</h2>
        </div>
        <div style="padding: 24px; background: #ffffff; border: 1px solid #e0e0e0; border-top: none; border-radius: 0 0 8px 8px;">
            <p style="margin: 0 0 16px; font-size: 15px; line-height: 1.5;">
                <strong>{exam_name}</strong> Examination Results have been published on <strong>{published_date}</strong>.
            </p>
            <p style="margin: 0 0 20px; font-size: 15px;">
                <a href="{RESULTS_URL}" style="color: #14777f; font-weight: 600;">Check your results here &rarr;</a>
            </p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
            <p style="margin: 0; font-size: 13px; color: #888;">
                KMIT Exam Portal &middot; Keshav Memorial Institute of Technology
            </p>
        </div>
    </div>
    """

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            for recipient in recipients:
                msg = MIMEMultipart("alternative")
                msg["From"] = f"KMIT Results <{GMAIL_ADDRESS}>"
                msg["To"] = recipient
                msg["Subject"] = f"{exam_name} - Results Published"
                msg.attach(MIMEText(text_body, "plain"))
                msg.attach(MIMEText(html_body, "html"))
                server.sendmail(GMAIL_ADDRESS, [recipient], msg.as_string())
        print(f"✅ Emails sent individually to {len(recipients)} recipients!")
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
