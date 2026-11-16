"""NSE behaviour across the pack: Yahoo .NS symbols kept as written, IST clock,
the 9:15 to 15:30 session, the NSE 2026 holidays, flat by 15:15, rupees for .NS."""

import copy
import datetime
import json
import os
import unittest

from _load import load, FIX
from _nse import shift_doc

alerts = load("trader-alerts", "alerts")
entry = load("trader-entry", "entry")
exitp = load("trader-exit", "exit")
scan = load("trader-scanner", "scan")
news = load("trader-news", "news")
tm = load("trader-timemachine", "timemachine")
risk = load("trader-risk", "check")
budget = load("trader-budget", "size")
broker = load("trader-broker", "ticket")

DT = datetime.datetime
CLOCKS = (alerts, entry, exitp, scan, tm, risk, budget, broker)
CALENDARS = (alerts, entry, exitp, scan, risk)
RUPEE = "₹"

# NSE circular NSE/CMTR/71775 (12 Dec 2025) plus NSE/CMTR/72260 (12 Jan 2026), weekdays only
NSE_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
            "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
            "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"]


def tsla_as(sym):
    """The real TSLA session of 2026-10-19 on the NSE clock, relabelled as another symbol."""
    with open(os.path.join(FIX, "trader-entry", "TSLA-2026-10-19-5m.json")) as fh:
        d = shift_doc(json.load(fh))
    d["chart"]["result"][0]["meta"]["symbol"] = sym
    return d


class Symbols(unittest.TestCase):
    def test_ns_symbols_reach_yahoo_exactly_as_written(self):
        for sym in ("RELIANCE.NS", "BAJAJ-AUTO.NS", "M&M.NS", "reliance.ns"):
            want = sym.upper()
            self.assertIn("/chart/%s?" % want, alerts.yahoo_url(sym))
            self.assertEqual(entry.yahoo_symbol(sym), want)
            self.assertEqual(scan.to_yahoo(sym), want)
            self.assertEqual(news.yahoo_symbol(sym), want)
            self.assertNotIn("-NS", tm.chart_url(sym))
            self.assertNotIn("RELIANCE-NS", alerts.yahoo_url(sym))

    def test_us_share_classes_still_use_a_dash(self):
        self.assertIn("/chart/BRK-B?", alerts.yahoo_url("BRK.B"))
        self.assertEqual(entry.yahoo_symbol("brk.b"), "BRK-B")
        self.assertEqual(scan.to_yahoo("BRK.B"), "BRK-B")
        self.assertEqual(news.yahoo_symbol("BRK.B"), "BRK-B")

    def test_scanner_keeps_ns_symbols_when_reading_yahoo_back(self):
        spark = {"spark": {"result": [{"symbol": "BAJAJ-AUTO.NS", "response": [
            tsla_as("BAJAJ-AUTO.NS")["chart"]["result"][0]]}]}}
        self.assertEqual(list(scan.parse_spark(json.dumps(spark))), ["BAJAJ-AUTO.NS"])

    def test_an_ampersand_does_not_cut_the_spark_list_short(self):
        path = scan.spark_path(["ITC.NS", "M&M.NS", "MARUTI.NS", "BAJAJ-AUTO.NS"], "3mo", "1d")
        self.assertIn("symbols=ITC.NS,M%26M.NS,MARUTI.NS,BAJAJ-AUTO.NS&range=3mo", path)
        self.assertEqual(path.count("&"), 2)  # only the &range and &interval separators
        self.assertIn("symbols=BRK-B,AAPL&", scan.spark_path(["BRK.B", "AAPL"], "3mo", "1d"))

    def test_risk_and_time_machine_accept_long_ns_tickers(self):
        for sym in ("RELIANCE.NS", "BAJAJ-AUTO.NS", "M&M.NS", "HDFCBANK.NS"):
            self.assertTrue(risk.TICKER.match(sym), sym)
        self.assertFalse(risk.TICKER.match("RELIANCE-NS"))
        plan = tm.normalize_plan({"symbol": "RELIANCE.NS", "entry": 364.18, "stop": 358.32, "target": 375.9,
                                  "date": "2026-10-19", "entry_time": "10:00 AM", "entry_bar": 8})
        self.assertEqual(plan["symbol"], "RELIANCE.NS")


