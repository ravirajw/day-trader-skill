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

    def test_the_config_must_have_the_three_numbers(self):
        self.assertEqual(loop.rules_from(CONFIG), {"max_risk": 50.0, "max_amount": 4000.0, "rr": 2.0})
        for k in ("max_loss_per_trade_usd", "max_order_usd", "max_reward_risk"):
            cfg = dict(CONFIG)
            del cfg[k]
            with self.assertRaises(loop.LoopError) as cm:
                loop.rules_from(cfg)
            self.assertIn(k, str(cm.exception))


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
        # entry = the 10:00 bar's close, stop = range midpoint, target = 2R (max_reward_risk),
        # qty = min(50 / 5.86, 4000 / 364.18) = 8
        self.assertEqual((t["entry"], t["stop"], t["target"], t["qty"], t["limited_by"]),
                         (364.18, 358.32, 375.9, 8, "max risk"))
        self.assertEqual((t["breakout_at"], t["found_at"], t["max_loss"], t["cost"]),
                         ("10:00 AM", "10:01 AM", 46.88, 2913.44))
        self.assertTrue(t["verdict"].startswith("Strongly toward the stop"))
        self.assertTrue(m["verdict"].startswith("Time Machine failed: no MSFT-60d5m.json"))
        self.assertEqual(m["group"], loop.FAILED)
        self.assertEqual(text.count("+ MSFT"), 1)

    def test_target_follows_max_reward_risk(self):
        self.write_config(dict(CONFIG, max_reward_risk=3.1))
        self.run_loop("--symbols", "TSLA")
        t = self.saved()["rows"][0]
        self.assertEqual((t["target"], t["rr"]), (382.35, 3.1))  # 364.18 + 3.1 x 5.86 = 382.346

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
        for head in ("Rank", "Symbol", "Breakout time", "Entry", "Stop", "Target", "Qty", "Verdict"):
            self.assertIn("<t xml:space=\"preserve\">%s</t>" % head, sheet)
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

    def test_a_state_from_another_day_starts_fresh(self):
        old = loop.fresh_state(datetime.date(2026, 10, 16))
        old["rows"].append(row("TSLA", "x"))
        with open(self.state, "w") as fh:
            json.dump(old, fh)
        self.assertEqual(loop.load_state(self.state, DAY)["rows"], [])


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
