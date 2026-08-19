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

### 4. Independent screens — my own ideas, not his

Bora is one input. This step looks for setups he has nothing to do with.

**Build the universe.** Start from: his book, my holdings, the tickers from
recent Skool posts, and `run_scan` results. Cap it at ~60 names — the cost is
real and the marginal name adds little.

For each, write `50_Finance/private/screen/TICKER.json` (bars from
`get_equity_historicals`, `interval: "day"`, ~300 sessions) plus a single
`50_Finance/private/screen/meta.json`:

```json
{"NVDA": {"sector": "Electronic Technology", "market_cap": 5537586514810,
          "avg_volume": 120585813, "next_earnings": "2026-08-26"}}
```

Include **QQQ** bars — the relative-strength screen needs the benchmark.

```bash
.venv/bin/python tools/screen.py \
  --bars-dir 50_Finance/private/screen \
  --meta 50_Finance/private/screen/meta.json \
  --benchmark QQQ --json > 50_Finance/private/screen/hits.json
```

Four screens run: **pullback in an uptrend**, **earnings catalyst ahead**,
**relative strength vs QQQ**, **Bora-adjacent**. Each hit carries its strategy,
a rationale, the evidence behind it, and a specific entry reference.

Then **check the news** on any hit you're going to surface. A screen sees price
and dates; it cannot see a fraud investigation or a guidance cut. Use WebSearch
for recent headlines and say what you found — including "nothing notable".

Screen hits are **always P6**, never a mirror sleeve. `screen.py` sets this;
do not override it.

### 5. Rank both, present separately

Merge his candidates and the screen hits into
`50_Finance/private/bora/candidates.json`. Bora names carry `source: "bora"`
and their mirror sleeve; screen hits carry `source: "screener"` and P6.

Fill `price`, `avg_volume`, `spread_pct`, `next_earnings`, `sector`,
`fractional` from Robinhood for both. Leave a field **out** rather than
guessing — the ranker reports unknowns instead of assuming.

```bash
.venv/bin/python tools/rank.py \
  --candidates 50_Finance/private/bora/candidates.json \
  --account 50_Finance/private/account.json
```

Present as **two separate sections, never one merged table**:

1. **FROM BORA** — his names, with his cost, his tag, his last action.
2. **MY OWN SCREENS (P6)** — strategy, rationale, entry reference, evidence,
   news. State plainly: *no thesis from Bora, no track record — these are the
   system's own ideas and they carry more uncertainty, not less.*

Ranking is **takeability**, not a forecast. Never call a high score a buy signal.

### 6. Tomorrow's actionable — pre-verified, top 3

Take the **top 3 takeable names across both lists** and run the full
`tools/verify.py` on each now, so the brief is decision-ready rather than a
to-do list. For each:

## N. `TICKER` — VERDICT `[sleeve / source]`

| | |
|---|---|
| Why it's here | his buy / his holding / which screen |
| Entry reference | $X (his cost, his fill, or the screen's level) |
| Now | $Y — Z% from that |
| Recommended size | $A — N shares, N% of sleeve |
| Invalidation | $B *(or "you must set one")* |
| Blocks / warnings | ... |
| Verdict | AGREE / WAIT / DISAGREE + one line |

If fewer than 3 are takeable, say so and show what blocked the rest. **Do not
pad the list to reach three** — a thin day is information.

### 7. What I would not touch today

Name two or three and why: he is selling it, his tag went Kritik, drift too far,
earnings inside the blackout, unmanageable size. This section matters as much as
the buy list and is easier to skip, which is why it is a required heading.

## Notes

- **The market context first.** Open with one line on SPY/QQQ and where he sits
  (`get_index_quotes`). A candidate list without the tape is half a picture.
- Report his sells as prominently as his buys. He has been a net seller; a
  brief that leads with buying opportunities while he reduces is telling you
  the opposite of what the data says.
- If a section of the platform fails to load, name it. Partial coverage stated
  as partial is fine; partial coverage presented as complete is not.
- Never place an order here. Sizes shown in section 6 are **proposals inside a
  brief**, not tickets — placing anything still goes through `/bora-green` and
  your approval of the exact ticket.
- Screen hits are the system's own guesses. Say that every time. They have no
  track record, and the honest framing is more uncertainty than a Bora name,
  not less.

## When to Use

Every trading morning, via launchd, or on demand.
Type: `/bora-daily`
