# Scoring and ranking — layer order

This is the spec for **how advice is built**. Read top to bottom. Later layers
may **filter, cap, or contradict** earlier layers. They must not rewrite them
into a single composite.

Daily deliverable: a **Build-50** ranking of best trades / positions to build on
(`sleeves.radar_curated_50`) plus Book actions. Capital budget stays 0–2 NEW /
ADD / TRIM-EXIT. Covering 50 names is **analysis coverage**, not 50 orders.

---

## One-page stack

```
 9  Operator High / Med / Low + capital sequence     (canvas; priors JSON)
 8  Stance + suggested_courses                        (operator_briefing.stance)
 7  Build-50 rank  ← unpaid_sort_key + industry cap     (sleeves + ranking.group_capped_top_n)
 6  Domain sleeves (unpaid ∪ continuation, shorts, …) (operator_briefing.sleeves)
 5  Leftover / range / rr / street_px / target_px    (generic_utils.derive)
 4  Join overlays: MTP, holdings, priors, dte        (compile)
 3  Edge: opp rank + forward_valuation_upside_pct      (edge_research_tools)
 2  Move prediction: weeks profiles + mix + conv     (prediction DuckDB)
 1  All-fields tape + fundamentals whitelist           (AF_CANDIDATES)
 0  Universe: US-listed, identity, close, mcap        (all-fields + pred tape)
```

**Generic** (zero cutoff): layers 0–1 formulas, 5, movers both-tails, `top_n`.
**Domain:** layers 6–8 eligibility and course.
**Operator:** layer 9. Polar continuity can override 8.

Do not invent SQL or `logs/_tmp_*.py` to skip a layer. Compile joins 0–8. Git Bash command sheet: [gitbash_agent_data_flows.md](gitbash_agent_data_flows.md).

---

## Layer 0 — Universe and identity

**Source:** newest all-fields DuckDB (`all_fields_rows`) + move-prediction
`raw_scan_rows` for the same scan day.

| Kept | Dropped / not a rank |
|---|---|
| `EXCHANGE:TICKER`, company, industry, sector, country | OTC / CSE / TSX / LSE / … listing prefixes (`sleeves.BLOCKED_LISTING_PREFIXES`) |
| close, mcap | Duplicate `TICKER23` warrant leaks on **movers** (`drop_duplicate_id_suffixes`) |

US $2B (`mcap >= 2e9`) is the **primary** unpaid/shorts universe. Satellite
`$0.5–2B` only if weeks profile is live or edge opp rank ≤ 15.

This layer does **not** decide buy/sell.

---

## Layer 1 — All-fields tape and fundamentals (whitelist)

**Source:** `operator_briefing.extract.AF_CANDIDATES`. Never `SELECT *`
(~3.5k columns).

Compile copies a compact row onto every name: returns (day, w, d5, m1, m3),
RSI, SMAs, 52w high/low, street PT, next earnings date, PE / PEG / EV/Rev /
EV/EBITDA / EV/FCF / P/B, operating margin, ADX, relative volume, then
`generic_utils.setup` stamps `val_field` / `val_vs_ind` / `peer` / `tech_sma`.

**What it is:** current price, multiples, and calendar.
**What it is not:** leftover (that is layer 5), weeks “goodness” (layer 2).

Field-by-field list: [data_used_for_ranking.md](data_used_for_ranking.md).

---

## Layer 2 — Move prediction (structure / mix / conviction)

**Source:** `logs/tradingview_analysis/prediction_analysis/duckdb_runs/.../move_prediction_*.duckdb`

This layer answers: **is the tape structurally long, continuing, fragile, or a trap?**
It does **not** answer leftover %.

### 2a. Profile scores (per symbol × horizon)

Table: `profile_horizon_scores`. Daily pack uses **weeks**.

| Pack alias | Profile | Weeks job |
|---|---|---|
| `bo` | `breakout_long_v1` | Confirmed upside continuation (tape; quality/valuation ~0 on weeks) |
| `cont` | `quality_continuation_v1` | Continuation **with** operating quality |
| `fwd` | `forward_edge_active_v2` | Quality/earnings improvement before it is fully priced |
| `early` | `early_momentum_inflection_v1` | Early turn |
| `sms` | `sustained_momentum_safety_v1` | Stay-long safety |
| `frag` | `fragility_short` | Fragile / short-lean structure |
| `exh` | `mean_reversion_exhaustion_v1` | Exhausted move |

Recipe: `pred.profile_weeks_pivot` (`generic_utils.recipes`).

Each profile score is a **weighted sum of component families**
(attention, event, momentum, trend, quality, valuation, safety). Weights
differ by profile **and** by horizon (days / weeks / months / years).
JSON source of truth: `config/move_prediction_profiles/profiles/*.json`.

`profile_live` (sleeve) = any of bo/cont/fwd ≥ 0.35.

**Compare** (`example_entry.py compare --session`) is Δbo / Δcont / Δfwd vs
the prior **calendar** pred run, not leftover and not intra-day OTC noise.

### 2b. Mix (`manager_action_signal`)

Table: `conviction_rankings.manager_action_signal` (also on consensus).

