# Move-prediction profile configs

Versioned JSON configs for scoring profiles and consensus suites. Baseline presets in Python (`PRESET_SCORING_PROFILES`) stay frozen for backward comparison. Changes are **appended** as new profile IDs (e.g. `breakout_long_v1`), never in-place edits to `breakout_long`.

## Layout

```
config/move_prediction_profiles/
  README.md
  profiles/          # one JSON file per versioned profile
    breakout_long_v1.json
    ...
  suites/            # suite manifests (profile list + consensus weights)
    baseline.json          # frozen 10-profile built-in mirror
    baseline_v2.json       # realigned 11-profile consolidated suite
    active_manager_v1.json
    active_manager_v2.json # legacy 17-profile suite
    active_manager_v3.json # production consolidated 12-profile suite
    active_manager_v4.json # v3 plus upside_reversal_v1 long reversal lens
```

## Profile file (`profiles/*.json`)

Each file defines one scoring lens:

| Field | Purpose |
|-------|---------|
| `profile_id` | Runtime name (e.g. `breakout_long_v1`) |
| `base_profile_id` | Lineage (e.g. `breakout_long`) |
| `version` | Semver for this JSON revision |
| `effective_from` | When this variant became active |
| `changelog` | Short note for run comparison |
| `profile` | Full `ScoringProfile` payload (weights, biases, horizons) |

## Suite file (`suites/*.json`)

A suite selects which profiles run together and how consensus aggregates them:

| Field | Purpose |
|-------|---------|
| `suite_id` | Stored in DuckDB run metadata |
| `suite_version` | Suite manifest revision |
| `profile_names` | Ordered list of profile IDs for the run |
| `consensus_profile_weights` | Per-profile consensus weights (must sum to 1.0) |
| `inverted_consensus_profiles` | Bearish overlays sign-inverted before aggregation |
| `long_consensus_profiles` | Profiles counted as long-directional for agreement stats |

Built-in presets (no `_v1` suffix) resolve from Python. Versioned IDs resolve from `profiles/*.json`.

## Running a suite

From `src/main.py` (or any caller of the DuckDB suite):

```python
from main import MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3

base_result = run_full_analysis_suite_duckdb(
    scan_data=scan_response,
    profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
)
run_full_analysis_suite_with_earnings_priority_duckdb(
    scan_data=scan_response,
    base_result=base_result,
    profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V3,
)
```

Omit `profile_suite_path` for the original 10-profile built-in baseline (default, unchanged).

Explicit baseline JSON (same lenses as built-in default):

```python
profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_BASELINE
```

Realigned consolidated baseline (11 lenses; `active_manager_v3` is now 12 lenses with `sustained_momentum_safety_v1`):

```python
profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_BASELINE_V2
```

Legacy extended suites remain available for historical comparison:

- `active_manager_v2.json` — 17 profiles (includes `swing_reversal_v1`)
- `active_manager_v1.json` — 16 profiles

Production suite with upside reversal lens:

```python
from main import MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V4

base_result = run_full_analysis_suite_duckdb(
    scan_data=scan_response,
    profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V4,
)
```

Standalone upside-reversal calibration:

```python
from main import MOVE_PREDICTION_PROFILE_SUITE_UPSIDE_REVERSAL_V1

run_full_analysis_suite_duckdb(
    scan_data=scan_response,
    profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_UPSIDE_REVERSAL_V1,
)
```

## Style taxonomy (`active_manager_v3` / `baseline_v2`)

| Investing style | Profile |
| --- | --- |
| Continuation (pure tape) | `breakout_long_v1` |
| Sustained momentum + safety | `sustained_momentum_safety_v1` |
| Swing (coiled setup) | `early_momentum_inflection_v1` |
| Forward edge + earnings catalyst | `forward_edge_active_v2` |
| Quality-backed continuation | `quality_continuation_v1` |
| Core quality compounder | `quality_value_compounder` |
| Quality at value | `durable_value_compounder_v1` |
| Fundamental undervalue | `asymmetric_value` |
| Structural recovery | `value_recovery_v3` |
| Fortress with action | `defensive_fortress_v2` |
| Bearish overlay (inverted) | `fragility_short` |
| Extension trim overlay (inverted) | `mean_reversion_exhaustion_v1` |

## Style taxonomy (`active_manager_v4`)

| Investing style | Profile |
| --- | --- |
| Continuation (pure tape) | `breakout_long_v1` |
| Swing (coiled setup) | `early_momentum_inflection_v1` |
| Upside reversal (pullback bounce) | `upside_reversal_v1` |
| Forward edge + earnings catalyst | `forward_edge_active_v2` |
| Quality-backed continuation | `quality_continuation_v1` |
| Core quality compounder | `quality_value_compounder` |
| Quality at value | `durable_value_compounder_v1` |
| Fundamental undervalue | `asymmetric_value` |
| Structural recovery | `value_recovery_v2` |
| Fortress with action | `defensive_fortress_v2` |
| Bearish overlay (inverted) | `fragility_short` |
| Extension trim overlay (inverted) | `mean_reversion_exhaustion_v1` |

Retired from consolidated suite (JSON files kept for history): `pre_earnings_drift_v1`, `sector_rotation_momentum_v1`, `quality_growth_at_reasonable_price_v1`, `income_compounder_v1`, `swing_reversal_v1`, `deep_value_momentum`, `sector_relative_outperformer_v1`.

## Overlap audit

Run top-10 Jaccard overlap report after suite changes:

```bash
PYTHONPATH=src python scripts/run_move_prediction_profile_overlap_report.py \
  --suite config/move_prediction_profiles/suites/active_manager_v3.json \
  --scan-json path/to/scan_rows.json \
  --output logs/tradingview_analysis/prediction_analysis/active_manager_v3__overlap_report.json
```

Use `--live-scan` instead of `--scan-json` when API credentials are available.

## Tracking and comparison

Each DuckDB run stores:

- `profile_config_hash` per profile (content hash of resolved config + suite weight)
- `base_profile_id`, `profile_version`, `profile_suite_id` in config snapshots
- Result keys: `_duckdb_profile_suite_id`, `_duckdb_profile_suite_version`, `_duckdb_profile_suite_path`

Compare runs by matching `profile_suite_id` or diffing `profile_config_hash` for the same `profile_id`.

## Adding a new variant

1. Copy an existing `profiles/*_v1.json` to `profiles/breakout_long_v2.json`.
2. Set `profile_id` to `breakout_long_v2`, bump `version`, update `changelog` and weights.
3. Add the ID to a suite `profile_names` and `consensus_profile_weights` (or create `suites/active_manager_v2.json`).
4. Run with `profile_suite_path` pointing at the new suite.

Do **not** edit `breakout_long` in Python or overwrite `breakout_long_v1.json` after it has been used in production runs.

## Export helper

To regenerate JSON from Python presets (only profiles that still exist in code):

```bash
PYTHONPATH=src python scripts/export_move_prediction_profile_configs.py
```

New-only profiles (no Python preset) are authored directly as JSON under `profiles/`.
