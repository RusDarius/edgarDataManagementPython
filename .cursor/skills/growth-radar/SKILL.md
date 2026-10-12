---
name: growth-radar
description: Growth radar from all-fields plus the financial projection. Two thresholds (market cap and a growth percent), separate revenue and earnings lanes, peer discount and own-PE history. Use when the user asks for growth companies, overlooked growers, or earnings that can re-rate a name. Not the Daily leftover radar, not movers, and not a 0-100.
---

# Growth radar

**Which $1B+ names are actually growing, and which of those are still cheap versus peers or their own history?**

Daily answers leftover. This radar answers growth. Revenue and earnings stay two rankings. Do not add them into one score.

## Thresholds

They live in `run_growth_radar_local_main` inside `src/run_growth_radar.py`. Change them there for a no-arg run. CLI flags override the same knobs.

| Threshold | Default | Effect |
|---|---|---|
| Market cap | $1,000,000,000 | Names under this are not loaded. Nano and thin quotes stay off. |
| Growth | 15% | Quoted revenue growth or quoted earnings growth must clear this. A name can clear one side or both. |

`rev_growth_source` and `earn_growth_source` name which input was quoted. Projection year-1 step first, then trailing year-over-year, then CAGR. Do not average those inputs.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/run_growth_radar.py
python src/run_growth_radar.py --min-mcap 1000000000 --min-growth 15 --top 40 --industry-cap 4
```

Folder: `logs/tradingview_analysis/growth_radar/runs/growth_radar_<stamp>/`

| File | What it is |
|---|---|
| `growth_radar.duckdb` | `universe`, `revenue_growth`, `earnings_delivery`, `overlooked`, overflow, `valuation_span` |
| `queries.txt` | Column meanings and copy-paste SQL. Open this before writing SQL. |
| `overview.log` | Run header, source ids, row counts |
| `highlights.log` | The shortlist in reading order |
| `spec.json` | The thresholds that were used |

Quote leftover, when you join a pack later, as `"left"`. This radar does not compute leftover. `pe_fwd` is the projection next-year PE. `pe_fwd_peer` sits beside `pe_fwd_vs_ind`. DISCOUNT is that percent <= -5.

## How to read it

1. State the two thresholds from `spec.json`.
2. Revenue lane is sorted by `rev_growth`. Earnings lane is sorted by `earn_growth`. Rank 1 is the top of that sort after the industry cap. Overflow tables are the names the cap held back.
3. `overlooked` is a label: forward PE or forward EV/EBITDA is DISCOUNT, and `rng` is under `overlooked_rng_max` (default 60). It is not a rank.
4. `earn_outrun` means earnings growth beats revenue growth by 10 points or more. That is the results label. It does not change the sort.
5. `pe_hist_pct` is the shortlist trailing-PE span (about 12 all-fields days). CHEAPER is `pe_hist_pct` <= -5. THIN_HIST means fewer than 4 days. This is not the analysis-style path class.
6. Do not spend a Daily NEW from this file alone. A grower that is `edge_only` on the pack, paid on range, or already a book add stays on the daily budget.

## Generic vs domain

Load, quote, peer percent, industry cap, and the span are generic (`generic_utils.growth_radar`, `forward_fundamentals`, `derive.attach_vs_group`, `series`). The two thresholds and whether a name is a buy are domain. Do not copy the 15% bar into `ranking.py`.
