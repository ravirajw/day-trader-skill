#!/usr/bin/env python3
"""The opening-range breakout loop (trader-scanner): every breakout into a sorted Excel sheet.

    python3 loop.py run                     # runs 9:36 to 10:56 AM IST, one a 5-minute bar
    python3 loop.py run --last-run 11:31    # a later last run
    python3 loop.py run --force             # rebuild today's sheet now, even after 10:56 (runs 9:36 to 10:56 in one go)
    python3 loop.py stop                    # from another terminal: stop the running loop
    python3 loop.py show                    # today's sheet, sorted, in the terminal
    python3 loop.py run --offline wall5m.json.gz --tm-offline FOLDER --now "2026-10-19 09:30" --symbols TSLA

One minute after each 5-minute bar closes (9:36, 9:41, ... 10:56) it checks the stocks
from trader/watchlist-daily.json that have not broken out yet. A breakout is the first
completed bar to CLOSE above the opening-range high (the same rule as scan.py orb).
Every stock that broke out moves to the sheet, once, and is not checked again that day:

  entry   the breakout bar's close
  stop    the opening-range midpoint, (range high + range low) / 2, rounded down
  target1 entry + min_reward_risk x (entry - stop), on the price step    (config)
  target2 entry + max_reward_risk x (entry - stop), on the price step    (config)
  qty     the smaller of  max_loss_per_trade_usd / (entry - stop)  and
                          max_order_usd / entry, rounded down           (config)
  odds    the Time Machine on entry, stop and Target 1: 10,000 replays to flat_by, run for the
          new breakouts in parallel

Nothing is filtered out. After each run the whole sheet is sorted: "Strongly toward the
target" on top, then "Leans toward the target", break-even, toward the stop, and rows
whose Time Machine failed at the bottom (a failure is not retried). Within one verdict,
the clearer the lean, the higher the row.

Files: trader/orb-YYYY-MM-DD.xlsx (the sheet, rewritten after every run: close and
reopen it in Excel to see the update), trader/loop.json (today's state: a restart picks
up where it left off; trader/watchlist-daily.json is never changed), trader/loop.stop
(written by `loop.py stop`). Nothing is ever placed: choosing and placing is yours.

Free Yahoo data, unofficial, can lag: a stock missing the newest bar is checked again at
the next run, and its "Found at" is later than its breakout time.
"""

