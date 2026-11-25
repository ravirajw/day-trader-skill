"""
Tests for combine_stocks.py (step 3). Run after combine_stocks.py:
  ../.venv/bin/python test_combine_stocks.py

Working on its own (temporary folders, deleted after the test)
  1. Only "Get NSE 500 stocks list/output/" (a copy of step 2's output) and "Combine stocks list/
     combine_stocks.py": it runs, gives the same two files as ours, writes nothing else, and does not
     change step 2's files.
  2. No Nifty 500 lists: stops with a clear error and writes nothing.
  3. Lists but no rename list (step 2's output/symbol_changes.csv): stops with a clear error.
  4. A rename list that makes a stock lose its symbol on a list date: the built-in check stops the script
     ("Symbol history check FAILED") and nothing is saved.

The output
  5. all_stocks.csv has exactly the stocks of all step 2's lists (by today's symbol), each once, with the
     expected columns.
  6. "In latest list" is exactly the latest list; delisted stocks are included (e.g. JETAIRWAYS: not in
     the latest list).
  7. First month, last month and months in index agree with the month-end lists, for every stock.
  8. Every stock has a symbol history: the newest symbol is the Stock itself and open-ended, and the date
     ranges follow on from each other with no gap or overlap.
  9. Every entry of every list maps back to its stock through the symbol history (symbol on that date).
 10. Known renames: ZOMATO -> ETERNAL (9 Apr 2025), NIITTECH -> COFORGE (20 Aug 2020), LTI -> LTIM -> LTM,
     PHILIPCARB -> PCBL (13 Jan 2022), WABCO-TVS -> WABCOINDIA -> ZFCVINDIA (1 Apr 2022), TELCO ->
     TATAMOTORS -> TMPV; MINDTREE stays its own stock (merged into LTIMindtree, 2022).
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
IN_DIR = os.path.join(HERE, "..", "Get NSE 500 stocks list", "output")
COLUMNS = ["Stock", "Company Name", "ISIN Code", "Industry", "Symbols in index", "First month in index",
           "Last month in index", "Months in index", "In latest list"]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -  {detail}" if detail else ""))


def setup(tmp, lists=True, renames=True):
    """tmp/Get NSE 500 stocks list/output (copy of step 2's) + tmp/Combine stocks list/combine_stocks.py."""
    src = os.path.join(tmp, "Get NSE 500 stocks list", "output")
    os.makedirs(src)
    for f in glob.glob(os.path.join(IN_DIR, "*.csv")):
        name = os.path.basename(f)
        if (name.startswith("nifty500_") and lists) or (name == "symbol_changes.csv" and renames) or \
                name == "removed_from_lists.csv":
            shutil.copy(f, src)
    step = os.path.join(tmp, "Combine stocks list")
    os.makedirs(step)
    shutil.copy(os.path.join(HERE, "combine_stocks.py"), step)
    return src, step


def run(step):
    p = subprocess.run([sys.executable, "-B", "combine_stocks.py"], cwd=step, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def files_under(d):
    return sorted(os.path.relpath(os.path.join(r, f), d) for r, _, fs in os.walk(d) for f in fs)


def same(a, b):
    return os.path.exists(a) and os.path.exists(b) and open(a).read() == open(b).read()


def main():
    # ---------------------------------------------------------------- on its own
    with tempfile.TemporaryDirectory() as tmp:
        src, step = setup(tmp)
        before = {f: open(os.path.join(src, f)).read() for f in os.listdir(src)}
        rc, txt = run(step)
        ok = (rc == 0 and same(os.path.join(step, "output", "all_stocks.csv"), os.path.join(OUT, "all_stocks.csv"))
              and same(os.path.join(step, "output", "symbol_history.csv"), os.path.join(OUT, "symbol_history.csv"))
              and files_under(step) == ["combine_stocks.py", os.path.join("output", "all_stocks.csv"), os.path.join("output", "symbol_history.csv")]
              and {f: open(os.path.join(src, f)).read() for f in os.listdir(src)} == before)
        check("1. only step 2's output/ next to it: same result, nothing else written, step 2's files untouched", ok,
              "" if ok else txt.strip()[-300:])

    with tempfile.TemporaryDirectory() as tmp:
        src, step = setup(tmp, lists=False)
        rc, txt = run(step)
        check("2. no lists: clear error, nothing written", rc != 0 and "No Nifty 500 lists found" in txt
              and not os.path.exists(os.path.join(step, "output")))

    with tempfile.TemporaryDirectory() as tmp:
        src, step = setup(tmp, renames=False)
        rc, txt = run(step)
        check("3. no rename list: clear error, nothing written", rc != 0 and "Rename list not found" in txt
              and not os.path.exists(os.path.join(step, "output")))

    with tempfile.TemporaryDirectory() as tmp:
        src, step = setup(tmp)
        r = pd.read_csv(os.path.join(src, "symbol_changes.csv"), dtype=str)
        bad = pd.DataFrame([{"Old symbol": "RELIANCE", "New symbol": "FAKERELIANCE", "From": "2015-01-01",
                             "Company Name": "test", "Source": "test"}])
        pd.concat([r, bad]).to_csv(os.path.join(src, "symbol_changes.csv"), index=False)
        rc, txt = run(step)
        check("4. a wrong rename is caught by the built-in check, nothing saved", rc != 0 and "Symbol history check FAILED" in txt
              and "RELIANCE" in txt and not os.path.exists(os.path.join(step, "output")))

    # ---------------------------------------------------------------- the output
    S = pd.read_csv(os.path.join(OUT, "all_stocks.csv"), dtype=str).fillna("")
    H = pd.read_csv(os.path.join(OUT, "symbol_history.csv"), dtype=str).fillna("")
    lists = {}
    for f in glob.glob(os.path.join(IN_DIR, "nifty500_*.csv")):
        x = pd.read_csv(f, dtype=str).fillna("")
        if "Symbol today" not in x: x["Symbol today"] = x.Symbol
        x["Symbol today"] = x["Symbol today"].where(x["Symbol today"] != "", x.Symbol)
        lists[os.path.basename(f)[len("nifty500_"):-4]] = x
    want = set().union(*(set(x["Symbol today"]) for x in lists.values()))
    check("5. all_stocks = every stock in the lists, once each, expected columns",
          set(S.Stock) == want and not S.Stock.duplicated().any() and list(S.columns) == COLUMNS,
          f"{len(S)} rows, {len(want)} expected")

    latest = set(lists["latest"].Symbol)
    jet = S[S.Stock == "JETAIRWAYS"]["In latest list"].tolist()
    check("6. 'In latest list' matches the latest list; delisted stocks included",
          set(S.Stock[S["In latest list"] == "Yes"]) == latest and jet == ["No"],
          f"{len(latest)} in latest list, {(S['In latest list'] == 'No').sum()} only in older lists; JETAIRWAYS: {jet}")

    rows = pd.concat([x.assign(M=d[:7]) for d, x in lists.items() if d != "latest"])
    g = rows.groupby("Symbol today").M
    exp = pd.DataFrame({"First": g.min(), "Last": g.max(), "N": g.nunique().astype(str)})
    got = S.set_index("Stock")
    bad = [s for s in exp.index if (got.at[s, "First month in index"], got.at[s, "Last month in index"],
                                    got.at[s, "Months in index"]) != tuple(exp.loc[s])]
    check("7. first/last month and months in index agree with the month-end lists", not bad, f"wrong: {bad[:5]}")

    bad = []
    for st, h in H.groupby("Stock"):
        h = h.sort_values("From")
        if h.Symbol.iloc[-1] != st or h.To.iloc[-1] != "": bad.append((st, "newest symbol")); continue
        for (_, a), (_, b) in zip(h.iloc[:-1].iterrows(), h.iloc[1:].iterrows()):
            if pd.Timestamp(a.To) + pd.Timedelta(days=1) != pd.Timestamp(b.From): bad.append((st, "gap/overlap"))
    check("8. every stock has a continuous symbol history", set(H.Stock) == set(S.Stock) and not bad, str(bad[:5]))

    lo = H.From.replace("", "1900-01-01"); hi = H.To.replace("", "2200-01-01")
    idx = {}
    for sym, st, a, b in zip(H.Symbol, H.Stock, lo, hi):
        idx.setdefault(sym, []).append((a, b, st))
    today = str(pd.Timestamp.today().date())
    miss = []
    for d, x in lists.items():
        day = today if d == "latest" else d
        for sym, st in zip(x.Symbol, x["Symbol today"]):
            if [s for a, b, s in idx.get(sym, []) if a <= day <= b] != [st]: miss.append((d, sym, st))
    check("9. every list entry maps back to its stock by its symbol on that date", not miss,
          f"{sum(len(x) for x in lists.values())} entries; wrong: {miss[:5]}")

    def hist(st): return H[H.Stock == st].sort_values("From")[["Symbol", "From", "To"]].values.tolist()
    exp = {"ETERNAL": [["ZOMATO", "", "2025-04-08"], ["ETERNAL", "2025-04-09", ""]],
           "COFORGE": [["NIITTECH", "", "2020-08-19"], ["COFORGE", "2020-08-20", ""]],
           "LTM": [["LTI", "", "2022-12-04"], ["LTIM", "2022-12-05", "2026-02-26"], ["LTM", "2026-02-27", ""]],
           "PCBL": [["PHILIPCARB", "", "2022-01-12"], ["PCBL", "2022-01-13", ""]],
           "ZFCVINDIA": [["WABCO-TVS", "", "2011-09-05"], ["WABCOINDIA", "2011-09-06", "2022-03-31"], ["ZFCVINDIA", "2022-04-01", ""]],
           "TMPV": [["TELCO", "", "2003-12-25"], ["TATAMOTORS", "2003-12-26", "2025-10-23"], ["TMPV", "2025-10-24", ""]],
           "MINDTREE": [["MINDTREE", "", ""]]}
    wrong = {k: hist(k) for k, v in exp.items() if hist(k) != v}
    check("10. known renames are right (NSE's list and the 3 added in step 2)", not wrong, str(wrong))

    print(f"\n{sum(results)} of {len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
