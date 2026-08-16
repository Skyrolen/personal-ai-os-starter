"""Score Bora's transaction ledger.

Answers the question the subscription actually raises: did following his trades
beat just holding the index — and do his tags and trims carry information?

Like everything in tools/, this is offline arithmetic. The agent assembles the
inputs (ledger + per-ticker bars via the Robinhood MCP); this script only
computes. It never places orders and has no broker or network access.

Inputs
------
--ledger    transactions JSON: [{"date","ticker","action","lot","price",
            "sleeve","note","porttech"}]  (action: "buy"|"sell";
            porttech optional — only trustworthy if recorded AT the time)
--bars-dir  directory of per-ticker OHLCV JSON: {"ticker","bars":[...]} in the
            same shape market_data.parse_market accepts
--index     ticker of the benchmark whose bars live in bars-dir (e.g. SPY)

Method notes, stated rather than buried:
- Round-trips are FIFO-matched per ticker. Partial sells close the oldest lots
  first. This may not match his broker's accounting; it is stated, consistent,
  and the standard convention.
- Only CLOSED trips enter realised stats. Open positions are reported
  separately — a book of open winners can hide realised losses and vice versa.
- Every percentage is printed next to its n. A 75% win rate on n=4 is noise.
- The ledger starts when his platform's history starts. If that window was a
  rising tape, absolute results flatter him; the vs-index columns are the
  honest comparison because the index rode the same tape.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from market_data import MarketDataError, parse_market


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------

@dataclass
class Txn:
    date: dt.date
    ticker: str
    action: str            # "buy" | "sell"
    lot: float
    price: float
    sleeve: str = ""
    note: str = ""
    porttech: str = ""     # tag at transaction time, if the ledger recorded it


@dataclass
class RoundTrip:
    ticker: str
    sleeve: str
    entry_date: dt.date
    exit_date: dt.date
    lot: float
    entry_price: float
    exit_price: float
    entry_porttech: str = ""

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.lot

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price * 100.0

    @property
    def hold_days(self) -> int:
        return (self.exit_date - self.entry_date).days


@dataclass
class OpenLot:
    ticker: str
    sleeve: str
    date: dt.date
    lot: float
    price: float
    porttech: str = ""


@dataclass
class Trim:
    """A partial sell — he kept some of the position afterward."""
    ticker: str
    date: dt.date
    lot: float
    price: float
    remaining_lot: float
    note: str = ""


def load_ledger(path: str) -> list[Txn]:
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    txns: list[Txn] = []
    for i, row in enumerate(raw):
        action = str(row.get("action", "")).strip().lower()
        # Accept the Turkish terms the scan may have preserved.
        action = {"alım": "buy", "alim": "buy", "satış": "sell",
                  "satis": "sell"}.get(action, action)
        if action not in ("buy", "sell"):
            raise ValueError(f"ledger row {i}: unknown action {row.get('action')!r}")
        txns.append(Txn(
            date=dt.date.fromisoformat(str(row["date"])[:10]),
            ticker=str(row["ticker"]).upper(),
            action=action,
            lot=float(row["lot"]),
            price=float(row["price"]),
            sleeve=str(row.get("sleeve", "")),
            note=str(row.get("note", "")),
            porttech=str(row.get("porttech", "")),
        ))
    txns.sort(key=lambda t: (t.date, t.ticker))
    return txns


# --------------------------------------------------------------------------
# FIFO matching
# --------------------------------------------------------------------------

@dataclass
class MatchResult:
    trips: list[RoundTrip] = field(default_factory=list)
    open_lots: list[OpenLot] = field(default_factory=list)
    trims: list[Trim] = field(default_factory=list)
    unmatched_sells: list[Txn] = field(default_factory=list)


def fifo_match(txns: list[Txn]) -> MatchResult:
    """Match sells against the oldest open buys, per ticker.

    A sell with no matching lot (history began mid-position) is recorded as
    unmatched rather than dropped — the report must own the gap.
    """
    result = MatchResult()
    book: dict[str, list[OpenLot]] = defaultdict(list)

    for txn in txns:
        if txn.action == "buy":
            book[txn.ticker].append(OpenLot(txn.ticker, txn.sleeve, txn.date,
                                            txn.lot, txn.price, txn.porttech))
            continue

        remaining = txn.lot
        lots = book[txn.ticker]
        while remaining > 1e-9 and lots:
            lot = lots[0]
            take = min(lot.lot, remaining)
            result.trips.append(RoundTrip(
                ticker=txn.ticker, sleeve=lot.sleeve or txn.sleeve,
                entry_date=lot.date, exit_date=txn.date,
                lot=take, entry_price=lot.price, exit_price=txn.price,
                entry_porttech=lot.porttech,
            ))
            lot.lot -= take
            remaining -= take
            if lot.lot <= 1e-9:
                lots.pop(0)

        if remaining > 1e-9:
            result.unmatched_sells.append(Txn(txn.date, txn.ticker, "sell",
                                              remaining, txn.price, txn.sleeve,
                                              txn.note))
        held_after = sum(l.lot for l in lots)
        if held_after > 1e-9:
            result.trims.append(Trim(txn.ticker, txn.date, txn.lot, txn.price,
                                     held_after, txn.note))

    for lots in book.values():
        result.open_lots.extend(l for l in lots if l.lot > 1e-9)
    return result


# --------------------------------------------------------------------------
# Price lookups
# --------------------------------------------------------------------------

class PriceBook:
    """Per-ticker daily closes, loaded lazily from bars-dir JSON files."""

    def __init__(self, bars_dir: str):
        self._dir = bars_dir
        self._cache: dict[str, pd.Series | None] = {}

    def closes(self, ticker: str) -> pd.Series | None:
        ticker = ticker.upper()
        if ticker not in self._cache:
            try:
                snap = parse_market(json.load(open(
                    f"{self._dir}/{ticker}.json", encoding="utf-8")))
                closes = snap.bars["close"]
                closes.index = pd.to_datetime(closes.index).date
                self._cache[ticker] = closes
            except (FileNotFoundError, MarketDataError, ValueError,
                    json.JSONDecodeError):
                self._cache[ticker] = None
        return self._cache[ticker]

    def at(self, ticker: str, when: dt.date) -> float | None:
        """Close on `when`, or the nearest prior session within 7 days."""
        closes = self.closes(ticker)
        if closes is None:
            return None
        for back in range(8):
            day = when - dt.timedelta(days=back)
            if day in closes.index:
                return float(closes.loc[day])
        return None

    def ret(self, ticker: str, start: dt.date, end: dt.date) -> float | None:
        a, b = self.at(ticker, start), self.at(ticker, end)
        if a is None or b is None or a <= 0:
            return None
        return (b - a) / a * 100.0


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------

def bucket(values: list[float]) -> dict:
    """Summary stats that always carry their n."""
    n = len(values)
    if n == 0:
        return {"n": 0}
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    return {
        "n": n,
        "win_rate_pct": round(len(wins) / n * 100.0, 1),
        "mean_pct": round(statistics.mean(values), 2),
        "median_pct": round(statistics.median(values), 2),
        "avg_win_pct": round(statistics.mean(wins), 2) if wins else None,
        "avg_loss_pct": round(statistics.mean(losses), 2) if losses else None,
        "best_pct": round(max(values), 2),
        "worst_pct": round(min(values), 2),
    }


def analyze(txns: list[Txn], prices: PriceBook, index_ticker: str,
            as_of: dt.date | None = None) -> dict:
    as_of = as_of or dt.date.today()
    match = fifo_match(txns)

    # 1. Round trips
    trip_returns = [t.pnl_pct for t in match.trips]
    realised_pnl = round(sum(t.pnl for t in match.trips), 2)
    hold_times = [t.hold_days for t in match.trips]

    # 2. Each trip vs holding the index over the identical window
    vs_index, alpha = [], []
    for trip in match.trips:
        idx = prices.ret(index_ticker, trip.entry_date, trip.exit_date)
        if idx is not None:
            vs_index.append(trip.pnl_pct - idx)
            alpha.append((trip, idx))

    # 3. Trim quality: price path after each partial sell
    trim_outcomes = {30: [], 60: [], 90: []}
    for trim in match.trims:
        for horizon in (30, 60, 90):
            end = trim.date + dt.timedelta(days=horizon)
            if end > as_of:
                continue  # horizon not yet elapsed — skip, don't guess
            later = prices.at(trim.ticker, end)
            if later is not None and trim.price > 0:
                # positive = price rose after he trimmed = the trim cost him
                trim_outcomes[horizon].append(
                    (later - trim.price) / trim.price * 100.0)

    # 4. Porttech predictiveness — only from tags recorded at entry time
    tag_returns: dict[str, list[float]] = defaultdict(list)
    tagged = 0
    for trip in match.trips:
        tag = _normalize_tag(trip.entry_porttech)
        if tag:
            tagged += 1
            tag_returns[tag].append(trip.pnl_pct)

    # Open positions, marked to the latest bar (separate from realised stats)
    open_marks = []
    for lot in match.open_lots:
        now = prices.at(lot.ticker, as_of)
        open_marks.append({
            "ticker": lot.ticker, "sleeve": lot.sleeve,
            "date": lot.date.isoformat(), "lot": lot.lot,
            "entry": lot.price,
            "mark": now,
            "unrealised_pct": round((now - lot.price) / lot.price * 100.0, 2)
            if now else None,
        })

    ledger_span = (txns[0].date.isoformat(), txns[-1].date.isoformat()) if txns else None
    index_same_window = (prices.ret(index_ticker, txns[0].date, min(txns[-1].date, as_of))
                         if txns else None)

    return {
        "as_of": as_of.isoformat(),
        "ledger_span": ledger_span,
        "counts": {
            "transactions": len(txns),
            "round_trips": len(match.trips),
            "open_lots": len(match.open_lots),
            "trims": len(match.trims),
            "unmatched_sells": len(match.unmatched_sells),
        },
        "realised": {
            "total_pnl": realised_pnl,
            "returns": bucket(trip_returns),
            "hold_days_median": statistics.median(hold_times) if hold_times else None,
        },
        "vs_index": {
            "index": index_ticker,
            "index_return_over_ledger_span_pct":
                round(index_same_window, 2) if index_same_window is not None else None,
            "excess_per_trip": bucket(vs_index),
            "trips_with_index_data": len(vs_index),
            "trips_missing_index_data": len(match.trips) - len(vs_index),
        },
        "trim_quality": {
            str(h): {**bucket(vals),
                     "reading": "positive mean = price kept rising after his "
                                "trims (trimming cost him); negative = trims "
                                "were well timed"}
            for h, vals in trim_outcomes.items()
        },
        "porttech": {
            "trips_with_entry_tag": tagged,
            "trips_without": len(match.trips) - tagged,
            "note": "Only tags recorded AT transaction time count. Tags from a "
                    "later snapshot are not back-filled — that would be "
                    "hindsight dressed as data.",
            "by_tag": {tag: bucket(vals) for tag, vals in sorted(tag_returns.items())},
        },
        "open_positions": open_marks,
        "honesty": [
            f"Ledger begins {ledger_span[0] if ledger_span else 'n/a'}; results "
            "reflect that window's tape. The vs-index columns are the fair "
            "comparison — the index rode the same tape.",
            "Only closed round-trips enter realised stats; open positions are "
            "marked separately above and can tell a different story.",
            "FIFO lot matching is a convention, not his broker's statement. "
            "Totals here need not equal the platform's Gerçekleşen K/Z; report "
            "the difference, don't force agreement.",
            f"{len(match.unmatched_sells)} sell(s) had no matching buy in the "
            "ledger (position predates history). Their P&L is unknowable from "
            "this data and is excluded, not estimated.",
            "Buckets with small n prove nothing. Every figure above carries "
            "its n for exactly that reason.",
        ],
    }


def _normalize_tag(raw: str) -> str | None:
    lowered = (raw or "").lower()
    for key, tag in (("kritik", "Kritik"), ("kırılgan", "Kritik"),
                     ("kirilgan", "Kritik"), ("izle", "İzle"),
                     ("i̇zle", "İzle"), ("sağlıklı", "Sağlıklı"),
                     ("saglikli", "Sağlıklı")):
        if key in lowered:
            return tag
    return None


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render(report: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 70)
    add(f"  BACKTEST — ledger {report['ledger_span'][0]} → "
        f"{report['ledger_span'][1]}   (as of {report['as_of']})")
    add("=" * 70)

    counts = report["counts"]
    add(f"  {counts['transactions']} transactions → {counts['round_trips']} closed "
        f"round-trips, {counts['open_lots']} open lots, {counts['trims']} trims, "
        f"{counts['unmatched_sells']} unmatched sells")
    add("")

    realised = report["realised"]
    ret = realised["returns"]
    add("REALISED (closed trips only)")
    add("-" * 70)
    add(f"  Total P&L: {realised['total_pnl']:+,.2f}")
    if ret["n"]:
        add(f"  Win rate {ret['win_rate_pct']}% (n={ret['n']})   "
            f"mean {ret['mean_pct']:+.2f}%   median {ret['median_pct']:+.2f}%")
        add(f"  Avg win {ret['avg_win_pct']}%   avg loss {ret['avg_loss_pct']}%   "
            f"best {ret['best_pct']:+.2f}%   worst {ret['worst_pct']:+.2f}%")
        add(f"  Median hold: {realised['hold_days_median']} days")
    add("")

    vsi = report["vs_index"]
    add(f"VS {vsi['index']} (same-window comparison, the one that matters)")
    add("-" * 70)
    if vsi["index_return_over_ledger_span_pct"] is not None:
        add(f"  {vsi['index']} over the whole ledger span: "
            f"{vsi['index_return_over_ledger_span_pct']:+.2f}%")
    excess = vsi["excess_per_trip"]
    if excess["n"]:
        add(f"  Excess return per trip: mean {excess['mean_pct']:+.2f}%, "
            f"median {excess['median_pct']:+.2f}% (n={excess['n']})")
        add(f"  Trips beating the index: {excess['win_rate_pct']}%")
    if vsi["trips_missing_index_data"]:
        add(f"  ({vsi['trips_missing_index_data']} trips lacked index bars — "
            f"excluded, not guessed)")
    add("")

    add("TRIM QUALITY (price path AFTER his partial sells)")
    add("-" * 70)
    for horizon, stats in report["trim_quality"].items():
        if stats["n"]:
            add(f"  {horizon}d after a trim: mean {stats['mean_pct']:+.2f}% "
                f"(n={stats['n']})")
    add("  positive = price kept rising after he trimmed (the trim cost him)")
    add("")

    pt = report["porttech"]
    add("PORTTECH TAG PREDICTIVENESS")
    add("-" * 70)
    add(f"  Trips with a tag recorded at entry: {pt['trips_with_entry_tag']} "
        f"of {pt['trips_with_entry_tag'] + pt['trips_without']}")
    for tag, stats in pt["by_tag"].items():
        add(f"  {tag}: mean {stats['mean_pct']:+.2f}%, win rate "
            f"{stats['win_rate_pct']}% (n={stats['n']})")
    if not pt["by_tag"]:
        add("  No entry-time tags in the ledger — this section needs the scan "
            "to record tags on future transactions before it can say anything.")
    add("")

    if report["open_positions"]:
        add(f"OPEN POSITIONS ({len(report['open_positions'])}) — not in the "
            f"realised stats")
        add("-" * 70)
        for pos in sorted(report["open_positions"],
                          key=lambda p: p["unrealised_pct"] or 0):
            mark = f"{pos['unrealised_pct']:+.2f}%" if pos["unrealised_pct"] is not None else "no mark"
            add(f"  {pos['ticker']:<6} {pos['sleeve']:<4} lot {pos['lot']:<8} "
                f"entry {pos['entry']:<10} {mark}")
        add("")

    add("HONESTY")
    add("-" * 70)
    for note in report["honesty"]:
        add(f"  - {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a transaction ledger.")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--bars-dir", required=True)
    parser.add_argument("--index", default="SPY")
    parser.add_argument("--as-of", help="YYYY-MM-DD, default today")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        txns = load_ledger(args.ledger)
        prices = PriceBook(args.bars_dir)
        as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
        report = analyze(txns, prices, args.index.upper(), as_of)
    except (ValueError, OSError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