Examples: `add_long_breakout`, `hold_quality_long`, `avoid_value_trap`,
`neutral_watch`. This is a **classifier**, not operator conviction.

On a live leftover Book line, `avoid_value_trap` → HOLD_NO_ADD, not EXIT.

### 2c. Conviction score and `rank_overall`

Table: `conviction_rankings`.

Conviction is a **sleeve-primary composite** (active-management sleeves) plus
bonuses (multi-lens, weeks agreement, breakout story, optional earnings, regime
fit). `avoid_value_trap` can **zero** conviction (`exclusion_reason`). That
zero is **not** leftover death.

**Do not rank leftover names by conviction_score.** Rank leftover by layer 7.

Also on the row: `weeks_ras`, `months_ras`, `entry_readiness`.

### 2d. Regime

Table: `regime_context_scores`: `regime_fit_score`, `active_mgmt_tier`.
Climate, not a buy list.

---

## Layer 3 — Edge research (state + fair-value leftover input)

**Source:** newest `logs/tradingview_analysis/edge_research_tools/runs/edge_*/`
parent → `aggregate/upside_opportunity_scan/upside_opportunity_candidates.csv`
(and unified highlights behind that scan).

Edge is a **vol/liq state + historical-forward + valuation** engine. Quiet
fundamentals can miss the shortlist.

### Internal edge order (inside the edge run)

```
Foundation (ADR/relvol/value-traded state)
  → screen composite (6 directional percentiles, default ≥ 0.55)
  → highlights shortlist
  → safety companion
  → forward upside valuation  →  forward_valuation_upside_pct
  → tradeable safety
  → upside prediction (empirical move odds in similar states)
  → unified highlights
  → upside opportunity scanner  →  upside_opportunity_rank, directional_lean
```

### What compile joins onto the name

| Pack field | Edge column | Role |
|---|---|---|
| `edge_left` | `forward_valuation_upside_pct` | Model fair-value upside % (one input to leftover) |
| `opp` | `upside_opportunity_rank` | 1 = best in the opportunity view (lower is better) |
| `lean` | `directional_lean` | STRONG_UP / LEAN_UP / NEUTRAL / CAUTION / AVOID |

`upside_opportunity_score` (not copied as a pack 0–100) is approximately:

`0.30 momentum + 0.25 historical validation + 0.20 valuation + 0.15 safety`,
then risk-adjusted by binary-risk and downside-risk penalties.

Compile does **not** re-rank the market by that score. It uses **opp rank** as
a sort key inside unpaid (layer 7).

---

## Layer 4 — Overlays (timing, Book, calendar, priors)

Joined in `compile._merge_names` / `_book_rows`. None of these is leftover.

| Overlay | Source | Role |
|---|---|---|
| MTP `action` | `market_timing_policy_*/action_policy_recommendations.csv` | ENTER_SMALL / ENTER_PROBE / WATCH / AVOID_CHASE. **Climate**, not a buy list. Coverage is edge top-50 ∪ screen top-100. |
| Holdings scoring | `holdings_scoring_*/holdings__summary.csv` | Mark, vs-cost, Book identity. Marks can be **stale vs tape** — leftover uses all-fields close. |
| Book NAV / loss ladders | `generic_utils.risk` (pack `capital`, `wt_nav`) | Position % / $ / % of NAV. Not a 0–100. Standalone: `tv_scan_cli.py risk`. |
| Book membership | `config/holdings_scoring/current_holdings.json` | Who is held. |
| Priors | `logs/AI_ANALYSIS_UTILS/priors/YYYYMMDD_operator_actions.json` | Yesterday’s operator action. Polar: ADD/NEW ↛ EXIT unless thesis_kill. |
| `dte` | all-fields next earnings vs scan-day folder | 0–7 / 8–21 / 22–60 catalyst lanes |

---

## Layer 5 — Leftover and price levels (generic formulas)

**Module:** `generic_utils.derive`. No eligibility cutoff.

```
street leftover % = (price_target_average / close − 1) × 100
edge leftover %    = edge.forward_valuation_upside_pct
left               = max(street leftover, edge leftover)     # pack leftover
rr                 = left / max(52w range used, 15)        # unused-upside vs paid range
rng                = (close − 52w low) / (52w high − low) × 100
vs50               = close / SMA50 − 1  (as %)
street_px          = PT $, or close × (1 + street leftover %)
target_px          = close × (1 + left %)                   # can sit above street
```

**Do not** average leftover with conviction, mix, or MTP.

Street leftover **negative** and pack leftover large means **edge leftover only**
— say so; do not treat it as street PT support.

---

## Layer 6 — Domain sleeves (eligibility, not a score)

**Module:** `operator_briefing.sleeves`. This is where “still unpaid” is defined.

Radar pool = **unpaid ∪ continuation_unpaid**, first-seen order unpaid then continuation, de-duped.

