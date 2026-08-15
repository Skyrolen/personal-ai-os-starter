# Bora Check

Take a position or trade from Bora Özkent's platform, verify it independently,
and — only if it survives — propose a sized ticket for my approval.

**The job is not to copy him. It is to find out whether the trade is still
available to me at a price that makes sense.** A run of AGREEs with no
DISAGREEs means this command is broken.

## Instructions

### 0. Read the rules first

Read `50_Finance/trading-policy.md` and `50_Finance/private/risk-profile.json`.
The policy overrides your defaults and it overrides me if I contradict it
mid-conversation. Note Rule 0: **you may never call `place_equity_order` without
my explicit approval of that exact ticket.**

### 1. Get the input — two modes, and they are not equivalent

Input is `$ARGUMENTS`: a ticker, a pasted row, or nothing.

His platform is `https://portfolio-platform-production-7518.up.railway.app/`.
Read it through the Chrome DevTools MCP against my logged-in debug profile.

**Mode A — `transaction`** (preferred). From **`İşlem Geçmişi`** (transaction
history) or **`Son İşlemler`** (recent transactions). A new **Alım** (buy) is a
real dated entry at a known price. This is the closest thing to a call he
publishes, and it gets the strict 5% drift limit.

**Mode B — `holdings`.** From a sub-portfolio position table. What you get is
**`ORT. MALİYET`** — a blended average cost across however many buys, not an
entry signal. It gets the sleeve's looser drift limit, because judging a
long-term average by a 5% rule would block every winner he owns.

Say which mode you used. If you can't reach the page, say so and ask me to
paste — never guess at what he holds.

His content is subscriber-only. Read it for my decisions, keep extracts in
`50_Finance/private/` (gitignored), never republish it.

### 2. Turkish field map — use this, don't improvise

| Turkish | Meaning |
|---|---|
| HİSSE | ticker |
| TREND | his Porttech health tag + 0–100 score |
| Sağlıklı / İzle / Kritik / Veri yok | healthy / watch / critical / no data |
| uyarı | warning |
| LOT | share count |
| ORT. MALİYET | average cost |
| BUGÜNKÜ FİYAT | current price |
| POZ. DEĞERİ | position value |
| K/Z % | total P&L % — **this is the drift already computed** |
| GÜNLÜK K/Z $ / GÜNLÜK % | daily P&L $ / % |
| Alım / Satış | buy / sell |
| Nakit / Pozisyon / Toplam | cash / position / total |
| Gerçekleşen K/Z | realized P&L |
| ALT PORTFÖY DAĞILIMI | sub-portfolio allocation |
| RAPOR TARİHİ / Başlangıç | report date / starting capital |

Sleeves: **P1 Yatırım** (core long-term) · **P2 Moonshot** (speculative) ·
**P3 Savunma** (defensive) · **P4 Trade** (short-term) · **P5 Opsiyon** (options,
which I cannot trade — my agentic account has no options level).

### 3. Extract the claim — without embellishing it

Write `50_Finance/private/bora/calls/YYYY-MM-DD-TICKER.json`:

```json
{
  "ticker": "NVDA",
  "direction": "buy",
  "sleeve": "P1",
  "source_type": "transaction",
  "call_price": 217.62,
  "call_date": "2026-05-01",
  "target": null,
  "invalidation": null,
  "thesis": "his reasoning, in his words, if any is given",
  "porttech": "71 Sağlıklı (1 uyarı)",
  "source": "İşlem Geçmişi row, read 2026-08-14"
}
```

- `call_price` is the **transaction price** in Mode A, the **ORT. MALİYET** in Mode B.
- **Omit what he didn't say.** The dashboard gives no targets and no stops. Do
  not invent them and do not attribute them to him. If there's no invalidation
  level, that's my job to set — say so.
- Record his Porttech tag verbatim. If he flags his own position **Kritik** or
  **İzle**, that is evidence and it belongs in the brief.

### 4. Pull account state from Robinhood

Use the agentic account number from `risk-profile.json`. **Always pass it
explicitly** — never default to whatever `get_accounts` returns first. My main
margin account and Roth IRA are `agentic_allowed: false`.

`get_portfolio`, `get_equity_positions`, plus `get_accounts` for
`unsettled_funds`. Write `50_Finance/private/account.json`. Tag each holding
with the sleeve it belongs to from the trade log — the sleeve checks are
meaningless without it.

### 5. Assemble market data from Robinhood

