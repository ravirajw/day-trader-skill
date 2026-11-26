"""
Tests for get_nse_prices.py (step 4). Run after get_nse_prices.py:
  ../.venv/bin/python test_get_nse_prices.py

Our data
  1. Stops with an error when step 3's files are missing.
  2. NSE calendar: every confirmed month end (step 1's dates) is a trading day and the last one of its
     month; NSE's published 2026 holidays are all closed days, except those NSE marks "*" (Diwali
     Laxmi Pujan: a one-hour Muhurat trading session).
  3. Every stock file: the right columns, one row per date in order, only trading days, only series
     EQ/BE/BZ, and each row's symbol is the stock's symbol on that date (step 3's symbol history).
  4. Every stock in every month-end list (from Jan 2011) has an NSE price on that month end, or did not
     trade that day (it has prices before and after it within 30 days: an illiquid/suspended day,
     listed in output/test_no_trade_month_ends.csv). Nothing may be missing for any other reason.
  5. Prices are NSE's, as traded: known official closes - RELIANCE 2,194.70 on 25 Nov 2009 (day before its
     bonus), 2,619.85 on 20 Jul 2023 (demerger day), 3,201.80 on 8 Jul 2024 (first day of NSE's new file
     format); ETERNAL 207.93 on 8 Jul 2024 (under its old symbol ZOMATO).

The saved NSE files
  6. Every trading day in the calendar has its NSE file saved (and nothing else is there: no zip, no extra
     file); each file is NSE's own (its name carries the day); the saved files hold every stock that
     traded, not only ours; and a saved file is byte-for-byte what NSE publishes (one day downloaded again
     and compared).

Later runs (temporary folders; the start date and "today" simulated)
  7. A stock added to step 3's list (TCS) is filled in from the saved files: nothing is downloaded again.
  8. A corrected rename (ZOMATO -> ETERNAL on 9 Apr 2025, first given wrongly) is filled in from the saved
     files: nothing is downloaded again, and the old symbol's days appear.
  9. A stock removed from step 3's list: its file is deleted; the saved NSE files are not touched.
 10. A saved NSE file that has gone missing is downloaded again on the next run (and only that one).
 11. A second run with nothing new downloads no old day again.
"""
import glob
import io
import os
import subprocess
import sys
import tempfile
import zipfile

import pandas as pd
import requests

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import get_nse_prices as g

ROOT = os.path.join(HERE, "..")
MONTH_ENDS = os.path.join(ROOT, "Get month ends dates", "output", "month_end_dates.csv")
LISTS = os.path.join(ROOT, "Get NSE 500 stocks list", "output")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -  {detail}" if detail else ""), flush=True)


def child(patch):
    code = f"import sys; sys.dont_write_bytecode=True; sys.path.insert(0, {HERE!r}); import get_nse_prices as g; {patch}; g.main()"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def temp_patch(d, start, today):
    y, m, dd = (int(x) for x in today.split("-"))
    return (f"g.IN_DIR={d!r}+'/in'; g.DEP_DIR={d!r}+'/dep'; g.STORE_DIR={d!r}+'/dep/nse_daily'; g.OUT_DIR={d!r}+'/out'; "
            f"g.DAILY_DIR={d!r}+'/out/daily'; g.CALENDAR={d!r}+'/dep/cal.csv'; g.REPORT={d!r}+'/out/rep.csv'; "
            f"g.START={start!r}; import datetime; "
            f"g.date=type('D',(datetime.date,),{{'today':classmethod(lambda c: datetime.date({y},{m},{dd}))}})")


def downloads(txt):
    return next((int(l.split(":")[1].split()[0]) for l in txt.splitlines() if l.startswith("Days to download:")), None)


