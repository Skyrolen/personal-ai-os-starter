# Bora Check

Take a trade idea from Bora Özkent, verify it independently, and — only if it
survives — produce a sized order ticket for approval.

**The job is not to execute his call. It is to find out whether it holds up.**
A run of AGREEs with no DISAGREEs means this command is broken.

## Instructions

### 0. Read the rules first

Read `50_Finance/trading-policy.md` before anything else. It overrides your
defaults and it overrides me if I contradict it mid-conversation. Note Rule 0:
**you may never call `place_equity_order` without my explicit approval of that
exact ticket.**

### 1. Get the call

Input is `$ARGUMENTS`, which may be a ticker, a pasted post, or nothing.

- **Pasted text or screenshot** — use it directly.
- **Nothing, or just a ticker** — pull from his site using the Chrome DevTools
  MCP against my dedicated debug profile (already logged into Skool and
  boraozkent.net). Navigate, read the relevant post or portfolio page, extract.
- **Can't reach it** — say so and ask me to paste. Do not guess at what he said.

His content is subscriber-only. Read it for my decisions; never republish it,
and keep the extracted text in `50_Finance/private/` (gitignored).

### 2. Extract the claim — without embellishing it

Write `50_Finance/private/bora/calls/YYYY-MM-DD-TICKER.json`:

```json
{
  "ticker": "NVDA",
  "direction": "buy",
  "call_price": 120.00,
  "call_date": "2026-07-01",
  "target": 160.00,
  "invalidation": 104.00,
  "thesis": "his reasoning, in his words",
  "source": "Skool post 2026-07-01"
}
```

Rules for this step:
- **Omit what he didn't say.** No target means no `target` field. Do not infer a
  stop from a chart and record it as his.
- Quote his thesis rather than paraphrasing it into something crisper than he
  said. If the reasoning is vague, the vagueness is data.
- If he gave a price *range*, use the midpoint and note the range in `thesis`.

### 3. Pull account state from Robinhood

Use the read tools — `get_accounts`, `get_portfolio`, `get_equity_positions` —
and write `50_Finance/private/account.json`:

```json
{
  "account_value": 0.0,
  "buying_power": 0.0,
  "settled_cash": 0.0,
  "positions": [{"ticker": "AAPL", "shares": 10, "market_value": 2000.0, "sector": "Technology"}]
}
```

Confirm you are reading the **agentic** account, not my main one. If sector is
missing for a holding, look it up — the concentration check is worthless without
it, and Bora's book is correlated enough that concentration is the risk most
likely to actually hurt me.

### 4. Run the verification

```bash
.venv/bin/python tools/verify.py \
  --call 50_Finance/private/bora/calls/YYYY-MM-DD-TICKER.json \
  --account 50_Finance/private/account.json
```

This checks drift from his entry, level coherence, risk/reward, trend, RSI,
MACD, ATR, support/resistance, earnings blackout, revenue growth, liquidity,
buying power, T+1 settlement, the 5% cap, the 10-position cap, and sector
concentration.

### 5. Add what the script cannot check

The script does arithmetic. You do judgement. Add:

- **News check** — has anything happened since his call that breaks the thesis?
  A downgrade, a guidance cut, a lawsuit, a change of CEO.
- **Thesis coherence** — does his stated reason actually imply this trade? A
  long-term AI-demand argument does not justify an entry timed to a chart
  pattern.
- **His own consistency** — does this contradict something he said recently?

### 6. Present the brief

Show the script's output, then your own read in this format:

## `TICKER` — VERDICT

**What he said:** [one line, his claim and his level]
**What the data says:** [one line]
**Verdict:** AGREE / AGREE (with caveats) / WAIT / DISAGREE

**Where I agree with him:** [specifics]
**Where I disagree with him:** [specifics — never omit this section; if there
is genuinely nothing, say "nothing material" rather than deleting the heading]
**What I could not verify:** [list, explicitly]
**What would change this verdict:** [a price, a date, or an event]

### Proposed ticket
[ticker, side, share count, cost, % of account, invalidation level]
*or* "No ticket — verdict is WAIT/DISAGREE."

You may **downgrade** the script's verdict on qualitative grounds. You may never
**upgrade** one: if the script found a hard block, the answer is not AGREE.

### 7. Review, then stop

If the verdict is AGREE, call `review_equity_order` and show me the pre-trade
warnings verbatim.

**Then stop.** Do not place it. Ask: *"Approve this exact ticket?"* and wait.

Nothing in my earlier messages counts as approval of a ticket that did not exist
when I wrote them.

### 8. Log it either way

Append to `50_Finance/private/trade-log.md` — including DISAGREEs and WAITs.
A log of only the trades I took cannot tell me whether my vetoes were any good.

```
## YYYY-MM-DD — TICKER — VERDICT
- **His call:** entry, target, date
- **Price at check:** X (drift +N% from his entry)
- **Verdict:** ... because ...
- **Disagreements:** ...
- **Could not verify:** ...
- **Action:** placed N shares @ X / declined / waiting for $X
- **Reasoning at the time:** [written now, never revised later]
```

## Notes

- Never soften a disagreement because he is the expert and I am paying for his
  calls. The subscription is the reason to check him, not a reason to defer.
- Never tell me a trade is "safe". Report what passed, what failed, and what you
  could not see.
- If I push to skip a check or override a block, quote the relevant rule from
  `trading-policy.md` and make me say plainly that I am overriding it. Then log
  that I did.

## When to Use

Whenever Bora posts a new call, or before acting on anything of his.
Type: `/bora-check NVDA` or `/bora-check [paste his post]`
