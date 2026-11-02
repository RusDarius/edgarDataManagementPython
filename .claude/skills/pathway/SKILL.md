---
name: pathway
description: Investing pathway for the Book from here - its factor tilt (NAV-weighted percentile per factor: quality, FCF, beta, volatility, street upside, momentum, distance to the high) beside what each factor has paid (signal-lab IC full sample, this month, the trailing tape), the 1M / 3M return gap split into industry choice vs stock picking, the regime series (breadth, share above the 200-day, the market's month), and what each trim or $5,000 starter does to the tilt; turned into stages with regime triggers. Use when the user asks for the overall investing path, how to get out of a drawdown or a dry spell, rebalancing, style / factor exposure, whether losses are stock picking or sector, when to lean back into a theme, or pastes PATHWAY. Not the Daily budget, not a 0-100.
---

# Pathway

**Where is the Book leaning, what has that lean paid, is the gap picking or industry, and which next trade moves the lean toward what pays?**

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py pathway --pack PACK --ids HIG,EOG,AMP --line     # exposure rows + attribution, one line each
python src/generic_utils/tv_scan_cli.py page --template pathway --day YYYYMMDD --judgment J --out-dir RUN_DIR --strict
python src/generic_utils/tv_scan_cli.py capital-plan --judgment J --key capital_plan_pathway --pack PACK/briefing_pack.json --out-dir PACK/capital_plan_pathway --quiet
```

The daily refresh runs `pathway` after deterioration (Book only); record a call with named candidates in the judgment `scans`. Code `generic_utils/pathway.py`, knobs `config/generic_utils/pathway.json` (factor list, IC horizons, tape window, starter / trim sizes, joins), page `config/generic_utils/pages/pathway.json`.

`PACK/pathway/pathway.duckdb`:

- `exposure`: per factor `book_pctl` (0.5 = the median name), `gap`, `coverage_pct`, `ic_28d` / `verdict_28d` / `t_indep_28d`, `ic_up_mkt` / `ic_down_mkt`, `ic_month` / `ic_prev_month` (7d IC by calendar month), `ic_tape` / `ic_tape_prev` / `tape_pos` (trailing 20 labelled days), `tilt_month` / `tilt_tape` (gap x IC).
- `lines`: per Book line `r_1m` / `r_3m` with `mkt_*`, `ind_*`, `rel_*`, `ind_part_*`, `stock_part_*`; `pctl_<f>` and `d_<f>_trim` for the move factors; joins (letters, alpha band, safety band, the industry's month read).
- `attribution`: NAV-weighted `book_rel` / `book_ind_part` / `book_stock_part` and their dollars per horizon.
- `regime`: per store day `mkt_1d`, `mkt_1m`, `breadth_1d`, `above_200d`, `med_rvol_20`, `med_hi_prox`.
- `factor_tape`: per factor and day `ic`, `ic_trail`.
- `candidates`: every floor name outside the Book with `pctl_<f>`, `d_<f>_add`, joins.

## How to read it

1. A positive `tilt_*` is a lean that was paid; a negative one cost. Read the factors one at a time; never add the tilts.
2. `ic_month` and `ic_tape` are recent; `ic_28d` / verdict is the long record. A factor that is STRONG on the record and negative on the tape is a regime bet (beta, momentum, street upside here).
3. Attribution: a large `ind_part` with a small `stock_part` means the industries chose the result, not the picks. Change the mix (where new money goes), not the selection method.
4. `d_<f>_add` is usually several times `d_<f>_trim` on the same factor: the tilt moves with where new money goes, not with selling drawdowns.
5. The factor tape lags by its horizon (7d labels): the 1-day IC in `signal_lab.factor_days` is the early, noisy read.
6. Lines outside the US store (XETR, OMXCOP) have no percentile: say what `coverage_pct` leaves out.

## Using it in judgment

- Write `pages.pathway`: `stages` `[[Stage, When, Action, Trigger to move on, Size / cash, Evidence]]`, `triggers` `[[Read, Now, Lean back into the AI cluster when, Go more defensive when, Source]]` (rename the theme in the text, not the header), `stats`, `read`, `checkpoints`, `rules`, the tab callouts (`tilt_read`, `attribution_read`, `regime_read`, `rebalance_read`) and `sources`.
- Stage money through `capital_plan.capital_plan_pathway` and `capital-plan`; keep cash at the level the plan states. A diversifier still needs a band, a kill and a course, and spends the Daily's one budget (one NEW per Daily).
- Re-read the triggers every Daily; move a stage only when its trigger holds. Standing rules: `documentation/market_insights/investing_pathway.md`.

## Generic vs domain

Percentiles, ICs, the return split, the deltas and the regime series are generic (no cutoffs beyond the floor). The stages, triggers, the cluster cap and the cash floor are judgment.