def main():
    # ---------------------------------------------------------------- our data
    with tempfile.TemporaryDirectory() as d:
        rc, out = child(f"g.IN_DIR={d!r}; g.DEP_DIR={d!r}+'/dep'; g.STORE_DIR={d!r}+'/dep/s'; g.DAILY_DIR={d!r}+'/daily'")
        check("1. error when step 3's files are missing", rc != 0 and "Run step 3 first" in out and not os.path.exists(d + "/dep"))

    cal = pd.read_csv(g.CALENDAR, dtype=str).fillna("")
    yes = set(cal.Date[cal["NSE file"] == "Yes"])
    me = pd.read_csv(MONTH_ENDS, dtype=str)
    me = me[me.Status == "Confirmed"]
    ydays = pd.Series(sorted(yes)); last_in_month = set(ydays.groupby(ydays.str[:7]).max())
    bad = [d for d in me["Last trading day"] if d not in yes or d not in last_in_month]
    sys.path.insert(0, os.path.join(ROOT, "Get month ends dates"))
    from get_month_ends import download_holidays        # NSE's current holiday list (not saved anywhere)
    hol = download_holidays()
    open_hol = [d for d, n in zip(hol.Date, hol.Holiday) if d in yes and not n.strip().endswith("*")]
    check("2. calendar agrees with month ends and NSE's holiday list", not bad and not open_hol,
          f"{len(me)} month ends, {len(hol)} holidays; wrong: {bad[:5]} {open_hol[:5]}")

    H = pd.read_csv(os.path.join(g.IN_DIR, "symbol_history.csv"), dtype=str).fillna("")
    H["From"] = H.From.replace("", "1900-01-01"); H["To"] = H.To.replace("", "2200-01-01")
    problems, data = [], {}
    for f in sorted(glob.glob(os.path.join(g.DAILY_DIR, "*.csv"))):
        s = os.path.basename(f)[:-4]
        x = pd.read_csv(f, dtype=str).fillna("")
        data[s] = x
        if list(x.columns) != g.COLS: problems.append((s, "columns")); continue
        if not x.Date.is_monotonic_increasing or x.Date.duplicated().any(): problems.append((s, "dates"))
        if not x.Date.isin(yes).all(): problems.append((s, "non-trading day"))
        if not x.Series.isin(g.SERIES).all(): problems.append((s, "series"))
        ok = pd.Series(False, index=x.index)
        for r in H[H.Stock == s].itertuples():
            ok |= (x.Symbol == r.Symbol) & (x.Date >= r.From) & (x.Date <= r.To)
        if not ok.all(): problems.append((s, f"{(~ok).sum()} rows with the wrong symbol for the date"))
    check("3. every stock file is well formed and uses the right symbol on each date", not problems,
          f"{len(data)} files; problems: {problems[:5]}")

    miss, no_trade, total = [], [], 0
    for f in sorted(glob.glob(os.path.join(LISTS, "nifty500_20*.csv"))):
        day = os.path.basename(f)[len("nifty500_"):-4]
        x = pd.read_csv(f, dtype=str).fillna("")
        total += len(x)
        lo = str((pd.Timestamp(day) - pd.Timedelta(days=30)).date()); hi = str((pd.Timestamp(day) + pd.Timedelta(days=30)).date())
        for sym, st in zip(x.Symbol, x["Symbol today"].where(x["Symbol today"] != "", x.Symbol)):
            d = data.get(st)
            if d is not None and (d.Date == day).any(): continue
            if d is not None and d.Date.between(lo, day).any() and d.Date.between(day, hi).any():
                no_trade.append((day, sym, d.Date[d.Date < day].iloc[-1], d.Date[d.Date > day].iloc[0]))
            else:
                miss.append((day, sym))
    check("4. every index stock has an NSE price on each month end (or did not trade that day)", not miss,
          f"{total - len(miss) - len(no_trade)} of {total} have a price; {len(no_trade)} did not trade that day; missing {len(miss)}: {miss[:15]}")
    pd.DataFrame(no_trade, columns=["Month end", "Symbol", "Last trade before", "Next trade after"]).to_csv(
        os.path.join(HERE, "output", "test_no_trade_month_ends.csv"), index=False)

    known = [("RELIANCE", "2009-11-25", "RELIANCE", 2194.70), ("RELIANCE", "2023-07-20", "RELIANCE", 2619.85),
             ("RELIANCE", "2024-07-08", "RELIANCE", 3201.80), ("ETERNAL", "2024-07-08", "ZOMATO", 207.93)]
    wrong = []
    for st, day, sym, close in known:
        r = data[st][data[st].Date == day]
        if r.empty or r.Symbol.iloc[0] != sym or abs(float(r.Close.iloc[0]) - close) > 0.001:
            wrong.append((st, day, r[["Symbol", "Close"]].values.tolist()))
    check("5. known official closes are right (as traded, old and new file formats, renamed stock)", not wrong, str(wrong))

    # ---------------------------------------------------------------- the saved NSE files
    saved = cal[cal["NSE file"] == "Yes"]
    missing = [p for p in saved["Saved as"] if not os.path.exists(os.path.join(g.STORE_DIR, p))]
    on_disk = {os.path.relpath(f, g.STORE_DIR) for f in glob.glob(os.path.join(g.STORE_DIR, "**", "*"), recursive=True) if os.path.isfile(f)}
    extra = on_disk - set(saved["Saved as"])
    def name_has_day(day, p):
        t = pd.Timestamp(day)
        return f"{t:%Y%m%d}" in p or f"{t:%d}{t.strftime('%b').upper()}{t:%Y}" in p
    misnamed = [(d, p) for d, p in zip(saved.Date, saved["Saved as"]) if not name_has_day(d, p)]
    zips = glob.glob(os.path.join(g.DEP_DIR, "**", "*.zip"), recursive=True)
    sample = pd.read_csv(os.path.join(g.STORE_DIR, saved[saved.Date == "2024-07-08"]["Saved as"].iloc[0]), dtype=str)
    eq = sample[sample.SctySrs.str.strip().isin(g.SERIES)]
    ours_that_day = sum((x.Date == "2024-07-08").any() for x in data.values())
    r = requests.get(g.urls(pd.Timestamp("2024-07-08"))[0], headers=g.HEADERS, timeout=60)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        fresh = z.read(z.namelist()[0])
    same = fresh == open(os.path.join(g.STORE_DIR, saved[saved.Date == "2024-07-08"]["Saved as"].iloc[0]), "rb").read()
    ok = not missing and not extra and not misnamed and not zips and len(eq) > 2 * ours_that_day and same
    check("6. every trading day's NSE file kept, unchanged, all stocks, nothing else saved", ok,
          f"{len(saved)} files; missing {len(missing)}, extra {len(extra)}, misnamed {len(misnamed)}, zips {len(zips)}; "
          f"8 Jul 2024: {len(eq)} equity rows saved vs {ours_that_day} of our stocks; byte-identical to NSE: {same}")

    # ---------------------------------------------------------------- later runs
    S = pd.read_csv(os.path.join(g.IN_DIR, "all_stocks.csv"), dtype=str)
    Hs = pd.read_csv(os.path.join(g.IN_DIR, "symbol_history.csv"), dtype=str).fillna("")

    def write_inputs(d, stocks, hist=None):
        os.makedirs(d + "/in", exist_ok=True)
        S[S.Stock.isin(stocks)].to_csv(d + "/in/all_stocks.csv", index=False)
        (Hs if hist is None else hist).pipe(lambda x: x[x.Stock.isin(stocks)]).to_csv(d + "/in/symbol_history.csv", index=False)

    with tempfile.TemporaryDirectory() as d:
        p = temp_patch(d, "2026-10-26", "2026-11-02")
        write_inputs(d, ["INFY"])
        rc1, o1 = child(p)
        write_inputs(d, ["INFY", "TCS"])
        rc2, o2 = child(p)
        tcs = pd.read_csv(d + "/out/daily/TCS.csv", dtype=str) if os.path.exists(d + "/out/daily/TCS.csv") else pd.DataFrame()
        ok = rc1 == 0 and rc2 == 0 and downloads(o1) > 0 and downloads(o2) == 0 and len(tcs) == len(pd.read_csv(d + "/out/daily/INFY.csv"))
        check("7. a stock added to the list is filled in from the saved files, nothing downloaded again", ok,
              f"first run downloaded {downloads(o1)} days, second {downloads(o2)}; TCS rows {len(tcs)}")

        # 9. remove TCS from the list
        before = {f: os.path.getmtime(f) for f in glob.glob(d + "/dep/nse_daily/*/*.csv")}
        write_inputs(d, ["INFY"])
        rc3, o3 = child(p)
        after = {f: os.path.getmtime(f) for f in glob.glob(d + "/dep/nse_daily/*/*.csv")}
        ok = rc3 == 0 and not os.path.exists(d + "/out/daily/TCS.csv") and before == after and downloads(o3) == 0
        check("9. a stock removed from the list: its file deleted, saved NSE files untouched", ok, f"{len(after)} saved files unchanged")

        # 10. a saved NSE file goes missing
        gone = sorted(before)[0]
        os.remove(gone)
        rc4, o4 = child(p)
        ok = rc4 == 0 and "1 saved NSE file(s) missing" in o4 and downloads(o4) == 1 and os.path.exists(gone)
        check("10. a missing saved NSE file is downloaded again (only that one)", ok, f"downloaded {downloads(o4)}")

        # 11. nothing new
        rc5, o5 = child(p)
        check("11. a second run with nothing new downloads nothing", rc5 == 0 and downloads(o5) == 0, f"downloaded {downloads(o5)}")

    with tempfile.TemporaryDirectory() as d:
        p = temp_patch(d, "2025-04-03", "2025-04-15")
        wrong_hist = Hs[Hs.Stock == "ETERNAL"].copy()
        wrong_hist = wrong_hist[wrong_hist.Symbol == "ETERNAL"].assign(From="")       # rename not known yet
        write_inputs(d, ["INFY", "ETERNAL"], pd.concat([Hs[Hs.Stock == "INFY"], wrong_hist]))
        rc1, o1 = child(p)
        r1 = pd.read_csv(d + "/out/daily/ETERNAL.csv", dtype=str)
        write_inputs(d, ["INFY", "ETERNAL"])
        rc2, o2 = child(p)
        r2 = pd.read_csv(d + "/out/daily/ETERNAL.csv", dtype=str)
        ok = (rc1 == 0 and rc2 == 0 and downloads(o2) == 0 and set(r1.Symbol) == {"ETERNAL"}
              and set(r2.Symbol) == {"ZOMATO", "ETERNAL"} and r2.Date.min() < "2025-04-09" and len(r2) > len(r1))
        check("8. a corrected rename is filled in from the saved files, nothing downloaded again", ok,
              f"before: {len(r1)} rows ({sorted(set(r1.Symbol))}); after: {len(r2)} rows ({sorted(set(r2.Symbol))}); downloaded {downloads(o2)}")

    print(f"\n{sum(results)} of {len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
