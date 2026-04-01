"""
Polymarket API wrapper — Gamma REST API
Docs: https://docs.polymarket.com

NOTE: Railway blocks *.polymarket.com HTTPS at the Python level.
The gamma_get() function uses curl subprocess to bypass this.
All Gamma API calls MUST use gamma_get() or _get() (which auto-detects).
"""

import requests
import subprocess
import json as _json
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode
import config

GAMMA_BASE  = "https://gamma-api.polymarket.com"
CLOB_BASE   = config.CLOB_BASE  # Routed through EU proxy when CLOB_PROXY_URL is set
DATA_BASE   = "https://data-api.polymarket.com"

HEADERS = {"User-Agent": "PolymarketBot/1.0"}


def gamma_get(path: str, params: dict = None, timeout: int = 15) -> Optional[list | dict]:
    ""
    Fetch from Gamma API using curl subprocess.
    Railway blocks *.polymarket.com at DNS/network level.
    We resolve the domain to Cloudflare IPs directly to bypass.
    """
    url = f"{GAMMA_BASE}{path}"
    if params:
        qs = urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{url}?{qs}"

    # Railway blocks *.polymarket.com DNS. Try multiple bypass methods:
    # 1. Direct DNS resolve to known Cloudflare IPs
    # 2. Fallback to system DNS (may work if only Python libs are blocked)
    bypass_methods = [
        # Method 1: Resolve gamma-api.polymarket.com to Cloudflare (1.1.1.1 DNS lookup)
        ['curl', '-s', '-L', '--max-time', str(timeout),
         '--resolve', 'gamma-api.polymarket.com:443:104.18.0.0',
         '-H', 'Accept: application/json',
         '-H', 'User-Agent: PolymarketBot/1.0', url],
        # Method 2: Use DNS-over-HTTPS to resolve, connect directly
        ['curl', '-s', '-L', '--max-time', str(timeout),
         '--doh-url', 'https://1.1.1.1/dns-query',
         '-H', 'Accept: application/json',
         '-H', 'User-Agent: PolymarketBot/1.0', url],
        # Method 3: Plain curl (fallback)
        ['curl', '-s', '-L', '--max-time', str(timeout),
         '-H', 'Accept: application/json',
         '-H', 'User-Agent: PolymarketBot/1.0', url],
    ]

    for i, cmd in enumerate(bypass_methods):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
            if proc.returncode == 0 and proc.stdout and proc.stdout.strip()[:1] in '[{':
                raw = proc.stdout.strip()
                result, _ = _json.JSONDecoder().raw_decode(raw)
                print(f"[GAMMA_GET] Method {i+1} succeeded for {path}")
                return result
            else:
                snippet = (proc.stdout or '')[:100]
                print(f"[GAMMA_GET] Method {i+1} failed: rc={proc.returncode} body={snippet}")
        except Exception as e:
            print(f"[GAMMA_GET] Method {i+1} error: {e}")
            continue

    # All methods failed — try the EU proxy as last resort
    try:
        proxy_url = f"http://13.49.25.66{path}"
        if params:
            proxy_url = f"{proxy_url}?{qs}"
        proc = subprocess.run(
            ['curl', '-s', '-L', '--max-time', str(timeout),
             '-H', 'Host: gamma-api.polymarket.com',
             '-H', 'Accept: application/json',
             '-H', 'User-Agent: PolymarketBot/1.0', proxy_url],
            capture_output=True, text=True, timeout=timeout + 5
        )
        if proc.returncode == 0 and proc.stdout and proc.stdout.strip()[:1] in '[{':
            raw = proc.stdout.strip()
            result, _ = _json.JSONDecoder().raw_decode(raw)
            print(f"[GAMMA_GET] EU proxy method succeeded for {path}")
            return result
    except Exception as e:
        print(f"[GAMMA_GET] EU proxy fallback error: {e}")

    print(f"[GAMMA_GET] All methods failed for {path}")
    return None


def _get(url: str, params: dict = None, retries: int = 3) -> Optional[dict]:
    # Auto-detect Gamma API calls and route through curl
    if "gamma-api.polymarket.com" in url:
        path = url.replace(GAMMA_BASE, "")
        return gamma_get(path, params)

    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"[API ERROR] {url} — {e}")
                return None
            time.sleep(2 ** attempt)

def get_markets(limit: int = 100, offset: int = 0, active: bool = True) -> list:
    """Fetch active markets from Gamma API."""
    data = _get(f"{GAMMA_BASE}/markets", params={
        "limit": limit,
        "offset": offset,
        "active": "true" if active else "false",
        "closed": "false",
        "order": "volume",
        "ascending": "false"
    })
    if isinstance(data, list):
        return data
    return []

def get_market_by_slug(slug: str) -> Optional[dict]:
    """Fetch a single market by slug or condition_id."""
    data = _get(f"{GAMMA_BASE}/markets", params={"slug": slug})
    if isinstance(data, list) and data:
        return data[0]
    return None

def get_market_by_id(market_id: str) -> Optional[dict]:
    data = _get(f"{GAMMA_BASE}/markets/{market_id}")
    return data

def get_events(limit: int = 50, offset: int = 0) -> list:
    """Fetch events (grouped markets)."""
    data = _get(f"{GAMMA_BASE}/events", params={
        "limit": limit,
        "offset": offset,
        "active": "true",
        "closed": "false",
        "order": "volume",
        "ascending": "false"
    })
    if isinstance(data, list):
        return data
    return []

def parse_market(m: dict) -> Optional[dict]:
    """
    Normalize a raw market dict into clean fields.
    Returns None if market doesn't have required fields.
    """
    try:
        import json

        question = m.get("question", "") or m.get("title", "")
        end_date_str = m.get("endDate") or m.get("end_date_iso") or ""
        volume = float(m.get("volume", 0) or 0)
        liquidity = float(m.get("liquidity", 0) or 0)

        # Parse outcome prices — format: '["0.15", "0.85"]'
        outcome_prices = m.get("outcomePrices", "[]")
        if isinstance(outcome_prices, str):
            try:
                prices = json.loads(outcome_prices)
            except Exception:
                prices = []
        else:
            prices = outcome_prices or []

        if len(prices) < 2:
            return None

        yes_price = float(prices[0])
        no_price  = float(prices[1])

        # Days until expiry
        days_left = None
        if end_date_str:
            try:
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                now    = datetime.now(timezone.utc)
                days_left = max(0, (end_dt - now).days)
            except Exception:
                pass

        market_id  = m.get("id") or m.get("conditionId", "")
        slug       = m.get("slug", "")
        url        = f"https://polymarket.com/event/{slug}" if slug else ""
        tags       = [t.get("label", "").lower() for t in (m.get("tags") or []) if isinstance(t, dict)]

        return {
            "id":         market_id,
            "question":   question,
            "yes_price":  round(yes_price, 4),
            "no_price":   round(no_price, 4),
            "volume":     volume,
            "liquidity":  liquidity,
            "end_date":   end_date_str,
            "days_left":  days_left,
            "slug":       slug,
            "url":        url,
            "tags":       tags,
            "raw":        m
        }
    except Exception as e:
        print(f"[PARSE ERROR] {e}")
        return None

def get_price_history(market_id: str, interval: str = "1h") -> list:
    """
    Fetch recent price history for a market.
    Returns list of {t: timestamp, p: price} dicts.
    """
    data = _get(f"{CLOB_BASE}/prices-history", params={
        "market": market_id,
        "interval": interval,
        "fidelity": 10
    })
    if data and "history" in data:
        return data["history"]
    return []

def get_recent_trades(market_id: str, limit: int = 50) -> list:
    """Fetch recent trades for whale detection."""
    data = _get(f"{DATA_BASE}/trades", params={
        "market": market_id,
        "limit": limit
    })
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return []
