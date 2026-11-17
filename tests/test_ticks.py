"""NSE's price steps live in three standalone skills: exit, budget and risk. They must agree."""

import unittest
from decimal import Decimal

from _load import load

exitp = load("trader-exit", "exit")
size = load("trader-budget", "size")
check = load("trader-risk", "check")

PRICES = (0.5, 180, 249.99, 250, 812.4, 999.99, 1000, 1412.5, 4999.9, 5000, 7000, 9999.5, 10000, 12500,
          19999, 20000, 30000)


class TickTablesAgree(unittest.TestCase):
    def test_the_three_tables_are_identical(self):
        self.assertEqual(exitp.NSE_TICKS, size.NSE_TICKS,
                         "trader-exit/exit.py and trader-budget/size.py have different NSE_TICKS: update both")
        self.assertEqual(exitp.NSE_TICKS, check.NSE_TICKS,
                         "trader-exit/exit.py and trader-risk/check.py have different NSE_TICKS: update both")

    def test_every_script_gives_the_same_step(self):
        for px in PRICES:
            for sym in ("X.NS", "TSLA"):
                want = float(exitp.tick_size(Decimal(str(px)), sym))
                self.assertEqual(size.tick_of(px, sym), want, (px, sym))
                self.assertEqual(check.tick_of(px, sym), want, (px, sym))

    def test_what_exit_rounds_to_the_others_accept(self):
        for px in PRICES:
            for rounding in (exitp.ROUND_HALF_UP, exitp.ROUND_FLOOR):
                p = exitp.to_tick(Decimal(str(px)) + Decimal("0.0137"),
                                  exitp.tick_size(Decimal(str(px)) + Decimal("0.0137"), "X.NS"), rounding)
                self.assertTrue(check.on_tick(float(p), "X.NS"), (px, p))
                self.assertTrue(size.on_tick(float(p), "X.NS"), (px, p))


if __name__ == "__main__":
    unittest.main()
