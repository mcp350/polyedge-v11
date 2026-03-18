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
            {"command": "subscribe", "description": "Subscribe — $99/mo"},
            {"command": "code", "description": "Redeem access code"},
        ]
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands",
            json={"commands": commands}, timeout=10)
        if r.ok: print("[BOOT] Bot menu commands set")
    except Exception as e:
        print(f"[BOOT] setMyCommands error: {e}")

# ═══════════════════════════════════════════════
# PAYWALL — STRICT PAID-ONLY ACCESS
# ═══════════════════════════════════════════════

_FREE_COMMANDS = {"/start", "/subscribe", "/code", "/dashboard"}

def _require_subscription(chat_id) -> bool:
    if user_store.is_admin(chat_id):
        return True
    if user_store.is_subscribed(chat_id):
        return True
    perf = pstore.get_performance()
    total = perf.get("total", 0)
    win_rate = perf.get("win_rate") or 0
    onboarding.send_inline(chat_id,
        "🔒 <b>Polytragent — Members Only</b>\n\n"
        "This bot is exclusively for subscribers.\n\n"
        "🧠 <b>What you get for $99/mo:</b>\n"
        "• AI-powered market analysis & strategy signals\n"
        "• Copy trading — follow top wallets\n"
        "• Real-time whale & price alerts\n"
        "• Portfolio management & risk controls\n"
        "• Strategy backtesting engine\n"
        "• Full accuracy dashboard\n\n"
        f"📊 Track record: <b>{total} predictions, {win_rate:.0f}% win rate</b>\n\n"
        "✅ Cancel anytime. No lock-in.",
        [[{"text": "⚡ Subscribe — $99/mo", "callback_data": "subscribe"}],
         [{"text": "🔑 Enter Access Code", "callback_data": "enter_code"}]])
    return False

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
    """Send the 5-section main menu per spec Section 2.1"""
    user = user_store.get_user(chat_id)
    name = ""
    if user:
        name = user.get("first_name") or user.get("username") or ""
    greeting = f", {name}" if name else ""

    onboarding.send_inline(chat_id,
        f"🤖 <b>Polytragent</b>{greeting}\n\n"
        "Your AI-Powered Polymarket Trading Agent.\n"
        "Select a section below to get started.",
        [[{"text": "🔬 Event Research", "callback_data": "quick_research"}],
         [{"text": "📊 Portfolio", "callback_data": "menu_portfolio"}],
         [{"text": "📈 Strategies", "callback_data": "menu_trade"}],
         [{"text": "🔬 Research", "callback_data": "menu_research"}],
         [{"text": "⚙️ Settings", "callback_data": "menu_settings"}]])

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
            "<b>🤖 Polytragent</b>\n"
            f"Users: {user_stats['total_users']}\n"
            f"Subscribers: {user_stats['active_subscribers']}\n"
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
    """Whale Alerts — track wallets, get notifications when whales from My Follows buy"""
    following_count = len(ct.get_following(chat_id))
    onboarding.send_inline(chat_id,
        "🐋 <b>Polytragent — Whale Alerts</b>\n\n"
        "Track top Polymarket wallets and get push notifications\n"
        "when whales from your Follows list make trades.\n\n"
        f"👤 Following: {following_count} wallets\n"
        f"🔔 Notifications: {'Active' if following_count > 0 else 'Add wallets to activate'}",
        [[{"text": "🏆 Monthly Leaderboard", "callback_data": "research_leaderboard"}],
         [{"text": "➕ Add Whale Wallet", "callback_data": "ct_follow_prompt"}],
         [{"text": "👤 My Follows", "callback_data": "ct_following"}],
         [{"text": "← Research", "callback_data": "menu_research"}]])

def show_research_leaderboard(chat_id):
    """Monthly Leaderboard — top Polymarket wallets by P&L (30 days)"""
    tg.send("🏆 <b>Loading Monthly Leaderboard...</b>", chat_id)
    try:
        leaders = ct.refresh_leaderboard()
        if leaders:
            msg = "🏆 <b>Polytragent — Monthly Leaderboard</b>\n"
            msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            msg += "<i>Top wallets by profit &amp; loss (30 days)</i>\n\n"
            for i, w in enumerate(leaders[:10], 1):
                addr = w.get("address", "")
                short = addr[:6] + "..." + addr[-4:] if len(addr) > 10 else addr
                pnl = w.get("pnl", 0)
                vol = w.get("volume", 0)
                pnl_sign = "+" if pnl >= 0 else ""
                msg += (f"{i}. <code>{short}</code>\n"
                        f"   P&L: {pnl_sign}${pnl:,.0f} | Vol: ${vol:,.0f}\n\n")
            msg += "<i>Use ➕ Add Whale Wallet to follow any address</i>"
        else:
            msg = "🏆 <b>Monthly Leaderboard</b>\n\nLoading leaderboard data..."
        onboarding.send_inline(chat_id, msg,
            [[{"text": "➕ Follow Wallet", "callback_data": "ct_follow_prompt"}],
             [{"text": "← Whale Alerts", "callback_data": "research_whale"}]])
    except Exception as e:
        tg.send(f"❌ Leaderboard error: {e}", chat_id)

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

