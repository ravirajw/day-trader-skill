"""
Step 3a - Every stock that was in the (filtered) Nifty 500 at any month end in a period, and whether it
was renamed, is delisted, and whether Yahoo has its prices. The delisted ones are then fetched from NSE.

Input : EVERY list in ../Filter NSE 500 stocks list/output/ (made by steps 1 and 2): all the dated
        month-end lists plus nifty500_filtered_latest.csv (counted as the current month).
        The script stops if there are none. No options.
        NSE's list of stocks listed today (EQUITY_L.csv, downloaded; a saved copy is used if NSE is down)
        Yahoo Finance (one daily-history check per stock)
Output: output/stocks_<first month>_to_<last month>.csv, one row per stock:
          Symbol today       - its NSE symbol today (or its last NSE symbol, if delisted)
          Symbols in index   - every symbol it had while in the index (more than one = renamed)
          Renamed            - Yes if it changed symbol, or Yahoo keeps it under another symbol
          Company Name, ISIN Code
          First / Last month in index, Months in index
          Listed on NSE today - Yes / No (No = delisted, or merged, or suspended)
          Yahoo symbol, Yahoo first day, Yahoo last day
          Status             - Trading, prices on Yahoo
                               Delisted, no prices on Yahoo     <- to fetch from NSE
                               Delisted, prices on Yahoo        (suspended stocks Yahoo still shows)
                               Listed, no prices on Yahoo        <- to fetch from NSE
                               "prices on Yahoo" only if Yahoo's history starts by the month it joined the index
          Yahoo starts late  - Yes if Yahoo's history starts after the stock joined the index

Usage (from this folder):
  ../.venv/bin/python find_delisted_stocks.py
"""
import glob
import io
import logging
import os
import sys
import time

import pandas as pd
import requests
import yfinance as yf

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "..", "Filter NSE 500 stocks list", "output")
OUT_DIR = os.path.join(HERE, "output")
DEP_DIR = os.path.join(HERE, "dependencies")
EQ_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
EQ_FILE = os.path.join(DEP_DIR, "nse_equity_list.csv")
HEADERS = {"User-Agent": "Mozilla/5.0"}
BATCH = 80
sys.path.insert(0, os.path.join(HERE, "..", "Get historical prices"))
sys.dont_write_bytecode = True                   # no __pycache__ in the prices folder
from get_historical_prices import YAHOO_SYMBOL   # same Yahoo symbol fixes as the price download


def listed_today():
    os.makedirs(DEP_DIR, exist_ok=True)
    try:
        r = requests.get(EQ_URL, headers=HEADERS, timeout=30); r.raise_for_status()
        eq = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")))
        eq.to_csv(EQ_FILE, index=False)
    except Exception as e:
        if not os.path.exists(EQ_FILE): sys.exit(f"Could not download NSE's equity list ({e}) and no saved copy exists.")
        print(f"  NSE's equity list could not be downloaded ({e}); using the saved copy")
        eq = pd.read_csv(EQ_FILE)
    eq.columns = [c.strip() for c in eq.columns]
    return set(eq.SYMBOL.astype(str).str.strip())


def yahoo_range(symbols):
    """{symbol: (first day, last day)} for symbols Yahoo has daily prices for."""
    out = {}
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        for attempt in range(3):
            try:
                df = yf.download([s + ".NS" for s in chunk], period="max", interval="1d", auto_adjust=False,
                                 actions=False, group_by="ticker", threads=True, progress=False); break
            except Exception:
                time.sleep(10)
        for s in chunk:
            try:
                c = df[s + ".NS"]["Close"].dropna()
                if len(c): out[s] = (c.index.min().date(), c.index.max().date())
            except Exception:
                pass
        print(f"  Yahoo checked {min(i + BATCH, len(symbols))}/{len(symbols)}", end="\r", flush=True)
    print()
    return out


def main():
    files = []
    for f in sorted(glob.glob(os.path.join(IN_DIR, "nifty500_filtered_*.csv"))):
        d = os.path.basename(f)[len("nifty500_filtered_"):-4]
        files.append((str(pd.Timestamp.today().date()) if d == "latest" else d, f))
    files.sort()
    if not files:
        sys.exit(f"No filtered Nifty 500 lists in {os.path.normpath(IN_DIR)}.\n"
                 "Run steps 1 and 2 first (get_nifty500.py, then filter_nifty500.py).")
    rows = []
    for d, f in files:
        x = pd.read_csv(f, dtype=str).fillna("")
        if "Symbol today" not in x: x["Symbol today"] = x.Symbol
        x["Symbol today"] = x["Symbol today"].where(x["Symbol today"] != "", x.Symbol)
        x["Month"] = d[:7]
        rows.append(x[["Symbol", "Symbol today", "Company Name", "ISIN Code", "Month"]])
    allm = pd.concat(rows, ignore_index=True)
    print(f"{len(files)} lists ({files[0][0]} to {files[-1][0]}): {allm['Symbol today'].nunique()} different stocks")

    g = allm.sort_values("Month").groupby("Symbol today")
    s = pd.DataFrame({
        "Symbols in index": g.Symbol.agg(lambda v: ", ".join(dict.fromkeys(v))),
        "Company Name": g["Company Name"].last(), "ISIN Code": g["ISIN Code"].agg(lambda v: next((x for x in v[::-1] if x), "")),
        "First month in index": g.Month.min(), "Last month in index": g.Month.max(), "Months in index": g.Month.nunique(),
    }).reset_index()
    s["Yahoo symbol"] = s["Symbol today"].replace(YAHOO_SYMBOL)
    s["Renamed"] = ["Yes" if ("," in syms or y != t or syms != t) else "" for syms, t, y in
                    zip(s["Symbols in index"], s["Symbol today"], s["Yahoo symbol"])]

    print("Checking NSE's list of stocks listed today...")
    listed = listed_today()
    s["Listed on NSE today"] = ["Yes" if t in listed or y in listed else "No" for t, y in zip(s["Symbol today"], s["Yahoo symbol"])]
    print("Checking which stocks Yahoo has prices for...")
    yr = yahoo_range(sorted(s["Yahoo symbol"].unique()))
    s["Yahoo first day"] = s["Yahoo symbol"].map(lambda x: yr.get(x, ("", ""))[0])
    s["Yahoo last day"] = s["Yahoo symbol"].map(lambda x: yr.get(x, ("", ""))[1])
    # Yahoo's prices count only if they cover the stock's time in the index (Yahoo sometimes keeps
    # just a few days of a delisted stock)
    has = (s["Yahoo first day"] != "") & (s["Yahoo first day"].astype(str).str[:7] <= s["First month in index"])
    s["Status"] = [("Trading, prices on Yahoo" if l == "Yes" and h else "Listed, no prices on Yahoo" if l == "Yes"
                    else "Delisted, prices on Yahoo" if h else "Delisted, no prices on Yahoo")
                   for l, h in zip(s["Listed on NSE today"], has)]
    s["Yahoo starts late"] = ["Yes" if f != "" and str(f)[:7] > m else "" for f, m in
                              zip(s["Yahoo first day"], s["First month in index"])]

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"stocks_{allm.Month.min()}_to_{allm.Month.max()}.csv")
    s = s.sort_values(["Status", "Symbol today"])
    s.to_csv(out, index=False)
    print(f"\nSaved: {out}")
    print(s.Status.value_counts().to_string())
    print(f"Renamed: {(s.Renamed == 'Yes').sum()}   Yahoo history starts after the stock joined the index: {(s['Yahoo starts late'] == 'Yes').sum()}")


if __name__ == "__main__":
    main()
