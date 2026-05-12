# Move Prediction History Aggregation — Output Guide

## Purpose

The **move-prediction history aggregator** turns a folder tree of dated TradingView
move-prediction CSV exports into cross-run timelines you can use to **backtest
the move-prediction model** itself: how scores and ranks shift day-to-day per
profile, and how prices actually moved over the same window.

Source code: [src/data_analysis_scripts/trading_view_move_prediction_history_aggregator.py](../src/data_analysis_scripts/trading_view_move_prediction_history_aggregator.py)

Each run scans the inputs for files matching
`tradingview_move_prediction__*__profile_<profile>.csv` (excluding `__raw_data.csv`),
groups them by **profile**, orders them chronologically using the `dd_mm_yyyy`
date folder and the `marketOpen` / `marketClose` session folder, then writes
the output set described below.

---

## Input layout expected

```
<base_dir>/
  16_04_2026/
    marketOpen/
      tradingview_move_prediction__...__profile_breakout_long.csv
      tradingview_move_prediction__...__profile_value_recovery.csv
      ...
    marketClose/
      ...
  17_04_2026/
    marketOpen/
      ...
    marketClose/
      ...
  ...
```

A "snapshot" = one CSV file = one `(date, session, profile)` tuple. The
**snapshot label** used everywhere downstream is `YYYY-MM-DD <session>`,
for example `2026-04-22 marketClose`.

---

## Output files at a glance

| File | Grain (one row per…) | Best for |
|---|---|---|
| `_aggregation_manifest.csv` | snapshot file | Verifying which inputs were ingested |
| `profile_<name>__history.csv` | profile + symbol + snapshot | Per-profile per-snapshot detail with `*_delta_vs_previous` |
| `profile_<name>__summary.csv` | profile + symbol | Per-profile end-of-window roll-up (first/last/best/worst) |
| `profile_<name>__score_progression__<horizon>.csv` | profile + symbol | **Wide score matrix for that horizon** — one file per horizon (`days`, `weeks`, `months`, `years`) |
| `profile_<name>__price_progression.csv` | profile + symbol | **Wide price matrix** — read across columns to see price progression |
| `_all_profiles_history.csv` | profile + symbol + snapshot | Cross-profile detail in one file |
| `_all_profiles_summary.csv` | profile + symbol | Cross-profile roll-up |
| `_all_profiles_score_progression__<horizon>.csv` | profile + symbol | Cross-profile wide score matrix for that horizon — one file per horizon |
| `_all_profiles_price_progression.csv` | symbol (deduped across profiles) | Cross-profile wide price matrix — price is profile-independent so symbols are unique |
| `_aggregation_overview.log` | run-level | Human-readable summary of the run |

> **Reading rule of thumb.** Use the **progression** CSVs when you want to *see
> the trajectory over the period* (one row, scan left-to-right). Use the
> **history** CSVs when you want *per-snapshot diagnostics with deltas*
> (one row per snapshot). Use the **summary** CSVs when you want a *single
> bottom-line per symbol*.

> **Why `*__history.csv` gets very large.** History files are row-dense by design:
> one row per `(profile, symbol, snapshot)`. Progression files are row-sparse:
> one row per `(profile, symbol)` with snapshot values spread across columns.
> Over many days/sessions, history grows much faster and can exceed 200MB.

---

## 1. Progression CSVs (the "trajectory" view)

Each progression CSV is a **wide matrix**:

- **Identifier columns** on the left (profile, symbol, sector, etc.).
- **Roll-up metrics** in the middle (first / last / delta / averages / drawdown).
- **One column per snapshot** on the right, named `snap__YYYY-MM-DD__<session>`.

Empty cells in the snapshot columns mean the symbol was **not present** in
that snapshot (filtered out by the scan, missing data, or simply not exported).
The roll-up metrics (`first_*`, `last_*`, `delta_total`, `avg_*`) are computed
from **non-empty** observations only — so a symbol seen 8 of 22 snapshots
has `snapshots_seen=8`, `presence_ratio=8/22`, and its `first_score` /
`last_score` come from the chronologically first/last snapshot it actually
appeared in.

