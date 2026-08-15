# Bora Portfolio

Snapshot Bora Özkent's dashboard, diff it against my sleeves, and surface what
changed since last time.

**Read-only. This command never places an order and never proposes a size** —
that's `/bora-check`, one name at a time.

## Instructions

### 0. Read the rules

`50_Finance/trading-policy.md` and `50_Finance/private/risk-profile.json` for
my sleeve weights and risk base.

### 1. Read his dashboard

If `/bora-scan` has run recently, use the latest file in
`50_Finance/private/bora/snapshots/` instead of re-crawling, and say which
snapshot you used and how old it is. Re-crawl only if it's stale or missing.

Via Chrome DevTools MCP against my logged-in debug profile:
`https://portfolio-platform-production-7518.up.railway.app/`

Capture from the home page:

- **Header:** RAPOR TARİHİ, TOPLAM PORTFÖY, BUGÜN, period returns (HAFTA / MTD /
  YTD / ALL TIME), and distance from ATH.
- **ALT PORTFÖY DAĞILIMI:** each sleeve's Pozisyon / Nakit / Toplam / %.
- **Son İşlemler:** counts and volumes of Alım (buy) vs Satış (sell), and
  Gerçekleşen K/Z.
- **Each sub-portfolio table** — expand them; the page collapses all but one by
  default and a collapsed sleeve is not an empty sleeve. Per row: HİSSE, TREND
  (Porttech score + tag), LOT, ORT. MALİYET, BUGÜNKÜ FİYAT, K/Z %.

Use the Turkish field map in `.claude/commands/bora-check.md`. Save the raw
snapshot to `50_Finance/private/bora/snapshots/YYYY-MM-DD.md` (gitignored).

### 2. Read my side

Agentic account number from `risk-profile.json`, passed explicitly.
`get_portfolio` and `get_equity_positions`, plus `get_equity_quotes` for current
prices. Map each holding to its sleeve using the trade log.

### 3. Report

## Bora — YYYY-MM-DD

### His book
Total, today's move, period returns, distance from ATH. One line.

### His recent activity
Buys vs sells, and the volumes. **Lead with the direction of travel.** If he is
net selling, say so first and plainly — it matters more than any single name on
the page.

### Sleeve allocation — his weights vs mine
| Sleeve | His % | My target % | My budget | Used | Headroom |

Flag any sleeve where his weight has drifted materially from the weights in
`risk-profile.json`. Those weights were captured once; they are not live, and a
rebalance on his side silently makes my targets wrong.

### What he holds that I don't
| Ticker | Sleeve | His lot | His avg cost | Now | K/Z % | Porttech |

Sort by **K/Z % ascending** — the ones nearest his cost are the ones still
takeable. Anything far above his average is a name I've already missed at his
price, and I'd be buying it on my own reasoning, not his.

### What I hold that he doesn't
Anything I still own after he exited. State when he sold if the transaction
history shows it. These are the positions most likely to be quietly orphaned.

### Warnings on names I hold
Any of my positions where his own Porttech tag is **Kritik** or **İzle**. His
system flagging his own position is evidence I should not ignore.

### Concentration
His book and mine, by sector and by theme. His P1 is heavily AI/semiconductor
infrastructure — if mine is converging on the same names, say so plainly: a
correlated set is one bet held under several tickers, not several bets.

### What changed since the last snapshot
Diff against the previous file in `snapshots/`. New positions, exits, size
changes, Porttech tags that moved. If there's no prior snapshot, say so rather
than implying nothing changed.

### What I'd look at first
One or two items, most important first, specific. Then: *"Run `/bora-check
TICKER` on any of these."*

## Notes

- Report his losers as plainly as his winners. He holds names well underwater;
  a summary that only shows the winners is a misleading summary.
- Never suggest a position size here. Never place an order.
- His scale is not mine: a position that is 10% of a $2.4M book is not
  transferable advice at a $1,000 cap. Don't imply it is.
- If a sleeve wouldn't expand or a table didn't load, say which — do not present
  a partial read as complete.

## When to Use

Weekly, and before any `/bora-check` session.
Type: `/bora-portfolio`
