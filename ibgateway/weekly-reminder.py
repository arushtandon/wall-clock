#!/usr/bin/env python3
"""
Weekly IBKR login reminder. Run via cron once per week (e.g. Sunday 9am).
Sends a single notification if prices appear stale, so you only need to log in when reminded.
Set NOTIFY_* env vars (same as server.py) for Telegram and/or email.
"""
import os
import sys
import time
import urllib.request
import urllib.parse

# Same env vars as server.py
NOTIFY_EMAIL = os.environ.get('NOTIFY_EMAIL', '')
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
NOTIFY_NOVNC_URL = os.environ.get('NOTIFY_NOVNC_URL', 'https://safronliveprices.duckdns.org/novnc/vnc.html')
STATUS_URL = os.environ.get('WALLCLOCK_STATUS_URL', 'http://127.0.0.1:8080/api/status')
STALE_SEC = 30 * 60  # Consider "stale" if no update in 30 minutes


def send(msg):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = "https://api.telegram.org/bot%s/sendMessage?chat_id=%s&text=%s" % (
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, urllib.parse.quote(msg)
            )
            urllib.request.urlopen(url, timeout=10)
            print("Sent Telegram", flush=True)
        except Exception as e:
            print("Telegram failed:", e, flush=True)
    if NOTIFY_EMAIL and SMTP_USER and SMTP_PASS:
        try:
            import smtplib
            from email.mime.text import MIMEText
            m = MIMEText(msg)
            m['Subject'] = "Weekly: IBKR login reminder - Wall Clock"
            m['From'] = SMTP_USER
            m['To'] = NOTIFY_EMAIL
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_USER, NOTIFY_EMAIL, m.as_string())
            print("Sent email", flush=True)
        except Exception as e:
            print("Email failed:", e, flush=True)


def main():
    try:
        r = urllib.request.urlopen(STATUS_URL, timeout=5)
        data = r.read().decode()
        import json
        j = json.loads(data)
        ib_connected = j.get('ib_connected', False)
        last_update = j.get('last_update', 0)
        now = time.time()
        if ib_connected and last_update and (now - last_update) < STALE_SEC:
            # All good, no reminder
            return 0
    except Exception as e:
        print("Status check failed:", e, flush=True)
    msg = (
        "Weekly reminder: IBKR login may be required if prices have stopped. "
        "Log in via noVNC (no need to open IBKR website): %s"
    ) % NOTIFY_NOVNC_URL
    send(msg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
