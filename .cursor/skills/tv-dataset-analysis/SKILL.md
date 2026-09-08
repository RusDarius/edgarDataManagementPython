---
name: tv-dataset-analysis
description: Generic, zero-cutoff interrogation of TradingView scan datasets (move-prediction DuckDB, all-fields, edge CSVs, MTP CSVs, briefing-pack JSON). Use when an agent would otherwise write SQL, `_tmp_*.py`, industry breadth, histograms, leftover/range formulas, overlap, or a FOCUS CSV; or when the operator wants a deterministic scan they can run without agent judgment.
---

# TradingView dataset analysis (generic)

Deterministic fetch / aggregate / prune. Not a buy list.
Domain screens stay in `.cursor/skills/daily-operator-scan/SKILL.md`.
Rank-only top-N: `.cursor/skills/generic-ranking/SKILL.md`.

Do not write `logs/_tmp_*.py`. Do not `SELECT *` on `all_fields_rows` (~3.5k cols)
or `raw_scan_rows` (~200 cols). Echo the `spec` block.

| Need | Use |
|---|---|
| Top-N / group cap, no filter | `rank_cli.py` / `generic_utils.ranking` |
| Schema, stats, histogram, overlap, SQL, FOCUS | this skill / `tv_scan_cli.py` |
| Daily Book + sleeves + polar | `operator_briefing` / daily-operator-scan |
| Name dossier / compare / stance | `example_entry.py lookup\|compare\|stance` |

## CLI

```powershell
$env:PYTHONPATH='src;.'
python src/generic_utils/tv_scan_cli.py inventory --db PATH.duckdb
python src/generic_utils/tv_scan_cli.py pack-focus --pack briefing_pack.json --sleeve sleeves.radar_curated_25 --out focus.csv
python src/generic_utils/tv_scan_cli.py focus --csv PATH --where "left>=25,rsi<=68" --field left --top 100 --group-field industry --cap 5 --out focus.csv
```

| Command | Purpose |
|---|---|
| `recipes` | list named SQL |
| `inventory --db` | tables + columns (wide tables previewed) |
| `summary --fields` | n/mean/median/min/max/pct_positive |
| `group --by --metrics` | group means; optional `--pct-positive` |
| `histogram --field --bins` | caller bin edges |
| `counts --field` | value counts |
| `overlap --csv-a --csv-b` | set overlap |
| `fetch --recipe --db` | named SQL; `--out` writes full CSV, stdout capped |
| `focus` | `--where` then rank then optional cap |
| `pack-focus` | dump a compiled pack sleeve to CSV |

Empty `--where` = no filter. No default leftover/RSI/mcap cutoff.
`--out` writes the CSV; stdout is spec-only (no row dump).

`pack-focus` sleeves: `sleeves.radar_curated_25`, `sleeves.radar_upside_100`,
`sleeves.short_book_15_curated`, `earnings_lanes.upside_curated`, `names`.

## Modules (store procedures here)

| Module | Procedures |
|---|---|
| `ranking` | `top_n`, `group_capped_top_n`, `rank_by_weights` |
| `scan_sources` | `rows_from_csv`, `rows_from_duckdb`, `rows_to_csv` |
| `aggregations` | `numeric_summary`, `group_stats`, `histogram`, `value_counts`, `overlap` |
| `predicates` | `parse_where`, `filter_rows` |
| `derive` | leftover / range / vs-ref / `apply_derived` |
| `recipes` | `pred.runs`, `pred.raw_identity`, `pred.profile_weeks_pivot`, `pred.conviction`, `pred.consensus_weeks`, `pred.regime` |
| `focus` | `build_focus`, `pack_path_rows` |

`operator_briefing.sleeves` imports leftover/range from `derive` and adds eligibility.

## Structural facts

- Mix = `manager_action_signal`. Rank = `rank_overall`. Regime = `active_mgmt_tier`.
- `consensus_rows` uses `ticker`; profile/conviction use `symbol`.
- Leftover = max(street PT upside, edge `forward_valuation_upside_pct`). `rr` = leftover / max(52w range, 15).
- DuckDB paths containing `=` must be quoted. Compare is weeks bo/cont/fwd deltas, not leftover.

Operator check: `inspect` → `compile` if ids moved → `pack-focus` on `radar_curated_25` → open the CSV. Judgment stays with you.

See [reference.md](reference.md) for where new procedures go.
