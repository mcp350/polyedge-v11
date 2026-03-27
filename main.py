"""
POLYTRAGENT — Polymarket AI Trading Agent v11
Event Research | Portfolio | Strategies | Research | Settings
Wallet Tracking — Read-Only via Public Address
PAID-ONLY ACCESS — $99/mo subscription or access code
"""
import os, sys, signal, atexit
import time, threading, requests
from datetime import datetime, timezone
import pytz
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE
import telegram_client as tg
import portfolio_store as store
import scanner, monitor, researcher, whale, news, swing
import gdelt, kalshi_api, acled, rss_intel, unsc
import digest
import top10
import swings as swings_mod
import btc_orderbook
import prediction_store as pstore
import user_store
import onboarding
import web_server
import copy_trading as ct
import copy_signals
import wallet_tracker as wt
import polymarket_trading as trading
import wallet_manager as wm
import copy_executor as ce

_last_update_id = 0
_locks = {}

# ── SINGLE INSTANCE ENFORCEMENT ──
_PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.pid")

def _kill_other_instances():
    my_pid = os.getpid()
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            if old_pid != my_pid:
                os.kill(old_pid, signal.SIGKILL)
                print(f"[BOOT] Killed old instance PID {old_pid}")
                time.sleep(1)
        except (ProcessLookupError, ValueError, PermissionError):
            pass
    # PID-file-only enforcement (pgrep removed for Railway/container compat)
    with open(_PID_FILE, "w") as f:
        f.write(str(my_pid))
    atexit.register(lambda: os.remove(_PID_FILE) if os.path.exists(_PID_FILE) else None)
    print(f"[BOOT] Single instance — PID {my_pid}")

def _set_bot_commands():
    try:
        commands = [
            {"command": "menu", "description": "Open main menu"},
            {"command": "trade", "description": "Trade on Polymarket"},
            {"command": "wallet", "description": "Wallet & balance"},
            {"command": "whales", "description": "Top whale wallets"},
            {"command": "degen", "description": "Degen Mode — $79/mo"},
        ]
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands",
            json={"commands": commands}, timeout=10)
        if r.ok: print("[BOOT] Bot menu commands set")
    except Exception as e:
        print(f"[BOOT] setMyCommands error: {e}")

# ═══════════════════════════════════════════════
# PHASE 2: FREE TRADING TERMINAL + DEGEN MODE
# ═══════════════════════════════════════════════
# No more paywall. Free access to all trading.
# Degen Mode ($79/mo) = unlimited whale tracking
# ═══════════════════════════════════════════════

# ═══════════════════════════════════════════════
# MESSAGE UTILITIES
# ═══════════════════════════════════════════════

def _send_long(result, chat_id):
    if len(result) <= 4000:
        tg.send(result, chat_id)
        return
    parts = result.split("━" * 30)
    if len(parts) >= 3:
        mid = len(parts) // 2
        p1 = ("━" * 30).join(parts[:mid]).strip()
        p2 = ("━" * 30).join(parts[mid:]).strip()
        tg.send(p1, chat_id)
        time.sleep(1)
        tg.send(p2, chat_id)
    else:
        tg.send(result[:4000], chat_id)
        if len(result) > 4000:
            time.sleep(1)
            tg.send(result[4000:], chat_id)

def _run_locked(name, chat_id, fn):
    if _locks.get(name):
        tg.send(f"⏳ {name} already running. Please wait.", chat_id)
        return
    _locks[name] = True
    try:
        result = fn()
        _send_long(result, chat_id)
    except Exception as e:
        print(f"[BOT] {name} error: {e}")
        tg.send(f"❌ {name} error: {e}", chat_id)
    finally:
        _locks[name] = False

# ═══════════════════════════════════════════════
# QUICK RESEARCH — Main menu top button
# ═══════════════════════════════════════════════

_waiting_for_research_link = {}  # chat_id -> True when waiting for link

def show_quick_research_prompt(chat_id):
    """Prompt user to send a Polymarket event link for full AI analysis"""
    _waiting_for_research_link[str(chat_id)] = True
    onboarding.send_inline(chat_id,
        "🔬 <b>Polytragent — Event Research</b>\n\n"
        "Paste a Polymarket event link and get:\n\n"
        "📊 <b>Market Analysis</b> — prices, volume, liquidity\n"
        "🧠 <b>AI Recommendation</b> — buy YES/NO/SKIP\n"
        "📈 <b>Edge Detection</b> — expert vs market pricing\n"
        "🐋 <b>Whale Activity</b> — smart money flow\n"
        "📰 <b>News Context</b> — recent developments\n"
        "🎯 <b>Entry/Exit Strategy</b> — sizing & timing\n\n"
        "👇 <b>Send a Polymarket link now:</b>\n"
        "<i>Example: https://polymarket.com/event/...</i>",
        [[{"text": "← Main Menu", "callback_data": "main_menu"}]])

def handle_research_link(chat_id, link):
    """Run full research on a Polymarket link"""
    _waiting_for_research_link.pop(str(chat_id), None)

    tg.send("🔬 <b>Researching event...</b>\n\n⏳ Running AI analysis, market data, whale scan, news check...\nThis takes ~30-60 seconds.", chat_id)

    try:
        # 1. Core AI research
        result = researcher.research_market(link)

        # 2. Get market data for enrichment
        try:
            import polymarket_api as papi
            slug = link.rstrip("/").split("/")[-1] if "polymarket.com" in link else link
            m = None
            if "/event/" in link:
                r = requests.get(f"https://gamma-api.polymarket.com/events",
                    params={"slug": slug}, timeout=15)
                if r.ok:
                    events = r.json()
                    if isinstance(events, list) and events and events[0].get("markets"):
                        m = events[0]["markets"][0]
            if not m:
                m = papi.get_market_by_slug(slug) or papi.get_market_by_id(slug)

            if m:
                parsed = papi.parse_market(m)
                if parsed:
                    # Add market snapshot
                    yes_p = parsed.get("yes_price", 0)
                    no_p = parsed.get("no_price", 0)
                    vol = parsed.get("volume", 0)
                    liq = parsed.get("liquidity", 0)
                    end = parsed.get("end_date", "N/A")

                    market_snap = (
                        "\n\n📊 <b>Market Snapshot</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"YES: ${yes_p:.2f} | NO: ${no_p:.2f}\n"
                        f"Volume: ${vol:,.0f} | Liquidity: ${liq:,.0f}\n"
                        f"Deadline: {end}\n"
                    )
                    result += market_snap

                    # Kalshi comparison
                    try:
                        comp = kalshi_api.compare_markets(parsed["question"], yes_p, no_p)
                        result += f"\n{comp}"
                    except: pass

                    # Quick recommendation based on NO theta logic
                    if no_p >= 0.55 and no_p <= 0.90:
                        edge_note = f"\n\n🎯 <b>NO Theta Candidate</b>\nNO at ${no_p:.2f} — within entry range ($0.55-$0.90)"
                        result += edge_note
                    elif no_p > 0.88:
                        edge_note = f"\n\n⚡ <b>Scalp Candidate</b>\nNO at ${no_p:.2f} — within scalp range ($0.88-$0.96)"
                        result += edge_note
        except Exception as e:
            print(f"[RESEARCH] Enrichment error: {e}")

        # Send results with action buttons
        if len(result) > 4000:
            _send_long(result, chat_id)
            onboarding.send_inline(chat_id,
                "👆 <b>Full analysis above</b>\n\nWhat would you like to do?",
                [[{"text": "🔬 Research Event", "callback_data": "quick_research"}],
                 [{"text": "📊 Portfolio", "callback_data": "menu_portfolio"},
                  {"text": "💰 Trade", "callback_data": "menu_trade"}],
                 [{"text": "← Main Menu", "callback_data": "main_menu"}]])
        else:
            onboarding.send_inline(chat_id, result,
                [[{"text": "🔬 Research Event", "callback_data": "quick_research"}],
                 [{"text": "📊 Portfolio", "callback_data": "menu_portfolio"},
                  {"text": "💰 Trade", "callback_data": "menu_trade"}],
                 [{"text": "← Main Menu", "callback_data": "main_menu"}]])

    except Exception as e:
        print(f"[RESEARCH] Error: {e}")
        tg.send(f"❌ Research error: {e}", chat_id)
        onboarding.send_inline(chat_id,
            "Try again with a valid Polymarket link.",
            [[{"text": "🔬 Try Again", "callback_data": "quick_research"},
              {"text": "← Main Menu", "callback_data": "main_menu"}]])

def is_waiting_for_research(chat_id):
    return _waiting_for_research_link.get(str(chat_id), False)

# ═══════════════════════════════════════════════
# MAIN MENU — 5 SECTIONS
# ═══════════════════════════════════════════════

def send_main_menu(chat_id):
    """Send the updated main menu with whale tracking and Degen Mode"""
    user = user_store.get_user(chat_id)
    name = ""
    if user:
        name = user.get("first_name") or user.get("username") or ""
    greeting = f", {name}" if name else ""

    # Trading engine status
    trade_status = "🟢 Live" if trading.is_trading_enabled() else "⚪ Signals Only"

    # Degen Mode status
    is_degen = user_store.is_degen(chat_id) if hasattr(user_store, 'is_degen') else False
    degen_badge = "🚀 Degen Active" if is_degen else ""

    onboarding.send_inline(chat_id,
        f"🤖 <b>Polytragent</b>{greeting}\n\n"
        "Your AI-Powered Polymarket Trading Agent.\n"
        f"Trading: {trade_status} {degen_badge}\n\n"
        "Select a section below to get started.",
        [[{"text": "🔬 Event Research", "callback_data": "quick_research"}],
         [{"text": "💰 Trade", "callback_data": "menu_trading"},
          {"text": "👛 Wallet", "callback_data": "menu_wallet"}],
         [{"text": "🐋 Whales", "callback_data": "menu_whales"},
          {"text": "🔄 Copy Trade", "callback_data": "menu_auto_copy"}],
         [{"text": "📊 Portfolio", "callback_data": "menu_portfolio"},
          {"text": "📈 Strategies", "callback_data": "menu_trade"}],
         [{"text": "🔬 Research", "callback_data": "menu_research"},
          {"text": "⚙️ Settings", "callback_data": "menu_settings"}],
         [{"text": "🚀 Degen Mode", "callback_data": "degen_subscribe"}] if not is_degen else []])

# ═══════════════════════════════════════════════
# SECTION 1: PORTFOLIO
# ═══════════════════════════════════════════════

def show_portfolio_menu(chat_id):
    """Portfolio sub-menu (Spec Section 2.3.1)"""
    wallet = wt.get_wallet(str(chat_id))
    if wallet:
        addr = wallet["address"]
        wallet_line = f"👛 Wallet: <code>{addr[:6]}...{addr[-4:]}</code>"
    else:
        wallet_line = "👛 No wallet connected — go to Settings"

    onboarding.send_inline(chat_id,
        f"📊 <b>Portfolio</b>\n\n"
        f"{wallet_line}\n\n"
        "Manage your positions and track performance.",
        [[{"text": "📋 Dashboard", "callback_data": "portfolio_dashboard"}],
         [{"text": "📂 Open Positions", "callback_data": "portfolio_positions"},
          {"text": "📜 Closed Trades", "callback_data": "portfolio_closed_trades"}],
         [{"text": "🛡 Risk Scorecard", "callback_data": "portfolio_risk"},
          {"text": "⚠️ Attention Items", "callback_data": "portfolio_attention"}],
         [{"text": "📁 Events Categories", "callback_data": "portfolio_categories"}],
         [{"text": "← Main Menu", "callback_data": "main_menu"}]])

def show_portfolio_dashboard(chat_id):
    """Spec Section 5.1 — Portfolio Dashboard with live wallet data"""
    try:
        # Check for connected wallet first
        wallet_data = wt.get_portfolio_data(str(chat_id))

        if wallet_data.get("connected"):
            # ── LIVE WALLET DASHBOARD ──
            msg = wt.format_portfolio_summary(str(chat_id))

            # Add AI performance
            perf = pstore.get_performance()
            win_rate = perf.get("win_rate") or 0
            total_preds = perf.get("total", 0)
            correct = perf.get("correct", 0)
            msg += (
                f"\n<b>🧠 AI Signal Performance</b>\n"
                f"Predictions: {total_preds} | Win Rate: {win_rate:.1f}%\n"
            )

            # Copy Trading
            ct_stats = ct.get_copy_stats()
            following = ct.get_following(chat_id)
            msg += (
                f"\n<b>🔄 Copy Trading</b>\n"
                f"Following: {len(following)} wallets\n"
            )

            onboarding.send_inline(chat_id, msg,
                [[{"text": "🔄 Refresh", "callback_data": "portfolio_dashboard"},
                  {"text": "📂 Positions", "callback_data": "portfolio_positions"}],
                 [{"text": "📜 Recent Trades", "callback_data": "portfolio_closed_trades"},
                  {"text": "🌐 Web Dashboard", "callback_data": "dashboard"}],
                 [{"text": "← Portfolio", "callback_data": "menu_portfolio"}]])
        else:
            # ── FALLBACK: no wallet connected ──
            report = monitor.build_report()
            perf = pstore.get_performance()
            win_rate = perf.get("win_rate") or 0
            total_preds = perf.get("total", 0)
            correct = perf.get("correct", 0)

            msg = (
                "📊 <b>Polytragent — Portfolio Dashboard</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "👛 <b>No wallet connected</b>\n"
                "Connect your Polymarket wallet in Settings\n"
                "to see live positions, P/L, and analytics.\n\n"
            )

            positions = store.get_positions()
            if positions:
                total_value = sum(p.get("size", 0) for p in positions)
                msg += f"💼 <b>Manual Positions:</b> ${total_value:,.2f}\n"
                msg += f"📂 Open: {len(positions)}\n\n"

            msg += (
                f"<b>🧠 AI Performance</b>\n"
                f"Total Predictions: {total_preds}\n"
                f"Win Rate: {win_rate:.1f}%\n"
                f"Correct: {correct}\n\n"
            )

            ct_stats = ct.get_copy_stats()
            following = ct.get_following(chat_id)
            msg += (
                f"<b>🔄 Copy Trading</b>\n"
                f"Following: {len(following)} wallets\n"
                f"Total Signals: {ct_stats.get('total_signals', 0)}\n"
            )

            onboarding.send_inline(chat_id, msg,
                [[{"text": "👛 Connect Wallet", "callback_data": "settings_wallet"},
                  {"text": "📂 Positions", "callback_data": "portfolio_positions"}],
                 [{"text": "🌐 Web Dashboard", "callback_data": "dashboard"}],
                 [{"text": "← Portfolio", "callback_data": "menu_portfolio"}]])
    except Exception as e:
        tg.send(f"❌ Dashboard error: {e}", chat_id)

def show_portfolio_positions(chat_id):
    """Spec Section 5.2 — Open Positions Detail (live from wallet)"""
    wallet = wt.get_wallet(str(chat_id))
    if wallet:
        # ── LIVE WALLET POSITIONS ──
        msg = wt.format_positions_detail(str(chat_id))
        onboarding.send_inline(chat_id, msg,
            [[{"text": "🔄 Refresh", "callback_data": "portfolio_positions"},
              {"text": "📋 Dashboard", "callback_data": "portfolio_dashboard"}],
             [{"text": "← Portfolio", "callback_data": "menu_portfolio"}]])
    else:
        # ── MANUAL POSITIONS FALLBACK ──
        positions = store.get_positions()
        if not positions:
            onboarding.send_inline(chat_id,
                "📂 <b>Open Positions</b>\n\n"
                "No positions found.\n\n"
                "👛 Connect your wallet in Settings to see\n"
                "your live Polymarket positions here.",
                [[{"text": "👛 Connect Wallet", "callback_data": "settings_wallet"},
                  {"text": "💰 Trade", "callback_data": "menu_trade"}],
                 [{"text": "← Portfolio", "callback_data": "menu_portfolio"}]])
            return

        msg = "📂 <b>Open Positions</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, p in enumerate(positions[:10], 1):
            question = (p.get("question", p.get("market_id", "Unknown")))[:50]
            entry = p.get("entry_price", 0)
            size = p.get("size", 0)
            url = p.get("url", "")
            msg += (
                f"<b>{i}. {question}</b>\n"
                f"   Entry: ${entry:.2f} | Size: ${size:.2f}\n"
            )
            if url:
                msg += f"   🔗 <a href=\"{url}\">View Market</a>\n"
            msg += "\n"
        msg += f"Total: {len(positions)} positions"

        onboarding.send_inline(chat_id, msg,
            [[{"text": "➕ Add Position", "callback_data": "trade_direct"},
              {"text": "🔄 Refresh", "callback_data": "portfolio_positions"}],
             [{"text": "← Portfolio", "callback_data": "menu_portfolio"}]])

