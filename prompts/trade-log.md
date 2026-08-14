# Trade Log - Universal Prompt

> Paste this into any AI tool to journal decisions and score them over time.

## Prompt — logging a decision

```
Append this decision to my trade log, in this format:

## YYYY-MM-DD — TICKER — VERDICT
- Source: Bora / my own idea
- His call: entry, target, date (if applicable)
- Price at check: X (drift +N% from his entry)
- Verdict: ... because ...
- Disagreements with him: ...
- Could not verify: ...
- Action: placed N shares @ X / declined / waiting for $X
- Invalidation: X
- Reasoning at the time: [what I actually believe right now]

The reasoning field is written now and never edited later. If it turns out
wrong, that entry becomes the most useful one in the file.

Log declines and waits too, not just fills.
```

## Prompt — weekly review

```
Score my trading log for the week. Read the whole log first.

1. CLOSED POSITIONS — entry, exit, P&L, days held, and whether the ORIGINAL
   reasoning turned out to be right. Flag any trade that made money for a reason
   other than its thesis: that is luck, and counting it as skill is how a
   strategy quietly stops working.

2. OPEN POSITIONS vs their invalidation levels.

3. SCORE THE TRADER I FOLLOW:
   - Calls tracked, taken vs declined
   - Hit rate on the calls I took; average win vs average loss
   - Hit rate on the calls I DECLINED, and their would-be P&L
   - Average price drift at the moment I saw each call

   Interpret those last three directly:
   - If declined calls did well, my filter is costing me money.
   - If they did badly, the filter is earning its keep.
   - If average drift is high, I am seeing his calls too late to trade them,
     and no amount of analysis fixes that.

4. SCORE MY OVERRIDES — every time I overrode a rule, how did it turn out?
   Aggregate them; do not cherry-pick.

5. VERDICT — direct. If following him is not working, say so. If my vetoes are
   performing worse than his raw calls, say that too. Being agreeable here is
   worth less to me than being right.

Below about 20 calls, report the numbers WITH the caveat that the sample proves
nothing, rather than drawing conclusions from them.

Never revise a past reasoning entry — add a follow-up line instead.
```
