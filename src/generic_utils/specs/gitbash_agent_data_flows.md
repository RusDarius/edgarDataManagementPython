# Git Bash — raw data for each agent prompt flow

From repo root. Same list as `python src/run_operator_suites.py --gitbash`.

These commands write the **raw files the model reads** (DuckDB / CSV / JSON / stdout). Canvases (`daily-focus`, `movers-YYYYMMDD`, `book-risk`, `capture-YYYYMMDD`) are a later agent step.

```bash
export PYTHONPATH=src:.
```

PowerShell: `$env:PYTHONPATH='src;.'`

Cover on generic dumps is a **size** (max 1000), not leftover/RSI eligibility and not an industry cap. Build-50 inside compile stays domain-capped.

Paste blocks live in `src/research_prompt/agent_copy_paste.txt`.
Short explainers, manual commands, and where to open the latest folder: `src/research_prompt/run_catalog.txt`.
Each DuckDB folder includes `queries.txt` (fields and the queries). Canvas only when the paste says `canvas output`. Wisdom always gets a ledger canvas.

---

## Prompt | Command | Usage / output

| Prompt | Command | Usage / output |
|---|---|---|
| DAILY (full) | `$ python src/operator_briefing/example_entry.py inspect && python src/operator_briefing/example_entry.py compile` | Lock leftover, Build-50, Book, shorts, earnings, movers_tails. Compile only if inspect sources moved. **Output:** newest `briefing_pack_*/` with `briefing_pack.duckdb`, `queries.txt`, `overview.log`. Rank tables: `radar_curated_50`, `radar_upside_100`, `short_book_15_curated`, `movers_tails`. |
| THEMED | `$ python src/operator_briefing/example_entry.py inspect && python src/operator_briefing/example_entry.py compile` | Same pack as DAILY. Agent overlay is `daily_themed_chips_software.txt`. Leadership from pack `industries_5d`. **Output:** same `briefing_pack_*` as DAILY (full). |
| DAILY (pack already good) | `$ python src/operator_briefing/example_entry.py inspect` | Confirm pack sources match disk. Do not recompile. Agent reads the pack. **Output:** stdout inspect JSON; reuse newest `briefing_pack.md` / `.json` / `.duckdb`. |
| STALE CHECK / RECOMPILE | `$ python src/operator_briefing/example_entry.py inspect` | Diff inspect.sources vs pack sources (pred / all-fields / edge / holdings / MTP). Mismatch → compile. **Output:** stdout JSON (locked run ids). Compile output same as DAILY (full). |
| MOVERS (day / 5D / 1M) | `$ python src/run_operator_suites.py movers` | Both-tail 25 up / 25 punished per horizon. Val/Peer/Proj/Tech. **Output:** `briefing_pack_*/movers/` — `movers.duckdb` table `movers_tails`, `movers_tails.csv`, `us2b_tape.csv`, `overview.log`. |
| MOVERS (3M) | `$ python src/run_operator_suites.py movers-3m` | Same as movers, recipe `movers_3m.json`. **Output:** `briefing_pack_*/movers_3m/` — `movers.duckdb`, `movers_tails.csv`, `overview.log`. |
| RISK (Book loss vs NAV) | `$ python src/run_operator_suites.py risk` | NAV ladders from latest holdings scan + pack leftover. No TRIM/EXIT in the dump. **Output:** `briefing_pack_*/book_risk/` — `book_risk.md`, `book_risk.csv`, `risk_levels.csv`, `capital.csv`, `industry_exposure.csv`, `book_risk.duckdb`, `overview.log`. |
| CAPTURE (whole book 90d) | `$ python src/run_operator_suites.py capture` | Whole-book membership + all-fields fundamentals path + prediction progression path. Existing on-disk data only; deterministic flags. **Output:** `briefing_pack_*/capture/` — `capture.duckdb` tables `book_asof`, `book_names`, `exits`, `fund_path`, `fund_span`, `score_path`, `score_span`, `jobs`, `coverage_fill_rates`, `ic_snapshot`, plus `capture.md`, `overview.log`. |
| CAPTURE-REPLAY | `$ python src/run_operator_suites.py capture-replay` | Walk-forward capture job tags vs later closes on the dump. PIT = no future leak into flags; hindsight = end-of-window jobs. Any window: `--af-start` / `--af-end --rebuild`. **Output:** `CAPTURE_DIR/replay/` — `capture_replay.duckdb` tables `replay_sleeves`, `replay_tune`, `replay_first_hit`, `replay_paths`, `replay_events`, plus `capture_replay.md`, `overview.log`. |
| FORWARD / Value | `$ python src/run_operator_suites.py value-tech --cover 1000 --forward` | Primary `val_field` / peer / tech. `pe_fwd` is projection `fp_pe_y1`. `--forward` also joins model terminals. Cover 1000, no industry cap. **Output:** `briefing_pack_*/value_tech/` — `setup.duckdb`, `setup.csv`, `overview.log`. |
| FORWARD (street vs model) | `$ python src/run_operator_suites.py forward --cover 1000` | Street PT leftover, pack leftover, projection `pe_fwd` (`fp_pe_y1` / `fp_pe_y5`), `fp_evebitda_y1`, `fp_net_debt_y1`, `fp_fcf_y1`, `fp_terminal_px` / `fp_rev_cagr_own`. `pe_fwd_tv` stays empty. **Output:** `briefing_pack_*/forward_value/` — `forward_value.duckdb`, `forward_value.csv`, `overview.log`. |
| FORWARD (run model first) | `$ python src/run_financial_projection.py --top-n 1000 && python src/run_financial_projection.py --mode price --top-n 1000` | Optional before forward join if dumps are stale. Growth default; `--mode price` = terminals. **Output:** `logs/.../financial_projection/<dd_mm_yyyy>/fingrowth_*.duckdb` and `finproj_*.duckdb` (`growth_summary` / `projection_summary`). |
| LOOKUP / PRUNE / DELEGATE | `$ python src/operator_briefing/example_entry.py lookup MU SNDK PGY JD` | Name dossier from the locked pack. Stance line: `python src/operator_briefing/example_entry.py stance MU SNDK`. Redirect to keep a file. **Output:** stdout JSON (`stance`, `suggested_conviction`, `left`, `rsi`, `bo`, `d_bo`, `mix`, `mtp`, `dte`, `conflicts`, `course`). Optional: `> lookup.json`. |
| COMPARE (pred progression) | `$ python src/operator_briefing/example_entry.py compare --session --exchanges NASDAQ,NYSE,AMEX` | Weeks `bo`/`cont`/`fwd` deltas vs prior calendar-day pred run. Not leftover. Optional `--ids MU,SNDK,...`. **Output:** stdout JSON. Optional: `> compare.json`. |
| BOOK ONLY | `$ python src/operator_briefing/example_entry.py inspect && python src/operator_briefing/example_entry.py compile` | Same pack as DAILY; agent uses `book` + `capital` only. Compile if holdings/tape ids moved. **Output:** `briefing_pack.duckdb` tables `book`, `capital`, `risk_levels`, `industry_exposure`; JSON `pack.book` / `capital`. |
| BRIEF HISTORY | `$ python src/generic_utils/tv_scan_cli.py briefs` | Prior operator briefs versus later pack closes. **Output:** `brief_history_*/` — `digest.md` (no notes), `brief_history.duckdb` tables `sessions`, `names`, `events`, `adapt`, `queries.txt`, `overview.log`. |
| DECISION REVIEW | `$ python src/run_decision_review.py` | Published upside 100 versus later pack closes, plus a KEEP / CHANGE matrix. **Output:** `decision_review_*/` — `highlights.log`, `decision_review.duckdb` tables `ranks`, `coverage`, `quality`, `decisions`, `matrix`, `signal_support`. Does not rerun `run_backtests.py`. |
| DETERMINISTIC FOCUS | `$ python src/run_operator_suites.py pack-focus --cover 1000` | Raw Build-50 + uncapped Top 100 + leftover-sorted 1000-name tape. No agent judgment. **Output:** `briefing_pack_*/pack_focus/` — `pack_focus.duckdb` tables `radar_curated_50`, `radar_upside_100`, `us2b_cover`, plus CSV + `overview.log`. |
| DATASETS | `$ python src/run_operator_suites.py sources` | Lock all-fields, prediction, edge, MTP, latest `family_metrics.csv`, and finproj/fingrowth. No rescan. **Output:** `briefing_pack_*/datasets/` — `datasets.duckdb` tables `datasets` and `family_metrics`. Wisdom writes the same folder under `suites_*/datasets/`. |
| WISDOM / ALL SUITES | `$ python src/run_operator_suites.py wisdom --cover 1000` | DuckDB for every suite under `suites_*/`, including `datasets/` for the six sources. Root `queries.txt` points at each child `queries.txt`. Agent always adds canvas `wisdom-YYYYMMDD` with one Ledger. Name tables only if the paste says `data lists`. |
| WISDOM + refresh finproj | `$ python src/run_operator_suites.py wisdom --run-finproj --cover 1000` | Re-run growth+price for 1000 names, then the wisdom dumps. **Output:** `fingrowth_*` + `finproj_*` under `financial_projection/` plus `suites_*/` as above. |
| PLAYBOOK (manual dump) | `$ python src/run_operator_suites.py playbook --weeks 25` | Upside 100, curated shorts, forming, Book, and 25-week bo/cont/fwd path. No mesh and no canvas. **Output:** `briefing_pack_*/playbook/` — `playbook.duckdb`, `playbook.json`, `overview.log`, `weeks_progression/`. |
| PLAYBOOK (agent canvas) | `$ python src/run_operator_suites.py playbook --weeks 25` | Same dump. Agent writes `playbook-YYYYMMDD` from those files. Mesh and Best 25 stay judgment. **Output:** the dump plus the canvas. |
| ANALYSIS STYLE (active management) | `$ python src/run_operator_suites.py wisdom --cover 1000` | Same dump as WISDOM. Agent then ranks ASYM (`rr`) and CONVICTION (`bo`) after an all-fields `history`/`span` on the payoff shortlist. Skill `.cursor/skills/analysis-style/SKILL.md`. Canvas `analysis-style-YYYYMMDD` only when the paste says `canvas output`. |
| BACKTRACK (weekly book) | `$ python src/run_backtracking.py prepare --start-day 01_04_2026 --end-day 01_08_2026 && python src/run_backtracking.py select --run logs/tradingview_analysis/backtracking_analysis/runs/<run_id> --selector program_bo --field bo --top 15 --group-field industry --cap 3 && python src/run_backtracking.py score --run logs/tradingview_analysis/backtracking_analysis/runs/<run_id> --picks picks_program_bo.csv` | Weekly point-in-time panel, picks file, then scored run. **Output:** `backtracking_*/` — `backtracking.duckdb` (`decision_weeks`, `panel_rows`), `run_manifest.json`, `coverage_ledger.csv`, `queries.txt`, `picks_*.csv`, `name_returns.csv`, `book_weeks.csv`, `investing_run.md`. |

