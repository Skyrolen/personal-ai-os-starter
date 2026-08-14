"""Technical indicators.

Pure functions over pandas Series/DataFrames. No network, no I/O, no globals —
everything here is deterministic and unit-testable, because these numbers feed
position-sizing decisions and a silent regression would be expensive.

Conventions:
  - Every function returns a Series aligned to the input index, NaN-padded at
    the front where there isn't enough history yet. Callers must check for NaN
    rather than assuming a value exists.
  - RSI and ATR use Wilder's smoothing with an SMA seed, which is what
    charting packages display. An EMA approximation drifts from those values
    on short histories, which matters when a threshold like "RSI > 70" is
    being used as a veto.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# Moving averages
# --------------------------------------------------------------------------

def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    _require_period(period)
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, standard alpha = 2/(period+1)."""
    _require_period(period)
    return series.ewm(span=period, adjust=False).mean()


def _wilder_smooth(values: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing: SMA seed over the first `period` values, then
    prev*(n-1)/n + current/n. Reindexed back onto the caller's index."""
    valid = values.dropna()
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if len(valid) < period:
        return out

    arr = valid.to_numpy(dtype=float)
    smoothed = np.full(len(arr), np.nan)
    smoothed[period - 1] = arr[:period].mean()
    for i in range(period, len(arr)):
        smoothed[i] = (smoothed[i - 1] * (period - 1) + arr[i]) / period

    out.loc[valid.index] = smoothed
    return out


# --------------------------------------------------------------------------
# Momentum
# --------------------------------------------------------------------------

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. Returns 0-100, NaN until enough history."""
    _require_period(period)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = _wilder_smooth(gain, period)
    avg_loss = _wilder_smooth(loss, period)

    # An all-gains window means avg_loss == 0, where RS is undefined; RSI is
    # 100 by definition there. Guard explicitly instead of dividing by zero.
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out[(avg_loss == 0.0) & avg_gain.notna()] = 100.0
    out[(avg_gain == 0.0) & avg_loss.notna() & (avg_loss > 0)] = 0.0
    return out


@dataclass
class MacdResult:
    macd: pd.Series
    signal: pd.Series
    histogram: pd.Series


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal_period: int = 9) -> MacdResult:
    """MACD line, signal line, and histogram."""
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be < slow ({slow})")
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal_period)
    return MacdResult(macd_line, signal_line, macd_line - signal_line)


# --------------------------------------------------------------------------
# Volatility
# --------------------------------------------------------------------------

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """max(H-L, |H-prevC|, |L-prevC|). First bar has no previous close, so it
    falls back to H-L rather than NaN."""
    prev_close = close.shift(1)
    ranges = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1)
    return ranges.max(axis=1, skipna=True)


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    """Average True Range, Wilder-smoothed."""
    _require_period(period)
    return _wilder_smooth(true_range(high, low, close), period)


# --------------------------------------------------------------------------
# Structure: support / resistance
# --------------------------------------------------------------------------

@dataclass
class Level:
    price: float
    touches: int
    kind: str  # "support" | "resistance"

    def __str__(self) -> str:
        return f"{self.kind} {self.price:.2f} ({self.touches} touches)"


def pivot_points(series: pd.Series, window: int = 5, kind: str = "high") -> pd.Series:
    """Fractal pivots: a bar that is the extreme of the +/- `window` bars
    around it. Returns only the pivot values, indexed by their bar."""
    if kind not in ("high", "low"):
        raise ValueError("kind must be 'high' or 'low'")
    span = 2 * window + 1
    if kind == "high":
        extreme = series.rolling(span, center=True).max()
    else:
        extreme = series.rolling(span, center=True).min()
    return series[series == extreme].dropna()


def support_resistance(high: pd.Series, low: pd.Series, close: pd.Series,
                       window: int = 5, tolerance_pct: float = 1.5,
                       max_levels: int = 4) -> list[Level]:
    """Cluster pivot highs/lows into price levels.

    Levels within `tolerance_pct` of each other are treated as the same level
    and their touch counts combined — three tests of "about $180" is one real
    level, not three. Levels are classified relative to the latest close and
    returned strongest-first (most touches, then nearest)."""
    last = float(close.iloc[-1])
    highs = pivot_points(high, window, "high")
    lows = pivot_points(low, window, "low")

    levels: list[Level] = []
    for pivots, kind in ((highs, "resistance"), (lows, "support")):
        for price in _cluster(sorted(pivots.tolist()), tolerance_pct):
            # Classify by where it actually sits now, not by which pivot type
            # produced it — old resistance becomes support once price is above.
            actual = "resistance" if price[0] > last else "support"
            levels.append(Level(price=price[0], touches=price[1], kind=actual))

    levels.sort(key=lambda lv: (-lv.touches, abs(lv.price - last)))
    return levels[:max_levels]


def _cluster(prices: list[float], tolerance_pct: float) -> list[tuple[float, int]]:
    """Group nearby prices into (mean_price, count) buckets."""
    if not prices:
        return []
    clusters: list[list[float]] = [[prices[0]]]
    for price in prices[1:]:
        anchor = clusters[-1][0]
        if anchor > 0 and abs(price - anchor) / anchor * 100.0 <= tolerance_pct:
            clusters[-1].append(price)
        else:
            clusters.append([price])
    return [(float(np.mean(c)), len(c)) for c in clusters]


# --------------------------------------------------------------------------
# Composite read
# --------------------------------------------------------------------------

@dataclass
class TechnicalRead:
    """Everything /bora-check needs to compare against someone else's chart
    opinion. Deliberately reports raw numbers alongside the verbal call so the
    agent can show its work rather than asserting a conclusion."""
    ticker: str
    last_close: float
    trend: str
    sma20: float | None
    sma50: float | None
    sma200: float | None
    rsi14: float | None
    macd_state: str
    atr14: float | None
    atr_pct: float | None
    volume_vs_avg: float | None
    levels: list[Level] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def analyze(df: pd.DataFrame, ticker: str = "") -> TechnicalRead:
    """Run the full indicator suite over an OHLCV frame.

    Expects columns Open/High/Low/Close/Volume (case-insensitive). Degrades
    gracefully on short history: indicators that need more bars than are
    available come back None with a note explaining which, rather than
    silently reporting a number computed from too little data.
    """
    df = _normalize_columns(df)
    close, high, low = df["close"], df["high"], df["low"]
    volume = df.get("volume")
    notes: list[str] = []

    n = len(df)
    if n < 2:
        raise ValueError(f"need at least 2 bars to analyze, got {n}")

    def _last(series: pd.Series | None) -> float | None:
        if series is None or series.empty or pd.isna(series.iloc[-1]):
            return None
        return float(series.iloc[-1])

    s20, s50, s200 = _last(sma(close, 20)), _last(sma(close, 50)), _last(sma(close, 200))
    for period, value in ((20, s20), (50, s50), (200, s200)):
        if value is None:
            notes.append(f"SMA{period} unavailable — only {n} bars of history")

    last = float(close.iloc[-1])
    trend = _classify_trend(last, s20, s50, s200)

    macd_res = macd(close)
    macd_state = _classify_macd(macd_res)

    atr14 = _last(atr(high, low, close, 14))
    atr_pct = (atr14 / last * 100.0) if (atr14 and last) else None

    vol_ratio = None
    if volume is not None and len(volume.dropna()) >= 20:
        avg_vol = float(volume.tail(20).mean())
        if avg_vol > 0:
            vol_ratio = float(volume.iloc[-1]) / avg_vol

    levels = support_resistance(high, low, close) if n >= 20 else []
    if n < 20:
        notes.append("support/resistance skipped — needs at least 20 bars")

    return TechnicalRead(
        ticker=ticker,
        last_close=last,
        trend=trend,
        sma20=s20, sma50=s50, sma200=s200,
        rsi14=_last(rsi(close, 14)),
        macd_state=macd_state,
        atr14=atr14,
        atr_pct=atr_pct,
        volume_vs_avg=vol_ratio,
        levels=levels,
        notes=notes,
    )


def _classify_trend(last: float, s20: float | None, s50: float | None,
                    s200: float | None) -> str:
    known = [s for s in (s20, s50, s200) if s is not None]
    if not known:
        return "unknown (insufficient history)"
    above = sum(1 for s in known if last > s)
    if above == len(known):
        stacked = (s20 and s50 and s200 and s20 > s50 > s200)
        return "strong uptrend" if stacked else "uptrend"
    if above == 0:
        return "downtrend"
    return "mixed / range-bound"


def _classify_macd(res: MacdResult) -> str:
    macd_line, signal_line = res.macd, res.signal
    if macd_line.empty or pd.isna(macd_line.iloc[-1]) or pd.isna(signal_line.iloc[-1]):
        return "unknown"
    now = macd_line.iloc[-1] - signal_line.iloc[-1]
    if len(macd_line) < 2 or pd.isna(macd_line.iloc[-2]) or pd.isna(signal_line.iloc[-2]):
        return "bullish" if now > 0 else "bearish"
    before = macd_line.iloc[-2] - signal_line.iloc[-2]
    if before <= 0 < now:
        return "bullish crossover (fresh)"
    if before >= 0 > now:
        return "bearish crossover (fresh)"
    return "bullish" if now > 0 else "bearish"


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Accept Open/open/OPEN and flatten any MultiIndex columns."""
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out.columns = [str(c).lower() for c in out.columns]
    missing = {"open", "high", "low", "close"} - set(out.columns)
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {sorted(missing)}")
    return out


def _require_period(period: int) -> None:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