class Clock(unittest.TestCase):
    def test_ist_is_utc_plus_five_thirty_all_year(self):
        for mod in CLOCKS:
            for utc in (DT(2026, 1, 15, 3, 45), DT(2026, 7, 1, 3, 45), DT(2026, 11, 16, 10, 0)):
                ist = mod.to_ist(utc)
                self.assertEqual(ist - utc, datetime.timedelta(hours=5, minutes=30), mod.__name__)
                self.assertEqual(mod.ist_to_utc(ist), utc, mod.__name__)

    def test_now_without_an_offset_is_ist(self):
        for mod in (alerts, entry, exitp, scan, tm):
            self.assertEqual(mod.parse_now("2026-11-16 09:15"), DT(2026, 11, 16, 3, 45), mod.__name__)
        self.assertEqual(entry.parse_now("2026-11-16 09:15 IST"), DT(2026, 11, 16, 3, 45))

    def test_session_is_9_15_to_15_30(self):
        for mod in CALENDARS:
            self.assertEqual((mod.OPEN, mod.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)), mod.__name__)
        self.assertEqual((tm.OPEN_MIN, tm.CLOSE_MIN, tm.SESSION_BARS), (9 * 60 + 15, 15 * 60 + 30, 75))

    def test_clock_opens_and_closes_on_ist(self):
        st = alerts.market_status(alerts.parse_now("2026-11-16 09:14"))
        self.assertEqual((st["open"], st["reason"], st["opens"], st["closes"]), (False, "before the open", "09:15", "15:30"))
        self.assertTrue(alerts.market_status(alerts.parse_now("2026-11-16 09:15"))["open"])
        self.assertTrue(alerts.market_status(alerts.parse_now("2026-11-16 15:29"))["open"])
        st = alerts.market_status(alerts.parse_now("2026-11-16 15:30"))
        self.assertEqual((st["open"], st["reason"]), (False, "after the close"))
        self.assertEqual(st["now_ist"], "2026-11-16 15:30")


class Holidays(unittest.TestCase):
    def test_holiday_list_is_the_nse_2026_circular(self):
        for mod in CALENDARS:
            self.assertEqual(sorted(mod.NSE_HOLIDAYS), NSE_2026, mod.__name__)
            self.assertEqual(mod.NSE_EARLY_CLOSE, {}, mod.__name__)
            if hasattr(mod, "CALENDAR_YEARS"):  # exit.py never reports calendar coverage
                self.assertEqual(mod.CALENDAR_YEARS, (2026,), mod.__name__)

    def test_a_holiday_is_closed_and_the_next_open_skips_it(self):
        # Tue 2026-11-24 is Guru Nanak Jayanti: closed; Mon 2026-11-23 15:40 -> next open Wed 09:15
        st = alerts.market_status(alerts.parse_now("2026-11-24 11:00"))
        self.assertFalse(st["open"])
        self.assertEqual(st["reason"], "NSE holiday: Prakash Gurpurb Sri Guru Nanak Dev")
        self.assertEqual(alerts.market_status(alerts.parse_now("2026-11-23 15:40"))["next_open"], "2026-11-25 09:15")
        self.assertEqual(entry.trading_day(datetime.date(2026, 10, 20)), (False, "NSE holiday: Dussehra"))

    def test_us_holidays_are_trading_days_now(self):
        for day in ("2026-11-26", "2026-07-03", "2026-01-19"):  # Thanksgiving, July 4 observed, MLK
            self.assertEqual(alerts.trading_day(datetime.date.fromisoformat(day)), (True, ""), day)

    def test_2027_is_flagged_unknown(self):
        st = alerts.market_status(alerts.parse_now("2027-01-04 10:00"))
        self.assertFalse(st["calendar_known"])


