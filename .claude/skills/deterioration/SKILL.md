---
name: deterioration
description: Book deterioration outlook - for every Book line (and named ids) whether a drawdown is only price or the business is deteriorating too (letters E estimate cut, R revenue cut, C negative FCF, S last miss), what that state did next on the panel store (mean excess, the bad case, odds of a further -10% and of regaining half the gap, by market regime and 12-month run), the dollars on the line, the recovery and kill levels, and a verdict with both paths. Use when the user asks about deteriorating positions, structural risk, cutting losses vs holding for a recovery, double-down candidates, a downturn plan for the Book, or pastes DETERIORATION. Not book-risk (NAV ladders), not a 0-100, not a second budget.
---

# Deterioration

**Is the drawdown only price, or is the business going with it? What did that state do next, and what is it worth on this line?**

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py deterioration --pack PACK --ids GOOG,MU,WDC --line            # Book + named rows, one line each
python src/generic_utils/tv_scan_cli.py deterioration --pack PACK --first-day 2026-07-01 --out-dir PACK/deterioration_jul --quiet   # regime check
python src/generic_utils/tv_scan_cli.py recovery --pack PACK --line                                    # recovery ladder per Book line (average-down math)
python src/generic_utils/tv_scan_cli.py page --template deterioration --day YYYYMMDD --judgment J --out-dir RUN_DIR --strict
```

**Recovery ladder** (`generic_utils/recovery_ladder.py`, knobs `config/generic_utils/recovery_ladder.json`, refresh suite `recovery`
after deterioration): `PACK/recovery_ladder/recovery_ladder.duckdb` `lines` (each Book line's cost, P&L, the rise from the close back to
the average `be_from_close_pct` and to +10%, its 50-day / half-way / street / bounce levels and state, and `add_be_at_<level>_usd`: the add at
the band after which a return to that level breaks the line even) and `ladder` (line x buy level close / bounce_high / bounce_low x add size:
`new_avg`, `avg_cut_pct`, `be_from_add_pct`, `be_vs_sma50_pct`, the add's and the line's loss at the kill). Prices in the quote currency;
the street price is left empty on non-USD lines. Scenarios only: the add is still judged by the rules below.

**Recovery clock** (`generic_utils/recovery_clock.py`, knobs `config/generic_utils/recovery_clock.json`, on demand):
`tv_scan_cli.py recovery-clock --pack PACK --ids A,B --line` -> `PACK/recovery_clock/recovery_clock.duckdb`. Every $2B+ name is a start
once a week in its deterioration state; `cells` (study x cell: `fwd_<W>` where it ended, `fx_<W>` vs the median start, `floor_<W>` the
path low first, `p_floor<k>_<W>`, `trough_<W>_med`, `p_half_<d>` / `p_high_<d>`), `curves` (cell x rise / half / high x checkpoint:
Kaplan-Meier `p_reach`, `n_obs`, `med_days`; empty under the observation floor), `lines` (each Book line: `need_<level>_pct` to cost,
cost +10%, the 50-day, half-way, street, high; `p_<need>_<d>` and `days_<need>` from the primary cell, the floor in price and USD) and
`reach` (the same per cell kind: dd, cnt, run, mkt). Answers "how long back, how far first, is staying worth it": read the 12-month-run
cell beside the count cell (crowding shows only there), `names_<W>` not `n_<W>`, and a touch is not a hold.

The daily refresh runs it as the optional suite `deterioration` after week_plays (Book only); record a call with named ids in the judgment `scans` so UPDATE replays it. Code `generic_utils/deterioration.py`, knobs `config/generic_utils/deterioration.json` (bucket edges, letter rules, splits, studies, the cell row floor), page `config/generic_utils/pages/deterioration.json`.

`PACK/deterioration/deterioration.duckdb`:

- `cells`: one row per study and cell. Studies: drawdown; x deterioration count; x letters; x count x 1m direction / 12m run / market / trend; x 12m run; x market; x trend; x street; and one per letter (`drawdown x E` ...). Per horizon (7d / 28d / 56d): `fx_<h>_mean / _med / _hit / _q10 / _q90`, `fx_<h>_t` (cell daily means on non-overlapping days, `fx_<h>_n_indep` of them), `fx_<h>_t_nw`, `fwd_<h>_mean` (raw), `p_dn_<h>` / `p_up_<h>` (raw -10% / +10%), `p_half_<h>` (half the gap to the 52-week high regained).
- `names`: every US floor name of the newest store day plus the Book and named ids: `dd`, `det` (empty when a letter's field is unknown), `det_seen` (`?` per unknown), `n_det`, the cells it sits in (`cnt_*`, `let_*`, `cdir_*`, `crun_*`, `cmkt_*`, `ctr_*`), estimate / target revisions, `opm_chg_q` / `rev_yoy_chg_q` / `fcf_margin_chg_q` (vs the panel row about 91 days earlier), levels (`sma50`, `sma200`, `half_px`, `hi52`, `lo52`, `to_*_pct`), joins (Book value, alpha bands, entry book / safety band, lenses) and `carry_<h>_usd` / `tail_<h>_usd` (Book dollars x the cnt cell's mean / q10).
- `letters`: the letter rules in force.

## How to read it

1. Depth alone is not the signal: deeper drawdowns carry a fatter left tail (q10 about -11% at 0-10% under the high, about -21% at 50%+) and rarely regain half the gap inside two months (`p_half_56d` 2-9% beyond 35%). Hold for the drift, not for the old high.
2. Letters beyond price separate recovery from continuation. On 2026-03-29..10-29: a 20-50%+ drawdown with no letter beat the median by +1.9 to +6.5 over 28 days (hit 56-65%); with 3+ letters -0.2 to -0.9, and -4.2 to -6.5 when the market's trailing month was down. C (cash burn) is the most consistent single letter; E and R next.
3. Trend under the 200-day and street target cuts follow the price; they are splits, not letters (in deep drawdowns both read the wrong way round).
4. Crowded winners are the exception: 12-month +100%, 10-35% under the high, even with no letter, kept unwinding (-2.2 over 28 days, 34% odds of -10% more). Do not add.
5. `mkt` matters: deep drawdowns bounced in up months and kept falling in down months. Read `cmkt_*` for the regime you are in.
6. The letter-count cells start about three months into the store (3-month revisions need history): about three independent 28-day periods. Direction, not proof. Re-run `--first-day` after a regime turn.
7. `carry_<h>_usd` is what the state's median-relative drift was worth on the line, not a forecast; `tail_<h>_usd` is its bad case. Tails of one cluster are not additive: they arrive together.

## Using it in judgment

- Price-only damage (no letter) is held, not cut; trim a third on a close under its kill, never the whole line.
- A line with 2+ letters that closes under its bounce kill is cut (polar rule still binds a prior ADD / NEW: thesis_kill).
- A new letter on a held line is the review trigger, not the price.
- Double-down asks: cite the state cell, the print cell (week_plays) and valuation; add only in the bounce band, never on a reaction day, never into a crowded-winner cell. Quote the recovery ladder: add only where the new break-even sits under the 50-day (`be_vs_sma50_pct` < 0); a large gap to cost means an add buys exposure, not recovery.
- Cap the correlated cluster by NAV (industry `nav_pct_after` from capital-plan), not the line.
- Fill `pages.deterioration` (verdicts `[[Symbol, NAV %, State, Verdict, Recovery path, Deterioration path, Levels, Why (data)]]`, `stats`, `read`, `concentration`, `path`, `rules`, the tab callouts, `sources`). Standing rules and evidence: `documentation/market_insights/deterioration_playbook.md`.

## Generic vs domain

The state labels, cells, joins and dollars are generic (recipe edges and letter rules are knobs, no ranking, no composite). Hold / cut / add, the cluster cap and the verdict text are judgment.
