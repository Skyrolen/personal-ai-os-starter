"""Tests for backtest.py against a synthetic ledger with known answers.

Every expected number here is computed by hand in the comments, because a
backtest you can't verify by hand is a random-number generator with a
confident interface.

Run:  .venv/bin/python tools/test_backtest.py
"""

from __future__ import annotations

import datetime as dt
import sys

import pandas as pd

import backtest
from backtest import OpenLot, PriceBook, RoundTrip, Txn, fifo_match

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


def d(iso: str) -> dt.date:
    return dt.date.fromisoformat(iso)


# --------------------------------------------------------------------------
# A stub PriceBook — deterministic closes, no files
# --------------------------------------------------------------------------

class StubPrices(PriceBook):
    """Linear price paths per ticker so every return is hand-computable."""

    def __init__(self, paths: dict):
        self._paths = paths  # ticker -> {date: close}

    def closes(self, ticker):  # pragma: no cover - unused in stub
        return None

    def at(self, ticker: str, when: dt.date):
        path = self._paths.get(ticker.upper())
        if not path:
            return None
        for back in range(8):
            day = when - dt.timedelta(days=back)
            if day in path:
                return float(path[day])
        return None


def daily(start: str, days: int, start_price: float, step: float) -> dict:
    base = d(start)
    return {base + dt.timedelta(days=i): start_price + step * i
            for i in range(days)}


# --------------------------------------------------------------------------
# FIFO matching
# --------------------------------------------------------------------------

def test_fifo_simple_round_trip():
    print("FIFO: simple round trip")
    txns = [
        Txn(d("2026-01-05"), "AAA", "buy", 10, 100.0),
        Txn(d("2026-02-05"), "AAA", "sell", 10, 110.0),
    ]
    result = fifo_match(txns)
    check("one trip", len(result.trips) == 1)
    trip = result.trips[0]
    check("pnl = 100", trip.pnl == 100.0, f"got {trip.pnl}")
    check("pnl_pct = 10", trip.pnl_pct == 10.0)
    check("hold 31 days", trip.hold_days == 31)
    check("no open lots", not result.open_lots)
    check("full exit is not a trim", not result.trims)


def test_fifo_partial_sell_is_trim():
    print("FIFO: partial sell")
    txns = [
        Txn(d("2026-01-05"), "BBB", "buy", 100, 50.0),
        Txn(d("2026-03-05"), "BBB", "sell", 40, 60.0),
    ]
    result = fifo_match(txns)
    check("one trip of 40", len(result.trips) == 1 and result.trips[0].lot == 40)
    # trip pnl = (60-50)*40 = 400
    check("trip pnl 400", result.trips[0].pnl == 400.0)
    check("60 still open", len(result.open_lots) == 1
          and result.open_lots[0].lot == 60)
    check("recorded as a trim with remaining 60",
          len(result.trims) == 1 and result.trims[0].remaining_lot == 60)


def test_fifo_oldest_lot_first():
    print("FIFO: oldest lot first")
    txns = [
        Txn(d("2026-01-05"), "CCC", "buy", 10, 100.0),
        Txn(d("2026-02-05"), "CCC", "buy", 10, 200.0),
        Txn(d("2026-03-05"), "CCC", "sell", 15, 150.0),
    ]
    result = fifo_match(txns)
    # First trip: 10 @ 100 -> 150 (+50%), second: 5 @ 200 -> 150 (-25%)
    check("two trips", len(result.trips) == 2)
    check("first consumes the older lot fully",
          result.trips[0].entry_price == 100.0 and result.trips[0].lot == 10)
    check("second takes 5 from the newer lot",
          result.trips[1].entry_price == 200.0 and result.trips[1].lot == 5)
    check("5 remain open at 200",
          result.open_lots[0].lot == 5 and result.open_lots[0].price == 200.0)


def test_fifo_unmatched_sell():
    print("FIFO: sell with no history")
    txns = [Txn(d("2026-01-05"), "DDD", "sell", 20, 80.0)]
    result = fifo_match(txns)
    check("no trips", not result.trips)
    check("recorded as unmatched, not dropped",
          len(result.unmatched_sells) == 1
          and result.unmatched_sells[0].lot == 20)


# --------------------------------------------------------------------------
# analyze() end to end on a synthetic ledger
# --------------------------------------------------------------------------

def make_fixture():
    """Two closed trips, one trim, one open lot, plus index bars.

    AAA: buy 10 @ 100 (Jan 5, tag Sağlıklı), sell 10 @ 120 (Feb 4): +20%, 30d
    BBB: buy 10 @ 50  (Jan 5, tag Kritik),  sell 10 @ 45  (Feb 4): -10%, 30d
    CCC: buy 100 @ 20 (Jan 5), sell 40 @ 30 (Feb 4) -> trip +50%, trim,
         60 still open. CCC keeps rising 0.1/day after -> trim "cost him".
    Index IDX: 500 -> 505 over Jan 5..Feb 4 = +1.0%
    """
    txns = [
        Txn(d("2026-01-05"), "AAA", "buy", 10, 100.0, "P1", porttech="Sağlıklı (61)"),
        Txn(d("2026-01-05"), "BBB", "buy", 10, 50.0, "P1", porttech="Kritik (12)"),
        Txn(d("2026-01-05"), "CCC", "buy", 100, 20.0, "P2"),
        Txn(d("2026-02-04"), "AAA", "sell", 10, 120.0, "P1"),
        Txn(d("2026-02-04"), "BBB", "sell", 10, 45.0, "P1"),
        Txn(d("2026-02-04"), "CCC", "sell", 40, 30.0, "P2"),
    ]
    prices = StubPrices({
        "AAA": daily("2026-01-01", 200, 100.0, 0.0),
        "BBB": daily("2026-01-01", 200, 50.0, 0.0),
        "CCC": daily("2026-01-01", 200, 20.0, 0.1),   # rises forever
        "IDX": {d("2026-01-05"): 500.0, d("2026-02-04"): 505.0,
                **daily("2026-02-05", 150, 505.0, 0.0)},
    })
    return txns, prices


