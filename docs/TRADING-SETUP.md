# Financial Agent — Setup

Connects Claude to a Robinhood agentic account and to Bora Özkent's subscriber
content, with a verification layer in between.

**Read `50_Finance/trading-policy.md` before you use any of this.** The design
assumption throughout is that the agent proposes and you approve — never that it
trades on its own.

---

## 1. Robinhood agentic account

Agentic Trading is **US-only, beta, equities-only** (options and crypto are
rolling out). Robinhood emails customers when they're eligible.

1. Open a **dedicated agentic account** in the Robinhood app and fund it with
   only what you're willing to let an agent touch. Your main account stays
   invisible to the agent — that separation is the product's core safety
   feature, so don't undermine it by over-funding the agentic side.
2. In Robinhood's settings, **enable per-order approval** and set a spending
   limit. Do this even though `/bora-check` already stops for approval: this
   gate is server-side and does not depend on Claude behaving correctly.
3. Connect the MCP server:

```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
```

4. Complete the OAuth flow in the browser. Claude never sees your password.
5. Verify with `/portfolio`. If it reads your balance, you're connected.

### Tools this exposes

| Purpose | Tools |
|---|---|
| Account | `get_accounts`, `get_portfolio`, `get_equity_positions`, `get_equity_orders`, `get_equity_tax_lots` |
| Market data | `get_equity_historicals` (OHLCV), `get_equity_quotes`, `get_equity_price_book` (L2), `get_equity_technical_indicators` |
| Fundamentals | `get_equity_fundamentals`, `get_financials`, `get_earnings_results`, `get_earnings_calendar` |
| Pre-trade | `get_equity_tradability`, `review_equity_order` |
| Execution | `place_equity_order`, `cancel_equity_order` |
| Performance | `get_realized_pnl`, `get_pnl_trade_history` |

`review_equity_order` simulates and returns pre-trade warnings; it moves no
money. `place_equity_order` is the only one that does, and per Rule 0 it needs
your explicit approval of that exact ticket.

**All market data comes from here** — no third-party feed. Robinhood's
tradability and price-book data are authoritative for the venue we execute on,
which no external source can be.

### Your account layout

Recorded in `50_Finance/private/risk-profile.json` (gitignored):

| Account | Type | Agent access |
|---|---|---|
| ••••2975 "Agentic" | cash | **yes** — the only one the agent can touch |
| ••••9457 (default) | margin | no (`agentic_allowed: false`) |
| ••••7439 | Roth IRA | no (`agentic_allowed: false`) |

The agentic account is a **cash** account: T+1 settlement, good-faith
violations apply, and `option_level` is empty so it is equities-only.

Always pass the account number explicitly. Never let the agent default to
whatever `get_accounts` returns first.

---

## 2. Chrome profile for Bora's content

Claude reads his Skool posts and site through **your** browser session, so you
never hand over credentials.

Since Chrome 136, remote debugging is blocked on the default profile (it was
being abused to steal cookies). Use a dedicated one:

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-claude-profile"

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/chrome-claude-profile"
```

Log into Skool and boraozkent.net **in that window**. The session persists
between restarts.

Then add the MCP server:

```bash
claude mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest --browserUrl http://127.0.0.1:9222
```

> **An agent connected this way inherits every logged-in session in that
> profile.** Keep email, banking, and your main Google account out of it. This
> profile should contain Skool and his site, and nothing else.

His content is subscriber-only: read it for your own decisions, keep extracts in
`50_Finance/private/` (gitignored), and don't republish it.

---

## 3. Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt   # pandas + numpy only
```

Confirm it works:

```bash
.venv/bin/python tools/test_indicators.py   # 30 checks
.venv/bin/python tools/test_verify.py       # 23 groups
```

Both run fully offline. **Nothing under `tools/` touches the network** — the
agent fetches market data via MCP and writes `market.json`; Python only does
arithmetic on it. That keeps the policy math deterministic and impossible to
run against accidentally-stale live state.

## 3a. Your risk profile

`50_Finance/private/risk-profile.json` (gitignored) holds the numbers the
public policy file deliberately omits:

```json
{
  "risk_base": 20000.0,
  "agentic_account": "539562975"
}
```

**`risk_base` is the number that controls your risk.** Position size is 5% of
it — not 5% of the agentic balance. That distinction is the point: you fund the
agentic account per trade, so sizing off its balance would let a transfer
inflate the cap. See Rule 1a in the policy.

Raising `risk_base` raises every position cap at once. Change it deliberately,
on a day you are not placing a trade.

---

## 4. Permissions

Add to `.claude/settings.json` so the tools run without a prompt each time:

```json
{
  "permissions": {
    "allow": ["Bash(.venv/bin/python tools/*)"]
  }
}
```

---

## 5. Commands

| Command | What it does |
|---|---|
| `/bora-daily` | Morning delta scan, signals, two ranked candidate lists |
| `/bora-green TICKER` | Green-flag one name: full check → ticket → your approval → place |
| `/bora-scan` | Crawl his platform + Skool into `50_Finance/private/bora/` |
| `/bora-portfolio` | Diff his book against yours, sleeve by sleeve |
| `/bora-check [ticker or pasted row]` | Verify one position, propose a size, stop for approval |
| `/bora-backtest` | Score his transaction ledger vs SPY/QQQ; trim quality; tag predictiveness |
| `/portfolio` | Read-only review of your holdings against policy |
| `/trade-log log` | Journal a decision |
| `/trade-log review` | Weekly scoring, including his hit rate and your overrides |

