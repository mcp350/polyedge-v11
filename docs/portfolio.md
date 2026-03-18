# Portfolio

The Portfolio section gives you full visibility into your Polymarket positions, performance, and risk exposure.

## Accessing Portfolio

1. Send `/menu`
2. Tap **📊 Portfolio**

## Portfolio Sub-Menu

```
📋 Dashboard
📂 Open Positions    📜 Closed Trades
🛡 Risk Scorecard    ⚠️ Attention Items
📁 Events Categories
← Main Menu
```

## Dashboard

The dashboard is your portfolio overview. What you see depends on whether you've connected a wallet.

### With Wallet Connected
- Live portfolio value from Polymarket
- Open positions count and total value
- Unrealized P&L
- AI signal performance (total predictions, win rate)
- Copy trading stats (following count)
- Quick links to Positions, Recent Trades, and Web Dashboard

### Without Wallet
- Manual position tracking summary
- AI performance stats
- Copy trading overview
- Prompt to connect wallet for live data

## Open Positions

Shows all your current active positions on Polymarket.

**With wallet:** Live data pulled directly from your Polymarket wallet — position sizes, entry prices, current prices, and unrealized P&L.

**Without wallet:** Manual positions you've tracked via `/add` command. Includes market name, entry price, and size.

### Adding Manual Positions

If you don't want to connect a wallet, you can manually track positions:

```
/add <market_id> <entry_price> <size>
```

## Closed Trades

View your trade history with P&L for each closed position.

**With wallet:** Live activity data from your wallet showing recent trades, settlements, and realized P&L.

**Without wallet:** AI prediction history showing resolved markets, outcomes, and performance.

## Risk Scorecard

A comprehensive risk assessment of your current portfolio:

- **Portfolio Summary** — Open positions count, total invested
- **Diversification Score** — Good (5+ positions, <40% concentration), Moderate (3+ positions), or Low
- **Max Concentration** — Percentage in single largest category
- **Category Breakdown** — How your positions are distributed
- **Risk Limits** — Max position (25%), drawdown breaker (-25%), cash reserve target (15%)
- **Attention Items** — Warnings for high concentration or excessive positions

## Attention Items

Positions that need your immediate attention:

- Approaching deadline
- Stop-loss triggered (current price <80% of entry)
- High exposure warnings

## Events Categories

Browse and filter markets by category. Categories are set during onboarding and can be updated in Settings. Categories include: Geopolitics, Military, Economics, Elections, Crypto, Sports, Entertainment, and more.

## Wallet Connection

For the best portfolio experience, connect your Polymarket wallet. See [Settings — Wallet Connection](settings.md#wallet-connection) for setup instructions.