US $2B tape lives on `briefing_pack.duckdb` table `us2b_tape`, not the pack JSON. The `run_operator_suites.py` wrappers resolve that.

Compile then dumps:

```bash
$ python src/run_operator_suites.py all --compile --cover 1000
```

---

## Scan-day producers (only if sources are stale)

Run these before inspect/compile when the locked tape, edge, MTP, or finproj is old.

| Flow | Prompt | Git Bash | Writes |
|---|---|---|---|
| Holdings marks (`main()`) | DAILY Book | `$ python src/main.py` | whatever is uncommented in `main()` |
| All-fields tape | DAILY / FORWARD | `$ python -c "from data_analysis_scripts.trading_view_export_all_tdfields import export_all_tradingview_fields_duckdb; export_all_tradingview_fields_duckdb()"` | `tradingview_all_fields_*.duckdb` |
| Move-prediction DuckDB | DAILY | `$ python -c "from main import daily_prediction_move_analysis_suite; daily_prediction_move_analysis_suite()"` | `move_prediction_*.duckdb` |
| Edge + upside opportunity | DAILY / FORWARD | `$ python src/run_upside_opportunity_scan.py --full-flow` | edge parent + upside_opportunity_scan (runs edge-research first) |
| Market timing policy | DAILY | `$ python src/run_market_timing_policy.py` | `market_timing_policy/runs/` |
| Growth lanes (5y rev/EBIT) | FORWARD | `$ python src/run_financial_projection.py --top-n 1000` | `fingrowth_*.duckdb` |
| Price terminals | FORWARD | `$ python src/run_financial_projection.py --mode price --top-n 1000` | `finproj_*.duckdb` |
| Dataset lock (no rescan) | WISDOM / FORWARD / ANALYSIS | `$ python src/run_operator_suites.py sources` | `datasets.duckdb` — paths for all six, plus `family_metrics` when a backtest run exists |

