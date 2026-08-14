# Portfolio

Read-only review of the Robinhood agentic account against policy.

**This command never places or cancels an order.** It reports and proposes; I act.

## Instructions

### 1. Read the rules

Read `50_Finance/trading-policy.md` first. Every limit below comes from there.

### 2. Pull live state

Read `50_Finance/private/risk-profile.json` for the agentic account number and
risk base. **Pass that account number explicitly to every call.** My main
margin account and Roth IRA are `agentic_allowed: false` — if you find yourself
reading either, stop and tell me.

Then: `get_portfolio`, `get_equity_positions`, `get_equity_orders`, and
`get_accounts` for `unsettled_funds`.

For each holding: `get_equity_quotes` for the current price, and the sector
(look it up if Robinhood doesn't supply it).

### 3. Check each position against its thesis

Cross-reference `50_Finance/private/trade-log.md` for why each position was
opened and what its invalidation level was.

For a fresh technical read on a holding, pull `get_equity_historicals`
(`interval: "day"`, ~300 sessions) plus `get_earnings_results`, and use
`get_realized_pnl` / `get_pnl_trade_history` for closed-trade performance.

All percentages are against the **risk base** from `risk-profile.json`, not the
agentic account balance — the balance moves when I transfer cash, so measuring
against it would make concentration look better simply because I funded a trade.

### 4. Report

## Portfolio — YYYY-MM-DD

### Summary
Account value, cash, settled cash, day P&L, total P&L, positions used of 10.

### Positions
| Ticker | Shares | Cost | Now | P&L | % of acct | Invalidation | Status |

Status is `OK`, `WATCH`, or `BREACH`.

### Policy check
- Any position over the cap (5% of the risk base)
- Position count vs the 10 limit
- **Sector concentration** — if tech is over 30%, say so plainly and say what it
  means: those names fall together, so the real number of independent bets here
  is smaller than the position count suggests.
- Unsettled cash that would block a new entry

### Invalidation breaches
Any position trading below its recorded invalidation level. State the level, the
current price, and how long it has been breached.

**A breach is not a suggestion to sell.** It means the reason I bought no longer
holds. Say that, and let me decide.

### Earnings in the next 10 days
Ticker, date, and current position size — so nothing walks into a print unnoticed.

### Positions with no recorded thesis
Anything held that isn't in the trade log. These are the ones most likely to be
drifting, because nothing was ever written down to check against.

### What I'd look at first
One or two items, most important first. Specific.

## Notes

- Report losses as plainly as gains. Do not lead with the winners.
- If the account is down more than 15% from its high-water mark, or the last 3
  closed trades were losses, say so prominently — those are the circuit-breaker
  conditions in Rule 10.
- Do not propose new positions here. That's `/bora-check`.

## When to Use

Weekly, and before any new entry.
Type: `/portfolio`