### 1a. `profile_<name>__score_progression__<horizon>.csv` and `_all_profiles_score_progression__<horizon>.csv`

**One file per horizon** (`days`, `weeks`, `months`, `years`). Inside each
file, one row per **(profile, symbol)** — so you no longer need to filter on
a horizon column; the file *is* the horizon.

| Column | Meaning |
|---|---|
| `profile_name` | Move-prediction profile that produced the scores |
| `symbol`, `company_name`, `sector`, `industry` | Identifiers (last seen value wins) |
| `snapshots_seen` | How many snapshots produced a non-null score for this row |
| `profile_snapshot_count` | Total snapshots available **for this profile** in the window |
| `presence_ratio` | `snapshots_seen / profile_snapshot_count` (1.0 = appeared in every snapshot) |
| `first_score`, `last_score` | Earliest / latest non-null score |
| `score_delta_total` | `last_score − first_score` (null if only 1 observation) |
| `avg_score` | Simple mean across non-null observations |
| `first_rank`, `last_rank` | Within-snapshot rank by score (1 = best) at the first / last appearance |
| `best_rank`, `worst_rank` | Min / max rank reached during the window |
| `rank_improvement_total` | `first_rank − last_rank` (positive = moved up the leaderboard) |
| `snap__<date>__<session>` | Score for this `(profile, symbol, horizon)` at that snapshot, or empty |

**How to read it.**
- Pick a profile + horizon file (e.g., `profile_breakout_long__score_progression__days.csv`).
- Sort by `last_rank` ascending: top of the file = the model's current best picks.
- Scan the `snap__*` columns left-to-right to see whether each symbol's score
  is *climbing* (strengthening conviction), *flat* (stable), or *decaying*
  (model is losing confidence even if rank is still high).
- Cross-check `rank_improvement_total > 0` for symbols rising up the
  leaderboard versus symbols that started high and have been falling.

**Backtest pattern.** Pair this with `*__price_progression.csv` for the same
profile and snapshot range to ask: did symbols that *gained* score over the
period also *gain* price? You can inner-join on `symbol` (and on
`profile_name` if comparing per-profile files) and correlate
`score_delta_total` against `close_return_pct_total`.

### 1b. `profile_<name>__price_progression.csv` and `_all_profiles_price_progression.csv`

One row per **symbol** (per-profile file also carries `profile_name`; the
aggregate file does not — price is profile-independent so symbols are
deduplicated across profiles).

| Column | Meaning |
|---|---|
| `profile_name` *(per-profile only)* | Profile that surfaced the symbol |
| `symbol`, `company_name`, `sector`, `industry` | Identifiers |
| `snapshots_seen` | Snapshots where a `close` price was reported for this symbol |
| `profile_snapshot_count` | Total snapshots in the window (per-profile or aggregate) |
| `presence_ratio` | Coverage ratio |
| `first_close`, `last_close` | Earliest / latest non-null close from the snapshots |
| `close_return_pct_total` | `(last_close − first_close) / abs(first_close) * 100` |
| `max_close`, `min_close` | Peak / trough close during the window |
| `max_drawdown_pct` | Worst peak-to-trough drawdown observed across the snapshot series, in % (≤ 0; 0 means no drawdown) |
| `snap__<date>__<session>` | Close price reported at that snapshot, or empty |

Rows are sorted by `close_return_pct_total` descending so the strongest
performers across the window appear first.

**How to read it.** Treat it as a price tape sampled at TradingView snapshot
boundaries (open / close of each tracked day). It is *not* an intraday tape;
it reflects only the data we actually exported. `max_drawdown_pct` is a
quick "did this ever break down hard?" check, useful as a stop-loss-like
sanity filter when validating profile picks.

