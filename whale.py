"""
MODULE 4 — Whale Alert
Monitors tracked positions for large YES buys.
"""

import polymarket_api as api
import portfolio_store as store
import telegram_client as tg
from config import WHALE_TRADE_USD

def _check_market(market_id: str, question: str, pos: dict):
    trades = api.get_recent_trades(market_id, limit=50)
    if not trades:
        return

    for trade in trades:
        try:
            amount = float(trade.get("size") or trade.get("usdcSize") or trade.get("amount") or 0)
            side   = (trade.get("side") or trade.get("outcome") or "").lower()

            if amount < WHALE_TRADE_USD:
                continue
            if "yes" not in side and "buy" not in side:
                continue

            entry = pos.get("entry_price", 0)
            size  = pos.get("size_usd", 0)
            url   = pos.get("url", "")

            tg.send(
                f"🐋 <b>WHALE ACTIVITY</b>\n\n"
                f"📌 {question[:60]}\n"
                f"(Your portfolio — NO at ${entry:.2f})\n\n"
                f"Wallet bought: <b>${amount:,.0f} YES</b>\n\n"
                f"⚠️ Recommend: monitor next 30 min\n"
                f"Your entry: ${entry:.2f} | Size: ${size}\n"
                f"🔗 <a href=\"{url}\">Open Market</a>"
            )
            print(f"[WHALE] Alert: ${amount:,.0f} YES on {question[:50]}")
            break
        except Exception as e:
            print(f"[WHALE] {e}")

def run_whale_check():
    positions = store.get_positions()
    if not positions:
        return
    print(f"[WHALE] Checking {len(positions)} positions")
    for mid, pos in positions.items():
        _check_market(mid, pos.get("question", "Unknown"), pos)