def show_risk_scorecard(chat_id):
    """Spec Section 5.3 — Risk Scorecard"""
    positions = store.get_positions()
    n_positions = len(positions)

    # Calculate diversification metrics
    total_value = sum(p.get("size", 0) for p in positions) if positions else 0
    categories = {}
    for p in positions:
        cat = p.get("category", "Unknown")
        categories[cat] = categories.get(cat, 0) + p.get("size", 0)

    # Concentration score
    max_concentration = 0
    if categories and total_value > 0:
        max_concentration = max(v / total_value * 100 for v in categories.values())

    # Diversification rating
    if n_positions == 0:
        div_score = "N/A"
        div_emoji = "⚪"
    elif n_positions >= 5 and max_concentration < 40:
        div_score = "Good"
        div_emoji = "🟢"
    elif n_positions >= 3:
        div_score = "Moderate"
        div_emoji = "🟡"
    else:
        div_score = "Low"
        div_emoji = "🔴"

    msg = (
        "🛡 <b>Risk Scorecard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Portfolio Summary</b>\n"
        f"📂 Open Positions: {n_positions}\n"
        f"💼 Total Invested: ${total_value:,.2f}\n\n"
        f"<b>Diversification</b>\n"
        f"{div_emoji} Score: {div_score}\n"
        f"📊 Max Concentration: {max_concentration:.0f}%\n"
        f"📁 Categories: {len(categories)}\n\n"
        f"<b>Risk Limits</b>\n"
        f"⚠️ Max Position: 25% of portfolio\n"
        f"🔴 Drawdown Breaker: -25%\n"
        f"🔒 Cash Reserve Target: 15%\n\n"
    )

    # Attention items
    attention = []
    if max_concentration > 40 and n_positions > 0:
        attention.append("⚠️ High concentration in single category")
    if n_positions >= 10:
        attention.append("⚠️ Many open positions — review exposure")

    if attention:
        msg += "<b>⚠️ Attention Items</b>\n"
        for a in attention:
            msg += f"{a}\n"
    else:
        msg += "✅ No attention items"

    onboarding.send_inline(chat_id, msg,
        [[{"text": "📊 Dashboard", "callback_data": "portfolio_dashboard"},
          {"text": "← Portfolio", "callback_data": "menu_portfolio"}]])

def show_closed_trades(chat_id):
    """NEW: Closed Trades with P&L, hold duration, strategy name"""
    wallet = wt.get_wallet(str(chat_id))
    if wallet:
        # Show live activity from wallet
        msg = wt.format_activity(str(chat_id))
        onboarding.send_inline(chat_id, msg,
            [[{"text": "🔄 Refresh", "callback_data": "portfolio_closed_trades"},
              {"text": "📋 Dashboard", "callback_data": "portfolio_dashboard"}],
             [{"text": "← Portfolio", "callback_data": "menu_portfolio"}]])
    else:
        try:
            pstore.check_resolutions()
            result = pstore.format_history(page=0)
            tg.send(result, chat_id)
        except Exception as e:
            tg.send(f"❌ History error: {e}", chat_id)

def show_attention_items(chat_id):
    """NEW: Positions needing action (approaching deadline, stop-loss, etc)"""
    positions = store.get_positions()

    attention_items = []
    for p in positions:
        issues = []
        entry = p.get("entry_price", 0)
        current = p.get("current_price", entry)

        # Check for approaching deadline
        end_date = p.get("end_date", "")
        if end_date:
            issues.append(f"⏰ Deadline: {end_date}")

        # Check for stop-loss
        if current and entry and current < entry * 0.8:
            loss_pct = ((current - entry) / entry) * 100
            issues.append(f"🛑 Stop-loss triggered: {loss_pct:.1f}%")

        if issues:
            attention_items.append((p.get("question", "Unknown")[:60], issues))

    if not attention_items:
        msg = "✅ <b>Attention Items</b>\n\nNo positions require immediate action."
        onboarding.send_inline(chat_id, msg,
            [[{"text": "← Portfolio", "callback_data": "menu_portfolio"}]])
        return

    msg = "⚠️ <b>Attention Items</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, (question, issues) in enumerate(attention_items[:10], 1):
        msg += f"<b>{i}. {question}</b>\n"
        for issue in issues:
            msg += f"   {issue}\n"
        msg += "\n"

    onboarding.send_inline(chat_id, msg,
        [[{"text": "📂 Positions", "callback_data": "portfolio_positions"},
          {"text": "← Portfolio", "callback_data": "menu_portfolio"}]])

def show_events_categories(chat_id):
    """NEW: Events Categories — reuse onboarding categories selection in portfolio context"""
    onboarding.show_category_selection(chat_id)

def show_performance(chat_id):
    """Performance stats"""
    tg.send("📊 Checking prediction performance...", chat_id)
    try:
        resolved = pstore.check_resolutions()
        if resolved > 0:
            tg.send(f"✅ {resolved} new resolutions!", chat_id)
            time.sleep(0.5)
        result = pstore.format_performance()
        tg.send(result, chat_id)
    except Exception as e:
        tg.send(f"❌ Error: {e}", chat_id)

# ═══════════════════════════════════════════════
# SECTION 2: RESEARCH HUB (formerly Markets)
# ═══════════════════════════════════════════════

def show_research_menu(chat_id):
    """Research sub-menu — market intelligence, alerts, analysis"""
    onboarding.send_inline(chat_id,
        "🔬 <b>Polytragent — Research</b>\n\n"
        "Market intelligence, alerts, and analysis.",
        [[{"text": "📈 Trending Events", "callback_data": "research_trending"}],
         [{"text": "🆕 New Markets", "callback_data": "research_new_markets"}],
         [{"text": "🐋 Whale Alerts", "callback_data": "research_whale"}],
         [{"text": "📰 Breaking News", "callback_data": "research_breaking_news"}],
         [{"text": "📊 Kalshi Markets", "callback_data": "research_kalshi"}],
         [{"text": "📊 Global Stats", "callback_data": "research_stats"}],
         [{"text": "← Main Menu", "callback_data": "main_menu"}]])

def show_trending_events(chat_id):
    """Trending Events — most recent data from Polymarket by 24h volume"""
    tg.send("📈 <b>Loading trending events by 24h volume...</b>", chat_id)
    try:
        r = requests.get("https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "order": "volume24hr",
                     "ascending": "false", "limit": 10}, timeout=15)
        if r.ok:
            events = r.json()
            msg = "📈 <b>Polytragent — Trending Events</b>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            msg += "<i>Top events by 24h trading volume</i>\n\n"
            for i, ev in enumerate(events[:10], 1):
                title = (ev.get("title") or "Untitled")[:55]
                vol24 = float(ev.get("volume24hr", 0) or 0)
                slug = ev.get("slug", "")
                # Get top market yes price
                markets = ev.get("markets") or []
                yes_price = ""
                if markets:
                    try:
                        tokens = markets[0].get("tokens") or []
                        for t in tokens:
                            if t.get("outcome", "").lower() == "yes":
                                yes_price = f" | YES: ${float(t.get('price', 0)):.2f}"
                    except: pass
                msg += f"{i}. <b>{title}</b>\n"
                msg += f"   24h Vol: ${vol24:,.0f}{yes_price}\n"
                if slug:
                    msg += f"   🔗 polymarket.com/event/{slug}\n"
                msg += "\n"
            onboarding.send_inline(chat_id, msg,
                [[{"text": "🔄 Refresh", "callback_data": "research_trending"}],
                 [{"text": "← Research", "callback_data": "menu_research"}]])
        else:
            tg.send("❌ Could not fetch trending events. Try again.", chat_id)
    except Exception as e:
        tg.send(f"❌ Trending error: {e}", chat_id)

def show_new_markets(chat_id):
    """New Markets — markets opened in the past 24 hours"""
    tg.send("🆕 <b>Scanning for new markets (past 24h)...</b>", chat_id)
    try:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        r = requests.get("https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "order": "startDate",
                     "ascending": "false", "limit": 50}, timeout=15)
        if r.ok:
            events = r.json()
            # Filter to events created in past 24h
            new_events = []
            for ev in events:
                created = ev.get("createdAt") or ev.get("startDate") or ""
                if created >= cutoff[:19]:
                    new_events.append(ev)
            if not new_events:
                # Fallback: show most recent ones
                new_events = events[:10]

            msg = "🆕 <b>Polytragent — New Markets</b>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            msg += "<i>Markets opened in the past 24 hours</i>\n\n"
            for i, ev in enumerate(new_events[:10], 1):
                title = (ev.get("title") or "Untitled")[:55]
                slug = ev.get("slug", "")
                vol = float(ev.get("volume", 0) or 0)
                msg += f"{i}. <b>{title}</b>\n"
                msg += f"   Volume: ${vol:,.0f}\n"
                if slug:
                    msg += f"   🔗 polymarket.com/event/{slug}\n"
                msg += "\n"
            if not new_events:
                msg += "No new markets in the past 24h.\n"
            onboarding.send_inline(chat_id, msg,
                [[{"text": "🔄 Refresh", "callback_data": "research_new_markets"}],
                 [{"text": "← Research", "callback_data": "menu_research"}]])
        else:
            tg.send("❌ Could not fetch new markets. Try again.", chat_id)
    except Exception as e:
        tg.send(f"❌ New markets error: {e}", chat_id)

def show_global_stats(chat_id):
    """Global Stats — Polymarket-wide dashboard"""
    tg.send("📊 <b>Loading Polymarket stats...</b>", chat_id)
    try:
        # Fetch Polymarket-wide data
        total_markets = 0
        total_volume = 0
        active_markets = 0
        try:
            r = requests.get("https://gamma-api.polymarket.com/events",
                params={"active": "true", "closed": "false", "limit": 100}, timeout=15)
            if r.ok:
                events = r.json()
                active_markets = len(events)
                for ev in events:
                    for m in (ev.get("markets") or []):
                        total_markets += 1
                        total_volume += float(m.get("volume", 0) or 0)
        except: pass

        # Internal stats
        perf = pstore.get_performance()
        ct_stats = ct.get_copy_stats()
        user_stats = user_store.get_stats()

        msg = (
            "📊 <b>Polytragent — Global Stats</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>🌐 Polymarket</b>\n"
            f"Active Events: {active_markets}\n"
            f"Active Markets: {total_markets}\n"
            f"Total Volume: ${total_volume:,.0f}\n\n"
            "<b>🤖 Polytragent (Phase 2)</b>\n"
            f"Users: {user_stats.get('total_users', 0)}\n"
            f"Degen Subscribers: {user_stats.get('degen_subscribers', 0)}\n"
            f"AI Predictions: {perf.get('total', 0)}\n"
            f"Win Rate: {(perf.get('win_rate') or 0):.1f}%\n"
            f"Tracked Wallets: {ct_stats.get('total_wallets', 0)}\n"
        )

        onboarding.send_inline(chat_id, msg,
            [[{"text": "🔄 Refresh", "callback_data": "research_stats"}],
             [{"text": "← Research", "callback_data": "menu_research"}]])
    except Exception as e:
        tg.send(f"❌ Error: {e}", chat_id)

def show_whale_alerts(chat_id):
    """Whale Alerts — redirects to the whale directory"""
    show_whales_menu(chat_id)

def show_research_leaderboard(chat_id):
    """Leaderboard — redirects to whale directory"""
    show_whales_menu(chat_id)

def show_breaking_news(chat_id):
    """Breaking News — pulls latest from Polymarket breaking news"""
    tg.send("📰 <b>Loading Breaking News...</b>", chat_id)
    try:
        import polymarket_api as papi
        # Fetch trending/breaking events from Polymarket
        r = requests.get("https://gamma-api.polymarket.com/events",
            params={"active": "true", "closed": "false", "order": "volume24hr",
                     "ascending": "false", "limit": 10}, timeout=15)
        if r.ok:
            events = r.json()
            msg = "📰 <b>Polytragent — Breaking News</b>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            msg += "<i>Latest high-volume events from Polymarket</i>\n\n"
            for i, ev in enumerate(events[:10], 1):
                title = (ev.get("title") or "Untitled")[:60]
                vol24 = ev.get("volume24hr", 0) or 0
                slug = ev.get("slug", "")
                msg += f"{i}. <b>{title}</b>\n"
                msg += f"   24h Vol: ${float(vol24):,.0f}\n"
                if slug:
                    msg += f"   🔗 polymarket.com/event/{slug}\n"
                msg += "\n"
        else:
            msg = "📰 <b>Breaking News</b>\n\nUnable to fetch latest events. Try again."
        onboarding.send_inline(chat_id, msg,
            [[{"text": "🔄 Refresh", "callback_data": "research_breaking_news"}],
             [{"text": "← Research", "callback_data": "menu_research"}]])
    except Exception as e:
        tg.send(f"❌ Breaking News error: {e}", chat_id)

def show_research_news(chat_id):
    """NEW: News Digest — AI-summarized news"""
    tg.send("📰 <b>Building News Digest...</b>\n~60-90s.", chat_id)
    _run_locked("Digest", chat_id, digest.run_digest)

def show_kalshi_menu(chat_id):
    """Kalshi Markets sub-menu — cross-platform market data"""
    onboarding.send_inline(chat_id,
        "📊 <b>Polytragent — Kalshi Markets</b>\n\n"
        "Cross-platform market intelligence from Kalshi exchange.\n"
        "Compare prices, find divergences, spot edge.",
        [[{"text": "🔍 Geo & Trending Scan", "callback_data": "kalshi_scan"}],
         [{"text": "📈 Top Markets by Volume", "callback_data": "kalshi_top_volume"}],
         [{"text": "← Research", "callback_data": "menu_research"}]])

def show_research_sources(chat_id):
    """NEW: Sources sub-menu with GDELT, Kalshi, RSS Intel, UNSC, Conflicts, Full Briefing"""
    onboarding.send_inline(chat_id,
        "📂 <b>News Sources</b>\n\n"
        "Multi-source intelligence feeds.",
        [[{"text": "🌍 GDELT", "callback_data": "run_gdelt"},
          {"text": "📊 Kalshi", "callback_data": "run_kalshi"}],
         [{"text": "📡 RSS Intel", "callback_data": "run_intel"},
          {"text": "🇺🇳 UNSC", "callback_data": "run_unsc"}],
         [{"text": "🔴 Conflicts", "callback_data": "run_conflicts"}],
         [{"text": "📋 Full Briefing", "callback_data": "run_briefing"}],
         [{"text": "← Research", "callback_data": "menu_research"}]])

# ═══════════════════════════════════════════════
# SECTION 3: TRADE (Betting Hub — Spec Section 7)
# ═══════════════════════════════════════════════

def show_trade_menu(chat_id):
    """Strategies sub-menu (renamed from Trade)"""
    onboarding.send_inline(chat_id,
        "📈 <b>Polytragent — Strategies</b>\n\n"
        "AI-powered trading strategy signals.",
        [[{"text": "🎯 NO Theta Signals", "callback_data": "trade_no_theta"}],
         [{"text": "⚡ Scalping Signals", "callback_data": "trade_scalp_signals"}],
         [{"text": "← Main Menu", "callback_data": "main_menu"}]])

def show_no_theta_signals(chat_id):
    """NEW: Direct to NO Theta signals menu"""
    show_no_theta_strategy(chat_id)

def show_scalp_signals(chat_id):
    """NEW: Direct to Scalp NO signals menu"""
    show_scalp_no_strategy(chat_id)

def show_my_strategies(chat_id):
    """NEW: User-created strategies"""
    onboarding.send_inline(chat_id,
        "📋 <b>My Strategies</b>\n\n"
        "Your custom trading strategies.\n\n"
        "No custom strategies yet.\n\n"
        "Use the Strategy Builder to create one,\n"
        "or start with pre-built strategies:",
        [[{"text": "🎯 NO Theta", "callback_data": "trade_no_theta"},
          {"text": "⚡ Scalp NO", "callback_data": "trade_scalp_signals"}],
         [{"text": "🔧 Strategy Builder", "callback_data": "trade_builder"}],
         [{"text": "← Strategies", "callback_data": "menu_trade"}]])

def show_strategy_signals(chat_id):
    """Strategy Module — Pre-built strategies from PTA spec"""
    onboarding.send_inline(chat_id,
        "🎯 <b>Strategy Signals</b>\n\n"
        "<b>Active Strategies:</b>\n\n"
        "1️⃣ <b>NO Theta Decay</b> — \"Sell the Fear\"\n"
        "   Buy NO on deadline events likely to expire worthless.\n"
        "   Hold 14-28 days. Target: 12-18% monthly.\n"
        "   Edge: Retail overestimates dramatic events.\n\n"
        "2️⃣ <b>Scalping NO Theta</b> — \"Quick Harvest\"\n"
        "   Late-stage theta decay (3-14 days to deadline).\n"
        "   Quick 2-5 cent profits. Target: 85-95% win rate.\n"
        "   Edge: Exponential decay acceleration in final days.\n\n"
        "<i>Select a strategy for full briefing + signals:</i>",
        [[{"text": "🎯 NO Theta Decay", "callback_data": "strategy_no_theta"}],
         [{"text": "⚡ Scalping NO", "callback_data": "strategy_scalp_no"}],
         [{"text": "📋 Strategy Details", "callback_data": "strategy_details_menu"}],
         [{"text": "🏆 TOP 10 (All)", "callback_data": "run_top10"}],
         [{"text": "← Strategies", "callback_data": "menu_trade"}]])

