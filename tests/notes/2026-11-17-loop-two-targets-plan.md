# Two Targets in the Breakout Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each breakout row in the loop's Excel sheet shows Target 1 (`min_reward_risk` R) and Target 2 (`max_reward_risk` R); the Time Machine and the sort use Target 1.

**Architecture:** `plan_row` builds the trader-exit plan with Target 1 (so `run_tm` needs no change) and works out Target 2 from the same entry and stop with trader-exit's own `to_tick` / `tick_size`. The sheet's two target headers are built from the rows' R values. Old same-day rows in `trader/loop.json` are filled in on load.

**Tech Stack:** Python 3.8+ standard library only (the repo promises 3.8); `unittest`.

**Spec:** `tests/notes/2026-11-17-loop-two-targets-design.md`

## Global Constraints

- Only `trader-scanner/loop.py`, `trader-scanner/SKILL.md` and `tests/test_loop.py` change. No other tool changes.
- Target 1 = entry + `min_reward_risk` x (entry - stop); Target 2 = entry + `max_reward_risk` x (entry - stop); each rounded to the nearest price step for its own price (`ex.to_tick(raw, ex.tick_size(raw, sym))`).
- Both config values required, above 0, and `min_reward_risk` < `max_reward_risk`.
- Qty, max loss, cost unchanged (entry and stop only).
- Sort unchanged: `sort_rows` by Time Machine group, then lean.
- No em dashes in any text file (`tests/test_repo.py`); no new top-level folders.
- Run tests from `tests/`: `python3 -m unittest discover -q`. After editing a file and reverting it within a second, clear `__pycache__`.

## Review Focus

- `min_reward_risk` equal to or above `max_reward_risk` -> the loop refuses with a message naming both keys (test in Task 1).
- A `trader/loop.json` saved earlier today, before this change (rows with `target`, `rr`, no `target1`) -> loads, sheet shows Target 1 = old target, Target 2 blank, no crash (test in Task 2).
- An NSE stock whose Target 2 crosses a price band (entry under 1,000, Target 2 over) -> Target 2 on the 0.10 step (test in Task 1).
- A row whose plan failed (`plan is None`) -> both targets blank, verdict "Plan failed: ..." as before (covered by existing behaviour; Task 1 keeps `target1`/`target2` as `None` in the empty row).
- An empty sheet (no breakouts yet) -> headers still render as "Target 1" / "Target 2" without R values (test in Task 2).

---

### Task 1: Rules and row values for two targets

**Files:**
- Modify: `trader-scanner/loop.py` (`rules_from`, `plan_row`, `short_row`, the start message in `cmd_run`, the module docstring)
- Test: `tests/test_loop.py`

**Interfaces:**
- Produces: `rules_from(cfg) -> {"max_risk": float, "max_amount": float, "rr1": float, "rr2": float}` (no `"rr"` key any more).
- Produces: row keys `target1`, `target2` (floats or `None`), `rr1`, `rr2` (floats). The old `target` and `rr` keys are no longer written.
- Consumes: `ex.build_plan`, `ex.to_tick(x, tick, rounding)`, `ex.tick_size(price, sym)`, `ex.D` from `trader-exit/exit.py`.

- [ ] **Step 1: Update and add the failing tests in `tests/test_loop.py`**

  - `test_the_config_must_have_the_three_numbers` -> rename `test_the_config_must_have_the_four_numbers`: `rules_from(CONFIG) == {"max_risk": 50.0, "max_amount": 4000.0, "rr1": 1.0, "rr2": 2.0}`; deleting each of `max_loss_per_trade_usd`, `max_order_usd`, `min_reward_risk`, `max_reward_risk` raises `LoopError` naming the key.
  - New `test_min_reward_risk_must_be_under_max`: `rules_from(dict(CONFIG, min_reward_risk=2))` raises `LoopError` whose text contains both `"min_reward_risk"` and `"max_reward_risk"`.
  - `test_every_breakout_goes_to_the_sheet_once_sorted_and_failures_are_not_retried`: replace the `t["target"]` tuple with `(t["entry"], t["stop"], t["target1"], t["target2"], t["qty"], t["limited_by"]) == (364.18, 358.32, 370.04, 375.9, 8, "max risk")` (370.04 = 364.18 + 1.0 x 5.86). The TSLA verdict assertion must match what the Time Machine now says for Target 1: run once, read the verdict, and assert on its first words.
  - `test_target_follows_max_reward_risk` -> rename `test_targets_follow_the_config`: with `min_reward_risk=1.5, max_reward_risk=3.1`, `(t["target1"], t["target2"], t["rr1"], t["rr2"]) == (372.97, 382.35, 1.5, 3.1)` (364.18 + 1.5 x 5.86 = 372.97; + 3.1 x 5.86 = 382.346).
  - New `test_target_2_uses_its_own_nse_price_band`: call `loop.plan_row` directly with an NSE hit `{"symbol": "X.NS", "at": "10:00 AM", "or_high": 995.0, "or_low": 985.2, "price": 995.35, "bar_index": 8}`, `rules = {"max_risk": 900, "max_amount": 30000, "rr1": 1.78, "rr2": 3.45}`, a Namespace with `or_minutes=15, config=self.cfg`; stop is 990.10 (or-mid 990.10), risk 5.25, Target 1 = 1004.70 (raw 1004.695, 0.10 step), Target 2 = 1013.50 (raw 1013.4625, 0.10 step). Assert `(row["target1"], row["target2"]) == (1004.7, 1013.5)` and `plan["target"] == 1004.7`.
  - `test_a_config_without_max_reward_risk`: unchanged; add a sibling `test_a_config_without_min_reward_risk` asserting `'needs "min_reward_risk"'` in stderr.

