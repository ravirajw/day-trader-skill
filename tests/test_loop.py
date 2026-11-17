import argparse
import atexit
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr
from xml.dom import minidom

from _load import load, FIX
from _nse import nse_copy

loop = load("trader-scanner", "loop")

# The reel's real charts on the NSE clock (tests/_nse.py): the 234-chart wall of Mon Oct 19 2026,
# recorded to 10:15 AM IST, and TSLA's 60 days of 5-minute bars for the Time Machine.
NSE_DIR = tempfile.mkdtemp()
atexit.register(shutil.rmtree, NSE_DIR, True)
WALL = nse_copy(os.path.join(FIX, "trader-scanner", "wall5m-2026-10-19.json.gz"), NSE_DIR)
TM_DIR = os.path.join(NSE_DIR, "tm")
nse_copy(os.path.join(FIX, "trader-timemachine", "TSLA-60d5m.json.gz"), TM_DIR)
DT = datetime.datetime
DAY = datetime.date(2026, 10, 19)
CONFIG = {"budget": 5000, "max_order_usd": 4000, "max_loss_per_trade_usd": 50, "max_reward_risk": 2,
          "min_reward_risk": 1.0, "flat_by": "15:15"}


def row(symbol, verdict, lean=0.0, at="10:00 AM"):
    return {"symbol": symbol, "verdict": verdict, "group": loop.group_of(verdict), "lean": lean, "breakout_at": at}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.cfg = os.path.join(self.d, "config.json")
        self.write_config(CONFIG)
        self.state = os.path.join(self.d, "loop.json")
        self.sheet = os.path.join(self.d, "orb.xlsx")
        self.stop = os.path.join(self.d, "loop.stop")

    def write_config(self, cfg):
        with open(self.cfg, "w") as fh:
            json.dump(cfg, fh)

    def run_loop(self, *extra, **kw):
        argv = ["run", "--offline", WALL, "--tm-offline", TM_DIR, "--now", kw.get("now", "2026-10-19 09:30"),
                "--config", self.cfg, "--state", self.state, "--sheet", self.sheet, "--stop-file", self.stop,
                "--replays", "2000"] + list(extra)
        lines, err = [], io.StringIO()
        on_line = kw.get("on_line")

        def out(text):
            lines.append(text)
            if on_line:
                on_line(text)
        with redirect_stderr(err):
            code = loop.main(argv, out)
        return code, "\n".join(lines), err.getvalue()

    def saved(self):
        with open(self.state) as fh:
            return json.load(fh)


class Qty(unittest.TestCase):
    def test_max_risk_decides_when_the_stop_is_wide(self):
        self.assertEqual(loop.qty_for(364.18, 358.32, 1000, 30000), (82, "max amount"))
        self.assertEqual(loop.qty_for(364.18, 358.32, 50, 4000), (8, "max risk"))

    def test_max_amount_decides_when_the_stop_is_tight(self):
        # the gotcha: a 1-rupee stop on a 1,000-rupee stock would be 1,000 shares on risk alone
        self.assertEqual(loop.qty_for(1000.0, 999.0, 1000, 30000), (30, "max amount"))

    def test_rounds_down_exactly_in_paise(self):
        self.assertEqual(loop.qty_for(102.50, 100.00, 1000, 10 ** 9), (400, "max risk"))
        self.assertEqual(loop.qty_for(102.51, 100.00, 1000, 10 ** 9), (398, "max risk"))

    def test_a_share_riskier_than_the_max_risk_is_qty_0(self):
        self.assertEqual(loop.qty_for(500.0, 400.0, 50, 30000), (0, "max risk"))

    def test_the_config_must_have_the_four_numbers(self):
        self.assertEqual(loop.rules_from(CONFIG), {"max_risk": 50.0, "max_amount": 4000.0, "rr1": 1.0, "rr2": 2.0})
        for k in ("max_loss_per_trade_usd", "max_order_usd", "min_reward_risk", "max_reward_risk"):
            cfg = dict(CONFIG)
            del cfg[k]
            with self.assertRaises(loop.LoopError) as cm:
                loop.rules_from(cfg)
            self.assertIn(k, str(cm.exception))


    def test_min_reward_risk_must_be_under_max(self):
        for rr1 in (2, 3):
            with self.assertRaises(loop.LoopError) as cm:
                loop.rules_from(dict(CONFIG, min_reward_risk=rr1))
            self.assertIn("min_reward_risk", str(cm.exception))
            self.assertIn("max_reward_risk", str(cm.exception))


