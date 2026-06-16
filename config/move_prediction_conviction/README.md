# Move-prediction conviction mode

Config-driven daily focus list for v1 move-prediction DuckDB runs. Full universe
scoring is unchanged; conviction mode ranks all candidates and displays up to
`max_display_total` names split across short / mid / long sleeves.

Default: `active_manager_v1.json` — pass via `conviction_mode_config_path` on
`run_full_analysis_suite_duckdb` or `run_full_analysis_suite_with_earnings_priority_duckdb`.
