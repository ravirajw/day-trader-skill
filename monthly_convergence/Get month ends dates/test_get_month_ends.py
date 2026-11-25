"""
Tests for get_month_ends.py (step 1). Run after get_month_ends.py:
  ../.venv/bin/python test_get_month_ends.py

  1. Known month ends: Sat 28 Feb 2015 (Budget day), Sun 30 Oct 2016 (Diwali Muhurat), Fri 29 Mar 2019
     (a day Yahoo misses), Tue 31 May 2011.
  2. One row per month from Jan 2011 to the current month, in order; all Confirmed except the current
     month, which is Expected.
  3. Every confirmed date is the last trading day of its month in step 5's NSE calendar (a separate
     record of every day NSE traded), if step 5 has been run.
  4. The expected-day rule: a holiday on the last weekday moves it back; a weekend is skipped; a holiday
     NSE marks "*" (Muhurat session) counts as a trading day.
  5. A later run only adds the missing months (two months removed from a copy are found again, same dates,
     earlier rows untouched).
  6. If NSE can't be reached, the script stops and the file is not changed.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import get_month_ends as g

NSE_CALENDAR = os.path.join(HERE, "..", "Get NSE prices", "dependencies", "nse_calendar.csv")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -  {detail}" if detail else ""))


def child(patch, args=""):
    code = (f"import sys; sys.dont_write_bytecode=True; sys.argv=['x'{args}]; sys.path.insert(0, {HERE!r}); "
            f"import get_month_ends as g, argparse; {patch}; "
            f"ap=argparse.ArgumentParser(); ap.add_argument('--rebuild', action='store_true'); g.main(ap.parse_args())")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main():
    d = pd.read_csv(g.OUT, dtype=str).fillna("")
    day = dict(zip(d.Month, d["Last trading day"]))

    # 1
    known = {"2015-02": "2015-02-28", "2016-10": "2016-10-30", "2019-03": "2019-03-29", "2011-05": "2011-05-31"}
    wrong = {m: day.get(m) for m, v in known.items() if day.get(m) != v}
    check("1. known month ends (weekend sessions included)", not wrong, str(wrong))

    # 2
    this = pd.Timestamp.today().to_period("M")
    months = [str(p) for p in pd.period_range(g.START, this, freq="M")]
    ok = (list(d.Month) == months and (d.Status.iloc[:-1] == "Confirmed").all() and d.Status.iloc[-1] == "Expected"
          and list(d.columns) == g.COLUMNS)
    check("2. one row per month, Jan 2011 to now; only the current month Expected", ok, f"{len(d)} rows")

    # 3
    if os.path.exists(NSE_CALENDAR):
        c = pd.read_csv(NSE_CALENDAR, dtype=str)
        y = c.Date[c["NSE file"] == "Yes"]
        last = y.groupby(y.str[:7]).max()
        conf = d[d.Status == "Confirmed"]
        bad = [(m, x, last.get(m)) for m, x in zip(conf.Month, conf["Last trading day"]) if last.get(m) != x]
        check("3. every confirmed date = last NSE trading day in step 5's calendar", not bad, f"{len(conf)} months; differ: {bad[:5]}")
    else:
        check("3. (skipped: step 5's NSE calendar not found)", True)

    # 4
    nov = pd.Period("2026-11", "M")
    t = [(g.expected_last_day(nov, {})[0], "2026-11-30"),                                   # Monday
         (g.expected_last_day(nov, {"2026-11-30": "Some holiday"})[0], "2026-11-27"),        # back over a weekend
         (g.expected_last_day(pd.Period("2026-10", "M"), {})[0], "2026-10-30"),              # 31 Oct is a Saturday
         (g.expected_last_day(nov, {"2026-11-30": "Diwali Laxmi Pujan*"})[0], "2026-11-30")]  # Muhurat counts
    bad = [(str(a.date()), b) for a, b in t if str(a.date()) != b]
    check("4. expected-day rule (holidays, weekends, Muhurat)", not bad, str(bad))

    # 5 and 6 on a copy
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "month_end_dates.csv")
        conf = d[d.Status == "Confirmed"]
        conf.iloc[:-2].to_csv(out, index=False)
        rc, txt = child(f"g.OUT={out!r}; g.OUT_DIR={tmp!r}")
        e = pd.read_csv(out, dtype=str).fillna("")
        ok = (rc == 0 and "for " + conf.Month.iloc[-2] + " to " + conf.Month.iloc[-1] + " (2 finished month(s))" in txt
              and list(e.Month) == list(d.Month) and list(e["Last trading day"]) == list(d["Last trading day"])
              and e.iloc[:-3].equals(conf.iloc[:-2].reset_index(drop=True)))
        check("5. a later run adds only the missing months", ok, [l for l in txt.splitlines() if "Finding" in l])

        conf.iloc[:-2].to_csv(out, index=False)
        before = open(out).read()
        rc, txt = child(f"g.OUT={out!r}; g.OUT_DIR={tmp!r}; g.nse_file_exists=lambda day, session=None: None")
        check("6. NSE not reachable: stops, file unchanged", rc != 0 and "Nothing saved" in txt and open(out).read() == before)

    print(f"\n{sum(results)} of {len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
