# Edge Upside Rankings, Forward Predictions, and Tradeable Safety

This guide explains what the numbers mean in the integrated edge research flow started from `run_edge_research_local_main()` in `src/run_edge_research_tools.py`. It focuses on the three ranking layers most relevant to upside and risk:

1. **Upside prediction** — empirical move probability from current setup + history
2. **Forward upside valuation** — model-based fair-value upside from valuation lenses
3. **Tradeable safety** — setup quality blended with balance-sheet / cash-generation safety

It also explains where results land on disk and how to study overlooked sectors (for example airlines or nursing services) that may not appear on the default quantitative radar.

---

## 1) What this flow is (and is not)

The edge research module is a **volatility / liquidity state engine**. It ranks names that are statistically “in expansion” today and compares them to how similar states behaved historically.

| It measures | It does not measure |
|---|---|
| Current ADRP, relative volume, traded value, sleeve percentiles | Narrative themes, sector rotation stories, or “underfollowed” sentiment |
| Historical forward returns when the same symbol was in a similar setup | Whether a name is fundamentally cheap in isolation |
| Lane (industry/sector) base rates for comparable groups | Guaranteed future performance |

**Implication for overlooked names:** Airlines, medical staffing, or other sectors with real moves but quiet quantitative signatures often **will not appear** in the highlights shortlist unless they currently pass volatility/liquidity percentile thresholds (or enter via the expanded-candidate path). Safety scoring runs on a broader universe, so a name can score well on safety while still being invisible to upside lenses. A dedicated **blindspot lane** (section 10) now runs a full backward DuckDB scan specifically to surface this class of name — persistent, rotating, or volume-building movers that are not currently "hot" on the vol/liq radar.

---

## 2) Entry point and pipeline

### How you run it

Edit parameters in `run_edge_research_local_main()` (around line 3381), then:

```powershell
$env:PYTHONPATH='src;.'
python src/run_edge_research_tools.py
```

Default workflow: `latest_500m_full_edge_research` — foundation snapshot + full integrated aggregate.

### Integrated pipeline order

```
Foundation (snapshot + forward labels + vol/liq setup passes)
    ↓
Daily quant pass (screen → persistence → edge summary → highlights)
    ↓
Safety scan (full scored universe, industry-grouped — staged, not a top-level folder)
    ↓
Forward upside valuation lens
    ↓
Tradeable safety lens  ← uses forward valuation overlay when available
    ↓
Upside prediction lens
    ↓
Unified edge highlights (consensus across all outlooks + folded-in safety universe)
    ↓
Blindspot lane (independent DuckDB backward scan — not gated by the shortlist)
    ↓
Historical edge progression + edge trade plan
```

**Note on safety highlights:** the safety scan still runs early (so forward valuation / tradeable safety / upside prediction can all use `safety_companion_score`), but it no longer gets its own top-level `safety_highlights/` folder. Its full scored universe and industry group summary are written directly into `edge_unified_highlights/` (see section 3C and section 7).

The parent run folder is created under:

```text
logs/tradingview_analysis/edge_research_tools/runs/<run_id>/
```

After a run, the console prints paths such as `Parent run output: ...`. Open `parent_run_manifest.json` in that folder for the full child directory map.

---

## 3) The gate: who gets ranked at all?

All three lenses (upside prediction, forward valuation, tradeable safety) operate on the **same highlights shortlist**, not the full market.

### Stage A — Screen (`screen/`)

Point-in-time filter on scan day. A symbol needs `composite_score >= min_composite` (default 0.55).

`composite_score` = average of six **directional universe percentiles**:

- ADRP
- relative volume (10d)
- value traded
- volatility_core sleeve
- liquidity_core sleeve
- momentum_context sleeve

`1.0` means top of universe on all six. This is why quiet names rarely pass.

### Stage B — Highlights shortlist (`highlights/edge_name_shortlist.csv`)

Builds ranked candidates from symbols on scan day with either:

- `composite_score_day >= screen_min_composite`, **or**
- `in_any_setup = 1` (passes a volatility/liquidity setup variant such as `adrp_relvol_core`)

