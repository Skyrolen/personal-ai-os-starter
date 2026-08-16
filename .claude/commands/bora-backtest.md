# Bora Backtest

Score his transaction ledger: did his trades beat holding the index, were his
trims well timed, and do his Porttech tags predict anything?

**Read-only.** No orders, no sizing. This answers whether following him pays,
with data instead of feel.

## Instructions

### 1. Parse the ledger

Convert `50_Finance/private/bora/transactions.md` to
`50_Finance/private/backtest/ledger.json`:

```json
[{"date": "2026-07-16", "ticker": "MU", "action": "buy", "lot": 50,
  "price": 848.56, "sleeve": "P1", "note": "his stated reason if any",
  "porttech": "tag AT transaction time, if the ledger recorded one"}]
```

`Alım`/`Satış` are accepted as-is. **Leave `porttech` empty unless the tag was
recorded at transaction time** — a tag from today's snapshot pasted onto last
year's trade is hindsight dressed as data, and the script's tag analysis is
only as honest as this field.

### 2. Assemble price history

For **every ticker in the ledger** plus the benchmarks **SPY and QQQ**:

`get_equity_historicals` (`interval: "day"`, `start_time` = ledger start minus
a month) → `50_Finance/private/backtest/TICKER.json`, in the `market.json` bar
shape (`{"ticker": ..., "bars": [...]}`).

If a ticker's history is unavailable (delisted, renamed), write no file and
note it — the script reports missing data rather than guessing, and so do you.

### 3. Run it

```bash
.venv/bin/python tools/backtest.py \
  --ledger 50_Finance/private/backtest/ledger.json \
  --bars-dir 50_Finance/private/backtest \
  --index SPY
```

Run once more with `--index QQQ`. His book is Nasdaq-heavy tech, so QQQ is the
harder and fairer comparison; SPY is the broad-market one. Report both.

### 4. Present — with the honesty section intact

Show the full output including HONESTY. Do not trim it to the flattering parts.
Lead with the two numbers that answer the actual question:

1. **Mean excess return per trip vs QQQ** — his edge over just buying the index
   he effectively trades.
2. **Trim quality at 30/60/90d** — positive means prices kept rising after his
   trims (trimming cost him); negative means his exits were well timed.

Then the sanity anchor: compare the script's realised P&L total against the
dashboard's `Gerçekleşen K/Z`. **They will differ** — FIFO convention, ledger
window, options excluded. State the difference and the reasons; do not force
agreement, and do not present agreement as validation if it happens.

Interpretation rules:
- Any bucket with n < 10: report it, but say it proves nothing.
- If unmatched sells exist, their P&L is unknowable from this data — excluded,
  not estimated.
- Open positions are marked separately. A book of open winners can coexist with
  poor realised results and vice versa; keep the two apart.

### 5. Save and log

Save the report to `50_Finance/private/bora/backtest-YYYY-MM-DD.md` and add a
one-line summary with the two headline numbers to the scorecard.

## Notes

- The ledger starts when his platform's history starts, on what has been a
  strong tape. Absolute returns flatter everyone in a bull market; the
  vs-index columns are the fair reading.
- This scores his *realised trading*, not his newsletter picks or his options
  sleeve. Say so when presenting.
- Re-run after each `/bora-scan` that adds meaningful new transactions.

## When to Use

After a scan with new transactions; monthly otherwise.
Type: `/bora-backtest`
