"""
Telegram client — sends messages to your chat.
"""

import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def send(text: str, chat_id: str = TELEGRAM_CHAT_ID, parse_mode: str = "HTML") -> bool:
    """Send a message. Falls back to plain text if HTML parse fails."""
    if not text or not text.strip():
        return False
    # Truncate to Telegram's 4096 char limit
    if len(text) > 4090:
        text = text[:4090] + "..."
    try:
        r = requests.post(f"{BASE_URL}/sendMessage", json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=15)
        if r.ok:
            return True
        # HTML parse failed — strip tags and retry as plain text
        print(f"[TELEGRAM] HTML send failed ({r.status_code}), retrying as plain text...")
        import re
        plain = re.sub(r"<[^>]+>", "", text)
        r2 = requests.post(f"{BASE_URL}/sendMessage", json={
            "chat_id":    chat_id,
            "text":       plain,
            "disable_web_page_preview": True
        }, timeout=15)
        if r2.ok:
            return True
        print(f"[TELEGRAM] Plain text also failed: {r2.text[:200]}")
        return False
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

def send_alert(emoji: str, title: str, lines: list, chat_id: str = TELEGRAM_CHAT_ID) -> bool:
    """Structured alert formatter."""
    body = "\n".join(lines)
    text = f"{emoji} <b>{title}</b>\n\n{body}"
    return send(text, chat_id)

def test_connection() -> bool:
    """Verify bot token is valid."""
    try:
        r = requests.get(f"{BASE_URL}/getMe", timeout=10)
        if r.ok:
            name = r.json().get("result", {}).get("username", "?")
            print(f"[TELEGRAM] Connected as @{name}")
            return True
        return False
    except Exception as e:
        print(f"[TELEGRAM] Connection failed: {e}")
        return False
