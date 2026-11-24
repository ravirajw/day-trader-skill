"""
Month-end trading days - the last trading day (the "month close") of every month from Jan 2011.

Output: output/month_end_dates.csv
  The first run builds every month from Jan 2011. Each later run keeps what is there and only ADDS the
  months after the last row, up to and including the CURRENT month (e.g. last row Oct 2026, run any day
  in November -> adds Nov 2026).
  Status column:
    Confirmed - the month is over; the date is checked against real trading data and NSE's files.
    Expected  - the current month, not over yet: its last weekday that is not an NSE trading holiday
                (dependencies/output/nse_holidays.csv, made by dependencies/get_nse_holidays.py). The next run after
                the month ends replaces it with the confirmed date. A surprise holiday or special session
                could still change it.

How the last trading day is found:
  1. Trading days come from Yahoo daily data of large, always-traded stocks; a day counts only if most of
     them have volume > 0 (Yahoo adds fake zero-volume rows on some holidays, e.g. 30 Apr 2009).
  2. Cross-check: the Nifty 50 index's last day in the month (Yahoo ^NSEI).
  3. NSE's official daily price files (bhavcopy) decide: the date must have an NSE file and no
     later weekday in the month may have one (those are holidays). If NSE traded on a later day that Yahoo
     is missing (e.g. 29 Mar 2019), the NSE date is used and noted.

Holidays: if dependencies/output/nse_holidays.csv is missing, or has no holidays for the current year,
dependencies/get_nse_holidays.py is run automatically first.

Usage (from this folder):
  ../.venv/bin/python get_month_ends.py              # add any finished months not yet in the file
  ../.venv/bin/python get_month_ends.py --rebuild    # rebuild the whole file from Jan 2011
  ../.venv/bin/python get_month_ends.py --skip-nse   # don't check NSE's official files (faster, less sure)

Output columns: Month, Last trading day, Weekday, Status, Check: NSE official files, Check: Nifty 50, Note
"""
import argparse
import logging
import os
import sys
import time
from datetime import date

import pandas as pd
import requests
import yfinance as yf

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
OUT = os.path.join(OUT_DIR, "month_end_dates.csv")
DEP_DIR = os.path.join(HERE, "dependencies")
HOLIDAYS = os.path.join(DEP_DIR, "output", "nse_holidays.csv")   # made by dependencies/get_nse_holidays.py
START = "2011-01"
NSE_FILES_FROM = "2011-01"      # NSE's official daily files are downloadable for every month we need
BELLWETHERS = ["RELIANCE", "INFY", "ITC", "SBIN", "HDFCBANK", "LT", "BHEL", "WIPRO"]


def nse_file_exists(day):
    """True/False if NSE's official bhavcopy for that day exists / doesn't; None if it can't be checked."""
    day = pd.Timestamp(day)
    urls = [f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{day:%Y}/{day.strftime('%b').upper()}/cm{day:%d}{day.strftime('%b').upper()}{day:%Y}bhav.csv.zip",
            f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"]
    codes = []
    for u in urls:
        c = None
        for attempt in range(3):
            try:
                c = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, stream=True).status_code; break
            except Exception:
                time.sleep(3)
        if c == 200: return True
        codes.append(c)
    return False if all(c in (403, 404) for c in codes) else None


def nse_holidays():
    """NSE's trading holidays, from dependencies/output/nse_holidays.csv. If that file is missing, or has no
    holidays for the current year yet, dependencies/get_nse_holidays.py is run first to get them."""
    year = str(date.today().year)
    have = os.path.exists(HOLIDAYS) and pd.read_csv(HOLIDAYS, dtype=str).Date.str.startswith(year).any()
    if not have:
        import subprocess
        why = "not found" if not os.path.exists(HOLIDAYS) else f"has no {year} holidays"
        print(f"Holiday list {why}: running dependencies/get_nse_holidays.py ...", flush=True)
        subprocess.run([sys.executable, os.path.join(DEP_DIR, "get_nse_holidays.py")], cwd=DEP_DIR)
    if not os.path.exists(HOLIDAYS):
        sys.exit(f"NSE holiday list still not available: {HOLIDAYS}")
    return set(pd.read_csv(HOLIDAYS, dtype=str).Date)


def expected_last_day(per, holidays):
    """Last weekday of the month that is not an NSE trading holiday."""
    for d in sorted(pd.bdate_range(per.start_time, per.end_time.normalize()), reverse=True):
        if str(d.date()) not in holidays: return d
    return None


def trading_days():
    """Real trading days from the bellwethers' Yahoo data, and Yahoo's fake zero-volume days."""
    raw = yf.download([s + ".NS" for s in BELLWETHERS], start="2010-12-01", auto_adjust=False, progress=False, group_by="ticker")
    vol = pd.DataFrame({s: raw[s + ".NS"]["Volume"] for s in BELLWETHERS if s + ".NS" in raw.columns.get_level_values(0)})
    vol.index = pd.to_datetime(vol.index).tz_localize(None)
    have = vol.notna().sum(axis=1); traded = (vol > 0).sum(axis=1)
    days = vol.index[(have > 0) & (traded >= (have / 2))]          # most bellwethers really traded
    zero = vol.index[(have > 0) & (traded == 0)]
    return days, zero


