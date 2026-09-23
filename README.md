# DoD Budget Dashboard

Ingests DoD Comptroller budget exhibits (P-1 Procurement, O-1 Operations &
Maintenance, R-1 RDT&E, RF-1 Working Capital Fund) for FY2000-FY2027 plus
defense-contractor stock data, loads both into SQLite, and serves a Streamlit
dashboard for exploring spend trends by branch/program alongside
contractor market cap.

## Layout

- `raw_data/` — source DoD Comptroller xlsx/xls exhibit files, FY2002-FY2027 vintages.
- `scripts/parsers.py` — parses every budget-exhibit format vintage into a common long-format record shape.
- `scripts/schema.py` — SQLite schema (`budget_line`, `stock_price` tables).
- `scripts/ingest.py` — parses all files in `raw_data/` into `db/dow_budget.sqlite`.
- `scripts/ingest_stocks.py` — fetches defense-contractor stock data via yfinance into the same db.
- `app.py` — Streamlit dashboard.

## Setup

```bash
pip install -r requirements.txt   # pandas, streamlit, yfinance, openpyxl, xlrd
cd scripts && python3 ingest.py && python3 ingest_stocks.py
cd .. && streamlit run app.py
```

The `db/*.sqlite` file is gitignored (regenerate it with the two ingest
scripts above rather than committing a stale copy — stock data in particular
changes daily).

## Known gaps

- FY2027 R-1 exhibit not yet published/downloaded.
- Market cap/P-B/P-E ratios use yfinance's *current* shares-outstanding/book-value/EPS
  applied across all historical prices, which misrepresents companies that
  underwent a reverse split (e.g. Vision Marine Technologies) — treat those
  outliers with suspicion.
- No true per-contractor spend attribution yet (DoD's exhibits break spend
  out by branch/program, not by contractor); the "Spend vs. Market Cap" tab
  is a branch-vs-subindustry proxy, not a contract-level join.