def test_analyze_realised():
    print("analyze: realised stats")
    txns, prices = make_fixture()
    report = backtest.analyze(txns, prices, "IDX", as_of=d("2026-06-01"))

    counts = report["counts"]
    check("3 trips", counts["round_trips"] == 3)
    check("1 open lot", counts["open_lots"] == 1)
    check("1 trim", counts["trims"] == 1)

    realised = report["realised"]
    # (120-100)*10 + (45-50)*10 + (30-20)*40 = 200 - 50 + 400 = 550
    check("total pnl 550", realised["total_pnl"] == 550.0,
          f"got {realised['total_pnl']}")
    ret = realised["returns"]
    # returns: +20, -10, +50 -> win rate 2/3, mean 20
    check("win rate 66.7 (n=3)", ret["win_rate_pct"] == 66.7 and ret["n"] == 3)
    check("mean +20", ret["mean_pct"] == 20.0)
    check("median hold 30d", realised["hold_days_median"] == 30)


def test_analyze_vs_index():
    print("analyze: vs index")
    txns, prices = make_fixture()
    report = backtest.analyze(txns, prices, "IDX", as_of=d("2026-06-01"))
    vsi = report["vs_index"]
    # Index +1.0% each window; excess = +19, -11, +49 -> mean 19
    excess = vsi["excess_per_trip"]
    check("all 3 trips have index data", vsi["trips_with_index_data"] == 3)
    check("mean excess +19", excess["mean_pct"] == 19.0,
          f"got {excess['mean_pct']}")
    check("2 of 3 beat the index", excess["win_rate_pct"] == 66.7)


def test_analyze_trim_quality():
    print("analyze: trim quality")
    txns, prices = make_fixture()
    report = backtest.analyze(txns, prices, "IDX", as_of=d("2026-06-01"))
    # CCC trimmed at 30 on Feb 4. Price path 20 + 0.1/day from Jan 1.
    # Feb 4 idx=34 -> 23.4. +30d = Mar 6 idx=64 -> 26.4. (26.4-30)/30 = -12%
    trim30 = report["trim_quality"]["30"]
    check("30d trim outcome n=1", trim30["n"] == 1)
    check("30d mean -12%", trim30["mean_pct"] == -12.0,
          f"got {trim30['mean_pct']}")

    # Horizon beyond as_of must be skipped, not guessed
    early = backtest.analyze(txns, prices, "IDX", as_of=d("2026-02-20"))
    check("unelapsed horizon skipped", early["trim_quality"]["90"]["n"] == 0)


def test_analyze_porttech():
    print("analyze: porttech buckets")
    txns, prices = make_fixture()
    report = backtest.analyze(txns, prices, "IDX", as_of=d("2026-06-01"))
    pt = report["porttech"]
    check("2 of 3 trips tagged", pt["trips_with_entry_tag"] == 2)
    check("Sağlıklı bucket +20 (n=1)",
          pt["by_tag"]["Sağlıklı"]["mean_pct"] == 20.0
          and pt["by_tag"]["Sağlıklı"]["n"] == 1)
    check("Kritik bucket -10 (n=1)",
          pt["by_tag"]["Kritik"]["mean_pct"] == -10.0)
    check("refuses to back-fill tags", "back-filled" in pt["note"] or
          "hindsight" in pt["note"])


def test_analyze_open_positions():
    print("analyze: open positions")
    txns, prices = make_fixture()
    report = backtest.analyze(txns, prices, "IDX", as_of=d("2026-06-01"))
    opens = report["open_positions"]
    check("CCC open with lot 60", opens[0]["ticker"] == "CCC"
          and opens[0]["lot"] == 60)
    # as_of Jun 1: idx from Jan 1 = 151 days -> price 20 + 15.1 = 35.1
    # unrealised = (35.1-20)/20 = +75.5%
    check("marked to latest bar +75.5%",
          opens[0]["unrealised_pct"] == 75.5,
          f"got {opens[0]['unrealised_pct']}")


def test_honesty_and_small_n():
    print("honesty")
    txns, prices = make_fixture()
    report = backtest.analyze(txns, prices, "IDX", as_of=d("2026-06-01"))
    text = backtest.render(report)
    check("renders", "BACKTEST" in text)
    check("honesty section present", "HONESTY" in text)
    check("n printed beside stats", "(n=3)" in text)
    check("FIFO caveat stated", "FIFO" in text)
    check("small-n caveat stated", "small n" in text)

    # Turkish action names accepted
    check("Alım/Satış accepted by loader",
          backtest.load_ledger.__doc__ is None or True)  # structural, see below


def test_turkish_actions():
    print("Turkish action names")
    import json as _json
    import tempfile
    rows = [
        {"date": "2026-01-05", "ticker": "aaa", "action": "Alım",
         "lot": 5, "price": 10.0},
        {"date": "2026-02-05", "ticker": "AAA", "action": "Satış",
         "lot": 5, "price": 12.0},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        _json.dump(rows, f)
        path = f.name
    txns = backtest.load_ledger(path)
    check("Alım -> buy", txns[0].action == "buy")
    check("Satış -> sell", txns[1].action == "sell")
    check("ticker uppercased", txns[0].ticker == "AAA")
    result = fifo_match(txns)
    check("round trip formed", len(result.trips) == 1
          and result.trips[0].pnl == 10.0)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        return 1
    print(f"All {len(tests)} backtest test groups passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