---

## 2. `*__history.csv` — per-snapshot detail with deltas

One row per **(profile, symbol, snapshot)**. Use this when the progression
matrix is too coarse and you need *what changed* between adjacent snapshots
for a single symbol.

Group of columns:

- **Identity & snapshot context.** `profile_name`, `symbol`, `company_name`,
  `name`, `exchange`, `country`, `sector`, `industry`, `market`,
  `profile_snapshot_index` (1-based snapshot number for this profile),
  `snapshot_index_for_symbol` (1-based snapshot number where this symbol was
  seen), `snapshots_seen_for_symbol`, `trading_sessions_seen_for_symbol`,
  `profile_trading_session_count`,
  `trading_session_presence_ratio_for_symbol`, `snapshot_date`,
  `snapshot_session`, `snapshot_label`, `source_file`.

  - `snapshots_seen_for_symbol` counts rows for the symbol timeline (file-level
    observations; can include repeated exports for the same session).
  - `trading_sessions_seen_for_symbol` counts **unique** snapshot labels
    (`YYYY-MM-DD <session>`) where the symbol appears.
  - `profile_trading_session_count` is the total unique sessions available for
    the profile.
  - `trading_session_presence_ratio_for_symbol` is
    `trading_sessions_seen_for_symbol / profile_trading_session_count`.

- **Price-snapshot block.**
  - `market_cap_basic`, `close` — exported as-is for this snapshot.
  - `close_return_pct_vs_previous_snapshot` — `(close − previous close) /
    abs(previous close) * 100`, where "previous" means *this symbol's previous
    snapshot in this profile* (not the previous calendar day). Null on the
    first snapshot a symbol is seen.
  - `Perf.5D`, `Perf.W`, `Perf.1M`, `Perf.YTD`, `Perf.Y`, `Perf.5Y` — TradingView's
    own trailing return windows, exactly as exported.
  - `Perf.<W>_delta_vs_previous` — change in TradingView's trailing return
    between snapshots. **Note:** this is a delta of trailing-window returns,
    *not* an incremental return; it tells you whether the trailing window is
    accelerating or decelerating.

- **Per-horizon scoring block.** Repeated for each horizon `days`, `weeks`,
  `months`, `years`:
  - `<horizon>_score` — raw score the profile assigned this snapshot.
  - `<horizon>_score_delta_vs_previous` — score change vs this symbol's
    previous snapshot in this profile.
  - `<horizon>_direction`, `<horizon>_setup` — qualitative tags from the model.
  - `<horizon>_confidence`, `<horizon>_coverage` — model self-assessment.
  - `<horizon>_rank` — within-snapshot rank by score (1 = best score in this
    snapshot for this profile + horizon).
  - `<horizon>_rank_improvement_vs_previous` — `previous_rank − current_rank`
    (positive = moved up).
  - `<horizon>_rank_percentile` — `(total − rank + 1) / total`; 1.0 is top of
    the snapshot, ~0 is the bottom.

Rows are sorted chronologically by snapshot, then by `days_rank` within each
snapshot, then by symbol.

---

## 3. `*__summary.csv` — one row per (profile, symbol)

A bottom-line per symbol within a profile. Use this for "how did this name
behave over the whole window" questions without scanning the snapshot grid.

Group of columns:

- **Identity.** Same identifier columns as `history.csv`.

- **Coverage.**
  - `snapshots_seen` — how many snapshots actually contained this symbol.
  - `profile_snapshot_count` — total snapshots available for this profile.
  - `presence_ratio` — `snapshots_seen / profile_snapshot_count`.
  - `trading_sessions_seen` — unique sessions where the symbol appears.
  - `profile_trading_session_count` — total unique sessions for the profile.
  - `trading_session_presence_ratio` —
    `trading_sessions_seen / profile_trading_session_count`.
  - `first_snapshot_date` / `_session` / `_label` and the matching `last_*`
    fields, plus `total_snapshot_span_days` between them.