import argparse
import concurrent.futures
import datetime
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import time
import zipfile
from argparse import Namespace
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    if not os.path.exists(path):
        sys.exit("loop.py needs %s: keep the trader-* folders side by side" % path)
    spec = importlib.util.spec_from_file_location("trader_loop_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan = _load("trader-scanner", "scan")
ex = _load("trader-exit", "exit")
tm = _load("trader-timemachine", "timemachine")

STATE = "trader/loop.json"
SHEET = "trader/orb-%s.xlsx"
STOP = "trader/loop.stop"
CONFIG = "trader/config.json"
DELAY = 60          # seconds after a bar closes before the run asks Yahoo for it
LAST_RUN = "10:56"  # the last run, IST
WORKERS = 4         # Time Machines at once: more than this and Yahoo's free endpoint starts answering 429
SLEEP = time.sleep  # tests swap this out
NOTE = "Matched a rule, not a recommendation. Nothing was placed: choosing and placing is yours."

# sort groups, best first; the verdict line starts with one of these
GROUPS = [("Strongly toward the target", 0), ("Leans toward the target", 1), ("Close to", 2),
          ("Leans toward the stop", 3), ("Strongly toward the stop", 4), ("No replay", 5)]
FAILED = 6


class LoopError(Exception):
    pass


# ---------------------------------------------------------------- clock

class Clock(object):
    """Real time, or a simulated clock (--now / --offline) that jumps instead of sleeping."""

    def __init__(self, start_utc=None):
        self.sim = start_utc is not None
        self.t = start_utc

    def now(self):
        return self.t if self.sim else scan.parse_now(None)

    def sleep_until(self, utc, stopped):
        """Sleep until utc. False if a stop came first."""
        if self.sim:
            if stopped():
                return False
            self.t = max(self.t, utc)
            return True
        while True:
            if stopped():
                return False
            left = (utc - self.now()).total_seconds()
            if left <= 0:
                return True
            SLEEP(min(left, 2.0))


def bar_closes(day, or_minutes, last_run, delay):
    """Naive IST datetimes of every bar close the loop checks: from the first bar after the
    range, every 5 minutes, to the newest close whose run (close + delay) is at or before last_run."""
    first = scan.slot(day, or_minutes // scan.BAR_MIN) + datetime.timedelta(minutes=scan.BAR_MIN)
    last = datetime.datetime.combine(day, last_run) - datetime.timedelta(seconds=delay)
    s = scan.session(day)
    if s:
        last = min(last, datetime.datetime.combine(day, s[1]))
    out, t = [], first
    while t <= last:
        out.append(t)
        t += datetime.timedelta(minutes=scan.BAR_MIN)
    return out


def due(closes, now_ist, delay, done):
    """The newest close whose run is due (close + delay <= now) and not done yet, or None."""
    ready = [c for c in closes if c + datetime.timedelta(seconds=delay) <= now_ist]
    if ready and (done is None or ready[-1] > done):
        return ready[-1]
    return None


# ---------------------------------------------------------------- state

def fresh_state(day):
    return {"created_by": "trader-loop", "date": day.isoformat(), "rows": [], "last_scan": None}


def upgrade_row(r, rules=None):
    """A row saved before the sheet had two targets ("target" at "rr", no "target1"): its one target goes
    under the column whose R it was built at, by the config's rules; otherwise into the verdict, so no
    price is shown under the wrong R. Its odds were worked out for that old target, so unless that is
    Target 1 the verdict says so (once: an upgraded row has "target1" and is left alone)."""
    if "target1" in r:
        return r
    t, rr = r.get("target"), r.get("rr")
    r.update({"target1": None, "target2": None, "rr1": None, "rr2": None})
    if rules:
        r["rr1"], r["rr2"] = rules["rr1"], rules["rr2"]
    if t is None or not rules:
        return r
    if rr == rules["rr1"]:
        r["target1"] = t
        return r
    if rr == rules["rr2"]:
        r["target2"] = t
        note = "these odds are for the %gR target (Target 2), not Target 1" % rr
    else:
        note = "target %.2f at %gR; these odds are for it, not Target 1" % (t, rr)
    r["verdict"] = (r.get("verdict") or "") + " [saved before two targets: %s]" % note
    return r


def load_state(path, day, rules=None):
    """Today's state, or a fresh one (a file from another day starts over)."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except ValueError as e:
            raise LoopError("%s is not valid JSON: %s (move it away to start fresh)" % (path, e))
        if isinstance(doc, dict) and doc.get("date") == day.isoformat() and isinstance(doc.get("rows"), list):
            doc.setdefault("last_scan", None)
            for r in doc["rows"]:
                upgrade_row(r, rules)
            return doc
    return fresh_state(day)


def moved(state):
    return {r["symbol"] for r in state["rows"]}


# ---------------------------------------------------------------- one breakout

def _num(cfg, key):
    v = cfg.get(key)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise LoopError("trader/config.json needs \"%s\" (a number above 0)" % key)
    if v <= 0:
        raise LoopError("trader/config.json needs \"%s\" above 0, it is %s" % (key, cfg.get(key)))
    return v


def rules_from(cfg):
    """The four numbers the sheet needs from the config: Target 1 is min_reward_risk R, Target 2 max_reward_risk R."""
    rules = {"max_risk": _num(cfg, "max_loss_per_trade_usd"), "max_amount": _num(cfg, "max_order_usd"),
             "rr1": _num(cfg, "min_reward_risk"), "rr2": _num(cfg, "max_reward_risk")}
    if rules["rr1"] >= rules["rr2"]:
        raise LoopError('trader/config.json needs "min_reward_risk" (%g) under "max_reward_risk" (%g): they are '
                        "Target 1 and Target 2" % (rules["rr1"], rules["rr2"]))
    return rules


def qty_for(entry, stop, max_risk, max_amount):
    """(qty, limited_by): the smaller of max risk / (entry - stop) and max amount / entry, rounded down.
    Worked in paise so 1000 / 2.50 is 400, not 399."""
    e, s = int(round(entry * 100)), int(round(stop * 100))
    risk = e - s
    if risk <= 0 or e <= 0:
        return 0, "stop not under entry"
    by_risk = int(round(max_risk * 100)) // risk
    by_amount = int(round(max_amount * 100)) // e
    if by_risk <= by_amount:
        return by_risk, "max risk"
    return by_amount, "max amount"


def minutes_of(label):
    """'9:40 AM' -> 580, for sorting."""
    try:
        t = ex.parse_clock(label, "time")
        return t.hour * 60 + t.minute
    except Exception:
        return 0


def group_of(verdict):
    for head, g in GROUPS:
        if verdict.startswith(head):
            return g
    return FAILED


def plan_row(hit, day, found_at, now_utc, rules, a):
    """The row for one breakout, before the Time Machine: entry, stop, target, qty."""
    sym = hit["symbol"]
    row = {"symbol": sym, "breakout_at": hit["at"], "found_at": found_at, "or_high": hit["or_high"],
           "or_low": hit["or_low"], "entry": hit["price"], "stop": None, "target1": None, "target2": None,
           "rr1": rules["rr1"], "rr2": rules["rr2"],
           "qty": None, "limited_by": "", "risk_per_share": None, "max_loss": None, "cost": None,
           "p_target": None, "p_stop": None, "p_neither": None, "verdict": "", "group": FAILED, "lean": 0.0,
           "bar_index": hit["bar_index"]}
    ns = Namespace(from_path=None, symbol=sym, entry=hit["price"], or_high=hit["or_high"], or_low=hit["or_low"],
                   or_minutes=a.or_minutes, setup="orb", date=day.isoformat(), time=hit["at"],
                   entry_bar=hit["bar_index"], interval="5m", side="buy", stop="or-mid",
                   target="%sR" % ("%g" % rules["rr1"]), flat_by=None, config=a.config, offline=None, now=None)
    try:
        plan, _ = ex.build_plan(ns, now_utc)
    except ex.PlanError as e:
        row["verdict"] = "Plan failed: %s" % e
        return row, None
    qty, why = qty_for(plan["entry"], plan["stop"], rules["max_risk"], rules["max_amount"])
    # Target 2 from the same entry and stop, on the price step for its own price (as trader-exit rounds)
    raw2 = ex.D(plan["entry"]) + ex.D(str(rules["rr2"])) * ex.D(plan["risk_per_share"])
    target2 = float(ex.to_tick(raw2, ex.tick_size(raw2, sym)))
    row.update({"entry": plan["entry"], "stop": plan["stop"], "target1": plan["target"], "target2": target2,
                "risk_per_share": plan["risk_per_share"], "qty": qty, "limited_by": why,
                "max_loss": round(qty * plan["risk_per_share"], 2), "cost": round(qty * plan["entry"], 2)})
    return row, plan


def run_tm(row, plan, now_utc, a):
    """Fill the row's odds and verdict from the Time Machine. Never raises."""
    if plan is None:
        return row
    try:
        text = tm.read_offline(a.tm_offline, row["symbol"]) if a.tm_offline else tm.fetch_chart(row["symbol"])
        doc = tm.time_machine(text, tm.normalize_plan(plan, "loop"), now_utc, replays=a.replays, seed=a.seed,
                              until=plan["flat_by"], source="offline" if a.tm_offline else "yahoo")
    except Exception as e:  # TMError, or anything odd in one stock: that row fails, the run goes on
        row["verdict"] = "Time Machine failed: %s" % e
        return row
    mc = doc["mc"]
    lv = tm.lean(mc)
    notes = doc.get("verdict_notes") or []
    row.update({"p_target": round(mc["p_target"], 4), "p_stop": round(mc["p_stop"], 4),
                "p_neither": round(mc.get("p_neither", 0), 4),
                "verdict": " ".join([doc["verdict"]] + notes), "group": group_of(doc["verdict"]),
                "lean": round(lv[0], 4) if lv else 0.0})
    return row


def sort_rows(rows):
    rows.sort(key=lambda r: (r["group"], -r["lean"], minutes_of(r["breakout_at"]), r["symbol"]))
    return rows


# ---------------------------------------------------------------- the sheet (.xlsx, no pip install)

COLUMNS = [  # header, key, style, width
    ("Rank", "rank", "int", 6), ("Symbol", "symbol", "text", 15), ("Breakout time", "breakout_at", "text", 13),
    ("Found at", "found_at", "text", 10), ("Range high", "or_high", "price", 11), ("Range low", "or_low", "price", 11),
    ("Entry", "entry", "price", 11), ("Stop", "stop", "price", 11), ("Target 1", "target1", "price", 15),
    ("Target 2", "target2", "price", 15), ("Qty", "qty", "int", 8), ("Qty limited by", "limited_by", "text", 13),
    ("Risk/share", "risk_per_share", "price", 11), ("Max loss", "max_loss", "price", 11),
    ("Cost", "cost", "price", 13), ("Target 1 first", "p_target", "pct", 13), ("Stop first", "p_stop", "pct", 10),
    ("Neither", "p_neither", "pct", 9), ("Verdict", "verdict", "verdict", 90),
]
STYLE = {"text": 0, "header": 1, "price": 2, "int": 3, "pct": 4, "good": 5, "bad": 6, "grey": 7}
STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0%"/></numFmts>'
    '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    '<font><sz val="11"/><color rgb="FF808080"/><name val="Calibri"/></font></fonts>'
    '<fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFDDEEFF"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD9F2D9"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF8D7D7"/><bgColor indexed="64"/></patternFill></fill>'
    '</fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="8">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


def col_letter(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, value, style):
    if value is None or value == "":
        return '<c r="%s" s="%d"/>' % (ref, style)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
            ref, style, escape(str(value)))
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return '<c r="%s" s="%d"/>' % (ref, style)
    return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, style, repr(value) if isinstance(value, float) else value)


