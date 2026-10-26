---
name: movement-opportunity
description: Score TRADING opportunity from day-to-day stock movement across all-fields, prediction, edge, MTP, projection, and backtest IC. Auditable 1-10 upside and downside. Use when the user asks where to trade movement, expected-move size, bounce vs dying path, or for the MOVEMENT copy-paste suite. Not Daily leftover, not movers tape, not Book NAV, not a 0-100.
---

# Movement opportunity

**Which names still have unused upside in the way they are moving, and which are dying / paid / fragile?**

Daily answers leftover. Movers answers tape leaders. This scan answers **trading movement**: day-to-day variation, an expected-move envelope, and a 1–10 upside (focus) plus 1–10 downside. The 1–10 is weighted visible components. It is not leftover and not a probability.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/run_movement_opportunity.py
python src/run_financial_projection.py --mode movement
```

Folder: `logs/tradingview_analysis/movement_opportunity/runs/movement_opportunity_<stamp>/`

| File | What it is |
|---|---|
| `movement_opportunity.duckdb` | `movement_tape`, `upside_50`, `downside_30`, `bounce`, overflow, `evidence` |
| `queries.txt` | Task SQL first: read the top with a line limit before SQL; grep the rest. |
| `highlights.log` | Shortlist plus cited IC |
| `spec.json` | Floor, window, weights |

Quote leftover as `"left"`. `pe_fwd` is projection `fp_pe_y1`. MTP `ENTER_SMALL` is climate.

## How to read it

1. State the market-cap floor and `history_days` from `spec.json`.
2. `upside_50` is industry-capped by `upside_10`. Rank 1 is the top of that sort. Overflow is `upside_overflow`.
3. Cite `up_asymmetric_payoff`, `up_structure_conviction`, `up_valuation_growth`, `up_timing_catalyst`, `up_move_fuel` next to the 1–10. Do not hide them.
4. `thin_up` means coverage under 0.4. Those ratings cannot print above 6.
5. `path_class` is OPENING / DYING / CHEAPER / FLAT / THIN_HIST. DYING wins. Enough history that is none of those three is FLAT. First-iteration CHEAPER uses `pe_pct` (trailing PE span).
6. `vol_class` is coiled / active_swing / high_velocity / extreme. `exp_move_20d` is ATRP × √20. Size, not quality. ATRP has negative IC as a rank.
7. Bounce table is punished names with `down_class=BOUNCE`, sorted by `upside_10`. CONTINUE_DOWN stays on `downside_30` when that 1–10 is high.
8. Table `evidence` is how well this can be done. Cited `expected_move_proxy_pct` 20d IC was 0.233 on **six** days. Decision review of leftover ranks was FLAT. Do not claim a hit rate.
9. Do not spend a Daily NEW from this file alone.

## Generic vs domain

Load, join, leftover/range, setup peer, span+steps, expected-move proxy, and the 1–10 weights live in `generic_utils.movement_opportunity`. ADD vs WAIT, polar, and starter dollars stay in Daily. Do not copy the 1–10 into `ranking.py`.

Usage README: `documentation/movement_opportunity_usage.md`.
