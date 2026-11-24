"""
NSE equity trading holidays - downloads NSE's published holiday list for the current year.

Source: NSE's holiday list (www.nseindia.com/api/holiday-master?type=trading, capital market segment).
        NSE publishes the coming year's list in December, so run this again early each year.
Output: output/nse_holidays.csv   columns: Date (YYYY-MM-DD), Weekday, Holiday
        Holidays already saved for earlier years are kept, so the file builds up a history.
        If NSE can't be reached nothing is changed and the script says so.

Used by ../get_month_ends.py to work out the current month's expected last trading day.

Usage (from this folder):
  ../../.venv/bin/python get_nse_holidays.py
"""
import os
import sys

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output", "nse_holidays.csv")
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.nseindia.com/"}


def main():
    try:
        ses = requests.Session()
        ses.get("https://www.nseindia.com/", headers=HEADERS, timeout=30)
        r = ses.get("https://www.nseindia.com/api/holiday-master?type=trading", headers=HEADERS, timeout=30)
        r.raise_for_status()
        cm = r.json().get("CM", [])
    except Exception as e:
        sys.exit(f"Could not download NSE's holiday list ({e}). Nothing changed.")
    if not cm:
        sys.exit("NSE's holiday list came back empty. Nothing changed.")
    new = pd.DataFrame([{"Date": pd.to_datetime(x["tradingDate"], format="%d-%b-%Y"),
                         "Holiday": " ".join(str(x.get("description", "")).split())} for x in cm])
    new["Weekday"] = new.Date.dt.day_name()
    new["Date"] = new.Date.dt.date.astype(str)
    new = new[["Date", "Weekday", "Holiday"]]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    old = pd.read_csv(OUT, dtype=str) if os.path.exists(OUT) else pd.DataFrame(columns=new.columns)
    years = set(new.Date.str[:4])
    old = old[~old.Date.str[:4].isin(years)]          # NSE's current list replaces what was saved for its year(s)
    d = pd.concat([old, new]).sort_values("Date").reset_index(drop=True)
    d.to_csv(OUT, index=False)
    print(f"NSE trading holidays for {', '.join(sorted(years))}: {len(new)} ({(new.Weekday.isin(['Saturday', 'Sunday'])).sum()} fall on a weekend)")
    print(new.to_string(index=False))
    print(f"\nSaved: {OUT}  ({len(d)} holidays, {d.Date.min()[:4]} to {d.Date.max()[:4]})")


if __name__ == "__main__":
    main()