| Tool | For |
|---|---|
| `get_equity_historicals` | daily OHLCV, ~300 sessions (`interval: "day"`) |
| `get_equity_quotes` | live last / bid / ask |
| `get_equity_fundamentals` | market cap, PE, average volume, 52-week range |
| `get_financials` | revenue trend → `revenue_growth` |
| `get_earnings_results` | next earnings date |
| `get_equity_tradability` | tradable + fractional flags for this account |

Write `50_Finance/private/market.json` (shape documented in
`tools/market_data.py`). A field you couldn't fetch must be **absent**, so the
brief reports it unverified rather than treating it as fine.

### 6. Run the verification — no budget yet

```bash
.venv/bin/python tools/verify.py \
  --call 50_Finance/private/bora/calls/YYYY-MM-DD-TICKER.json \
  --market 50_Finance/private/market.json \
  --account 50_Finance/private/account.json
```

Omit `--budget` on this first pass so the brief shows the **recommendation and
the ceiling** rather than assuming a size.

### 7. Add what the script cannot check

- **News** — has anything broken the thesis since his entry?
- **His own signal** — is his Porttech tag Kritik/İzle? Is he *selling* this
  name? Check `Son İşlemler`: a recent **Satış** in a name you're about to buy
  is the single most important thing on the page.
- **Scale** — his position may be 10%+ of a $2.4M book. Yours is capped near
  $1,000. Never present his conviction as transferable to your size.

### 8. Present the brief

## `TICKER` — VERDICT `[sleeve / mode]`

**What he did:** [bought N at $X on date] *or* [holds N lots, avg cost $X, K/Z +N%]
**His own tag:** [Porttech score and label]
**What the data says:** [one line]
**Verdict:** AGREE / AGREE (with caveats) / WAIT / DISAGREE

**Where I agree with him:** [specifics]
**Where I disagree with him:** [specifics — never omit this heading; if there's
genuinely nothing, write "nothing material"]
**What I could not verify:** [list]
**What would change this verdict:** [a price, a date, or an event]

You may **downgrade** the script's verdict on judgement. You may never
**upgrade** one: a hard block is not an AGREE.

### 9. Propose an amount and STOP

State it concretely:

> Recommended **$X** (N shares @ $P) — that's N% of the {sleeve} sleeve and N%
> of the risk base. Ceiling for this sleeve is **$C**; {sleeve} has **$H** of
> **$B** headroom left.
>
> **Approve at $X, or name a different amount?**

Then **stop and wait.** Do not proceed to a review or an order.

When I answer, re-run with `--budget`:

```bash
.venv/bin/python tools/verify.py --call ... --market ... --account ... --budget 800
```

- **At or below the ceiling:** accept it without argument.
- **Above the ceiling:** it blocks as `over_cap`. This is *not* a refusal —
  quote the rule, tell me the amount over, and ask me to state plainly that I'm
  overriding. Then log it as an override.

### 10. Review, then stop again

Call `review_equity_order` and show me the pre-trade warnings verbatim.

**Then stop.** Ask: *"Approve this exact ticket?"* and wait.

Approving a **size** is not approving the **order** — two separate gates.
Nothing in my earlier messages counts as approval of a ticket that did not exist
when I wrote them.

### 11. Log it either way

Append to `50_Finance/private/trade-log.md` — including DISAGREEs, WAITs, and
overrides. A log of only the trades I took cannot tell me whether my vetoes were
any good.

```
## YYYY-MM-DD — TICKER — VERDICT [sleeve / mode]
- **His action:** bought N @ $X on date / holds N lots @ avg $X (K/Z +N%)
- **His Porttech tag:** ...
- **Price at check:** X (drift +N% vs his entry/average)
- **Verdict:** ... because ...
- **Disagreements:** ...
- **Could not verify:** ...
- **Recommended / approved:** $X / $Y   (override: yes/no)
- **Action:** placed N shares @ X / declined / waiting for $X
- **Invalidation:** X
- **Reasoning at the time:** [written now, never revised later]
```

## Notes

- Never soften a disagreement because he is the expert and I pay for his calls.
  The subscription is the reason to check him, not a reason to defer.
- Never tell me a trade is "safe". Report what passed, what failed, what you
  could not see.
- If I push to skip a check, quote the rule and make me say plainly that I'm
  overriding it. Then log that I did.

## When to Use

When he posts a new transaction, or before acting on anything of his.
Type: `/bora-check NVDA` or `/bora-check [paste the row]`