Do not start `python src/run_backtests.py` from a global prompt. The lock reads the newest `family_metrics.csv`.

`main.py` only runs what is **uncommented** in `main()`. The one-liners above do a full tape day without editing.

---

## Equivalent generic CLIs

Same engines as the `run_operator_suites.py` wrappers. You need a CSV for raw `tv_scan_cli.py movers` (wisdom writes `us2b_tape.csv`).

```bash
PACK=$(ls -td logs/tradingview_analysis/operator_briefing/runs/briefing_pack_*/briefing_pack.json | head -1)
$ python src/generic_utils/tv_scan_cli.py movers --recipe config/generic_utils/movers_day.json --csv us2b_tape.csv --out-dir RUN/movers
$ python src/generic_utils/tv_scan_cli.py movers --recipe config/generic_utils/movers_3m.json --csv us2b_tape.csv --out-dir RUN/movers_3m
$ python src/generic_utils/tv_scan_cli.py risk --run latest --pack "$PACK" --out-dir RUN/book_risk
$ python src/generic_utils/tv_scan_cli.py capture --lookback-days 90 --pack "$PACK" --out-dir RUN/capture
$ python src/generic_utils/tv_scan_cli.py capture-replay --capture-dir RUN/capture --out-dir RUN/capture/replay
$ python src/generic_utils/tv_scan_cli.py setup --pack "$PACK" --sleeve us2b --forward --cover 1000 --out-dir RUN/value_tech
$ python src/generic_utils/tv_scan_cli.py forward --pack "$PACK" --sleeve us2b --cover 1000 --out-dir RUN/forward
$ python src/generic_utils/tv_scan_cli.py pack-focus --pack "$PACK" --sleeve sleeves.radar_curated_50 --out radar50.csv
```