class Sorting(unittest.TestCase):
    def test_strong_target_on_top_failures_at_the_bottom(self):
        rows = [row("FAIL", "Time Machine failed: Yahoo answered HTTP 500"),
                row("STOP", "Strongly toward the stop: ...", -0.3),
                row("EVEN", "Close to break-even: ...", 0.01),
                row("LEAN", "Leans toward the target: ...", 0.08),
                row("WEAK", "Leans toward the stop: ...", -0.1),
                row("STRONG", "Strongly toward the target: ...", 0.2),
                row("NONE", "No replay reached the target or the stop by 3:15 PM: ..."),
                row("PLAN", "Plan failed: the stop is too tight")]
        self.assertEqual([r["symbol"] for r in loop.sort_rows(rows)],
                         ["STRONG", "LEAN", "EVEN", "WEAK", "STOP", "NONE", "FAIL", "PLAN"])

    def test_within_a_verdict_the_clearer_lean_then_the_earlier_breakout_wins(self):
        rows = [row("B", "Strongly toward the target: ...", 0.2, "10:00 AM"),
                row("A", "Strongly toward the target: ...", 0.4, "10:30 AM"),
                row("C", "Strongly toward the target: ...", 0.2, "9:40 AM")]
        self.assertEqual([r["symbol"] for r in loop.sort_rows(rows)], ["A", "C", "B"])


class Schedule(unittest.TestCase):
    def test_runs_from_9_36_to_10_56(self):
        closes = loop.bar_closes(DAY, 15, datetime.time(10, 56), 60)
        self.assertEqual(closes[0], DT(2026, 10, 19, 9, 35))
        self.assertEqual(closes[-1], DT(2026, 10, 19, 10, 55))
        self.assertEqual(len(closes), 17)

    def test_due_waits_for_the_delay_and_catches_up_to_the_newest_bar(self):
        closes = loop.bar_closes(DAY, 15, datetime.time(10, 56), 60)
        self.assertIsNone(loop.due(closes, DT(2026, 10, 19, 9, 35, 59), 60, None))
        self.assertEqual(loop.due(closes, DT(2026, 10, 19, 9, 36), 60, None), DT(2026, 10, 19, 9, 35))
        self.assertEqual(loop.due(closes, DT(2026, 10, 19, 10, 2), 60, None), DT(2026, 10, 19, 10, 0))
        self.assertIsNone(loop.due(closes, DT(2026, 10, 19, 10, 2), 60, DT(2026, 10, 19, 10, 0)))

    def test_simulated_clock_jumps_and_honours_a_stop(self):
        c = loop.Clock(DT(2026, 10, 19, 4, 0))
        self.assertTrue(c.sleep_until(DT(2026, 10, 19, 4, 6), lambda: False))
        self.assertEqual(c.now(), DT(2026, 10, 19, 4, 6))
        self.assertFalse(c.sleep_until(DT(2026, 10, 19, 4, 11), lambda: True))


