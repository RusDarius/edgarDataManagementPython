---
name: alpha-model
description: Walk-forward, self-correcting forecast of excess return per horizon (1d / 7d / 28d / 56d, US $1B+) from the panel store - features and move-prediction scores weighted by their trailing IC on past-known days only, an out-of-sample record against walk-forward baselines, a state per horizon (OK / WEAK / DEGRADED / FAILING / NO_TRACK), calibration deciles, volatility-scaled price bands and the top drivers per name, with a live log scored as labels arrive. Use when the user asks for a predictive model, expected move or return, short-term or mid-term positioning bands, what the model says about named tickers or the book, or pastes ALPHA MODEL. Not the backward study (signal-lab), not the Daily budget, not a 0-100.
---

# Alpha model

**What does the past-only evidence forecast for each name over the next week and month, how good has that forecast been, and where are the bands?**

## Commands

```powershell
python src/generic_utils/tv_scan_cli.py alpha-model --pack PACK --no-update --ids RMBS,PWR,MU   # one line per id + summary
python src/generic_utils/tv_scan_cli.py alpha-model --pack PACK --line                          # digest: state, weights, top / bottom
python src/generic_utils/tv_scan_cli.py alpha-model --pack none --no-log --horizons 7d,28d      # experiment without touching the live log
python src/run_alpha_model.py --page                                                            # no-arg runner, + data-only page
```

The daily refresh runs it as the optional suite `alpha_model` (after `earnings_week`) and `page-all` renders the `alpha_model` template. Output `PACK/alpha_model/alpha_model.duckdb`: `state`, `oos_summary`, `oos_days`, `deciles`, `weights`, `coef_days`, `live`, `live_track`. Read `highlights.log` first; grep `queries.txt` before SQL. Knobs: `config/generic_utils/alpha_model.json`. Code: `generic_utils/alpha_model.py`.

## How to read it

1. Start at `state`. Lean on a horizon only when it is `OK` and `beats_baselines` is true. `WEAK` = positive out of sample but unproven; `DEGRADED` = lost its recent edge; `FAILING` = negative; `NO_TRACK` = too few scored days.
2. The fair bar is the walk-forward baselines (`ew_sign`, `best_single`). The fixed features in `baselines` (p_def, p_dvc...) were picked from the full-sample signal lab, so they carry hindsight.
3. `pred_<h>` is percent excess against the US $1B+ median over the horizon. It tops out near +-1.73 x slope, so read the order and `dec_<h>`; `deciles` gives what each decile actually returned out of sample. The bottom decile is often the clearest signal (laggards to avoid or trim).
4. Bands `b<h>_q20 / q50 / q80` are price levels for the horizon in a flat market (decile quantile x the name's own volatility). Use `b7d_q20` as a dip level and `b28d_q80` as a month trim level; shift both by the market's move.
5. `why_<h>` lists the largest contributions; `weights` shows what the model uses today, `coef_20d_ago` and `flips_60d` how it moved. The self-correction is that weights come only from past-known days and shrink when their own t fades.
6. Non-US names are outside the store's floor and have no forecast.

## Using it in judgment

- `pred` is a separate column and a separate order. Never add it to rr / bo / left, never make it a gate or a size. It can change Grade or timing, or flag a Book line for a closer look (bottom decile with a live leftover is a question, not an EXIT; the polar rule stands).
- Quote it with its state: "28d decile 10 (+2.6%, state WEAK, OOS IC +0.054)".
- Event plays and NEW / ADD stay inside the Daily's one budget.

## Generic vs domain

Features, labels, weights, forecasts, deciles and bands are generic (the universe floor is the only cutoff; `select_t` and `shrink_c` are statistical knobs in the recipe). Courses and sizes are judgment.
