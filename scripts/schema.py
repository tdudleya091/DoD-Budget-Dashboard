import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_line (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    exhibit_type TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    amount_type TEXT NOT NULL,
    branch TEXT,
    account_code TEXT,
    account_title TEXT,
    budget_activity_code TEXT,
    budget_activity_title TEXT,
    subactivity_code TEXT,
    subactivity_title TEXT,
    line_item_code TEXT,
    line_item_title TEXT,
    cost_type_code TEXT,
    cost_type_title TEXT,
    classification TEXT,
    quantity REAL,
    amount_thousands REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_budget_line_year ON budget_line(fiscal_year);
CREATE INDEX IF NOT EXISTS idx_budget_line_exhibit ON budget_line(exhibit_type);
CREATE INDEX IF NOT EXISTS idx_budget_line_branch ON budget_line(branch);

CREATE TABLE IF NOT EXISTS stock_price (
    company TEXT NOT NULL,
    ticker TEXT NOT NULL,
    sub_industry TEXT,
    date TEXT NOT NULL,
    close_usd REAL,
    market_cap_usd REAL,
    pb_ratio REAL,
    pe_ratio REAL,
    PRIMARY KEY (company, date)
);
CREATE INDEX IF NOT EXISTS idx_stock_price_date ON stock_price(date);
CREATE INDEX IF NOT EXISTS idx_stock_price_company ON stock_price(company);
"""


def create_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