class Replay(Base):
    def test_every_breakout_goes_to_the_sheet_once_sorted_and_failures_are_not_retried(self):
        # TSLA and MSFT both break out on the 10:00 bar; only TSLA has 60-day data for the Time Machine
        code, text, _ = self.run_loop("--symbols", "TSLA,MSFT")
        self.assertEqual(code, 0)
        self.assertIn("9:36 AM run (9:35 AM bar)  checked 2 stocks: 0 broke out", text)
        self.assertIn("10:01 AM run (10:00 AM bar)  checked 2 stocks: 2 broke out; 2 in the sheet; 0 still watched.",
                      text)
        self.assertIn("every stock on the list broke out", text)
        rows = self.saved()["rows"]
        self.assertEqual([r["symbol"] for r in rows], ["TSLA", "MSFT"])
        t, m = rows
        # entry = the 10:00 bar's close, stop = range midpoint, Target 1 = 1R (min_reward_risk),
        # Target 2 = 2R (max_reward_risk), qty = min(50 / 5.86, 4000 / 364.18) = 8
        self.assertEqual((t["entry"], t["stop"], t["target1"], t["target2"], t["qty"], t["limited_by"]),
                         (364.18, 358.32, 370.04, 375.9, 8, "max risk"))
        self.assertEqual((t["breakout_at"], t["found_at"], t["max_loss"], t["cost"]),
                         ("10:00 AM", "10:01 AM", 46.88, 2913.44))
        # the Time Machine runs on Target 1 (1R): target first 27.5 % vs stop first 21.5 %
        self.assertTrue(t["verdict"].startswith("Leans toward the target"))
        self.assertTrue(m["verdict"].startswith("Time Machine failed: no MSFT-60d5m.json"))
        self.assertEqual(m["group"], loop.FAILED)
        self.assertEqual(text.count("+ MSFT"), 1)

    def test_targets_follow_the_config(self):
        self.write_config(dict(CONFIG, min_reward_risk=1.5, max_reward_risk=3.1))
        self.run_loop("--symbols", "TSLA")
        t = self.saved()["rows"][0]
        # 364.18 + 1.5 x 5.86 = 372.97; 364.18 + 3.1 x 5.86 = 382.346
        self.assertEqual((t["target1"], t["target2"], t["rr1"], t["rr2"]), (372.97, 382.35, 1.5, 3.1))

    def test_target_2_uses_its_own_nse_price_band(self):
        hit = {"symbol": "X.NS", "at": "10:00 AM", "or_high": 995.0, "or_low": 985.2, "price": 995.35,
               "bar_index": 8}
        rules = {"max_risk": 900.0, "max_amount": 30000.0, "rr1": 1.78, "rr2": 3.45}
        a = argparse.Namespace(or_minutes=15, config=self.cfg)
        r, plan = loop.plan_row(hit, DAY, "10:01 AM", DT(2026, 10, 19, 4, 31), rules, a)
        # stop = or-mid 990.10, risk 5.25: 995.35 + 1.78 x 5.25 = 1004.695 and + 3.45 x 5.25 = 1013.4625,
        # both above 1,000, so on the 0.10 step
        self.assertEqual((r["stop"], r["target1"], r["target2"]), (990.1, 1004.7, 1013.5))
        self.assertEqual(plan["target"], 1004.7)

    def test_the_sheet_is_a_real_xlsx_in_sorted_order(self):
        self.run_loop("--symbols", "TSLA,MSFT")
        with zipfile.ZipFile(self.sheet) as z:
            names = set(z.namelist())
            for n in names:
                minidom.parseString(z.read(n))  # every part is well-formed XML
            sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn('name="ORB 2026-10-19"', z.read("xl/workbook.xml").decode("utf-8"))
        self.assertEqual(names, {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                                 "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml"})
        self.assertIn('state="frozen"', sheet)
        for head in ("Rank", "Symbol", "Breakout time", "Entry", "Stop", "Target 1 (1R)", "Target 2 (2R)", "Qty",
                     "Target 1 first", "Verdict"):
            self.assertIn("<t xml:space=\"preserve\">%s</t>" % head, sheet)
        for gone in ("R:R", "Target"):
            self.assertNotIn("<t xml:space=\"preserve\">%s</t>" % gone, sheet)
        self.assertLess(sheet.index(">TSLA<"), sheet.index(">MSFT<"))
        self.assertIn('<c r="E2" s="2"><v>362.18</v></c>', sheet)  # range high, as a number

    def test_a_restart_does_not_add_a_stock_again(self):
        self.run_loop("--symbols", "TSLA,NVDA", "--last-run", "10:06")  # TSLA breaks out at 10:00, NVDA at 10:10
        code, text, _ = self.run_loop("--symbols", "TSLA,NVDA", now="2026-10-19 10:07")
        self.assertIn("1 already in the sheet", text)
        self.assertIn("+ NVDA", text)
        self.assertNotIn("+ TSLA", text)
        self.assertEqual(sorted(r["symbol"] for r in self.saved()["rows"]), ["NVDA", "TSLA"])

    def test_started_late_found_at_is_the_run_not_the_breakout(self):
        self.run_loop("--symbols", "TSLA", now="2026-10-19 10:12")
        t = self.saved()["rows"][0]
        self.assertEqual((t["breakout_at"], t["found_at"]), ("10:00 AM", "10:11 AM"))

    def test_the_stop_file_ends_the_loop_after_the_run_it_is_in(self):
        stop = self.stop

        def on_line(text):
            if "9:41 AM run" in text:
                open(stop, "w").close()
        code, text, _ = self.run_loop("--symbols", "TSLA", on_line=on_line)
        self.assertIn("Loop ended: stopped by %s" % stop, text)
        self.assertNotIn("9:46 AM run", text)
        self.assertFalse(os.path.exists(stop))
        self.assertEqual(self.saved()["last_scan"], "2026-10-19 09:40 IST")

    def test_ends_after_the_last_run(self):
        code, text, _ = self.run_loop("--symbols", "NVDA", "--last-run", "10:06")
        self.assertIn("10:06 AM run (10:05 AM bar)", text)
        self.assertNotIn("10:11 AM run", text)
        self.assertIn("Loop ended: the 10:06 run was the last. 0 of 1 stocks broke out", text)
        self.assertEqual(code, 1)


