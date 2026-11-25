"""
Step 1 - Month-end trading days: the last trading day (the "month close") of every month from Jan 2011.

Output: output/month_end_dates.csv
  The first run builds every month from Jan 2011. Each later run keeps what is there and only ADDS the
  months after the last confirmed row, up to and including the CURRENT month (e.g. last row Oct 2026,
  run any day in November -> adds Nov 2026).
  Status column:
    Confirmed - the month is over; the date is NSE's own: the last day of the month that has an NSE
                daily price file.
    Expected  - the current month, not over yet: its last weekday that is not an NSE trading holiday
                (dependencies/output/nse_holidays.csv, made by dependencies/get_nse_holidays.py), or a
                holiday NSE marks "*" (Diwali Muhurat trading session). The next run after the month ends
                replaces it with the confirmed date. A special session announced later (e.g. a Budget
                Saturday) or a surprise holiday can still change it - the confirmed date always comes
                from NSE's files.

How a finished month is confirmed (NSE only, no other source):
  NSE publishes a daily price file (bhavcopy) for every day it trades, weekends included (Budget day
  sessions, Diwali Muhurat trading). Starting from the month's last calendar day and going back one day at
  a time, the first day with an NSE file is the last trading day. Days with no file (HTTP 404) were closed.
  Examples: Sat 28 Feb 2015 (Budget day), Sun 30 Oct 2016 (Muhurat), Fri 29 Mar 2019 (Yahoo misses it).
  If NSE can't be reached (any answer other than file / no file), the script stops and saves nothing new:
  a date is never guessed.

Holidays: if dependencies/output/nse_holidays.csv is missing, or has no holidays for the current year,
dependencies/get_nse_holidays.py is run automatically first (used only for the Expected month).

Usage (from this folder):
  ../.venv/bin/python get_month_ends.py              # add the months not yet in the file
  ../.venv/bin/python get_month_ends.py --rebuild    # rebuild the whole file from Jan 2011

Output columns: Month, Last trading day, Weekday, Status, How found
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import date

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
OUT = os.path.join(OUT_DIR, "month_end_dates.csv")
DEP_DIR = os.path.join(HERE, "dependencies")
HOLIDAYS = os.path.join(DEP_DIR, "output", "nse_holidays.csv")   # made by dependencies/get_nse_holidays.py
START = "2011-01"
HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_CLOSED_DAYS = 15        # no month ends with this many closed days in a row: more means something is wrong
COLUMNS = ["Month", "Last trading day", "Weekday", "Status", "How found"]


def nse_file_exists(day, session=requests):
    """True if NSE has a daily price file for that day, False if it has none (closed), None if it could not
    be checked. NSE's file names changed on 8 Jul 2024; both are tried."""
    day = pd.Timestamp(day)
    m = day.strftime("%b").upper()
    urls = [f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{day:%Y}/{m}/cm{day:%d}{m}{day:%Y}bhav.csv.zip",
            f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"]
    if day >= pd.Timestamp("2024-07-08"): urls.reverse()
    codes = []
    for u in urls:
        code = None
        for attempt in range(4):
            try:
                code = session.get(u, headers=HEADERS, timeout=30, stream=True).status_code
                if code in (200, 404): break
            except requests.RequestException:
                code = "error"
            time.sleep(3 * (attempt + 1))
        if code == 200: return True
        codes.append(code)
    return False if all(c == 404 for c in codes) else None


def confirmed_day(per, session):
    """(last trading day, closed days after it) for a finished month, from NSE's files."""
    day = per.end_time.normalize()
    closed = []
    while day.to_period("M") == per and len(closed) < MAX_CLOSED_DAYS:
        ok = nse_file_exists(day, session)
        if ok is None:
            sys.exit(f"Could not check NSE's file for {day.date()} (NSE not reachable?). Nothing saved - try again later.")
        if ok: return day, closed
        closed.append(day)
        day -= pd.Timedelta(days=1)
    sys.exit(f"No NSE file in the last {len(closed)} days of {per} - stopping, nothing saved.")


def nse_holidays():
    """NSE's trading holidays {date: name}, from dependencies/output/nse_holidays.csv. If that file is missing,
    or has no holidays for the current year yet, dependencies/get_nse_holidays.py is run first to get them."""
    year = str(date.today().year)
    have = os.path.exists(HOLIDAYS) and pd.read_csv(HOLIDAYS, dtype=str).Date.str.startswith(year).any()
    if not have:
        why = "not found" if not os.path.exists(HOLIDAYS) else f"has no {year} holidays"
        print(f"Holiday list {why}: running dependencies/get_nse_holidays.py ...", flush=True)
        subprocess.run([sys.executable, os.path.join(DEP_DIR, "get_nse_holidays.py")], cwd=DEP_DIR)
    if not os.path.exists(HOLIDAYS):
        sys.exit(f"NSE holiday list still not available: {HOLIDAYS}")
    h = pd.read_csv(HOLIDAYS, dtype=str).fillna("")
    return dict(zip(h.Date, h.Holiday))


def expected_last_day(per, holidays):
    """Last day of the month NSE is expected to trade: a weekday that is not a holiday, or a holiday NSE
    marks "*" (Muhurat trading session, which can fall on any day)."""
    for d in sorted(pd.date_range(per.start_time, per.end_time.normalize()), reverse=True):
        name = holidays.get(str(d.date()))
        if name is not None:
            if name.strip().endswith("*"): return d, f"Muhurat session ({name.strip()})"
            continue
        if d.day_of_week < 5: return d, ""
    return None, ""


def main(a):
    os.makedirs(OUT_DIR, exist_ok=True)
    old = None
    if os.path.exists(OUT) and not a.rebuild:
        old = pd.read_csv(OUT, dtype=str).fillna("")
        if "Status" not in old or "Last trading day" not in old:
            sys.exit(f"{OUT} is not in the expected format. Run with --rebuild.")
        old = old[old.Status == "Confirmed"]          # an "Expected" month is worked out again
        if "How found" not in old: old = old.assign(**{"How found": ""})
        old = old[COLUMNS]
    this_month = pd.Timestamp(date.today()).to_period("M")
    last_done = this_month - 1
    first = pd.Period(START, "M") if old is None or old.empty else pd.Period(old.Month.iloc[-1], "M") + 1
    rows = []
    if first <= last_done:
        months = pd.period_range(first, last_done, freq="M")
        print(f"Finding the last trading day from NSE's files for {months[0]} to {months[-1]} ({len(months)} finished month(s))...")
        session = requests.Session()
        for i, per in enumerate(months, 1):
            day, closed = confirmed_day(per, session)
            how = "last day with an NSE file" + (f"; no NSE file on the {len(closed)} later day(s)" if closed else "")
            if day.day_of_week >= 5: how += f"; NSE traded on a {day.day_name()}"
            rows.append({"Month": str(per), "Last trading day": str(day.date()), "Weekday": day.day_name(),
                         "Status": "Confirmed", "How found": how})
            print(f"  {i}/{len(months)} {per}: {day.date()}   ", end="\r", flush=True)
        print()
    hol = nse_holidays()
    if not any(h.startswith(str(this_month.year)) for h in hol):
        print(f"  WARNING: NSE has not published {this_month.year} holidays yet; the expected date ignores holidays")
    day, why = expected_last_day(this_month, hol)
    today = pd.Timestamp(date.today())
    rows.append({"Month": str(this_month), "Last trading day": str(day.date()), "Weekday": day.day_name(),
                 "Status": "Expected",
                 "How found": "month not over: " + (why or "last weekday that is not an NSE holiday") +
                              (" (today)" if day == today else "")})
    new = pd.DataFrame(rows, columns=COLUMNS)
    d = new if old is None or old.empty else pd.concat([old, new], ignore_index=True)
    d.to_csv(OUT, index=False)
    done = new[new.Status == "Confirmed"]
    if len(done): print(f"Confirmed {len(done)} month(s): {done.Month.iloc[0]} to {done.Month.iloc[-1]}")
    print(f"Expected  {this_month}: {day.date()} ({day.day_name()}) - confirmed on the first run after the month ends")
    print(f"File now has {len(d)} months ({d.Month.iloc[0]} to {d.Month.iloc[-1]}): {OUT}")
    weekend = done[done.Weekday.isin(["Saturday", "Sunday"])]
    if len(weekend):
        print("Months ending on a weekend session: " + ", ".join(f"{m} ({x} {w})" for m, x, w in
                                                              zip(weekend.Month, weekend["Last trading day"], weekend.Weekday)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Last trading day of every month from Jan 2011, from NSE's daily files (adds new months only)")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the whole file from Jan 2011")
    main(ap.parse_args())