- [ ] **Step 2: Run them and see them fail**

  Run: `cd tests && python3 -m unittest test_loop -q`
  Expected: FAIL / ERROR on the tests above (`KeyError: 'rr'`, missing `target1`, no `LoopError`).

- [ ] **Step 3: Implement in `trader-scanner/loop.py`**

  - `rules_from(cfg)`: add `rr1 = _num(cfg, "min_reward_risk")`, `rr2 = _num(cfg, "max_reward_risk")`; if `rr1 >= rr2` raise `LoopError('trader/config.json needs "min_reward_risk" (%g) under "max_reward_risk" (%g): they are Target 1 and Target 2')`.
  - `plan_row`: empty row has `target1`, `target2` = `None`, `rr1`, `rr2` from rules (drop `target`, `rr`); `build_plan` target string `"%gR" % rules["rr1"]`; after the plan, `raw2 = ex.D(plan["entry"]) + ex.D(str(rules["rr2"])) * ex.D(plan["risk_per_share"])`, `target2 = float(ex.to_tick(raw2, ex.tick_size(raw2, sym)))`; set `target1 = plan["target"]`.
  - `short_row`: print `t1 <money> t2 <money>` in place of `target <money>` (use `scan.money`; `t2` shows `-` when `None`).
  - `cmd_run` start message: `"Target 1 %gR (min_reward_risk), Target 2 %gR (max_reward_risk); the Time Machine runs on Target 1"` in place of `"Target %gR (max_reward_risk)"`.
  - Module docstring: replace the `target` line with `target1` / `target2` lines and say the odds are for Target 1.

- [ ] **Step 4: Run and see them pass**

  Run: `cd tests && python3 -m unittest test_loop -q`
  Expected: OK (the sheet tests in Task 2 may still fail on the "Target" header; that is Task 2).

---

### Task 2: Sheet columns, old rows and the instructions

**Files:**
- Modify: `trader-scanner/loop.py` (`COLUMNS`, `sheet_xml`, `load_state`)
- Modify: `trader-scanner/SKILL.md` (the loop table row "Target" and the worked example)
- Test: `tests/test_loop.py`

**Interfaces:**
- Consumes: row keys `target1`, `target2`, `rr1`, `rr2` from Task 1.
- Produces: `column_heads(rows) -> list` of header strings, one per `COLUMNS` entry; the `target1` header is `"Target 1 (%gR)" % rr1` and `target2` is `"Target 2 (%gR)" % rr2`, taking `rr1`/`rr2` from the first row that has them, else plain `"Target 1"` / `"Target 2"`.

- [ ] **Step 1: Update and add the failing tests**

  - `test_the_sheet_is_a_real_xlsx_in_sorted_order`: header list becomes `("Rank", "Symbol", "Breakout time", "Entry", "Stop", "Target 1 (1R)", "Target 2 (2R)", "Qty", "Target 1 first", "Verdict")`; assert `">R:R<"` and `">Target<"` are not in the sheet.
  - New `test_an_empty_sheet_has_plain_target_heads`: `loop.column_heads([])` contains `"Target 1"` and `"Target 2"`.
  - New `test_rows_saved_before_two_targets_still_load`: write a `loop.json` for `DAY` whose one row has `"target": 375.9, "rr": 2.0` and no `target1`; `load_state(path, DAY)["rows"][0]` has `target1 == 375.9`, `target2 is None`; `loop.sheet_xml(rows)` does not raise.

- [ ] **Step 2: Run and see them fail**

  Run: `cd tests && python3 -m unittest test_loop -q`
  Expected: FAIL on the header and the old-row tests.

- [ ] **Step 3: Implement**

  - `COLUMNS`: replace `("Target", "target", "price", 11)` and `("R:R", "rr", "price", 6)` with `("Target 1", "target1", "price", 15)` and `("Target 2", "target2", "price", 15)`, placed right after "Stop"; rename `"Target first"` to `"Target 1 first"` (width 13).
  - `column_heads(rows)` as in Interfaces; `sheet_xml` writes `column_heads(rows)` in row 1.
  - `load_state`: for each row, `setdefault("target1", r.get("target"))`, `setdefault("target2", None)`, `setdefault("rr1", r.get("rr"))`, `setdefault("rr2", None)`.
  - `trader-scanner/SKILL.md`: the table's "Target" row becomes two rows (Target 1: entry + `min_reward_risk` x (entry - stop); Target 2: entry + `max_reward_risk` x (entry - stop); both on the price step), a sentence that the Time Machine odds and the sort are for Target 1, and the worked example's target line updated to the values the TSLA test asserts (Target 1 370.04 at 1R, Target 2 375.90 at 2R).

- [ ] **Step 4: Run the whole suite**

  Run: `find .. -name __pycache__ -prune -exec rm -rf {} + ; cd tests && python3 -m unittest discover -q`
  Expected: OK, 405 + the new tests.

- [ ] **Step 5: Live check and hand over**

  Run `loop.py run --offline ... --tm-offline ... --symbols TSLA,MSFT` as in `test_the_sheet_is_a_real_xlsx_in_sorted_order` into a scratch folder and open the sheet's XML to confirm the two target columns and the R values in their headers. Do not commit until the user says so.
