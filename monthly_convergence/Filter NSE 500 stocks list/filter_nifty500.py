"""
Step 2 - Filter the Nifty 500 list: keep only ordinary traded shares.

Input : the list made by step 1, in ../Get NSE 500 stocks list/output/
          nifty500_latest.csv        (default)
          nifty500_<YYYY-MM-DD>.csv  (with --date)
        If that list does not exist the script stops with an error. It does NOT create it:
        run step 1 first (get_nifty500.py, with the same --date).
Output: output/nifty500_filtered_latest.csv  or  output/nifty500_filtered_<YYYY-MM-DD>.csv
        (same columns as the input list), and the removed entries with the reason are printed.

What is removed (not ordinary shares, so not traded like a stock):
  1. REITs and InvITs (units, not shares), e.g. EMBASSY, BIRET, BAGMANE. Found from the ISIN:
     an Indian ISIN is INE + 4-character issuer code + 2-digit security type + ...; type "01" is
     equity shares. NSE's series "RR" (REIT) or "IV" (InvIT) is also used when the list has it.
  2. NSE placeholders: symbols starting with DUMMY (used for a few days during a demerger).

What is NOT removed: stocks that have since been delisted, merged or renamed. On the list's date
they were real, traded index stocks, so a backtest must keep them (dropping them would bias it).
A stock with no ISIN in the list is kept (older lists miss a few ISINs) and is named in the summary.

Usage (from this folder):
  ../.venv/bin/python filter_nifty500.py                    # latest list
  ../.venv/bin/python filter_nifty500.py --date 2011-01-31  # list on that date
"""
import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "..", "Get NSE 500 stocks list", "output")
OUT_DIR = os.path.join(HERE, "output")
NON_SHARE_SERIES = {"RR": "REIT", "IV": "InvIT"}


def reason(row):
    sym, isin = str(row["Symbol"]), str(row.get("ISIN Code", "") or "").strip()
    series = str(row.get("Series", "") or "").strip()
    if sym.upper().startswith("DUMMY"):
        return "NSE placeholder (DUMMY symbol), not a stock"
    if series in NON_SHARE_SERIES:
        return f"{NON_SHARE_SERIES[series]} units (series {series}), not shares"
    if isin.startswith("IN") and len(isin) == 12 and isin[7:9] != "01":
        return f"not equity shares (ISIN {isin}: security type {isin[7:9]}, shares are 01), e.g. REIT/InvIT units"
    return ""


def main(a):
    label = a.date or "latest"
    src = os.path.join(IN_DIR, f"nifty500_{label}.csv")
    if not os.path.exists(src):
        sys.exit(f"Nifty 500 list not found: {os.path.normpath(src)}\n"
                 f"Run step 1 first: get_nifty500.py" + (f" --date {a.date}" if a.date else ""))
    df = pd.read_csv(src, dtype=str).fillna("")
    df["Reason"] = df.apply(reason, axis=1)
    keep, drop = df[df.Reason == ""].drop(columns="Reason"), df[df.Reason != ""]
    no_isin = keep[keep.get("ISIN Code", pd.Series("", index=keep.index)) == ""].Symbol.tolist()

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"nifty500_filtered_{label}.csv")
    keep.to_csv(out, index=False)

    print(f"Nifty 500 list ({label}): {len(df)}   kept: {len(keep)}   removed: {len(drop)}")
    print(f"Input:  {os.path.normpath(src)}")
    print(f"Saved:  {out}")
    if len(drop):
        print("\nRemoved:")
        print(drop[["Symbol", "Company Name", "Reason"]].to_string(index=False))
    if no_isin:
        print(f"\nKept, but no ISIN in the list to check (older list): {', '.join(no_isin)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Keep only ordinary traded shares in a Nifty 500 list")
    ap.add_argument("--date", help="YYYY-MM-DD: filter the list for this date (default: latest list)")
    a = ap.parse_args()
    if a.date:
        try: a.date = str(pd.Timestamp(a.date).date())
        except ValueError: sys.exit(f"Not a date: {a.date} (use YYYY-MM-DD)")
    main(a)
