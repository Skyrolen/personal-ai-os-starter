# Trade Log

Journal every decision, then score them — including the ones I didn't take.

Two modes: `log` (append a decision) and `review` (weekly scoring).

## Instructions

### Mode: log — `/trade-log log`

Append to `50_Finance/private/trade-log.md`:

```
## YYYY-MM-DD — TICKER — VERDICT
- **Source:** Bora / my own idea
- **His call:** entry, target, date (if applicable)
- **Price at check:** X (drift +N% from his entry)
- **Verdict:** ... because ...
- **Disagreements with him:** ...
- **Could not verify:** ...
- **Action:** placed N shares @ X / declined / waiting for $X
- **Invalidation:** X
- **Reasoning at the time:** [what I actually believed, right now]
```

The reasoning field is written **at decision time and never edited afterwards.**
If it turns out wrong, that entry is the most useful one in the file. Rewriting
it to look smarter destroys the only honest record I have.

Record declines and waits too. Otherwise the scorecard can only measure the
trades I took, which is the half of the data that flatters me.

For declines, add a **decline reason** from this list (or a new one, named
plainly): `drift` / `his-own-warning` / `he-is-selling` / `unmanageable-size` /
`earnings` / `concentration` / `funding` / `conviction`. The weekly review
scores declines *by reason* — "my drift vetoes are working, my conviction
vetoes are costing me" is exactly the kind of thing only a tagged log can show.

### Mode: review — `/trade-log review`

Run weekly.

**1. Score closed positions.** For each one closed since the last review: entry,
exit, P&L, days held, and whether the *original* reasoning turned out right.
Note when a trade made money for a reason other than the thesis — that's luck,
and counting it as skill is how a strategy quietly stops working.

**2. Score open positions** against their invalidation levels.

**3. Score Bora.** Update `50_Finance/private/bora/scorecard.md`:

| Metric | Value |
|---|---|
| Calls tracked | |
| Taken / declined | |
| Hit rate on taken calls | |
| Avg win vs avg loss | |
| **Hit rate on calls I declined** | |
| **Would-be P&L of declined calls** | |
| Avg drift when I saw the call | |

The bolded rows are the point. They answer the question the subscription
actually raises:

- If his declined calls did **well**, my filter is costing me money.
- If they did **badly**, the filter is earning its keep.
- If the average drift is high, I am seeing his calls too late to trade them,
  and no amount of analysis fixes that.

**4. Score my overrides.** Every time I overrode a block or a WAIT, how did it
turn out? Aggregate, don't cherry-pick.

**5. Report honestly.**

## Weekly review — YYYY-MM-DD

### Closed this week
### Open positions vs thesis
### Bora scorecard (running)
### My overrides
### What the data says
[Direct. If following him is not working, say so. If my vetoes are worse than
his calls, say that too — it is more useful than being agreeable.]

## Notes

- Small samples prove nothing. Below ~20 calls, report the numbers **with** that
  caveat rather than drawing conclusions from them.
- Never revise a past reasoning entry. Add a follow-up line instead.
- Do not let a good week become a case for raising the position cap. That change
  goes through `trading-policy.md`, on a different day.

## When to Use

`log` after every decision. `review` weekly, alongside `/weekly-review`.
Type: `/trade-log log` or `/trade-log review`
