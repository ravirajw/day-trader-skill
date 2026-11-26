"""
Step 4 - Get NSE prices: each stock's official daily prices from NSE's daily price files (bhavcopy),
from 1 Jan 2005, one file per stock. Delisted stocks are included (NSE's files have every stock that
traded that day). Prices are as traded (NOT adjusted for splits, bonuses or demergers - a later step).

Input : ../Combine stocks list/output/all_stocks.csv and symbol_history.csv (made by step 3).
        The script stops with an error if they are missing. It does NOT run step 3 itself.

How it works:
  1. NSE publishes a daily file of every stock's prices for each day it trades (weekends included, e.g.
     Budget day and Diwali Muhurat sessions). For every calendar day from 1 Jan 2005 to today that has not
     been checked yet, the script downloads NSE's file. No file (HTTP 404) = NSE was closed that day.
  2. Each downloaded file is KEPT, exactly as NSE published it (NSE sends a zip; the CSV inside it is saved
     unchanged), in dependencies/nse_daily/<year>/. Files are never deleted, so no day is ever downloaded
     twice, and a stock added to step 3's list later (e.g. after an index review) or a corrected rename
     is filled in from the saved files with no download at all.
  3. Each stock's file is then built from the saved files: series EQ, BE and BZ (the normal,
     trade-for-trade and Z-group equity segments); a row belongs to a stock if its symbol was that stock's
     symbol on that day (step 3's symbol history - renamed stocks are found under their old symbols).
     Where NSE's file has an ISIN (newer files), it must belong to the same company as the stock's ISIN.
     One row per day (preference EQ, then BE, then BZ).

Kept for the next run (dependencies/):
  nse_daily/<year>/<NSE's file name>.csv  NSE's daily file for every trading day since 2005, unchanged.
  nse_calendar.csv   every day checked: Date, NSE file (Yes / No = closed), Saved as (path of the saved
                     file). Later runs only download days not in it. Today is not marked closed if its
                     file is not out yet.

Output: output/daily/<STOCK>.csv   one file per stock in step 3's list (Stock = its symbol today, or last
          symbol): Date, Symbol (on that day), Series, Open, High, Low, Close, Last, Prev Close, Volume,
          Value (Rs), Trades, ISIN       (Trades and ISIN are empty in older NSE files)
        output/nse_prices_report.csv per stock: rows, first and last day, symbols, notes
        Files are rebuilt from the saved NSE files on every run; a file of a stock no longer in the list is deleted.

Usage (from this folder):
  ../.venv/bin/python get_nse_prices.py
"""
import glob
import io
import os
import sys
import time
import zipfile
from datetime import date

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "..", "Combine stocks list", "output")
DEP_DIR = os.path.join(HERE, "dependencies")
STORE_DIR = os.path.join(DEP_DIR, "nse_daily")
OUT_DIR = os.path.join(HERE, "output")
DAILY_DIR = os.path.join(OUT_DIR, "daily")
CALENDAR = os.path.join(DEP_DIR, "nse_calendar.csv")
REPORT = os.path.join(OUT_DIR, "nse_prices_report.csv")
START = "2005-01-01"
NEW_FORMAT_FROM = pd.Timestamp("2024-07-08")      # NSE's new daily file format (UDiFF) starts here
SERIES = ["EQ", "BE", "BZ"]                        # in this order of preference
SAVE_EVERY = 100                                   # days downloaded between calendar saves
HEADERS = {"User-Agent": "Mozilla/5.0"}
COLS = ["Date", "Symbol", "Series", "Open", "High", "Low", "Close", "Last", "Prev Close", "Volume", "Value",
        "Trades", "ISIN"]
OLD = {"SYMBOL": "Symbol", "SERIES": "Series", "OPEN": "Open", "HIGH": "High", "LOW": "Low", "CLOSE": "Close",
       "LAST": "Last", "PREVCLOSE": "Prev Close", "TOTTRDQTY": "Volume", "TOTTRDVAL": "Value",
       "TOTALTRADES": "Trades", "ISIN": "ISIN"}
NEW = {"TckrSymb": "Symbol", "SctySrs": "Series", "OpnPric": "Open", "HghPric": "High", "LwPric": "Low",
       "ClsPric": "Close", "LastPric": "Last", "PrvsClsgPric": "Prev Close", "TtlTradgVol": "Volume",
       "TtlTrfVal": "Value", "TtlNbOfTxsExctd": "Trades", "ISIN": "ISIN"}


