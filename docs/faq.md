# FAQ

## General

### What is Polytragent?
Polytragent is an AI-powered trading agent for Polymarket, the world's largest prediction market. It runs inside Telegram and provides market research, portfolio tracking, trading strategy signals, and whale alerts.

### How much does it cost?
$79.99/month. Cancel anytime, no lock-in. You can also use an access code if you have one.

### Is my data safe?
Yes. We only store your Telegram chat ID and public wallet address (if connected). No private keys, no passwords, no trading access. Wallet connection is 100% read-only.

### Can Polytragent trade for me?
No. Polytragent provides research, signals, and alerts. All trading decisions and executions are made by you on Polymarket directly.

## Wallet & Portfolio

### Do I need to connect a wallet?
No, but it's recommended. Without a wallet, you can still use Event Research, Strategies, Research, and manual position tracking. With a wallet connected, you get live positions, P&L, and automated portfolio analytics.

### Is wallet connection safe?
Yes. We only use your **public** wallet address — the same address anyone can look up on Polygonscan. No private keys, no signing, no custody.

### How do I find my wallet address?
Go to polymarket.com → Profile → Settings → Funding → Copy your deposit address (starts with 0x).

### Can I disconnect my wallet?
Yes. Go to Settings → Wallet → Disconnect. Your wallet data is removed immediately.

## Strategies

### What is NO Theta Decay?
A strategy that profits from buying NO on deadline events where the outcome is unlikely to happen. The "fear premium" in YES prices decays over time, and NO holders profit as the deadline approaches without the event occurring.

### What is Scalping NO Theta?
A faster version of NO Theta that targets markets in their final 3-14 days before deadline, harvesting small 3-5 cent gains with high win rate and quick capital rotation.

### Are the strategies guaranteed to profit?
No. All trading involves risk. The strategies are based on historical patterns and quantitative analysis, but past performance does not guarantee future results. Always manage your risk and never trade more than you can afford to lose.

## Research & Alerts

### How does Event Research work?
Paste any Polymarket event link in the chat. The AI analyzes market data, expert forecaster opinions, whale positions, news context, and market microstructure to produce a comprehensive report with a recommendation.

### How do Whale Alerts work?
Follow top Polymarket wallets from the Monthly Leaderboard or by adding addresses manually. When a followed wallet executes a trade, you receive an instant Telegram notification.

### What is Breaking News?
A feed of the highest-volume events on Polymarket right now, giving you a quick view of what's making headlines in prediction markets.

## Subscription

### How do I cancel?
Go to Settings → Manage Subscription, or send `/manage` in the bot. You'll receive a link to manage your Stripe subscription.

### Can I get a refund?
Contact the Polytragent team for refund requests. Refunds are handled on a case-by-case basis.

### My access code isn't working
Make sure you're entering the full code in the format `PTA-XXXXXXXX`. Each code can only be used once. Contact the person who gave you the code if it doesn't work.

## Technical

### The bot isn't responding
Wait 30 seconds and try again. If the issue persists, the bot may be restarting. Try again in a few minutes.

### I see "Unknown command"
Make sure you're using a valid command. Send `/menu` to access all features through the menu interface.

### Research is taking too long
Full AI research takes 30-60 seconds. If it exceeds 90 seconds, try again. Some events with limited data may take longer.