def column_heads(rules=None):
    """The header row: the two target columns carry their R from the config ("Target 1 (1.78R)")."""
    heads = []
    for head, key, _, _ in COLUMNS:
        if rules and key in ("target1", "target2"):
            head = "%s (%gR)" % (head, rules["rr" + key[-1]])
        heads.append(head)
    return heads


def other_r_note(r, rules):
    """A row found while the config had other R values (changed mid-morning) says so in its verdict cell."""
    if not rules or r.get("rr1") is None or (r.get("rr1"), r.get("rr2")) == (rules["rr1"], rules["rr2"]):
        return ""
    return " [this row's targets are %gR and %gR: the config changed after it was found]" % (
        r["rr1"], r["rr2"] if r.get("rr2") is not None else 0)


def sheet_xml(rows, rules=None):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
           'state="frozen"/></sheetView></sheetViews><cols>']
    for i, (_, _, _, w) in enumerate(COLUMNS):
        out.append('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w))
    out.append('</cols><sheetData><row r="1">')
    for i, head in enumerate(column_heads(rules)):
        out.append(_cell("%s1" % col_letter(i), head, STYLE["header"]))
    out.append("</row>")
    for n, r in enumerate(rows, start=2):
        out.append('<row r="%d">' % n)
        for i, (_, key, kind, _) in enumerate(COLUMNS):
            v = n - 1 if key == "rank" else r.get(key)
            if kind == "verdict":
                v = (v or "") + other_r_note(r, rules)
                style = STYLE["good"] if r["group"] <= 1 else STYLE["bad"] if r["group"] in (3, 4) else (
                    STYLE["grey"] if r["group"] == FAILED else STYLE["text"])
            else:
                style = STYLE[kind]
            out.append(_cell("%s%d" % (col_letter(i), n), v, style))
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_xlsx(path, rows, title, rules=None):
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % escape(title[:31]),
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>',
        "xl/styles.xml": STYLES_XML,
        "xl/worksheets/sheet1.xml": sheet_xml(rows, rules),
    }
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                     "xl/styles.xml", "xl/worksheets/sheet1.xml"):
            z.writestr(name, files[name])
    os.replace(tmp, path)