# ---------------------------------------------------------------- inputs
def read_inputs():
    files = [os.path.join(IN_DIR, f) for f in ("all_stocks.csv", "symbol_history.csv")]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        sys.exit("Not found: " + ", ".join(os.path.normpath(f) for f in missing) +
                 "\nRun step 3 first: combine_stocks.py")
    S = pd.read_csv(files[0], dtype=str).fillna("")
    H = pd.read_csv(files[1], dtype=str).fillna("")
    if S.empty or H.empty:
        sys.exit("Step 3's stock list is empty.")
    H["From"] = pd.to_datetime(H.From.replace("", "1900-01-01"))
    H["To"] = pd.to_datetime(H.To.replace("", "2200-01-01"))
    issuer = {s: i[:7] for s, i in zip(S.Stock, S["ISIN Code"]) if i}
    return S, H, issuer


# ---------------------------------------------------------------- NSE's daily file
def urls(day):
    m = day.strftime("%b").upper()
    old = f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{day:%Y}/{m}/cm{day:%d}{m}{day:%Y}bhav.csv.zip"
    new = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    return [new, old] if day >= NEW_FORMAT_FROM else [old, new]


def fetch_day(session, day):
    """(file name, the CSV exactly as NSE published it) for a day; None if NSE has no file (closed);
    raises if the file could not be checked (network problem, NSE refusing)."""
    codes = []
    for u in urls(day):
        code = None
        for attempt in range(4):
            try:
                r = session.get(u, headers=HEADERS, timeout=60)
                code = r.status_code
                if code in (200, 404): break
            except requests.RequestException:
                code = "error"
            time.sleep(3 * (attempt + 1))
        if code == 200:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                name = z.namelist()[0]
                return os.path.basename(name), z.read(name)
        codes.append(code)
    if all(c == 404 for c in codes):
        return None
    raise RuntimeError(f"NSE file for {day.date()} could not be checked (HTTP {codes})")


# ---------------------------------------------------------------- the saved NSE files
def read_saved(path, day):
    """A saved NSE daily file as rows with COLS (EQ/BE/BZ only), whichever of NSE's two formats it is in."""
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns=NEW if "TckrSymb" in df.columns else OLD)
    for c in COLS[1:]:
        if c not in df.columns: df[c] = ""
    df["Date"] = day
    df = df[COLS].fillna("")
    for c in ("Symbol", "Series", "ISIN"): df[c] = df[c].str.strip()
    return df[df.Series.isin(SERIES)]


def save_calendar(cal):
    pd.DataFrame([(d, v[0], v[1]) for d, v in sorted(cal.items())],
                 columns=["Date", "NSE file", "Saved as"]).to_csv(CALENDAR, index=False)


def download(session, days, cal):
    """Download the given days and save each NSE file unchanged. A day is marked Yes in the calendar only
    after its file is saved. Returns the days that could not be checked."""
    failed, t0 = [], time.time()
    for i, day in enumerate(days, 1):
        try:
            got = fetch_day(session, day)
            if got is None:
                if day < pd.Timestamp(date.today()):          # today's file may simply not be out yet
                    cal[str(day.date())] = ("No", "")
            else:
                name, data = got
                rel = os.path.join(f"{day:%Y}", name)
                path = os.path.join(STORE_DIR, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "wb") as f: f.write(data)
                os.replace(path + ".tmp", path)
                cal[str(day.date())] = ("Yes", rel)
        except RuntimeError as e:
            failed.append(str(e))
        if i % SAVE_EVERY == 0 or i == len(days):
            save_calendar(cal)
            left = (time.time() - t0) / i * (len(days) - i)
            print(f"  {i}/{len(days)} days ({day.date()}), about {left / 60:.0f} min left   ", end="\r", flush=True)
    print()
    return failed


# ---------------------------------------------------------------- each stock's file, from the saved NSE files
def build_files(H, issuer, cal):
    """Every stock's rows from the saved NSE files, by its symbol on each date. Returns ({stock: rows}, notes)."""
    syms = set(H.Symbol)
    hist = H[["Symbol", "Stock", "From", "To"]]
    parts, notes = [], {}
    saved = sorted((d, rel) for d, (v, rel) in cal.items() if v == "Yes")
    for year in sorted({d[:4] for d, _ in saved}):
        y = pd.concat([read_saved(os.path.join(STORE_DIR, rel), d) for d, rel in saved if d[:4] == year], ignore_index=True)
        y = y[y.Symbol.isin(syms)]
        x = y.merge(hist, on="Symbol")
        d = pd.to_datetime(x.Date)
        x = x[(d >= x.From) & (d <= x.To)]
        # newer files have the ISIN: it must be the same company (first 7 characters = the issuer)
        iss = x.Stock.map(issuer)
        wrong = (x.ISIN != "") & iss.notna() & (x.ISIN.str[:7] != iss)
        for r in x[wrong].itertuples():
            notes.setdefault(r.Stock, []).append(f"{r.Date} {r.Symbol} skipped: ISIN {r.ISIN} is another company")
        x = x[~wrong]
        # one row per stock per day (preference EQ, then BE, then BZ)
        x = x.assign(o=x.Series.map({s: i for i, s in enumerate(SERIES)})).sort_values(["Stock", "Date", "o"])
        dup = x.duplicated(["Stock", "Date"])
        for r in x[dup].itertuples():
            notes.setdefault(r.Stock, []).append(f"{r.Date} extra row in series {r.Series} ignored")
        parts.append(x[~dup][["Stock"] + COLS])
    allx = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["Stock"] + COLS)
    return {s: g[COLS] for s, g in allx.groupby("Stock")}, notes


