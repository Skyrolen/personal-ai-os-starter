# Bora Scan

Crawl Bora Özkent's platform and Skool, and write a structured snapshot to
`50_Finance/private/bora/`.

**Read-only.** This command places no orders, proposes no sizes, and makes no
calls to Robinhood. It gathers; `/bora-portfolio` interprets and `/bora-check`
decides.

## Instructions

### 0. Setup

His platform: `https://portfolio-platform-production-7518.up.railway.app/`
Skool: `https://www.skool.com/`

Read both through the **Chrome DevTools MCP** against my logged-in debug
profile. If the browser isn't reachable, say so and stop — do not fall back to
fetching the public web and presenting it as his content.

Use the Turkish field map below. **Do not improvise field names.**

| Turkish | Meaning |
|---|---|
| HİSSE | ticker |
| TREND | Porttech health tag + 0–100 score |
| Sağlıklı / İzle / Kritik / Veri yok | healthy / watch / critical / no data |
| uyarı | warning |
| LOT | share count |
| ORT. MALİYET | average cost |
| BUGÜNKÜ FİYAT | current price |
| POZ. DEĞERİ | position value |
| K/Z % | total P&L % |
| GÜNLÜK K/Z $ / GÜNLÜK % | daily P&L $ / % |
| Alım / Satış | buy / sell |
| Nakit / Pozisyon / Toplam | cash / position / total |
| Gerçekleşen K/Z | realized P&L |
| ALT PORTFÖY DAĞILIMI | sub-portfolio allocation |
| RAPOR TARİHİ / Başlangıç | report date / starting capital |
| İşlem Geçmişi / Son İşlemler | transaction history / recent transactions |
| Bora'nın Felsefesi | his investing philosophy |
| Trend & Çıkış Planı | trend & exit plan |

Sleeves: **P1 Yatırım** · **P2 Moonshot** · **P3 Savunma** · **P4 Trade** ·
**P5 Opsiyon**

### 1. Dashboard → `snapshots/YYYY-MM-DD-dashboard.md`

From the home page capture:

- **Header** — RAPOR TARİHİ, last-update time, TOPLAM PORTFÖY, BUGÜN (with the
  hisse/opsiyon split), Başlangıç, period returns (HAFTA / MTD / YTD / ALL TIME)
  with their SPX/NDX/IXIC comparisons, and distance from ATH.
- **ALT PORTFÖY DAĞILIMI** — every sleeve's Pozisyon / Nakit / Toplam / %.
- **Son İşlemler** — trade count, Alım vs Satış split, both volumes,
  Gerçekleşen K/Z.

### 2. Expand every sub-portfolio — all five

The page collapses all but one sleeve by default. **A collapsed sleeve is not an
empty sleeve.** Click into each of P1, P2, P3, P4, P5 and record every row:

```
| Ticker | Porttech | Lot | Avg cost | Price | K/Z % |
```

P3 Savunma was $0 with no positions when last read. If that is still true, write
it as **empty**, not as missing — those are different facts and only one of them
means something.

If a sleeve won't expand, name it in a `## Failed to load` section at the bottom
of the snapshot. Never let a failure look like an absence.

### 3. `İşlem Geçmişi` → append to `transactions.md`

Paginate until you reach either the end or an entry already on disk.

```
| Date | Ticker | Alım/Satış | Lot | Price | Sleeve | Notes |
```

**Append-only.** Never rewrite or reorder rows already recorded — a partial scan
must not be able to corrupt history. Deduplicate on
`date + ticker + action + price`. Report how many new rows you added; if zero,
say so plainly rather than implying the file is fresh.

This is the highest-value file we collect: a dated buy is a real entry price,
which is what `/bora-check` needs in `transaction` mode.

### 4. His method → `method.md`

Read **`Bora'nın Felsefesi`** and **`Trend & Çıkış Planı`**. Capture:

- his stated entry criteria
- his stated **exit** rules and where he cuts
- position-sizing and concentration rules
- what the Porttech score means and how the tags are banded

**Quote him.** Do not paraphrase his rules into something crisper than he
actually said — the vagueness, where it exists, is itself information. Translate
to English but keep the Turkish term in brackets where the meaning is load-bearing.

Write this once and update it rarely. It is the file that ages best: the daily
reports are stale tomorrow, his method is not.

### 5. Skool → `skool/YYYY-MM-DD-posts.md`

Target: the last **30 days** plus anything pinned. His posting pace is high, so
one session may not cover the window.

**Resume, don't restart.** Before scraping, check the newest existing file in
`skool/` for its recorded coverage and resume point (page/date). Continue from
there instead of re-reading what's already on disk. At the end of every run,
record in the file: the date range actually covered, and if short of 30 days,
the exact resume point — so the next run (or a follow-up run right now) can
close the gap. Partial coverage stated as partial is fine; partial coverage
presented as complete is not.

For each post: date, title, a short summary, **every ticker mentioned**, and
whether it states or changes a thesis.

Then flag separately:

- posts that **contradict the dashboard** (talks up a name he is selling)
- posts that give a **reason** for a transaction in `transactions.md`
- macro or positioning calls that affect everything rather than one name

Skip pure community chatter. If a post is ambiguous, keep it and mark it
ambiguous rather than deciding for me.

### 6. Derive `watchlist.md`

Cross-reference his holdings against mine (from the trade log; do **not** call
Robinhood here — `/bora-portfolio` does that).

```
| Ticker | Sleeve | His avg cost | Price now | K/Z % | Porttech | Takeable? |
```

Sort by **K/Z % ascending**. The names nearest his cost are the ones still
available at something like his price. Anything far above is a name I already
missed — buying it there is my own decision, not a mirror of his.

### 7. Report back

## Scan — YYYY-MM-DD

- **Captured:** which sections, how many positions, how many new transactions
- **Failed:** anything that didn't load, named explicitly
- **His direction of travel:** buys vs sells, and what that says
- **Changes since the last snapshot:** new positions, exits, size changes,
  Porttech tags that moved. If there's no prior snapshot, say so.
- **Worth your attention:** two or three items, most important first

## Notes

- His content is subscriber-only. It stays in `50_Finance/private/` (gitignored),
  is used for my decisions, and is never republished.
- Don't hammer the site. One pass per section; it auto-refreshes on its own.
- Record the report date and last-update time in every snapshot. A scan of stale
  data that looks fresh is worse than no scan.
- If a number looks implausible, write down what you saw and flag it. Do not
  "correct" it.

## When to Use

Weekly, or when he posts activity. Run before `/bora-portfolio` and `/bora-check`.
Type: `/bora-scan`