# ---------------------------------------------------------------- one run

def symbols_for(a, day, offline_data):
    if a.symbols:
        syms = [s for s in a.symbols.replace(",", " ").upper().split() if s]
    elif a.watchlist:
        syms = scan.symbols_from_file(a.watchlist)
    elif offline_data is not None:
        syms = sorted(offline_data)
    else:
        if not os.path.exists(scan.DAILY_WATCHLIST):
            raise LoopError("no %s yet: run /trader-scanner daily first (after the close or before the open)"
                            % scan.DAILY_WATCHLIST)
        try:
            scan.check_daily_list_fresh(scan.DAILY_WATCHLIST, day)
        except scan.ScanError as e:
            raise LoopError(str(e))
        syms = scan.symbols_from_file(scan.DAILY_WATCHLIST)
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if not out:
        raise LoopError("no stocks to watch")
    return out


def fetch_charts(symbols):
    """Today's full 5-minute chart for each stock, one request each. -> ({SYM: series}, {SYM: error})."""
    data, failed = {}, {}
    for i, sym in enumerate(symbols):
        if i:
            scan.SLEEP(scan.PAUSE)
        try:
            data[sym] = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, "1d", "%dm" % scan.BAR_MIN)), sym)
        except scan.ScanError as e:
            failed[sym] = str(e)
    return data, failed


