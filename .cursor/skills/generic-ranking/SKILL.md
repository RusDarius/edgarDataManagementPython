---
name: generic-ranking
description: Deterministic top-N ranking / grouping engine any agent prompt can reuse against a CSV or DuckDB source. Use when a prompt needs "top 100 upside", "top movers", "top rankers", or an industry/setup-capped shortlist, and does not want to hand-write sort/filter logic or hardcode thresholds. Not a domain-specific screen -- it has zero embedded eligibility cutoffs.
---

# Generic ranking

Zero-cutoff top-N. Caller names the field, direction, N, and any group cap.
No RSI/leftover/mcap filters live here.

- Schema, histograms, leftover formulas, named SQL, FOCUS CSV → `.cursor/skills/tv-dataset-analysis/SKILL.md`
- Book / sleeves / polar / earnings → `.cursor/skills/daily-operator-scan/SKILL.md`

Filter then rank: `tv_scan_cli.py focus --where ...` (same engine). This CLI is rank-only.

## CLI

```powershell
$env:PYTHONPATH='src;.'
python src/generic_utils/rank_cli.py --csv PATH --field left --top 100
python src/generic_utils/rank_cli.py --db PATH.duckdb --sql "SELECT symbol, d5 FROM conviction_rankings WHERE run_id = 'RUN'" --field d5 --top 100 --asc
python src/generic_utils/rank_cli.py --csv PATH --field left --top 25 --group-field industry --cap 5 --exempt "NASDAQ:MU" --id-field symbol
```

JSON: `{"spec": {...params...}, "rows": [...], "overflow": [...]}` (`overflow` only with `--group-field`). Never `SELECT *` on `raw_scan_rows` or `all_fields_rows`.

- `--asc` = lowest-first. `--tie-breaker FIELD` uses the same direction.
- `--group-field` + `--cap` together; no default cap.
- `--exempt` always keeps those ids (Book-style).

## Python API (`generic_utils.ranking`)

- `top_n` / `top_n_by` / `rank_by_weights` (`_composite_score` is visible)
- `group_capped_top_n(..., overflow_fields=())` → `(kept, overflow)`
- Presets: `top_upside`, `top_movers`, `top_rankers` (same zero-filter contract)
- Size without a screen: `cover_rows` (optional sort, then take N). Not an industry cap. Operator dumps clamp at 1000 via `forward_value.clamp_cover` / `run_operator_suites.py`.
- Both tails / multi-horizon: `both_tails`, `horizon_movers`, `flatten_movers` (CLI: `tv_scan_cli.py movers`). Generic default cover 50 per field, 75% leaders / 25% laggards (`split_cover`). Operator recipes pin `n_leaders`/`n_laggards` (movers_day / movers_3m = 25 / 25, no industry cap). `--top N` without a recipe is still per-tail. Bounce vs continue-down is **not** in this module — `sleeves.classify_punished_tape`.
- Book NAV / loss ladders: `generic_utils.risk` / `tv_scan_cli.py risk` (zero-cutoff math). TRIM vs EXIT is **not** here.
- Identity hygiene (not an eligibility cutoff): `drop_duplicate_id_suffixes` / `ticker_of` — drop `TICKER23` when `TICKER` is also in the set.

Load rows with `generic_utils.scan_sources`. Do not add eligibility thresholds inside `ranking.py`. Echo `spec`. Overflow is never silently dropped.

Scoring layer order (edge + move prediction + leftover → Build-50): `src/generic_utils/specs/`.