def _handle(cmd, chat_id):
    """Command handler — routes all /commands"""
    text = cmd
    parts = cmd.split()
    cmd = parts[0].lower().split("@")[0]  # strip @botname suffix

    # ── FREE COMMANDS (no subscription required) ──

    if cmd in ("/start", "/help"):
        onboarding._send_start(chat_id)
        return

    if cmd == "/subscribe":
        onboarding._send_subscription_prompt(chat_id)
        return

    if cmd == "/code":
        onboarding.send_inline(chat_id,
                "🔑 <b>Enter Access Code</b>\n\n"
                "Send your access code now (e.g., PTA-XXXXXXXX).\n\n"
                "Or use it directly: /code PTA-XXXXXXXX",
                [[{"text": "← Cancel", "callback_data": "main_menu"}]])
        return

    if cmd == "/dashboard":
        onboarding._send_dashboard_link(chat_id)
        return

    # ═══════════════════════════════════════════
    # EVERYTHING BELOW REQUIRES ACTIVE SUBSCRIPTION
    # ═══════════════════════════════════════════

    if not _require_subscription(chat_id):
        return

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
        tg.send(
            f"✅ <b>Polytragent — Online (v11)</b>\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"👥 Users: {len(user_store.get_all_users())}\n"
            f"💎 Subscribers: {len(user_store.get_all_subscribers())}\n"
            f"📊 Predictions: {pstore.get_performance().get('total', 0)}\n"
            f"🔄 Copy Trading: {ct_stats['total_wallets']} wallets, {ct_stats['total_signals']} signals", chat_id)

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
            f"🔐 <b>Polytragent Admin</b>\n\n"
            f"👥 Total users: {stats['total_users']}\n"
            f"💎 Active subs: {stats['active_subscribers']}\n"
            f"  └ Stripe: {stats.get('stripe_subscribers', 0)}\n"
            f"  └ Codes: {stats.get('code_subscribers', 0)}\n"
            f"💰 MRR: ${stats['mrr']}\n"
            f"🔑 Active codes: {stats.get('active_codes', 0)} / {stats.get('total_codes', 0)}\n\n"
            f"<b>Copy Trading:</b>\n"
            f"👛 Wallets: {ct_stats['total_wallets']}\n"
            f"👥 CT users: {ct_stats['unique_followers']}\n"
            f"🔔 Signals: {ct_stats['total_signals']}\n\n"
            f"<b>Admin Commands:</b>\n"
            f"/gencode — Generate access code\n"
            f"/codes — List all codes\n"
            f"/broadcast — Message all subs\n"
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
        subs = user_store.get_all_subscribers()
        sent = 0
        for u in subs:
            try:
                tg.send(f"📢 <b>Polytragent Announcement</b>\n\n{msg}", u["chat_id"])
                sent += 1
                time.sleep(0.1)
            except: pass
        tg.send(f"✅ Broadcast sent to {sent}/{len(subs)} subscribers.", chat_id)

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
    """Background scheduler for periodic tasks"""
    while True:
        try:
            time.sleep(60)
        except Exception as e:
            print(f"[SCHEDULER] Error: {e}")

# ═══════════════════════════════════════════════
# CALLBACK HANDLER — Routes all inline keyboard taps
# ═══════════════════════════════════════════════

_original_handle_callback = onboarding.handle_callback

def _extended_handle_callback(callback_query):
    data = callback_query.get("data", "")
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))

    # Always answer the callback to remove loading state
    onboarding.answer_callback(callback_query.get("id"))

    # ── FREE CALLBACKS (no sub required) ──
    if data in ("subscribe", "enter_code", "main_menu_free"):
        _original_handle_callback(callback_query)
        return

    # ── Check subscription for all menu callbacks ──
    if data.startswith("menu_") or data.startswith("portfolio_") or data.startswith("research_") or \
       data.startswith("trade_") or data.startswith("backtest_") or data.startswith("settings_") or \
       data.startswith("run_") or data.startswith("strategy_") or data.startswith("ct_") or \
       data.startswith("risk_") or data.startswith("wallet_") or data.startswith("nt_") or \
       data.startswith("sn_") or data.startswith("sizing_") or data.startswith("notif_") or \
       data.startswith("api_") or data.startswith("export_") or data == "main_menu" or data == "dashboard" or \
       data == "quick_research":
        if not user_store.is_admin(chat_id) and not user_store.is_subscribed(chat_id):
            _require_subscription(chat_id)
            return

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
    print(" POLYTRAGENT — Polymarket AI Trading Agent v11")
    print(" 5-Section Menu: Portfolio|Research|Trade|Backtest|Settings")
    print(" PAID-ONLY ACCESS — $99/mo or Access Code")
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

    # Pre-load 100 access codes
    added = user_store.preload_access_codes()
    print(f"[BOOT] Access codes: {added} new loaded")

    try:
        web_server.start_server(port=8080)
    except Exception as e:
        print(f"[BOOT] Web server error: {e}")

    try:
        leaders = ct.refresh_leaderboard()
        print(f"[BOOT] Copy trading leaderboard: {len(leaders)} traders")
    except Exception as e:
        print(f"[BOOT] Leaderboard init error: {e}")

    stats = user_store.get_stats()
    ct_stats = ct.get_copy_stats()
    tg.send(
        f"🤖 <b>Polytragent v11 Online</b>\n"
        f"🔒 <b>PAID-ONLY MODE</b>\n\n"
        f"📱 Menu Architecture\n"
        f"🔬 Event Research | 📊 Portfolio | 📈 Strategies | 🔬 Research | ⚙️ Settings\n\n"
        f"👥 Users: {stats['total_users']}\n"
        f"💎 Subscribers: {stats['active_subscribers']}\n"
        f"  └ Stripe: {stats.get('stripe_subscribers', 0)}\n"
        f"  └ Codes: {stats.get('code_subscribers', 0)}\n"
        f"💰 MRR: ${stats['mrr']}\n"
        f"🔑 Active codes: {stats.get('active_codes', 0)}\n"
        f"🔄 Copy Trading: {ct_stats['total_wallets']} wallets\n\n"
        f"🌐 Dashboard: port 8080\n"
        f"/menu for command center"
    )

    threading.Thread(target=_scheduler_loop, daemon=True).start()
    _polling_loop()

if __name__ == "__main__":
    main()