class FlatBy(unittest.TestCase):
    def test_day_trades_close_by_15_15(self):
        self.assertEqual(exitp.DEFAULTS["flat_by"], "15:15")
        self.assertEqual(budget.DEFAULTS["flat_by"], "15:15")
        self.assertEqual(risk.DEFAULTS["flat_by"], "15:15")
        with open(os.path.join(os.path.dirname(budget.__file__), "config.example.json")) as fh:
            self.assertEqual(json.load(fh)["flat_by"], "15:15")
        self.assertEqual(exitp.resolve_flat_by(datetime.date(2026, 11, 16), None, {}, None), ("15:15", ""))

    def test_plan_alerts_ring_flat_by_15_15(self):
        rules = alerts.rules_from_plan({"symbol": "RELIANCE.NS", "date": "2026-11-16", "stop": 1500, "target": 1540})
        self.assertEqual([r["at"] for r in rules if r["type"] == "time"], ["15:15"])

    def test_entries_stop_before_the_flat_by(self):
        self.assertEqual(entry.DEFAULTS, {"no_entries_before": "09:30", "no_entries_after": "15:00"})
        self.assertLess(entry.DEFAULTS["no_entries_after"], exitp.DEFAULTS["flat_by"])


class Rupees(unittest.TestCase):
    def test_entry_shows_rupees_for_ns_and_dollars_otherwise(self):
        for sym, sign in (("RELIANCE.NS", RUPEE), ("TSLA", "$")):
            res = entry.evaluate(entry.parse_chart(json.dumps(tsla_as(sym)), sym), entry.parse_now("2026-10-19 10:00"))
            self.assertEqual(res["status"], "triggered")
            self.assertEqual(res["trigger"]["price"], 364.18)
            text = "\n".join(entry.describe(res))
            self.assertIn("TRIGGERED 10:00 AM at %s364.18" % sign, text)
            self.assertIn("Opening range 9:15-9:30: high %s362.18" % sign, text)
            other = "$" if sign == RUPEE else RUPEE
            self.assertNotIn(other, text)

    def test_alert_messages_in_rupees(self):
        rules = [{"id": "r-stop", "symbol": "RELIANCE.NS", "type": "stop", "price": 1500}]
        quotes = {"RELIANCE.NS": {"symbol": "RELIANCE.NS", "price": 1499.5, "prev_close": 1510.0}}
        status = alerts.market_status(alerts.parse_now("2026-11-16 11:00"))
        rings, _ = alerts.evaluate(rules, quotes, status, {}, alerts.parse_now("2026-11-16 11:00"))
        self.assertEqual(len(rings), 1)
        self.assertIn(RUPEE + "1499.50", rings[0]["message"])
        self.assertNotIn("$", rings[0]["message"])

    def test_exit_plan_in_rupees(self):
        plan = {"symbol": "RELIANCE.NS", "date": "2026-11-16", "setup": "orb", "interval": "5m",
                "entry": 1520.0, "stop": 1510.0, "target": 1540.0, "risk_per_share": 10.0, "reward_risk": 2.0,
                "stop_label": "opening-range midpoint", "target_label": "2R", "flat_by": "15:15"}
        text = "\n".join(exitp.describe(plan))
        self.assertIn("Entry         %s1,520.00" % RUPEE, text)
        self.assertIn("Flat by       15:15 IST", text)
        self.assertNotIn("$", text)

    def test_budget_and_broker_tickets_in_rupees(self):
        plan = {"symbol": "RELIANCE.NS", "side": "buy", "setup": "orb", "date": "2026-11-16", "entry": 1520.0,
                "stop": 1510.0, "target": 1540.0, "flat_by": "15:15"}
        cfg = dict(budget.DEFAULTS, max_loss_per_trade_usd=500, max_position_pct=100, max_order_usd=200000)
        s = budget.size_trade(plan, cfg, budget=200000)
        order = budget.bracket_order(s)
        report = budget.plan_report(s, order)
        self.assertEqual(s["qty"], 50)
        self.assertIn("Max loss at the stop: 50 x 10.00 = %s500" % RUPEE, report)
        self.assertNotIn("$", report)
        self.assertNotIn("$", order["why"])
        ticket = broker.plain(order)
        self.assertIn("max loss about %s500" % RUPEE, ticket)
        self.assertNotIn("$", ticket)
        self.assertIn("$", broker.plain(dict(order, symbol="TSLA")))

    def test_risk_messages_in_rupees(self):
        order = {"symbol": "RELIANCE.NS", "side": "buy", "qty": "50", "type": "market", "time_in_force": "day",
                 "order_class": "bracket", "take_profit_limit_price": "1540.00", "stop_loss_stop_price": "1510.00",
                 "plan": {"entry": 1520.0, "stop": 1510.0, "target": 1540.0, "risk_per_share": 10.0}}
        cfg = dict(risk.DEFAULTS, max_loss_per_trade_usd=400, budget=200000)
        res = risk.check([order], cfg, prices={"RELIANCE.NS": 1520.0}, now=risk.parse_now("2026-11-16 10:30"),
                         price_times={"RELIANCE.NS": DT(2026, 11, 16, 4, 59)})
        r = res["results"][0]
        text = " ".join(r["reasons"] + r["warnings"] + r["info"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn(RUPEE + "500", text)
        self.assertNotIn("$", text)
        self.assertNotIn("does not look like", text)


class ReviewFixes(unittest.TestCase):
    BATCH = ["TCS.NS", "RELIANCE.NS", "BAJAJ-AUTO.NS", "M&M.NS", "INFY.NS"]
    TCS = {"symbol": "TCS.NS", "side": "buy", "qty": "5", "type": "market", "time_in_force": "day"}

    def test_a_yes_naming_another_nse_stock_is_not_a_yes(self):
        for reply in ("yes RELIANCE.NS", "yes BAJAJ-AUTO.NS", "yes M&M.NS", "yes INFY.NS", "yes RELIANCE",
                      "yes reliance.ns"):
            ok, why = broker.parse_yes(reply, self.TCS, self.BATCH)
            self.assertFalse(ok, reply)
            self.assertIn("but this order is for TCS.NS", why)

    def test_a_yes_for_the_right_nse_stock_still_counts(self):
        for reply in ("yes", "yes TCS.NS", "yes TCS", "yes 5 TCS.NS"):
            self.assertTrue(broker.parse_yes(reply, self.TCS, self.BATCH)[0], reply)

    def test_news_still_matches_nyse_tickers(self):
        pat = news.mention_patterns({"KO": "Coca-Cola"})["KO"]
        self.assertTrue(pat.search("Coca-Cola (NYSE: KO) beats"))

    def test_weights_mode_is_rupees_only_when_every_stock_is_nse(self):
        prices = {"RELIANCE.NS": 1500.0, "TCS.NS": 3100.0, "M&M.NS": 3000.0, "AAPL": 230.0}
        cfg = dict(budget.DEFAULTS, max_position_pct=75)
        rows = [{"ticker": "RELIANCE.NS", "name": "", "weight": 40.0}, {"ticker": "TCS.NS", "name": "", "weight": 50.0},
                {"ticker": "M&M.NS", "name": "", "weight": 10.0}]
        res = budget.size(rows, cfg, 10000, prices=prices, whole=True)
        text = budget.report(res, budget.orders_from(res))
        self.assertIn("Budget %s10,000" % RUPEE, text)
        self.assertIn("Dropped M&M.NS: one share costs %s3000" % RUPEE, text)
        self.assertNotIn("$", text)
        mixed = budget.size([{"ticker": "RELIANCE.NS", "name": "", "weight": 50.0},
                             {"ticker": "AAPL", "name": "", "weight": 50.0}], cfg, 10000, prices=prices, whole=True)
        self.assertNotIn(RUPEE, budget.report(mixed, budget.orders_from(mixed)))

    def test_risk_report_cash_line_in_rupees_for_nse_orders(self):
        order = {"symbol": "RELIANCE.NS", "side": "buy", "qty": "2", "type": "market", "time_in_force": "day",
                 "order_class": "bracket", "take_profit_limit_price": "1540.00", "stop_loss_stop_price": "1510.00",
                 "plan": {"entry": 1520.0, "stop": 1510.0, "target": 1540.0, "risk_per_share": 10.0}}
        cfg = dict(risk.DEFAULTS, max_loss_per_trade_usd=400, budget=200000)
        res = risk.check([order], cfg, prices={"RELIANCE.NS": 1520.0}, now=risk.parse_now("2026-11-16 10:30"),
                         price_times={"RELIANCE.NS": DT(2026, 11, 16, 4, 59)})
        text = risk.report(res)
        self.assertIn("Cash after the passed orders: %s" % RUPEE, text)
        self.assertNotIn("$", text)

    def test_junk_nse_tickers_are_refused(self):
        for junk in ("-.NS", "&&.NS", ".NS"):
            self.assertFalse(risk.TICKER.match(junk), junk)


class RewardRiskRange(unittest.TestCase):
    """min_reward_risk 0.9 and max_reward_risk 3.1: entry 1520, stop 1510 (risk 10 a share)."""
    CFG = {"min_reward_risk": 0.9, "max_reward_risk": 3.1, "max_loss_per_trade_usd": 1000, "budget": 100000,
           "max_order_usd": 30000, "max_position_pct": 30, "cash_buffer_pct": 10}

    def order(self, target):
        return {"symbol": "RELIANCE.NS", "side": "buy", "qty": "10", "type": "market", "time_in_force": "day",
                "order_class": "bracket", "take_profit_limit_price": "%.2f" % target, "stop_loss_stop_price": "1510.00",
                "plan": {"entry": 1520.0, "stop": 1510.0, "target": target, "risk_per_share": 10.0}}

    def verdict(self, target):
        cfg = dict(risk.DEFAULTS, **self.CFG)
        res = risk.check([self.order(target)], cfg, prices={"RELIANCE.NS": 1520.0},
                         now=risk.parse_now("2026-11-16 10:30"), price_times={"RELIANCE.NS": DT(2026, 11, 16, 4, 59)})
        return res["results"][0]

    def test_risk_passes_inside_the_range_and_at_both_ends(self):
        for target in (1529.0, 1540.0, 1551.0):  # 0.9R, 2R, 3.1R
            r = self.verdict(target)
            self.assertEqual(r["verdict"], "PASS", (target, r["reasons"]))

    def test_risk_blocks_outside_the_range(self):
        low, high = self.verdict(1528.9), self.verdict(1551.1)  # 0.89R, 3.11R
        self.assertEqual((low["verdict"], high["verdict"]), ("BLOCK", "BLOCK"))
        self.assertIn("is under your minimum 0.9", " ".join(low["reasons"]))
        self.assertIn("is over your maximum 3.1", " ".join(high["reasons"]))

    def test_budget_refuses_over_the_maximum(self):
        plan = {"symbol": "RELIANCE.NS", "side": "buy", "setup": "orb", "date": "2026-11-16",
                "entry": 1520.0, "stop": 1510.0, "target": 1560.0, "flat_by": "15:15"}
        s = budget.size_trade(plan, dict(budget.DEFAULTS, **self.CFG))
        self.assertIsNone(budget.bracket_order(s))
        self.assertIn("is over your maximum 3.1", s["refusal"])
        ok = budget.size_trade(dict(plan, target=1540.0), dict(budget.DEFAULTS, **self.CFG))
        self.assertIsNone(ok["refusal"])
        self.assertEqual(ok["qty"], 19)  # 30% of 100,000 = 30,000 / 1,520 = 19 shares

    def test_no_maximum_unless_it_is_set(self):
        cfg = dict(risk.DEFAULTS, **self.CFG)
        del cfg["max_reward_risk"]
        res = risk.check([self.order(1580.0)], cfg, prices={"RELIANCE.NS": 1520.0},  # 6R
                         now=risk.parse_now("2026-11-16 10:30"), price_times={"RELIANCE.NS": DT(2026, 11, 16, 4, 59)})
        self.assertEqual(res["results"][0]["verdict"], "PASS")


class ScannerWatchlistDefault(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.old = os.getcwd()
        self.tmp = tempfile.mkdtemp()
        os.chdir(self.tmp)
        os.makedirs("trader")
        with open("nifty50.txt", "w") as fh:
            fh.write("RELIANCE.NS\nTCS.NS\nM&M.NS\n")

    def tearDown(self):
        import shutil
        os.chdir(self.old)
        shutil.rmtree(self.tmp)

    def args(self, **kw):
        import argparse
        base = dict(symbols=None, watchlist=None, universe=None, limit=None, config="trader/config.json")
        base.update(kw)
        return argparse.Namespace(**base)

    def test_config_watchlist_is_the_default_list(self):
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"watchlist": "nifty50.txt"}, fh)
        self.assertEqual(scan.read_symbols(self.args()), ["RELIANCE.NS", "TCS.NS", "M&M.NS"])
        self.assertEqual(scan.read_symbols(self.args(symbols="INFY.NS")), ["INFY.NS"])  # an explicit list still wins

    def test_without_it_the_us_universe_is_still_the_default(self):
        with self.assertRaises(scan.ScanError) as e:
            scan.read_symbols(self.args())
        self.assertIn("trader/universe.txt", str(e.exception))


if __name__ == "__main__":
    unittest.main()