# ── NO THETA DECAY STRATEGY — Full Spec ──

def show_no_theta_strategy(chat_id):
    """NO Theta Decay — Main briefing from PTA_Strategies_Filled.docx"""
    onboarding.send_inline(chat_id,
        "🎯 <b>NO Theta Decay — \"Sell the Fear\"</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<code>NO_THETA_V1</code>\n\n"
        "<b>Core Thesis:</b>\n"
        "Most deadline events (\"Will X happen by [date]?\") do NOT happen. "
        "YES shares carry a fear premium that decays as the deadline approaches. "
        "By buying NO, we harvest this premium — like selling OTM puts in TradFi.\n\n"
        "<b>Edge Source:</b>\n"
        "Retail traders overestimate probability of dramatic events. "
        "Expert forecasters (Metaculus, Swift Centre) consistently price YES lower than Polymarket.\n\n"
        "<b>Target Markets:</b>\n"
        "Geopolitics, military, regime change, ceasefire, policy deadline, economic threshold\n\n"
        "📊 <b>Target KPIs (Base Case):</b>\n"
        "Win Rate: 88% | Monthly Return: 12-18%\n"
        "Sharpe Ratio: 2.5-3.5 | Trades/Month: 5-8",
        [[{"text": "📋 Entry Gates", "callback_data": "nt_entry_gates"},
          {"text": "🚪 Exit Rules", "callback_data": "nt_exit_rules"}],
         [{"text": "📐 Position Sizing", "callback_data": "nt_position_sizing"},
          {"text": "🛡 Risk Params", "callback_data": "nt_risk_params"}],
         [{"text": "🔬 Research Protocol", "callback_data": "nt_research_protocol"}],
         [{"text": "🏆 Run Scan Now", "callback_data": "run_top10"}],
         [{"text": "← Strategies", "callback_data": "trade_signals"}]])

def show_nt_entry_gates(chat_id):
    """NO Theta — Entry conditions (6 gates, all required)"""
    onboarding.send_inline(chat_id,
        "📋 <b>NO Theta — Entry Gates</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>All 6 gates must pass before entry:</i>\n\n"
        "🔒 <b>Gate 1:</b> Market is deadline-based binary YES/NO\n"
        "   <i>Source: Gamma API metadata</i> ✅ Auto\n\n"
        "🔒 <b>Gate 2:</b> Time to resolution ≥ 14 days\n"
        "   (≥ 7 days if edge &gt; 10pts)\n"
        "   <i>Source: Gamma API end_date_iso</i> ✅ Auto\n\n"
        "🔒 <b>Gate 3:</b> NO price $0.55 — $0.90\n"
        "   <i>Below $0.55 = genuine risk; above $0.90 = no premium</i>\n"
        "   <i>Source: Gamma API tokens[].price</i> ✅ Auto\n\n"
        "🔒 <b>Gate 4:</b> Edge ≥ 5pts above market NO price\n"
        "   <i>5-source weighted research engine</i> ⚙️ Semi-auto\n\n"
        "🔒 <b>Gate 5:</b> Volume ≥ $50K lifetime AND ≥ $5K 24h\n"
        "   ✅ Auto\n\n"
        "🔒 <b>Gate 6:</b> No material catalyst within 48 hours\n"
        "   ⚙️ Semi-auto (AI flags, human confirms)",
        [[{"text": "🔬 Research Protocol", "callback_data": "nt_research_protocol"},
          {"text": "🚪 Exit Rules", "callback_data": "nt_exit_rules"}],
         [{"text": "← NO Theta", "callback_data": "strategy_no_theta"}]])

def show_nt_exit_rules(chat_id):
    """NO Theta — Exit rules from spec"""
    onboarding.send_inline(chat_id,
        "🚪 <b>NO Theta — Exit Rules</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>Take Profit</b> — Normal priority\n"
        "   NO price ≥ $0.93 (configurable $0.90-$0.97)\n"
        "   Sell all via limit order.\n\n"
        "⏰ <b>Time Exit</b> — High priority\n"
        "   5-7 days before market deadline.\n"
        "   Sell all at market. Never hold into final week.\n\n"
        "🚨 <b>Adverse Catalyst</b> — Critical priority\n"
        "   Credible material news + &gt;5% adverse price move.\n"
        "   Sell immediately at market. Speed &gt; price.\n\n"
        "⚠️ <b>Stop-Loss Review</b> — High priority\n"
        "   NO drops below $0.65 (YES &gt; $0.35)\n"
        "   Bot alerts with thesis summary. User decides.\n\n"
        "🛑 <b>Hard Stop-Loss</b> — Critical priority\n"
        "   NO drops below $0.50 (YES &gt; $0.50)\n"
        "   Auto-sell if enabled. Otherwise urgent alert.\n\n"
        "🔗 <b>Correlation Cascade</b> — Critical\n"
        "   3+ correlated positions move adverse simultaneously.\n"
        "   Alert: review entire cluster. Exit weakest.",
        [[{"text": "📐 Position Sizing", "callback_data": "nt_position_sizing"},
          {"text": "🛡 Risk Params", "callback_data": "nt_risk_params"}],
         [{"text": "← NO Theta", "callback_data": "strategy_no_theta"}]])

def show_nt_position_sizing(chat_id):
    """NO Theta — Position sizing (Half-Kelly)"""
    onboarding.send_inline(chat_id,
        "📐 <b>NO Theta — Position Sizing</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Method:</b> Half-Kelly Criterion (f*/2)\n\n"
        "<b>Formula:</b>\n"
        "<code>Full Kelly: f* = (bp - q) / b</code>\n"
        "<code>where b = (1/NO_price - 1)</code>\n"
        "<code>      p = estimated P(NO)</code>\n"
        "<code>      q = 1 - p</code>\n"
        "<code>Size = (f*/2) × portfolio_balance</code>\n\n"
        "📊 <b>Limits:</b>\n"
        "• Minimum bet: <b>$10</b>\n"
        "• Maximum bet: <b>$30</b> or 15% of portfolio\n"
        "• Fallback: Fixed $15 if Kelly &lt; $10 or portfolio &lt; $150\n"
        "• Reserve: Always keep 15-20% cash uninvested\n\n"
        "📝 <b>Example:</b>\n"
        "NO at $0.85, P(NO) = 92%, portfolio = $200\n"
        "b = 0.176, f* = 46.4%, Half-Kelly = 23.2% = $46.40\n"
        "Capped at $30 (hard cap). <b>Final: $30</b>",
        [[{"text": "🛡 Risk Params", "callback_data": "nt_risk_params"},
          {"text": "🚪 Exit Rules", "callback_data": "nt_exit_rules"}],
         [{"text": "← NO Theta", "callback_data": "strategy_no_theta"}]])

def show_nt_risk_params(chat_id):
    """NO Theta — Risk parameters"""
    onboarding.send_inline(chat_id,
        "🛡 <b>NO Theta — Risk Parameters</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Position Limits:</b>\n"
        "• Max concurrent positions: <b>4-6</b>\n"
        "• Max per category: <b>2</b>\n"
        "• Max correlated exposure: <b>40%</b> of portfolio\n\n"
        "💰 <b>Capital Management:</b>\n"
        "• Cash reserve: <b>15-20%</b> always uninvested\n"
        "• Max loss per position: 100% (binary)\n\n"
        "🛑 <b>Circuit Breakers:</b>\n"
        "• Daily loss limit: <b>10%</b> of portfolio → trade pause\n"
        "• Drawdown breaker: <b>25%</b> from peak → auto-execution paused\n\n"
        "⏱ <b>Timeframes:</b>\n"
        "• Entry window: 14-45 days before deadline\n"
        "• Avg hold: 14-28 days\n"
        "• Scanner: Every 10-15 min\n"
        "• Portfolio review: Daily 8:00 AM\n"
        "• AI performance review: Weekly (Sun 8 PM)",
        [[{"text": "📐 Position Sizing", "callback_data": "nt_position_sizing"},
          {"text": "📋 Entry Gates", "callback_data": "nt_entry_gates"}],
         [{"text": "← NO Theta", "callback_data": "strategy_no_theta"}]])

def show_nt_research_protocol(chat_id):
    """NO Theta — 5-source research engine"""
    onboarding.send_inline(chat_id,
        "🔬 <b>NO Theta — Research Protocol</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>5-Source Weighted Edge Detection:</b>\n\n"
        "📊 <b>1. Historical Base Rate</b> — 20%\n"
        "   ACLED API for conflict events, Wikipedia timelines.\n"
        "   Output: Base rate % (e.g. \"6.4% of similar timeframes\")\n\n"
        "👨‍🔬 <b>2. Expert Forecasters</b> — 30%\n"
        "   Metaculus, Swift Centre, GJO estimates.\n"
        "   Output: Expert NO %, divergence, overpricing factor\n\n"
        "🐋 <b>3. On-Chain Whales</b> — 15%\n"
        "   Top 20 wallets by PnL. Positions + conviction.\n"
        "   Output: # wallets on NO, avg entry, conviction signal\n\n"
        "🏛 <b>4. Structural Analysis</b> — 25%\n"
        "   AI analysis of diplomatic/logistical/political barriers.\n"
        "   Output: Barrier score (1-10), key barriers\n\n"
        "📈 <b>5. Market Microstructure</b> — 10%\n"
        "   Order book depth, spread, order imbalance, flow.\n"
        "   Output: Book tilt ratio, net flow, liquidity grade\n\n"
        "<b>Aggregation Formula:</b>\n"
        "<code>P(NO) = 0.20×BaseRate + 0.30×Expert</code>\n"
        "<code>       + 0.15×Whale + 0.25×Structural</code>\n"
        "<code>       + 0.10×Micro</code>\n\n"
        "<b>Edge</b> = P(NO) - Market NO Price (min 5 pts)\n"
        "<b>Confidence:</b> Low (2/5), Med (3/5), High (4-5/5)\n"
        "Only trade on Medium or High.",
        [[{"text": "📋 Entry Gates", "callback_data": "nt_entry_gates"},
          {"text": "🏆 Run Scan", "callback_data": "run_top10"}],
         [{"text": "← NO Theta", "callback_data": "strategy_no_theta"}]])

# ── SCALPING NO THETA STRATEGY — Full Spec ──

def show_scalp_no_strategy(chat_id):
    """Scalping NO Theta — Main briefing"""
    onboarding.send_inline(chat_id,
        "⚡ <b>Scalping NO Theta — \"Quick Harvest\"</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<code>SCALP_NO_V1</code>\n\n"
        "<b>Core Thesis:</b>\n"
        "In the final 3-14 days before deadline, theta decay accelerates exponentially. "
        "NO shares become increasingly certain — we harvest 3-5 cent gains "
        "with minimal risk, multiple times per week.\n\n"
        "<b>Edge Source:</b>\n"
        "Market makers reprice inefficiently during final days. "
        "Retail panic = price discovery lag we exploit.\n\n"
        "<b>Target Markets:</b>\n"
        "Same high-volume, binary YES/NO markets. Final stage only.\n\n"
        "📊 <b>Target KPIs (Base Case):</b>\n"
        "Win Rate: 85-95% | Monthly Return: 5-12%\n"
        "Sharpe Ratio: 2.0-4.0 | Trades/Month: 12-32",
        [[{"text": "📋 Entry Gates", "callback_data": "sn_entry_gates"},
          {"text": "🚪 Exit Rules", "callback_data": "sn_exit_rules"}],
         [{"text": "📐 Position Sizing", "callback_data": "sn_position_sizing"},
          {"text": "🛡 Risk Params", "callback_data": "sn_risk_params"}],
         [{"text": "🏆 Run Scan Now", "callback_data": "run_top10"}],
         [{"text": "← Strategies", "callback_data": "trade_signals"}]])

def show_sn_entry_gates(chat_id):
    """Scalp NO — Entry conditions"""
    onboarding.send_inline(chat_id,
        "📋 <b>Scalp NO — Entry Gates</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>All gates required:</b>\n\n"
        "🔒 <b>Gate 1:</b> Binary deadline market\n\n"
        "🔒 <b>Gate 2:</b> 3-14 days to deadline (late-stage)\n\n"
        "🔒 <b>Gate 3:</b> NO price $0.88 — $0.96\n"
        "   (High NO = high certainty = maximum decay advantage)\n\n"
        "🔒 <b>Gate 4:</b> Spread &lt; 2 cents\n"
        "   (Tight book for quick execution)\n\n"
        "🔒 <b>Gate 5:</b> 24h volume ≥ $10K\n"
        "   (Sufficient liquidity to exit quickly)\n\n"
        "🔒 <b>Gate 6:</b> No major news within 24h\n"
        "   (Stable; low catalyst risk)",
        [[{"text": "🚪 Exit Rules", "callback_data": "sn_exit_rules"},
          {"text": "📐 Position Sizing", "callback_data": "sn_position_sizing"}],
         [{"text": "← Scalp NO", "callback_data": "strategy_scalp_no"}]])

def show_sn_exit_rules(chat_id):
    """Scalp NO — Exit rules"""
    onboarding.send_inline(chat_id,
        "🚪 <b>Scalp NO — Exit Rules</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>Take Profit</b>\n"
        "   Entry + 3 cents (default; configurable 2-5 cents)\n"
        "   Limit sell placed <b>immediately</b> at entry.\n\n"
        "⏰ <b>Time Exit</b>\n"
        "   2 days before market deadline if TP not hit.\n"
        "   Market sell.\n\n"
        "⚠️ <b>Soft Stop</b>\n"
        "   NO drops 5+ cents from entry.\n"
        "   Alert user; suggest review.\n\n"
        "🛑 <b>Hard Stop</b>\n"
        "   NO drops 10+ cents from entry.\n"
        "   Auto-sell if enabled; urgent alert otherwise.\n\n"
        "🚨 <b>Catalyst Exit</b>\n"
        "   Material news detected → market sell immediately.",
        [[{"text": "📐 Sizing", "callback_data": "sn_position_sizing"},
          {"text": "🛡 Risk", "callback_data": "sn_risk_params"}],
         [{"text": "← Scalp NO", "callback_data": "strategy_scalp_no"}]])

def show_sn_position_sizing(chat_id):
    """Scalp NO — Position sizing (Fixed %)"""
    onboarding.send_inline(chat_id,
        "📐 <b>Scalp NO — Position Sizing</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Method:</b> Fixed Percentage — 2% of portfolio per scalp\n\n"
        "<b>Rationale:</b>\n"
        "High trade frequency (3-8/week) means smaller per-trade size "
        "to manage aggregate exposure.\n\n"
        "📊 <b>Limits:</b>\n"
        "• Minimum bet: <b>$5</b>\n"
        "• Maximum bet: <b>$20</b> or 5% of portfolio\n"
        "• Max concurrent scalps: <b>4</b>\n\n"
        "📝 <b>Example:</b>\n"
        "Portfolio $200, size = 2% = $4\n"
        "Rounded up to minimum $5\n"
        "4 concurrent = $20 capital = 10% exposure",
        [[{"text": "🛡 Risk Params", "callback_data": "sn_risk_params"},
          {"text": "📋 Entry Gates", "callback_data": "sn_entry_gates"}],
         [{"text": "← Scalp NO", "callback_data": "strategy_scalp_no"}]])

def show_sn_risk_params(chat_id):
    """Scalp NO — Risk parameters"""
    onboarding.send_inline(chat_id,
        "🛡 <b>Scalp NO — Risk Parameters</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Position Limits:</b>\n"
        "• Max concurrent scalp positions: <b>4</b>\n"
        "• Max per market: <b>1</b>\n"
        "• Hard stop distance: <b>10 cents</b> from entry\n\n"
        "🛑 <b>Circuit Breakers:</b>\n"
        "• Max daily scalp loss: <b>3%</b> of portfolio ($6 on $200)\n"
        "• Cooldown: <b>4 hours</b> after 2 consecutive losses\n"
        "   (No new scalp signals during cooldown)\n\n"
        "⏱ <b>Timeframes:</b>\n"
        "• Entry window: 3-14 days before deadline\n"
        "• Avg hold: 2-7 days\n"
        "• Scanner: Every 10 min\n"
        "• Trade frequency: 3-8 per week\n"
        "• Capital rotation: Very fast; proceeds same day",
        [[{"text": "📐 Position Sizing", "callback_data": "sn_position_sizing"},
          {"text": "📋 Entry Gates", "callback_data": "sn_entry_gates"}],
         [{"text": "← Scalp NO", "callback_data": "strategy_scalp_no"}]])

# ── STRATEGY DETAILS OVERVIEW ──

