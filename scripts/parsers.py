"""Parsers for DoD Comptroller budget exhibit files (P-1 procurement, O-1 operations,
R-1 RDT&E, RF-1 working capital fund), FY2002-FY2027 vintages.

Two structural families exist in the raw files:
  - "flat" tabular: one row per line item, with FY-labeled Qty/Amt columns
    (this covers P-1 and R-1 for every vintage 2002-2027, O-1 for 2006+, RF-1 for
    every vintage it exists in, 2012+).
  - "hierarchical" (O-1 only, FY2002 and FY2004): rows are a mix of subtotal/header
    rows and leaf line-item rows, indentation implied by which column holds text,
    needs a stateful walker.

Both parsers emit the same long-format record shape (see RECORD_FIELDS below), which
ingest.py loads into the `budget_line` SQLite table.
"""
import re
import pandas as pd

RECORD_FIELDS = [
    "source_file", "exhibit_type", "fiscal_year", "amount_type", "branch",
    "account_code", "account_title", "budget_activity_code", "budget_activity_title",
    "subactivity_code", "subactivity_title", "line_item_code", "line_item_title",
    "cost_type_code", "cost_type_title", "classification", "quantity", "amount_thousands",
]

BRANCH_CODE_MAP = {"A": "Army", "N": "Navy", "F": "Air Force", "D": "Defense-Wide"}
BRANCH_WORD_MAP = {
    "ARMY": "Army", "NAVY": "Navy", "MARINE": "Navy", "AF": "Air Force",
    "AIR FORCE": "Air Force",
}


def to_number(v):
    """Coerce a cell value to a number; a handful of legacy sheets store amounts
    as comma-formatted strings (e.g. "45,608") instead of numeric cells."""
    if pd.isna(v):
        return 0
    if isinstance(v, str):
        v = v.replace(",", "").strip()
        if v == "":
            return 0
        try:
            return float(v)
        except ValueError:
            return 0
    return v


def normalize_header(s):
    return re.sub(r"\s+", " ", str(s).replace("\n", " ")).strip().lower()


def branch_from_code(code):
    if code is None or (isinstance(code, float) and pd.isna(code)):
        return None
    code = str(code).strip().upper()
    if code in BRANCH_CODE_MAP:
        return BRANCH_CODE_MAP[code]
    if code in BRANCH_WORD_MAP:
        return BRANCH_WORD_MAP[code]
    return "Defense-Wide"


def branch_from_text(text):
    if not isinstance(text, str):
        return "Defense-Wide"
    t = text.strip().upper()
    if t == "AF" or "AIR FORCE" in t:
        return "Air Force"
    if "ARMY" in t:
        return "Army"
    if "NAVY" in t or "MARINE" in t:
        return "Navy"
    return "Defense-Wide"


FIELD_RULES = [
    ("account_code", lambda h: h in ("account", "appn")),
    ("account_title", lambda h: "account title" in h or "appropriation name" in h or h == "appn name"),
    ("organization", lambda h: h == "organization"),
    ("branch_code", lambda h: h in ("comp", "treasury agency")),
    ("budget_activity_code", lambda h: h in ("ba",) or h == "budget activity"),
    ("budget_activity_title", lambda h: "budget activity" in h and ("name" in h or "title" in h)),
    ("subactivity_code", lambda h: h in ("bsa", "ag/bsa", "ag-bsa", "ag")),
    ("subactivity_title", lambda h: (h == "ag title" or "ag/bsa" in h or "ag-bsa" in h
                                      or (("bsa" in h or "subactivity" in h) and ("name" in h or "title" in h)))),
    ("line_item_code", lambda h: h in ("pe", "program element", "item con nr", "item control nr",
                                        "line item", "sag/bli", "sag-bli", "sag", "budget line item")),
    ("line_item_title", lambda h: (h == "sag title" or "sag/budget line item" in h or "sag-bli" in h
                                    or (("item con" in h or "line item" in h or "program element" in h
                                         or "program elememt" in h)
                                        and ("name" in h or "title" in h)))),
    ("cost_type_code", lambda h: h in ("ct", "cost type") or h == "cost\ntype"),
    ("cost_type_title", lambda h: "cost type" in h and ("name" in h or "title" in h)),
    ("classification", lambda h: h in ("sec", "classification")),
]


def classify_field(header_norm):
    for field, rule in FIELD_RULES:
        if rule(header_norm):
            return field
    return None


AMOUNT_PRIORITY_HIGH = ("total",)
AMOUNT_PRIORITY_PENALTY = ("base", "oco", "division", "supplemental", "annualized", "cr ")


