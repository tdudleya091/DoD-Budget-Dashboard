"""
tickers.py
----------
Defines the universe of US defense-industry stocks used by this project,
split into the two sub-industries named in the checklist: Aviation and
Shipbuilding. Vendored from the sibling defense-dashboard project (same
ticker choices/rationale) so this repo has no cross-repo dependency and
works standalone on a fresh deploy (e.g. Streamlit Community Cloud).
"""

# --- Aviation sub-industry -------------------------------------------------

AVIATION_TICKERS = {
    "Boeing":      "BA",
    "Textron":     "TXT",
    "Northrop Grumman": "NOC",
    "RTX":         "RTX",
    "Honeywell":   "HON",
    "Lockheed Martin": "LMT",
    "GE Aviation": "GE",     # GE Aerospace -- the surviving entity after the GE breakup
    "Astronics":   "ATRO",
}

# --- Shipbuilding sub-industry ----------------------------------------------

SHIPBUILDING_TICKERS = {
    "General Dynamics": "GD",
    "HII":               "HII",     # Huntington Ingalls Industries
    "BAE Systems":       "BAESY",   # USD OTC ADR
    "L3Harris":          "LHX",
    # Austal USA is a subsidiary of Austal Limited, primary-listed on the
    # ASX (no direct US listing for the shipyard itself). Try the thin USD
    # OTC ADR first, fall back to the AUD-denominated ASX listing.
    "Austal":            "AUTLY",
    "Vision Marine Technologies": "VMAR",  # "Vision Tech" per user
    "Kirby Corporation": "KEX",
}

# TICKER_CANDIDATES: fallback preference order for tickers that might not
# resolve on the primary attempt. data.resolve_best_ticker() walks these in
# order and keeps the first one that returns usable price history.
TICKER_CANDIDATES = {
    "Austal": [("AUTLY", "USD"), ("ASB.AX", "AUD")],  # ADR first, ASX fallback
}

# CURRENCY_MAP: company name -> currency of its primary ticker. Companies in
# TICKER_CANDIDATES get their currency from resolve_best_ticker() instead.
CURRENCY_MAP = {
    "Boeing": "USD", "Textron": "USD", "Northrop Grumman": "USD", "RTX": "USD",
    "Honeywell": "USD", "Lockheed Martin": "USD", "GE Aviation": "USD", "Astronics": "USD",
    "General Dynamics": "USD", "HII": "USD", "BAE Systems": "USD", "L3Harris": "USD",
    "Vision Marine Technologies": "USD", "Kirby Corporation": "USD",
}

TICKERS = {**AVIATION_TICKERS, **SHIPBUILDING_TICKERS}

AVIATION = list(AVIATION_TICKERS.keys())
SHIPBUILDING = list(SHIPBUILDING_TICKERS.keys())
ALL_COMPANIES = AVIATION + SHIPBUILDING

# SUB_INDUSTRY_MAP: company name -> "Aviation" or "Shipbuilding".
SUB_INDUSTRY_MAP = {
    **{n: "Aviation" for n in AVIATION},
    **{n: "Shipbuilding" for n in SHIPBUILDING},
}

# --- Benchmark indices -------------------------------------------------------

INDICES = {
    "Dow Jones Industrial Average": "^DJI",
    "NASDAQ Composite":             "^IXIC",
}