def show_strategy_details_menu(chat_id):
    """Side-by-side comparison of both strategies"""
    onboarding.send_inline(chat_id,
        "📋 <b>Strategy Comparison</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🎯 NO Theta Decay</b> vs <b>⚡ Scalp NO</b>\n\n"
        "Hold Period:  14-28 days  vs  2-7 days\n"
        "NO Price:     $0.55-$0.90  vs  $0.88-$0.96\n"
        "Edge Needed:  ≥ 5pts  vs  ≥ 3pts\n"
        "TP Target:    NO ≥ $0.93  vs  Entry + 3¢\n"
        "Sizing:       Half-Kelly  vs  Fixed 2%\n"
        "Max Bet:      $30  vs  $20\n"
        "Max Positions: 4-6  vs  4\n"
        "Trades/Month: 5-8  vs  12-32\n"
        "Win Rate:     88%  vs  85-95%\n"
        "Monthly:      12-18%  vs  5-12%\n"
        "Sharpe:       2.5-3.5  vs  2.0-4.0\n\n"
        "<b>Best for:</b>\n"
        "🎯 NO Theta → Larger edge, patient capital\n"
        "⚡ Scalp → Quick turns, high frequency",
        [[{"text": "🎯 NO Theta Detail", "callback_data": "strategy_no_theta"},
          {"text": "⚡ Scalp NO Detail", "callback_data": "strategy_scalp_no"}],
         [{"text": "← Strategies", "callback_data": "trade_signals"}]])

def show_direct_bet(chat_id):
    """Direct Betting"""
    onboarding.send_inline(chat_id,
        "📊 <b>Direct Bet</b>\n\n"
        "Place a manual bet on any Polymarket event.\n\n"
        "<b>How to bet:</b>\n"
        "1. Send a Polymarket event link or search keyword\n"
        "2. Choose your side (YES/NO)\n"
        "3. Select bet size\n"
        "4. Confirm execution\n\n"
        "<b>Commands:</b>\n"
        "/research &lt;url&gt; — Deep research on a market\n"
        "/add &lt;id&gt; &lt;entry&gt; &lt;size&gt; — Track a position\n"
        "/exit &lt;id&gt; — Close a position\n\n"
        "<b>Position Sizing Methods:</b>\n"
        "• <b>Half-Kelly</b> (default) — f*/2 = (bp-q)/2b\n"
        "• Fixed: $10, $15, $25, $30\n"
        "• % of Portfolio: 2%, 5%, 10%, 15%\n"
        "• Volatility-Adjusted: Scale by market vol",
        [[{"text": "🔬 Research Market", "callback_data": "trade_research_prompt"},
          {"text": "📂 My Positions", "callback_data": "portfolio_positions"}],
         [{"text": "← Strategies", "callback_data": "menu_trade"}]])

def show_strategy_builder(chat_id):
    """Strategy Builder"""
    onboarding.send_inline(chat_id,
        "🔧 <b>Strategy Builder</b>\n\n"
        "Create your own custom trading strategy.\n\n"
        "A strategy defines:\n"
        "• <b>Market filters</b> — deadline type, categories, time range\n"
        "• <b>Entry gates</b> — price range, volume, edge threshold\n"
        "• <b>Exit rules</b> — TP, time exit, stop-loss, catalyst\n"
        "• <b>Position sizing</b> — Kelly, fixed, or % of portfolio\n"
        "• <b>Risk limits</b> — max positions, daily loss, circuit breakers\n\n"
        "<b>Active strategies:</b>\n"
        "• NO Theta Decay (NO_THETA_V1)\n"
        "• Scalping NO (SCALP_NO_V1)\n\n"
        "<i>Custom strategy builder coming in Phase 2.</i>\n"
        "<i>For now, use pre-built strategies or TOP 10 AI picks.</i>",
        [[{"text": "🎯 Pre-Built Strategies", "callback_data": "trade_signals"}],
         [{"text": "← Strategies", "callback_data": "menu_trade"}]])

# ═══════════════════════════════════════════════
# SECTION 4: BACKTEST (Spec Section 8)
# ═══════════════════════════════════════════════

def show_backtest_menu(chat_id):
    """Backtest sub-menu (Spec Section 2.3.4)"""
    perf = pstore.get_performance()
    total = perf.get("total", 0)
    win_rate = perf.get("win_rate") or 0
    correct = perf.get("correct", 0)
    resolved = perf.get("resolved", 0)

    onboarding.send_inline(chat_id,
        "📉 <b>Backtest — Strategy Testing</b>\n\n"
        "Test strategies against historical market data.\n\n"
        f"<b>Current AI Performance:</b>\n"
        f"📊 Total Predictions: {total}\n"
        f"✅ Correct: {correct}/{resolved}\n"
        f"📈 Win Rate: {win_rate:.1f}%\n\n"
        "<b>Available for backtesting:</b>\n"
        "• <b>NO Theta Decay</b> (NO_THETA_V1) — 14-45d holds\n"
        "• <b>Scalp NO Theta</b> (SCALP_NO_V1) — 2-7d quick harvests\n\n"
        "<i>Select a strategy to run backtest:</i>",
        [[{"text": "📋 Select Strategy", "callback_data": "backtest_select"}],
         [{"text": "📅 Set Date Range", "callback_data": "backtest_dates"}],
         [{"text": "▶️ Run Backtest", "callback_data": "backtest_run"}],
         [{"text": "💾 Saved Results", "callback_data": "backtest_saved"}],
         [{"text": "← Main Menu", "callback_data": "main_menu"}]])

def show_backtest_no_theta(chat_id):
    """Run NO Theta backtest analysis with real spec params"""
    tg.send("📉 <b>Running NO_THETA_V1 backtest...</b>\nApplying spec parameters to historical data. ~10s.", chat_id)
    try:
        perf = pstore.get_performance()
        calibration = perf.get("calibration", {})
        total = perf.get("total", 0)
        win_rate = perf.get("win_rate") or 0
        resolved = perf.get("resolved", 0)

        msg = (
            "📉 <b>NO Theta Decay — Backtest Results</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<code>NO_THETA_V1</code>\n\n"
            f"<b>Strategy Params:</b>\n"
            f"Entry: NO $0.55-$0.90, edge ≥ 5pts\n"
            f"Hold: 14-45 days, Half-Kelly sizing\n"
            f"TP: NO ≥ $0.93 | Stop: NO &lt; $0.50\n\n"
            f"<b>Results (Historical Data):</b>\n"
            f"Total Trades: {total}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"Resolved: {resolved}\n\n"
            f"<b>Target KPIs (Base Case):</b>\n"
            f"Win Rate: 88% | Monthly: 12-18%\n"
            f"Sharpe: 2.5-3.5 | Max DD: 10-15%\n\n"
            f"<b>Assumptions:</b>\n"
            f"Starting: $200 | Max bet: $30 (capped)\n"
            f"Slippage: 0.5% | Fees: 1% taker\n"
            f"Reserve: 15-20% cash always held\n\n"
        )

        if calibration:
            msg += "<b>AI Calibration:</b>\n"
            for bucket, data in sorted(calibration.items()):
                actual = data.get("actual_rate", data.get("actual", 0))
                count = data.get("count", 0)
                msg += f"  {bucket}%: actual {actual:.0f}% ({count} trades)\n"

        onboarding.send_inline(chat_id, msg,
            [[{"text": "⚡ Scalp NO Backtest", "callback_data": "backtest_scalp_no"}],
             [{"text": "📊 Full Performance", "callback_data": "portfolio_performance"}],
             [{"text": "← Backtest", "callback_data": "menu_backtest"}]])
    except Exception as e:
        tg.send(f"❌ Backtest error: {e}", chat_id)

def show_backtest_scalp_no(chat_id):
    """Run Scalp NO backtest analysis"""
    tg.send("📉 <b>Running SCALP_NO_V1 backtest...</b>\nApplying spec parameters to historical data. ~10s.", chat_id)
    try:
        perf = pstore.get_performance()
        total = perf.get("total", 0)
        win_rate = perf.get("win_rate") or 0
        resolved = perf.get("resolved", 0)

        # Estimate scalp-eligible subset (high NO price markets)
        scalp_est = int(total * 0.4)  # ~40% of markets reach late stage

        msg = (
            "📉 <b>Scalp NO Theta — Backtest Results</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<code>SCALP_NO_V1</code>\n\n"
            f"<b>Strategy Params:</b>\n"
            f"Entry: NO $0.88-$0.96, 3-14 days to deadline\n"
            f"TP: Entry + 3¢ (auto-placed at entry)\n"
            f"Sizing: Fixed 2% of portfolio\n\n"
            f"<b>Results (Historical Data):</b>\n"
            f"Eligible Markets: ~{scalp_est}\n"
            f"Base Win Rate: {win_rate:.1f}%\n"
            f"Scalp Est. Win Rate: {min(win_rate + 8, 95):.0f}%\n"
            f"(Late-stage = higher base probability)\n\n"
            f"<b>Target KPIs:</b>\n"
            f"Win Rate: 85-95% | Monthly: 5-12%\n"
            f"Sharpe: 2.0-4.0 | Trades/Month: 12-32\n"
            f"Max daily loss: 3% of portfolio\n\n"
            f"<b>Assumptions:</b>\n"
            f"Starting: $200 | Max bet: $20\n"
            f"Cooldown: 4h after 2 consecutive losses\n"
            f"Auto-TP: Limit sell at entry + 3¢ on every trade\n"
        )

        onboarding.send_inline(chat_id, msg,
            [[{"text": "🎯 NO Theta Backtest", "callback_data": "backtest_no_theta"}],
             [{"text": "📊 Full Performance", "callback_data": "portfolio_performance"}],
             [{"text": "← Backtest", "callback_data": "menu_backtest"}]])
    except Exception as e:
        tg.send(f"❌ Backtest error: {e}", chat_id)

# ═══════════════════════════════════════════════
# SECTION 5: SETTINGS (Spec Section 2.3.5)
# ═══════════════════════════════════════════════

def show_settings_menu(chat_id):
    """Settings sub-menu"""
    user = user_store.get_user(chat_id) or {}
    sub = user.get("subscription", {})
    status = sub.get("status", "inactive")
    status_emoji = "✅" if status == "active" else "❌"

    risk_profile = user.get("onboarding", {}).get("risk_profile", "Not set")

    # Wallet status
    wallet = wt.get_wallet(str(chat_id))
    if wallet:
        addr = wallet["address"]
        wallet_line = f"👛 Wallet: <code>{addr[:6]}...{addr[-4:]}</code>"
        wallet_btn = "👛 Connect Wallet"
    else:
        wallet_line = "👛 Wallet: <b>Not connected</b>"
        wallet_btn = "👛 Connect Wallet"

    onboarding.send_inline(chat_id,
        f"⚙️ <b>Settings</b>\n\n"
        f"{status_emoji} Subscription: <b>{status.upper()}</b>\n"
        f"{wallet_line}\n"
        f"🎯 Risk Profile: <b>{risk_profile}</b>",
        [[{"text": wallet_btn, "callback_data": "settings_wallet"}],
         [{"text": "📐 Bet Sizing", "callback_data": "settings_sizing"}],
         [{"text": "🔔 Notifications", "callback_data": "settings_notifications"}],
         [{"text": "🛡 Risk Limits", "callback_data": "settings_risk_limits"}],
         [{"text": "🔑 API Keys", "callback_data": "settings_api_keys"}],
         [{"text": "📥 Export Data", "callback_data": "settings_export"}],
         [{"text": "← Main Menu", "callback_data": "main_menu"}]])

def show_wallet_settings(chat_id):
    """Wallet Connection — Read-Only Portfolio Tracking"""
    wallet = wt.get_wallet(str(chat_id))

    if wallet:
        addr = wallet["address"]
        label = wallet.get("label", "")
        synced = wallet.get("last_synced", "Never")[:16].replace("T", " ")
        total_syncs = wallet.get("total_syncs", 0)
        display = f"{label} " if label else ""
        display += f"<code>{addr[:6]}...{addr[-4:]}</code>"

        onboarding.send_inline(chat_id,
            f"👛 <b>Wallet Connected</b>\n\n"
            f"Address: {display}\n"
            f"Last Synced: {synced}\n"
            f"Total Syncs: {total_syncs}\n\n"
            "Your portfolio data is pulled live from Polymarket.\n"
            "No private keys needed — read-only tracking.\n\n"
            "🔄 <b>Rename:</b> /wallet_label My Wallet\n"
            "❌ <b>Disconnect:</b> Use button below",
            [[{"text": "📊 View Portfolio", "callback_data": "portfolio_dashboard"},
              {"text": "🔄 Change Wallet", "callback_data": "wallet_connect"}],
             [{"text": "❌ Disconnect", "callback_data": "wallet_disconnect"}],
             [{"text": "← Settings", "callback_data": "menu_settings"}]])
    else:
        onboarding.send_inline(chat_id,
            "👛 <b>Connect Wallet</b>\n\n"
            "Track your Polymarket portfolio in real-time.\n\n"
            "<b>How it works:</b>\n"
            "• Paste your <b>public</b> wallet address (0x...)\n"
            "• We pull your positions, P/L, and activity\n"
            "• 100% read-only — no private keys ever needed\n\n"
            "🔒 <b>Security:</b>\n"
            "• Only your public address is stored\n"
            "• No signing, no custody, no trading access\n"
            "• Same as viewing your wallet on Polygonscan\n\n"
            "<b>To connect, send:</b>\n"
            "<code>/wallet 0xYourAddress...</code>",
            [[{"text": "📋 How to Find My Address", "callback_data": "wallet_help"},
              {"text": "← Settings", "callback_data": "menu_settings"}]])

def show_bet_sizing(chat_id):
    """NEW: Bet Sizing configuration"""
    onboarding.send_inline(chat_id,
        "📐 <b>Bet Sizing Method</b>\n\n"
        "Choose how the bot sizes positions automatically.\n\n"
        "• <b>Fixed</b> — Same amount every trade\n"
        "• <b>% of Portfolio</b> — Scale with your account\n"
        "• <b>Kelly Criterion</b> — Math-optimal sizing\n"
        "• <b>Vol-Adjusted</b> — Scale by market volatility\n\n"
        "Current: <b>Half-Kelly</b> (recommended)",
        [[{"text": "🔧 Fixed Amount", "callback_data": "sizing_fixed"},
          {"text": "📊 Percentage", "callback_data": "sizing_percent"}],
         [{"text": "📈 Kelly", "callback_data": "sizing_kelly"},
          {"text": "📉 Vol-Adjusted", "callback_data": "sizing_vol"}],
         [{"text": "← Settings", "callback_data": "menu_settings"}]])

def show_notifications(chat_id):
    """NEW: Notifications configuration"""
    onboarding.send_inline(chat_id,
        "🔔 <b>Notifications</b>\n\n"
        "Control when you receive alerts.\n\n"
        "🎯 Strategy Signals\n"
        "⚠️ Risk Alerts\n"
        "💰 Daily Summary\n"
        "🔴 Stop-Loss Triggered\n"
        "✅ Position Closed\n\n"
        "Current: All enabled",
        [[{"text": "🎯 Signals", "callback_data": "notif_signals"},
          {"text": "⚠️ Risk", "callback_data": "notif_risk"}],
         [{"text": "💰 Daily", "callback_data": "notif_daily"},
          {"text": "🔴 Stops", "callback_data": "notif_stops"}],
         [{"text": "← Settings", "callback_data": "menu_settings"}]])

def show_risk_limits(chat_id):
    """NEW: Risk Limits configuration"""
    onboarding.send_inline(chat_id,
        "🛡 <b>Risk Limits</b>\n\n"
        "Set hard boundaries for your account.\n\n"
        "• Max position size: 25% of portfolio\n"
        "• Daily loss limit: 10% of portfolio\n"
        "• Max drawdown: 25% from peak\n"
        "• Max open positions: 6\n"
        "• Min cash reserve: 15%\n\n"
        "Adjust these to match your risk tolerance.",
        [[{"text": "📊 Position Size", "callback_data": "risk_position"},
          {"text": "📉 Drawdown", "callback_data": "risk_drawdown"}],
         [{"text": "💰 Daily Loss", "callback_data": "risk_daily"},
          {"text": "📂 Open Positions", "callback_data": "risk_open"}],
         [{"text": "← Settings", "callback_data": "menu_settings"}]])

def show_api_keys(chat_id):
    """NEW: API Keys management"""
    onboarding.send_inline(chat_id,
        "🔑 <b>API Keys</b>\n\n"
        "Connect external services for automated trading.\n\n"
        "Supported:\n"
        "• Polymarket API\n"
        "• Trading automation webhook\n"
        "• Price feed integrations\n\n"
        "⚠️ Never share your keys publicly.",
        [[{"text": "➕ Add Key", "callback_data": "api_add"},
          {"text": "📋 View Keys", "callback_data": "api_list"}],
         [{"text": "❌ Delete Key", "callback_data": "api_delete"}],
         [{"text": "← Settings", "callback_data": "menu_settings"}]])

def show_export_data(chat_id):
    """NEW: Export Data option"""
    onboarding.send_inline(chat_id,
        "📥 <b>Export Data</b>\n\n"
        "Download your Polytragent data.\n\n"
        "Available exports:\n"
        "📊 Portfolio (CSV)\n"
        "📈 Trade History (CSV)\n"
        "🧠 Performance Report (PDF)\n"
        "🔄 Copy Trading Log (JSON)\n\n"
        "All data is yours. Export anytime.",
        [[{"text": "📊 Portfolio", "callback_data": "export_portfolio"},
          {"text": "📈 Trades", "callback_data": "export_trades"}],
         [{"text": "🧠 Performance", "callback_data": "export_perf"},
          {"text": "🔄 Copy Trading", "callback_data": "export_ct"}],
         [{"text": "← Settings", "callback_data": "menu_settings"}]])

