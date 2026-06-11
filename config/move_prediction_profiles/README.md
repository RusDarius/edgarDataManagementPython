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
    baseline.json
    active_manager_v1.json
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
from main import MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V1

base_result = run_full_analysis_suite_duckdb(
    scan_data=scan_response,
    profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_ACTIVE_MANAGER_V1,
)
run_full_analysis_suite_with_earnings_priority_duckdb(
    scan_data=scan_response,
    base_result=base_result,
)
```

Omit `profile_suite_path` for the original 10-profile built-in baseline (default, unchanged).

Explicit baseline JSON (same lenses as built-in default):

```python
profile_suite_path=MOVE_PREDICTION_PROFILE_SUITE_BASELINE
```

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
