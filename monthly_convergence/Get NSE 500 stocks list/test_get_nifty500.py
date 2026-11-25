"""
Tests for get_nifty500.py (step 2). Run after get_nifty500.py has made its lists (latest and month ends):
  ../.venv/bin/python test_get_nifty500.py
Takes about 6 minutes: test 1 rebuilds everything from scratch in an empty folder.

Working on its own (only get_nifty500.py copied into an empty temporary folder)
  1. Fresh run, --date 2011-01-31: it downloads all it needs (NSE's old list copies, press releases)
     into its own dependencies/ folder, creates output/, and the list equals ours. No PDF is left behind;
     only the text of press releases about the Nifty 500 is kept, and every release read is logged.
  2. A second run downloads no press release again (they are all in the read log) and is quick;
     --date 2016-10-30 equals ours.

The lists
  3. Latest list (no option): equals ours; about 500 stocks, no duplicate symbols, the expected columns.
  4. Past dates (our 190 month-end lists): 495-505 stocks each, no duplicates, every row has a Source and a
     Symbol today. Known facts: 500 stocks on 31 Jan 2011; renames show the symbol of that date
     (PCBL from 13 Jan 2022 and PHILIPCARB before, with Symbol today PCBL; BURGERKING on 31 Jan 2022,
     RBA on 28 Feb 2022).

Options
  5. --dates-file: a file with 2011-01-31, 2016-10-30 and a date not yet come (2099-12-31) builds the
     two lists (equal to ours) and skips the future date with a message; --start/--end limit the months.
  6. --check: every saved copy of NSE's list is rebuilt exactly from the one before it (a result may carry
     a note, e.g. 2022-05-04: NSE's file was saved before NSE updated it for a change effective that day).
  7. Missing files: --dates-file with a file that does not exist, and --month-ends with no step 1 folder
     next to it, both stop with a clear message and make no list.
  8. --month-ends next to step 1's folder: builds the month ends in --start/--end and skips the month end
     that has not come yet (the current month's "Expected" date).

The filter (only ordinary shares are kept)
  9. The rule: a REIT/InvIT (ISIN security type not "01", or series RR/IV) and a DUMMY placeholder are
     removed with their reason; an ordinary share, and a stock with no ISIN, are kept.
 10. Our lists: no REIT/InvIT or DUMMY left in any list; every removal is in removed_from_lists.csv with a
     reason and a list that exists; delisted stocks are kept (e.g. JETAIRWAYS in the 2018-12-31 list).

For the next step
 11. output/symbol_changes.csv holds exactly the renames the script uses (NSE's list plus the 3 added in
     EXTRA_RENAMES, with their dates), so step 3 can follow the same renames without this script.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
STEP1_FILE = os.path.join(HERE, "..", "Get month ends dates", "output", "month_end_dates.csv")
COLUMNS = ["Symbol", "Symbol today", "Company Name", "Industry", "ISIN Code", "Source"]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -  {detail}" if detail else ""), flush=True)


def run(folder, *args):
    t = time.time()
    p = subprocess.run([sys.executable, "-B", "get_nifty500.py", *args], cwd=folder, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr, time.time() - t


def same(a, b):
    return os.path.exists(a) and os.path.exists(b) and open(a).read() == open(b).read()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        s = os.path.join(tmp, "Get NSE 500 stocks list")
        os.makedirs(s)
        shutil.copy(os.path.join(HERE, "get_nifty500.py"), s)

        # 1. fresh run
        print("Fresh run in an empty folder (about 4 minutes)...", flush=True)
        rc, txt, secs = run(s, "--date", "2011-01-31")
        hist = os.path.join(s, "dependencies", "history")
        pdfs = glob.glob(os.path.join(s, "**", "*.pdf"), recursive=True)
        texts = os.listdir(os.path.join(hist, "press_releases")) if os.path.isdir(os.path.join(hist, "press_releases")) else []
        log = pd.read_csv(os.path.join(hist, "press_releases_read.csv"), dtype=str) if os.path.exists(os.path.join(hist, "press_releases_read.csv")) else pd.DataFrame()
        kept = log[log.iloc[:, -1].str.contains("text kept", na=False)] if len(log) else log
        ok = (rc == 0 and same(os.path.join(s, "output", "nifty500_2011-01-31.csv"), os.path.join(OUT, "nifty500_2011-01-31.csv"))
              and not pdfs and texts and all(t.endswith(".txt") for t in texts) and len(texts) == len(kept)
              and len(glob.glob(os.path.join(hist, "archive_copies", "*"))) > 0)
        check("1. fresh run from an empty folder: downloads what it needs, list equals ours, no PDFs kept", ok,
              f"{secs:.0f}s; {len(log)} press releases logged, {len(texts)} texts kept, {len(pdfs)} PDFs left")

        # 2. second run reuses the saved data
        rc, txt, secs = run(s, "--date", "2016-10-30")
        ok = rc == 0 and "new press releases" not in txt and same(os.path.join(s, "output", "nifty500_2016-10-30.csv"),
                                                                  os.path.join(OUT, "nifty500_2016-10-30.csv"))
        check("2. second run downloads no press release again and is quick", ok and secs < 120, f"{secs:.0f}s")

        # 3. latest
        rc, txt, _ = run(s)
        lat = pd.read_csv(os.path.join(s, "output", "nifty500_latest.csv"), dtype=str)
        ok = (rc == 0 and same(os.path.join(s, "output", "nifty500_latest.csv"), os.path.join(OUT, "nifty500_latest.csv"))
              and 490 <= len(lat) <= 510 and not lat.Symbol.duplicated().any()
              and {"Symbol", "Company Name", "Industry", "ISIN Code"} <= set(lat.columns))
        check("3. latest list equals ours, about 500 stocks, no duplicates", ok, f"{len(lat)} stocks")

        # 5. --dates-file (with a future date) and --start/--end
        dates = os.path.join(tmp, "dates.csv")
        pd.DataFrame({"Last trading day": ["2011-01-31", "2016-10-30", "2099-12-31"]}).to_csv(dates, index=False)
        for f in glob.glob(os.path.join(s, "output", "nifty500_20*.csv")): os.remove(f)
        rc, txt, _ = run(s, "--dates-file", dates)
        made = sorted(os.path.basename(f) for f in glob.glob(os.path.join(s, "output", "nifty500_20*.csv")))
        ok1 = (rc == 0 and made == ["nifty500_2011-01-31.csv", "nifty500_2016-10-30.csv"] and "Skipped (not come yet): 2099-12-31" in txt
               and all(same(os.path.join(s, "output", m), os.path.join(OUT, m)) for m in made))
        for f in glob.glob(os.path.join(s, "output", "nifty500_20*.csv")): os.remove(f)
        rc, txt, _ = run(s, "--dates-file", dates, "--start", "2016-01", "--end", "2020-12")
        made2 = sorted(os.path.basename(f) for f in glob.glob(os.path.join(s, "output", "nifty500_20*.csv")))
        check("5. --dates-file builds past dates, skips a future one; --start/--end limit the months",
              ok1 and rc == 0 and made2 == ["nifty500_2016-10-30.csv"], f"made {made}, then {made2}")

        # 6. --check
        rc, txt, _ = run(s, "--check")
        rows = [l for l in txt.splitlines() if re.match(r"^\s*\d{4}-\d{2}-\d{2}\s+\d{4}-\d{2}-\d{2}\s+\d+", l)]
        # a result may carry a note after "exact match" (e.g. 2022-05-04: NSE's file was saved before it was
        # updated for a change effective that day - the rebuild still matches it exactly)
        match = [l for l in rows if re.search(r"\d+\s+exact match", l)]
        ok = rc == 0 and len(rows) >= 15 and len(match) == len(rows)
        check("6. --check: every saved NSE list copy rebuilt exactly", ok, f"{len(match)} of {len(rows)} periods match")

        # 7. missing files
        before = set(os.listdir(os.path.join(s, "output")))
        rc1, txt1, _ = run(s, "--dates-file", os.path.join(tmp, "no_such_file.csv"))
        rc2, txt2, _ = run(s, "--month-ends")
        ok = (rc1 != 0 and "Dates file not found" in txt1 and rc2 != 0 and "Run get_month_ends.py first" in txt2
              and set(os.listdir(os.path.join(s, "output"))) == before)
        check("7. missing dates file / no step 1 folder: clear error, no list made", ok)

        # 8. --month-ends next to step 1
        m = os.path.join(tmp, "Get month ends dates", "output")
        os.makedirs(m)
        shutil.copy(STEP1_FILE, m)
        me = pd.read_csv(STEP1_FILE, dtype=str)
        expected = me[me.Status == "Expected"]["Last trading day"].tolist()
        last2 = me[me.Status == "Confirmed"].Month.iloc[-2:].tolist()
        end = me.Month.iloc[-1]
        for f in glob.glob(os.path.join(s, "output", "nifty500_20*.csv")): os.remove(f)
        rc, txt, _ = run(s, "--month-ends", "--start", last2[0], "--end", end)
        made = sorted(os.path.basename(f) for f in glob.glob(os.path.join(s, "output", "nifty500_20*.csv")))
        want = [f"nifty500_{d}.csv" for d in me[me.Month.isin(last2)]["Last trading day"]]
        future = [d for d in expected if pd.Timestamp(d) > pd.Timestamp.today().normalize()]
        ok = (rc == 0 and made == want and all(same(os.path.join(s, "output", f), os.path.join(OUT, f)) for f in made)
              and all(f"Skipped (not come yet): {d}" in txt for d in future))
        check("8. --month-ends next to step 1: builds the months, skips the one not come yet", ok,
              f"made {made}; skipped {future}")

    # 4. our month-end lists
    files = sorted(glob.glob(os.path.join(OUT, "nifty500_20*.csv")))
    bad = []
    for f in files:
        x = pd.read_csv(f, dtype=str).fillna("")
        if list(x.columns) != COLUMNS or not 495 <= len(x) <= 505 or x.Symbol.duplicated().any() \
                or (x.Source == "").any() or (x["Symbol today"] == "").any():
            bad.append(os.path.basename(f))
    def syms(d): return set(pd.read_csv(os.path.join(OUT, f"nifty500_{d}.csv"), dtype=str).Symbol)
    def today_of(d, sym):
        x = pd.read_csv(os.path.join(OUT, f"nifty500_{d}.csv"), dtype=str)
        return x.loc[x.Symbol == sym, "Symbol today"].tolist()
    facts = {
        "500 stocks on 2011-01-31": len(syms("2011-01-31")) == 500,
        "PCBL on 2022-01-31 (renamed 13 Jan 2022)": "PCBL" in syms("2022-01-31") and "PHILIPCARB" not in syms("2022-01-31"),
        "PHILIPCARB on 2021-12-31, Symbol today PCBL": today_of("2021-12-31", "PHILIPCARB") == ["PCBL"],
        "BURGERKING on 2022-01-31, RBA on 2022-02-28": "BURGERKING" in syms("2022-01-31") and "RBA" in syms("2022-02-28"),
    }
    wrong = [k for k, v in facts.items() if not v]
    check("4. our past-date lists: 495-505 stocks, no duplicates, complete rows; known facts right",
          len(files) >= 190 and not bad and not wrong, f"{len(files)} lists; bad {bad[:5]}; wrong facts {wrong}")

    # 9. the filter rule
    sys.dont_write_bytecode = True
    sys.path.insert(0, HERE)
    import get_nifty500 as g
    sample = pd.DataFrame({
        "Symbol": ["RELIANCE", "EMBASSY", "PGINVIT", "DUMMYHEG", "OLDCO", "NEWREIT"],
        "ISIN Code": ["INE002A01018", "INE041025011", "INE0GGX23010", "DUM545A01024", "", ""],
        "Series": ["EQ", "", "", "", "", "RR"]})
    kept, removed = g.remove_non_shares(sample)
    ok = (list(kept.Symbol) == ["RELIANCE", "OLDCO"]
          and dict(zip(removed.Symbol, removed.Reason)) == {
              "EMBASSY": "REIT/InvIT units, not shares", "PGINVIT": "REIT/InvIT units, not shares",
              "DUMMYHEG": "NSE placeholder (DUMMY symbol), not a stock", "NEWREIT": "REIT/InvIT units, not shares"})
    check("9. filter rule: REIT/InvIT and DUMMY removed with reason; shares and no-ISIN kept", ok,
          f"kept {list(kept.Symbol)}; removed {dict(zip(removed.Symbol, removed.Reason))}")

    # 10. our lists
    left = []
    for f in glob.glob(os.path.join(OUT, "nifty500_*.csv")):
        x = pd.read_csv(f, dtype=str).fillna("")
        if (x.apply(g.why_removed, axis=1) != "").any(): left.append(os.path.basename(f))
    rem = pd.read_csv(os.path.join(OUT, "removed_from_lists.csv"), dtype=str).fillna("")
    lists_ok = all(os.path.exists(os.path.join(OUT, f"nifty500_{l}.csv")) for l in rem.List)
    in_lists = [r for r in rem.itertuples() if r.Symbol in set(pd.read_csv(os.path.join(OUT, f"nifty500_{r.List}.csv"), dtype=str).Symbol)]
    jet = "JETAIRWAYS" in set(pd.read_csv(os.path.join(OUT, "nifty500_2018-12-31.csv"), dtype=str).Symbol)
    ok = (not left and len(rem) > 0 and (rem.Reason != "").all() and lists_ok and not in_lists and jet
          and list(rem.columns) == ["List", "Symbol", "Company Name", "ISIN Code", "Reason"])
    check("10. our lists: no REIT/DUMMY left, removals logged with reasons, delisted stocks kept", ok,
          f"{len(rem)} removals logged ({', '.join(sorted(set(rem.Symbol)))}); lists with non-shares left: {left[:3]}; JETAIRWAYS kept: {jet}")

    # 11. the rename list for step 3
    used = {(o, str(f.date()), n) for o, lst in g.renames().items() for f, n in lst}
    saved = pd.read_csv(os.path.join(OUT, "symbol_changes.csv"), dtype=str).fillna("")
    in_file = set(zip(saved["Old symbol"], saved.From, saved["New symbol"]))
    extras = {(o, d, n) for o, n, d in g.EXTRA_RENAMES}
    check("11. symbol_changes.csv = the renames the script uses, including the 3 added ones", in_file == used and extras <= in_file,
          f"{len(in_file)} renames saved, {len(used)} used; extras saved: {sorted(extras & in_file)}")

    print(f"\n{sum(results)} of {len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