**Strict tier** — must pass all four durability filters:

| Filter | Typical meaning |
|---|---|
| `hist_occurrence_count_in_setup` | Enough past setup days for this symbol |
| `win_rate_{horizon}d_in_setup` | Historical positive forward-return rate in setup |
| `median_fwd_{horizon}d_in_setup` | Historical median forward return (%) in setup |
| `any_setup_rate` | How often the symbol revisits setup conditions |

**Expanded tier** — if strict names are below `highlights_min_shortlist_count` (default 2000), the run adds momentum candidates ranked by `expanded_capture_score` (big mover + confidence + lane context, penalized per failed strict filter).

Check `shortlist_source_tier` (`strict` vs `expanded`) and `strict_filter_failed_tags` on each row.

### Stage C — Safety universe (`edge_unified_highlights/edge_unified_highlights_safety_scored_universe.csv`)

Scores **all symbols in the snapshot** for the scan date (not just the highlights shortlist). This is the right place to find a fundamentally safer airline or nursing-services name that never triggered the vol/liq radar. This file lives inside `edge_unified_highlights/` (not a separate `safety_highlights/` folder) alongside `edge_unified_highlights_safety_group_summary.csv`, an industry/sector rollup of the same scores.

---

## 4) Upside prediction ranking

**Code:** `src/edge_research_tools/upside_prediction.py`  
**Outputs:** `<parent_run>/upside_prediction_lens/`

| File | Use |
|---|---|
| `edge_upside_prediction_ranked.csv` | Full shortlist re-ranked for upward move prediction |
| `edge_upside_prediction_top<N>.csv` | Focus list (default N=20) |
| `edge_upside_prediction_lens_report.md` | Run-specific methodology |

### What question it answers

> “Among names already in expansion today, which are most likely to move up over the ranking horizon, given current state and how this symbol (or its industry lane) behaved historically in similar setups?”

Default `ranking_horizon` = **5 trading days**.

### Score formula (`upside_prediction_score`, 0–1)

| Component | Weight | Source |
|---|---:|---|
| `big_mover_score` | 30% | Current expansion state (composite, ADRP, relvol, traded value, sleeves) |
| `upside_hist_signal_blend` | 25% | Blended historical win / median fwd / target rate / MFE |
| `composite_score` | 20% | Same six-percentile composite from screen |
| `lane_context_score` | 15% | Industry (or sector) lane median fwd, win rate, sample depth |
| `target_rate_{horizon}d_in_setup` | 10% | Historical +10% before −7% hit rate |
| MAE penalty | up to −8% | Penalizes severe historical drawdowns (`median_mae`) |

**Historical blend logic:**

- **Name-level signal** (when `hist_occurrence_count_in_setup` is high): win rate, median fwd, target rate, MFE
- **Lane-level signal** (industry base rates): lane win rate + lane median fwd
- Blend weight: `upside_name_hist_weight = min(1, hist_occurrences / 8)`

Thin history → score leans on **lane** (industry), not the individual ticker.

### Key columns to read

| Column | Meaning |
|---|---|
| `upside_prediction_rank` | Sort key — lower is better |
| `upside_prediction_score` | Headline 0–1 score |
| `upside_hist_win_rate` | Historical win rate in setup for ranking horizon |
| `upside_hist_median_fwd_pct` | Historical median forward return (%) in setup |
| `upside_target_before_stop_rate` | Share of past setup days where +10% was reached before −7% within horizon |
| `upside_hist_mfe_pct` / `upside_hist_mae_pct` | Typical best / worst excursion from historical setup entries |
| `lane_group_value` | Industry (default) used for lane context |
| `hist_occurrence_count_in_setup` | Sample depth — low = trust lane more than name |

Trailing safety columns (`balance_sheet_safety_score`, `safety_companion_score`, `safety_bucket`) are **context only** — they do not change upside rank.

### Target rule (forward labels)

From label generation: **+10% target, −7% stop** within the ranking horizon. `target_before_stop_rate` and MFE/MAE columns derive from these labels.