def classify_amount_header(header_norm):
    m = re.search(r"(19|20)\d{2}", header_norm)
    if not m:
        return None
    year = int(m.group(0))
    is_qty = "qty" in header_norm or "quantity" in header_norm
    # O-1/R-1/RF-1 never carry a Quantity column, and some modern-era P-1/O-1
    # vintages label the dollar column just "...Actuals"/"...Total" with no
    # literal "Amt"/"Amount" suffix -- so anything year-bearing that isn't
    # explicitly a quantity column is treated as the dollar amount column
    if "request" in header_norm:
        amount_type = "request"
    elif "enact" in header_norm:
        amount_type = "enacted"
    elif "actual" in header_norm:
        amount_type = "actual"
    else:
        amount_type = "unknown"
    priority = 1 if any(k in header_norm for k in AMOUNT_PRIORITY_HIGH) else 0
    if any(k in header_norm for k in AMOUNT_PRIORITY_PENALTY):
        priority = -1
    return {"year": year, "is_qty": is_qty, "amount_type": amount_type, "priority": priority,
            "raw": header_norm}


def find_header_row(raw, max_scan=10):
    for i in range(min(max_scan, len(raw))):
        v = raw.iloc[i, 0]
        if isinstance(v, str) and v.strip().lower() in ("account", "appn"):
            return i
    return None


def build_combined_headers(raw, header_row):
    row1 = raw.iloc[header_row]
    row0 = raw.iloc[header_row - 1] if header_row > 0 else None
    ff0 = row0.ffill() if row0 is not None else None
    headers = []
    for j in range(raw.shape[1]):
        r1v = row1.iloc[j]
        r1s = "" if pd.isna(r1v) else str(r1v)
        if re.search(r"(19|20)\d{2}", r1s):
            headers.append(r1s)
        else:
            r0s = "" if ff0 is None or pd.isna(ff0.iloc[j]) else str(ff0.iloc[j])
            # a row0 cell that's a long bare number is a leftover "Total of
            # Displayed Rows" grand-total, not a header fragment -- a real
            # forward-fillable year fragment is always exactly 4 digits
            r0_digits = re.sub(r"\.0$", "", r0s)
            if r0_digits.isdigit() and len(r0_digits) != 4:
                r0s = ""
            headers.append((r0s + " " + r1s).strip())
    return headers


def parse_flat_sheet(path, sheet_name, exhibit_type, source_file):
    """Parses flat/tabular sheets: P-1 and R-1 (all vintages), O-1 (2006+), RF-1 (all)."""
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
    header_row = find_header_row(raw)
    if header_row is None:
        raise ValueError(f"no header row found in {path}::{sheet_name}")
    headers = build_combined_headers(raw, header_row)
    data = raw.iloc[header_row + 1:].reset_index(drop=True)
    data.columns = range(data.shape[1])

    field_cols = {}
    amount_cols = {}  # year -> list of {col, is_qty, amount_type, priority}
    for j, h in enumerate(headers):
        hn = normalize_header(h)
        amt = classify_amount_header(hn)
        if amt is not None:
            amount_cols.setdefault(amt["year"], []).append({**amt, "col": j})
            continue
        field = classify_field(hn)
        if field is not None and field not in field_cols:
            field_cols[field] = j

    # for each year, pick the best amount column and best qty column
    year_best = {}
    for year, cols in amount_cols.items():
        amts = [c for c in cols if not c["is_qty"]]
        qtys = [c for c in cols if c["is_qty"]]
        best_amt = max(amts, key=lambda c: c["priority"], default=None)
        best_qty = max(qtys, key=lambda c: c["priority"], default=None)
        year_best[year] = {"amt": best_amt, "qty": best_qty}

    records = []
    account_col = field_cols.get("account_code")
    if account_col is None:
        raise ValueError(f"no account_code column found in {path}::{sheet_name}")
    sorted_years = sorted(year_best)
    min_year = sorted_years[0] if sorted_years else None
    max_year = sorted_years[-1] if sorted_years else None

    title_col = field_cols.get("line_item_title")

    for _, row in data.iterrows():
        acct = row.get(account_col)
        if pd.isna(acct) or str(acct).strip() == "":
            continue
        # rows with no leaf title are department/account subtotal rollups that
        # some vintages (e.g. FY2010 O-1) mix into the otherwise-flat table --
        # skip them so their totals aren't double-counted against real leaf rows
        if title_col is not None and pd.isna(row.get(title_col)):
            continue

        def get(field):
            col = field_cols.get(field)
            if col is None:
                return None
            v = row.get(col)
            return None if pd.isna(v) else v

        branch_code = get("branch_code")
        organization = get("organization")
        branch = branch_from_code(branch_code) if branch_code is not None else branch_from_text(
            organization if isinstance(organization, str) else str(get("account_title") or ""))

        base = {
            "source_file": source_file,
            "exhibit_type": exhibit_type,
            "branch": branch,
            "account_code": str(acct).strip(),
            "account_title": get("account_title"),
            "budget_activity_code": get("budget_activity_code"),
            "budget_activity_title": get("budget_activity_title"),
            "subactivity_code": get("subactivity_code"),
            "subactivity_title": get("subactivity_title"),
            "line_item_code": get("line_item_code"),
            "line_item_title": get("line_item_title"),
            "cost_type_code": get("cost_type_code"),
            "cost_type_title": get("cost_type_title"),
            "classification": get("classification"),
        }

        for year, best in year_best.items():
            amt_info = best["amt"]
            if amt_info is None:
                continue
            amount = to_number(row.get(amt_info["col"]))
            qty = None
            if best["qty"] is not None:
                qv = row.get(best["qty"]["col"])
                qty = None if pd.isna(qv) else to_number(qv)
            amount_type = amt_info["amount_type"]
            if amount_type == "unknown":
                if year == min_year:
                    amount_type = "actual"
                elif year == max_year:
                    amount_type = "request"
                else:
                    amount_type = "enacted"
            rec = dict(base)
            rec["fiscal_year"] = year
            rec["amount_type"] = amount_type
            rec["quantity"] = qty
            rec["amount_thousands"] = amount
            records.append(rec)

    return records


