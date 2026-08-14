"""Tests for indicators.py.

These check indicator maths against hand-computed values and structural
invariants. They deliberately do NOT assert against numbers copied from a
charting website, because a value I cannot derive here is a value I cannot
verify — it would encode a guess as an expectation.

Run:  .venv/bin/python tools/test_indicators.py
"""

from __future__ import annotations

import math
import sys

import numpy as np
import pandas as pd

import indicators


FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


def close_to(actual, expected, tol=1e-6) -> bool:
    if actual is None or (isinstance(actual, float) and math.isnan(actual)):
        return False
    return abs(float(actual) - float(expected)) < tol


# --------------------------------------------------------------------------

def test_sma():
    print("SMA")
    s = pd.Series([1.0, 2, 3, 4, 5])
    out = indicators.sma(s, 3)
    # First two have insufficient history; then (1+2+3)/3, (2+3+4)/3, (3+4+5)/3
    check("first two are NaN", out.iloc[:2].isna().all())
    check("sma[2] == 2.0", close_to(out.iloc[2], 2.0))
    check("sma[3] == 3.0", close_to(out.iloc[3], 3.0))
    check("sma[4] == 4.0", close_to(out.iloc[4], 4.0))


def test_ema():
    print("EMA")
    s = pd.Series([1.0, 2, 3])
    out = indicators.ema(s, 2)  # alpha = 2/(2+1) = 2/3
    # seed 1.0; then 2*(2/3)+1*(1/3)=1.666667; then 3*(2/3)+1.666667*(1/3)=2.555556
    check("ema[0] seeds to first value", close_to(out.iloc[0], 1.0))
    check("ema[1] == 1.666667", close_to(out.iloc[1], 5 / 3, 1e-9))
    check("ema[2] == 2.555556", close_to(out.iloc[2], 23 / 9, 1e-9))


def test_rsi_hand_computed():
    """Wilder RSI, period 3, worked through by hand.

    closes:  10, 11, 10.5, 11.5, 12, 11, 11.8
    deltas:      +1, -0.5, +1.0, +0.5, -1.0, +0.8
    gains:        1,    0,  1.0,  0.5,    0,  0.8
    losses:       0,  0.5,    0,    0,  1.0,    0

    seed (mean of first 3): gain 2/3, loss 1/6
    step 4 (+0.5): gain (2/3*2+0.5)/3 = 0.6111..., loss (1/6*2+0)/3 = 0.1111...
    step 5 (-1.0): gain (0.6111*2+0)/3 = 0.407407,
                   loss (0.1111*2+1.0)/3 = 0.407407  -> RS = 1 -> RSI = 50 exactly
    step 6 (+0.8): gain (0.407407*2+0.8)/3 = 0.538272,
                   loss (0.407407*2+0)/3   = 0.271605
                   RS = 1.981818...  -> RSI = 100 - 100/2.981818 = 66.4633...
    """
    print("RSI (hand-computed, period=3)")
    closes = pd.Series([10.0, 11, 10.5, 11.5, 12, 11, 11.8])
    out = indicators.rsi(closes, 3)

    check("RSI at the -1.0 step is exactly 50", close_to(out.iloc[5], 50.0, 1e-9),
          f"got {out.iloc[5]}")
    expected_final = 100.0 - 100.0 / (1.0 + (0.5382716049 / 0.2716049383))
    check("RSI final == 66.4633", close_to(out.iloc[6], expected_final, 1e-6),
          f"got {out.iloc[6]}, expected {expected_final}")


def test_rsi_invariants():
    print("RSI (invariants)")
    rising = pd.Series(np.arange(1.0, 40.0))
    falling = pd.Series(np.arange(40.0, 1.0, -1.0))
    noisy = pd.Series(np.random.default_rng(7).normal(100, 3, 200).cumsum() / 10 + 100)

    check("monotonic rise -> RSI 100", close_to(indicators.rsi(rising).iloc[-1], 100.0))
    check("monotonic fall -> RSI 0", close_to(indicators.rsi(falling).iloc[-1], 0.0))
    values = indicators.rsi(noisy).dropna()
    check("always within 0..100", bool(((values >= 0) & (values <= 100)).all()))
    check("no division-by-zero infinities", bool(np.isfinite(values).all()))