---

## 5) Forward upside valuation ranking

**Code:** `src/edge_research_tools/forward_upside_valuation.py`  
**Outputs:** `<parent_run>/forward_upside_valuation_lens/`

| File | Use |
|---|---|
| `edge_forward_upside_valuation_ranked.csv` | Full shortlist ranked by forward valuation outlook |
| `edge_forward_upside_valuation_top<N>.csv` | Focus list |
| `edge_forward_upside_valuation_lens_report.md` | Mode coverage and methodology |

### What question it answers

> “Among shortlist names, what is the model-implied upside to fair value, and how strong is that estimate?”

This is **not** the same as upside prediction. Prediction is empirical (history + current state). Forward valuation is **anchor-based** (multiples, DCF/yield, analyst, technical, range, trajectory, book value).

### Valuation lenses (max 4 active per company)

Company style (from investment-style tags) picks lens priority:

| Style | Preferred lenses |
|---|---|
| growth | trajectory, multiple, analyst, technical |
| value | multiple, book_value, analyst, range |
| income | yield_dcf, multiple, analyst, range |
| risk | multiple, range, technical, analyst |
| balanced | multiple, analyst, trajectory, range |

Peer multiples resolve **industry → sector → global** depending on group size.

### Two modes

| Mode | When | Score blend |
|---|---|---|
| `valuation` | ≥2 usable lenses and long-term target exists | 55% valuation signal + 15% lens coverage + 15% upside prediction support + 15% safety |
| `fallback` | Sparse data, missing close, or insufficient lenses | 60% upside prediction support + 25% confidence + 15% big mover |

Always check `forward_upside_mode` and `forward_upside_fallback_reason` before acting on sparse names.

### Headline return columns

| Column | Meaning |
|---|---|
| `forward_valuation_upside_pct` | Primary fair-value upside % — 55% long / 25% medium / 20% near base-case blend vs current price |
| `forward_rank_horizon_valuation_upside_pct` | Horizon-mapped upside: ≤20d → near, ≤126d → medium, else long |
| `forward_valuation_bear_upside_pct` / `forward_valuation_bull_upside_pct` | Long-term scenario band |
| `forward_upside_score` | 0–1 rank score (valuation or fallback formula) |
| `forward_selected_lenses` | Which lenses were used (pipe-separated) |
| `forward_peer_scope` | `industry`, `sector`, or `global` peer normalization |
| `forward_supporting_upside_prediction_score` | Linked empirical upside signal |

Negative `forward_valuation_upside_pct` means model fair value is **below** current price.

---

## 6) Tradeable safety ranking

**Code:** `_build_tradeability_safety_lens` in `src/run_edge_research_tools.py`  
**Outputs:** `<parent_run>/tradeable_safety_lens/`

| File | Use |
|---|---|
| `edge_tradeable_safety_overlay.csv` | Full shortlist with tradeable + safety blend |
| `edge_tradeable_safety_top<N>.csv` | Focus list |
| `edge_tradeable_safety_lens_report.md` | Methodology |

### What question it answers

> “Among upside-oriented shortlist names, which combine strong **current tradeable setup** with acceptable **balance-sheet / cash-generation** quality?”

This lens **does not exclude** names for weak safety. It ranks and labels them.

### Score construction

```
tradeable_core_score      = 55% confidence_score + 45% big_mover_score
tradeable_safety_blend_score = 65% tradeable_core + 35% safety_companion_score
```

| Input | What it captures |
|---|---|
| `confidence_score` | Stability, recurrence, historical win rate, lane quality |
| `big_mover_score` | Current vol/liq expansion state |
| `safety_companion_score` | 55% balance sheet + 45% cash generation / value (from safety highlights) |

### Safety companion (from the safety scan, folded into `edge_unified_highlights/`)

| Component | Covers |
|---|---|
| `balance_sheet_safety_score` | Liquidity, leverage, Altman Z, Zmijewski, net-cash context |
| `cash_generation_value_score` | FCF margin, earnings yield, shareholder yield |
| `indicator_pass_count` | Count of explicit boolean safety flags that passed |

