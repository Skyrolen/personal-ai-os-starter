# Bora Green

Green-flag one name: run the full verification, produce a real ticket, and place
it **once I approve that ticket**.

`$ARGUMENTS` — a ticker, optionally followed by an amount: `/bora-green NVDA`
or `/bora-green NVDA 600`.

## What a green flag is, and is not

A green flag means **"work this one up and show me a ticket."** It is not
standing authorisation.

- **One name, one session, one ticket.** It does not carry to the next name, the
  next day, or a re-run after the price moved.
- **It never overrides a block.** It removes the size-confirmation step, nothing
  else. If verification hard-blocks, the run stops and you tell me why.
- **It does not skip the ticket gate.** Rule 0 stands: `place_equity_order`
  requires my explicit approval of the exact ticket in front of me.

## Instructions

### 1. Run the full check

Follow `.claude/commands/bora-check.md` end to end — do not reimplement or
abbreviate it. That means: read the policy, pull his last action from
`transactions.md`, assemble account and market JSON from Robinhood, and run
`tools/verify.py`.

Use the amount I gave as `--budget` if I gave one. If I didn't, use the
recommended size.

### 2. If the verdict is DISAGREE or has any hard block — stop

Report the blocks and stop. Do not size, do not review, do not place.

Two exceptions worth naming, because they are fixable rather than fatal:

- **`funding`** — tell me the exact amount to transfer, then stop. I move the
  money; you never do.
- **`over_cap`** — I named an amount above the ceiling. Quote the rule, tell me
  how far over, and ask me to say plainly that I'm overriding. Log the override
  if I do.

### 3. If the verdict is WAIT — stop and say what would clear it

Usually a missing invalidation level or drift past his entry. Tell me the
specific thing that would change it. Do not proceed on a WAIT.

### 4. If it clears — review and present the real ticket

Call `review_equity_order` at the approved size and show me its **actual
pre-trade warnings, verbatim**. Then:

## Ticket — `TICKER`

| | |
|---|---|
| Side / quantity | BUY N shares |
| Order type | limit @ $X *(or market + why)* |
| Cost | $X — N% of the {sleeve} sleeve, N% of the risk base |
| Sleeve after | $used of $budget |
| Invalidation | $X |
| Robinhood warnings | *verbatim* |

**Approve this exact ticket?**

Then **stop and wait.** Not "looks good", not silence — an explicit yes to this
ticket.

### 5. On approval

Call `place_equity_order`. Report the fill or the rejection plainly, then log to
`50_Finance/private/trade-log.md` with the reasoning **as it stood before the
fill**, not rewritten in light of it.

If the order is rejected, say why and stop. Do not retry with a different order
type or size without asking — a rejection is information, not an obstacle.

### 6. If I decline

Log it as a decline with a reason tag (`drift`, `he-is-selling`,
`unmanageable-size`, `conviction`, …). Declines are half the data the scorecard
needs.

## Notes

- If the price has moved materially since the brief that produced this green
  flag, re-run the check rather than trusting the older numbers. A green flag
  given at one price is not a green flag at another.
- Never widen a limit price or switch to market to force a fill.
- Never present the ticket as a good idea. Present it as compliant with my
  rules, which is a different and smaller claim.

## When to Use

After `/bora-daily` or `/bora-check`, when I want to act on a name.
Type: `/bora-green NVDA` or `/bora-green NVDA 600`
