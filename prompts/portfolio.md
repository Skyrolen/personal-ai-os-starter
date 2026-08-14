# Portfolio - Universal Prompt

> Paste this into any AI tool to review your holdings against your own rules.
> Read-only: it reports and proposes, it never places orders.

## Prompt

```
Review my portfolio against my trading policy. Read 50_Finance/trading-policy.md
first. Do not propose new positions — this is a review of what I already hold.

My account: value $____, cash $____, settled cash $____
Positions:
[TICKER | shares | cost basis | current price | sector | invalidation level]

Cross-reference 50_Finance/private/trade-log.md for why each position was opened.

Produce:

1. SUMMARY — account value, P&L, positions used out of 10.

2. POSITION TABLE — ticker, shares, cost, current, P&L, % of account,
   invalidation level, and status (OK / WATCH / BREACH).

3. POLICY CHECK
   - Anything over the 5% single-position cap
   - Position count against the 10 limit
   - Sector concentration over 30% — and if so, say plainly that those names
     fall together, so the real number of independent bets is smaller than the
     position count suggests
   - Unsettled cash that would block a new entry

4. INVALIDATION BREACHES — anything trading below the level I recorded. State
   the level, the current price, and how long it has been breached. A breach is
   not a signal to sell; it means the reason I bought no longer holds. Say that,
   and let me decide.

5. EARNINGS IN THE NEXT 10 DAYS — ticker, date, position size.

6. POSITIONS WITH NO RECORDED THESIS — anything I hold that isn't in the log.
   These drift the most, because nothing was written down to check against.

7. WHAT I'D LOOK AT FIRST — one or two items, most important first, specific.

Report losses as plainly as gains. Do not lead with the winners. If the account
is down more than 15% from its high, or the last 3 closed trades were losses,
say so prominently.
```