### Safety buckets (non-excluding labels)

| Bucket | Rule (approximate) |
|---|---|
| `safer` | safety_companion ≥ 0.80 and ≥ 6 indicators |
| `balanced` | ≥ 0.68 and ≥ 5 indicators |
| `aggressive` | ≥ 0.55 and ≥ 3 indicators |
| `speculative` | below aggressive thresholds |
| `unscored` | no safety row for symbol |

### Extra columns on the overlay

| Column | Meaning |
|---|---|
| `tradeable_safety_rank` | Primary sort — blend of setup + safety |
| `safety_rank_within_tradeables` | Safest within shortlist (sort by safety, not setup) |
| `risk_rank_within_tradeables` | Inverse — highest-risk shortlist names |
| `historical_validation_score` | Backward check: recurrence, win rate, median fwd, stability |
| `historical_validation_bucket` | `strong`, `supported`, `mixed`, or `thin` |
| Forward valuation columns | Appended when forward lens ran first |

**Historical validation** is separate from the main blend rank. A name can rank high on tradeable safety but show `historical_validation_bucket = thin` if it lacks setup history.

---

## 7) Unified consensus (how the lenses fit together)

**Outputs:** `<parent_run>/edge_unified_highlights/`

`unified_edge_highlight_score` blends six normalized outlooks:

| Outlook | Weight |
|---|---:|
| upside_prediction | 20% |
| forward_valuation | 20% |
| edge_big_mover | 18% |
| edge_confidence | 16% |
| tradeable_safety | 14% |
| safety_companion | 12% |

Plus 28% weight on `unified_probabil_edge_fit_score` (bootstrap CI / lane uncertainty).

Use unified rank when you want one consensus row. Use individual lenses when you care **why** a name ranked (empirical move vs fair value vs safety).

### Supporting tables in this folder

`edge_unified_highlights/` is also where the full safety scan now lives (see section 3C):

| File | Contents |
|---|---|
| `edge_unified_highlights.csv` | The primary shortlist consensus table described above |
| `edge_unified_highlights_safety_scored_universe.csv` | Every scanned symbol's safety score — not just the shortlist |
| `edge_unified_highlights_safety_group_summary.csv` | Industry/sector rollup of safety scores — good for spotting durable, well-capitalized lanes even when they are off the volatility-liquidity radar |
| `edge_unified_highlights.duckdb` | Same data queryable as `symbol_unified_highlights`, `safety_scored_universe`, `safety_group_summary` tables |

---

## 8) Where to look after a run

Replace `<parent_run>` with your printed run directory.

```text
<parent_run>/
├── parent_run_manifest.json          ← start here
├── highlights/
│   ├── edge_name_shortlist.csv       ← who entered the funnel
│   ├── edge_lane_leaders.csv         ← best historical lanes (industry/sector)
│   ├── edge_name_top30.csv
│   └── edge_name_top10_confidence.csv
├── upside_prediction_lens/
│   └── edge_upside_prediction_ranked.csv
├── forward_upside_valuation_lens/
│   └── edge_forward_upside_valuation_ranked.csv
├── tradeable_safety_lens/
│   └── edge_tradeable_safety_overlay.csv
├── edge_unified_highlights/
│   ├── edge_unified_highlights.csv                        ← shortlist consensus
│   ├── edge_unified_highlights_safety_scored_universe.csv  ← FULL universe safety scores
│   └── edge_unified_highlights_safety_group_summary.csv    ← industry safety rollup
├── blindspot_lane/
│   ├── edge_blindspot_industry_rotation.csv  ← lane-level rotation, not gated by shortlist
│   ├── edge_blindspot_symbol_candidates.csv  ← full backward-scanned candidate universe
│   └── edge_blindspot_symbol_top<N>.csv      ← flagged quiet movers only
└── edge_trade_plan/
    └── edge_trade_plan_ranked.csv    ← entry timing layer (see edge_trade_plan_guide.md)
```