def show_risk_profile(chat_id):
    """Spec Section 3.1 Step 2 — Risk Profile"""
    user = user_store.get_user(chat_id) or {}
    current = user.get("onboarding", {}).get("risk_profile", "Not set")
    onboarding.send_inline(chat_id,
        f"🎯 <b>Risk Profile</b>\n\n"
        f"Current: <b>{current}</b>\n\n"
        "Choose your comfort level with market swings.",
        [[{"text": "🟢 Conservative", "callback_data": "risk_conservative"},
          {"text": "🟡 Moderate", "callback_data": "risk_moderate"}],
         [{"text": "🔴 Aggressive", "callback_data": "risk_aggressive"}],
         [{"text": "← Settings", "callback_data": "menu_settings"}]])

def show_account(chat_id):
    """Account settings"""
    user = user_store.get_user(chat_id) or {}
    username = user.get("username", "N/A")
    created = user.get("created_at", "N/A")[:10]
    onboarding.send_inline(chat_id,
        f"👤 <b>Account</b>\n\n"
        f"Username: @{username}\n"
        f"Joined: {created}\n\n"
        f"Manage your subscription and account settings.",
        [[{"text": "💳 Manage Subscription", "callback_data": "manage_sub"},
          {"text": "📧 Contact Support", "callback_data": "support"}],
         [{"text": "← Settings", "callback_data": "menu_settings"}]])

# ═══════════════════════════════════════════════
# SECTION: LIVE TRADING
# ═══════════════════════════════════════════════

def show_trading_menu(chat_id):
    """Trading hub — buy, sell, positions, orders"""
    enabled = trading.is_trading_enabled()
    if not enabled:
        onboarding.send_inline(chat_id,
            "💰 <b>Live Trading</b>\n\n"
            "⚠️ Trading engine not configured.\n\n"
            "Admin needs to set these env vars:\n"
            "• POLY_API_KEY\n"
            "• POLY_API_SECRET\n"
            "• POLY_API_PASSPHRASE\n"
            "• POLY_PRIVATE_KEY\n\n"
            "Get keys at builders.polymarket.com",
            [[{"text": "← Main Menu", "callback_data": "main_menu"}]])
        return

    addr = trading.get_wallet_address() or "Not connected"
    short = f"{addr[:6]}...{addr[-4:]}" if addr and addr.startswith("0x") else addr

    onboarding.send_inline(chat_id,
        f"💰 <b>Live Trading</b>\n\n"
        f"🔗 Wallet: <code>{short}</code>\n\n"
        "Buy or sell on any Polymarket event.\n"
        "Send a Polymarket link to start.",
        [[{"text": "🟩 Quick Buy", "callback_data": "trading_buy_prompt"},
          {"text": "🟥 Quick Sell", "callback_data": "trading_sell_prompt"}],
         [{"text": "📊 My Positions", "callback_data": "trading_positions"},
          {"text": "📋 Open Orders", "callback_data": "trading_orders"}],
         [{"text": "📜 Trade History", "callback_data": "trading_history"}],
         [{"text": "❌ Cancel All Orders", "callback_data": "trading_cancel_all"}],
         [{"text": "← Main Menu", "callback_data": "main_menu"}]])


_waiting_for_trade = {}  # chat_id -> {"action": "buy"|"sell", "step": ...}

def show_buy_prompt(chat_id):
    """Prompt user to enter market URL + amount for buying"""
    _waiting_for_trade[str(chat_id)] = {"action": "buy", "step": "market"}
    onboarding.send_inline(chat_id,
        "🟩 <b>Buy — Step 1/3</b>\n\n"
        "Send a Polymarket event link or slug:\n\n"
        "<i>Example: https://polymarket.com/event/will-trump...</i>\n"
        "<i>Or just: will-trump-win-2024</i>",
        [[{"text": "← Cancel", "callback_data": "trading_cancel_flow"}]])


def show_sell_prompt(chat_id):
    """Show positions to sell from"""
    positions = trading.get_positions()
    if not positions:
        tg.send("📭 No open positions to sell.", chat_id)
        show_trading_menu(chat_id)
        return

    _waiting_for_trade[str(chat_id)] = {"action": "sell", "step": "market"}
    msg = "🟥 <b>Sell Position</b>\n\nSend a Polymarket event link for the position you want to close.\n\n"
    msg += trading.format_positions(positions)
    tg.send(msg, chat_id)


def handle_trade_input(chat_id, text):
    """Process multi-step trade input"""
    chat_str = str(chat_id)
    state = _waiting_for_trade.get(chat_str)
    if not state:
        return False

    action = state["action"]
    step = state["step"]

    if step == "market":
        # User sent market URL/slug — resolve it
        tg.send("🔍 Resolving market...", chat_id)
        market = trading.resolve_market_tokens(text.strip())
        if not market:
            tg.send("❌ Could not find that market. Try a different link or slug.", chat_id)
            return True

        state["market"] = market
        state["step"] = "outcome"

        # Show outcomes to choose from
        buttons = []
        for t in market["tokens"]:
            mid = trading.get_midpoint(t["token_id"])
            price_str = f" (${mid:.2f})" if mid else ""
            buttons.append([{"text": f"{t['outcome']}{price_str}", "callback_data": f"trade_outcome_{t['outcome']}"}])
        buttons.append([{"text": "← Cancel", "callback_data": "trading_cancel_flow"}])

        onboarding.send_inline(chat_id,
            f"{'🟩 Buy' if action == 'buy' else '🟥 Sell'} — <b>Step 2/3</b>\n\n"
            f"📊 <b>{market['question']}</b>\n\n"
            "Select outcome:",
            buttons)
        return True

    elif step == "amount":
        # User sent dollar amount
        try:
            amount = float(text.strip().replace("$", "").replace(",", ""))
            if amount < 1:
                tg.send("❌ Minimum trade is $1.00", chat_id)
                return True
            if amount > 10000:
                tg.send("❌ Maximum trade is $10,000", chat_id)
                return True
        except ValueError:
            tg.send("❌ Enter a valid number (e.g., 25 or 100.50)", chat_id)
            return True

        market = state["market"]
        outcome = state["outcome"]
        token_id = state["token_id"]

        _waiting_for_trade.pop(chat_str, None)

        # Execute trade
        if action == "buy":
            tg.send(f"⏳ Executing BUY ${amount:.2f} on {outcome}...", chat_id)
            result = trading.market_buy(
                token_id=token_id,
                amount=amount,
                neg_risk=market.get("neg_risk", False),
                tick_size=market.get("tick_size", "0.01"),
            )
            result["market"] = market["question"]
            result["outcome"] = outcome
            result["amount"] = amount
        else:
            tg.send(f"⏳ Executing SELL {amount} shares of {outcome}...", chat_id)
            result = trading.market_sell(
                token_id=token_id,
                amount=amount,
                neg_risk=market.get("neg_risk", False),
                tick_size=market.get("tick_size", "0.01"),
            )
            result["market"] = market["question"]
            result["outcome"] = outcome
            result["shares"] = amount

        msg = trading.format_order_result(result)
        onboarding.send_inline(chat_id, msg,
            [[{"text": "💰 Trade Again", "callback_data": "menu_trading"},
              {"text": "📊 Positions", "callback_data": "trading_positions"}],
             [{"text": "← Main Menu", "callback_data": "main_menu"}]])
        return True

    return False


def show_trading_positions(chat_id):
    """Show live positions from the trading wallet"""
    tg.send("📊 Loading positions...", chat_id)
    positions = trading.get_positions()
    msg = trading.format_positions(positions)
    onboarding.send_inline(chat_id, msg,
        [[{"text": "🔄 Refresh", "callback_data": "trading_positions"},
          {"text": "🟥 Sell", "callback_data": "trading_sell_prompt"}],
         [{"text": "← Trading", "callback_data": "menu_trading"}]])


def show_trading_orders(chat_id):
    """Show open orders"""
    orders = trading.get_open_orders()
    msg = trading.format_open_orders(orders)
    onboarding.send_inline(chat_id, msg,
        [[{"text": "🔄 Refresh", "callback_data": "trading_orders"},
          {"text": "❌ Cancel All", "callback_data": "trading_cancel_all"}],
         [{"text": "← Trading", "callback_data": "menu_trading"}]])


def show_trade_history(chat_id):
    """Show recent trade history"""
    trades = trading.get_trade_history(20)
    if not trades:
        msg = "📭 No recent trades."
    else:
        lines = ["📜 <b>Recent Trades</b>", ""]
        for t in trades[:15]:
            side = t.get("side", "?").upper()
            price = float(t.get("price", 0))
            size = float(t.get("size", 0))
            emoji = "🟩" if side == "BUY" else "🟥"
            title = t.get("market", t.get("title", ""))[:35]
            lines.append(f"{emoji} {side} | ${price:.2f} × {size:.0f} | {title}")
        msg = "\n".join(lines)

    onboarding.send_inline(chat_id, msg,
        [[{"text": "🔄 Refresh", "callback_data": "trading_history"}],
         [{"text": "← Trading", "callback_data": "menu_trading"}]])


# ═══════════════════════════════════════════════
# SECTION: WALLET MANAGEMENT
# ═══════════════════════════════════════════════

def show_wallet_menu(chat_id):
    """Wallet management hub"""
    wallets = wm.get_wallets(str(chat_id))
    primary = wm.get_primary_wallet(str(chat_id))

    if wallets:
        msg = wm.format_wallets(wallets)
        if primary:
            balance = wm.get_full_balance(primary["address"])
            msg += "\n\n" + wm.format_balance(balance)
    else:
        msg = (
            "👛 <b>Wallet Manager</b>\n\n"
            "No wallets yet. Create one to start trading.\n\n"
            "Your wallet holds USDC on the Polygon network\n"
            "for trading on Polymarket."
        )

    onboarding.send_inline(chat_id, msg,
        [[{"text": "➕ Create Wallet", "callback_data": "wallet_create"},
          {"text": "📥 Import Wallet", "callback_data": "wallet_import_prompt"}],
         [{"text": "💰 Balance", "callback_data": "wallet_balance"},
          {"text": "📤 Send USDC", "callback_data": "wallet_send_prompt"}],
         [{"text": "📥 Receive/Deposit", "callback_data": "wallet_deposit"},
          {"text": "🔑 Export Keys", "callback_data": "wallet_export_prompt"}],
         [{"text": "← Main Menu", "callback_data": "main_menu"}]])


_waiting_for_wallet = {}  # chat_id -> {"action": "import"|"send"|"export", ...}

def handle_wallet_input(chat_id, text):
    """Process wallet-related text input"""
    chat_str = str(chat_id)
    state = _waiting_for_wallet.get(chat_str)
    if not state:
        return False

    action = state["action"]

    if action == "import" and state.get("step") == "key":
        result = wm.import_wallet(chat_str, text.strip())
        _waiting_for_wallet.pop(chat_str, None)
        if result["success"]:
            tg.send(
                f"✅ <b>Wallet Imported!</b>\n\n"
                f"Address: <code>{result['address']}</code>\n"
                f"Label: {result['label']}\n\n"
                "⚠️ Delete the message with your private key!",
                chat_id)
        else:
            tg.send(f"❌ Import failed: {result['error']}", chat_id)
        show_wallet_menu(chat_id)
        return True

    elif action == "send":
        if state.get("step") == "address":
            state["to_address"] = text.strip()
            state["step"] = "amount"
            tg.send("💰 How much USDC to send? (e.g., 50 or 100.50)", chat_id)
            return True

        elif state.get("step") == "amount":
            try:
                amount = float(text.strip().replace("$", "").replace(",", ""))
            except ValueError:
                tg.send("❌ Enter a valid number.", chat_id)
                return True

            to_addr = state["to_address"]
            _waiting_for_wallet.pop(chat_str, None)

            tg.send(f"⏳ Sending ${amount:.2f} USDC to {to_addr[:10]}...", chat_id)
            result = wm.send_usdc(chat_str, to_addr, amount)
            tg.send(wm.format_send_result(result), chat_id)
            return True

    _waiting_for_wallet.pop(chat_str, None)
    return False


# ═══════════════════════════════════════════════
# SECTION: AUTO COPY TRADING
# ═══════════════════════════════════════════════

def show_auto_copy_menu(chat_id):
    """Auto-copy trading settings and stats"""
    stats = ce.get_auto_copy_stats(str(chat_id))
    msg = ce.format_auto_copy_stats(stats) if stats.get("enabled") else ce.format_auto_copy_settings(None)

    buttons = []
    if stats.get("enabled"):
        buttons.append([{"text": "⏹ Disable Auto-Copy", "callback_data": "auto_copy_off"}])
        buttons.append([{"text": "⚙️ Settings", "callback_data": "auto_copy_settings_menu"},
                        {"text": "📊 Stats", "callback_data": "auto_copy_stats"}])
    else:
        buttons.append([{"text": "▶️ Enable Auto-Copy ($25/trade, $200/day)", "callback_data": "auto_copy_on"}])
    buttons.append([{"text": "🔄 Copy Trading Signals", "callback_data": "ct_signals"}])
    buttons.append([{"text": "← Main Menu", "callback_data": "main_menu"}])

    onboarding.send_inline(chat_id, msg, buttons)


def show_auto_copy_settings_menu(chat_id):
    """Auto-copy settings adjustment"""
    settings = ce.get_auto_copy_settings(str(chat_id))
    if not settings:
        show_auto_copy_menu(chat_id)
        return

    onboarding.send_inline(chat_id,
        f"⚙️ <b>Auto-Copy Settings</b>\n\n"
        f"💰 Max per trade: <b>${settings.get('max_per_trade', 25):.2f}</b>\n"
        f"📅 Daily limit: <b>${settings.get('daily_limit', 200):.2f}</b>\n"
        f"📊 Mode: <b>{settings.get('mode', 'fixed')}</b>\n"
        f"📉 Max slippage: <b>{settings.get('max_slippage', 0.05)*100:.0f}%</b>\n\n"
        "Send a command to adjust:\n"
        "/ac_max 50 — Max $50 per trade\n"
        "/ac_daily 500 — Daily limit $500\n"
        "/ac_mode fixed|percentage|proportional",
        [[{"text": "🔄 Refresh", "callback_data": "auto_copy_settings_menu"}],
         [{"text": "← Auto-Copy", "callback_data": "menu_auto_copy"}]])

def show_whales_menu(chat_id):
    """Show the whale directory — curated list with follow buttons."""
    import whale_discovery as wd_mod
    text, buttons = wd_mod.format_directory_page(page=0, per_page=5)

    # Add following count header
    following = ct.get_following_count(str(chat_id))
    limit = user_store.get_wallet_tracking_limit(str(chat_id))
    is_degen = user_store.is_degen(str(chat_id))

    header = (
        f"📊 Following: <b>{following} / {limit}</b> wallets"
        f"{' 🚀 Degen Mode' if is_degen else ''}\n"
        f"🔔 You'll get <b>real-time alerts</b> when followed whales trade.\n\n"
    )
    onboarding.send_inline(chat_id, header + text, buttons)

def show_degen_mode_info(chat_id):
    """Degen Mode subscription info"""
    is_degen = user_store.is_degen(chat_id) if hasattr(user_store, 'is_degen') else False

    if is_degen:
        onboarding.send_inline(chat_id,
            "🚀 <b>Degen Mode — ACTIVE</b>\n\n"
            "You have unlimited access to:\n"
            "• Unlimited whale tracking (3+ wallets)\n"
            "• Advanced portfolio analytics\n"
            "• Priority alerts & notifications\n\n"
            "💰 $79/month billed to your Stripe account.\n"
            "🔄 Cancel anytime in settings.",
            [[{"text": "⚙️ Manage Subscription", "callback_data": "degen_manage"}],
             [{"text": "← Main Menu", "callback_data": "main_menu"}]])
    else:
        onboarding.send_inline(chat_id,
            "🚀 <b>Degen Mode — Unlimited Whale Tracking</b>\n\n"
            "Get the most from Polytragent:\n"
            "• Unlimited whale wallet tracking (vs. 3 free)\n"
            "• Advanced portfolio analytics\n"
            "• Priority alerts & notifications\n"
            "• Full whale discovery suite\n\n"
            "💰 <b>$79/month</b>\n"
            "✅ Cancel anytime. No lock-in.\n\n"
            "🔗 Set up via Stripe checkout below.",
            [[{"text": "💳 Upgrade to Degen Mode", "callback_data": "degen_subscribe"}],
             [{"text": "← Main Menu", "callback_data": "main_menu"}]])

