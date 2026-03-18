# Settings

Configure your Polytragent experience — wallet, sizing, notifications, risk limits, and more.

## Accessing Settings

1. Send `/menu`
2. Tap **⚙️ Settings**

## Settings Sub-Menu

```
👛 Connect Wallet
📐 Bet Sizing
🔔 Notifications
🛡 Risk Limits
🔑 API Keys
📥 Export Data
← Main Menu
```

---

## Wallet Connection

Connect your Polymarket wallet for live portfolio tracking. This is **100% read-only** — no private keys are ever needed or stored.

### How to Connect

1. Go to **Settings → Connect Wallet**
2. Find your public wallet address (see below)
3. Send: `/wallet 0xYourAddress...`

### How to Find Your Wallet Address

**From Polymarket:**
1. Go to polymarket.com
2. Click your profile icon (top-right)
3. Go to Settings → Funding
4. Copy the deposit address (starts with 0x...)

**From MetaMask/Rainbow:**
1. Open your wallet app
2. Tap your account name to copy address
3. Paste it to the bot

### What Gets Tracked
- Live open positions
- Unrealized and realized P&L
- Trade history and activity
- Portfolio value over time

### Security
- Only your **public** address is stored
- No signing, no custody, no trading access
- Same as viewing your wallet on Polygonscan
- You can disconnect anytime

### Managing Your Wallet
- **Rename:** `/wallet_label My Trading Wallet`
- **Change:** Tap "Change Wallet" in wallet settings
- **Disconnect:** Tap "Disconnect" button

---

## Bet Sizing

Choose how Polytragent calculates position sizes for strategy signals.

### Available Methods

| Method | Description |
|--------|-------------|
| **Half-Kelly** (default) | Mathematically optimal sizing based on edge and odds |
| **Fixed Amount** | Same dollar amount every trade |
| **% of Portfolio** | Scale with your account size |
| **Vol-Adjusted** | Scale based on market volatility |

### Recommended

Half-Kelly is the default and recommended for most users. It automatically sizes larger on higher-edge opportunities and smaller on marginal ones.

---

## Notifications

Control which alerts you receive:

| Alert Type | Description |
|------------|-------------|
| 🎯 Strategy Signals | New trading opportunities from AI |
| ⚠️ Risk Alerts | Portfolio risk warnings |
| 💰 Daily Summary | End-of-day portfolio recap |
| 🔴 Stop-Loss | When a position hits stop-loss |

All notifications are enabled by default. Toggle any category on/off.

---

## Risk Limits

Set hard boundaries for your account to prevent excessive losses:

| Limit | Default | Description |
|-------|---------|-------------|
| Max Position Size | 25% of portfolio | Largest single position allowed |
| Daily Loss Limit | 10% of portfolio | Stops new trades after this daily loss |
| Max Drawdown | 25% from peak | Pauses trading if portfolio drops this much |
| Max Open Positions | 6 | Maximum concurrent positions |
| Min Cash Reserve | 15% | Always keep this much uninvested |

---

## API Keys

Connect external services for advanced features:
- Polymarket API access
- Trading automation webhooks
- Price feed integrations

Keys are encrypted and stored securely. Never share your API keys publicly.

---

## Export Data

Download your Polytragent data in standard formats:

| Export | Format |
|--------|--------|
| Portfolio | CSV |
| Trade History | CSV |
| Performance Report | PDF |
| Copy Trading Log | JSON |

All your data belongs to you. Export anytime.
