"""
Step 3 - One combined list of every stock that was in any Nifty 500 list (as filtered by step 2), and the NSE symbol
each of them traded under on every date (stocks get renamed).

Input : every list in ../Get NSE 500 stocks list/output/ (made by step 2, already filtered to ordinary
        shares): all the dated month-end lists plus nifty500_latest.csv. The script stops with an error if
        there are none. It does NOT run steps 1-2 itself.
        ../Get NSE 500 stocks list/output/symbol_changes.csv - the renames step 2 used (NSE's official
        symbol-change list plus its documented extra renames), so steps 2, 3 and 4 all follow the same
        renames. Only step 2's output/ folder is needed, not its script.

Output: output/all_stocks.csv - one row per stock (delisted stocks included):
          Stock                - its NSE symbol today, or its last symbol if it no longer trades
          Company Name, ISIN Code, Industry  (from the most recent list it is in)
          Symbols in index     - every symbol it had while in the index (more than one = renamed)
          First month in index, Last month in index, Months in index (month-end lists only)
          In latest list       - Yes / No
        output/symbol_history.csv - one row per stock per symbol it has had:
          Stock, Symbol, From, To   (From empty = from the start; To empty = still in use / to the end)
          Used by step 4 to find each stock's rows in NSE's daily price files.

Checks (the script stops if any fails):
  - every stock in every list is found again: its symbol on the list's date leads to the same Stock
  - no symbol belongs to two stocks on the same date

Usage (from this folder):
  ../.venv/bin/python combine_stocks.py
"""
import glob
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "..", "Get NSE 500 stocks list", "output")
OUT_DIR = os.path.join(HERE, "output")
RENAMES = os.path.join(IN_DIR, "symbol_changes.csv")
DAY = pd.Timedelta(days=1)


def read_lists():
    files = sorted(glob.glob(os.path.join(IN_DIR, "nifty500_*.csv")))
    if not files:
        sys.exit(f"No Nifty 500 lists found in {os.path.normpath(IN_DIR)}\n"
                 "Run steps 1-2 first (get_month_ends.py, then get_nifty500.py --month-ends and get_nifty500.py).")
    today = pd.Timestamp.today().normalize()
    rows = []
    for f in files:
        label = os.path.basename(f)[len("nifty500_"):-4]
        x = pd.read_csv(f, dtype=str).fillna("")
        if "Symbol today" not in x: x["Symbol today"] = x.Symbol
        x["Symbol today"] = x["Symbol today"].where(x["Symbol today"] != "", x.Symbol)
        x["Date"] = today if label == "latest" else pd.Timestamp(label)
        x["Latest"] = label == "latest"
        rows.append(x[["Symbol", "Symbol today", "Company Name", "Industry", "ISIN Code", "Date", "Latest"]])
    return files, pd.concat(rows, ignore_index=True)


def load_renames():
    """{old symbol: [(date, new symbol), ...]} from the renames step 2 used (its output/symbol_changes.csv)."""
    if not os.path.exists(RENAMES):
        sys.exit(f"Rename list not found: {os.path.normpath(RENAMES)}\n"
                 "Run step 2 again (get_nifty500.py) - it saves the renames it used there.")
    df = pd.read_csv(RENAMES, dtype=str).fillna("")
    ren = {}
    for r in df.sort_values("From").itertuples():
        ren.setdefault(r._1, []).append((pd.Timestamp(r.From), r._2))
    return ren


def symbol_history(stock, ren, back):
    """[(symbol, from, to)] for one stock, newest first, by walking its renames backwards from today's
    symbol. A rename on date F means the new symbol is used from F; the old one up to the day before."""
    segs, cur, to = [], stock, None
    for _ in range(20):
        prev = [(f, o) for f, o in back.get(cur, []) if to is None or f <= to]
        if not prev:
            # the oldest symbol: it starts after any rename that took this symbol away from another company
            freed = [f for f, n in ren.get(cur, []) if to is None or f <= to]
            segs.append((cur, max(freed) if freed else None, to))
            break
        f, o = max(prev)
        segs.append((cur, f, to))
        cur, to = o, f - DAY
    return segs