class Commands(Base):
    def test_stop_writes_the_stop_file(self):
        self.assertEqual(loop.main(["stop", "--stop-file", self.stop], [].append), 0)
        self.assertTrue(os.path.exists(self.stop))

    def test_show_prints_the_sheet_in_rank_order(self):
        self.run_loop("--symbols", "TSLA,MSFT")
        lines = []
        self.assertEqual(loop.main(["show", "--state", self.state], lines.append), 0)
        text = "\n".join(lines)
        self.assertIn("  1. TSLA", text)
        self.assertIn("  2. MSFT", text)

    def test_the_target_heads_come_from_the_config(self):
        self.assertEqual([h for h in loop.column_heads(None) if h.startswith("Target ")][:2], ["Target 1", "Target 2"])
        heads = loop.column_heads({"rr1": 1.78, "rr2": 3.45})
        self.assertIn("Target 1 (1.78R)", heads)
        self.assertIn("Target 2 (3.45R)", heads)

    def write_old_state(self, rr):
        # a loop.json saved earlier today, before the sheet had two targets: one target at max_reward_risk
        old = loop.fresh_state(DAY)
        old["rows"].append(dict(row("TSLA", "Strongly toward the stop: ..."), target=375.9, rr=rr, entry=364.18,
                                stop=358.32, qty=8))
        with open(self.state, "w") as fh:
            json.dump(old, fh)

    def test_an_old_row_target_goes_under_the_column_with_its_r(self):
        self.write_old_state(2.0)
        r = loop.load_state(self.state, DAY, loop.rules_from(CONFIG))["rows"][0]   # CONFIG: 1R and 2R
        self.assertEqual((r["target1"], r["target2"]), (None, 375.9))
        self.write_old_state(1.0)
        r = loop.load_state(self.state, DAY, loop.rules_from(CONFIG))["rows"][0]
        self.assertEqual((r["target1"], r["target2"]), (375.9, None))

    def test_an_old_row_with_another_r_keeps_its_target_in_the_verdict(self):
        self.write_old_state(3.45)
        r = loop.load_state(self.state, DAY, loop.rules_from(CONFIG))["rows"][0]
        self.assertEqual((r["target1"], r["target2"]), (None, None))
        self.assertIn("target 375.90 at 3.45R", r["verdict"])
        self.assertTrue(r["verdict"].startswith("Strongly toward the stop"))   # the note goes after the verdict

    def test_an_old_row_says_its_odds_are_for_its_old_target(self):
        self.write_old_state(2.0)   # built at 2R, which is Target 2 under CONFIG (1R / 2R)
        r = loop.load_state(self.state, DAY, loop.rules_from(CONFIG))["rows"][0]
        self.assertTrue(r["verdict"].startswith("Strongly toward the stop"))
        self.assertIn("these odds are for the 2R target (Target 2), not Target 1", r["verdict"])
        self.assertIn("these odds are for the 2R target", loop.sheet_xml([r], loop.rules_from(CONFIG)))
        self.write_old_state(1.0)   # built at 1R = Target 1: the odds are for Target 1, nothing to say
        r = loop.load_state(self.state, DAY, loop.rules_from(CONFIG))["rows"][0]
        self.assertNotIn("odds are for", r["verdict"])

    def test_the_old_row_note_is_added_once(self):
        self.write_old_state(2.0)
        for _ in range(3):
            doc = loop.load_state(self.state, DAY, loop.rules_from(CONFIG))
            with open(self.state, "w") as fh:
                json.dump(doc, fh)
        self.assertEqual(doc["rows"][0]["verdict"].count("saved before two targets"), 1)

    def test_show_reads_a_row_without_changing_it(self):
        self.write_old_state(2.0)
        with open(self.state) as fh:
            r = json.load(fh)["rows"][0]   # as saved, before any upgrade
        before = dict(r)
        text = loop.short_row(r)
        self.assertEqual(r, before)
        self.assertIn("Strongly toward the stop", text)
        self.assertNotIn("(saved before", text)

    def test_show_works_on_rows_saved_before_two_targets(self):
        self.write_old_state(3.45)
        lines = []
        self.assertEqual(loop.main(["show", "--state", self.state], lines.append), 0)
        self.assertIn("target $375.90 (3.45R)", "\n".join(lines))

    def test_a_row_found_under_other_r_values_says_so_in_the_sheet(self):
        r = dict(row("TSLA", "Leans toward the target: ..."), entry=364.18, stop=358.32, target1=372.97,
                 target2=382.35, rr1=1.5, rr2=3.1, qty=8)
        sheet = loop.sheet_xml([r], {"rr1": 1.78, "rr2": 3.45})
        self.assertIn("Target 1 (1.78R)", sheet)
        self.assertIn("this row's targets are 1.5R and 3.1R", sheet)
        self.assertNotIn("this row's targets", loop.sheet_xml([r], {"rr1": 1.5, "rr2": 3.1}))

    def test_a_state_from_another_day_starts_fresh(self):
        old = loop.fresh_state(datetime.date(2026, 10, 16))
        old["rows"].append(row("TSLA", "x"))
        with open(self.state, "w") as fh:
            json.dump(old, fh)
        self.assertEqual(loop.load_state(self.state, DAY)["rows"], [])


