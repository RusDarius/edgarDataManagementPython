# Prediction module2 profile configs

Orthogonal outlook profiles for `analysis_prediction_module2.py`. These use a **different schema** from v1 move-prediction profiles (`prediction_module2_profile_v1` vs `move_prediction_profile_file_v1`).

## Layout

```
config/prediction_module2_profiles/
  profiles/          # outlook profiles with regime gates and pillars
  suites/            # suite manifests with family-orthogonal consensus
```

## Running

```python
from pathlib import Path
from data_analysis_scripts.analysis_prediction_module2 import run_module2_suite_duckdb

PREDICTION_MODULE2_SUITE_ACTIVE_MANAGER_V1 = (
    Path("config/prediction_module2_profiles/suites/active_manager_module2_v1.json")
)

result = run_module2_suite_duckdb(
    scan_data=scan_response,
    profile_suite_path=PREDICTION_MODULE2_SUITE_ACTIVE_MANAGER_V1,
    min_market_cap_usd=1_000_000_000,
)
```

Outputs land under `logs/tradingview_analysis/prediction_module2/` with per-profile CSV/log files and `module2__overlap_report.json`.

## Anti-overlap design

- **Regime gates** filter names before scoring (e.g. upside swing rejects extended tape).
- **Outlook pillars** use disjoint metric families across profiles.
- **Family-orthogonal consensus** takes the best score per outlook family, then blends — avoiding triple-counting repair signals.

## Adding a profile

1. Copy an existing `profiles/*.json` and bump `profile_id` / `version`.
2. Set a unique `outlook_family` or reuse an existing family intentionally.
3. Add the profile to a suite `profile_names`, `consensus_profile_weights`, and `outlook_families`.