def test_macd_definition():
    """MACD is definitional, so verify the wiring rather than magic numbers:
    macd == ema(fast) - ema(slow), signal == ema(macd, 9)."""
    print("MACD")
    closes = pd.Series(np.random.default_rng(3).normal(0, 1, 120).cumsum() + 100)
    res = indicators.macd(closes, 12, 26, 9)

    expected_macd = indicators.ema(closes, 12) - indicators.ema(closes, 26)
    expected_signal = indicators.ema(expected_macd, 9)

    check("macd line matches ema12-ema26",
          bool(np.allclose(res.macd, expected_macd, equal_nan=True)))
    check("signal matches ema9(macd)",
          bool(np.allclose(res.signal, expected_signal, equal_nan=True)))
    check("histogram == macd - signal",
          bool(np.allclose(res.histogram, res.macd - res.signal, equal_nan=True)))
    check("fast >= slow is rejected",
          _raises(lambda: indicators.macd(closes, 26, 12)))


def test_true_range_and_atr():
    print("True Range / ATR")
    high = pd.Series([10.0, 12.0, 11.0])
    low = pd.Series([9.0, 10.0, 8.0])
    close = pd.Series([9.5, 11.5, 9.0])
    tr = indicators.true_range(high, low, close)
    # bar 0: no prev close -> H-L = 1.0
    # bar 1: max(2.0, |12-9.5|=2.5, |10-9.5|=0.5) = 2.5
    # bar 2: max(3.0, |11-11.5|=0.5, |8-11.5|=3.5) = 3.5
    check("tr[0] == 1.0 (falls back to H-L)", close_to(tr.iloc[0], 1.0))
    check("tr[1] == 2.5", close_to(tr.iloc[1], 2.5))
    check("tr[2] == 3.5", close_to(tr.iloc[2], 3.5))

    atr2 = indicators.atr(high, low, close, 2)
    # seed = mean(1.0, 2.5) = 1.75; then (1.75*1 + 3.5)/2 = 2.625
    check("atr[1] seeds to 1.75", close_to(atr2.iloc[1], 1.75))
    check("atr[2] == 2.625", close_to(atr2.iloc[2], 2.625))


def test_support_resistance():
    print("Support / resistance")
    # Sawtooth oscillating between ~90 and ~110 produces repeated pivots that
    # should cluster into a small number of levels, not one per swing.
    base = np.tile([100, 105, 110, 105, 100, 95, 90, 95], 12).astype(float)
    close = pd.Series(base)
    high = close + 1
    low = close - 1
    levels = indicators.support_resistance(high, low, close, window=2)

    check("returns at most 4 levels", len(levels) <= 4)
    check("clusters repeated swings", any(lv.touches > 1 for lv in levels),
          f"touches={[lv.touches for lv in levels]}")
    last = close.iloc[-1]
    check("classifies relative to last close",
          all((lv.kind == "resistance") == (lv.price > last) for lv in levels))


def test_analyze_short_history():
    """Short history must degrade to None + a note, never a number computed
    from too few bars."""
    print("analyze() on short history")
    df = pd.DataFrame({
        "Open": [100.0, 101, 102], "High": [101.0, 102, 103],
        "Low": [99.0, 100, 101], "Close": [100.5, 101.5, 102.5],
        "Volume": [1000, 1100, 1200],
    })
    read = indicators.analyze(df, "TEST")
    check("sma200 is None", read.sma200 is None)
    check("explains what was skipped", len(read.notes) > 0)
    check("still reports last close", close_to(read.last_close, 102.5))
    check("rejects a single bar",
          _raises(lambda: indicators.analyze(df.head(1), "TEST")))


def test_column_normalization():
    print("Column handling")
    df = pd.DataFrame({
        "open": [1.0, 2], "high": [2.0, 3], "low": [0.5, 1], "close": [1.5, 2.5],
    })
    read = indicators.analyze(df, "T")
    check("accepts lowercase columns", close_to(read.last_close, 2.5))
    check("rejects a frame missing High",
          _raises(lambda: indicators.analyze(df.drop(columns=["high"]), "T")))


def _raises(fn) -> bool:
    try:
        fn()
    except (ValueError, KeyError):
        return True
    except Exception:
        return False
    return False


def main() -> int:
    for test in (test_sma, test_ema, test_rsi_hand_computed, test_rsi_invariants,
                 test_macd_definition, test_true_range_and_atr,
                 test_support_resistance, test_analyze_short_history,
                 test_column_normalization):
        test()
        print()

    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {', '.join(FAILURES)}")
        return 1
    print("All indicator tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
