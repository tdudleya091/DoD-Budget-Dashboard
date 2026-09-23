"""Ingests all DoD Comptroller budget exhibit files in raw_data/ into the SQLite
budget_line fact table. Run: python3 ingest.py
"""
import os
import re
import sys

import pandas as pd

from parsers import RECORD_FIELDS, parse_flat_sheet, parse_legacy_o1_hierarchical
from schema import create_db

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "raw_data")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "dow_budget.sqlite")

# hierarchical (non-flat) O-1 vintages -- everything else uses parse_flat_sheet
HIERARCHICAL_O1_YEARS = {2002, 2004}
HIERARCHICAL_O1_SHEET = "DATA"

NAME_PATTERNS = [
    re.compile(r"^fy(\d{4})_(rf1|o1|p1|r1)\.xlsx?$", re.IGNORECASE),
    re.compile(r"^(rf1|o1|p1|r1)(\d{4})\.xlsx?$", re.IGNORECASE),
]


def classify_filename(fname):
    for pat in NAME_PATTERNS:
        m = pat.match(fname)
        if not m:
            continue
        g1, g2 = m.group(1), m.group(2)
        if g1.isdigit():
            return int(g1), g2.upper()
        return int(g2), g1.upper()
    return None


def main():
    files = sorted(os.listdir(RAW_DIR))
    all_records = []
    skipped = []

    for fname in files:
        classified = classify_filename(fname)
        if classified is None:
            skipped.append(fname)
            continue
        year, exhibit_type = classified
        path = os.path.join(RAW_DIR, fname)

        try:
            if exhibit_type == "O1" and year in HIERARCHICAL_O1_YEARS:
                xl = pd.ExcelFile(path)
                sheet = HIERARCHICAL_O1_SHEET if HIERARCHICAL_O1_SHEET in xl.sheet_names else xl.sheet_names[0]
                filename_years = list(range(year - 2, year + 2)) if year == 2004 else list(range(year - 2, year + 1))
                recs = parse_legacy_o1_hierarchical(path, sheet, fname, filename_years)
            else:
                recs = parse_flat_sheet(path, 0, exhibit_type, fname)
        except Exception as e:
            print(f"FAILED  {fname}: {e}", file=sys.stderr)
            continue

        print(f"OK      {fname}: {len(recs)} records")
        all_records.extend(recs)

    if skipped:
        print(f"\nSkipped (no filename match): {skipped}", file=sys.stderr)

    df = pd.DataFrame(all_records, columns=RECORD_FIELDS)
    print(f"\nTotal records: {len(df)}")

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = create_db(DB_PATH)
    conn.execute("DELETE FROM budget_line")
    df.to_sql("budget_line", conn, if_exists="append", index=False)
    conn.commit()
    print(f"Loaded into {DB_PATH}")

    # sanity check: total dollars by exhibit_type/fiscal_year, most recent years
    summary = pd.read_sql(
        """SELECT exhibit_type, fiscal_year, amount_type, ROUND(SUM(amount_thousands)/1e6, 1) AS billions
           FROM budget_line GROUP BY exhibit_type, fiscal_year, amount_type
           ORDER BY exhibit_type, fiscal_year""",
        conn,
    )
    print(summary.to_string())
    conn.close()


if __name__ == "__main__":
    main()