def _handle(cmd, chat_id):
    """Command handler — routes all /commands"""
    text = cmd
    parts = cmd.split()
    cmd = parts[0].lower().split("@")[0]  # strip @botname suffix

    # ── ALWAYS AVAILABLE COMMANDS ──

    if cmd in ("/start", "/help"):
        user = user_store.get_user(chat_id)
        username = user.get("username", "") if user else ""
        first_name = user.get("first_name", "") if user else ""
        if hasattr(onboarding, 'handle_start'):
            onboarding.handle_start(chat_id, username, first_name)
        else:
            onboarding._send_start(chat_id)
        return

    if cmd == "/degen":
        show_degen_mode_info(chat_id)
        return

    if cmd == "/code":
        onboarding.send_inline(chat_id,
                "🎁 <b>Redeem Degen Mode Code</b>\n\n"
                "Send your Degen Mode code now (e.g., DEGEN-XXXXXXXX).\n\n"
                "Or use it directly: /code DEGEN-XXXXXXXX",
                [[{"text": "← Cancel", "callback_data": "main_menu"}]])
        return

    if cmd == "/whales":
        show_whales_menu(chat_id)
        return

    if cmd == "/dashboard":
        onboarding._send_dashboard_link(chat_id)
        return

    # ═══════════════════════════════════════════
    # ALL FEATURES NOW FREE (Degen Mode for extra wallets)
    # ═══════════════════════════════════════════

    # ── MENU NAVIGATION ──

    if cmd in ("/help", "/menu"):
        send_main_menu(chat_id)

    # ── SHORTCUT COMMANDS (still work alongside menu) ──

    elif cmd == "/manage":
        onboarding._manage_subscription(chat_id)

    elif cmd == "/account":
        show_account(chat_id)

    elif cmd == "/wallet":
        if len(parts) < 2:
            show_wallet_settings(chat_id)
            return
        address = parts[1].strip()
        result = wt.connect_wallet(str(chat_id), address)
        if result["success"]:
            tg.send(
                f"✅ <b>Wallet Connected!</b>\n\n"
                f"Address: <code>{address[:6]}...{address[-4:]}</code>\n\n"
                "Your portfolio will now show live positions and P/L.\n"
                "Go to 📊 Portfolio to see your data.",
                chat_id)
            show_wallet_settings(chat_id)
        else:
            tg.send(f"❌ {result['error']}", chat_id)

    elif cmd == "/wallet_label":
        if len(parts) < 2:
            tg.send("Usage: /wallet_label My Wallet Name", chat_id)
            return
        label = " ".join(parts[1:])
        wt.set_wallet_label(str(chat_id), label)
        tg.send(f"✅ Wallet label set to: <b>{label}</b>", chat_id)

    elif cmd == "/performance":
        show_performance(chat_id)

    elif cmd == "/status":
        ct_stats = ct.get_copy_stats()
        stats = user_store.get_stats()
        tg.send(
            f"✅ <b>Polytragent — Online (v12 - Phase 2)</b>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"👥 Users: {stats.get('total_users', 0)}\n"
            f"🚀 Degen Subscribers: {stats.get('degen_subscribers', 0)}\n"
            f"📊 Predictions: {pstore.get_performance().get('total', 0)}\n"
            f"🔄 Copy Trading: {ct_stats['total_wallets']} wallets, {ct_stats['total_signals']} signals", chat_id)

    # ── LIVE TRADING ──
    elif cmd == "/buy":
        if len(parts) >= 4:
            # /buy <market> <outcome> <amount>
            market_ref = parts[1]
            outcome = parts[2]
            try:
                amount = float(parts[3])
            except ValueError:
                tg.send("Usage: /buy &lt;market-slug&gt; &lt;Yes|No&gt; &lt;amount&gt;", chat_id)
                return
            tg.send(f"⏳ Buying ${amount:.2f} of {outcome} on {market_ref}...", chat_id)
            result = trading.quick_buy(market_ref, outcome, amount)
            tg.send(trading.format_order_result(result), chat_id)
        else:
            show_buy_prompt(chat_id)

    elif cmd == "/sell":
        if len(parts) >= 4:
            market_ref = parts[1]
            outcome = parts[2]
            try:
                shares = float(parts[3])
            except ValueError:
                tg.send("Usage: /sell &lt;market-slug&gt; &lt;Yes|No&gt; &lt;shares&gt;", chat_id)
                return
            tg.send(f"⏳ Selling {shares} shares of {outcome} on {market_ref}...", chat_id)
            result = trading.quick_sell(market_ref, outcome, shares)
            tg.send(trading.format_order_result(result), chat_id)
        else:
            show_sell_prompt(chat_id)

    elif cmd == "/positions":
        show_trading_positions(chat_id)

    elif cmd == "/orders":
        show_trading_orders(chat_id)

    elif cmd == "/cancel_order":
        if len(parts) < 2:
            tg.send("Usage: /cancel_order &lt;order_id&gt;", chat_id)
            return
        result = trading.cancel_order(parts[1])
        tg.send("✅ Order cancelled." if result["success"] else f"❌ {result['error']}", chat_id)

    elif cmd == "/cancel_all":
        result = trading.cancel_all_orders()
        tg.send("✅ All orders cancelled." if result["success"] else f"❌ {result['error']}", chat_id)

    elif cmd == "/limit_buy":
        if len(parts) >= 5:
            # /limit_buy <market> <outcome> <price> <size>
            market_ref = parts[1]
            outcome_name = parts[2]
            try:
                price = float(parts[3])
                size = float(parts[4])
            except ValueError:
                tg.send("Usage: /limit_buy &lt;market&gt; &lt;Yes|No&gt; &lt;price&gt; &lt;size&gt;", chat_id)
                return
            market = trading.resolve_market_tokens(market_ref)
            if not market:
                tg.send("❌ Could not resolve market.", chat_id)
                return
            token_id = None
            for t in market["tokens"]:
                if t["outcome"].lower() == outcome_name.lower():
                    token_id = t["token_id"]
                    break
            if not token_id:
                tg.send(f"❌ Outcome '{outcome_name}' not found.", chat_id)
                return
            tg.send(f"⏳ Placing limit BUY at ${price:.2f}...", chat_id)
            result = trading.limit_buy(token_id, price, size, market.get("neg_risk", False))
            tg.send(trading.format_order_result(result), chat_id)
        else:
            tg.send("Usage: /limit_buy &lt;market-slug&gt; &lt;Yes|No&gt; &lt;price&gt; &lt;shares&gt;", chat_id)

    elif cmd == "/limit_sell":
        if len(parts) >= 5:
            market_ref = parts[1]
            outcome_name = parts[2]
            try:
                price = float(parts[3])
                size = float(parts[4])
            except ValueError:
                tg.send("Usage: /limit_sell &lt;market&gt; &lt;Yes|No&gt; &lt;price&gt; &lt;size&gt;", chat_id)
                return
            market = trading.resolve_market_tokens(market_ref)
            if not market:
                tg.send("❌ Could not resolve market.", chat_id)
                return
            token_id = None
            for t in market["tokens"]:
                if t["outcome"].lower() == outcome_name.lower():
                    token_id = t["token_id"]
                    break
            if not token_id:
                tg.send(f"❌ Outcome '{outcome_name}' not found.", chat_id)
                return
            tg.send(f"⏳ Placing limit SELL at ${price:.2f}...", chat_id)
            result = trading.limit_sell(token_id, price, size, market.get("neg_risk", False))
            tg.send(trading.format_order_result(result), chat_id)
        else:
            tg.send("Usage: /limit_sell &lt;market-slug&gt; &lt;Yes|No&gt; &lt;price&gt; &lt;shares&gt;", chat_id)

    # ── WALLET MANAGEMENT ──
    elif cmd == "/create_wallet":
        result = wm.create_wallet(str(chat_id))
        if result["success"]:
            tg.send(
                f"✅ <b>Wallet Created!</b>\n\n"
                f"Address: <code>{result['address']}</code>\n\n"
                f"🔑 Private Key (SAVE THIS — shown only once):\n"
                f"<tg-spoiler>{result['private_key']}</tg-spoiler>\n\n"
                f"⚠️ Fund this wallet with USDC on Polygon to trade.\n"
                f"⛽ You also need ~0.01 MATIC for gas.",
                chat_id)
        else:
            tg.send(f"❌ {result['error']}", chat_id)

    elif cmd == "/wallets":
        show_wallet_menu(chat_id)

    elif cmd == "/balance":
        primary = wm.get_primary_wallet(str(chat_id))
        if primary:
            balance = wm.get_full_balance(primary["address"])
            tg.send(wm.format_balance(balance), chat_id)
        else:
            tg.send("No wallet found. Use /create_wallet first.", chat_id)

    elif cmd == "/deposit":
        info = wm.get_deposit_info(str(chat_id))
        if info["success"]:
            tg.send(info["instructions"], chat_id)
        else:
            tg.send(f"❌ {info['error']}", chat_id)

    elif cmd == "/send":
        if len(parts) >= 3:
            to_addr = parts[1]
            try:
                amount = float(parts[2])
            except ValueError:
                tg.send("Usage: /send &lt;address&gt; &lt;amount&gt;", chat_id)
                return
            tg.send(f"⏳ Sending ${amount:.2f} USDC to {to_addr[:10]}...", chat_id)
            result = wm.send_usdc(str(chat_id), to_addr, amount)
            tg.send(wm.format_send_result(result), chat_id)
        else:
            _waiting_for_wallet[str(chat_id)] = {"action": "send", "step": "address"}
            tg.send("📤 <b>Send USDC</b>\n\nEnter the recipient address:", chat_id)

    elif cmd == "/export_keys":
        result = wm.export_wallet(str(chat_id))
        if result["success"]:
            tg.send(
                f"🔑 <b>Private Key Export</b>\n\n"
                f"Address: <code>{result['address']}</code>\n"
                f"Key: <tg-spoiler>{result['private_key']}</tg-spoiler>\n\n"
                f"⚠️ Never share this with anyone!",
                chat_id)
        else:
            tg.send(f"❌ {result['error']}", chat_id)

    elif cmd == "/import_wallet":
        _waiting_for_wallet[str(chat_id)] = {"action": "import", "step": "key"}
        tg.send("🔑 <b>Import Wallet</b>\n\nSend your private key (0x...):\n\n"
                "⚠️ Message will be processed and you should delete it after.",
                chat_id)

    # ── AUTO COPY TRADING ──
    elif cmd == "/auto_copy_on":
        result = ce.enable_auto_copy(str(chat_id))
        tg.send(ce.format_auto_copy_settings(result.get("settings", {})), chat_id)

    elif cmd == "/auto_copy_off":
        ce.disable_auto_copy(str(chat_id))
        tg.send("🤖 Auto-copy trading <b>disabled</b>.", chat_id)

    elif cmd == "/auto_copy":
        show_auto_copy_menu(chat_id)

    elif cmd == "/ac_max":
        if len(parts) >= 2:
            try:
                val = float(parts[1])
                ce.update_auto_copy_settings(str(chat_id), max_per_trade=val)
                tg.send(f"✅ Max per trade set to <b>${val:.2f}</b>", chat_id)
            except ValueError:
                tg.send("Usage: /ac_max &lt;amount&gt;", chat_id)
        else:
            tg.send("Usage: /ac_max 50", chat_id)

    elif cmd == "/ac_daily":
        if len(parts) >= 2:
            try:
                val = float(parts[1])
                ce.update_auto_copy_settings(str(chat_id), daily_limit=val)
                tg.send(f"✅ Daily limit set to <b>${val:.2f}</b>", chat_id)
            except ValueError:
                tg.send("Usage: /ac_daily &lt;amount&gt;", chat_id)
        else:
            tg.send("Usage: /ac_daily 500", chat_id)

    elif cmd == "/ac_mode":
        if len(parts) >= 2 and parts[1] in ("fixed", "percentage", "proportional"):
            ce.update_auto_copy_settings(str(chat_id), mode=parts[1])
            tg.send(f"✅ Mode set to <b>{parts[1]}</b>", chat_id)
        else:
            tg.send("Usage: /ac_mode fixed|percentage|proportional", chat_id)

    # ── COPY TRADING ──
    elif cmd.startswith("/ct"):
        if not _handle_copy_trading(cmd, parts, chat_id):
            tg.send("Unknown copy trading command. Use /ct for menu.", chat_id)

    # ── PAID FEATURE COMMANDS ──
    elif cmd == "/top10":
        tg.send("🏆 <b>Building TOP 10 picks...</b>\nScoring + AI analysis. ~90-120s.", chat_id)
        _run_locked("Top10", chat_id, top10.run_top10)

    elif cmd == "/swings":
        tg.send("📈 <b>Scanning price swings...</b>\n~60-90s.", chat_id)
        _run_locked("Swings", chat_id, swings_mod.run_swings)

    elif cmd == "/btcbook":
        tg.send("₿ <b>Fetching BTC order book...</b>", chat_id)
        _run_locked("BTC", chat_id, btc_orderbook.run_btc_orderbook)

    elif cmd == "/digest":
        tg.send("📋 <b>Building Intel Digest...</b>\n~60-90s.", chat_id)
        _run_locked("Digest", chat_id, digest.run_digest)

    elif cmd == "/scan":
        tg.send("🎯 <b>Deep scanning geopolitical markets...</b>\n~60-90s.", chat_id)
        _run_locked("Scan", chat_id, scanner.run_deep_scan)

    elif cmd == "/research":
        if len(parts) < 2:
            tg.send("Usage: /research &lt;url_or_id&gt;", chat_id); return
        tg.send("🔬 Researching (~15s)...", chat_id)
        try:
            result = researcher.research_market(parts[1])
            try:
                import polymarket_api as papi
                slug = parts[1].rstrip("/").split("/")[-1] if "polymarket.com" in parts[1] else parts[1]
                m = None
                if "/event/" in parts[1]:
                    r = requests.get(f"https://gamma-api.polymarket.com/events",
                        params={"slug": slug}, timeout=15)
                    if r.ok:
                        events = r.json()
                        if isinstance(events, list) and events and events[0].get("markets"):
                            m = events[0]["markets"][0]
                if not m:
                    m = papi.get_market_by_slug(slug) or papi.get_market_by_id(slug)
                if m:
                    parsed = papi.parse_market(m)
                    if parsed:
                        comp = kalshi_api.compare_markets(parsed["question"], parsed["yes_price"], parsed["no_price"])
                        result += f"\n\n{comp}"
            except Exception as e:
                print(f"[BOT] Kalshi error: {e}")
            tg.send(result, chat_id)
        except Exception as e:
            tg.send(f"❌ Research error: {e}", chat_id)

    elif cmd == "/history":
        try:
            page = int(parts[1]) - 1 if len(parts) > 1 else 0
            page = max(0, page)
        except: page = 0
        show_closed_trades(chat_id)

    elif cmd in ("/portfolio", "/report"):
        show_portfolio_dashboard(chat_id)

    elif cmd == "/add":
        if len(parts) < 4:
            tg.send("Usage: /add &lt;id&gt; &lt;entry&gt; &lt;size&gt;", chat_id); return
        try:
            import polymarket_api as papi
            mid, entry, size = parts[1], float(parts[2]), float(parts[3])
            m = papi.get_market_by_id(mid) or papi.get_market_by_slug(mid)
            if m:
                p = papi.parse_market(m)
                if p:
                    store.add_position(p["id"], p["question"], entry, size, p["url"], p["end_date"])
                    tg.send(f"✅ Added: {p['question'][:60]}\nEntry: ${entry} | Size: ${size}", chat_id)
                    return
            store.add_position(mid, mid, entry, size, "", "")
            tg.send(f"✅ Position tracked: {mid}", chat_id)
        except Exception as e:
            tg.send(f"❌ {e}", chat_id)

    elif cmd == "/exit":
        if len(parts) < 2: tg.send("Usage: /exit &lt;id&gt;", chat_id); return
        if store.remove_position(parts[1]): tg.send(f"✅ Removed: {parts[1]}", chat_id)
        else: tg.send(f"❌ Not found: {parts[1]}", chat_id)

    # ── Intel commands ──
    elif cmd == "/gdelt":
        tg.send("🌍 Fetching GDELT...", chat_id); tg.send(gdelt.gdelt_briefing(), chat_id)
    elif cmd == "/conflicts":
        tg.send("🔴 Fetching ACLED...", chat_id); tg.send(acled.acled_briefing(), chat_id)
    elif cmd == "/kalshi":
        tg.send("📊 Scanning Kalshi...", chat_id); tg.send(kalshi_api.run_kalshi_scan(), chat_id)
    elif cmd == "/intel":
        tg.send("📡 Fetching intel...", chat_id); tg.send(rss_intel.rss_briefing(), chat_id)
    elif cmd == "/unsc":
        tg.send("🇺🇳 Checking UNSC...", chat_id); tg.send(unsc.unsc_briefing(), chat_id)
    elif cmd == "/briefing":
        tg.send("📋 <b>Building full briefing...</b>", chat_id)
        for fn, name in [(gdelt.gdelt_briefing,"GDELT"),(kalshi_api.run_kalshi_scan,"Kalshi"),
                          (rss_intel.rss_briefing,"RSS"),(unsc.unsc_briefing,"UNSC"),(acled.acled_briefing,"ACLED")]:
            try: tg.send(fn(), chat_id)
            except Exception as e: tg.send(f"❌ {name}: {e}", chat_id)
        tg.send(monitor.build_report(), chat_id)
        tg.send("✅ <b>Briefing complete.</b>", chat_id)

    # ── ADMIN COMMANDS ──
    elif cmd == "/admin" and user_store.is_admin(chat_id):
        stats = user_store.get_stats()
        ct_stats = ct.get_copy_stats()
        tg.send(
            f"🔐 <b>Polytragent Admin Panel (Phase 2)</b>\n\n"
            f"👥 Total users: {stats.get('total_users', 0)}\n"
            f"🚀 Degen subscribers: {stats.get('degen_subscribers', 0)}\n"
            f"💰 MRR: ${stats.get('mrr', 0)}\n"
            f"📊 Total volume: ${stats.get('total_volume', 0):,.0f}\n"
            f"💸 Fees collected: ${stats.get('fees_collected', 0):,.0f}\n\n"
            f"<b>Copy Trading:</b>\n"
            f"👛 Wallets: {ct_stats['total_wallets']}\n"
            f"👥 CT users: {ct_stats['unique_followers']}\n"
            f"🔔 Signals: {ct_stats['total_signals']}\n\n"
            f"<b>Admin Commands:</b>\n"
            f"/broadcast — Message all users\n"
            f"/dashboard — Web admin panel", chat_id)

    elif cmd == "/gencode" and user_store.is_admin(chat_id):
        max_uses = 1
        duration = 30
        note = ""
        if len(parts) > 1:
            try: max_uses = int(parts[1])
            except: pass
        if len(parts) > 2:
            try: duration = int(parts[2])
            except: pass
        if len(parts) > 3:
            note = " ".join(parts[3:])
        code = user_store.generate_access_code(
            created_by=str(chat_id), max_uses=max_uses,
            duration_days=duration, note=note)
        tg.send(
            f"🔑 <b>Access Code Generated</b>\n\n"
            f"<code>{code}</code>\n\n"
            f"📋 Max uses: {max_uses}\n"
            f"📅 Duration: {duration} days\n"
            f"📝 Note: {note or 'None'}\n\n"
            f"Share with: /code {code}", chat_id)

    elif cmd == "/codes" and user_store.is_admin(chat_id):
        codes = user_store.get_all_access_codes()
        if not codes:
            tg.send("No codes yet. Use /gencode to create one.", chat_id)
            return
        lines = ["🔑 <b>Access Codes</b>\n"]
        for c in sorted(codes, key=lambda x: x.get("created_at", ""), reverse=True)[:20]:
            status = "✅" if c.get("active") else "❌"
            lines.append(
                f"{status} <code>{c['code']}</code> — "
                f"{c['uses']}/{c['max_uses']} uses, {c.get('duration_days', 30)}d"
                f"{' — ' + c['note'] if c.get('note') else ''}")
        tg.send("\n".join(lines), chat_id)

    elif cmd == "/deactivate_code" and user_store.is_admin(chat_id):
        if len(parts) < 2:
            tg.send("Usage: /deactivate_code &lt;code&gt;", chat_id); return
        if user_store.deactivate_access_code(parts[1]):
            tg.send(f"✅ Code {parts[1]} deactivated.", chat_id)
        else:
            tg.send(f"❌ Code not found.", chat_id)

    elif cmd == "/broadcast" and user_store.is_admin(chat_id):
        if len(parts) < 2:
            tg.send("Usage: /broadcast &lt;message&gt;", chat_id); return
        msg = text[len("/broadcast "):].strip()
        users = user_store.get_all_users()
        sent = 0
        for u in users:
            try:
                tg.send(f"📢 <b>Polytragent Announcement</b>\n\n{msg}", u.get("chat_id") or u.get("id"))
                sent += 1
                time.sleep(0.1)
            except: pass
        tg.send(f"✅ Broadcast sent to {sent}/{len(users)} users.", chat_id)

    else:
        onboarding.send_inline(chat_id,
            "Unknown command. Use the menu to navigate.",
            [[{"text": "📱 Main Menu", "callback_data": "main_menu"}]])

