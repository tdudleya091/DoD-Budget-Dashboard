"""Fetches defense-contractor stock data via yfinance and loads it into the
same SQLite db as the budget data, so dashboards can join budget spend
against company market cap/valuation by year.

Ticker universe and fetch/dead-stock-handling logic (tickers.py/stockdata.py,
in this same directory) were originally built for and are still shared with
the sibling defense-dashboard project -- vendored here (not imported
cross-repo) so this project is self-contained and works on a fresh deploy
that only has this one repo checked out (e.g. Streamlit Community Cloud).
"""
import os
import sys

import pandas as pd

# make sibling modules importable regardless of how this file is invoked --
# see ingest.py for why this can't be left implicit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema import create_db
import tickers
import stockdata as dd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "dow_budget.sqlite")


def fetch_company(company, ticker_symbol):
    hist, tobj = dd.fetch_ticker_data(ticker_symbol)
    if hist is None:
        candidates = tickers.TICKER_CANDIDATES.get(company)
        if candidates:
            hist, tobj, currency, ticker_symbol = dd.resolve_best_ticker(candidates)
        if hist is None:
            return None

    currency = tickers.CURRENCY_MAP.get(company, "USD")
    close = dd.truncate_at_last_valid(hist["Close"])
    close_usd = dd.convert_to_usd(close, currency) if currency != "USD" else close
    if close_usd is None:
        return None

    pb = dd.get_pb_series(hist, tobj)
    pe = dd.get_pe_series(hist, tobj)
    mcap = dd.get_market_cap_series(hist, tobj)
    if currency != "USD":
        pb = dd.convert_to_usd(pb, currency) if pb is not None else None
        pe = dd.convert_to_usd(pe, currency) if pe is not None else None
        mcap = dd.convert_to_usd(mcap, currency) if mcap is not None else None

    idx = close_usd.index.tz_localize(None) if close_usd.index.tz is not None else close_usd.index
    df = pd.DataFrame({"close_usd": close_usd.values}, index=idx)
    for name, series in (("pb_ratio", pb), ("pe_ratio", pe), ("market_cap_usd", mcap)):
        if series is None:
            df[name] = None
            continue
        s_idx = series.index.tz_localize(None) if series.index.tz is not None else series.index
        s = pd.Series(series.values, index=s_idx)
        df[name] = s.reindex(df.index)

    df["company"] = company
    df["ticker"] = ticker_symbol
    df["sub_industry"] = tickers.SUB_INDUSTRY_MAP.get(company)
    df["date"] = df.index.strftime("%Y-%m-%d")
    return df.reset_index(drop=True)


def main():
    frames = []
    for company, ticker_symbol in tickers.TICKERS.items():
        print(f"Fetching {company} ({ticker_symbol})...")
        df = fetch_company(company, ticker_symbol)
        if df is None:
            print(f"  SKIP {company}: no usable data")
            continue
        print(f"  OK {company}: {len(df)} rows")
        frames.append(df)

    if not frames:
        print("No stock data fetched.")
        return

    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df[["company", "ticker", "sub_industry", "date", "close_usd",
                      "market_cap_usd", "pb_ratio", "pe_ratio"]]

    conn = create_db(DB_PATH)
    conn.execute("DELETE FROM stock_price")
    all_df.to_sql("stock_price", conn, if_exists="append", index=False)
    conn.commit()
    print(f"\nLoaded {len(all_df)} stock_price rows into {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