Daily rhythm: **daily → green**. `/bora-daily` does its own delta scan and
leaves you a ranked list; `/bora-green` acts on one name from it.

Deeper passes when you want them: **scan → portfolio → backtest**. The full
scan writes files the others read, so later commands need far less browsing.

### What the scan collects

Everything lands in `50_Finance/private/bora/` (gitignored):

| File | Content |
|---|---|
| `snapshots/YYYY-MM-DD-dashboard.md` | header stats, sleeve allocation, all five position tables |
| `transactions.md` | append-only ledger from `İşlem Geçmişi` — the real signal feed |
| `method.md` | his philosophy and exit rules, in his words |
| `skool/YYYY-MM-DD-posts.md` | recent + pinned posts, tickers flagged |
| `watchlist.md` | what he holds that you don't, sorted by K/Z ascending |

`method.md` is the one worth capturing carefully. With his stated rules on file,
`/bora-check` can ask whether his *current behaviour matches his own method* —
a position held well through a level he says he exits at is a signal that market
data alone cannot show you.

---

## 6. The daily loop

### Scheduling (launchd, not cron)

Claude Code's `CronCreate` jobs live only in the session that made them, vanish
when Claude exits, and auto-expire after 7 days. For a real every-morning job
use launchd:

```bash
sed "s|__REPO__|$HOME/projects/serifs-os|g" \
  scripts/com.serifs.bora-daily.plist.template \
  > ~/Library/LaunchAgents/com.serifs.bora-daily.plist
launchctl load ~/Library/LaunchAgents/com.serifs.bora-daily.plist
```

Fires 07:12 on weekdays. `scripts/bora-daily.sh` launches the Chrome debug
profile if port 9222 is down, waits for it, then runs `claude -p "/bora-daily"`.
Logs land in `50_Finance/private/logs/`.

Run it now, check it, or remove it:

```bash
launchctl start com.serifs.bora-daily
launchctl list | grep bora
launchctl unload ~/Library/LaunchAgents/com.serifs.bora-daily.plist
```

### Push notifications (ntfy.sh)

`PushNotification` needs a live Claude session and Remote Control, so it cannot
fire from a headless job. ntfy can:

1. Install the **ntfy** app (iOS/Android).
2. **Generate** a topic — do not invent one by hand, and do not paste an
   example from any document including this one:

```bash
openssl rand -hex 16
```

3. Write it into `50_Finance/private/notify.json` (gitignored) and subscribe to
   that exact string in the ntfy app:

```bash
echo "{\"ntfy_topic\": \"$(openssl rand -hex 16)\", \"include_tickers\": true}" \
  > 50_Finance/private/notify.json
cat 50_Finance/private/notify.json
```

`notify.sh` refuses to send if the topic is a known placeholder or shorter than
20 characters. **The topic string is the only thing protecting these pushes** —
there is no auth on ntfy, so anyone who guesses it reads everything.

4. Test without sending: `scripts/notify.sh --dry-run "test"`

> **An ntfy topic is readable by anyone who guesses the name.** The push carries
> actions and tickers only. `notify.sh` refuses any message containing a dollar
> amount — balances and position sizes never leave the machine.

Pushes fire **only when there are signals**. Quiet mornings are silent on
purpose; a channel that pings daily is one you stop reading.

### The loop

```
/bora-daily   (or launchd, each weekday morning)
      |
      +-- signals: his buys, sells, tag changes, thesis posts
      +-- FROM BORA        - his names, ranked by takeability
      +-- SCREENER         - filter matches, no thesis from him
      |
/bora-green TICKER
      |
      +-- full /bora-check
      +-- review_equity_order  ->  real ticket + warnings
      +-- YOUR APPROVAL        ->  place_equity_order
```

**When it can't scan, it says so.** If Chrome is down or the Skool/platform
session has expired, the run pushes "re-login needed", writes a `-FAILED.md`
record, and stops. It will not emit a quiet-day report on a failed scan — a
brief that says "no new signals" because nobody was logged in is worse than no
brief, because you would believe it.

Sessions expire periodically and no automation can log back in for you. That
re-login is the one recurring manual step in the whole system.

---

## What this does not do

- **The backtest scores his *realised trading only*** — closed round-trips from
  the platform's transaction ledger, FIFO-matched, benchmarked against SPY/QQQ
  over identical windows. It does not score his newsletter picks, his options
  sleeve, or positions opened before the ledger begins; buckets with small n
  carry their n and prove nothing. (An earlier version of this document said
  backtesting was impossible for lack of timestamped history — the platform's
  transaction ledger *is* that history, so that claim was wrong and `/bora-backtest`
  supersedes it.)
- **Rankings are takeability, not forecasts.** The daily list answers "can I
  take this cleanly today at my size", never "will this go up". A high score
  means low mechanical friction and nothing more.
- **Screener names carry no thesis from him.** They are rendered in a separate
  labelled section; taking one is independent stock-picking.
- **No transfers.** The agent reports the exact amount to move between accounts;
  you move it. It has no transfer tools and must never be given any.
- **No options or crypto** in the agentic account — its `option_level` is empty.
- **No intraday or real-time execution.** The design targets Bora's quarterly
  horizon. Don't repurpose this for day trading; none of the checks are
  calibrated for it, and the agentic account is a cash account (T+1) anyway.
- **No advice.** It verifies, sizes, and reports. Every trade remains your
  decision and your risk, and a trade that passes every check can still lose the
  whole position.