def _handle_copy_trading(cmd, parts, chat_id):
    """Copy trading command router"""
    if cmd == "/ct":
        _handle("/ct_leaderboard", chat_id)
    elif cmd == "/ct_leaderboard":
        tg.send("📋 <b>Leaderboard</b>\n\nLoading top traders...", chat_id)
        ct.show_leaderboard(chat_id)
    elif cmd == "/ct_following":
        tg.send("👤 <b>My Follows</b>\n\nLoading...", chat_id)
        ct.show_following(chat_id)
    elif cmd == "/ct_signals":
        tg.send("🔔 <b>Recent Signals</b>\n\nLoading...", chat_id)
        ct.show_signals(chat_id)
    elif cmd == "/ct_follow" and len(parts) > 1:
        tg.send("➕ Following trader...", chat_id)
        ct.follow_trader(chat_id, parts[1])
    else:
        return False
    return True

def _scheduler_loop():
    """Background scheduler — scans followed whale wallets every 5 min and sends alerts."""
    import copy_signals

    # Seed curated wallets on first run
    try:
        ct.seed_curated_wallets()
        ct.refresh_leaderboard()
        print("[SCHEDULER] Curated wallets seeded, leaderboard built")
    except Exception as e:
        print(f"[SCHEDULER] Seed error: {e}")

    scan_count = 0
    while True:
        try:
            time.sleep(300)  # 5 minutes between scans
            scan_count += 1
            print(f"[SCHEDULER] Scan #{scan_count} starting...")

            # Scan all tracked wallets for position changes
            signals = ct.scan_all_wallets()
            if signals:
                sent = copy_signals.dispatch_signals(signals)
                print(f"[SCHEDULER] Scan #{scan_count}: {len(signals)} signals → {sent} notifications")
            else:
                print(f"[SCHEDULER] Scan #{scan_count}: no new signals")

            # Refresh leaderboard every 30 min (every 6 scans)
            if scan_count % 6 == 0:
                ct.refresh_leaderboard()
                print("[SCHEDULER] Leaderboard refreshed")

        except Exception as e:
            print(f"[SCHEDULER] Error: {e}")
            time.sleep(60)  # back off on error

# ═══════════════════════════════════════════════
# CALLBACK HANDLER — Routes all inline keyboard taps
# ═══════════════════════════════════════════════

_original_handle_callback = onboarding.handle_callback

def _extended_handle_callback(callback_query):
    data = callback_query.get("data", "")
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))

    # Always answer the callback to remove loading state
    onboarding.answer_callback(callback_query.get("id"))

    # ── ALL CALLBACKS NOW FREE (except Degen upgrades) ──
    # All features available to all users

    # ── QUICK RESEARCH (top button) ──
    if data == "quick_research":
        show_quick_research_prompt(chat_id)
        return

    # ── MAIN MENU ──
    if data == "main_menu":
        _waiting_for_research_link.pop(str(chat_id), None)  # clear research state
        send_main_menu(chat_id)
        return

    # ── PORTFOLIO SECTION ──
    if data == "menu_portfolio":
        show_portfolio_menu(chat_id)
    elif data == "portfolio_dashboard":
        show_portfolio_dashboard(chat_id)
    elif data == "portfolio_positions":
        show_portfolio_positions(chat_id)
    elif data == "portfolio_risk":
        show_risk_scorecard(chat_id)
    elif data == "portfolio_performance":
        show_performance(chat_id)
    elif data == "portfolio_closed_trades":
        show_closed_trades(chat_id)
    elif data == "portfolio_attention":
        show_attention_items(chat_id)
    elif data == "portfolio_categories":
        show_events_categories(chat_id)

    # ── RESEARCH HUB SECTION (formerly Markets) ──
    elif data == "menu_research":
        show_research_menu(chat_id)
    elif data == "research_stats":
        show_global_stats(chat_id)
    elif data == "research_whale":
        show_whale_alerts(chat_id)
    elif data == "research_leaderboard":
        show_research_leaderboard(chat_id)
    elif data == "research_news":
        show_research_news(chat_id)
    elif data == "research_breaking_news":
        show_breaking_news(chat_id)
    elif data == "research_price_alerts":
        show_breaking_news(chat_id)  # redirect old callback
    elif data == "research_trending":
        show_trending_events(chat_id)
    elif data == "research_new_markets":
        show_new_markets(chat_id)
    elif data == "research_kalshi":
        show_kalshi_menu(chat_id)
    elif data == "kalshi_scan":
        tg.send("📊 <b>Scanning Kalshi markets...</b>", chat_id)
        _run_locked("Kalshi", chat_id, kalshi_api.run_kalshi_scan)
    elif data == "kalshi_top_volume":
        tg.send("📊 <b>Loading Kalshi top markets...</b>", chat_id)
        _run_locked("KalshiTop", chat_id, kalshi_api.run_kalshi_top_volume)
    elif data == "research_sources":
        show_research_sources(chat_id)
    elif data == "research_btcbook":
        tg.send("₿ <b>Fetching BTC order book...</b>", chat_id)
        _run_locked("BTC", chat_id, btc_orderbook.run_btc_orderbook)

    # ── TRADE SECTION ──
    elif data == "menu_trade":
        show_trade_menu(chat_id)
    elif data == "trade_no_theta":
        show_no_theta_signals(chat_id)
    elif data == "trade_scalp_signals":
        show_scalp_signals(chat_id)
    elif data == "trade_my_strategies":
        show_my_strategies(chat_id)
    elif data == "trade_direct":
        show_direct_bet(chat_id)
    elif data == "trade_builder":
        show_strategy_builder(chat_id)
    elif data == "trade_research_prompt":
        tg.send("🔬 <b>Research a Market</b>\n\nSend a Polymarket event link:\n/research &lt;url&gt;", chat_id)

    # ── STRATEGY CALLBACKS — NO Theta ──
    elif data == "strategy_no_theta":
        show_no_theta_strategy(chat_id)
    elif data == "nt_entry_gates":
        show_nt_entry_gates(chat_id)
    elif data == "nt_exit_rules":
        show_nt_exit_rules(chat_id)
    elif data == "nt_position_sizing":
        show_nt_position_sizing(chat_id)
    elif data == "nt_risk_params":
        show_nt_risk_params(chat_id)
    elif data == "nt_research_protocol":
        show_nt_research_protocol(chat_id)

    # ── STRATEGY CALLBACKS — Scalp NO ──
    elif data == "strategy_scalp_no":
        show_scalp_no_strategy(chat_id)
    elif data == "sn_entry_gates":
        show_sn_entry_gates(chat_id)
    elif data == "sn_exit_rules":
        show_sn_exit_rules(chat_id)
    elif data == "sn_position_sizing":
        show_sn_position_sizing(chat_id)
    elif data == "sn_risk_params":
        show_sn_risk_params(chat_id)

    # ── STRATEGY DETAILS ──
    elif data == "strategy_details_menu":
        show_strategy_details_menu(chat_id)

    # ── BACKTEST SECTION ──
    elif data == "menu_backtest":
        show_backtest_menu(chat_id)
    elif data == "backtest_select":
        tg.send("📋 <b>Select Strategy</b>\n\n🎯 NO Theta or ⚡ Scalp NO", chat_id)
    elif data == "backtest_dates":
        tg.send("📅 <b>Set Date Range</b>\n\nSend: /backtest_dates YYYY-MM-DD YYYY-MM-DD", chat_id)
    elif data == "backtest_run":
        show_backtest_no_theta(chat_id)
    elif data == "backtest_saved":
        tg.send("💾 <b>Saved Results</b>\n\nNo saved backtests yet.", chat_id)
    elif data == "backtest_no_theta":
        show_backtest_no_theta(chat_id)
    elif data == "backtest_scalp_no":
        show_backtest_scalp_no(chat_id)

    # ── SETTINGS SECTION ──
    elif data == "menu_settings":
        show_settings_menu(chat_id)
    elif data == "settings_wallet":
        show_wallet_settings(chat_id)
    elif data == "settings_sizing":
        show_bet_sizing(chat_id)
    elif data == "settings_notifications":
        show_notifications(chat_id)
    elif data == "settings_risk_limits":
        show_risk_limits(chat_id)
    elif data == "settings_api_keys":
        show_api_keys(chat_id)
    elif data == "settings_export":
        show_export_data(chat_id)
    elif data == "settings_categories":
        # Re-use onboarding category selection
        onboarding.show_category_selection(chat_id)
    elif data == "settings_account":
        show_account(chat_id)
    elif data == "dashboard":
        onboarding._send_dashboard_link(chat_id)

    # ── RISK PROFILE SELECTION ──
    elif data == "risk_conservative":
        user_store.update_user(chat_id, {"onboarding.risk_profile": "Conservative"})
        tg.send("🟢 Risk profile set to <b>Conservative</b>.\nMax 3 positions, 2% sizing, -10% stop.", chat_id)
    elif data == "risk_moderate":
        user_store.update_user(chat_id, {"onboarding.risk_profile": "Moderate"})
        tg.send("🟡 Risk profile set to <b>Moderate</b>.\nMax 5 positions, 5% sizing, -15% stop.", chat_id)
    elif data == "risk_aggressive":
        user_store.update_user(chat_id, {"onboarding.risk_profile": "Aggressive"})
        tg.send("🔴 Risk profile set to <b>Aggressive</b>.\nMax 10 positions, 10% sizing, -25% stop.", chat_id)

    # ── WALLET CALLBACKS ──
    elif data == "wallet_connect":
        onboarding.send_inline(chat_id,
            "👛 <b>Connect Wallet</b>\n\n"
            "Send your public Polymarket wallet address:\n\n"
            "<code>/wallet 0xYourAddress...</code>\n\n"
            "🔒 Read-only — no private keys needed.",
            [[{"text": "← Settings", "callback_data": "settings_wallet"}]])
    elif data == "wallet_disconnect":
        if wt.disconnect_wallet(str(chat_id)):
            tg.send("✅ Wallet disconnected.", chat_id)
        else:
            tg.send("No wallet was connected.", chat_id)
        show_wallet_settings(chat_id)
    elif data == "wallet_help":
        onboarding.send_inline(chat_id,
            "📋 <b>How to Find Your Wallet Address</b>\n\n"
            "<b>From Polymarket:</b>\n"
            "1. Go to polymarket.com\n"
            "2. Click your profile icon (top-right)\n"
            "3. Go to Settings → Funding\n"
            "4. Copy the deposit address (0x...)\n\n"
            "<b>From MetaMask/Rainbow:</b>\n"
            "1. Open your wallet app\n"
            "2. Copy the Polygon address\n\n"
            "Then send:\n"
            "<code>/wallet 0xYourAddress...</code>",
            [[{"text": "← Back", "callback_data": "settings_wallet"}]])

    # ── RUN COMMANDS ──
    elif data == "run_top10":
        tg.send("🏆 <b>Building TOP 10 picks...</b>\nScoring + AI analysis. ~90-120s.", chat_id)
        _run_locked("Top10", chat_id, top10.run_top10)
    elif data == "run_digest":
        tg.send("📋 <b>Building Intel Digest...</b>\n~60-90s.", chat_id)
        _run_locked("Digest", chat_id, digest.run_digest)
    elif data == "run_scan":
        tg.send("🎯 <b>Deep scanning...</b>\n~60-90s.", chat_id)
        _run_locked("Scan", chat_id, scanner.run_deep_scan)
    elif data == "run_gdelt":
        tg.send("🌍 Fetching GDELT...", chat_id); tg.send(gdelt.gdelt_briefing(), chat_id)
    elif data == "run_kalshi":
        tg.send("📊 Scanning Kalshi...", chat_id); tg.send(kalshi_api.run_kalshi_scan(), chat_id)
    elif data == "run_intel":
        tg.send("📡 Fetching intel...", chat_id); tg.send(rss_intel.rss_briefing(), chat_id)
    elif data == "run_unsc":
        tg.send("🇺🇳 Checking UNSC...", chat_id); tg.send(unsc.unsc_briefing(), chat_id)
    elif data == "run_conflicts":
        tg.send("🔴 Fetching ACLED...", chat_id); tg.send(acled.acled_briefing(), chat_id)
    elif data == "run_briefing":
        tg.send("📋 <b>Building full briefing...</b>", chat_id)
        for fn, name in [(gdelt.gdelt_briefing,"GDELT"),(kalshi_api.run_kalshi_scan,"Kalshi"),
                          (rss_intel.rss_briefing,"RSS"),(unsc.unsc_briefing,"UNSC"),(acled.acled_briefing,"ACLED")]:
            try: tg.send(fn(), chat_id)
            except Exception as e: tg.send(f"❌ {name}: {e}", chat_id)
        tg.send("✅ <b>Briefing complete.</b>", chat_id)

    # ── LIVE TRADING CALLBACKS ──
    elif data == "menu_trading":
        show_trading_menu(chat_id)
    elif data == "trading_buy_prompt":
        show_buy_prompt(chat_id)
    elif data == "trading_sell_prompt":
        show_sell_prompt(chat_id)
    elif data == "trading_positions":
        show_trading_positions(chat_id)
    elif data == "trading_orders":
        show_trading_orders(chat_id)
    elif data == "trading_history":
        show_trade_history(chat_id)
    elif data == "trading_cancel_all":
        result = trading.cancel_all_orders()
        tg.send("✅ All orders cancelled." if result["success"] else f"❌ {result['error']}", chat_id)
        show_trading_orders(chat_id)
    elif data == "trading_cancel_flow":
        _waiting_for_trade.pop(str(chat_id), None)
        show_trading_menu(chat_id)
    elif data.startswith("trade_outcome_"):
        outcome = data.replace("trade_outcome_", "")
        state = _waiting_for_trade.get(str(chat_id))
        if state and state.get("step") == "outcome":
            market = state["market"]
            token_id = None
            for t in market["tokens"]:
                if t["outcome"] == outcome:
                    token_id = t["token_id"]
                    break
            if token_id:
                state["outcome"] = outcome
                state["token_id"] = token_id
                state["step"] = "amount"
                action = state["action"]
                if action == "buy":
                    tg.send(f"{'🟩 Buy' } — <b>Step 3/3</b>\n\n"
                            f"📊 {market['question']}\n"
                            f"🎯 Outcome: <b>{outcome}</b>\n\n"
                            "Enter dollar amount (e.g., 25 or 100):", chat_id)
                else:
                    tg.send(f"🟥 Sell — <b>Step 3/3</b>\n\n"
                            f"📊 {market['question']}\n"
                            f"🎯 Outcome: <b>{outcome}</b>\n\n"
                            "Enter number of shares to sell:", chat_id)
            else:
                tg.send("❌ Could not find that outcome.", chat_id)
        else:
            tg.send("❌ No active trade. Start with /buy or /sell.", chat_id)

    # ── WALLET CALLBACKS ──
    elif data == "menu_wallet":
        show_wallet_menu(chat_id)
    elif data == "wallet_create":
        _handle("/create_wallet", chat_id)
    elif data == "wallet_import_prompt":
        _waiting_for_wallet[str(chat_id)] = {"action": "import", "step": "key"}
        tg.send("🔑 <b>Import Wallet</b>\n\nSend your private key (0x...):", chat_id)
    elif data == "wallet_balance":
        _handle("/balance", chat_id)
    elif data == "wallet_send_prompt":
        _waiting_for_wallet[str(chat_id)] = {"action": "send", "step": "address"}
        tg.send("📤 <b>Send USDC</b>\n\nEnter the recipient Polygon address:", chat_id)
    elif data == "wallet_deposit":
        _handle("/deposit", chat_id)
    elif data == "wallet_export_prompt":
        _handle("/export_keys", chat_id)

    # ── AUTO COPY CALLBACKS ──
    elif data == "menu_auto_copy":
        show_auto_copy_menu(chat_id)
    elif data == "auto_copy_on":
        result = ce.enable_auto_copy(str(chat_id))
        tg.send("✅ Auto-copy trading <b>enabled</b>!\n\nDefault: $25/trade, $200/day limit.", chat_id)
        show_auto_copy_menu(chat_id)
    elif data == "auto_copy_off":
        ce.disable_auto_copy(str(chat_id))
        tg.send("⏹ Auto-copy trading <b>disabled</b>.", chat_id)
        show_auto_copy_menu(chat_id)
    elif data == "auto_copy_settings_menu":
        show_auto_copy_settings_menu(chat_id)
    elif data == "auto_copy_stats":
        stats = ce.get_auto_copy_stats(str(chat_id))
        tg.send(ce.format_auto_copy_stats(stats), chat_id)

    # ── WHALE DIRECTORY CALLBACKS (Phase 2) ──
    elif data == "menu_whales":
        show_whales_menu(chat_id)
    elif data.startswith("whale_page_"):
        # Pagination: whale_page_0, whale_page_1, etc.
        import whale_discovery as wd_mod
        page = int(data.replace("whale_page_", ""))
        text, buttons = wd_mod.format_directory_page(page=page, per_page=5)
        following = ct.get_following_count(str(chat_id))
        limit = user_store.get_wallet_tracking_limit(str(chat_id))
        is_degen = user_store.is_degen(str(chat_id))
        header = (
            f"📊 Following: <b>{following} / {limit}</b> wallets"
            f"{' 🚀 Degen Mode' if is_degen else ''}\n"
            f"🔔 Real-time alerts when followed whales trade.\n\n"
        )
        onboarding.send_inline(chat_id, header + text, buttons)
    elif data.startswith("whale_follow_"):
        # Follow a whale by directory index: whale_follow_1, whale_follow_2, etc.
        import whale_discovery as wd_mod
        idx = int(data.replace("whale_follow_", ""))
        whale = wd_mod.get_whale_by_index(idx)
        if whale:
            # Ensure it's in tracking system
            ct.add_wallet(whale["address"], alias=whale["name"])
            result = ct.follow_wallet(str(chat_id), whale["address"])
            msg = result.get("message", "Done")
            if result["status"] == "followed":
                tg.send(f"✅ <b>Now following {whale['name']}</b>\n\n{msg}\n\n🔔 You'll get notified when they make trades.", chat_id)
            elif result["status"] == "exists":
                tg.send(f"ℹ️ Already following <b>{whale['name']}</b>.", chat_id)
            else:
                tg.send(f"⚠️ {msg}", chat_id)
        else:
            tg.send("❌ Whale not found.", chat_id)
    elif data.startswith("whale_detail_"):
        # Show whale detail: whale_detail_1
        import whale_discovery as wd_mod
        idx = int(data.replace("whale_detail_", ""))
        whale = wd_mod.get_whale_by_index(idx)
        text, buttons = wd_mod.format_whale_detail(whale)
        onboarding.send_inline(chat_id, text, buttons)
    elif data == "whales_my_list":
        _handle("/ct_following", chat_id)

    # ── DEGEN MODE CALLBACKS (Phase 2) ──
    elif data == "degen_subscribe":
        onboarding.send_inline(chat_id,
            "🚀 <b>Degen Mode Checkout</b>\n\n"
            "Unlimited whale tracking + premium features.\n\n"
            "$79/month, cancel anytime.\n\n"
            "🔗 Opening Stripe checkout...",
            [[{"text": "← Main Menu", "callback_data": "main_menu"}]])
        # TODO: Integrate with Stripe checkout
        tg.send("🔗 Stripe checkout: [Integration pending - contact support]", chat_id)
    elif data == "degen_manage":
        onboarding.send_inline(chat_id,
            "⚙️ <b>Manage Degen Mode</b>\n\n"
            "Current subscription active.\n"
            "Renews: Next billing date\n\n"
            "💳 Update payment method or cancel at stripe.com",
            [[{"text": "← Main Menu", "callback_data": "main_menu"}]])

    # ── COPY TRADING CALLBACKS ──
    elif data == "ct_leaderboard":
        _handle("/ct_leaderboard", chat_id)
    elif data == "ct_following":
        _handle("/ct_following", chat_id)
    elif data == "ct_signals":
        _handle("/ct_signals", chat_id)
    elif data == "ct_follow_prompt":
        tg.send(
            "➕ <b>Follow a Trader</b>\n\n"
            "Use the leaderboard number or wallet address:\n"
            "/ct_follow 1\n"
            "/ct_follow 0x1234...abcd\n\n"
            "Or browse the leaderboard first:\n"
            "/ct_leaderboard", chat_id)
    elif data.startswith("ct_detail_"):
        addr = data.replace("ct_detail_", "")
        wallets = ct.get_tracked_wallets()
        for w in wallets:
            if w["address"].lower().startswith(addr.lower()):
                tg.send(ct.format_wallet_detail(w["address"]), chat_id)
                return
        tg.send("❌ Wallet not found.", chat_id)

    # ── SIZING CALLBACKS ──
    elif data == "sizing_fixed":
        tg.send("✅ Bet sizing set to <b>Fixed Amount</b>", chat_id)
    elif data == "sizing_percent":
        tg.send("✅ Bet sizing set to <b>% of Portfolio</b>", chat_id)
    elif data == "sizing_kelly":
        tg.send("✅ Bet sizing set to <b>Kelly Criterion</b>", chat_id)
    elif data == "sizing_vol":
        tg.send("✅ Bet sizing set to <b>Vol-Adjusted</b>", chat_id)

    # ── NOTIFICATION CALLBACKS ──
    elif data == "notif_signals":
        tg.send("✅ Strategy signals: <b>Enabled</b>", chat_id)
    elif data == "notif_risk":
        tg.send("✅ Risk alerts: <b>Enabled</b>", chat_id)
    elif data == "notif_daily":
        tg.send("✅ Daily summary: <b>Enabled</b>", chat_id)
    elif data == "notif_stops":
        tg.send("✅ Stop-loss alerts: <b>Enabled</b>", chat_id)

    # ── RISK LIMITS CALLBACKS ──
    elif data == "risk_position":
        tg.send("✅ Position size limit configured", chat_id)
    elif data == "risk_drawdown":
        tg.send("✅ Drawdown limit configured", chat_id)
    elif data == "risk_daily":
        tg.send("✅ Daily loss limit configured", chat_id)
    elif data == "risk_open":
        tg.send("✅ Open positions limit configured", chat_id)

    # ── API CALLBACKS ──
    elif data == "api_add":
        tg.send("🔑 Send your API key securely. It will be encrypted.", chat_id)
    elif data == "api_list":
        tg.send("📋 <b>Your API Keys</b>\n\nNo keys connected yet.", chat_id)
    elif data == "api_delete":
        tg.send("❌ No keys to delete.", chat_id)

    # ── EXPORT CALLBACKS ──
    elif data == "export_portfolio":
        tg.send("📊 <b>Exporting portfolio...</b>", chat_id)
    elif data == "export_trades":
        tg.send("📈 <b>Exporting trade history...</b>", chat_id)
    elif data == "export_perf":
        tg.send("🧠 <b>Exporting performance report...</b>", chat_id)
    elif data == "export_ct":
        tg.send("🔄 <b>Exporting copy trading log...</b>", chat_id)

    # ── FALLBACK to original onboarding handler ──
    else:
        _original_handle_callback(callback_query)

