"""
stockdata.py
------------
Fetches stock data from yfinance and computes financial metrics (P/B, P/E,
Market Cap) for defense contractors. Every fetch defaults to period="max" so
history always runs through the most recent completed trading session --
yfinance itself, not a cron job, is what keeps this current: every call
re-pulls through today's session.

All functions handle missing/unavailable data gracefully (never raise), so
ingest_stocks.py and the Streamlit app never crash on a bad ticker or
missing field. Vendored from the sibling defense-dashboard project so this
repo has no cross-repo dependency and works standalone on a fresh deploy.
"""

import warnings
import yfinance as yf
import pandas as pd

# AUD is the only non-USD currency needed here (Austal's ASX fallback listing).
FX_TICKERS = {
    "AUD": "AUDUSD=X",
}


def fetch_ticker_data(ticker_symbol, period="max", interval="1d"):
    """
    Download price history for a single ticker from Yahoo Finance, through
    the most recent available trading session.

    Returns:
        tuple: (pd.DataFrame OHLCV history, yf.Ticker object), or (None, None)
        on any failure or empty result. Never raises.
    """
    try:
        t = yf.Ticker(ticker_symbol)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hist = t.history(period=period, interval=interval)

        if hist is None or hist.empty:
            print(f"WARNING: No price history returned for {ticker_symbol}")
            return None, None

        return hist, t
    except Exception as e:
        print(f"WARNING: Could not fetch data for {ticker_symbol}: {e}")
        return None, None


def resolve_best_ticker(candidates, period="max", interval="1d"):
    """
    Try a list of (ticker, currency) candidates in order, return the first
    one with usable price history. Used for Austal's ADR-then-ASX fallback.

    Returns:
        tuple: (hist_df, ticker_obj, currency, ticker_symbol), or
        (None, None, None, None) if every candidate fails.
    """
    for ticker_symbol, currency in candidates:
        print(f"[TRY] {ticker_symbol} ({currency}) ...")
        hist_df, ticker_obj = fetch_ticker_data(ticker_symbol, period=period, interval=interval)
        if hist_df is not None and ticker_obj is not None:
            return hist_df, ticker_obj, currency, ticker_symbol
        print(f"WARNING: Candidate {ticker_symbol} failed; trying next fallback if any")

    print(f"WARNING: All candidates exhausted for {candidates}; no usable ticker found")
    return None, None, None, None


def get_fx_series(currency, period="max", interval="1d"):
    """Fetch an FX rate series (Close) for converting a local currency to USD."""
    fx_ticker = FX_TICKERS.get(currency)
    if fx_ticker is None:
        print(f"WARNING: No FX ticker configured for currency '{currency}'")
        return None
    hist_df, _ = fetch_ticker_data(fx_ticker, period=period, interval=interval)
    if hist_df is None:
        return None
    return hist_df["Close"]


def convert_to_usd(price_series, currency, fx_series=None):
    """
    Convert a local-currency price Series to USD. AUDUSD=X quotes "USD per 1
    AUD", so we multiply.
    """
    if currency == "USD":
        return price_series

    try:
        if price_series is None or price_series.empty:
            print("WARNING: convert_to_usd received an empty price series; skipping")
            return None

        if fx_series is None:
            fx_series = get_fx_series(currency)
        if fx_series is None or fx_series.empty:
            print(f"WARNING: No FX series available for {currency}; cannot convert to USD")
            return None

        local_index = price_series.index.tz_localize(None) if price_series.index.tz is not None else price_series.index
        fx_index = fx_series.index.tz_localize(None) if fx_series.index.tz is not None else fx_series.index
        price_series = pd.Series(price_series.values, index=local_index)
        fx_series = pd.Series(fx_series.values, index=fx_index)

        fx_aligned = fx_series.reindex(price_series.index).ffill()

        if currency == "AUD":
            usd_series = price_series * fx_aligned
        else:
            print(f"WARNING: Unrecognized currency '{currency}'; cannot convert to USD")
            return None

        usd_series = usd_series.dropna()
        if usd_series.empty:
            print(f"WARNING: USD conversion for currency '{currency}' produced no valid rows")
            return None
        return usd_series
    except Exception as e:
        print(f"WARNING: convert_to_usd failed for currency '{currency}': {e}")
        return None


def get_pb_series(hist_df, ticker_obj):
    """P/B = Close / book value per share (bookValue treated as a constant, latest-known value)."""
    try:
        info = ticker_obj.info
        book_value = info.get("bookValue", None)
        if book_value is None or book_value <= 0:
            print("WARNING: Valid bookValue not available in ticker info")
            return None
        return hist_df["Close"] / book_value
    except Exception as e:
        print(f"WARNING: Could not compute P/B series: {e}")
        return None


