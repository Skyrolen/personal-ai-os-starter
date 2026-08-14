"""Market data access.

Robinhood's agentic MCP gives live quotes but no historical OHLCV, so technical
analysis needs its own feed. yfinance is free and needs no key, which is plenty
for the position-trading horizon this agent works on.

Everything goes through the `MarketDataProvider` protocol so swapping in a paid
feed (Polygon, Tiingo, Finnhub) later means writing one new class, not touching
indicators.py or verify.py.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

import pandas as pd


class MarketDataError(RuntimeError):
    """Raised when data cannot be fetched or comes back unusable.

    Deliberately loud: a verification step that silently proceeds on empty data
    would produce a confident-looking brief backed by nothing.
    """


@dataclass
class Fundamentals:
    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_sales: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    debt_to_equity: float | None = None
    next_earnings: dt.date | None = None
    avg_volume_10d: float | None = None
    missing: list[str] = None  # fields the provider could not supply

    def __post_init__(self) -> None:
        if self.missing is None:
            self.missing = []


class MarketDataProvider(Protocol):
    def history(self, ticker: str, period: str = "1y",
                interval: str = "1d") -> pd.DataFrame: ...

    def quote(self, ticker: str) -> float: ...

    def fundamentals(self, ticker: str) -> Fundamentals: ...


class YFinanceProvider:
    """yfinance-backed provider. Free, no API key, delayed but adequate for
    daily-bar analysis."""

    def __init__(self) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover - environment issue
            raise MarketDataError(
                "yfinance is not installed. Run:\n"
                "  python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt"
            ) from exc

    def _ticker(self, ticker: str):
        import yfinance as yf
        return yf.Ticker(ticker.strip().upper())

    def history(self, ticker: str, period: str = "1y",
                interval: str = "1d") -> pd.DataFrame:
        df = self._ticker(ticker).history(period=period, interval=interval,
                                          auto_adjust=False)
        if df is None or df.empty:
            raise MarketDataError(
                f"No price history for {ticker!r} (period={period}, "
                f"interval={interval}). Check the symbol is correct and listed."
            )
        return df

    def quote(self, ticker: str) -> float:
        """Latest available close. This is delayed data — for anything
        execution-critical use Robinhood's get_equity_quotes instead."""
        df = self.history(ticker, period="5d", interval="1d")
        return float(df["Close"].iloc[-1])

    def fundamentals(self, ticker: str) -> Fundamentals:
        """Best-effort fundamentals. yfinance's `info` is scraped and fields
        come and go, so every value is optional and anything absent is recorded
        in `.missing` — the brief reports what it could not check rather than
        implying a clean bill of health."""
        symbol = ticker.strip().upper()
        tk = self._ticker(symbol)
        try:
            info = tk.info or {}
        except Exception:
            info = {}

        missing: list[str] = []

        def pick(*keys: str) -> float | None:
            for key in keys:
                value = info.get(key)
                if value is not None and not isinstance(value, str):
                    return value
            missing.append(keys[0])
            return None

        result = Fundamentals(
            ticker=symbol,
            name=info.get("shortName") or info.get("longName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            market_cap=pick("marketCap"),
            trailing_pe=pick("trailingPE"),
            forward_pe=pick("forwardPE"),
            price_to_sales=pick("priceToSalesTrailing12Months"),
            revenue_growth=pick("revenueGrowth"),
            earnings_growth=pick("earningsGrowth", "earningsQuarterlyGrowth"),
            debt_to_equity=pick("debtToEquity"),
            avg_volume_10d=pick("averageDailyVolume10Day", "averageVolume"),
            next_earnings=self._next_earnings(tk, info),
        )
        if result.next_earnings is None:
            missing.append("next_earnings")
        result.missing = missing
        return result

    def _next_earnings(self, tk, info: dict) -> dt.date | None:
        """Next scheduled earnings date, or None if unknown.

        None is meaningfully different from 'no earnings soon': the policy
        earnings-blackout check treats unknown as unverified and says so,
        rather than waving the trade through.
        """
        try:
            cal = tk.calendar
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date") or []
                if isinstance(dates, (list, tuple)) and dates:
                    return _as_date(dates[0])
                if dates:
                    return _as_date(dates)
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Earnings Date" in cal.index:
                    return _as_date(cal.loc["Earnings Date"].iloc[0])
        except Exception:
            pass

        for key in ("earningsTimestampStart", "earningsTimestamp"):
            ts = info.get(key)
            if isinstance(ts, (int, float)) and ts > 0:
                return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date()
        return None


def _as_date(value) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def get_provider(name: str = "yfinance") -> MarketDataProvider:
    """Factory. Add new providers here; callers never import them directly."""
    providers = {"yfinance": YFinanceProvider}
    if name not in providers:
        raise MarketDataError(
            f"Unknown provider {name!r}. Available: {sorted(providers)}"
        )
    return providers[name]()
