"""DoD budget + defense-contractor stock dashboard -- first pass.

Covers a subset of the full checklist in `DoW data vis project.md`:
  - overall spend trend by exhibit type (P-1/O-1/R-1/RF-1) and by branch
  - procurement breakdown by budget activity for a chosen year
  - defense-contractor market cap over time (from the reused
    defense-dashboard yfinance ingestion)
Run: streamlit run app.py
"""
import os
import sqlite3

import pandas as pd
import streamlit as st

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "dow_budget.sqlite")

EXHIBIT_LABELS = {"P1": "Procurement (P-1)", "O1": "Operations & Maintenance (O-1)",
                   "R1": "RDT&E (R-1)", "RF1": "Working Capital Fund (RF-1)"}


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
    detail = load_procurement_detail(int(year_choice))
    st.bar_chart(detail.set_index("budget_activity_title")["billions"].head(20))
    st.dataframe(detail, use_container_width=True)
    st.caption("Units: billions of dollars.")

with tab_stocks:
    st.subheader("Defense-contractor market cap over time")
    companies = sorted(stock_df.company.unique())
    chosen = st.multiselect("Companies", companies, default=["Lockheed Martin", "Boeing", "General Dynamics", "HII"])
    if chosen:
        sub = stock_df[stock_df.company.isin(chosen)]
        pivot_mc = sub.pivot_table(index="date", columns="company", values="market_cap_usd") / 1e9
        st.line_chart(pivot_mc)
    st.caption("Units: billions of USD. Note: a few small/thinly-traded tickers (e.g. Vision Marine Technologies) "
               "show unreliable market-cap history because yfinance only exposes current shares-outstanding, which "
               "gets misapplied to pre-split prices -- treat outliers with suspicion rather than as ground truth.")

with tab_correlation:
    st.subheader("Sub-industry spend vs. combined market cap")
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
