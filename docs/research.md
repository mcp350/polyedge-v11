# Research

The Research section is your market intelligence hub — live data from Polymarket including trending events, new markets, whale tracking, breaking news, and global statistics.

## Accessing Research

1. Send `/menu`
2. Tap **🔬 Research**

## Research Sub-Menu

```
📈 Trending Events
🆕 New Markets
🐋 Whale Alerts
📰 Breaking News
📊 Global Stats
← Main Menu
```

---

## Trending Events

Shows the top 10 most active events on Polymarket right now, sorted by **24-hour trading volume**.

For each event you'll see:
- Event title
- 24h trading volume
- Current YES price
- Direct link to Polymarket

**Data source:** Live from Polymarket Gamma API, refreshed on each tap.

**Use this for:** Finding the hottest markets where money is flowing right now.

---

## New Markets

Displays markets that were **created within the past 24 hours** on Polymarket.

For each new market:
- Event title
- Current volume
- Direct link

**Use this for:** Getting in early on new opportunities before the crowd.

---

## Whale Alerts

Track top Polymarket wallets and get **push notifications** when whales from your Follows list make trades.

### Whale Alerts Sub-Menu

```
🏆 Monthly Leaderboard
➕ Add Whale Wallet
👤 My Follows
← Research
```

### Monthly Leaderboard

Top Polymarket wallets ranked by **profit and loss over 30 days**. Each entry shows:
- Wallet address (abbreviated)
- Total P&L for the month
- Trading volume
- Option to follow the wallet

### Add Whale Wallet

Follow a new trader by their wallet address:
- Use a leaderboard number: `/ct_follow 1`
- Use a wallet address: `/ct_follow 0x1234...abcd`

Once followed, you'll get push notifications whenever they execute trades.

### My Follows

View all wallets you're currently following:
- Wallet addresses
- When you started following
- Recent signals from each wallet

**How notifications work:** When a wallet on your Follows list makes a trade on Polymarket, you receive an instant Telegram notification with the trade details — market, side (YES/NO), and size.

---

## Breaking News

Pulls the latest high-volume events from Polymarket, showing what's making news in prediction markets right now.

For each item:
- Event headline
- 24h trading volume
- Direct Polymarket link

**Data source:** Polymarket Gamma API sorted by recent volume activity.

---

## Global Stats

A simple dashboard with Polymarket-wide statistics:

### Polymarket Data
- Active events count
- Active markets count
- Total trading volume

### Polytragent Data
- Total users
- Active subscribers
- AI predictions count and win rate
- Tracked wallets

**Use this for:** Understanding the overall Polymarket ecosystem at a glance.
