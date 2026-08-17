# Bora Daily

The morning loop: what changed overnight, what it means, and what is takeable
today.

**This command places no orders.** It ends by handing you a list; acting on one
is `/bora-green TICKER`.

## Instructions

### 1. Preflight — fail loudly, never quietly

Before anything else:

```bash
curl -s -m 5 http://127.0.0.1:9222/json/version | head -3
```

Then navigate to his platform and confirm you are **actually authenticated** —
look for the `Çıkış` (logout) control or live portfolio numbers. A page that
loads a login wall is not a working scan.

If Chrome is down **or** the session is logged out:

1. Run `scripts/notify.sh "Bora daily FAILED: re-login needed"`.
2. Write `50_Finance/private/bora/daily/YYYY-MM-DD-FAILED.md` recording what
   failed and when.
3. **Stop.**

Do not produce a quiet-day report on a failed scan. A daily brief that says
"no new signals" because nobody was logged in is worse than no brief at all,
because you will believe it.

### 2. Delta scan

Load the newest `50_Finance/private/bora/snapshots/` file as the baseline, then
read his dashboard and `İşlem Geçmişi` and compute what is **new**:

- new transaction rows since the last recorded one
- Porttech tag changes (Sağlıklı → İzle → Kritik and back)
- position size changes (adds and trims that show in the table)
- new Skool posts since the last `skool/` file

Append new transactions to `transactions.md` (append-only, deduped) and write a
fresh snapshot. Use the Turkish field map in `.claude/commands/bora-scan.md`.

### 3. Signals — with the context, not just the headline

For each, give me everything I need without opening the platform myself:

**BUY** — new `Alım`
> `TICKER` — he bought N at $X on DATE (sleeve). Now $Y (Z% from his fill).
> His stated reason: "..." · His tag: ... · Your sleeve headroom: $H

**SELL** — new `Satış`
> `TICKER` — he sold N at $X on DATE. Full exit or trim? Remaining lot: N.
> His stated reason: "..." · **If I hold this, say so first.**

**TAG CHANGE** — e.g. Sağlıklı → İzle
> `TICKER` — tag moved X → Y. He holds N. I hold N.

**THESIS POST** — a Skool post that states or changes a view
> Date, tickers, one-line summary, and whether it agrees with what the
> dashboard shows him doing. Flag contradictions explicitly.

If nothing changed, say **"No new activity since DATE"** in one line. Do not
pad a quiet day into a report.

### 4. Build the two lists — never blended

Assemble candidates into `50_Finance/private/bora/candidates.json`:

```json
[{"ticker": "NVDA", "sleeve": "P1", "source": "bora",
  "his_avg_cost": 217.62, "price": 225.06,
  "porttech": "Sağlıklı (1, score 71)",
  "his_last_action": {"action": "buy", "date": "2026-07-16"},
  "avg_volume": 120585813, "spread_pct": 0.01,
  "next_earnings": "2026-08-26", "sector": "Electronic Technology",
  "trend": "uptrend", "fractional": true}]
```

- **`source: "bora"`** — every name he holds, plus anything he bought recently.
- **`source: "screener"`** — `run_scan` matches on his profile's shape (sector,
  market-cap band, volume floor drawn from his actual holdings, not guessed).
  These have **no thesis from him**.

Fill `price`, `avg_volume`, `spread_pct`, `next_earnings`, `sector`,
`fractional` from Robinhood (`get_equity_quotes`, `get_equity_fundamentals`,
`get_earnings_results`, `get_equity_tradability`). Leave a field **out** rather
than guessing — the ranker reports unknowns instead of assuming.

Then:

```bash
.venv/bin/python tools/rank.py \
  --candidates 50_Finance/private/bora/candidates.json \
  --account 50_Finance/private/account.json
```

Present the two sections **separately**, in this order and never merged into one
table:

1. **FROM BORA** — his names, ranked.
2. **SCREENER — NO THESIS FROM HIM** — with the label kept intact. Taking one
   of these is independent stock-picking, not following him. Say so every time;
   it stops being obvious after the third morning.

The ranking is **takeability**, not a forecast. Do not describe a high score as
a buy signal or a recommendation.

### 5. Write, push, hand over

Save the whole brief to `50_Finance/private/bora/daily/YYYY-MM-DD.md`.

Push **only if there were signals** (a new transaction, a tag change, or a
thesis post). Quiet days get no notification — a channel that pings you every
morning is one you stop reading.

```bash
scripts/notify.sh "Bora: 2 buys (DRAM, DNTH), 1 sell (AVGO). 3 takeable."
```

Terse by design: actions and tickers only. **Never balances, position sizes, or
account data** — the ntfy topic is public to anyone who guesses it.

End with:

> Green-flag any of these with `/bora-green TICKER`.

## Notes

- Report his sells as prominently as his buys. He has been a net seller; a
  brief that leads with buying opportunities while he reduces is telling you
  the opposite of what the data says.
- If a section of the platform fails to load, name it. Partial coverage stated
  as partial is fine; partial coverage presented as complete is not.
- Never place an order here, and never propose a size. That is `/bora-green`.

## When to Use

Every trading morning, via launchd, or on demand.
Type: `/bora-daily`
