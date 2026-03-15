"""Portfolio storage — reads/writes portfolio.json"""
import json, os
from datetime import datetime, timezone

FILE = os.path.join(os.path.dirname(__file__), "portfolio.json")

def _load():
    if not os.path.exists(FILE):
        return {"positions": {}, "seen_markets": [], "swing_alerts": {}, "price_snapshots": {}}
    with open(FILE) as f:
        return json.load(f)

def _save(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

def add_position(mid, question, entry, size, url, end_date):
    d = _load()
    d["positions"][mid] = {"question": question, "entry_price": entry, "size_usd": size,
        "url": url, "end_date": end_date, "added_at": datetime.now(timezone.utc).isoformat()}
    _save(d)

def remove_position(mid):
    d = _load()
    if mid in d["positions"]:
        del d["positions"][mid]; _save(d); return True
    return False

def get_positions():
    return _load().get("positions", {})

def mark_seen(mid):
    d = _load()
    if mid not in d.get("seen_markets", []):
        d.setdefault("seen_markets", []).append(mid)
        d["seen_markets"] = d["seen_markets"][-500:]
        _save(d)

def is_seen(mid):
    return mid in _load().get("seen_markets", [])

def get_last_swing_alert(mid):
    return _load().get("swing_alerts", {}).get(mid, "")

def set_swing_alert_time(mid):
    d = _load()
    d.setdefault("swing_alerts", {})[mid] = datetime.now(timezone.utc).isoformat()
    _save(d)

def save_price_snapshot(mid, no_price):
    d = _load()
    snaps = d.setdefault("price_snapshots", {}).setdefault(mid, [])
    snaps.append({"price": no_price, "ts": datetime.now(timezone.utc).isoformat()})
    d["price_snapshots"][mid] = snaps[-20:]
    _save(d)

def get_price_snapshots(mid):
    return _load().get("price_snapshots", {}).get(mid, [])