def wall_with_a_gap(symbol, hhmm):
    """The recorded wall with one bar of `symbol` blanked (Yahoo sometimes leaves a bar empty)."""
    import gzip
    with gzip.open(WALL, "rt") if WALL.endswith(".gz") else open(WALL) as fh:
        doc = json.load(fh)
    r = doc[symbol]["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    for i, t in enumerate(r["timestamp"]):
        if (DT.utcfromtimestamp(t) + datetime.timedelta(hours=5, minutes=30)).strftime("%H:%M") == hhmm:
            for k in q:
                q[k][i] = None
    path = os.path.join(NSE_DIR, "gap-%s-%s.json" % (symbol, hhmm.replace(":", "")))
    with open(path, "w") as fh:
        json.dump(doc, fh)
    return path


class Force(Base):
    def run_with(self, wall, *extra, **kw):
        argv = ["run", "--offline", wall, "--tm-offline", TM_DIR, "--now", kw.get("now", "2026-10-19 13:00"),
                "--config", self.cfg, "--state", self.state, "--sheet", self.sheet, "--stop-file", self.stop,
                "--replays", "2000"] + list(extra)
        lines, err = [], io.StringIO()
        with redirect_stderr(err):
            code = loop.main(argv, lines.append)
        return code, "\n".join(lines), err.getvalue()

    def test_force_finds_a_breakout_before_a_later_gap(self):
        # TSLA breaks out on the 10:00 bar and its 10:05 bar is empty: the morning loop finds it at 10:01,
        # and so must --force, which replays every bar from 9:36 up to the last run in one go
        gap = wall_with_a_gap("TSLA", "10:05")
        code, text, _ = self.run_with(gap, "--symbols", "TSLA", "--force", "--last-run", "10:11")
        self.assertEqual(code, 0)
        t = self.saved()["rows"][0]
        self.assertEqual((t["symbol"], t["breakout_at"], t["found_at"], t["entry"]), ("TSLA", "10:00 AM", "10:01 AM", 364.18))

    def test_force_live_reads_each_full_chart_not_the_closes_only_spark(self):
        # live (no --offline): Yahoo's spark answer has closes only, which cannot show a break of the range
        # high; --force must read each stock's full 5-minute chart, as the live loop's confirm step does
        import gzip
        with gzip.open(WALL, "rt") if WALL.endswith(".gz") else open(WALL) as fh:
            wall = json.load(fh)
        asked = []

        def fake_get(path, *args, **kw):
            asked.append(path)
            if "/spark" in path:
                raise AssertionError("--force must not use the closes-only spark answer")
            sym = path.split("/v8/finance/chart/")[1].split("?")[0]
            return json.dumps(wall[sym])
        for name, value in (("yahoo_get", fake_get), ("SLEEP", lambda s: None)):
            self.addCleanup(setattr, loop.scan, name, getattr(loop.scan, name))
            setattr(loop.scan, name, value)
        argv = ["run", "--tm-offline", TM_DIR, "--now", "2026-10-19 13:00", "--config", self.cfg, "--state",
                self.state, "--sheet", self.sheet, "--stop-file", self.stop, "--replays", "2000",
                "--symbols", "TSLA,MSFT", "--force", "--last-run", "10:11"]
        lines = []
        with redirect_stderr(io.StringIO()):
            code = loop.main(argv, lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(r["symbol"] for r in self.saved()["rows"]), ["MSFT", "TSLA"])
        self.assertEqual(len(asked), 2)                                # one chart per stock, once

    def fake_yahoo(self, fails):
        """Yahoo answering from the recorded wall; `fails` = {SYM: how many times its chart fails first}."""
        import gzip
        with gzip.open(WALL, "rt") if WALL.endswith(".gz") else open(WALL) as fh:
            wall = json.load(fh)
        left = dict(fails)
        sleeps = []

        def fake_get(path, *args, **kw):
            sym = path.split("/v8/finance/chart/")[1].split("?")[0]
            if left.get(sym, 0) > 0:
                left[sym] -= 1
                raise loop.scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s" % sym)
            return json.dumps(wall[sym])
        for name, value in (("yahoo_get", fake_get), ("SLEEP", sleeps.append)):
            self.addCleanup(setattr, loop.scan, name, getattr(loop.scan, name))
            setattr(loop.scan, name, value)
        return sleeps

    def force_live(self):
        argv = ["run", "--tm-offline", TM_DIR, "--now", "2026-10-19 13:00", "--config", self.cfg, "--state",
                self.state, "--sheet", self.sheet, "--stop-file", self.stop, "--replays", "2000",
                "--symbols", "TSLA,MSFT", "--force", "--last-run", "10:11"]
        lines, err = [], io.StringIO()
        with redirect_stderr(err):
            code = loop.main(argv, lines.append)
        return code, "\n".join(lines), err.getvalue()

    def test_a_download_that_fails_once_is_tried_again(self):
        sleeps = self.fake_yahoo({"MSFT": 1})
        code, text, _ = self.force_live()
        self.assertEqual(code, 0)
        self.assertEqual(sorted(r["symbol"] for r in self.saved()["rows"]), ["MSFT", "TSLA"])
        self.assertIn(loop.scan.BACKOFF, sleeps)                       # waited before the retry
        self.assertIn("trying MSFT again", text)

    def test_a_download_that_keeps_failing_leaves_todays_sheet_alone(self):
        self.run_loop("--symbols", "TSLA,MSFT")
        before = open(self.state).read()
        self.fake_yahoo({"MSFT": 2})
        code, _, err = self.force_live()
        self.assertEqual(code, 1)
        self.assertIn("no chart for MSFT", err)
        self.assertIn("Today's sheet was left as it was", err)
        self.assertEqual(open(self.state).read(), before)

    def test_charts_with_no_usable_bars_leave_the_sheet_alone(self):
        # the recorded wall is Mon 19 Oct; rebuilding Wed 21 Oct from it finds no bar of the 21st in any chart
        # (a stale or empty Yahoo answer looks the same): nothing usable, so nothing is overwritten
        day2 = loop.fresh_state(datetime.date(2026, 10, 21))
        day2["rows"].append(dict(row("TSLA", "Leans toward the target: ..."), target1=1.0, target2=2.0, rr1=1.0,
                                 rr2=2.0, entry=1.0, stop=0.5, qty=1))
        with open(self.state, "w") as fh:
            json.dump(day2, fh)
        before = open(self.state).read()
        code, _, err = self.run_loop("--symbols", "TSLA,MSFT", "--force", "--last-run", "10:11",
                                     now="2026-10-21 13:00")
        self.assertEqual(code, 1)
        self.assertIn("no stock had usable bars", err)
        self.assertIn("Today's sheet was left as it was", err)
        self.assertEqual(open(self.state).read(), before)

    def test_a_failed_check_leaves_todays_sheet_alone(self):
        self.run_loop("--symbols", "TSLA,MSFT")
        before = open(self.state).read()
        real = loop.scan_once

        def broken(*args, **kw):
            raise loop.scan.ScanError("Yahoo timed out")
        loop.scan_once = broken
        self.addCleanup(setattr, loop, "scan_once", real)
        code, text, err = self.run_loop("--symbols", "TSLA,MSFT", "--force", "--last-run", "10:11",
                                        now="2026-10-19 13:00")
        self.assertEqual(code, 1)
        self.assertIn("check failed: Yahoo timed out", text)
        self.assertIn("Today's sheet was left as it was", err)
        self.assertEqual(open(self.state).read(), before)

    def test_ctrl_c_leaves_todays_sheet_alone(self):
        self.run_loop("--symbols", "TSLA,MSFT")
        before = open(self.state).read()
        real = loop.run_tm

        def interrupted(*args, **kw):
            raise KeyboardInterrupt()
        loop.run_tm = interrupted
        self.addCleanup(setattr, loop, "run_tm", real)
        code, text, _ = self.run_loop("--symbols", "TSLA,MSFT", "--force", "--last-run", "10:11",
                                      now="2026-10-19 13:00")
        self.assertEqual(open(self.state).read(), before)
        self.assertIn("left as it was", text)

    def test_force_leaves_a_stop_request_for_another_loop(self):
        with open(self.stop, "w") as fh:
            fh.write("stop")
        self.run_loop("--symbols", "TSLA", "--force", "--last-run", "10:11", now="2026-10-19 13:00")
        self.assertTrue(os.path.exists(self.stop))

    def test_force_with_a_last_run_before_the_first_run(self):
        code, _, err = self.run_loop("--symbols", "TSLA", "--force", "--last-run", "09:30", now="2026-10-19 13:00")
        self.assertEqual(code, 1)
        self.assertIn("--last-run 09:30 is before the first run (9:36 AM)", err)

    def test_force_says_it_is_one_rebuild(self):
        code, text, _ = self.run_loop("--symbols", "TSLA", "--force", "--last-run", "10:11", now="2026-10-19 13:00")
        self.assertIn("--force: rebuilding today's sheet in one go", text)
        self.assertNotIn("Stop: Ctrl+C", text)

    def test_force_after_the_last_run_builds_the_morning_sheet_in_one_run(self):
        # 1 PM: a normal run refuses (after the last run); --force checks once, on the newest bar up to the
        # last run. The recorded day ends at 10:15 AM, so the last run here is 10:11 (the 10:10 bar).
        code, _, err = self.run_loop("--symbols", "TSLA,MSFT", now="2026-10-19 13:00")
        self.assertIn("after the last run (10:56)", err)
        code, text, _ = self.run_loop("--symbols", "TSLA,MSFT", "--force", "--last-run", "10:11",
                                      now="2026-10-19 13:00")
        self.assertEqual(code, 0)
        self.assertIn("9:36 AM run (9:35 AM bar)", text)               # replayed bar by bar, in one go
        self.assertIn("--force rebuilt today's sheet, runs 9:36 AM to 10:11 AM", text)
        rows = self.saved()["rows"]
        self.assertEqual(sorted(r["symbol"] for r in rows), ["MSFT", "TSLA"])
        t = [r for r in rows if r["symbol"] == "TSLA"][0]
        self.assertEqual((t["breakout_at"], t["found_at"], t["entry"], t["target1"], t["target2"]),
                         ("10:00 AM", "10:01 AM", 364.18, 370.04, 375.9))   # as the morning loop found it
        self.assertTrue(os.path.exists(self.sheet))

    def test_force_starts_fresh_and_overwrites_the_saved_state(self):
        old = loop.fresh_state(DAY)
        old["last_scan"] = "2026-10-19 10:55 IST"
        old["rows"].append(dict(row("TSLA", "Strongly toward the stop: old"), target1=1.0, target2=2.0, rr1=1.0,
                                rr2=2.0, entry=1.0, stop=0.5, qty=1))
        with open(self.state, "w") as fh:
            json.dump(old, fh)
        code, _, _ = self.run_loop("--symbols", "TSLA", "--force", "--last-run", "10:11", now="2026-10-19 13:00")
        self.assertEqual(code, 0)
        rows = self.saved()["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry"], 364.18)                     # the fresh row, not the saved one
        self.assertNotIn("old", rows[0]["verdict"])

    def test_force_with_the_default_last_run_checks_the_10_55_bar(self):
        code, text, _ = self.run_loop("--symbols", "TSLA", "--force", now="2026-10-19 13:00")
        self.assertEqual(code, 0)                       # found at 10:01, before the recorded day runs out at 10:15
        self.assertEqual(self.saved()["rows"][0]["found_at"], "10:01 AM")
        self.assertIn("runs 9:36 AM to 10:56 AM", text)

    def test_force_counts_only_bars_up_to_the_last_run(self):
        # TSLA breaks out on the 10:00 bar: with the last run at 9:56 it is not found
        code, text, _ = self.run_loop("--symbols", "TSLA", "--force", "--last-run", "09:56", now="2026-10-19 13:00")
        self.assertEqual(code, 1)                                      # an empty sheet ends with 1, as a normal run does
        self.assertIn("(9:55 AM bar)", text)
        self.assertEqual(self.saved()["rows"], [])

    def test_force_during_the_morning_checks_the_newest_closed_bar(self):
        code, text, _ = self.run_loop("--symbols", "TSLA", "--force", now="2026-10-19 10:07")
        self.assertEqual(code, 0)
        self.assertIn("runs 9:36 AM to 10:06 AM", text)               # up to the newest closed bar, 10:05
        self.assertNotIn("(10:10 AM bar)", text)

    def test_force_before_the_first_run_has_nothing_to_check(self):
        code, _, err = self.run_loop("--symbols", "TSLA", "--force", now="2026-10-19 09:33")
        self.assertEqual(code, 1)
        self.assertIn("nothing to check yet", err)

    def test_force_still_needs_a_trading_day(self):
        code, _, err = self.run_loop("--symbols", "TSLA", "--force", now="2026-10-17 13:00")
        self.assertIn("No session on Sat 2026-10-17", err)


