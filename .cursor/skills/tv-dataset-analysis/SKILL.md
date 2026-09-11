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
| Price movers / supported tape | `tv_scan_cli.py movers` / price-movers |
| Book NAV / loss vs cash+equity | `tv_scan_cli.py risk` / book-risk (`--out-dir` → `book_risk.md`; canvas `book-risk-YYYYMMDD`) |
| Value / efficiency / peer + technical structure | `tv_scan_cli.py setup` / `generic_utils.setup` (`value_tech.json`; pack `val_field`; street/TV `fwd_*`) |
| Street vs pack vs model forward price | `tv_scan_cli.py forward` / `generic_utils.forward_value` / `run_operator_suites.py forward` |
| Name dossier / compare / stance | `example_entry.py lookup\|compare\|stance` |
| Manual all-suite dumps (max 1000, no industry cap) | `src/run_operator_suites.py` (`--list`, `wisdom`) |

## CLI

```powershell
$env:PYTHONPATH='src;.'
python src/generic_utils/tv_scan_cli.py inventory --db PATH.duckdb
python src/generic_utils/tv_scan_cli.py pack-focus --pack briefing_pack.json --sleeve sleeves.radar_curated_50 --out focus.csv
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
| `named --db --table --fields` | named-column fetch (ids / filter / equals). Never SELECT * |
| `history --root --glob` | oldest→latest named fields across dated DuckDBs |
| `span --csv --id-field --fields` | first/last/Δ per id from a history CSV |
| `weight-group --by --weight` | capital-style weighted group sums/means |
| `join` | join two CSVs on normalized ticker ids |
| `derive` | leftover / range / vs50 / rr (`--refresh` recomputes from a later close); `--vs-by` / `--vs-fields` attaches `{field}_vs_ind` |
| `focus` | `--where` then rank then optional cap |
| `pack-focus` | dump a compiled pack sleeve to CSV |
| `movers` | both-tail leaders/laggards; recipes pin 25 up / 25 punished (`movers_day.json`); recipe `vs_group` attaches Val/Peer (`*_vs_ind`); laggards get `down_class` from sleeves; `--out-dir` writes DuckDB + overview.log. Generic CLI with no recipe still defaults to cover 50 at 75/25. |
| `setup` | Primary value / efficiency / peer + tech labels + street/TV `fwd_*`. `--recipe value_tech.json`. `--forward` joins financial_projection. `--cover` is a size (max 1000), not a screen. |
| `forward` | Street/TV leftover targets + `finproj_*` / `fingrowth_*` join. Recipe `forward_value.json`. Cover default/max 1000, no industry cap. `--no-finproj` = street/TV only. |
| `risk` | Book NAV + loss ladders. `--out-dir` writes `book_risk.md` + csv + duckdb (default dated `book_risk_*` folder). `--pack` leftover/vs50/ATR. `--config-only` is cost-only (USD-naive on FX). Agent canvas is `book-risk-YYYYMMDD`. |
| `export` | named CSVs → DuckDB + overview.log (manual section reruns) |

Empty `--where` = no filter. No default leftover/RSI/mcap cutoff.
`--out` writes the CSV; stdout is spec-only (no row dump).

History / named (no `_tmp_*.py`):

```powershell
python src/generic_utils/tv_scan_cli.py named --db PATH.duckdb --table all_fields_rows --fields symbol,close,enterprise_value_to_revenue_ttm --id-field symbol --ids NASDAQ:MU,NASDAQ:ADBE --run-id RUN --rename enterprise_value_to_revenue_ttm=evrev --out named.csv
python src/generic_utils/tv_scan_cli.py history --root logs/tradingview_analysis/trading_view_all_fields_data --glob "**/tradingview_all_fields_*.duckdb" --table all_fields_rows --id-field symbol --ids MU,ADBE,ZS --fields close,RSI,enterprise_value_to_revenue_ttm --run-prefix tradingview_all_fields_ --newest 60 --rename enterprise_value_to_revenue_ttm=evrev,RSI=rsi --span-id-field symbol --out logs/tradingview_analysis/operator_briefing/field_history.csv
python src/generic_utils/tv_scan_cli.py history --root logs/tradingview_analysis/prediction_analysis/duckdb_runs --glob "**/move_prediction_*.duckdb" --recipe pred.profile_weeks_pivot --ids NASDAQ:MU,NASDAQ:ADBE --id-field symbol --all-runs --span-id-field symbol --span-fields bo,cont,fwd --out logs/tradingview_analysis/operator_briefing/score_history.csv
python src/generic_utils/tv_scan_cli.py span --csv field_history.csv --id-field symbol --fields close,evrev
python src/generic_utils/tv_scan_cli.py weight-group --csv book.csv --by ind --weight wt --metrics left,bo,vs_cost
python src/generic_utils/tv_scan_cli.py join --csv-a book.csv --csv-b tape.csv --left-on ticker --right-on symbol --prefix tape_ --out joined.csv
python src/generic_utils/tv_scan_cli.py setup --csv us2b.csv --peer-mcap 2000000000 --out setup.csv
python src/generic_utils/tv_scan_cli.py forward --pack briefing_pack.json --sleeve us2b --cover 1000 --out-dir RUN/forward
python src/run_operator_suites.py wisdom --cover 1000
python src/generic_utils/tv_scan_cli.py derive --csv joined.csv --close tape_close --pt pt --refresh --out live_leftover.csv
```

`pack-focus` sleeves: `sleeves.radar_curated_50` (Build-50 headline), `sleeves.radar_curated_25` (first 25), `sleeves.radar_upside_100`,
`sleeves.short_book_15_curated`, `earnings_lanes.upside_curated`, `names`.
`--sleeve us2b` on setup/forward reads `us2b_tape` from `briefing_pack.duckdb` (not the JSON).
Scoring layers: `src/generic_utils/specs/scoring_layer_order.md`.

## Modules (store procedures here)

| Module | Procedures |
|---|---|
| `ranking` | `top_n`, `group_capped_top_n`, `rank_by_weights`, `both_tails`, `horizon_movers` |
| `scan_sources` | `rows_from_csv`, `rows_from_duckdb`, `rows_to_csv` |
| `run_export` | DuckDB + `overview.log` run folder (`write_run_export`) |
| `aggregations` | `numeric_summary`, `group_stats`, `histogram`, `value_counts`, `overlap`, `weighted_group_stats` |
| `predicates` | `parse_where`, `filter_rows` |
| `derive` | leftover / range / vs-ref / `apply_derived` / `attach_vs_group` (pct vs industry median) |
| `setup` | `attach_setup` / `pick_value_field` — primary multiple + efficiency + peer + tech + street/TV `fwd_*` |
| `forward_value` | `attach_forward_value` — street/pack leftover + `financial_projection` join |
| `risk` | NAV, wt_nav, loss ladders, trim-size cash (no TRIM/EXIT cutoff) |
| `recipes` | `pred.runs`, `pred.raw_identity`, `pred.tape_returns`, `pred.profile_weeks_pivot`, `pred.conviction`, `pred.consensus_weeks`, `pred.regime` |
| `series` | `list_dated_files`, `fetch_named`, `field_history`, `group_history`, `series_span`, `join_rows` |
| `focus` | `build_focus`, `pack_path_rows` |

`operator_briefing.sleeves` imports leftover/range from `derive` and adds eligibility.

## Structural facts

- Mix = `manager_action_signal`. Rank = `rank_overall`. Regime = `active_mgmt_tier`.
- `consensus_rows` uses `ticker`; profile/conviction use `symbol`.
- Leftover = max(street PT upside, edge `forward_valuation_upside_pct`). `rr` = leftover / max(52w range, 15).
- `street_px` = analyst PT $ (or close × (1 + street leftover%)). `target_px` = close × (1 + pack leftover%).
- DuckDB reserved word: leftover column is `"left"`. `named` already quotes. Raw `--sql SELECT left` fails — quote it or pass `--csv`.
- Movers listing leaks: `drop_duplicate_id_suffixes` drops `TICKER23` when `TICKER` is in the universe (recipe `drop_duplicate_suffixes`, default on). `--keep-duplicate-suffixes` keeps them.
- DuckDB paths containing `=` must be quoted. Compare is weeks bo/cont/fwd deltas, not leftover. DAILY: `compare --session --exchanges NASDAQ,NYSE,AMEX`.

`history` / `span` use the full `EXCHANGE:TICKER` id. Do not pass bare tickers
when dual listings exist (`NASDAQ:MRVL` vs `TSXV:MRVL`). `series_span --normalize`
is opt-in. Industry `group` history on all-fields without a US/$2B filter is
not comparable to pack `industries_5d`.

See [reference.md](reference.md) for where new procedures go.
