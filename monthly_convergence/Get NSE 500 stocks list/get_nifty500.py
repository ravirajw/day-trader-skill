"""
Step 2 - Get the list of Nifty 500 stocks (the only stocks we trade): today's list, or the list on a past date,
already filtered to ordinary shares (see "Filter" below).

Today's list (no --date):
  1. Downloads the official Nifty 500 constituents list from NSE
     (falls back to niftyindices.com if the NSE archive is unavailable).
  2. Checks the file looks right (expected columns, about 500 rows, no duplicate symbols).
  3. Saves it as output/nifty500_latest.csv
  4. Compares with the previous saved list and prints any stocks added to or removed from
     the index (NSE reviews the index twice a year).

List on a past date (--date YYYY-MM-DD):
  NSE does not publish old lists, so the list is REBUILT from two official sources:
  a. Saved copies of NSE's own list file, kept by the Internet Archive (web.archive.org):
     Jan 2010, Jan 2014, Jul 2014, Mar 2015, then Oct 2018 onwards.
  b. NSE's index press releases (niftyindices.com/press-release), which announce every stock
     leaving and joining the index, with the date the change takes effect.
  The nearest saved copy ON OR BEFORE the date is the starting point; every announced change
  that took effect after that copy and on or before the date is applied (exclusions first).
  Renamed stocks are followed with NSE's official symbol-change list.
  A change effective ON the date counts (the stock is in the index at that day's close).

  Output: output/nifty500_<date>.csv  (nifty500_latest.csv is NOT changed)
     Symbol        - the stock's NSE symbol ON that date
     Symbol today  - its symbol today (after renames), for matching with price data
     Company Name, Industry, ISIN Code
     Source        - "list copy <date>" or the press release that added it

  --check rebuilds every saved copy from the one before it and prints any differences,
  to prove the announcements are being read correctly.

Filter (every list is filtered before it is saved): only ordinary shares are kept. Removed:
  1. REITs and InvITs - units, not shares (e.g. EMBASSY, BIRET, BAGMANE). Found from the ISIN: an Indian
     ISIN is INE + 4-character issuer code + 2-digit security type + ...; type "01" is equity shares.
     NSE's series "RR" (REIT) or "IV" (InvIT) is also used when the list has it.
  2. NSE placeholders: symbols starting with DUMMY (used for a few days during a demerger).
  NOT removed: stocks that have since been delisted, merged or renamed - on the list's date they were real
  index stocks, so a backtest must keep them. A stock with no ISIN in the list is kept.
  Every removal is written to output/removed_from_lists.csv (List, Symbol, Company Name, ISIN Code, Reason),
  so nothing is taken out silently. --check is not affected: it compares the full rebuilt list with NSE's
  own saved files (which do include REITs).

Downloaded data is kept and reused (dependencies/history/), so later runs only fetch what is new:
  dependencies/history/archive_copies/      Internet Archive copies of NSE's list file
  dependencies/history/press_releases/      text of each NSE press release that mentions the Nifty 500 (PDFs are deleted once read)
  dependencies/history/press_releases_read.csv  every press release already read and what it was, so none is downloaded twice
  dependencies/history/nifty500_changes.csv every Nifty 500 change read from the press releases

Known gap, filled by hand (MANUAL_CHANGES below): the press release of 2 Mar 2012
("Change in S&P CNX 500 Index w.e.f March 7, 2012") is missing from NSE's site and the archive.
Its changes are taken from the difference between the saved copies of Jan 2010 and Jan 2014.

Many dates in one run (NSE and the archive are checked for updates only once):
  --month-ends        one list per date in ../Get month ends dates/output/month_end_dates.csv
                      (the script stops if that file does not exist; run get_month_ends.py first)
  --dates-file PATH   one list per date in any CSV of dates (column "Last trading day", or the first column)
  --start / --end     optional, YYYY-MM: only months in this range
  Dates that have not come yet (e.g. an expected month end later this month) are skipped.

Usage (from this folder):
  ../.venv/bin/python get_nifty500.py                                   # today's list
  ../.venv/bin/python get_nifty500.py --date 2011-01-31                 # one date
  ../.venv/bin/python get_nifty500.py --month-ends                      # every month end
  ../.venv/bin/python get_nifty500.py --month-ends --start 2011-01 --end 2026-10
  ../.venv/bin/python get_nifty500.py --dates-file /path/to/dates.csv   # your own list of dates
  ../.venv/bin/python get_nifty500.py --check
"""
import argparse
import io
import logging
import os
import re
import sys
import time
from datetime import date, datetime

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "dependencies")    # data the script needs, kept between runs
RESULT_DIR = os.path.join(HERE, "output")       # the lists it produces
MONTH_ENDS = os.path.join(HERE, "..", "Get month ends dates", "output", "month_end_dates.csv")
LATEST = os.path.join(RESULT_DIR, "nifty500_latest.csv")
REMOVED = os.path.join(RESULT_DIR, "removed_from_lists.csv")
NON_SHARE_SERIES = {"RR": "REIT", "IV": "InvIT"}
HIST = os.path.join(OUT_DIR, "history")
COPIES_DIR = os.path.join(HIST, "archive_copies")
PRS_DIR = os.path.join(HIST, "press_releases")
CHANGES = os.path.join(HIST, "nifty500_changes.csv")
READ_LOG = os.path.join(HIST, "press_releases_read.csv")   # every press release already read, so none is downloaded twice
SYMBOL_CHANGES = os.path.join(OUT_DIR, "nse_symbol_changes.csv")
SYMBOL_CHANGES_URL = "https://archives.nseindia.com/content/equities/symbolchange.csv"
SOURCES = [
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
HEADERS = {"User-Agent": "Mozilla/5.0"}
# Addresses NSE has used for the list over the years (CNX 500 was renamed Nifty 500 in 2015)
ARCHIVED_URLS = [
    "nseindia.com/content/indices/ind_cnx500list.csv",
    "nseindia.com/content/indices/ind_nifty500list.csv",
    "archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
CDX = "https://web.archive.org/cdx/search/cdx"
PRESS_PAGE = "https://www.niftyindices.com/press-release"
PRESS_BASE = "https://www.niftyindices.com"
PRESS_FROM = "2009-10-01"          # oldest press release read: the backtest starts Jan 2011 and the
                                   # first saved copy used is 2 Jan 2010 (older ones are not needed)
COPIES_FROM = "2010-01-01"         # saved copies before this are ignored, so dates before 2 Jan 2010 are refused
WORKERS = 8                        # press releases downloaded / read at the same time
SKIP_TITLES = re.compile(r"fixed income|\bsdl\b|g-sec|gilt|bond|t-bill|treasury|money market", re.I)
COLUMNS = ["Symbol", "Company Name", "Industry", "Series", "ISIN Code"]

# Older press releases give only company names (no symbol). Symbols as on the release date.
NAME_TO_SYMBOL = {
    "AGC Networks": "AGCNET", "Allied Digital Services": "ADSL", "Core Projects & Technologies": "COREPROTEC",
    "Kemrock Industries and Exports": "KEMROCK", "Orissa Mineral Development": "ORISSAMINE",
    "Pipavav Shipyard": "PIPAVAVYD", "3i Infotech": "3IINFOTECH", "Jindal SouthWest Hold": "JINDALSWHL",
    "Binani Cement": "BINANICEM", "Banco Products (India)": "BANCOINDIA",
}
# Changes added by hand: (effective, action, symbol, company, reason). Each one is backed by a source.
MANUAL_CHANGES = [
    ("2012-03-07", "Exclude", "PROVOGUE", "Provogue (India) Ltd.", "2 Mar 2012 release missing; inferred from saved copies Jan 2010 / Jan 2014"),
    ("2012-03-07", "Exclude", "ZANDUREALT", "Zandu Realty Ltd.", "2 Mar 2012 release missing; inferred from saved copies Jan 2010 / Jan 2014"),
    ("2012-03-07", "Include", "JINDALSAW", "Jindal Saw Ltd.", "2 Mar 2012 release missing; inferred from saved copies Jan 2010 / Jan 2014"),
    ("2024-08-30", "Exclude", "TATAMTRDVR", "Tata Motors Ltd. DVR", "ind_prs23082024_1: DVR shares cancelled, excluded (stated in text, not a table)"),
    ("2024-09-30", "Exclude", "PRSMJOHNSN", "Prism Johnson Ltd.", "ind_prs25092024: excluded instead of IDEA (table layout, read by hand)"),
]
# Announced changes that NSE later cancelled or reversed: (release, action or None = all, symbol or None = all, reason)
CANCELLED = [
    ("ind_prs18022020", None, None, "Mar 2020 review deferred (ind_prs23032020) and declared null (ind_prs13052020)"),
    ("ind_prs12032020", None, None, "Mar 2020 review deferred (ind_prs23032020) and declared null (ind_prs13052020)"),
    ("ind_prs19032020", None, None, "Mar 2020 review deferred (ind_prs23032020) and declared null (ind_prs13052020)"),
    ("ind_prs23032020", None, None, "deferred (ind_prs25032020) and declared null (ind_prs13052020)"),
    ("ind_prs28022024", "Include", "IREDA", "inclusion revoked by ind_prs19032024"),
    ("ind_prs28022024", "Exclude", "VGUARD", "exclusion revoked by ind_prs19032024"),
    ("ind_prs19032024", None, None, "table layout; its Nifty 500 effect is the two revocations above"),
    ("ind_prs23082024", "Exclude", "IDEA", "exclusion revoked by ind_prs25092024"),
    ("ind_prs25092024", None, None, "table layout; read by hand into MANUAL_CHANGES"),
]
# Renames missing from NSE's symbol-change file: (old, new, date). Date = the first day the new symbol is in
# NSE's daily price file (checked day by day: the old symbol is gone that day). This only changes the symbol
# shown, not whether the stock is a member.
EXTRA_RENAMES = [("PHILIPCARB", "PCBL", "2022-01-13"), ("BURGERKING", "RBA", "2022-02-17"), ("WABCOINDIA", "ZFCVINDIA", "2022-04-01")]


# ---------------------------------------------------------------- today's list
def download():
    errors = []
    for url in SOURCES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")))
            df.columns = [c.strip() for c in df.columns]
            return df, url
        except Exception as e:
            errors.append(f"{url}: {e}")
    sys.exit("Could not download the Nifty 500 list:\n  " + "\n  ".join(errors))


def validate(df):
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        sys.exit(f"Downloaded file is missing columns {missing}. Columns found: {list(df.columns)}")
    df = df[COLUMNS].copy()
    for c in COLUMNS:
        df[c] = df[c].astype(str).str.strip()
    dupes = df[df.Symbol.duplicated()].Symbol.tolist()
    if dupes:
        sys.exit(f"Duplicate symbols in the list: {dupes}")
    if not 480 <= len(df) <= 520:
        sys.exit(f"Expected about 500 stocks but got {len(df)} - not saving. Check the source.")
    return df.sort_values("Symbol").reset_index(drop=True)


def main():
    os.makedirs(OUT_DIR, exist_ok=True); os.makedirs(RESULT_DIR, exist_ok=True)
    df, url = download()
    df = validate(df)

    previous = pd.read_csv(LATEST) if os.path.exists(LATEST) else None
    df, removed = remove_non_shares(df)
    df.to_csv(LATEST, index=False)
    log_removed({"latest": removed})

    print(f"Nifty 500 list downloaded from {url}")
    print(f"Stocks: {len(df)}" + (f" (removed {len(removed)}: {', '.join(removed.Symbol)} - see {os.path.basename(REMOVED)})" if len(removed) else ""))
    print(f"Saved:  {LATEST}")

    if previous is not None:
        old, new = set(previous.Symbol.astype(str)), set(df.Symbol)
        added, removed = sorted(new - old), sorted(old - new)
        if added or removed:
            print(f"\nChanges since the last saved list:")
            print(f"  Added   ({len(added)}): {', '.join(added) or '-'}")
            print(f"  Removed ({len(removed)}): {', '.join(removed) or '-'}")
        else:
            print("\nNo change since the last saved list.")

    print("\nStocks by industry:")
    print(df.Industry.value_counts().to_string())


# ---------------------------------------------------------------- saved copies (Internet Archive)
def read_list(text):
    """Old files start with a title line ("Constituents list of S&P CNX 500"): read from the header row."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if "symbol" in l.lower() and "," in l), 0)
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
    df.columns = [c.strip() for c in df.columns]
    return df


def archive_get(url, **kw):
    """The Internet Archive limits how fast it can be called: retry with growing waits."""
    err = "rate limited"
    for wait in (0, 10, 30, 60):
        time.sleep(wait)
        try:
            r = requests.get(url, headers=HEADERS, timeout=60, **kw)
            if r.status_code == 429: continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            err = e
    raise RuntimeError(f"Internet Archive not reachable after retries ({err})")


def saved_copies():
    """{copy date: list} for every saved copy, downloading only copies not already kept locally."""
    os.makedirs(COPIES_DIR, exist_ok=True)
    have = {f[5:13] for f in os.listdir(COPIES_DIR) if f.startswith("copy_")}
    snaps = {}
    for u in ARCHIVED_URLS:
        try:
            r = archive_get(CDX, params={"url": u, "output": "json", "fl": "timestamp,original",
                                         "filter": "statuscode:200", "collapse": "digest"})
            for ts, orig in r.json()[1:]:
                snaps.setdefault(ts[:8], (ts, orig))
        except Exception as e:
            print(f"  could not search the archive for {u} ({e}); using copies already kept")
    new = 0
    for day, (ts, orig) in sorted(snaps.items()):
        if day in have or day < COPIES_FROM.replace("-", ""): continue
        try:
            df = read_list(archive_get(f"https://web.archive.org/web/{ts}id_/{orig}").content.decode("utf-8-sig", "ignore"))
        except Exception as e:
            print(f"  archived copy {ts} could not be read ({e})"); continue
        if "Symbol" not in df.columns or len(df) < 480: continue
        df.to_csv(os.path.join(COPIES_DIR, f"copy_{day}.csv"), index=False); new += 1
        time.sleep(2)
    if new: print(f"  {new} new saved list copies downloaded")
    out = {}
    for f in sorted(os.listdir(COPIES_DIR)):
        if f.startswith("copy_"):
            d = pd.read_csv(os.path.join(COPIES_DIR, f), dtype=str)
            d["Symbol"] = d.Symbol.str.strip()
            d = d[~d.Symbol.str.startswith("DUMMY")]     # NSE's temporary placeholders during demergers, not stocks
            out[pd.Timestamp(f[5:13])] = d
    return {d: v for d, v in out.items() if d >= pd.Timestamp(COPIES_FROM)}


# ---------------------------------------------------------------- press releases
def press_list():
    r = requests.get(PRESS_PAGE, headers=HEADERS, timeout=60); r.raise_for_status()
    items = re.findall(r'data-date="([^"]+)"[^<]*<p>[^<]*</p>\s*<a href=\'([^\']+\.pdf)\'[^>]*>([^<]*)</a>', r.text)
    rows = []
    for d, u, t in items:
        try: day = datetime.strptime(d.strip(), "%b %d, %Y").date()
        except ValueError: continue
        rows.append({"Announced": day, "Url": PRESS_BASE + u if u.startswith("/") else u,
                     "Release": os.path.basename(u)[:-4], "Title": " ".join(t.split()).replace("&amp;", "&")})
    df = pd.DataFrame(rows).drop_duplicates("Release")
    return df[df.Announced >= pd.Timestamp(PRESS_FROM).date()]


MENTIONS_500 = re.compile(r"(CNX|NIFTY)\s*500", re.I)


def fetch_pdf(rel, url):
    """Download one press release PDF. Returns "ok", "missing on NSE's site", or None (network trouble: retried next run)."""
    pdf = os.path.join(PRS_DIR, rel + ".pdf")
    if os.path.exists(pdf): return "ok"
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60); break
        except requests.RequestException:
            time.sleep(5)
    else:
        return None
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return "missing on NSE's site"
    open(pdf, "wb").write(r.content)
    return "ok"


def read_pdf(rel):
    """Read a downloaded PDF's text. Keeps the text only if it mentions the Nifty 500; the PDF is always deleted."""
    import pdfplumber
    pdf, txt = os.path.join(PRS_DIR, rel + ".pdf"), os.path.join(PRS_DIR, rel + ".txt")
    try:
        logging.disable(logging.CRITICAL)
        with pdfplumber.open(pdf) as p: text = "\n".join(pg.extract_text() or "" for pg in p.pages)
    except Exception:
        text = ""
    finally:
        logging.disable(logging.NOTSET)
    os.remove(pdf)
    if not text.strip(): return "no readable text"
    if not MENTIONS_500.search(undouble(text)): return "not about Nifty 500"
    open(txt, "w", encoding="utf-8").write(text)
    return "Nifty 500 - text kept"


def release_text(rel):
    txt = os.path.join(PRS_DIR, rel + ".txt")
    return open(txt, encoding="utf-8").read() if os.path.exists(txt) else ""


PREFIX = r"(?:\(?[A-Za-z]{1,3}[\.\)]|\(?\d+\)?[\).:]?)"     # "1)", "(2)", "A.", "c)", "iv)"
HDR = re.compile(r"^\s*" + PREFIX + r"?\s*(?:S&P\s*)?(CNX|NIFTY)\s*500\s*(?:Index)?\s*:?\s*$", re.I)
NEXT_HDR = re.compile(r"^\s*(?:\(?[A-Za-z]{1,3}[\.\)]|\(?\d{1,2}[\).:])\s*.*?(Index|NIFTY|CNX)", re.I)   # numbered heading
BARE_HDR = re.compile(r"^\s*(S&P\s*)?(CNX|NIFTY)\b.*Index\s*$", re.I)
END = re.compile(r"^\s*(Place:|Date:|For and on behalf|About )", re.I)
ROW = re.compile(r"^\s*(\d+)\s+(.+?)\s+([A-Z0-9][A-Z0-9&\-\.]*)\s*$")
EFFECTIVE = re.compile(r"effective\s+(?:from|on)?\s*(?:the\s+)?([A-Z][a-z]+\.?\s+\d{1,2},?\s*\d{4})", re.I)
OTHER_INDEX = re.compile(r"(CNX|NIFTY)\s*(Midcap|Smallcap|100|200|Nifty|50\b|IT|Bank|Media|Infra|FMCG|Energy|Pharma|PSE|MNC|Service)", re.I)


def undouble(text):
    """Some PDFs print bold text with every letter doubled ("eexxcclluuddeedd")."""
    def fix(w):
        core, tail = re.match(r"^(.*?)([:.,;]?)$", w).groups()
        return core[::2] + tail if len(core) >= 4 and len(core) % 2 == 0 and core[::2] == core[1::2] else w
    return "\n".join(" ".join(fix(w) for w in l.split(" ")) for l in text.splitlines())


def to_date(s):
    s = re.sub(r",\s*", ", ", re.sub(r"\s+", " ", s.replace(".", "")))
    if ", " not in s: s = re.sub(r"(\d{1,2}) (\d{4})", r"\1, \2", s)
    for f in ("%B %d, %Y", "%b %d, %Y"):
        try: return datetime.strptime(s, f).date()
        except ValueError: pass
    return None


def nearest_date(lines, i):
    """The 'effective from <date>' written nearest to line i, before or after it (releases put it either side)."""
    best = None
    for k, l in enumerate(lines):
        joined = " ".join(lines[k:k + 2])                 # a date can wrap onto the next line
        for m in EFFECTIVE.finditer(re.sub(r"\s+", " ", joined)):
            d = to_date(m.group(1))
            if d and (best is None or abs(k - i) < best[0]): best = (abs(k - i), d)
    return best[1] if best else None


def parse_release(rel, announced, title, text):
    """Nifty 500 exclusions / inclusions in one press release. Each section takes the last
    'effective from <date>' written before it (one release can have parts with different dates)."""
    text = undouble(text)
    if not re.search(r"(CNX|NIFTY)\s*500", text, re.I): return []
    lines = text.splitlines()
    t = re.search(r"w\.?e\.?f\.?\s*(.+?\d{4})", title)
    default = to_date(t.group(1)) if t else None
    if not any(HDR.match(l) for l in lines) and not OTHER_INDEX.search(re.sub(r"(CNX|NIFTY)\s*500", "", text, flags=re.I)):
        lines = ["CNX 500 Index"] + lines            # a release about the Nifty 500 only, with no section heading
    rows, i = [], 0
    while i < len(lines):
        if not HDR.match(lines[i]): i += 1; continue
        eff = nearest_date(lines, i) or default
        mode, j = None, i + 1
        while j < len(lines) and not END.match(lines[j]) and not BARE_HDR.match(lines[j]) \
                and not (NEXT_HDR.match(lines[j]) and not ROW.match(lines[j])):
            l = lines[j]
            if re.search(r"being\s+exclud", l, re.I): mode = "Exclude"
            elif re.search(r"being\s+includ", l, re.I): mode = "Include"
            elif mode:
                r = ROW.match(l)
                if r and (r.group(3) in ("LTD", "LTD.", "LIMITED") or not re.search(r"[A-Z]{2}", r.group(3))): r = None
                if r:
                    rows.append((rel, announced, eff, mode, r.group(3), r.group(2)))
                else:
                    n = re.match(r"^\s*(\d+)\s+(.*\S)\s*$", l)       # company name only (older releases)
                    if n and re.fullmatch(r"[A-Z0-9&\-]{3,}", n.group(2)):     # symbol alone, name wrapped to other lines
                        rows.append((rel, announced, eff, mode, n.group(2), "")); j += 1; continue
                    if n and len(n.group(2)) > 4 and n.group(2)[0].isupper():
                        name = n.group(2)
                        sym = next((v for k, v in NAME_TO_SYMBOL.items() if name.startswith(k)), None)
                        m = re.search(r"\s([A-Z0-9&\-]{3,})\s+[A-Z][a-z]+ \d{1,2}, \d{4}$", name)   # "Name SYMBOL <date>"
                        sym = sym or (m.group(1) if m else "?" + name)
                        rows.append((rel, announced, eff, mode, sym, name))
            j += 1
        i = j
    return rows


def name_key(name):
    n = name.lower().replace("limited", "ltd").replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\bltd\b.*$", "", n))


def names_to_symbols(changes, copies):
    """Older releases give only the company name: find its symbol in the saved list copies (closest copy wins)."""
    import difflib
    known = {}
    for d in sorted(copies, reverse=True):
        for r in copies[d].fillna("").to_dict("records"):
            known[name_key(str(r.get("Company Name", "")))] = r["Symbol"]
    for i, r in changes[changes.Symbol.str.startswith("?")].iterrows():
        m = difflib.get_close_matches(name_key(r.Company), list(known), 1, 0.9)
        if m: changes.at[i, "Symbol"] = known[m[0]]; changes.at[i, "Note"] = "symbol matched by company name"
    return changes


def all_changes(copies):
    """Every Nifty 500 change: reads new press releases only, keeps the result in nifty500_changes.csv."""
    os.makedirs(PRS_DIR, exist_ok=True)
    try:
        releases = press_list()
    except Exception as e:
        if not os.path.exists(CHANGES): sys.exit(f"Could not read NSE's press release list ({e}) and no saved changes exist.")
        print(f"  could not read NSE's press release list ({e}); using the changes already saved")
        return load_changes()
    todo = releases[~releases.Title.str.contains(SKIP_TITLES)]
    log = pd.read_csv(READ_LOG, dtype=str) if os.path.exists(READ_LOG) else pd.DataFrame(columns=["Release", "Announced", "Title", "Result"])
    done = set(log.Release)
    new = [r for r in todo.itertuples() if r.Release not in done]
    if new:
        from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
        print(f"  downloading and reading {len(new)} new press releases, {WORKERS} at a time...")
        with ThreadPoolExecutor(WORKERS) as ex:
            got = list(ex.map(lambda r: fetch_pdf(r.Release, r.Url), new))
        ok = [r for r, g in zip(new, got) if g == "ok"]
        with ProcessPoolExecutor(WORKERS) as ex:
            res = dict(zip([r.Release for r in ok], ex.map(read_pdf, [r.Release for r in ok])))
        add = [{"Release": r.Release, "Announced": r.Announced, "Title": r.Title, "Result": res.get(r.Release, g)}
               for r, g in zip(new, got) if g is not None]           # network failures are not logged: retried next run
        log = pd.concat([log, pd.DataFrame(add)], ignore_index=True).sort_values("Announced")
        log.to_csv(READ_LOG, index=False)
        unread = [a for a in add if a["Result"] == "no readable text"]
        if unread:
            print("  WARNING: press release(s) with no readable text (a scanned image?). Check by hand whether they change the Nifty 500:")
            for a in unread: print(f"    {a['Announced']}  {a['Release']}  {a['Title']}")
    rows = []
    for r in todo.itertuples():
        if os.path.exists(os.path.join(PRS_DIR, r.Release + ".txt")):
            rows += parse_release(r.Release, r.Announced, r.Title, release_text(r.Release))
    ch = pd.DataFrame(rows, columns=["Release", "Announced", "Effective", "Action", "Symbol", "Company"])
    ch["Note"] = ""
    ch = names_to_symbols(ch, copies)
    for rel, act, sym, why in CANCELLED:
        hit = (ch.Release == rel) & ((ch.Action == act) if act else True) & ((ch.Symbol == sym) if sym else True)
        ch = ch[~hit]
    man = pd.DataFrame([{"Release": "MANUAL", "Announced": "", "Effective": pd.Timestamp(e).date(),
                         "Action": a, "Symbol": s, "Company": c, "Note": why} for e, a, s, c, why in MANUAL_CHANGES])
    ch = pd.concat([ch, man], ignore_index=True).drop_duplicates(["Effective", "Action", "Symbol"])
    ch = ch.sort_values(["Effective", "Action", "Symbol"]).reset_index(drop=True)
    ch.to_csv(CHANGES, index=False)
    bad = ch[(ch.Symbol.str.startswith("?") | ch.Effective.isna()) & (pd.to_datetime(ch.Announced, errors="coerce") >= pd.Timestamp(COPIES_FROM))]
    if len(bad):
        print(f"  WARNING: {len(bad)} change(s) without a symbol or date (add to NAME_TO_SYMBOL):")
        print(bad[["Release", "Action", "Symbol"]].to_string(index=False))
    return load_changes()


def load_changes():
    ch = pd.read_csv(CHANGES, dtype=str).fillna("")
    ch["Effective"] = pd.to_datetime(ch.Effective, errors="coerce")
    return ch[~ch.Symbol.str.startswith("?") & ch.Effective.notna()]


# ---------------------------------------------------------------- symbol renames
_REN = None


def renames():
    global _REN
    if _REN is None:
        try:
            r = requests.get(SYMBOL_CHANGES_URL, headers=HEADERS, timeout=30); r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig", "ignore")))
            df.to_csv(SYMBOL_CHANGES, index=False)
        except Exception:
            if not os.path.exists(SYMBOL_CHANGES): sys.exit("Could not get NSE's symbol-change list and no saved copy exists.")
            df = pd.read_csv(SYMBOL_CHANGES)
        df = df.iloc[:, :4]; df.columns = ["Company", "Old", "New", "From"]
        df["Old"], df["New"] = df.Old.astype(str).str.strip(), df.New.astype(str).str.strip()
        df["From"] = pd.to_datetime(df.From.astype(str).str.strip(), format="%d-%b-%Y", errors="coerce")
        extra = pd.DataFrame([{"Company": "", "Old": o, "New": n, "From": pd.Timestamp(d)} for o, n, d in EXTRA_RENAMES])
        df = pd.concat([df, extra], ignore_index=True)
        _REN = {}
        for r in df.dropna(subset=["From"]).sort_values("From").itertuples():
            _REN.setdefault(r.Old, []).append((r.From, r.New))
    return _REN


def symbol_as_of(sym, since, until):
    """Follow renames that happened after `since` and on or before `until`."""
    ren = renames()
    for _ in range(10):
        hit = [(f, n) for f, n in ren.get(sym, []) if since < f <= until]
        if not hit: break
        since, sym = hit[0][0] - pd.Timedelta(days=1), hit[0][1]
    return sym


# ---------------------------------------------------------------- rebuild a list on a date
def rebuild(day, copies, changes, start=None):
    """Members on `day`: nearest saved copy on or before it (or `start`), plus the changes after it.
    Returns {symbol on day: source}, the copy date used and the number of changes applied."""
    base = start or max((d for d in copies if d <= day), default=None)
    if base is None:
        sys.exit(f"No saved list copy on or before {day.date()} (oldest is {min(copies).date()}).")
    # a copy can lag NSE by a few days (an old symbol after a rename, a change effective that day not yet
    # shown), so renames from 60 days before it are followed and changes effective ON its date are applied
    # (applying a change the copy already shows does nothing)
    members = {symbol_as_of(s, base - pd.Timedelta(days=60), base): f"list copy {base.date()}" for s in copies[base].Symbol}
    todo = changes[(changes.Effective >= base) & (changes.Effective <= day)]
    todo = todo.assign(o=todo.Action.map({"Exclude": 0, "Include": 1})).sort_values(["Effective", "o"])
    last = base
    for r in todo.itertuples():
        members = {symbol_as_of(s, last, r.Effective): src for s, src in members.items()}
        last = r.Effective
        sym = symbol_as_of(r.Symbol, pd.Timestamp(r.Announced) - pd.Timedelta(days=1), r.Effective)
        if r.Action == "Exclude": members.pop(sym, None)
        else: members[sym] = f"press release {r.Release} (effective {r.Effective.date()})"
    members = {symbol_as_of(s, last, day): src for s, src in members.items()}
    return members, base, len(todo)


def details(members, day, copies, changes):
    """Company name, industry and ISIN for each member, from the nearest copy that has the symbol."""
    info = {}
    for d in sorted(copies, key=lambda d: abs((d - day).days), reverse=True):   # nearest copy written last, so it wins
        for r in copies[d].fillna("").to_dict("records"):
            info[symbol_as_of(r["Symbol"], d, day) if d < day else r["Symbol"]] = r
    rows = []
    today = pd.Timestamp(date.today())
    for s, src in sorted(members.items()):
        r = info.get(s, {})
        name = r.get("Company Name", "")
        if not name:
            c = changes[changes.Symbol == s]
            name = c.Company.iloc[-1] if len(c) else ""
        rows.append({"Symbol": s, "Symbol today": symbol_as_of(s, day, today), "Company Name": str(name).strip(),
                     "Industry": str(r.get("Industry", "")).strip(), "ISIN Code": str(r.get("ISIN Code", "")).strip(),
                     "Source": src})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- filter: ordinary shares only
def why_removed(row):
    """Why a list entry is not an ordinary share ("" = keep it)."""
    sym = str(row.get("Symbol", ""))
    isin = str(row.get("ISIN Code", "") or "").strip()
    series = str(row.get("Series", "") or "").strip()
    if sym.upper().startswith("DUMMY"):
        return "NSE placeholder (DUMMY symbol), not a stock"
    if series in NON_SHARE_SERIES or (isin.startswith("IN") and len(isin) == 12 and isin[7:9] != "01"):
        return "REIT/InvIT units, not shares"
    return ""


def remove_non_shares(df):
    """(kept rows, removed rows with a Reason column)."""
    df = df.fillna("")
    why = df.apply(why_removed, axis=1) if len(df) else pd.Series(dtype=str)
    removed = df[why != ""].assign(Reason=why[why != ""])
    return df[why == ""].reset_index(drop=True), removed


def log_removed(by_list):
    """Write the removals of the lists just saved into output/removed_from_lists.csv (their old rows replaced)."""
    cols = ["List", "Symbol", "Company Name", "ISIN Code", "Reason"]
    old = pd.read_csv(REMOVED, dtype=str).fillna("") if os.path.exists(REMOVED) else pd.DataFrame(columns=cols)
    old = old[~old.List.isin(list(by_list))]
    new = [r.assign(List=label).reindex(columns=cols).fillna("") for label, r in by_list.items() if len(r)]
    d = pd.concat([old] + new, ignore_index=True).sort_values(["List", "Symbol"])
    d.to_csv(REMOVED, index=False)


def main_on(day, copies=None, changes=None, quiet=False):
    if copies is None:
        print("Updating the saved list copies and press releases (only new ones are downloaded)...")
        copies = saved_copies(); changes = all_changes(copies)
    members, base, n = rebuild(day, copies, changes)
    df = details(members, day, copies, changes)
    df, removed = remove_non_shares(df)
    out = os.path.join(RESULT_DIR, f"nifty500_{day.date()}.csv")
    df.to_csv(out, index=False)
    added = df[df.Source.str.startswith("press")]
    if quiet:
        return len(df), base, removed
    log_removed({str(day.date()): removed})
    print(f"\nNifty 500 list on {day.date()}")
    print(f"Started from NSE's list as saved on {base.date()}, then applied {n} announced change(s) up to {day.date()}")
    print(f"Stocks: {len(df)}" + ("" if 495 <= len(df) <= 505 else "   WARNING: expected about 500, check the changes file"))
    print(f"Added by press releases since that copy: {len(added)}")
    if len(removed): print(f"Removed (not ordinary shares): {', '.join(removed.Symbol + ' (' + removed.Reason + ')')}")
    print(f"Saved:  {out}")


def main_many(dates_file, start, end):
    """One list per date in a CSV of dates (column "Last trading day", or the first date column), between
    months start and end. NSE and the archive are checked for updates once, not once per date."""
    if not os.path.exists(dates_file):
        sys.exit(f"Dates file not found: {os.path.normpath(dates_file)}" +
                 ("\nRun get_month_ends.py first (in ../Get month ends dates)." if dates_file == MONTH_ENDS else ""))
    d = pd.read_csv(dates_file, dtype=str)
    col = "Last trading day" if "Last trading day" in d else d.columns[0]
    days = pd.to_datetime(d[col])
    days = sorted(x for x in days if (not start or x.to_period("M") >= pd.Period(start, "M"))
                  and (not end or x.to_period("M") <= pd.Period(end, "M")))
    today = pd.Timestamp(date.today())
    future = [x for x in days if x > today]
    days = [x for x in days if x <= today]
    if future: print(f"Skipped (not come yet): {', '.join(str(x.date()) for x in future)}")
    if not days: sys.exit("No dates to build in that range.")
    print("Updating the saved list copies and press releases (only new ones are downloaded)...")
    copies = saved_copies(); changes = all_changes(copies)
    print(f"Building {len(days)} lists, {days[0].date()} to {days[-1].date()}...")
    odd, removed = [], {}
    for day in days:
        n, _, rem = main_on(day, copies, changes, quiet=True)
        removed[str(day.date())] = rem
        if not 495 <= n <= 505: odd.append(f"{day.date()} ({n} stocks)")
    log_removed(removed)
    print(f"Saved {len(days)} lists to {RESULT_DIR}")
    print("All lists have 495-505 stocks." if not odd else "WARNING, unusual size: " + ", ".join(odd))
    gone = pd.concat([r.assign(List=k) for k, r in removed.items()] or [pd.DataFrame(columns=["Symbol", "Reason", "List"])],
                     ignore_index=True)
    if len(gone):
        print(f"Removed (not ordinary shares), see {os.path.basename(REMOVED)}:")
        for (sym, why), g in gone.groupby(["Symbol", "Reason"]):
            ls = sorted(g.List)
            print(f"  {sym:<12} {why}   in {len(ls)} list(s): {ls[0]}" + (f" to {ls[-1]}" if len(ls) > 1 else ""))


def main_check():
    """Rebuild each saved copy from the one before it and show the differences."""
    copies = saved_copies(); changes = all_changes(copies)
    days = sorted(copies)
    print(f"\n{'Saved copy':<12} {'From copy':<12} {'Changes':>7}  Result")
    for prev, day in zip(days, days[1:]):
        members, _, n = rebuild(day, copies, changes, start=prev)
        today = pd.Timestamp(date.today())          # compare on today's symbols, so a rename on the copy's day is not a difference
        real = {symbol_as_of(s, day - pd.Timedelta(days=30), today) for s in copies[day].Symbol}
        got = {symbol_as_of(s, day - pd.Timedelta(days=30), today) for s in members}
        miss, extra = sorted(real - got), sorted(got - real)
        res = "exact match" if not miss and not extra else f"missing {miss}  extra {extra}"
        if miss or extra:     # NSE's file sometimes still shows the old list on the day a change takes effect
            m2, _, _ = rebuild(day - pd.Timedelta(days=1), copies, changes, start=prev)
            if {symbol_as_of(x, day - pd.Timedelta(days=30), today) for x in m2} == real:
                res = f"exact match (NSE's file was not yet updated for the change effective that day: {extra} in, {miss} out)"
        print(f"{day.date()!s:<12} {prev.date()!s:<12} {n:>7}  {res}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Nifty 500 list: latest, or as it was on a date")
    ap.add_argument("--date", help="YYYY-MM-DD: rebuild the list as it was on this date (default: today's list)")
    ap.add_argument("--check", action="store_true", help="rebuild every saved copy from the one before it and show differences")
    ap.add_argument("--month-ends", action="store_true", help="one list per date in ../Get month ends dates/output/month_end_dates.csv")
    ap.add_argument("--dates-file", help="path to a CSV of dates: one list per date")
    ap.add_argument("--start", help="with --month-ends / --dates-file: first month, YYYY-MM")
    ap.add_argument("--end", help="with --month-ends / --dates-file: last month, YYYY-MM")
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True); os.makedirs(RESULT_DIR, exist_ok=True)
    if a.check: main_check()
    elif a.month_ends: main_many(MONTH_ENDS, a.start, a.end)
    elif a.dates_file: main_many(a.dates_file, a.start, a.end)
    elif a.date: main_on(pd.Timestamp(a.date))
    else: main()