def main():
    files, L = read_lists()
    ren = load_renames()
    back = {}
    for old, lst in ren.items():
        for f, new in lst:
            back.setdefault(new, []).append((f, old))

    dated = L[~L.Latest]
    L = L.sort_values("Date")
    g = L.groupby("Symbol today")
    gd = dated.groupby("Symbol today")
    month = dated.assign(M=dated.Date.dt.strftime("%Y-%m")).groupby("Symbol today").M
    S = pd.DataFrame({
        "Company Name": g["Company Name"].last(),
        "ISIN Code": g["ISIN Code"].agg(lambda v: next((x for x in v[::-1] if x), "")),
        "Industry": g.Industry.last(),
        "Symbols in index": g.Symbol.agg(lambda v: ", ".join(dict.fromkeys(v))),
        "First month in index": month.min(), "Last month in index": month.max(), "Months in index": month.nunique(),
        "In latest list": g.Latest.any().map({True: "Yes", False: "No"}),
    })
    S.index.name = "Stock"
    S = S.reset_index()
    S["Months in index"] = S["Months in index"].fillna(0).astype(int)
    S[["First month in index", "Last month in index"]] = S[["First month in index", "Last month in index"]].fillna("")

    H = pd.DataFrame([(s, sym, f, t) for s in S.Stock for sym, f, t in symbol_history(s, ren, back)],
                     columns=["Stock", "Symbol", "From", "To"]).sort_values(["Stock", "From"], na_position="first")

    # check 1: every list entry maps back to its stock
    lo = H.From.fillna(pd.Timestamp("1900-01-01")); hi = H.To.fillna(pd.Timestamp("2200-01-01"))
    idx = {}
    for sym, st, a, b in zip(H.Symbol, H.Stock, lo, hi):
        idx.setdefault(sym, []).append((a, b, st))
    bad = []
    for r in L.drop_duplicates(["Symbol", "Symbol today", "Date"]).itertuples():
        hits = [st for a, b, st in idx.get(r.Symbol, []) if a <= r.Date <= b]
        if hits != [r._2]: bad.append((r.Date.date(), r.Symbol, r._2, hits))
    if bad:
        sys.exit("Symbol history check FAILED (date, symbol in list, stock, found):\n" + "\n".join(map(str, bad[:30])))
    # check 2: no symbol used by two stocks at the same time
    clash = []
    for sym, segs in idx.items():
        segs = sorted(segs)
        for (a1, b1, s1), (a2, b2, s2) in zip(segs, segs[1:]):
            if a2 <= b1: clash.append((sym, s1, s2))
    if clash:
        sys.exit("Symbol used by two stocks at the same time:\n" + "\n".join(map(str, clash[:30])))

    os.makedirs(OUT_DIR, exist_ok=True)
    S.to_csv(os.path.join(OUT_DIR, "all_stocks.csv"), index=False)
    out = H.copy()
    for c in ("From", "To"): out[c] = out[c].dt.strftime("%Y-%m-%d").fillna("")
    out.to_csv(os.path.join(OUT_DIR, "symbol_history.csv"), index=False)

    n_dated = L[~L.Latest].Date.nunique()
    renamed = (H.groupby("Stock").size() > 1).sum()
    print(f"Read {len(files)} lists ({n_dated} month ends + latest): {len(S)} different stocks")
    print(f"  in the latest list: {(S['In latest list'] == 'Yes').sum()}   only in older lists: {(S['In latest list'] == 'No').sum()}")
    print(f"  stocks that changed symbol at some time: {renamed}")
    print("Checks passed: every list entry maps back to its stock; no symbol is used by two stocks at once.")
    print(f"Saved: {os.path.join(OUT_DIR, 'all_stocks.csv')}\n       {os.path.join(OUT_DIR, 'symbol_history.csv')}")


if __name__ == "__main__":
    main()