def scan_once(cutoff, day, pending, a, offline_data):
    """Breakout hits among `pending` by `cutoff` (naive IST), oldest first, and the scan counts."""
    if offline_data is not None:
        data, confirm = offline_data, None
    else:
        data, _ = scan.fetch_spark(pending, "1d", "%dm" % scan.BAR_MIN, scan.PAUSE)

        def confirm(sym):
            SLEEP(scan.PAUSE)
            return scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, "1d", "%dm" % scan.BAR_MIN)), sym)
    hits, counts, failed = scan.scan_orb(data, pending, day, cutoff, a.or_minutes, a.min_price, 0, confirm, 0)
    return hits, counts, failed


def short_row(r):
    sym = r["symbol"]
    if r["qty"] is None:
        return "%-14s breakout %s  %s" % (sym, r["breakout_at"], r["verdict"])
    head = r["verdict"].split(":")[0]
    if "target1" not in r and r.get("target") is not None:   # a row saved before two targets, as it is
        targets = "target %s (%gR)" % (scan.money(r["target"], sym), r.get("rr") or 0)
    else:
        targets = "t1 %s t2 %s" % tuple(scan.money(r.get(k), sym) if r.get(k) is not None else "-"
                                        for k in ("target1", "target2"))
    return "%-14s breakout %-8s entry %s stop %s %s qty %d  %s" % (
        sym, r["breakout_at"], scan.money(r["entry"], sym), scan.money(r["stop"], sym), targets, r["qty"], head)


def run_once(cutoff, day, symbols, state, rules, clock, a, offline_data, out, tally=None):
    """One run at `cutoff`: new breakouts -> rows, the Time Machine in parallel, sort, write. -> new rows,
    or None when the check failed. `tally` (a dict) adds up how many stocks had usable bars."""
    done = moved(state)
    pending = [s for s in symbols if s not in done]
    found_at = scan.clock_label(cutoff + datetime.timedelta(seconds=a.delay))  # the run's time, e.g. 9:36 AM
    if not pending:
        return []
    try:
        hits, counts, _ = scan_once(cutoff, day, pending, a, offline_data)
        if tally is not None:
            tally["scanned"] = tally.get("scanned", 0) + counts.get("scanned", 0)
    except scan.ScanError as e:
        out("%s  check failed: %s (trying again at the next run)" % (found_at, e))
        return None  # a failed check, not "no breakouts": --force stops on it
    now_utc = clock.now()
    planned = [plan_row(h, day, found_at, now_utc, rules, a) for h in hits]
    if planned:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
            new = list(pool.map(lambda rp: run_tm(rp[0], rp[1], now_utc, a), planned))
    else:
        new = []
    state["rows"].extend(new)
    sort_rows(state["rows"])
    left = len(symbols) - len(moved(state))
    gaps = counts.get("unusable") or 0
    out("%s run (%s bar)  checked %d stock%s: %d broke out%s; %d in the sheet; %d still watched."
        % (found_at, scan.clock_label(cutoff), len(pending), "" if len(pending) == 1 else "s", len(new),
           (", %d missing the newest bar (checked again next run)" % gaps) if gaps else "",
           len(state["rows"]), left))
    for r in new:
        out("          + " + short_row(r))
    if new:
        out("\a          sheet sorted; top: " + "; ".join(
            "%d. %s (%s)" % (i + 1, r["symbol"], r["verdict"].split(":")[0]) for i, r in enumerate(state["rows"][:3])))
    return new


