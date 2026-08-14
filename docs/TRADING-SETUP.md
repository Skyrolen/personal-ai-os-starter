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

| Read | Trade |
|---|---|
| `get_accounts`, `get_portfolio`, `get_equity_positions`, `get_equity_quotes`, `get_equity_orders`, `search` | `review_equity_order`, `place_equity_order`, `cancel_equity_order` |

`review_equity_order` simulates and returns pre-trade warnings; it moves no
money. `place_equity_order` is the only one that does, and per Rule 0 it needs
your explicit approval of that exact ticket.

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
.venv/bin/pip install -r tools/requirements.txt
```

Confirm it works:

```bash
.venv/bin/python tools/test_indicators.py   # 30 checks, offline
.venv/bin/python tools/test_verify.py       # 18 groups, offline
.venv/bin/python tools/verify.py --ticker AAPL --call-price 200 --call-date 2026-08-01
```

The two test files run offline against synthetic data. The third needs network
access to Yahoo Finance — if it fails with a connection error, your network is
blocking `query1.finance.yahoo.com`.

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
| `/bora-check [ticker or pasted post]` | Verify one of his calls, produce a sized ticket, stop for approval |
| `/portfolio` | Read-only review of holdings against policy |
| `/trade-log log` | Journal a decision |
| `/trade-log review` | Weekly scoring, including his hit rate |

---

## What this does not do

- **No backtesting of his historical record.** That needs his timestamped call
  history, which isn't available. The scorecard builds that record going
  forward instead — expect it to mean nothing for the first ~20 calls.
- **No options, crypto, or futures.** Robinhood's agentic MCP is equities-only
  in beta.
- **No intraday or real-time execution.** yfinance data is delayed, and the
  design targets Bora's quarterly horizon. Don't repurpose this for day trading;
  none of the checks are calibrated for it.
- **No advice.** It verifies, sizes, and reports. Every trade remains your
  decision and your risk, and a trade that passes every check can still lose the
  whole position.