Note there is no top-level `safety_highlights/` folder anymore — that data now lives inside `edge_unified_highlights/` (see section 3C and section 7).

### Inspect one symbol across all lenses

```powershell
$env:PYTHONPATH='src;.'
python -m run_edge_research_tools inspect-symbol --symbol NASDAQ:AAL --run-ref "<parent_run>"
```

Swap `NASDAQ:AAL` for any exchange:ticker. This prints rank/score in unified, big mover, confidence, upside prediction, forward valuation, tradeable safety, and safety companion datasets.

---

## 9) Reading the numbers — practical cheat sheet

| If you want… | Sort / filter by… | Watch out for… |
|---|---|---|
| Best empirical move odds | `upside_prediction_rank` | Thin `hist_occurrence_count` → lane-driven, not name-specific |
| Cheapest vs model fair value | `forward_upside_rank`, `forward_valuation_upside_pct` | `forward_upside_mode = fallback` → weak valuation coverage |
| Setup + balance sheet | `tradeable_safety_rank` | High blend can still be `speculative` on safety_bucket |
| Safest names in shortlist | `safety_rank_within_tradeables` | May have weak `tradeable_core_score` |
| Industry historical edge | `edge_lane_leaders.csv` by `lane_group_value` | Lane strength ≠ every name in sector |
| Names off the radar but safer | `edge_unified_highlights_safety_scored_universe.csv` filter `industry` | Not in shortlist = no upside lens ranks |
| Persistent movers the quant gate missed | `blindspot_lane/edge_blindspot_symbol_candidates.csv` filter `blindspot_flag = 1` | Thin `blindspot_hist_occurrence_count` = pattern not yet historically validated |
| Rotating industries/lanes | `blindspot_lane/edge_blindspot_industry_rotation.csv` sort by `industry_rotation_score` | Thin-coverage industries add noise to the percentile rank |

### Score scale

Most scores are **0–1** (higher = better). Percent columns (`*_pct`, `*_fwd_pct`) are in **percent return** units (e.g. `5.2` = +5.2%).

---

## 10) Blindspot lane — a DuckDB backward scan outside the quant gate

**Code:** `src/edge_research_tools/blindspot_lane.py` (`run_edge_blindspot_lane`)  
**Outputs:** `<parent_run>/blindspot_lane/`

Every lens above (highlights, upside prediction, forward valuation, tradeable safety) is gated by *today's* volatility/liquidity state — a name only shows up once ADRP, relative volume, and value traded are already elevated. That gate structurally cannot see a rotation while it is still building, e.g. a lane like Airlines or Medical Care / Nursing Services grinding higher for weeks on rising baseline volume before any single day looks statistically "hot".

The blindspot lane removes that gate. It runs a standalone DuckDB pass over the **entire loaded snapshot history** (not just the scan day) for the full region-filtered universe — sized for larger datasets since it works directly against the DuckDB tables rather than in-memory CSV joins.

### What it computes

| Signal | Level | What it captures |
|---|---|---|
| `trend_persistence_score` | symbol | Rolling fraction of the trailing `persistence_window_days` snapshot rows where `perf_1m > 0` (window function over full symbol history) |
| `volume_base_trend_pct` / `value_traded_trend_pct` | symbol | Trailing-window average volume/value vs. the same average `lookback_trading_days` rows earlier — is the baseline actually building? |
| `industry_rotation_delta` | industry | Change in the industry's cross-sectional percentile rank of median `perf_1m`, now vs. `lookback_trading_days` rows earlier — laggard-to-leader rotation |
| `currently_quant_hot_flag` | symbol | Cross-check against today's `volatility_core` / `liquidity_core` sleeve percentiles (same ceilings the rest of the suite uses) |
| `blindspot_hist_occurrence_count` / `_median_fwd_pct` / `_win_rate` | symbol & industry | Backward-validated: every historical day where the same "persistent, not quant-hot" pattern occurred, joined to `symbol_day_forward_labels` |