# ---------------------------------------------------------------- commands

def keep_awake():
    """macOS: hold off idle sleep while this process runs. Returns the caffeinate process or None."""
    if sys.platform != "darwin" or not shutil.which("caffeinate"):
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
    except OSError:
        return None


def save(state, a, day, rules=None):
    if a.no_write:
        return
    scan.write_json(a.state, state)
    write_xlsx(a.sheet or SHEET % day.isoformat(), state["rows"], "ORB %s" % day.isoformat(), rules)


def cmd_run(a, out=print):
    sim = bool(a.now or a.offline)
    if a.offline and not a.now:
        raise LoopError("an offline replay needs --now \"YYYY-MM-DD HH:MM\" (IST)")
    clock = Clock(scan.parse_now(a.now) if sim else None)
    rules = rules_from(scan.read_config(a.config))
    try:
        last_run = scan.parse_hhmm(a.last_run, "--last-run")
    except scan.ScanError as e:
        raise LoopError(str(e))
    now_ist = scan.to_ist(clock.now())
    day = now_ist.date()
    ok, why = scan.trading_day(day)
    if not ok:
        raise LoopError("No session on %s (%s): the market is closed. Nothing to watch." % (scan.day_label(day), why))
    closes = bar_closes(day, a.or_minutes, last_run, a.delay)
    forced = None
    if a.force:  # rebuild now: every run from the first up to the newest due one, in one go, into a fresh sheet
        first_run = scan.slot(day, a.or_minutes // scan.BAR_MIN) + datetime.timedelta(
            minutes=scan.BAR_MIN, seconds=a.delay)
        if not closes:
            raise LoopError("--last-run %s is before the first run (%s): nothing to check with --force."
                            % (last_run.strftime("%H:%M"), scan.clock_label(first_run)))
        forced = [c for c in closes if c + datetime.timedelta(seconds=a.delay) <= now_ist]
        if not forced:
            raise LoopError("It is %s IST: nothing to check yet with --force, the first run is at %s."
                            % (scan.clock_label(now_ist), scan.clock_label(first_run)))
    elif not closes or now_ist > closes[-1] + datetime.timedelta(seconds=a.delay):
        raise LoopError("It is %s IST, after the last run (%s). Run it on the next trading day from 9:15."
                        % (scan.clock_label(now_ist), last_run.strftime("%H:%M")))
    offline_data = None
    if a.offline:
        try:
            offline_data, _ = scan.load_offline(a.offline)
        except scan.ScanError as e:
            raise LoopError(str(e))
    symbols = symbols_for(a, day, offline_data)
    if forced and offline_data is None:
        # one download of each stock's full 5-minute chart (highs and lows: Yahoo's quicker spark answer has
        # closes only), replayed bar by bar: a later gap cannot hide an earlier breakout, and a failed
        # download stops here, before today's sheet is touched
        offline_data, failed = fetch_charts(symbols)
        if failed:  # one more try after a pause (Yahoo's free endpoint answers 429 when asked too fast)
            out("--force: trying %s again in %g s." % (", ".join(sorted(failed)), scan.BACKOFF))
            scan.SLEEP(scan.BACKOFF)
            more, failed = fetch_charts(sorted(failed))
            offline_data.update(more)
        if failed:  # all or nothing: a sheet missing stocks must not replace today's
            raise LoopError("--force: no chart for %s (%s). Today's sheet was left as it was; run it again in a "
                            "minute." % (", ".join(sorted(failed)), "; ".join(sorted(set(failed.values())))))
    state = fresh_state(day) if forced else load_state(a.state, day, rules)  # --force overwrites today's
    sheet = a.sheet or SHEET % day.isoformat()
    if os.path.exists(a.stop_file) and not forced:  # a stop request belongs to a running loop
        os.remove(a.stop_file)
    done = None
    if state.get("last_scan"):
        try:
            done = datetime.datetime.strptime(state["last_scan"][:16], "%Y-%m-%d %H:%M")
        except ValueError:
            done = None
    sym0 = symbols[0]
    if forced:
        out("--force: rebuilding today's sheet in one go for %d stock%s on %s, replaying the runs from %s to %s "
            "IST. Target 1 %gR (min_reward_risk), Target 2 %gR (max_reward_risk); the Time Machine runs on "
            "Target 1. Today's sheet and %s are overwritten when it finishes."
            % (len(symbols), "" if len(symbols) == 1 else "s", scan.day_label(day),
               scan.clock_label(forced[0] + datetime.timedelta(seconds=a.delay)),
               scan.clock_label(forced[-1] + datetime.timedelta(seconds=a.delay)), rules["rr1"], rules["rr2"],
               a.state))
    else:
        out("Watching %d stock%s for opening-range breakouts on %s: the %d-minute range (9:15 to %s), a run %d s "
        "after each 5-minute bar closes, %s to %s IST. Target 1 %gR (min_reward_risk), Target 2 %gR (max_reward_risk); the Time Machine runs on "
        "Target 1; qty by max risk %s and max "
        "amount %s. %d already in the sheet."
        % (len(symbols), "" if len(symbols) == 1 else "s", scan.day_label(day), a.or_minutes,
           scan.clock_label(scan.slot(day, a.or_minutes // scan.BAR_MIN)), a.delay,
           scan.clock_label(closes[0] + datetime.timedelta(seconds=a.delay)),
           scan.clock_label(closes[-1] + datetime.timedelta(seconds=a.delay)), rules["rr1"], rules["rr2"],
           scan.money(rules["max_risk"], sym0), scan.money(rules["max_amount"], sym0), len(state["rows"])))
        out("Sheet: %s (rewritten after each run: close and reopen it in Excel). Stop: Ctrl+C, or "
            "`python3 loop.py stop` from another terminal." % sheet)
    awake = None if sim or a.no_caffeinate or forced else keep_awake()
    stopped = lambda: os.path.exists(a.stop_file)
    reason = "the %s run was the last" % last_run.strftime("%H:%M")
    finished = not forced  # a --force rebuild is saved only when it finishes
    try:
        if forced:
            tally = {}
            for c in forced:
                if run_once(c, day, symbols, state, rules, clock, a, offline_data, out, tally) is None:
                    raise LoopError("--force stopped: the %s check failed (above). Today's sheet was left as "
                                    "it was." % scan.clock_label(c))
                state["last_scan"] = "%s %s IST" % (day.isoformat(), c.strftime("%H:%M"))
                if len(moved(state)) >= len(symbols):
                    break
            if not state["rows"] and not tally.get("scanned"):
                raise LoopError("--force: no stock had usable bars for %s at any run (Yahoo sent no bars of that "
                                "day, or the charts are stale). Today's sheet was left as it was."
                                % scan.day_label(day))
            finished = True
            reason = "--force rebuilt today's sheet, runs %s to %s" % (
                scan.clock_label(forced[0] + datetime.timedelta(seconds=a.delay)),
                scan.clock_label(forced[-1] + datetime.timedelta(seconds=a.delay)))
        while not forced:
            now_ist = scan.to_ist(clock.now())
            c = due(closes, now_ist, a.delay, done)
            if c is not None:
                run_once(c, day, symbols, state, rules, clock, a, offline_data, out)
                done = c
                state["last_scan"] = "%s %s IST" % (day.isoformat(), c.strftime("%H:%M"))
                save(state, a, day, rules)
                if len(moved(state)) >= len(symbols):
                    reason = "every stock on the list broke out"
                    break
                continue
            nxt = [x for x in closes if done is None or x > done]
            if not nxt:
                break
            wake = scan.ist_to_utc(nxt[0] + datetime.timedelta(seconds=a.delay))
            if not clock.sleep_until(wake, stopped):
                reason = "stopped by %s" % a.stop_file
                break
    except KeyboardInterrupt:
        reason = "stopped with Ctrl+C"
    finally:
        if awake is not None:
            awake.terminate()
        if os.path.exists(a.stop_file) and not forced:
            os.remove(a.stop_file)
        if finished:
            save(state, a, day, rules)
    if not finished:
        out("--force %s before it finished: today's sheet was left as it was." % reason)
        return 1
    out("Loop ended: %s. %d of %d stocks broke out and are in the sheet. %s"
        % (reason, len(moved(state)), len(symbols), NOTE))
    if not a.no_write:
        out("Sheet: %s. State: %s." % (sheet, a.state))
    return 0 if state["rows"] else 1


def cmd_stop(a, out=print):
    d = os.path.dirname(a.stop_file)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(a.stop_file, "w", encoding="utf-8") as fh:
        fh.write(scan.parse_now(None).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
    out("Asked the loop to stop (%s): it stops within a few seconds, or when the run it is in finishes."
        % a.stop_file)
    return 0


def cmd_show(a, out=print):
    if not os.path.exists(a.state):
        out("No loop has run yet (%s is missing)." % a.state)
        return 1
    with open(a.state, encoding="utf-8") as fh:
        state = json.load(fh)
    rows = state.get("rows") or []
    out("Loop of %s, last run on the %s bar: %d in the sheet." % (state.get("date"), state.get("last_scan") or "none",
                                                                   len(rows)))
    for i, r in enumerate(rows):
        out("%3d. %s" % (i + 1, short_row(r)))
    return 0 if rows else 1


def main(argv=None, out=print):
    ap = argparse.ArgumentParser(description="The opening-range breakout loop (trader-scanner): every breakout "
                                             "into a sorted Excel sheet with entry, stop, target, qty and odds.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("run", help="check the daily list every 5 minutes until the last run")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--symbols", help="comma-separated tickers instead of trader/watchlist-daily.json")
    g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json or a text file of tickers")
    p.add_argument("--last-run", default=LAST_RUN, help="the last run, HH:MM IST (default %s)" % LAST_RUN)
    p.add_argument("--delay", type=int, default=DELAY, help="seconds after a bar closes before a run (60)")
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--min-price", type=float, default=5.0)
    p.add_argument("--replays", type=int, default=10000)
    p.add_argument("--seed", type=int, default=19)
    p.add_argument("--workers", type=int, default=WORKERS, help="Time Machines at once (4)")
    p.add_argument("--config", default=CONFIG)
    p.add_argument("--state", default=STATE)
    p.add_argument("--sheet", help="the .xlsx to write (default trader/orb-YYYY-MM-DD.xlsx)")
    p.add_argument("--stop-file", default=STOP)
    p.add_argument("--now", help='replay from this IST time on a simulated clock, e.g. "2026-10-19 09:30"')
    p.add_argument("--offline", metavar="PATH", help="saved 5-minute Yahoo charts to check (a file or folder)")
    p.add_argument("--tm-offline", metavar="PATH", help="saved 60-day charts for the Time Machine "
                                                        "(a file, or a folder of <SYM>-60d5m.json)")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--no-caffeinate", action="store_true", help="do not keep the Mac awake")
    p.add_argument("--force", action="store_true",
                   help="rebuild today's sheet now, even after --last-run: one download, then every run from 9:36 "
                        "to --last-run replayed in one go; today's trader/loop.json and sheet are overwritten when "
                        "it finishes (a failure or Ctrl+C leaves them as they were)")
    p = sub.add_parser("stop", help="stop a running loop")
    p.add_argument("--stop-file", default=STOP)
    p = sub.add_parser("show", help="today's sheet, sorted, in the terminal")
    p.add_argument("--state", default=STATE)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "stop": cmd_stop, "show": cmd_show}[a.cmd](a, out)
    except (LoopError, scan.ScanError) as e:
        print("loop.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
