"""Put the recorded US sessions on the NSE clock, so the real bars in tests/fixtures
can test the NSE tools without editing the fixtures.

Each bar keeps its price and volume; only its time moves: the New York wall-clock
time minus 15 minutes, read as IST. So the 9:30 ET open becomes the 9:15 IST
open, 10:10 ET becomes 9:55 IST, and the last three US bars (15:45 to 15:55 ET)
land at or after the 15:30 IST close. `days` also moves the date (for a session
recorded on an NSE holiday, such as Dussehra on 2026-10-20).
"""

import datetime
import gzip
import json
import os

EPOCH = datetime.datetime(1970, 1, 1)
SHIFT = datetime.timedelta(minutes=15)


def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def _ny_offset(utc):
    """-4 in US daylight time, -5 otherwise (the rule the fixtures were recorded under)."""
    y = utc.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7, 0))
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6, 0))
    return -4 if start <= utc < end else -5


def nse_ts(ts, days=0):
    """Unix seconds of a US bar -> Unix seconds of the same bar on the NSE clock."""
    utc = EPOCH + datetime.timedelta(seconds=int(ts))
    ny = utc + datetime.timedelta(hours=_ny_offset(utc))
    ist = ny - SHIFT + datetime.timedelta(days=days)
    return int((ist - datetime.timedelta(hours=5, minutes=30) - EPOCH).total_seconds())


def shift_doc(doc, days=0):
    """Every Yahoo "timestamp" list and "regularMarketTime" in doc, moved to the NSE clock."""
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k == "timestamp" and isinstance(v, list):
                out[k] = [nse_ts(t, days) if isinstance(t, (int, float)) else t for t in v]
            elif k == "regularMarketTime" and isinstance(v, (int, float)):
                out[k] = nse_ts(v, days)
            else:
                out[k] = shift_doc(v, days)
        return out
    if isinstance(doc, list):
        return [shift_doc(v, days) for v in doc]
    return doc


def nse_copy(src, dst_dir, days=0, name=None):
    """Write src (a .json or .json.gz chart file) shifted to the NSE clock into dst_dir; return the new path."""
    gz = src.endswith(".gz")
    with (gzip.open(src, "rt", encoding="utf-8") if gz else open(src, encoding="utf-8")) as fh:
        doc = json.load(fh)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, name or os.path.basename(src))
    with (gzip.open(dst, "wt", encoding="utf-8") if dst.endswith(".gz") else open(dst, "w", encoding="utf-8")) as fh:
        json.dump(shift_doc(doc, days), fh)
    return dst
