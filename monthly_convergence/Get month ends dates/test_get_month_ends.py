"""
Tests for get_month_ends.py (step 1). Run after get_month_ends.py:
  ../.venv/bin/python test_get_month_ends.py

The saved file
  1. Known month ends, weekend sessions included: Sat 28 Feb 2015 (Budget day), Sun 30 Oct 2016 (Diwali
     Muhurat), Fri 29 Mar 2019, Tue 31 May 2011.
  2. One row per month from Jan 2011 to the current month, in order. Every finished month is "Confirmed";
     only the current month is "Expected".
  3. Every confirmed date is the last trading day of its month in step 5's NSE calendar (a separate record
     of every day NSE traded), if step 5 has been run.

How a finished month is found (NSE's answers simulated)
  4. It starts at the month's last calendar day, weekends included, and goes back to the first day with an
     NSE file: Oct 2016 -> checks 31 Oct (no file), 30 Oct (file, a Sunday) -> 30 Oct.
     Mar 2019 -> checks 31, 30 Mar (no file), 29 Mar (file) -> 29 Mar.
  5. If NSE can't be reached, the script stops and the file is not changed (no date is guessed).

How the current month is predicted
  6. Last weekday that is not an NSE holiday; a holiday on the last weekday moves it back; weekends are
     skipped; a holiday NSE marks "*" (Muhurat session) counts as a trading day.
  7. The holiday list is downloaded on every run and never saved: two runs both download it, and no
     holiday file is left anywhere.
  8. If the holiday list can't be downloaded, the script still finishes (warned; expected date ignores
     holidays).

Later runs (today and NSE's answers simulated)
  9. Run on 1 Dec 2026: the 190 confirmed rows are kept without any NSE check, Nov 2026 "Expected" becomes
     "Confirmed" (30 Nov, one NSE check), and Dec 2026 is added as "Expected" (31 Dec).
 10. Run on 5 Mar 2027 (months skipped): Nov 2026, Dec 2026, Jan 2027 and Feb 2027 are confirmed, Mar 2027
     added as Expected; nothing earlier is checked again.
 11. --rebuild checks every month again from Jan 2011.

Working on its own
 12. Only get_month_ends.py copied into an empty folder (no output/ or any other folder): it runs, gives
     the same file as ours, and writes nothing but output/month_end_dates.csv.
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

# Child-process helpers: run get_month_ends.main() with its output file, "today" and NSE simulated.
SETUP = """
import sys, datetime, json
sys.dont_write_bytecode = True
sys.path.insert(0, {here!r})
import get_month_ends as g, argparse, pandas as pd
g.OUT = {out!r}; g.OUT_DIR = {outdir!r}
CHECKED = []
"""
FAKE_TODAY = """
class D(datetime.date):
    @classmethod
    def today(cls): return datetime.date({y}, {m}, {d})
g.date = D
"""
FAKE_NSE = """
def fake(day, session=None):
    CHECKED.append(str(pd.Timestamp(day).date()))
    return pd.Timestamp(day).day_of_week < 5          # simulated NSE: a file on every weekday