`quiet_mover_score` (0–1) blends trend persistence (30%), volume/value trend (25%), medium-term performance acceleration (25%), and historical win rate (20%, only trusted once `blindspot_hist_occurrence_count >= 3`). `blindspot_flag = 1` marks rows that clear `persistence_threshold` (default 0.65) while remaining below both hot ceilings today — the actual blind spot.

### Output files

| File | Use |
|---|---|
| `edge_blindspot_industry_rotation.csv` | Every qualifying industry, sorted internally by `industry_rotation_score`; includes `top_quiet_movers` (top 5 tickers in that lane) |
| `edge_blindspot_symbol_candidates.csv` | Every symbol with sufficient trailing history — flagged and unflagged, hot and quiet |
| `edge_blindspot_symbol_top<N>.csv` | Just the flagged (`blindspot_flag = 1`) candidates, ranked by `quiet_mover_score` |
| `edge_blindspot_lane.duckdb` | Same two tables, queryable |
| `edge_blindspot_lane_report.md` | Method, scope, and known limitations for this run |

### How to use it for sector research

1. Open `edge_blindspot_industry_rotation.csv`, sort by `industry_rotation_score` or `industry_rotation_delta` — these are the lanes rotating into favor right now.
2. Cross-reference `top_quiet_movers` on a lane of interest, then look those tickers up in `edge_blindspot_symbol_candidates.csv` for the underlying trend/volume/historical detail.
3. Filter `edge_blindspot_symbol_candidates.csv` on `blindspot_flag = 1` AND `in_shortlist_flag = 0` for the purest "moving but not on the radar yet" list.
4. Treat `blindspot_hist_median_fwd_pct` / `blindspot_hist_win_rate` as informative context only when `blindspot_hist_occurrence_count` is below ~3 — the historical sample is thin.
5. This lane is a screening aid, not a valuation or safety check — always cross-reference candidates against `edge_unified_highlights` and the safety universe before acting.

---

## 11) Exploring overlooked sectors (airlines, nursing services, etc.)

These sectors often move on narrative, capacity, regulation, or labor — not always on ADRP / relative-volume expansion. The default pipeline will miss them unless they are statistically “hot” on scan day.

### Why they disappear

1. `composite_score` below screen threshold and not `in_any_setup`
2. Strict durability filters fail (`strict_filter_failed_tags`)
3. Industry lane may be strong while individual tickers are quiet (`edge_lane_leaders.csv` shows lane, not ticker)
4. Forward valuation may work (if they were on shortlist) but upside prediction needs setup history

### Workflow A — Check whether they are on radar at all

1. Open `highlights/edge_name_shortlist.csv` and filter `industry` or `sector`
2. If absent, filter `edge_unified_highlights/edge_unified_highlights_safety_scored_universe.csv` by same industry — confirms universe coverage. Also check `blindspot_lane/edge_blindspot_symbol_candidates.csv` for the same industry — it covers names regardless of shortlist membership.
3. Run `inspect-symbol` for specific tickers (e.g. `NASDAQ:AAL`, `NYSE:DAL`, `NYSE:UHS`)

### Workflow B — Study the industry lane without ticker bias

`highlights` defaults to `--group-by industry`. Read `edge_lane_leaders.csv`:

- `lane_median_fwd_5d`, `lane_win_rate_5d` — industry base rates when setup fires
- Bootstrap CI columns — uncertainty on lane median

If airlines or nursing services show strong lane stats but no tickers in shortlist, the **sector can move** while **current names are not in expansion state**.

### Workflow C — Deliberately widen the funnel

Re-run on an existing snapshot with looser gates (no need to rebuild foundation):

```powershell
$env:PYTHONPATH='src;.'
python -m run_edge_research_tools screen `
  --snapshot-db "<path/to/symbol_day_feature_snapshot.duckdb>" `
  --date <scan_day> `
  --min-composite 0.45 `
  --top-n 2000
```

Then run `highlights` with a lower `screen_min_composite` or inspect `persistence` output for symbols with high `any_setup_rate` in target industries.

### Workflow D — Safety-first sector scan

Filter `edge_unified_highlights_safety_scored_universe.csv`:

