---
name: trader-scanner
description: >-
  Chart Scanner. Scans thousands of charts for setups: opening-range
  breakouts, flags, oversold bounces. Builds the list of US-listed common
  stocks, ordinary shares and ADRs from Nasdaq Trader's symbol directories,
  runs a scan of daily closes, after the close or before the open (20-day
  closing-high breakout, bull flag, reclaimed the 20-day average, oversold,
  oversold bounce) and an intraday opening-range breakout scan on 5-minute
  bars, then writes
  trader/watchlist-daily.json (daily) and trader/watchlist.json (orb) for
  trader-news and trader-entry. A loop (loop.py) re-checks the daily list
  one minute after every 5-minute bar, 9:36 to 10:56 AM IST, and puts every
  breakout into a sorted Excel sheet (trader/orb-YYYY-MM-DD.xlsx) with entry,
  stop, Target 1 and Target 2, quantity and the Time Machine's odds. Free Yahoo data,
  bar by bar. Use when the user says "scan the market", "find me setups",
  "what is breaking out", "opening range breakouts", "scan for bull flags",
  "oversold stocks", "pre-market scan", "build my watchlist", "what should I
  look at today", "watch for breakouts all morning", "run the loop", "build
  the breakout sheet", or at the start of a trading day before
  /trader-news.
