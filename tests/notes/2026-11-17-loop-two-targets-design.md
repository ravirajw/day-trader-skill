# Design: two targets in the breakout loop sheet - 17 Nov 2026

Status: approved by the user on 17 Nov 2026.

## Why

Orders are placed by hand, watching prices in the breakout loop's Excel sheet (`trader/orb-YYYY-MM-DD.xlsx`). The user wants two exit prices per breakout on the sheet, and the odds (and the sort) to be about the nearer one.

## Goals

- Each breakout row shows **Target 1** = entry + `min_reward_risk` x risk (1.78R in the config) and **Target 2** = entry + `max_reward_risk` x risk (3.45R).
- Both targets are on NSE's price step for their own price (the same rounding trader-exit uses: nearest step, half up).
- The Time Machine runs on entry, stop and **Target 1**.
- The sheet stays sorted by the Time Machine verdict (now about Target 1).
- `loop.py show` prints both targets.

## Non-goals

- No change to trader-exit, trader-budget, trader-risk, trader-broker, the Time Machine itself or any other tool. Orders are placed manually.
- No second Time Machine run for Target 2.
- No quantity split between targets.

## Constraints

- `min_reward_risk` and `max_reward_risk` must both be set and above 0, and min must be under max; otherwise the loop stops with a clear message.
- Qty, max loss and cost depend only on entry and stop, so they do not change.
- `trader/loop.json` rows saved earlier the same day (before this change) have one target (`target` at `rr`): it goes under the column whose R matches the config (it was built at `max_reward_risk`, so normally Target 2); if neither matches, the target is written into the verdict instead. Its odds were worked out for that old target, so unless it is Target 1 the verdict ends with a note saying which target the odds are for (added once). `loop.py show` must not crash on them and never changes a row. (Changed after the final review: the first version put the old target under Target 1, which mislabelled it.)
- Repo layout test allows no new top-level folders: design and notes live under `tests/notes/`.

## Approaches

1. **Chosen - plan once with Target 1, compute Target 2 from the same plan.** `build_plan` runs with `"<min>R"`, so the plan (and the Time Machine input) is Target 1. Target 2 is entry + max x risk, rounded with trader-exit's own `to_tick` / `tick_size`. One plan, one Time Machine run.
2. Run the Time Machine twice, once per target - doubles the slow Yahoo calls every run; odds on Target 2 were not asked for.
3. Teach trader-exit to plan two targets - touches the order tools the user wants left alone.

## Design

- `rules_from(cfg)`: returns `rr1` (`min_reward_risk`) and `rr2` (`max_reward_risk`) instead of `rr`; refuses rr1 >= rr2.
- `plan_row(...)`: builds the plan with target `"%gR" % rr1`; sets `target1` = plan target, `target2` = `ex.to_tick(entry + rr2 x risk, ex.tick_size(raw, sym))`; keeps `rr1`, `rr2` on the row.
- `run_tm(...)`: unchanged (it already uses the plan, which now carries Target 1).
- `COLUMNS`: "Target" and "R:R" replaced by "Target 1 (1.78R)" and "Target 2 (3.45R)" (headers built from the config's rules, never from the rows); "Target first" becomes "Target 1 first". A row found while the config had other R values says so in its verdict cell.
- `short_row(...)`: prints `t1` and `t2`.
- Rows from an older `loop.json` (key `target`, no `target1`): `upgrade_row` places the old target by its R (see Constraints), used by both `load_state` and `loop.py show`.
- Docstring and `trader-scanner/SKILL.md` updated to describe the two targets.

## Left to the builder

- Exact wording of the error messages.
- Test fixtures: extend the existing `tests/test_loop.py` config with `min_reward_risk`.