```text
industry contains "Airline" OR "Nursing" OR "Medical"
ORDER BY safety_companion_score DESC
```

Cross-reference with `screen/edge_screen_ranked.csv` (if saved) for `composite_score` and `in_any_setup` flags. Names with high safety but low composite are **quality candidates waiting for a vol/liq trigger**. Also cross-reference with `blindspot_lane/edge_blindspot_symbol_candidates.csv` — a name that is both safety-strong and flagged there has capital-structure support **and** a persistent, quietly-building move.

### Workflow E — Custom symbol list (adhoc)

For a fixed watchlist, use setup engine grouping:

```bash
PYTHONPATH='src:.' python -m run_edge_research_tools setup-vol-liq \
  --snapshot-db "<snapshot.duckdb>" \
  --group-by symbol \
  --symbols "NASDAQ:AAL,NASDAQ:UAL,NYSE:DAL"
```

This reports historical forward behavior for those symbols in isolation, outside the default shortlist machinery.

### What not to expect

- Upside prediction rank for a name **not** on the shortlist
- Forward valuation rank without shortlist membership (lens only processes highlight rows)
- Unified rank or trade plan states without running the full integrated suite
- The default shortlist lenses to surface "underfollowed" names that are quantitatively quiet on scan day — use the blindspot lane (section 10) or safety-universe cross-checks for that instead

---

## 12) Suggested review order for a new run

1. `parent_run_manifest.json` — confirm scan day, horizon, paths
2. `highlights/edge_name_shortlist.csv` — funnel membership and `shortlist_source_tier`
3. `upside_prediction_lens/edge_upside_prediction_ranked.csv` — empirical move ranking
4. `forward_upside_valuation_lens/edge_forward_upside_valuation_ranked.csv` — fair-value upside
5. `tradeable_safety_lens/edge_tradeable_safety_overlay.csv` — setup + safety blend
6. `edge_unified_highlights/edge_unified_highlights.csv` — single consensus view
7. `blindspot_lane/edge_blindspot_symbol_top<N>.csv` — persistent movers the quant gate missed
8. `edge_trade_plan/edge_trade_plan_ranked.csv` — timing / entry states (optional execution layer)

For sector exploration, parallel-read `edge_lane_leaders.csv`, `edge_unified_highlights_safety_scored_universe.csv`, and `blindspot_lane/edge_blindspot_industry_rotation.csv` filtered to your industry.

---

## 13) Related docs

- [edge_research_tools_active_management_playbook.md](./edge_research_tools_active_management_playbook.md) — full module, screen, persistence, highlights
- [edge_trade_plan_guide.md](./edge_trade_plan_guide.md) — ENTER_STARTER / ARMED / WATCH states on top of unified rows

---

## 14) Quick reference — formulas

**Upside prediction:**

```
upside_prediction_score =
  0.30 * big_mover
+ 0.25 * blended_hist_signal
+ 0.20 * composite
+ 0.15 * lane_context
+ 0.10 * target_before_stop_rate
- mae_penalty (≤ 0.08)
```

**Forward valuation (valuation mode):**

```
forward_upside_score =
  0.55 * valuation_signal
+ 0.15 * selected_lens_coverage
+ 0.15 * upside_prediction_support
+ 0.15 * safety_companion
```

**Tradeable safety:**

```
tradeable_core = 0.55 * confidence + 0.45 * big_mover
tradeable_safety_blend = 0.65 * tradeable_core + 0.35 * safety_companion
```

**Unified highlight:**

```
unified_edge_highlight_score = 0.72 * consensus_outlook + 0.28 * probabil_edge_fit
```

**Blindspot lane (quiet mover):**

```
quiet_mover_score =
  0.30 * trend_persistence_score
+ 0.25 * volume_value_trend_component
+ 0.25 * perf_trend_acceleration_component
+ 0.20 * historical_win_rate (0.5 default if occurrence_count < 3)

blindspot_flag = 1 if trend_persistence_score >= persistence_threshold
                      AND currently_quant_hot_flag == 0
```