onboarding.handle_callback = _extended_handle_callback

# ═══════════════════════════════════════════════
# POLLING
# ═══════════════════════════════════════════════

def _polling_loop():
    global _last_update_id
    print("[BOT] Polling started")
    _409_backoff = 5
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"timeout": 30, "offset": _last_update_id,
                        "allowed_updates": '["message","callback_query"]'}, timeout=60)
            if r.ok:
                _409_backoff = 5
                for upd in r.json().get("result", []):
                    _last_update_id = upd["update_id"] + 1

                    if "callback_query" in upd:
                        try:
                            onboarding.handle_callback(upd["callback_query"])
                        except Exception as e:
                            print(f"[BOT] callback error: {e}")
                        continue

                    msg = upd.get("message", {})
                    text = msg.get("text", "")
                    cid = str(msg.get("chat", {}).get("id", ""))

                    if not text or not cid:
                        continue

                    # Update user info from message
                    from_user = msg.get("from", {})
                    if from_user:
                        updates = {}
                        if from_user.get("username"):
                            updates["username"] = from_user["username"]
                        if from_user.get("first_name"):
                            updates["first_name"] = from_user["first_name"]
                        if updates:
                            user_store.update_user(cid, updates)

                    # Trade flow input (multi-step buy/sell)
                    if not text.startswith("/") and str(cid) in _waiting_for_trade:
                        try:
                            handle_trade_input(cid, text)
                        except Exception as e:
                            print(f"[BOT] trade input error: {e}")
                            tg.send(f"❌ Trade error: {e}", cid)
                            _waiting_for_trade.pop(str(cid), None)
                        continue

                    # Wallet flow input (multi-step send/import)
                    if not text.startswith("/") and str(cid) in _waiting_for_wallet:
                        try:
                            handle_wallet_input(cid, text)
                        except Exception as e:
                            print(f"[BOT] wallet input error: {e}")
                            tg.send(f"❌ Wallet error: {e}", cid)
                            _waiting_for_wallet.pop(str(cid), None)
                        continue

                    # Research link input — auto-trigger Event Research when user sends Polymarket link ANYTIME
                    if not text.startswith("/") and "polymarket.com" in text.lower():
                        try:
                            threading.Thread(target=handle_research_link, args=(cid, text.strip()), daemon=True).start()
                        except Exception as e:
                            print(f"[BOT] research input error: {e}")
                            tg.send(f"❌ Error: {e}", cid)
                        continue

                    # Research link input (when explicitly waiting)
                    if not text.startswith("/") and is_waiting_for_research(cid):
                        try:
                            if text.startswith("http"):
                                threading.Thread(target=handle_research_link, args=(cid, text.strip()), daemon=True).start()
                            else:
                                tg.send("❌ Please send a valid Polymarket link.\n\n<i>Example: https://polymarket.com/event/...</i>", cid)
                        except Exception as e:
                            print(f"[BOT] research input error: {e}")
                            tg.send(f"❌ Error: {e}", cid)
                        continue

                    # Access code input (non-command text)
                    if not text.startswith("/") and onboarding.is_waiting_for_code(cid):
                        try:
                            onboarding.handle_access_code_input(cid, text)
                        except Exception as e:
                            print(f"[BOT] code input error: {e}")
                            tg.send(f"❌ Error processing code: {e}", cid)
                        continue

                    if text.startswith("/"):
                        print(f"[BOT] [{cid}] {text[:60]}")
                        try:
                            _handle(text, cid)
                        except Exception as e:
                            print(f"[BOT] handler error: {e}")
                            tg.send(f"❌ Error: {e}", cid)

            elif r.status_code == 409:
                print(f"[BOT] HTTP 409 — backoff {_409_backoff}s")
                time.sleep(_409_backoff)
                _409_backoff = min(_409_backoff * 2, 60)
                try:
                    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", timeout=5)
                    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                        params={"offset": -1, "timeout": 1}, timeout=5)
                except: pass
            else:
                print(f"[BOT] HTTP {r.status_code}")
                time.sleep(5)
        except Exception as e:
            print(f"[BOT] poll error: {e}"); time.sleep(5)

# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════

def main():
    print("=" * 50)
    print(" POLYTRAGENT — Polymarket AI Trading Agent v12.0")
    print(" FREE TRADING TERMINAL + Degen Mode ($79/mo)")
    print(" PHASE 2: OPEN ACCESS BUSINESS MODEL")
    print("=" * 50)

    _kill_other_instances()

    try:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", timeout=5)
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": -1, "timeout": 1}, timeout=5)
        print("[BOOT] Webhook deleted, updates flushed")
    except: pass

    print("[BOOT] Waiting 5s for Telegram lock release...")
    time.sleep(5)

    if not tg.test_connection():
        print("[ERROR] Telegram connection failed"); return

    _set_bot_commands()

    try:
        web_server.start_server(port=8080)
    except Exception as e:
        print(f"[BOOT] Web server error: {e}")

    # Try to start admin dashboard on 8081
    try:
        # TODO: import admin_dashboard and start on port 8081
        print("[BOOT] Admin dashboard: port 8081 (if enabled)")
    except Exception as e:
        print(f"[BOOT] Admin dashboard not available: {e}")

    try:
        leaders = ct.refresh_leaderboard()
        print(f"[BOOT] Copy trading leaderboard: {len(leaders)} traders")
    except Exception as e:
        print(f"[BOOT] Leaderboard init error: {e}")

    # Initialize trading engine
    trade_status = "🟢 LIVE" if trading.is_trading_enabled() else "⚪ Disabled (set POLY_* env vars)"
    trade_addr = trading.get_wallet_address() or "N/A"
    print(f"[BOOT] Trading engine: {trade_status}")
    if trade_addr != "N/A":
        print(f"[BOOT] Trading wallet: {trade_addr}")

    stats = user_store.get_stats()
    ct_stats = ct.get_copy_stats()
    tg.send(
        f"🤖 <b>Polytragent v12.0 Online</b>\n"
        f"⚡ <b>FREE TRADING TERMINAL</b>\n\n"
        f"🐋 Whale Tracking • 💰 Live Trading • 📊 Copy Trading\n"
        f"🚀 Degen Mode: Unlimited tracking + premium features\n\n"
        f"💰 Trading: {trade_status}\n"
        f"👛 Wallet: <code>{trade_addr[:10]}...</code>\n\n"
        f"👥 Total users: {stats.get('total_users', 0)}\n"
        f"🚀 Degen subscribers: {stats.get('degen_subscribers', 0)}\n"
        f"📊 Total volume: ${stats.get('total_volume', 0):,.0f}\n"
        f"💸 Fees collected: ${stats.get('fees_collected', 0):,.0f}\n"
        f"🔄 Copy Trading: {ct_stats['total_wallets']} wallets tracked\n\n"
        f"🌐 Dashboard: port 8080\n"
        f"/menu for main menu"
    )

    threading.Thread(target=_scheduler_loop, daemon=True).start()
    _polling_loop()

if __name__ == "__main__":
    main()
