---
name: gmv-lens
description: GMV lens - growth, estimate revisions, momentum (trend quality), participation (up/down volume) and value on the all-fields day history, as five separate lenses and six weakest-lens profiles (COMPOUNDER, RERATE, BREAKOUT, VALUE_TURN, ALL5, FADE), with a walk-forward check of which lens led later closes. Use when the user asks to focus growth names, model stock movement / volume / trend over days, combine growth, momentum and valuation without hard filters, or pastes GMV LENS. Not the Daily leftover radar, not movers tape, not a 0-100.
---

# GMV lens

**Which names are growing, being revised up, trending with volume behind them, and still priced fairly, and which of those reads has actually led later closes lately?**

The growth radar (`growth-radar`) is one day and hard bars (15% growth, PE 5-40, RSI 45-70). Movement opportunity (`movement-opportunity`) is a weighted 1-10. GMV keeps every read visible and drops nobody with a threshold. A weak lens only lowers a name's place.

## Data flow

1. `generic_utils.panel.load_panel`: every `trading_view_all_fields_data/*/tradingview_all_fields_*.duckdb` (about 140 days since 29 Mar 2026), newest run per file, US venues, market cap at least half the floor. About 10 s for about 300k rows.
2. `gmv_lens.prepare_series`: split guard on per-share prints, one point per day.
3. `gmv_lens.point_features`: every lens part as of a day, from snapshots up to that day only.
4. `score_cross_section`: industry percents (pe_nfy, EV/EBITDA), part percentiles, lens = median of parts, profile floors, tape_state.
5. `join_pack`: pack `us2b_tape` overlay (left, street_left, rr, bo, mix, mtp, dte) and the Book flag. Overlay columns are support only and never a lens part.
6. `select_profiles`: top N per profile after the industry cap (overflow kept), then a Focus round-robin.
7. `lens_evidence`: the same steps on each past evaluation day (every 7 days), then rank IC against the close 14 and 28 days later.

## Lenses (parts in `config/generic_utils/gmv_lens.json`)

| Lens | Parts |
|---|---|
| G growth | fwd_rev_g, fwd_eps_g (street next FY vs TTM), rev_yoy, earn_yoy. A print beyond 300% is a base effect: left empty and named in `base_effect` |
| E estimates | eps_rev_3m, eps_rev_breadth, rev_rev_3m, pt_drift_3m, eps_surprise |
| M momentum | r_3m, trend_q (signed R squared), r_3m_per_vol, hi_prox |
| P participation | updown_vol, accum_3m, vol_trend |
| V value | pe_nfy_vs_ind, evebitda_vs_ind, peg_fwd (lower is better), pt_left |

Volatility enters in two places only: as the denominator of `r_3m_per_vol`, and as the size `exp_move_20d`. It is never a lens. `pe_nfy` (close / street next-FY EPS) is not `pe_fwd` (the projection PE).

## Commands

```powershell
python src/run_gmv_lens.py                       # data only: newest pack -> PACK/gmv (about 35 s)
python src/run_gmv_lens.py --full-flow           # produce first: all-fields export + compile + pack dumps, then GMV
python src/run_gmv_lens.py --full-flow --no-export   # same on the newest all-fields file (no TradingView export)
python src/run_gmv_lens.py --page                # + data-only page in logs/canvases/<day>/<HHMM>_gmv/
python src/run_gmv_lens.py --print-prompt        # + page + prompt.txt printed (prompt mode: paste it in the IDE)
python src/run_gmv_lens.py --agent               # + page + headless Claude run that writes pages.gmv and re-renders
python src/run_gmv_lens.py --no-evidence         # skip the walk-forward (about 20 s faster; Evidence tab says skipped)
python src/generic_utils/tv_scan_cli.py page --template gmv --day YYYYMMDD --judgment JUDGMENT --out-dir RUN_DIR --strict
```

Inside the day nothing extra is needed: `run_daily_refresh.py` runs suite `gmv`, and
`run_operator_day.py [--agent]` renders the `gmv` page beside daily / movers / playbook (the full
Daily prompt fills `pages.gmv`). Standalone prompt mode uses `src/research_prompt/agent_runs/gmv.txt`
on the day's live judgment when a Daily already ran (adds pages.gmv and new courses only), else on
`<run>/judgment.json`, so the next `run_operator_day` still runs the full Daily.

`queries.txt` starts with the task SQL. Grep the field list below it; do not read it whole.

## How to read it

1. Start with the Evidence tab: `ic_mean` and `ic_t` per lens and profile at 14 and 28 days, and `ic_recent3` / `ic_last` for a lens that changed sign (momentum often does). In the first run (23 Oct 2026, 11 days from 9 Jul to 21 Sep), G, E and V led and M and P did not. RERATE had IC 0.084, t 5.1, positive on 10 of 11 days. Momentum IC went from -0.17 in July to +0.21 on 21 Sep. Re-read it every run; do not hard-code it.
2. Focus is rank 1 of each long profile, then rank 2, and so on, up to `focus_max`. `profiles` lists every profile a name ranked in, so a name on three profiles is a stronger read than one on one.
3. Horizon: BREAKOUT, VALUE_TURN and FADE are short term (trigger and kill). COMPOUNDER, RERATE and ALL5 are mid term (entry band). `tape_state` PULLBACK on a COMPOUNDER is the rebound case. EXTENDED waits for a band.
4. A RERATE name in DOWNTREND is early or a falling knife. Momentum is not in that profile, so say which one it is (estimates still rising, a print ahead, peers bottoming).
5. FADE names are short candidates. Run the short-book check and the entry-book on them before a SHORT course. MTP is climate.
6. Book: the weakest lens of a held line is its question. For example, a held name at M15 and P18 is not getting paid for the growth yet.
7. Do not spend a Daily NEW from this page alone: the Daily budget, the polar rule, and edge_only still bind. Earnings inside 10 sessions (`dte`) is an event line.

## Generic vs domain

The panel load, features, percentiles, median-of-parts, weakest-lens order, industry cap and walk-forward IC are generic (`generic_utils.panel`, `generic_utils.gmv_lens`). Profile choice, lens parts, tape_state bands and coverage are recipe knobs (`gmv_lens.json`). Courses, sizes and the short decision stay in Daily judgment. Never add lens percentiles together, and never copy a lens into `ranking.py`.