ACCOUNT_CODE_RE = re.compile(r"^\d{4}[A-Za-z]$")


def parse_legacy_o1_hierarchical(path, sheet_name, source_file, filename_years):
    """Stateful walker for FY2002/FY2004 O-1 files, which have no explicit BSA/leaf
    columns -- leaf rows are identified by an account-code-like token (e.g. '2020a')
    followed by a numeric item number and a title, with trailing FY amount columns.
    """
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
    n_cols = raw.shape[1]
    n_years = len(filename_years)
    amount_cols = list(range(n_cols - n_years, n_cols))

    records = []
    current_account_title = None
    current_group_label = None

    for i in range(len(raw)):
        row = raw.iloc[i]
        # find the account-code column and adjacent item-number/title columns;
        # search right-to-left since some vintages repeat the code earlier in the
        # row as part of a redundant hierarchy id (e.g. FY2002's cols 0-3), with
        # the real item-number/title columns following the rightmost occurrence
        acct_col = None
        item_num = None
        title = None
        for j in range(n_cols - 1, -1, -1):
            v = row.iloc[j]
            if not (isinstance(v, str) and ACCOUNT_CODE_RE.match(v.strip())):
                continue
            # candidate leaf row -- validate it actually has a numeric item number
            # and a string title in the next two columns, plus at least one amount;
            # a department/budget-activity header row can coincidentally match the
            # account-code regex (e.g. "1804n") without being a real leaf row
            if j + 2 >= n_cols:
                continue
            cand_item, cand_title = row.iloc[j + 1], row.iloc[j + 2]
            has_amount = any(not pd.isna(row.iloc[c]) for c in amount_cols)
            if pd.isna(cand_item) or not isinstance(cand_title, str) or not cand_title.strip() or not has_amount:
                continue
            acct_col, item_num, title = j, cand_item, cand_title
            break

        if acct_col is None:
            # not a leaf row -- either a pure label row (department/budget-activity
            # header, no amounts) or a group subtotal row (e.g. "LAND FORCES" with
            # its own amount columns filled in). Label position varies by vintage
            # (col0 in FY2004, col5-7 in FY2002), so scan for the first text cell
            # before the amount columns rather than assuming a fixed column.
            text = None
            for j in range(n_cols - n_years):
                v = row.iloc[j]
                if isinstance(v, str) and v.strip() and not ACCOUNT_CODE_RE.match(v.strip()):
                    text = v.strip()
                    break
            if text is None:
                continue
            has_amount = any(not pd.isna(row.iloc[c]) for c in amount_cols)
            if has_amount:
                current_group_label = text
            elif branch_from_text(text) != "Defense-Wide" or "DEPARTMENT OF" in text.upper():
                current_account_title = text
                current_group_label = None
            continue

        branch = branch_from_text(current_account_title)
        base = {
            "source_file": source_file,
            "exhibit_type": "O1",
            "branch": branch,
            "account_code": row.iloc[acct_col].strip(),
            "account_title": current_account_title,
            "budget_activity_code": None,
            "budget_activity_title": current_group_label,
            "subactivity_code": None,
            "subactivity_title": None,
            "line_item_code": str(item_num),
            "line_item_title": title.strip(),
            "cost_type_code": None,
            "cost_type_title": None,
            "classification": None,
        }
        min_year, max_year = filename_years[0], filename_years[-1]
        for year, col in zip(filename_years, amount_cols):
            amount = to_number(row.iloc[col])
            if year == min_year:
                amount_type = "actual"
            elif year == max_year:
                amount_type = "request"
            else:
                amount_type = "enacted"
            rec = dict(base)
            rec["fiscal_year"] = year
            rec["amount_type"] = amount_type
            rec["quantity"] = None
            rec["amount_thousands"] = amount
            records.append(rec)

    return records