class Refusals(Base):
    def test_no_session_on_a_weekend(self):
        code, _, err = self.run_loop("--symbols", "TSLA", now="2026-10-17 09:30")
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-17", err)

    def test_after_the_last_run(self):
        code, _, err = self.run_loop("--symbols", "TSLA", now="2026-10-19 10:58")
        self.assertIn("after the last run (10:56)", err)

    def test_a_config_without_max_reward_risk(self):
        cfg = dict(CONFIG)
        del cfg["max_reward_risk"]
        self.write_config(cfg)
        code, _, err = self.run_loop("--symbols", "TSLA")
        self.assertIn('needs "max_reward_risk"', err)

    def test_a_config_without_min_reward_risk(self):
        cfg = dict(CONFIG)
        del cfg["min_reward_risk"]
        self.write_config(cfg)
        code, _, err = self.run_loop("--symbols", "TSLA")
        self.assertIn('needs "min_reward_risk"', err)

    def test_offline_needs_a_clock(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = loop.main(["run", "--offline", WALL, "--config", self.cfg, "--state", self.state], [].append)
        self.assertEqual(code, 1)
        self.assertIn("needs --now", err.getvalue())

    def test_live_default_needs_the_daily_list_and_never_changes_it(self):
        cwd = os.getcwd()
        os.chdir(self.d)
        self.addCleanup(os.chdir, cwd)
        ns = argparse.Namespace(symbols=None, watchlist=None)
        with self.assertRaises(loop.LoopError) as cm:
            loop.symbols_for(ns, DAY, None)
        self.assertIn("run /trader-scanner daily first", str(cm.exception))
        os.makedirs("trader")
        daily = {"mode": "daily", "as_of": "2026-10-16 close", "hits": [{"symbol": "TSLA"}, {"symbol": "NVDA"}]}
        with open("trader/watchlist-daily.json", "w") as fh:
            json.dump(daily, fh)
        with open("trader/watchlist-daily.json") as fh:
            before = fh.read()
        self.assertEqual(loop.symbols_for(ns, DAY, None), ["TSLA", "NVDA"])
        with open("trader/watchlist-daily.json") as fh:
            self.assertEqual(fh.read(), before)


if __name__ == "__main__":
    unittest.main()