g.nse_file_exists = fake
"""
RUN = """
ap = argparse.ArgumentParser(); ap.add_argument('--rebuild', action='store_true')
g.main(ap.parse_args({args!r}))
print('CHECKED=' + json.dumps(CHECKED))
"""


def child(out, extra="", args=()):
    code = SETUP.format(here=HERE, out=out, outdir=os.path.dirname(out)) + extra + RUN.format(args=list(args))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    txt = p.stdout + p.stderr
    checked = next((eval(l[8:]) for l in txt.splitlines() if l.startswith("CHECKED=")), None)
    return p.returncode, txt, checked


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -  {detail}" if detail else ""))


def main():
    d = pd.read_csv(g.OUT, dtype=str).fillna("")
    day = dict(zip(d.Month, d["Last trading day"]))
    conf = d[d.Status == "Confirmed"]

    # ---------------------------------------------------------------- the saved file
    known = {"2015-02": "2015-02-28", "2016-10": "2016-10-30", "2019-03": "2019-03-29", "2011-05": "2011-05-31"}
    wrong = {m: day.get(m) for m, v in known.items() if day.get(m) != v}
    check("1. known month ends, weekend sessions included", not wrong, str(wrong))

    this = pd.Timestamp.today().to_period("M")
    months = [str(p) for p in pd.period_range(g.START, this, freq="M")]
    ok = (list(d.Month) == months and (d.Status.iloc[:-1] == "Confirmed").all() and d.Status.iloc[-1] == "Expected"
          and list(d.columns) == g.COLUMNS)
    check("2. one row per month, Jan 2011 to now; finished months Confirmed, current month Expected", ok, f"{len(d)} rows")

    if os.path.exists(NSE_CALENDAR):
        c = pd.read_csv(NSE_CALENDAR, dtype=str)
        y = c.Date[c["NSE file"] == "Yes"]
        last = y.groupby(y.str[:7]).max()
        bad = [(m, x, last.get(m)) for m, x in zip(conf.Month, conf["Last trading day"]) if last.get(m) != x]
        check("3. every confirmed date = last NSE trading day in step 5's calendar", not bad, f"{len(conf)} months; differ: {bad[:5]}")
    else:
        check("3. (skipped: step 5's NSE calendar not found)", True)

    # ---------------------------------------------------------------- how a finished month is found
    def walk(per, files):
        asked = []
        def fake(dd, session=None):
            asked.append(str(dd.date())); return str(dd.date()) in files
        real, g.nse_file_exists = g.nse_file_exists, fake
        try:
            got, closed = g.confirmed_day(pd.Period(per, "M"), None)
        finally:
            g.nse_file_exists = real
        return str(got.date()), asked
    a = walk("2016-10", {"2016-10-28", "2016-10-30"})
    b = walk("2019-03", {"2019-03-28", "2019-03-29"})
    ok = a == ("2016-10-30", ["2016-10-31", "2016-10-30"]) and b == ("2019-03-29", ["2019-03-31", "2019-03-30", "2019-03-29"])
    check("4. walks back from the last calendar day (weekends included) to the first day with an NSE file", ok, f"{a}; {b}")

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "month_end_dates.csv")
        conf.iloc[:-2].to_csv(out, index=False)
        before = open(out).read()
        rc, txt, _ = child(out, "g.nse_file_exists = lambda day, session=None: None\n")
        check("5. NSE not reachable: stops, file unchanged", rc != 0 and "Nothing saved" in txt and open(out).read() == before)

    # ---------------------------------------------------------------- the current month
    nov = pd.Period("2026-11", "M")
    t = [(g.expected_last_day(nov, {})[0], "2026-11-30"),                                   # Monday
         (g.expected_last_day(nov, {"2026-11-30": "Some holiday"})[0], "2026-11-27"),        # back over a weekend
         (g.expected_last_day(pd.Period("2026-10", "M"), {})[0], "2026-10-30"),              # 31 Oct is a Saturday
         (g.expected_last_day(nov, {"2026-11-30": "Diwali Laxmi Pujan*"})[0], "2026-11-30")]  # Muhurat counts
    bad = [(str(x.date()), e) for x, e in t if str(x.date()) != e]
    check("6. expected-day rule (holidays, weekends, Muhurat)", not bad, str(bad))

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "month_end_dates.csv")
        d.to_csv(out, index=False)
        counter = "N = [0]\nreal_dl = g.download_holidays\ndef dl():\n    N[0] += 1\n    return real_dl()\ng.download_holidays = dl\n"
        runs = [child(out, counter + "import atexit; atexit.register(lambda: print('DOWNLOADS=' + str(N[0])))\n")
                for _ in range(2)]
        downloads = [next((l for l in r[1].splitlines() if l.startswith("DOWNLOADS=")), "") for r in runs]
        files = sorted(os.path.relpath(os.path.join(r, f), tmp) for r, _, fs in os.walk(tmp) for f in fs)
        stray = [f for f in os.listdir(HERE) if "holiday" in f.lower()] + \
                [f for f in os.listdir(tmp) if "holiday" in f.lower()]
        ok = all(r[0] == 0 for r in runs) and downloads == ["DOWNLOADS=1", "DOWNLOADS=1"] and files == ["month_end_dates.csv"] and not stray
        check("7. holiday list downloaded on every run, never saved", ok, f"{downloads}; files after runs: {files}")

        rc, txt, _ = child(out, "g.download_holidays = lambda: (_ for _ in ()).throw(RuntimeError('test: NSE down'))\n")
        e = pd.read_csv(out, dtype=str)
        check("8. holiday download fails: warned, still finishes", rc == 0 and "WARNING: could not download" in txt
              and "ignores holidays" in txt and e.Status.iloc[-1] == "Expected")

    # ---------------------------------------------------------------- later runs
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "month_end_dates.csv")
        start = d[d.Month <= "2026-11"].copy()                     # as it is now: up to Oct confirmed, Nov expected
        start.to_csv(out, index=False)
        rc, txt, checked = child(out, FAKE_TODAY.format(y=2026, m=12, d=1) + FAKE_NSE)
        e = pd.read_csv(out, dtype=str).fillna("")
        ok = (rc == 0 and checked == ["2026-11-30"] and len(e) == len(start) + 1
              and e.iloc[:len(conf)].equals(conf.reset_index(drop=True))
              and e[e.Month == "2026-11"][["Last trading day", "Status"]].values.tolist() == [["2026-11-30", "Confirmed"]]
              and e[e.Month == "2026-12"][["Last trading day", "Status"]].values.tolist() == [["2026-12-31", "Expected"]])
        check("9. run on 1 Dec 2026: only Nov confirmed (1 NSE check), Dec added as Expected, older rows kept", ok,
              f"NSE checks: {checked}; last rows: {e.tail(2)[['Month', 'Last trading day', 'Status']].values.tolist()}")

        start.to_csv(out, index=False)
        rc, txt, checked = child(out, FAKE_TODAY.format(y=2027, m=3, d=5) + FAKE_NSE)
        e = pd.read_csv(out, dtype=str).fillna("")
        new = e.iloc[len(conf):][["Month", "Status"]].values.tolist()
        ok = (rc == 0 and new == [["2026-11", "Confirmed"], ["2026-12", "Confirmed"], ["2027-01", "Confirmed"],
                                  ["2027-02", "Confirmed"], ["2027-03", "Expected"]]
              and checked and min(checked) >= "2026-11-01" and e.iloc[:len(conf)].equals(conf.reset_index(drop=True)))
        check("10. run on 5 Mar 2027: the 4 skipped months confirmed, Mar added, nothing older re-checked", ok,
              f"new rows: {new}; NSE checks from {min(checked) if checked else None}")

        rc, txt, checked = child(out, FAKE_TODAY.format(y=2026, m=12, d=1) + FAKE_NSE, args=["--rebuild"])
        e = pd.read_csv(out, dtype=str)
        ok = rc == 0 and checked and min(checked)[:7] == "2011-01" and len({c[:7] for c in checked}) == 191 and len(e) == 192
        check("11. --rebuild checks every month again from Jan 2011", ok,
              f"{len({c[:7] for c in checked or []})} months checked, {len(e)} rows")

    # ---------------------------------------------------------------- on its own
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(os.path.join(HERE, "get_month_ends.py"), tmp)
        p = subprocess.run([sys.executable, "-B", "get_month_ends.py"], cwd=tmp, capture_output=True, text=True)
        made = os.path.join(tmp, "output", "month_end_dates.csv")
        same = p.returncode == 0 and os.path.exists(made) and open(made).read() == open(g.OUT).read()
        written = sorted(os.path.relpath(os.path.join(r, f), tmp) for r, _, fs in os.walk(tmp) for f in fs)
        check("12. works on its own in an empty folder: same result, nothing else written",
              same and written == ["get_month_ends.py", os.path.join("output", "month_end_dates.csv")],
              f"files: {written}" + ("" if same else "; " + (p.stdout + p.stderr).strip()[-200:]))

    print(f"\n{sum(results)} of {len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
