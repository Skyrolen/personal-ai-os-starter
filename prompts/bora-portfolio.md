# Bora Portfolio - Universal Prompt

> Paste this into any AI tool to diff a trader's published book against your own.
> Read-only: it reports, it never sizes a position or places an order.

## Prompt

```
Compare the trader I follow against my own portfolio. Read my trading policy
first if you have access to it.

HIS BOOK:
[PASTE HIS DASHBOARD: totals, sub-portfolio allocation, position tables,
recent transactions]

MY POSITIONS:
[TICKER | sleeve | shares | cost basis | current price | sector]
My risk base: $____   My sleeve budgets: ____

Turkish field names you may see: HISSE = ticker, LOT = share count,
ORT. MALIYET = average cost, BUGUNKU FIYAT = current price, K/Z % = total P&L %,
Alim = buy, Satis = sell, Nakit = cash, Saglikli / Izle / Kritik =
healthy / watch / critical, Son Islemler = recent transactions.

Produce:

1. HIS BOOK - total, period returns, distance from all-time high. One line.

2. HIS RECENT ACTIVITY - buys versus sells, and the volumes. LEAD WITH THE
   DIRECTION OF TRAVEL. If he is net selling, say that first and plainly; it
   matters more than any single name on the page.

3. SLEEVE ALLOCATION - his weights against my targets and budgets. Flag any
   sleeve where his weight has drifted from what my config assumes. Those
   weights were captured once and are not live; a rebalance on his side silently
   makes my targets wrong.

4. WHAT HE HOLDS THAT I DON'T - ticker, his lot, his average cost, current
   price, P&L%, his own health tag. Sort by P&L% ASCENDING: the names nearest
   his cost are the ones still takeable. Anything far above his average is a
   name I already missed at his price, and buying it means acting on my own
   reasoning rather than his.

5. WHAT I HOLD THAT HE DOESN'T - including anything he has since sold. These are
   the positions most likely to be quietly orphaned.

6. WARNINGS ON NAMES I HOLD - any of my positions his own system flags as
   watch or critical. Him flagging his own position is evidence.

7. CONCENTRATION - his and mine, by sector and theme. If we are converging on
   one correlated group, say so plainly: correlated names are one bet held under
   several tickers, not several bets.

8. WHAT CHANGED since the last snapshot, if I gave you one. If I didn't, say so
   rather than implying nothing changed.

9. WHAT I'D LOOK AT FIRST - one or two items, most important first, specific.

Report his losers as plainly as his winners. Do not suggest a position size and
do not propose an order. And remember his scale is not mine: a position that is
10% of a multi-million-dollar book is not transferable advice at my size.
```
