"""
Step 2 - Filter every Nifty 500 list made by step 1: keep only ordinary traded shares.

No options. It filters EVERY list in ../Get NSE 500 stocks list/output/:
  nifty500_latest.csv         -> output/nifty500_filtered_latest.csv
  nifty500_<YYYY-MM-DD>.csv   -> output/nifty500_filtered_<YYYY-MM-DD>.csv
If there are no lists the script stops with an error. It does NOT create them: run step 1 first
(get_nifty500.py). Each run filters all lists again (it takes a few seconds), so the outputs always
match the current lists.

What is removed (not ordinary shares, so not traded like a stock):
  1. REITs and InvITs (units, not shares), e.g. EMBASSY, BIRET, BAGMANE. Found from the ISIN:
     an Indian ISIN is INE + 4-character issuer code + 2-digit security type + ...; type "01" is
     equity shares. NSE's series "RR" (REIT) or "IV" (InvIT) is also used when the list has it.
  2. NSE placeholders: symbols starting with DUMMY (used for a few days during a demerger).

What is NOT removed: stocks that have since been delisted, merged or renamed. On the list's date
they were real, traded index stocks, so a backtest must keep them (dropping them would bias it).
A stock with no ISIN in the list is kept (older lists miss a few ISINs).

Usage (from this folder):
  ../.venv/bin/python filter_nifty500.py
"""
import glob
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
    if series in NON_SHARE_SERIES or (isin.startswith("IN") and len(isin) == 12 and isin[7:9] != "01"):
        return "REIT/InvIT units, not shares"
    return ""


def main():
    files = sorted(glob.glob(os.path.join(IN_DIR, "nifty500_*.csv")))
    if not files:
        sys.exit(f"No Nifty 500 lists found in {os.path.normpath(IN_DIR)}\nRun step 1 first: get_nifty500.py")
    os.makedirs(OUT_DIR, exist_ok=True)
    removed = {}          # (symbol, reason) -> lists it was removed from
    for f in files:
        label = os.path.basename(f)[len("nifty500_"):-4]
        df = pd.read_csv(f, dtype=str).fillna("")
        df["Reason"] = df.apply(reason, axis=1)
        df[df.Reason == ""].drop(columns="Reason").to_csv(os.path.join(OUT_DIR, f"nifty500_filtered_{label}.csv"), index=False)
        for r in df[df.Reason != ""].itertuples():
            removed.setdefault((r.Symbol, r.Reason), []).append(label)

    labels = [os.path.basename(f)[len("nifty500_"):-4] for f in files]
    dated = sorted(l for l in labels if l != "latest")
    print(f"Filtered {len(files)} lists" + (f" ({dated[0]} to {dated[-1]}" + (", plus latest)" if "latest" in labels else ")") if dated else "")
          + f"\nSaved to {OUT_DIR}")
    if not removed:
        print("Nothing removed: every entry is an ordinary share.")
        return
    print("\nRemoved (symbol, reason, lists):")
    for (sym, why), ls in sorted(removed.items()):
        print(f"  {sym:<12} {why}   in {len(ls)} list(s): {ls[0]}" + (f" to {ls[-1]}" if len(ls) > 1 else ""))


if __name__ == "__main__":
    main()
