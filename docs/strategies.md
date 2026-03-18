# Strategies

The Strategies section provides AI-powered trading signals based on proven quantitative strategies for Polymarket.

## Accessing Strategies

1. Send `/menu`
2. Tap **📈 Strategies**

## Available Strategies

```
🎯 NO Theta Signals
⚡ Scalping Signals
← Main Menu
```

---

## NO Theta Decay

**Strategy Code:** `NO_THETA_V1`
**Nickname:** "Sell the Fear"

### Core Thesis

Most deadline events ("Will X happen by [date]?") do NOT happen. YES shares carry a fear premium that decays as the deadline approaches. By buying NO, you harvest this premium — similar to selling out-of-the-money puts in traditional finance.

### Edge Source

Retail traders overestimate the probability of dramatic events. Expert forecasters (Metaculus, Swift Centre) consistently price YES lower than Polymarket, creating an exploitable gap.

### Target Markets

Geopolitics, military, regime change, ceasefire, policy deadline, economic threshold events.

### Target KPIs

| Metric | Target |
|--------|--------|
| Win Rate | 88% |
| Monthly Return | 12-18% |
| Sharpe Ratio | 2.5-3.5 |
| Trades/Month | 5-8 |

### Entry Gates (All 6 Required)

1. **Market Type** — Binary deadline-based YES/NO market
2. **Time to Resolution** — ≥14 days (≥7 days if edge >10pts)
3. **NO Price** — Between $0.55 and $0.90
4. **Edge** — ≥5 points above market NO price
5. **Volume** — ≥$50K lifetime AND ≥$5K 24h
6. **No Catalyst** — No material catalyst within 48 hours

### Exit Rules

| Exit Type | Condition | Action |
|-----------|-----------|--------|
| Take Profit | NO ≥ $0.93 | Sell all via limit |
| Time Exit | 5-7 days before deadline | Sell all at market |
| Adverse Catalyst | Credible news + >5% move | Sell immediately |
| Stop-Loss Review | NO < $0.65 | Alert — user decides |
| Hard Stop-Loss | NO < $0.50 | Auto-sell or urgent alert |

### Position Sizing — Half-Kelly

```
Full Kelly: f* = (bp - q) / b
where b = (1/NO_price - 1), p = P(NO), q = 1 - p
Size = (f*/2) × portfolio_balance
```

- Minimum bet: $10
- Maximum bet: $30 or 15% of portfolio
- Always keep 15-20% cash uninvested

### Risk Parameters

- Max concurrent positions: 4-6
- Max per category: 2
- Daily loss limit: 10% of portfolio
- Drawdown breaker: 25% from peak

---

## Scalping NO Theta

**Strategy Code:** `SCALP_NO_V1`
**Nickname:** "Quick Harvest"

### Core Thesis

In the final 3-14 days before deadline, theta decay accelerates exponentially. NO shares become increasingly certain — harvest 3-5 cent gains with minimal risk, multiple times per week.

### Target KPIs

| Metric | Target |
|--------|--------|
| Win Rate | 85-95% |
| Monthly Return | 5-12% |
| Sharpe Ratio | 2.0-4.0 |
| Trades/Month | 12-32 |

### Entry Gates

1. **Market Type** — Binary deadline market
2. **Time to Deadline** — 3-14 days (late stage)
3. **NO Price** — Between $0.88 and $0.96
4. **Spread** — Less than 2 cents
5. **24h Volume** — ≥$10K
6. **No Major News** — Stable, low catalyst risk

### Exit Rules

| Exit Type | Condition | Action |
|-----------|-----------|--------|
| Take Profit | Entry + 3 cents | Auto limit sell at entry |
| Time Exit | 2 days before deadline | Market sell |
| Soft Stop | NO drops 5 cents | Alert — review |
| Hard Stop | NO drops 10 cents | Auto-sell or alert |

### Position Sizing — Fixed 2%

- 2% of portfolio per scalp
- Minimum bet: $5
- Maximum bet: $20 or 5% of portfolio
- Max concurrent scalps: 4

### Risk Parameters

- Max daily scalp loss: 3% of portfolio
- Cooldown: 4 hours after 2 consecutive losses
- Capital rotation: Very fast, same day

---

## Strategy Comparison

| | NO Theta | Scalp NO |
|---|----------|----------|
| Hold Period | 14-28 days | 2-7 days |
| NO Price Range | $0.55-$0.90 | $0.88-$0.96 |
| Edge Needed | ≥5 points | ≥3 points |
| TP Target | NO ≥ $0.93 | Entry + 3¢ |
| Sizing | Half-Kelly | Fixed 2% |
| Max Bet | $30 | $20 |
| Best For | Larger edge, patient | Quick turns, high freq |
