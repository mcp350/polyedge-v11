"""
MODULE 5 — News Catalyst Alert
Monitors Google News RSS for breaking news on your open positions.
"""

import re
import requests
import hashlib
import portfolio_store as store
import telegram_client as tg

HEADERS = {"User-Agent": "PolymarketBot/1.0"}
_alerted_hashes: set = set()

TRIGGER_WORDS = [
    "strike","attack","bomb","missile","invade","invasion","ceasefire",
    "signed","military","troops","coup","sanctions","nuclear","war",
    "confirmed","breaking","official","pentagon","kremlin","emergency"
]

def _keywords(question: str) -> list:
    stop = {"will","the","a","an","by","in","of","to","be","on","at","for","or","and","is"}
    words = re.sub(r"[^a-zA-Z0-9 ]", "", question).split()
    kws = [w for w in words if w.lower() not in stop and len(w) > 3]
    return kws[:4]

def _rss(query: str) -> list:
    try:
        r = requests.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers=HEADERS, timeout=10
        )
        if not r.ok:
            return []
        items = []
        for m in re.finditer(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", r.text):
            t = (m.group(1) or m.group(2) or "").strip()
            if t and "Google News" not in t:
                items.append(t)
        return items[:5]
    except Exception:
        return []

def run_news_check():
    positions = store.get_positions()
    if not positions:
        return
    print(f"[NEWS] Checking {len(positions)} positions")

    for mid, pos in positions.items():
        q      = pos.get("question", "")
        entry  = pos.get("entry_price", 0)
        url    = pos.get("url", "")
        kw     = " ".join(_keywords(q))
        if not kw:
            continue

        for title in _rss(kw):
            h = hashlib.md5(f"{mid}:{title}".encode()).hexdigest()
            if h in _alerted_hashes:
                continue
            if not any(tw in title.lower() for tw in TRIGGER_WORDS):
                continue

            _alerted_hashes.add(h)
            tg.send(
                f"⚠️ <b>NEWS CATALYST</b>\n\n"
                f"📌 {q[:60]}\n"
                f"NO entry: ${entry:.2f}\n\n"
                f"📰 <b>{title[:120]}</b>\n\n"
                f"Check if this triggers resolution\n"
                f"🔗 <a href=\"{url}\">Open Market</a>"
            )
            print(f"[NEWS] Alert: {title[:60]}")
            break