| Sleeve | Meaning |
|---|---|
| unpaid | leftover ≥ 25 (≤ 90 unless Book), RSI ≤ 68, close ≥ $5, US, mcap gate, not skip-industry dump, live profile or decent opp rank |
| continuation_unpaid | live bo/cont, leftover still there, not paid chase |
| forming | unused range or mid-RSI + rising bo |
| continuation_paid | bo ≥ 1.2 and (RSI≥70 & rng≥85, or leftover gone + RSI stretched) — **watch, do not chase** |
| shorts_limited_upside | leftover not large, tape deteriorating — overlay, not leftover-91 EXIT |

Skip industries for **new** unpaid (biotech, precious metals, pharma dumps, …):
`SKIP_UNPAID_INDUSTRIES`. Book names bypass industry skip.

**Movers / groovers are a different sleeve** (`ranking.horizon_movers` +
recipes `movers_day.json` / `movers_3m.json`). They answer “did price already
move?” not “can this still pay?”

- **Up:** true top 25 by that horizon’s return, best → least, **no industry cap**.
- **Down:** bottom 25 by return, then domain class `sleeves.classify_punished_tape`
  → `down_class` BOUNCE / CONTINUE_DOWN / MIXED. Cutoffs stay in sleeves, not
  in `ranking.py`.
- Canvas: ranked 25 up; downside publishes bounce vs keep-falling (MIXED optional).

---

## Layer 7 — Build-50 ranking (best trades / positions to build on)

**This is the daily 50-name ranking.**

1. Start from radar pool (layer 6).
2. **Sort** with `unpaid_sort_key` (not leftover-first, not conviction-first):

   ```
   1. profile_live (bo/cont/fwd ≥ 0.35) first
   2. then edge opp rank ascending (missing opp sorts last)
   3. then leftover descending
   ```

3. **Industry cap** 5 per industry (`RADAR_INDUSTRY_CAP`), Book **exempt**.
   Overflow → `radar_industry_overflow` (visible, not silent drop).
4. Take **50** → `sleeves.radar_curated_50`.
   First 25 of that list → `radar_curated_25` (legacy / excerpt).
   Uncapped first 100 → `radar_upside_100` (appendix, high-rr unused-range check).

Generic primitive: `generic_utils.ranking.group_capped_top_n`.
Domain wrapper: `sleeves.dedupe_by_industry`.

**Export:**

```powershell
python src/generic_utils/tv_scan_cli.py pack-focus --pack PACK.json --sleeve sleeves.radar_curated_50 --out radar50.csv
```

DuckDB table: `radar_curated_50` on `briefing_pack.duckdb`.

Daily canvas **Radar** tab covers these 50 (filters: Headline 50 / Capital /
Pack NEW parked / Conflict / Book). Chat stays 5–8 actions. Auto pack NEW is
not the 0–2 NEW budget.

---

## Layer 8 — Stance and suggested courses

**Module:** `operator_briefing.stance`.

For each name: a **course** (ADD / NEW / HOLD / HOLD_NO_ADD / TRIM / EXIT /
WAIT / PASS / SHORT_WAIT / HOLD_THROUGH / …) plus:

- `suggested_conviction` — support for **that course**, 0.15–0.90, **not** a
  probability and **not** mix
- `evidence` / `conflicts`
- `sleeve_tags` / `sleeve_count`
- invalidation / next_check

`suggested_courses` is ranked **inside** buckets (ADD, NEW, WAIT, PASS,
SHORT_WAIT), not as one blended list. Seed the Plan tab from that.

Earnings plays (`classify_event_play`) sit on the same name: HOLD_THROUGH,
BUILD_TO_SELL, BUY_PRE, SHORT_PRE, … Do not double-size a Book ADD as a
second rumour ticket.

---

## Layer 9 — Operator (canvas)

The agent:

1. Locks sources (`inspect` / `compile`).
2. Reads regime (US $2B breadth) **before** naming stocks.
3. Applies polar + priors.
4. Covers **all 50** Build-50 names on the Radar tab (reason or cluster).
5. Quotes pack support, then assigns **High / Med / Low**.
6. Publishes 0–2 capital actions. Tape groovers do not steal that budget.
7. Writes `logs/AI_ANALYSIS_UTILS/priors/YYYYMMDD_operator_actions.json`.

Paid continuation (RSI ≥ 70, leftover gone, rng ≥ 85) = CHASE, do not add.

---

## What each layer is allowed to decide

| Question | Layer |
|---|---|
| Is this US / big enough / not a listing leak? | 0, 6 |
| What did price and multiples do? | 1 |
| Is structure breakout / continuation / trap? | 2 |
| Is vol-state + model FV still unpaid? | 3 + 5 |
| Bounce vs chase climate? | 4 MTP |
| Can this still pay (leftover vs paid range)? | 5 + 6 |
| Order the 50 names to build on | 7 |
| Named course + support | 8 |
| Size, timing, High/Med/Low | 9 |

## Forbidden shortcuts

- Leftover-first dump of `radar_upside_100` as the daily ranking
- Ranking leftover names by `conviction_score` / mix
- Averaging bo + leftover + MTP into a “play score”
- Bare `compare` (intra-day OTC) as session progression
- Treating MTP ENTER_SMALL as a buy list
- Treating pack auto NEW as the 0–2 NEW budget
- `SELECT left` in DuckDB (quote `"left"`)
