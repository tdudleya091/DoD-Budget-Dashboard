"""DoD budget + defense-contractor stock dashboard -- first pass.

Covers a subset of the full checklist in `DoW data vis project.md`:
  - overall spend trend by exhibit type (P-1/O-1/R-1/RF-1) and by branch
  - procurement breakdown by budget activity for a chosen year
  - defense-contractor market cap over time (from the reused
    defense-dashboard yfinance ingestion)
Run: streamlit run app.py
"""
import os
import re
import sqlite3
import sys

import altair as alt
import pandas as pd
import streamlit as st

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "dow_budget.sqlite")
STOCK_SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "data", "stock_snapshot.parquet")

EXHIBIT_LABELS = {"P1": "Procurement (P-1)", "O1": "Operations & Maintenance (O-1)",
                   "R1": "RDT&E (R-1)", "RF1": "Working Capital Fund (RF-1)"}


def _table_has_rows(conn, table):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return False
    return conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None


@st.cache_resource
def ensure_database():
    """Builds db/dow_budget.sqlite from raw_data/ (and fetches stock data) on
    first run of a fresh deploy -- the db file itself is gitignored (it's
    regenerable and stock data changes daily), so a clean checkout like
    Streamlit Community Cloud's has no db until this runs. Cached so it only
    runs once per running app instance, not on every rerun/widget interaction.
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    from schema import create_db
    conn = create_db(DB_PATH)  # idempotent CREATE TABLE IF NOT EXISTS for both tables
    has_budget = _table_has_rows(conn, "budget_line")
    has_stock = _table_has_rows(conn, "stock_price")
    conn.close()

    if not has_budget:
        with st.spinner("First run: parsing DoD budget exhibits into the database..."):
            import ingest
            ingest.main()

    # Prefer the committed snapshot over a live yfinance fetch: Yahoo Finance
    # blocks/rate-limits requests from cloud-provider IP ranges (confirmed on
    # Streamlit Community Cloud), and its price-history and fundamentals
    # (.info, used for shares-outstanding/book-value/EPS) endpoints appear to
    # be rate-limited *separately* -- an earlier deploy attempt could leave
    # stock_price populated with prices but every market_cap/pb/pe NULL,
    # which a simple "does this table have any rows" check would treat as
    # already-loaded and skip. So: always (re)load from the snapshot when one
    # is committed, rather than only when the table looks empty. This is a
    # cheap operation (~1-2s for the full history) so re-running it on every
    # fresh process start costs nothing.
    if os.path.exists(STOCK_SNAPSHOT_PATH):
        with st.spinner("Loading defense-contractor stock snapshot..."):
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM stock_price")
            snap = pd.read_parquet(STOCK_SNAPSHOT_PATH)
            snap.to_sql("stock_price", conn, if_exists="append", index=False)
            conn.commit()
            conn.close()
    elif not has_stock:
        with st.spinner("First run: fetching defense-contractor stock data (yfinance)..."):
            try:
                import ingest_stocks
                ingest_stocks.main()
            except Exception as e:
                st.warning(f"Stock data fetch failed ({e}); budget dashboards will still work, "
                           "but stock/correlation tabs will be empty.")
    return True


ensure_database()


@st.cache_data
def load_budget_totals():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        """SELECT exhibit_type, fiscal_year, amount_type, branch,
                  SUM(amount_thousands) / 1e6 AS billions
           FROM budget_line GROUP BY exhibit_type, fiscal_year, amount_type, branch""",
        conn,
    )
    conn.close()
    return df


@st.cache_data
def load_procurement_detail(fiscal_year):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        """SELECT branch, budget_activity_title, SUM(amount_thousands) / 1e6 AS billions
           FROM budget_line
           WHERE exhibit_type = 'P1' AND fiscal_year = ? AND amount_type = 'request'
           GROUP BY branch, budget_activity_title
           HAVING billions > 0
           ORDER BY billions DESC""",
        conn, params=(fiscal_year,),
    )
    conn.close()
    return df


def _normalize_activity(title):
    """DoD budget activity titles drift in case/wording across vintages (e.g.
    "AIRCRAFT" vs "Aircraft", "... AND ..." vs "... & ..."), which fragments a
    multi-year trend if left as-is. This merges the common variants; a few
    finer abbreviation differences (e.g. "Supt" vs "Support") can still slip
    through as separate categories."""
    if title is None:
        return None
    t = str(title).upper().strip()
    t = re.sub(r"\s+AND\s+", " & ", t)
    t = re.sub(r"\s+", " ", t)
    return t


@st.cache_data
def load_procurement_by_activity(fiscal_year):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        """SELECT budget_activity_title, SUM(amount_thousands) / 1e6 AS billions
           FROM budget_line
           WHERE exhibit_type = 'P1' AND fiscal_year = ? AND amount_type = 'request'
           GROUP BY budget_activity_title""",
        conn, params=(fiscal_year,),
    )
    conn.close()
    df["activity"] = df["budget_activity_title"].map(_normalize_activity)
    grouped = df.groupby("activity", as_index=False)["billions"].sum()
    return grouped[grouped.billions > 0].sort_values("billions", ascending=False)


@st.cache_data
def load_procurement_trend():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        """SELECT fiscal_year, budget_activity_title, SUM(amount_thousands) / 1e6 AS billions
           FROM budget_line
           WHERE exhibit_type = 'P1' AND amount_type = 'request'
           GROUP BY fiscal_year, budget_activity_title""",
        conn,
    )
    conn.close()
    df["activity"] = df["budget_activity_title"].map(_normalize_activity)
    return df.groupby(["fiscal_year", "activity"], as_index=False)["billions"].sum()


@st.cache_data
def load_stock_prices():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM stock_price", conn, parse_dates=["date"])
    conn.close()
    return df


budget_df = load_budget_totals()
stock_df = load_stock_prices()

st.title("DoD Budget & Defense-Contractor Dashboard")
st.caption("FY2000-FY2027 DoD Comptroller budget exhibits (P-1/O-1/R-1/RF-1) joined against defense-contractor stock data.")

tab_overall, tab_branch, tab_procurement, tab_stocks, tab_correlation = st.tabs(
    ["Overall Spend", "Spend by Branch", "Procurement Detail", "Company Stocks", "Spend vs. Market Cap"]
)

# preferred amount_type per year: request (this year's ask) is most consistent across vintages
PREFERRED_AMOUNT_TYPE = "request"

with tab_overall:
    st.subheader("Total spend by exhibit type, request-year dollars")
    overall = (
        budget_df[budget_df.amount_type == PREFERRED_AMOUNT_TYPE]
        .groupby(["exhibit_type", "fiscal_year"], as_index=False)["billions"].sum()
    )
    overall["exhibit_type"] = overall["exhibit_type"].map(EXHIBIT_LABELS)
    pivot = overall.pivot(index="fiscal_year", columns="exhibit_type", values="billions").sort_index()
    st.line_chart(pivot)
    st.caption("Units: billions of dollars (then-year, request amount as submitted that budget cycle).")

with tab_branch:
    st.subheader("Spend by branch of military")
    exhibit_choice = st.selectbox("Exhibit", list(EXHIBIT_LABELS.keys()), format_func=lambda k: EXHIBIT_LABELS[k])
    branch_df = (
        budget_df[(budget_df.amount_type == PREFERRED_AMOUNT_TYPE) & (budget_df.exhibit_type == exhibit_choice)]
        .groupby(["branch", "fiscal_year"], as_index=False)["billions"].sum()
    )
    pivot_b = branch_df.pivot(index="fiscal_year", columns="branch", values="billions").sort_index()
    st.line_chart(pivot_b)
    st.caption("Units: billions of dollars, request amount.")

with tab_procurement:
    st.subheader("Procurement (P-1) breakdown by budget activity")
    years = sorted(budget_df[budget_df.exhibit_type == "P1"].fiscal_year.unique(), reverse=True)
    year_choice = st.selectbox("Fiscal year (request amount)", years)

    by_activity = load_procurement_by_activity(int(year_choice))
    top_n = by_activity.head(15)

    col_pie, col_bar = st.columns(2)
    with col_pie:
        pie = (
            alt.Chart(top_n)
            .mark_arc()
            .encode(
                theta=alt.Theta("billions:Q"),
                color=alt.Color("activity:N", legend=alt.Legend(title="Budget activity", symbolLimit=15)),
                tooltip=["activity", alt.Tooltip("billions:Q", title="$B", format=".1f")],
            )
            .properties(height=420)
        )
        st.altair_chart(pie, width="stretch")
    with col_bar:
        st.bar_chart(top_n.set_index("activity")["billions"], height=420)
    st.caption(f"Top {len(top_n)} budget activities by FY{year_choice} request $ (billions), summed across branches. "
               "Category names are case/wording-normalized across vintages, but a few finer abbreviation "
               "differences may still appear as separate slices.")

    st.markdown("#### Trend over time by budget activity")
    trend = load_procurement_trend()
    all_activities = sorted(a for a in trend.activity.dropna().unique())
    default_activities = (
        trend.groupby("activity")["billions"].sum().sort_values(ascending=False).head(6).index.tolist()
    )
    chosen_activities = st.multiselect("Budget activities to trend", all_activities, default=default_activities)
    if chosen_activities:
        trend_pivot = (
            trend[trend.activity.isin(chosen_activities)]
            .pivot(index="fiscal_year", columns="activity", values="billions")
            .sort_index()
        )
        st.line_chart(trend_pivot)
    st.caption("Units: billions of dollars, request amount, summed across branches.")

    st.markdown("#### Detail by branch")
    detail = load_procurement_detail(int(year_choice))
    st.dataframe(detail, width="stretch")
    st.caption("Units: billions of dollars.")

@st.cache_data
def monthly_market_cap_pivot(chosen_companies):
    """Daily history for some tickers (Boeing, GE, Honeywell) runs back to 1962
    -- plotting several companies' full daily series at once sends tens of
    thousands of raw points to the browser chart and visibly lags. Resampling
    to month-end values cuts that by ~20x with no real loss of visual trend,
    and caching keyed on the company selection avoids redoing this work on
    every unrelated widget interaction elsewhere in the app (Streamlit reruns
    the whole script top-to-bottom on any rerun)."""
    sub = stock_df.loc[stock_df.company.isin(chosen_companies), ["date", "company", "market_cap_usd"]]
    monthly = (
        sub.set_index("date").groupby("company")["market_cap_usd"].resample("ME").last().reset_index()
    )
    return monthly.pivot(index="date", columns="company", values="market_cap_usd") / 1e9


with tab_stocks:
    st.subheader("Defense-contractor market cap over time")
    if stock_df.empty:
        st.info("No stock data available yet (the yfinance fetch may have failed on startup). "
                "Budget dashboards above are unaffected.")
    else:
        companies = sorted(stock_df.company.unique())
        chosen = st.multiselect("Companies", companies, default=["Lockheed Martin", "Boeing", "General Dynamics", "HII"])
        if chosen:
            pivot_mc = monthly_market_cap_pivot(tuple(sorted(chosen)))
            st.line_chart(pivot_mc)
        st.caption("Shown as monthly values (not daily) to keep the chart responsive -- some tickers have "
                    "60+ years of daily history. Units: billions of USD. Note: a few small/thinly-traded tickers "
                    "(e.g. Vision Marine Technologies) "
                   "show unreliable market-cap history because yfinance only exposes current shares-outstanding, which "
                   "gets misapplied to pre-split prices -- treat outliers with suspicion rather than as ground truth.")

with tab_correlation:
    st.subheader("Sub-industry spend vs. combined market cap")
    if stock_df.empty:
        st.info("No stock data available yet (the yfinance fetch may have failed on startup).")
        st.stop()
    sub_industry = st.radio("Sub-industry", ["Aviation", "Shipbuilding"], horizontal=True)
    branch_for_industry = "Air Force" if sub_industry == "Aviation" else "Navy"
    spend = (
        budget_df[(budget_df.amount_type == PREFERRED_AMOUNT_TYPE) & (budget_df.exhibit_type == "P1")
                  & (budget_df.branch == branch_for_industry)]
        .groupby("fiscal_year", as_index=False)["billions"].sum()
        .rename(columns={"billions": f"{branch_for_industry} procurement ($B)"})
    )
    stock_df["year"] = stock_df.date.dt.year
    mcap = (
        stock_df[stock_df.sub_industry == sub_industry]
        .groupby("year", as_index=False)["market_cap_usd"].mean()
    )
    mcap["Combined avg market cap ($B)"] = mcap.market_cap_usd / 1e9
    merged = spend.merge(mcap, left_on="fiscal_year", right_on="year", how="inner")
    st.line_chart(merged.set_index("fiscal_year")[[f"{branch_for_industry} procurement ($B)", "Combined avg market cap ($B)"]])
    if len(merged) > 2:
        corr = merged[f"{branch_for_industry} procurement ($B)"].corr(merged["Combined avg market cap ($B)"])
        st.metric("Correlation (Pearson r)", f"{corr:.2f}")
    st.caption(f"Proxy comparison: {branch_for_industry} procurement (P-1) request $ vs. average market cap of "
               f"{sub_industry} sub-industry companies. This is a branch-level proxy, not a per-contract linkage -- "
               "DoD's P-1/O-1/R-1 exhibits don't attribute spend to specific contractors, only to branch/program.")
