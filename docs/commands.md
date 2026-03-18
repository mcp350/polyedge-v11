# Bot Commands

## Menu Commands

These commands appear in Telegram's command menu (tap the `/` button):

| Command | Description |
|---------|-------------|
| `/menu` | Open the main menu |
| `/subscribe` | Subscribe — $99/mo |
| `/code` | Redeem an access code |

## Additional Commands

These commands work when typed directly in the chat:

### Portfolio & Trading

| Command | Description |
|---------|-------------|
| `/research <url>` | Deep research on a Polymarket event |
| `/add <id> <entry> <size>` | Track a manual position |
| `/exit <id>` | Close a tracked position |
| `/performance` | View AI prediction performance |
| `/manage` | Manage your subscription |
| `/account` | View account details |
| `/dashboard` | Open web dashboard |

### Wallet

| Command | Description |
|---------|-------------|
| `/wallet <address>` | Connect your Polymarket wallet |
| `/wallet_label <name>` | Set a label for your wallet |

### Copy Trading

| Command | Description |
|---------|-------------|
| `/ct_leaderboard` | View top trader leaderboard |
| `/ct_following` | View wallets you're following |
| `/ct_signals` | View recent copy trading signals |
| `/ct_follow <number or address>` | Follow a trader |
| `/ct_unfollow <address>` | Unfollow a trader |

### Admin Commands

These commands are only available to bot administrators:

| Command | Description |
|---------|-------------|
| `/admin` | Admin panel |
| `/broadcast <message>` | Send announcement to all subscribers |

## Auto-Detection

The bot automatically detects certain inputs without needing a command:

- **Polymarket links** — Paste any `polymarket.com` URL and Event Research runs automatically
- **Access codes** — When prompted, simply type your `PTA-XXXXXXXX` code
