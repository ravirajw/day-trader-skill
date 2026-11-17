# NSE review fixes - 17 Nov 2026

This note explains, in plain words, every change made after reviewing commit `3340561` ("Make the NSE trading config the default") on the `nse-market` branch. For each change it says what was wrong, a real example, what was changed, where, how it is tested, and why.

It also lists the things that were looked at and **left alone on purpose**, so nobody has to re-argue them later.

---

## Contents

1. [Words used in this note](#1-words-used-in-this-note)
2. [Quick summary](#2-quick-summary)
3. [Change 1 - "No broker set" is shown and respected everywhere](#3-change-1---no-broker-set-is-shown-and-respected-everywhere)
4. [Change 2 - Most lost in one day: 3,000 to 2,700](#4-change-2---most-lost-in-one-day-3000-to-2700)
5. [Change 3 - The scanner finds the Nifty 50 list from any folder](#5-change-3---the-scanner-finds-the-nifty-50-list-from-any-folder)
6. [Change 4 - The Nifty 50 list now carries a date](#6-change-4---the-nifty-50-list-now-carries-a-date)
7. [Change 5 - A note that "_usd" settings are really rupees](#7-change-5---a-note-that-_usd-settings-are-really-rupees)
8. [Change 6 - Stop and target land on NSE's price step](#8-change-6---stop-and-target-land-on-nses-price-step)
9. [Change 7 - The checks allow half a price step of rounding](#9-change-7---the-checks-allow-half-a-price-step-of-rounding)
10. [Change 8 - A limit price you type is put on the price step](#10-change-8---a-limit-price-you-type-is-put-on-the-price-step)
11. [Change 9 - The risk check blocks NSE prices that are off the step](#11-change-9---the-risk-check-blocks-nse-prices-that-are-off-the-step)
12. [Change 10 - A test that keeps the three price-step tables the same](#12-change-10---a-test-that-keeps-the-three-price-step-tables-the-same)
13. [Change 11 - Two small tidy-ups](#13-change-11---two-small-tidy-ups)
14. [Looked at and left alone on purpose](#14-looked-at-and-left-alone-on-purpose)
15. [Things to remember](#15-things-to-remember)
16. [How to check it all still works](#16-how-to-check-it-all-still-works)

---

## 1. Words used in this note

| Word | What it means here |
| --- | --- |
| **Entry** | The price you buy at. |
| **Stop** (stop-loss) | The price where you sell to cut the loss if the trade goes wrong. It sits under the entry. |
| **Target** (take-profit) | The price where you sell to take the profit. It sits above the entry. |
| **Risk** | Entry minus stop: what you lose on one share if the stop is hit. |
| **R** | One unit of risk. A "2R target" means the target is twice the risk above the entry. |
| **Reward:risk** (R:R) | (Target - entry) / (entry - stop). Your config wants it between **1.78** and **3.45**. |
| **Price step** (tick size) | The smallest amount a price can move on the exchange. NSE does not allow any price between two steps. US stocks move in cents (0.01). |
| **Bracket order** | One buy with its stop and target attached, sent together. |
| **Paper** | Practice money, not real money. |
| **Broker "none"** | The config says no broker is connected. The tool must never send an order anywhere. You place it yourself. |
| **Flatten** | Sell everything in a stock to close the day trade. |

---

## 2. Quick summary

| # | Change | Why, in one line | Main files |
| --- | --- | --- | --- |
| 1 | "No broker set" respected everywhere | With broker "none" the tool still talked about Alpaca and told Claude to send orders there | `trader-broker/ticket.py` |
| 2 | Daily loss limit 3,000 to 2,700 | Your choice: exactly three full losing trades of 900 | `config.example.json`, `size.py`, `check.py`, `trader-budget/SKILL.md` |
| 3 | Watchlist found from any folder | The scan stopped if you ran it from the "wrong" folder | `trader-scanner/scan.py` |
| 4 | Dated Nifty 50 list | NSE changes the index twice a year; the list had no date | `trader-scanner/nifty50.txt` |
| 5 | Currency note | Settings say "usd" but hold rupees | `config.example.json`, `trader-budget/SKILL.md` |
| 6 | Stop and target on NSE's price step | NSE rejects prices that are not on its step | `trader-exit/exit.py`, `trader-exit/SKILL.md` |
| 7 | Half a step of slack in the checks | The tool was refusing targets it had set itself, because of rounding | `trader-budget/size.py`, `trader-risk/check.py` |
| 8 | Typed limit price put on the step | A typed limit like 812.43 would be rejected by NSE | `trader-budget/size.py` |
| 9 | Risk check blocks off-step NSE prices | Catch a bad price before the broker does | `trader-risk/check.py` |
| 10 | Test that the step tables match | The table is copied in three files; a missed update would go unnoticed | `tests/test_ticks.py` (new) |
| 11 | Tidy-ups | Use the existing `is_nse()` helper; remove unused `floor_cents()` | `exit.py`, `size.py`, `check.py` |

All changes come with tests. The full suite has **405 tests, all passing**.

---

## 3. Change 1 - "No broker set" is shown and respected everywhere

### What was wrong

Commit `3340561` set `"broker": "none"` as the default. But the order ticket only knew two brokers: "if it is Interactive Brokers, say so, otherwise say Alpaca". So with "none":

- The ticket header said **"Alpaca"**.
- The ticket body said "Claude will call `place_stock_order`" (Alpaca's tool) and explained how Alpaca places a bracket.
- After you said "yes", it told Claude: "Now call `place_stock_order` with exactly: ...".
- The computer-readable ticket (`ticket.py show --json`, the version Claude reads) said `"tool": "place_stock_order"` and included the full order to send.
- The "get me out" steps (`ticket.py flatten`) said "Alpaca holds these shares ... cancel the legs ... then `place_stock_order`".

### Why it matters

"None" is supposed to mean **nothing is ever sent**. If you later connected Alpaca's tool in Claude but kept "none" in your config, Claude could have followed those instructions and sent a real order to Alpaca. A safety setting has to be kept everywhere, not just in one message.

### What was changed (all in `trader-broker/ticket.py`)

New small helpers:

- `BROKER_NAMES` and `broker_name(cfg)` - give the ticket header the right name: "Alpaca", "Interactive Brokers" or **"No broker set"**.
- `no_broker(cfg)` - answers "is the broker 'none'?".
- `own_words(label, cfg)` - with no broker, changes the status "waiting for the place_stock_order call" to **"waiting for you to place it with your broker and record the result"**.

Where they are used:

| Place | Before (broker "none") | After |
| --- | --- | --- |
| Ticket header (`render`) | `ORDER 1 of 1, PAPER account, Alpaca` | `ORDER 1 of 1, PAPER account, No broker set` |
| Ticket body (`render`) | "Claude will call place_stock_order with exactly: ..." | "No broker is set in the config (broker: none): nothing is sent. Place this order yourself with your broker, then record what happened with ticket.py result." |
| Yes prompt (`render`) | "Reply yes to place this one order" + "Claude's own card then asks again ..." | "Reply yes to **record that you will place this one order yourself**, or no to skip it." (no card line) |
| After your yes (`cmd_approve`) | "Now call place_stock_order with exactly: ..." | "No broker is set ... so nothing is sent. Place it yourself with your broker, then: ticket.py result --index N --status placed\|denied\|failed" |
| Status after approval (`render`, `ticket_json`) | "waiting for the place_stock_order call" | "waiting for you to place it with your broker and record the result" |
| JSON ticket (`ticket_json`) | `"tool": "place_stock_order"`, full `"tool_input"` | `"tool": null`, `"tool_input": null` |
| Flatten steps (`cmd_flatten`) | "Check it against get_open_position ..." and Alpaca step 3 | "Check it against what your broker shows you hold" and "close the position yourself with your broker (cancel its open stop-loss and target orders first), then record it with ticket.py result" |

**Alpaca and Interactive Brokers work exactly as before.**

### Tests (`tests/test_broker.py`)

- `test_no_broker_tickets_say_so_and_send_nothing` - header says "No broker set", no "Alpaca" or `place_stock_order` anywhere, the yes message says nothing is sent, the status line is the new one.
- `test_no_broker_json_names_no_tool_and_asks_for_the_right_yes` - JSON has `tool` and `tool_input` as null; the yes prompt has the new wording.
- `test_no_broker_flatten_says_close_it_yourself` - flatten text has no "Alpaca", `place_stock_order` or `cancel_order_by_id`.

---

## 4. Change 2 - Most lost in one day: 3,000 to 2,700

### What was changed

`max_daily_loss_usd` went from **3,000** to **2,700** in all four places that hold it:

- `trader-budget/config.example.json` (the starting settings file)
- `trader-budget/size.py` (`DEFAULTS`, the backup numbers used when a setting is missing)
- `trader-risk/check.py` (`DEFAULTS`, same)
- `trader-budget/SKILL.md` (the instructions, now saying "2,700: three full losing trades of 900")

### Why

Your choice. The most one trade can lose is 900, and you allow 3 trades a day. 3 x 900 = 2,700, so the daily limit now matches exactly three full losses. With 3,000 there was 300 of room that no real day could use.

### Important

These files only hold **starting values**. Your real settings file, `trader/config.json`, is not in this repo. **If it still says 3,000, change it to 2,700 yourself.**

---

## 5. Change 3 - The scanner finds the Nifty 50 list from any folder

### What was wrong

The settings say `"watchlist": "nifty50.txt"` - a file name with no folder. The scanner looked for it **only in the folder you happened to be in** when you ran it. The real list lives in `trader-scanner/`. Run from anywhere else, the daily scan stopped with "no file at nifty50.txt". No daily list means the 9:36 breakout loop has nothing to check that morning.

### What was changed (`trader-scanner/scan.py`)

A new function `find_watchlist(name, config_path)`. If the watchlist setting is a bare file name, it looks in this order and uses the first one it finds:

1. The folder you run in (same as before, so nothing that worked breaks).
2. Next to your config file (inside `trader/`).
3. The folder that holds `trader/` (your project folder).
4. `trader-scanner/`, which ships the list.

A setting with a folder in it (like `lists/my.txt`, or a full path) is used exactly as written. If the file is nowhere, the error now **lists every place it looked**.

The setup instructions in `trader-budget/SKILL.md` no longer tell you to copy the list by hand.

### Why

A missed scan costs a trading morning, and "copy this file next to that one" is easy to forget. Looking in the sensible places first removes that trap without changing anything for people whose setup already worked.

### Test (`tests/test_scanner.py`)

`test_config_watchlist_is_found_from_any_folder` - checks each of the four places in order, that a missing file lists the places it looked, and that a path with a folder is used as given.

---

## 6. Change 4 - The Nifty 50 list now carries a date

### What was wrong

`trader-scanner/nifty50.txt` is a fixed list of 50 names typed in on one day. NSE reviews the Nifty 50 every **March and September** and swaps stocks in and out. Nothing updates the file, and it had no date, so you could not tell how old it was. Stocks that left the index would keep being scanned; new ones would be missed. Nothing would crash, so nobody would notice.

### What was changed

Three comment lines at the top of the file:

```
# Nifty 50 members, as of 17 Nov 2026 (the day this list was made).
# NSE reviews the index every March and September: check this list against
# nseindia.com after each review and update it, or the scanner keeps old names.
```

The scanner already skips lines that start with `#`, so this cannot break anything. It was checked: the scanner still reads exactly 50 stocks.

### Why not download the list automatically?

NSE often blocks automated downloads, so it would fail at random. A dated note plus a check twice a year is more reliable.

---

## 7. Change 5 - A note that "_usd" settings are really rupees

### What was wrong

Four settings have `_usd` (US dollars) in their names: `max_loss_per_trade_usd`, `max_daily_loss_usd`, `max_order_usd`, `min_order_usd`. Your values (900, 2,700, 30,000, 5) are rupees.

### Does it break anything?

**No.** The code never converts money. It compares plain numbers, so 900 means 900 of whatever currency the share prices are in - rupees for `.NS` stocks. The risk is only that a person (or Claude) reads "usd", thinks "900 dollars is about 75,000 rupees", and changes a limit by mistake.

### What was changed

- `trader-budget/config.example.json` now starts with a `"_note"` line saying these settings are in your trading currency and nothing is converted. Scripts ignore setting names they do not know, so this extra line is safe. (JSON files cannot hold normal comments.)
- `trader-budget/SKILL.md` says the same in the setup steps.

### What was checked

A sample SBIN.NS trade was run through sizing and the risk check with these settings. **Every amount showed as ₹** (₹900 limit, ₹2,700 daily loss) and there was no `$` sign in the files they wrote. The scripts already swap `$` for ₹ on `.NS` stocks, so no messages needed fixing.

### Why not rename the settings?

Renaming touches every script, test and existing config file, and old config files would stop working. A clear note fixes the real risk (a misread number) at a fraction of the cost.

---

## 8. Change 6 - Stop and target land on NSE's price step

### What was wrong

The stop and target were rounded **to the cent (0.01)**. But NSE only accepts prices on its own price step, which depends on the share price. For example, SBIN at about ₹812 moves in steps of **₹0.05**, so 834.14 is not a price NSE allows. The broker would reject the order.

### NSE's price steps

From NSE circular **CMTR67133**, in force from 15 April 2025:

| Share price | Step |
| --- | --- |
| Below ₹250 | ₹0.01 |
| ₹250 to under ₹1,000 | ₹0.05 |
| ₹1,000 to under ₹5,000 | ₹0.10 |
| ₹5,000 to under ₹10,000 | ₹0.50 |
| ₹10,000 to under ₹20,000 | ₹1.00 |
| ₹20,000 and above | ₹5.00 |

US stocks are unchanged: they still use cents.

### What was changed (`trader-exit/exit.py`)

- New table `NSE_TICKS` (the one above).
- New `tick_size(price, sym)` - gives the step for a price: NSE's band for `.NS` stocks, a cent otherwise.
- New `to_tick(x, tick, rounding)` - rounds a price to a whole number of steps.
- `compute(...)` now takes the stock's symbol (`sym`) instead of assuming cents, and:
  - **Stop**: rounded **down** to its step (away from the entry, so the stop is never tighter than asked). Example: 806.12 becomes **806.10**.
  - **Target** (`2R`, `3.45R` ...): rounded to the **nearest** step, half up. Example: 834.135 becomes **834.15**; 823.614 becomes **823.60**.
  - **Target you type** (`price:X`): also rounded to the nearest step.
- `build_plan(...)` passes the stock's symbol into `compute`.
- The instructions (`trader-exit/SKILL.md`) and the help text at the top of `exit.py` now describe price steps instead of "to the cent".

### Each price uses the step for its own price

An early version took the step from the **entry** and used it for the target too. The independent review found the problem: if the entry is under ₹1,000 and the target is over ₹1,000, the target was rounded to 0.05 when NSE wants 0.10.

Example: entry ₹995.35, stop ₹990.10, 2R target. Raw target is 1005.85. Using the entry's step (0.05) gave **1005.85**, which is not on the 0.10 step above ₹1,000, so the risk check blocked the trade. Now the target uses its own band's step and becomes **1005.90**, which passes.

So now **the stop is rounded with the stop's band and the target with the target's band** - which is how NSE does it.

### A known limit

NSE sets each stock's step **every month from its closing price**. The tool uses the price at the time of the trade. A stock sitting right at a band edge (for example ₹998 or ₹1,003) could be one step off for that month. This is rare and accepted.

### Tests (`tests/test_exit.py`)

`test_nse_prices_land_on_nse_price_steps` - checks every band in the table, US stays on cents, your two examples (834.15 and 823.60), a typed `price:X` target, stops rounding down, and the band edges at ₹250 and ₹1,000 (including the 995.35 -> 1005.90 case).

---

## 9. Change 7 - The checks allow half a price step of rounding

### What was wrong

Once the target is rounded, it can land a tiny bit past your reward:risk limits. Then the budget step (`size.py`) refused the trade **the tool itself had just planned**, with a confusing message.

Real example, SBIN.NS, entry 812.40, stop 806.10, risk 6.30:

- The breakout loop sets every target at your maximum, **3.45R**: 812.40 + 3.45 x 6.30 = 834.135, rounded to **834.15**. That is 3.452R - a hair over 3.45. Refused: *"reward:risk 3.45 ... is over your maximum 3.45"*.
- A target at exactly **1.78R**: 823.614, rounded to **823.60**. That is 1.778R - a hair under 1.78. Refused: *"reward:risk 1.78 ... is under your minimum 1.78"*.

Because the loop puts every target at 3.45R, roughly half of the loop's breakouts could be refused for a difference smaller than one price step.

Note: rounding to 0.05 instead of 0.01 does **not** fix this by itself - it makes the gap a bit bigger. That is why the checks also had to change.

### What was changed

**`trader-budget/size.py`**
- New `half_tick_cents(price, sym)` - half of the price step, in cents.
- The minimum and maximum reward:risk checks now allow the reward to be up to **half a step per share** past the limit.

**`trader-risk/check.py`**
- The same `half_tick_cents` helper.
- The risk check already rounded R:R to 2 decimals before comparing. It now blocks a target only if it fails **both** that 2-decimal rule **and** the half-step rule. So it is never stricter than before, and never looser than half a step.

### Why this is safe

A price cannot be finer than one step, so half a step is the most rounding can ever move a target. A target **a full step** past the limit is still refused. In the example: 834.15 and 823.60 pass; **834.20** and **823.55** are still blocked.

For US stocks the slack is half a cent a share, which is nothing. For your Nifty 50 list the biggest step is ₹1.00 (MARUTI, ULTRACEMCO and BAJAJ-AUTO, all between ₹10,000 and ₹20,000 on 17 Nov 2026; none is above ₹20,000), so the slack is at most 50 paise a share.

### Tests

- `tests/test_budget.py` - `test_a_target_rounded_to_the_nse_step_is_not_refused`: 834.15 and 823.60 are sized; 834.20 and 823.55 are refused; a US stock gets only half a cent.
- `tests/test_risk.py` - `test_a_target_rounded_to_the_nse_step_passes`: the same four cases in the risk check.

---

## 10. Change 8 - A limit price you type is put on the price step

### What was wrong

`size.py --limit 812.43` put 812.43 straight into the order. For SBIN.NS the step is 0.05, so NSE would reject it.

### What was changed (`trader-budget/size.py`)

- New helpers `tick_of(price, sym)`, `on_tick(price, sym)` and `to_tick(price, sym)`.
- In `run_plan(...)`: if the limit price is not on the step, it is rounded to the **nearest** step and the report says so:

```
Limit 812.43 is not on the price step (0.05 for this price): rounded to the nearest step, 812.45.
```

This applies to a price you type (`--limit 812.43`) and to `--limit` with no price (which uses the plan's entry). US stocks are unchanged.

### Why nearest and not down?

A buy limit means "pay at most this much", so rounding up (812.43 to 812.45) pays 2 paise more a share than you typed. This was raised, and you decided **a small up/down in profit or loss is fine**. Nearest was kept.

### Test (`tests/test_budget.py`)

`test_a_typed_nse_limit_is_rounded_to_the_price_step` - 812.43 becomes 812.45, 812.42 becomes 812.40, the 0.10 band works (1412.46 becomes 1412.50), a US stock is unchanged, and the order file holds `"limit_price": "812.45"`.

---

## 11. Change 9 - The risk check blocks NSE prices that are off the step

### What was wrong

The risk check is the last gate before the broker. It never checked whether prices were on NSE's step, so an order with a bad price (for example typed into an order file by hand) got a **PASS** and only failed later at the broker.

### What was changed (`trader-risk/check.py`)

For every `.NS` order, the risk check now looks at `limit_price`, `stop_price`, `take_profit_limit_price`, `stop_loss_stop_price` and `stop_loss_limit_price`. Any price not on its step **blocks** the order, with the nearest valid price in the message:

```
BLOCK #1 buy SBIN.NS 36 shares, entry
  - price step: stop_loss_stop_price 806.12 is not on NSE's 0.05 price step for this price; NSE rejects it. Nearest step: 806.10
```

US stocks are not affected.

### Why block and not fix it quietly?

The risk check only judges orders; it never changes them. Blocking with a clear reason keeps it predictable: you see exactly what is wrong and fix it at the source.

### Test (`tests/test_risk.py`)

`test_nse_prices_off_the_price_step_are_blocked` - a good order passes; an off-step stop and target are blocked with the right message and nearest price; an off-step limit price is blocked; the 0.10 band is checked; a US order is never flagged.

---

## 12. Change 10 - A test that keeps the three price-step tables the same

### What was wrong

The NSE price-step table now lives in **three files**: `trader-exit/exit.py`, `trader-budget/size.py` and `trader-risk/check.py`. If NSE changes its steps and someone updates two copies but not the third, trader-exit would make prices that the risk check blocks (or the other way round), and nothing would warn you.

### Why keep three copies?

Every skill folder in this project is built to **stand alone**: no script imports from another folder. Helpers like `is_nse()` and the ₹ swap are already copied into many files for the same reason. One shared file would break that design.

### What was changed

A new test file, `tests/test_ticks.py`:

- `test_the_three_tables_are_identical` - the three `NSE_TICKS` tables must be exactly the same. If not, it fails with a message naming the two files to fix.
- `test_every_script_gives_the_same_step` - all three scripts give the same step for 17 prices (including every band edge), for NSE and US stocks.
- `test_what_exit_rounds_to_the_others_accept` - any price trader-exit rounds is accepted as on-step by both the budget and the risk scripts.

It was proven to work by changing one copy on purpose: the test failed with *"trader-exit/exit.py and trader-risk/check.py have different NSE_TICKS: update both"*. The file was then put back.

---

## 13. Change 11 - Two small tidy-ups

No change in behaviour; the tests confirm it.

- **Use the existing `is_nse()` helper.** The new code had written `.endswith(".NS")` out again in several places in `exit.py`, `size.py` and `check.py`. It now calls `is_nse()`, which each script already had. If another exchange (like BSE, `.BO`) is ever added, there is one place per script to change.
- **Removed `floor_cents()`** from `trader-exit/exit.py`. Its only job (rounding the stop down to the cent) is now done by `to_tick(..., ROUND_FLOOR)`, so it was unused. Leaving it would suggest stops still round to the cent.

---

## 14. Looked at and left alone on purpose

| What | Why it was left |
| --- | --- |
| **The backup numbers got 18x bigger** (`size.py` / `check.py` `DEFAULTS`: max loss 50 to 900, max order 4,000 to 30,000) | Right for rupees. Only matters if someone trades US stocks with a config that leaves these limits out. Your config sets them all. If the project is ever shared with US traders, the fix is to make the risk check refuse to run when loss limits are missing. |
| **A typed buy limit can round up** (812.43 to 812.45) | Your decision: a small up/down is fine. See Change 8. |
| **A typed entry is not put on the step** (`exit.py --entry 3100.37` keeps 3100.37) | Entries the tool finds come from 5-minute bar closes, which are always on the step. Only a hand-typed entry is affected, by less than one step, and sizing rounds the limit before the risk check. Your decision: leave it. |
| **Is half a step of slack too generous?** | Closed by Changes 6 and 9: targets are always rounded with their own band's step, and any off-step NSE price is blocked, so the slack can only ever cover the tool's own rounding. |
| **Commit `3340561`'s message says "no paper off"** | It probably meant "paper stays on". The code is correct (`"paper": true`). The commit is already pushed, and rewriting shared history for one phrase is not worth it. |
| **Flatten steps with Interactive Brokers still mention Alpaca** | Only "none" was changed in Change 1; Alpaca and IBKR were deliberately left exactly as they were. With `"broker": "ibkr"`, `ticket.py flatten` still prints the Alpaca cancel-the-legs steps. Worth fixing if you ever use IBKR. |

---

## 15. Things to remember

- **Update your own `trader/config.json`**: set `"max_daily_loss_usd": 2700` if it still says 3,000. It is not in this repo.
- **Every March and September**: check `trader-scanner/nifty50.txt` against NSE's Nifty 50 list and update the date line.
- **If NSE changes its price steps**: update `NSE_TICKS` in **all three** files (`trader-exit/exit.py`, `trader-budget/size.py`, `trader-risk/check.py`). `tests/test_ticks.py` will fail until all three match.
- **Source for the price steps**: NSE circular CMTR67133 - https://nsearchives.nseindia.com/content/circulars/CMTR67133.pdf

---

## 16. How to check it all still works

From the project folder:

```
cd tests && python3 -m unittest discover
```

Expected: **405 tests, OK**.

If a test fails right after you edited a file and changed it back within the same second, clear Python's cache and run again - Python can keep using an old compiled copy:

```
find . -name __pycache__ -type d -prune -exec rm -rf {} +
```