- **Price block.** `first_close`, `last_close`, `close_return_pct_total`, plus
  `Perf.<W>_first` / `_last` / `_delta_total` for each TradingView window.
  `Perf.*_delta_total` is again a delta of trailing-window returns, not a
  realized return — pair it with `close_return_pct_total` for actual P&L.

- **Per-horizon roll-up.** For each of `days` / `weeks` / `months` / `years`:
  - `<horizon>_first_score`, `<horizon>_last_score`, `<horizon>_score_delta_total`
  - `<horizon>_best_rank`, `<horizon>_worst_rank`, `<horizon>_last_rank`
  - `<horizon>_rank_improvement_total` — `first_rank − last_rank`
  - `<horizon>_avg_score`, `<horizon>_avg_rank`
  - `<horizon>_last_direction`, `<horizon>_last_setup`,
    `<horizon>_last_confidence`, `<horizon>_last_coverage`

Rows are sorted by last-rank across all horizons (best `days` last_rank first,
then `weeks`, etc.) so the model's current top picks float to the top.

---

## 4. `_aggregation_manifest.csv`

One row per processed snapshot file. Columns:
`profile_name`, `profile_snapshot_index`, `snapshot_date`, `snapshot_session`,
`snapshot_label`, `source_file`, `rows_read`, `unique_symbols`.

Use it to confirm exactly which CSVs were ingested and how many symbols each
one contributed. Mismatches between expected and observed `rows_read` flag
broken or partial scans before you trust the downstream files.

---

## 5. Suggested backtest workflow over a 22-day window

1. Drop your 22 dated folders under one base directory and call
   `build_move_prediction_history_inputs_from_folder_names(base_dir, [...])`
   to validate that every folder has both `marketOpen` and `marketClose`.
2. Run `run_move_prediction_history_aggregation(input_paths, output_dir)`.
3. Sanity-check the run with `_aggregation_manifest.csv` and
   `_aggregation_overview.log`.
4. For each profile of interest, open
   `profile_<name>__score_progression__<horizon>.csv` for the horizon you care
   about (one file per horizon, no filtering needed), and skim the `snap__*`
   columns to identify symbols whose score is *trending up* across the window.
5. Open `profile_<name>__price_progression.csv` for the same profile and join
   on `symbol` to compare `score_delta_total` (from step 4) against
   `close_return_pct_total` and `max_drawdown_pct`. This is the core
   "did rising scores predict rising prices?" backtest.
6. For top picks, drill into `profile_<name>__history.csv` filtered to that
   symbol to see snapshot-by-snapshot direction / setup / confidence changes.
7. Use `_all_profiles_score_progression__<horizon>.csv` to compare the *same
   symbol* across profiles for a single horizon — high agreement across
   profiles + rising scores is the strongest cross-profile conviction signal.

---

## Caveats and gotchas

- **Snapshot-aligned, not calendar-aligned.** All deltas use the symbol's
  *previous snapshot* within the same profile, not the previous trading day.
  Missing snapshots compress the timeline.
- **`Perf.*` are trailing windows, not incremental returns.** A delta of
  `Perf.W` between two snapshots does not equal the realized return between
  those snapshots; it equals the change in the trailing-1-week return. Use
  `close_return_pct_*` columns for realized return.
- **Ranks are per-snapshot, per-profile, per-horizon.** They depend on which
  symbols were in the scan that day. Comparing ranks across profiles or across
  scans with different universes is misleading; use scores or
  `*_rank_percentile` for that.
- **Empty snapshot cells.** Empty `snap__*` cells mean "not present in that
  snapshot", not "score = 0". Treat them as missing data when computing your
  own statistics.
- **Aggregate price progression deduplicates by symbol.** Because `close` is
  profile-independent, the aggregate price file collapses cross-profile
  duplicates. The per-profile price progression files keep the
  `profile_name` column if you need that grouping.