def get_pe_series(hist_df, ticker_obj):
    """P/E = Close / trailing EPS (constant, latest-known value)."""
    try:
        info = ticker_obj.info
        trailing_eps = info.get("trailingEps", None)
        if trailing_eps is None or trailing_eps == 0:
            print("WARNING: Valid trailingEps not available in ticker info")
            return None
        return hist_df["Close"] / trailing_eps
    except Exception as e:
        print(f"WARNING: Could not compute P/E series: {e}")
        return None


def get_market_cap_series(hist_df, ticker_obj):
    """
    Market Cap = Close * shares outstanding (sharesOutstanding treated as a
    constant, latest-known value -- same simplification as P/B and P/E).
    """
    try:
        info = ticker_obj.info
        shares = info.get("sharesOutstanding", None)
        if shares is None or shares <= 0:
            print("WARNING: Valid sharesOutstanding not available in ticker info")
            return None
        return hist_df["Close"] * shares
    except Exception as e:
        print(f"WARNING: Could not compute Market Cap series: {e}")
        return None


def truncate_at_last_valid(series, grace_days=5):
    """
    "Dead stock" handling per checklist: stop recording a company as of its
    date of death (delisting/last trade) rather than letting later steps
    (e.g. a forward-fill across an outer-joined date index) silently carry
    its last known price forward forever, which would misrepresent a dead
    stock as flat-lining rather than absent.

    A stock is considered "alive" through its own last observation date;
    grace_days is a small buffer (default 5 trading days) to avoid false
    positives from ordinary reporting lag. Callers that build a combined
    index across multiple companies (see compute_industry_averages) should
    apply this to each company's series BEFORE forward-filling onto the
    shared date index.

    Returns:
        pd.Series unchanged (yfinance history already stops at the last
        traded session for a delisted ticker -- this function exists mainly
        as the documented hook other code calls into so the "why" is
        traceable to the checklist requirement, and so that any future
        multi-series alignment step has a single place to enforce the rule).
    """
    if series is None or series.empty:
        return series
    return series.dropna()


def compute_industry_averages(company_series_dict, metric_key=None):
    """
    Mean time series across multiple companies, aligned via outer join and
    forward-filled across calendar gaps -- EXCEPT past a company's own last
    valid observation date, where its contribution is dropped instead of
    held flat (see truncate_at_last_valid / checklist's dead-stock rule).

    Returns:
        pd.Series or None if no valid series exist.
    """
    valid_series = [s for s in company_series_dict.values() if s is not None and not s.empty]
    if not valid_series:
        print(f"WARNING: No valid series for industry average ({metric_key})")
        return None

    cleaned = []
    last_valid_dates = []
    for s in valid_series:
        s = truncate_at_last_valid(s)
        if s.index.tz is not None:
            s = pd.Series(s.values, index=s.index.tz_localize(None))
        cleaned.append(s)
        last_valid_dates.append(s.index.max())

    combined = pd.concat(cleaned, axis=1).sort_index()
    combined = combined.ffill()

    # undo the indefinite ffill tail past each column's own last real date --
    # a "dead" company should drop out of the average, not flat-line it
    for col, last_date in zip(combined.columns, last_valid_dates):
        combined.loc[combined.index > last_date, col] = None

    return combined.mean(axis=1, skipna=True)


def compute_industry_sum(company_series_dict, metric_key=None):
    """
    Sum (rather than mean) across companies -- used for total sub-industry /
    industry Market Cap, with the same dead-stock drop-off as
    compute_industry_averages.

    Returns:
        pd.Series or None if no valid series exist.
    """
    valid_series = [s for s in company_series_dict.values() if s is not None and not s.empty]
    if not valid_series:
        print(f"WARNING: No valid series for industry sum ({metric_key})")
        return None

    cleaned = []
    last_valid_dates = []
    for s in valid_series:
        s = truncate_at_last_valid(s)
        if s.index.tz is not None:
            s = pd.Series(s.values, index=s.index.tz_localize(None))
        cleaned.append(s)
        last_valid_dates.append(s.index.max())

    combined = pd.concat(cleaned, axis=1).sort_index()
    combined = combined.ffill()
    for col, last_date in zip(combined.columns, last_valid_dates):
        combined.loc[combined.index > last_date, col] = None

    return combined.sum(axis=1, skipna=True, min_count=1)