def write_files(per_stock, stocks):
    os.makedirs(DAILY_DIR, exist_ok=True)
    for s, g in per_stock.items():
        path = os.path.join(DAILY_DIR, f"{s}.csv")
        tmp = path + ".tmp"
        g.to_csv(tmp, index=False)
        os.replace(tmp, path)
    removed = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv") and f[:-4] not in per_stock]
    for f in removed: os.remove(os.path.join(DAILY_DIR, f))
    for f in os.listdir(DAILY_DIR):
        if f.endswith(".tmp"): os.remove(os.path.join(DAILY_DIR, f))
    return removed


# ---------------------------------------------------------------- main
def main():
    S, H, issuer = read_inputs()
    os.makedirs(STORE_DIR, exist_ok=True)
    cal = {}
    if os.path.exists(CALENDAR):
        c = pd.read_csv(CALENDAR, dtype=str).fillna("")
        if "Saved as" in c:
            cal = {d: (v, p) for d, v, p in zip(c.Date, c["NSE file"], c["Saved as"])}
    # a trading day whose saved file is missing is downloaded again
    lost = [d for d, (v, p) in cal.items() if v == "Yes" and not os.path.exists(os.path.join(STORE_DIR, p))]
    if lost:
        print(f"{len(lost)} saved NSE file(s) missing: downloading them again")
        for d in lost: del cal[d]

    # 1. days never checked
    todo = [d for d in pd.date_range(START, pd.Timestamp(date.today())) if str(d.date()) not in cal]
    print(f"Days to download: {len(todo)}" + (f" ({todo[0].date()} to {todo[-1].date()})" if todo else ""))
    failed = download(requests.Session(), todo, cal) if todo else []
    if failed:
        print(f"\nWARNING: {len(failed)} day(s) could not be checked; they will be tried again on the next run:")
        for f in failed[:20]: print("  " + f)

    # 2. each stock's file, from the store
    print("Building each stock's file from the saved NSE files...")
    per_stock, notes = build_files(H, issuer, cal)
    stocks = set(S.Stock)
    removed = write_files({s: g for s, g in per_stock.items() if s in stocks}, stocks)

    # 3. report
    rep = []
    for s in sorted(stocks):
        g = per_stock.get(s)
        if g is not None and len(g):
            rep.append({"Stock": s, "Rows": len(g), "First day": g.Date.iloc[0], "Last day": g.Date.iloc[-1],
                        "Symbols": ", ".join(dict.fromkeys(g.Symbol)), "Notes": "; ".join(notes.get(s, [])[:5])})
        else:
            rep.append({"Stock": s, "Rows": 0, "First day": "", "Last day": "", "Symbols": "",
                        "Notes": "no rows in NSE's files from 2005 (EQ/BE/BZ)"})
    R = pd.DataFrame(rep)
    os.makedirs(OUT_DIR, exist_ok=True)
    R.to_csv(REPORT, index=False)
    yes = sum(v == "Yes" for v, _ in cal.values())
    files = glob.glob(os.path.join(STORE_DIR, "*", "*.csv"))
    size = sum(os.path.getsize(f) for f in files) / 1e6
    print(f"NSE calendar: {len(cal)} days checked, {yes} trading days ({min(cal)} to {max(cal)})")
    print(f"Saved NSE daily files: {len(files)}, {size:.0f} MB, in {STORE_DIR}")
    print(f"Built {int((R.Rows > 0).sum())} of {len(stocks)} stock files in {DAILY_DIR}")
    if removed: print(f"Deleted {len(removed)} file(s) of stocks no longer in the list")
    none = R[R.Rows == 0]
    if len(none): print(f"No NSE rows from 2005: {len(none)} stock(s): {', '.join(none.Stock[:30])}")
    if notes: print(f"Stocks with notes (see report): {len(notes)}")
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