argument-hint: "[universe | daily | orb | loop] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads thousands of
free charts and lists the ones that match a rule. That is all it does. A scan
is a list of charts that match a rule, not a list of stocks to buy, and a
setup on a chart is not a prediction. Most people who day trade lose money;
the scanner does not change that. It only saves you from flipping through
5,000 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # after the close or before the open -> trader/watchlist-daily.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols TSLA,NVDA,AMD  # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 AM IST: reads watchlist-daily.json -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                 # cut the clock at 10:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --symbols RELIANCE.NS,TCS.NS --json        # or any other list
python3 "${CLAUDE_SKILL_DIR}/loop.py" run                            # 9:36 to 10:56 AM: every breakout -> trader/orb-YYYY-MM-DD.xlsx
python3 "${CLAUDE_SKILL_DIR}/loop.py" run --force                    # rebuild today's sheet now, even after 10:56: the 9:36 to 10:56 runs replayed in one go
python3 "${CLAUDE_SKILL_DIR}/loop.py" stop                           # from another terminal: stop the loop
python3 "${CLAUDE_SKILL_DIR}/loop.py" show                           # today's sheet, sorted, in the terminal
```

Flags: `--min-price 5` and `--min-volume` (daily 1,000,000, orb 0),
`--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-19 10:15"` (IST) to pin the clock, `--offline PATH`
to read saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0
when the scan found something, 1 when it found nothing or could not run (the
reason is one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist-daily.json` | written by `daily` (never by `orb`): `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. `orb` scans its `hits[].symbol` when no `--symbols` / `--watchlist` / `--universe` is given, and stops with a plain line, before fetching anything, if the file is missing or is not the daily scan of the previous NSE trading day's close (`mode` daily, `as_of` that date; weekends and NSE holidays skipped). trader-news reads it too |
| `trader/watchlist.json` | written by `orb`, the same shape plus each hit's range (`or_high`, `or_low`, `at`, `price`). Re-running `orb` during the morning overwrites only this file, so the daily list is never lost. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | `no_entries_after` (default 11:00), read by `orb`; `daily_min_volume` (optional, default 1,000,000 shares): the daily scan's volume floor when no `--min-volume` is given; `watchlist` (optional): a file of tickers that `daily` scans when no `--symbols` / `--watchlist` is given, instead of the US universe |

## The universe

The built-in universe is the US list (Nasdaq Trader files) and has not been
converted for NSE: on the IST clock its stocks trade outside the 9:15 to 15:30
session, so `orb` finds no usable charts for them. For NSE, scan your own
`.NS` symbols with `--symbols` or `--watchlist`.

`universe` downloads `nasdaqlisted.txt` and `otherlisted.txt` from
nasdaqtrader.com/dynamic/SymDir and keeps a row when, in this order:

1. It is not a test issue, not an ETF, not NextShares.
2. It is a plain ticker: 1 to 5 letters, or 1 to 4 letters plus one class
   letter after a dot (BRK.B). A `$` preferred (ABR$D) is out, and so is a
   `.U`, `.W` or `.R` suffix (units, warrants, rights).
3. Out, whatever else the name says: warrants; SPAC units and rights ("Units,
   each consisting of", "Rights to receive"); notes, ETNs, debentures; a %
   coupon; "Depositary Shares" that are not American or Global (those carry a
   preferred).
4. In: the name calls it common stock, common shares or units, ordinary
   shares, shares of beneficial interest, ADS / ADR / American or Global
   depositary shares, or partnership or trust units.
5. Out: the name still says unit, right, bond, preferred or preference.
6. In: everything else (plain names like "AMETEK, Inc.").

On the Oct 19 2026 files this keeps 5,943. The reel said 5,259: that count
came from an ad hoc filter (names that say common or ordinary, no ADRs). All
5,259 of those are in this list; the 684 extra are mostly ADRs, closed-end
funds and partnership units. The Oct 20 files gave 5,942.

## The daily scan (after the close or before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 5,942
symbols take 298 requests. On Oct 20 2026 that took 145 seconds with the
polite pause (0.3 s). Liquidity comes from the spark meta: last price at
least `--min-price` and last session's volume at least `--min-volume`. Run it
before the open or after the close: during the session the volume is the
day's so far, and today's daily bar is left out until 3:30 PM.

The setups are computed from daily closes only (spark has no highs or lows),
so they are close cousins of the reel's `refs/scan.py`, which used highs and
lows:

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

A closing-high breakout fires more often than an intraday-high one (KO on
Oct 16 broke its 20-day closing high, not its 20-day intraday high). Hits are
listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars. A hit is the first completed 5-minute bar
to CLOSE above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

Timing, live on Tue Oct 20 2026 (a strong morning): 100 liquid names at
10:01 AM took 35 seconds (5 spark requests, 70 v8 charts, 51 of 91 usable
charts broke out). The whole universe at 10:03 AM took about 4 minutes (298
spark requests, then 200 v8 charts): 2,448 usable charts, 1,782 candidates,
the 200 most traded checked, 119 of those broke out. `orb` scans the daily
list (`trader/watchlist-daily.json`) by default, which is the fast answer. To
scan the whole universe instead, pass `--universe trader/universe.txt`; for
names of your own, `--symbols` or `--watchlist`.

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt or a thin stock. Null closes are dropped,
a bar off the 5-minute grid (the live tick, like 10:58:07) is dropped, and a
bar counts only once it is complete (start + 5 minutes). Before 9:35 AM, on a
weekend or holiday, after the close, or after `no_entries_after` (an expired
setup) it prints one plain line and writes nothing. NSE has no scheduled
early closes.

## The loop (loop.py): every breakout into a sorted Excel sheet

`loop.py run` checks the stocks in `trader/watchlist-daily.json` one minute
after each 5-minute bar closes: 9:36, 9:41, ... 10:56 AM IST (`--last-run` to
change the last one). Start it any time before then; it waits. A breakout is
the first completed bar to CLOSE above the opening-range high, the same rule as
`orb`. Every stock that broke out moves to the sheet once and is not checked
again that day, so at 9:41 only the ones still waiting are checked. It ends
after the 10:56 run, or sooner when every stock has broken out.

Each row:

| column | how |
| --- | --- |
| Entry | the breakout bar's close |
| Stop | the opening-range midpoint, (range high + range low) / 2, rounded down |
| Target 1 | entry + `min_reward_risk` x (entry - stop), from the config (1.78 gives a 1.78R target); the header shows the R |
| Target 2 | entry + `max_reward_risk` x (entry - stop), from the config (3.45 gives a 3.45R target); the header shows the R |
| Qty | the smaller of `max_loss_per_trade_usd` / (entry - stop) and `max_order_usd` / entry, rounded down; "Qty limited by" says which |
| Max loss, Cost | qty x (entry - stop), qty x entry |
| Target 1 first, Stop first, Neither, Verdict | the Time Machine on entry, stop and **Target 1**: 10,000 replays to `flat_by`, run for a run's new breakouts in parallel (4 at a time, `--workers`) |

Both targets land on the price step for their own price (NSE's band for `.NS`
stocks, a cent for US stocks), the same rounding trader-exit uses. The loop
needs `min_reward_risk` under `max_reward_risk` in the config and stops with
a message otherwise. Orders are not built from the sheet: trader-exit,
trader-budget and trader-risk still plan one target.

Nothing is filtered out. After every run the whole sheet is re-sorted by the
Time Machine verdict for Target 1: "Strongly toward the target" on top, then "Leans toward the target",
break-even, toward the stop, and rows whose Time Machine failed (Yahoo error,
too little history) at the bottom with the reason. A failure is not retried.
Within one verdict the clearer lean ranks higher, then the earlier breakout.
"Breakout time" is the bar that broke out; "Found at" is the run that saw it
(later when Yahoo was slow to post a bar).

- **The sheet** is a real `.xlsx` written with plain Python (no install),
  `trader/orb-YYYY-MM-DD.xlsx`, one a day, rewritten after each run. Excel does
  not reload an open file: close and reopen it to see the latest run.
- **The daily list is never changed.** Which stocks have moved to the sheet is
  kept in `trader/loop.json`, so a restart the same day carries on without
  adding a stock twice.
- **Nothing is placed and nothing is capped by count.** Choosing rows and
  placing orders is the user's job; each row is sized on its own, so the costs
  of several rows can add up past the cash.
- **Rebuild today's sheet now (`--force`):** `loop.py run --force` rebuilds
  today's sheet straight away, at any time of the trading day, even after
  10:56. It downloads each stock's full 5-minute chart once (highs and lows,
  not the closes-only quick answer), then replays every run from 9:36 to
  `--last-run` (10:56 by default) in one go, bar by bar, exactly as the
  morning loop would have: same breakout bars, same "Found at" times, and a
  missing bar later in the morning cannot hide an earlier breakout. Today's
  `trader/orb-YYYY-MM-DD.xlsx` and `trader/loop.json` are overwritten only
  when the rebuild is complete: a chart that fails twice (one retry after a
  5 s pause), charts with no usable bars for the day (stale or empty
  answers), a failed check or Ctrl+C all leave them as they were. It does not touch a stop request (`loop.py stop`)
  meant for a loop running in another terminal. The sheet shows the
  morning's breakout prices and odds, not the current price: check the live
  price before placing anything. Before the first run (9:36 AM) there is
  nothing to check and it says so.
- **Stopping:** Ctrl+C, or `loop.py stop` from another terminal (it writes
  `trader/loop.stop`).
- **Awake:** on a Mac it runs `caffeinate` while it works (`--no-caffeinate`
  to skip). Closing the lid still sleeps most Macs.
- **Replay a past day:** `loop.py run --offline wall5m.json.gz --tm-offline
  FOLDER --now "2026-10-19 09:30" --symbols TSLA` runs the morning on a
  simulated clock (`FOLDER` holds `<SYM>-60d5m.json`). On the reel's session
  on the NSE clock, TSLA breaks out on the 10:00 bar: entry 364.18, stop
  358.32, Target 1 370.04 at 1R and Target 2 375.90 at 2R (the reel's config:
  `min_reward_risk` 1, `max_reward_risk` 2), and the Time Machine on Target 1
  says "Close to a coin flip" (target first about 27 %, stop first about 23 %).

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query2 first, query1 on a 429
  or 5xx, one retry after a 5-second wait on a 429.
- Partial results are said as partial: "Fetched 4,980 of 5,259 charts (279
  failed or had no data)". Never round a partial scan up to "the whole market".