def main(a):
    os.makedirs(OUT_DIR, exist_ok=True)
    old = pd.read_csv(OUT, dtype=str).fillna("") if os.path.exists(OUT) and not a.rebuild else None
    if old is not None:
        if "Status" not in old: old.insert(3, "Status", "Confirmed")
        old = old[old.Status == "Confirmed"]          # an "Expected" month is worked out again
    this_month = pd.Timestamp(date.today()).to_period("M")
    last_done = this_month - 1
    first = pd.Period(START, "M") if old is None or old.empty else pd.Period(old.Month.iloc[-1], "M") + 1
    rows = []
    if first <= last_done:
        months = pd.period_range(first, last_done, freq="M")
        print(f"Finding the last trading day for {months[0]} to {months[-1]} ({len(months)} finished month(s))...")
        rows = confirmed_rows(months, a.skip_nse)
    # the current month: expected last trading day from NSE's holiday list
    hol = nse_holidays()
    if not any(h.startswith(str(this_month.year)) for h in hol):
        print(f"  WARNING: NSE has not published {this_month.year} holidays yet; the expected date ignores holidays")
    day = expected_last_day(this_month, hol)
    today = pd.Timestamp(date.today())
    rows.append({"Month": str(this_month), "Last trading day": str(day.date()), "Weekday": day.day_name(),
                 "Status": "Expected", "Check: NSE official files": "not yet", "Check: Nifty 50": "not yet",
                 "Note": "month not over: last weekday that is not an NSE holiday" +
                         (" (today)" if day == today else "")})
    new = pd.DataFrame(rows)
    d = new if old is None or old.empty else pd.concat([old, new], ignore_index=True)
    d.to_csv(OUT, index=False)
    done = new[new.Status == "Confirmed"]
    if len(done): print(f"Confirmed {len(done)} month(s): {done.Month.iloc[0]} to {done.Month.iloc[-1]}")
    print(f"Expected  {this_month}: {day.date()} ({day.day_name()}) - confirmed on the first run after the month ends")
    print(f"File now has {len(d)} months ({d.Month.iloc[0]} to {d.Month.iloc[-1]}): {OUT}")
    att = done[(done.Note != "") | ~done["Check: NSE official files"].str.startswith("OK") | done["Check: Nifty 50"].str.startswith("differs")]
    if len(att):
        pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 120)
        print("\nConfirmed months with a note or a differing check:\n" + att.to_string(index=False))


def confirmed_rows(months, skip_nse):
    days, zero_days = trading_days()
    by_month = pd.Series(days, index=days).groupby(days.to_period("M")).max()
    idx = yf.Ticker("^NSEI").history(start="2010-12-01", auto_adjust=False)
    idx.index = idx.index.tz_localize(None)
    idx_last = pd.Series(idx.index, index=idx.index).groupby(idx.index.to_period("M")).max()
    rows = []
    for per in months:
        day = by_month.get(per)
        if day is None:
            sys.exit(f"No trading days found for {per} in Yahoo's data - not saving. Try again later.")
        note = []
        zeros_after = [z for z in zero_days if z.to_period("M") == per and z > day]
        if zeros_after: note.append("Yahoo has fake zero-volume rows on " + ", ".join(str(z.date()) for z in zeros_after) + " (holiday), ignored")
        nse = "not checked"
        if per >= pd.Period(NSE_FILES_FROM, "M") and not skip_nse:
            later = list(pd.bdate_range(day + pd.Timedelta(days=1), per.end_time.normalize()))
            here = nse_file_exists(day); after = [nse_file_exists(x) for x in later]
            traded_later = [x for x, ok in zip(later, after) if ok is True]
            if traded_later:
                note.append(f"Yahoo is missing {', '.join(str(x.date()) for x in traded_later)}; NSE traded that day, so the NSE date is used")
                day = traded_later[-1]; nse = "OK (NSE date used)"
            elif here is True and all(x is False for x in after):
                nse = "OK" + (f" ({len(later)} later weekday(s) confirmed holidays)" if later else "")
            elif here is False:
                nse = "FAIL - no NSE file for this date"
            else:
                nse = "could not check"
            time.sleep(0.3)
        il = idx_last.get(per)
        rows.append({"Month": str(per), "Last trading day": str(day.date()), "Weekday": day.day_name(), "Status": "Confirmed",
                     "Check: NSE official files": nse,
                     "Check: Nifty 50": "OK" if il is not None and il == day else ("n/a" if il is None else f"differs ({il.date()})"),
                     "Note": "; ".join(note)})
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Last trading day of every month from Jan 2011 (adds new months only)")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the whole file from Jan 2011")
    ap.add_argument("--skip-nse", action="store_true", help="skip the NSE official-file check (faster)")
    main(ap.parse_args())