- NSE holidays for 2026 (nseindia.com (circulars NSE/CMTR/71775 and 72260), read 2026-11-16); no scheduled early closes.

## Steps

1. Once a week or so: `scan.py universe`.
2. After the close or before the open: `scan.py daily` (it writes
   `trader/watchlist-daily.json`). Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:15 is the classic): `scan.py orb`. It scans the
   stocks in `trader/watchlist-daily.json` and stops with a plain line if that
   file is not the daily scan of the last NSE close (run step 2 first). It
   writes `trader/watchlist.json`; running it again later only overwrites
   that file. Tell the user how many usable charts broke their range and when,
   and that a later hit is closer to the price now.
4. Or, for the whole morning: `loop.py run` (above). Tell the user where the
   sheet is, that it is re-sorted after every run, and that they choose and
   place the orders themselves.
5. The user picks one chart. Hand it on: `/trader-news TICKER` (what is moving
   it), then `/trader-entry TICKER` (the entry bar by bar). Never pick for them.

## Example: the reel

_This example is the original US recording (TSLA on the NYSE, New York time, US dollars). On NSE the same steps run on IST (9:15 to 15:30, flat by 15:00), and prices for .NS symbols show in rupees._

Monday Oct 19 2026, scan at 10:15 AM over the reel's 234-ticker wall
(`tests/fixtures/trader-scanner/wall5m-2026-10-19.json.gz`, real Yahoo charts):

```
python3 scan.py orb --offline wall5m-2026-10-19.json.gz --now "2026-10-19 10:15" --min-price 0
Fetched 233 of 234 charts (1 failed or had no data); 231 usable; 95 candidates, 95 confirmed with real highs and lows.
42 of 231 charts closed above their opening-range high by 10:15 AM:
   9:50 AM  AMC         $2.97  range 2.90 to 2.96  +0.50% over
  ...
  10:15 AM  TSLA      $364.18  range 354.47 to 362.18  +0.55% over
```

The reel's wall had no price floor (AMC at $2.97 is one of the 42), hence
`--min-price 0`; with the default $5 floor it is 41. TSLA's range is the 9:30
to 9:40 bars, high 362.18, low 354.47; the 10:10 bar closed at 364.18, 0.55 %
over the high, at 10:15 AM. The user picks TSLA and Claude hands it to
`/trader-news TSLA`, then `/trader-entry TSLA`. In the reel the universe was
5,259 US stocks; this tool's rule gives 5,943 on the same files.

The pre-market scan that morning (closes through Fri Oct 16, the reel's daily
charts) tags BLK, CI, XOM and HD a 20-day breakout, CCL a bull flag, F, JPM
and GE a reclaim of the 20-day average, and BAC, MS, RIVN and PLUG oversold,
as the reel did. On closes it also tags KO (breakout), RCL (flag) and PNC (an
oversold bounce).

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
