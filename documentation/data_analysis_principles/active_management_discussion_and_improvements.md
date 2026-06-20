# Active Management — Discussion, Critique, and Improvement Directions

> **Status:** Living discussion document (June 2026, revised after holdings scoring + regime/backscan review)  
> **Context:** ~$110k portfolio · ~$7k realized (2025) · ~$21k unrealized (2026 YTD) · value-tilted philosophy adapted for active management  
> **Related tooling:** [move-prediction profiles](../tradingview_move_prediction_profile_weighting_reference.md) · [Module2](../prediction_module2_run_output_guide.md) · [pre-earnings](../pre_earnings_profile_analysis.md) · [screening fundamentals](active_screening_analysis_fundamentals.md)

---

## 1. Where you are (honest snapshot)

You have built a **serious retail PM stack** — not a toy screener:

| Layer | What it does | Typical cadence |
|-------|----------------|-----------------|
| **Move-prediction v1** (`active_manager_v3`, 11 lenses) | Daily directional scores, consensus, `manager_action_signal`, risk tiers | Daily |
| **Module2** | Orthogonal outlook pillars — less profile overlap than v1 | Weekly / on demand |
| **Price-driven decile** | Score profiles inside worst/best price buckets — contrarian + continuation | On demand |
| **All-fields DuckDB export** | Full ~3,500-field catalog for research | Daily |
| **Scan-period predictor tracking** | ~3-month field ↔ forward-return correlation, stability | Batch (Mar–Jun pilot) |
| **SEC fundamentals** (EDGAR) | Industry share, quality, capital discipline — value sanity layer | Slower / structural |

Returns are strong. The frustration is not “the system doesn’t work” — it is **opportunity cost and process coherence**:

- Too many lenses produce **different top names** → hard to concentrate.
- Value/safety gates **correctly filter** many momentum winners (pre-profit, extended multiples, thin fundamentals).
- Mega-winners (SpaceX-adjacent, memory cycle names like Micron, AI infra) often **fail value-first screens** or appear only after the easy move.
- Tactical positions **stay open too long** when the thesis was days/weeks but conviction was sized like months.

This document is a critique and action list — not a new model spec.

---

## 2. Core tension (name it explicitly)

You are running **two incompatible games** with one portfolio:

| Game | What wins | Your tools favor | Your instinct |
|------|-----------|------------------|---------------|
| **A — Structural compounder** | Quality + mispricing + time | QVC, DVC, asymmetric value, EDGAR composites | Value as anchor ✓ |
| **B — Regime / theme momentum** | Narrative + flows + scarcity | breakout_long, sector rotation, forward_edge | Under-weighted in *sizing*, over-weighted in *attention* |

**Value as safety check** is correct for Game A. It is **actively harmful as an entry filter** for Game B unless narrowed to trap-avoidance (`avoid_value_trap`, Altman, liquidity, not “must be cheap”).

**Action:** Split the book mentally (and in logs) into **Core** vs **Tactical/Theme** before any scan. Never let a tactical idea die because P/E is high; never let a core idea in because RSI is hot.

---

## 3. Critical improvements (prioritized)

### 3.1 — Reduce screens to a **three-step funnel** (stop running everything as one decision)

**Problem:** 11 v1 profiles + Module2 + field-correlation + decile analysis = **analysis paralysis** and inconsistent concentration.

**Fix — one funnel, fixed order:**

```text
Step 1 — REGIME / THEME (weekly, 30 min)
  "What is the market paying for this month?"
  → sector/industry Perf.1M/3M leaders, breadth, your fragility_short count
  → output: 2–4 active themes max (e.g. memory, power, defense, software)

Step 2 — SLEEVE SHORTLIST (daily scan, 45 min)
  → Core: quality_continuation, DVC, asymmetric_value (months/years)
  → Tactical: breakout_long, early_momentum_inflection, forward_edge_v2 (days/weeks)
  → Overlay: mean_reversion_exhaustion + fragility_short on EXISTING holdings
  → output: ≤15 names total across sleeves (not 30 per profile × 11)

Step 3 — CONFIRMATION (only for Step 2 finalists)
  → cross-scanner aggregator OR Module2 consensus OR EDGAR quality flag
  → scan-period top fields for current regime (see §3.6)
  → output: 0–2 **new** positions/week, 0–2 **exits**/week
```

**Concrete rule:** If a name is not top-30 in **two profiles from different style lanes** (see v3 table: continuation vs quality vs value), it does not get full size. Watchlist only.

References: v3 style lanes in [profile weighting reference](../tradingview_move_prediction_profile_weighting_reference.md); Module2 family-orthogonal consensus in [Module2 guide](../prediction_module2_run_output_guide.md).

---

### 3.2 — **Exit discipline** (your explicit gap — treat as a system, not willpower)

Screens are good at **entries**. Your edge leak is likely **holding losers and stale winners**.

| Position type | Max hold without thesis progress | Loss rule (suggest starting point) | Exit signal from your stack |
|---------------|-----------------------------------|-------------------------------------|-----------------------------|
| **Tactical** (breakout, inflection, pre-earnings) | 10–15 trading days | −6% to −8% hard stop | `trim_extended_long`, weeks score flips negative, `mean_reversion_exhaustion` on symbol |
| **Catalyst** (forward_edge, value_recovery entry) | 4–8 weeks post-catalyst | −10% if catalyst failed | earnings drift profile drops out of top 50%; event component collapse |
| **Core** (QVC, DVC, quality_continuation) | quarters | −15–20% **only** if quality gate breaks | `avoid_value_trap`, fragility_short top quartile + 2 long-profile disagreements |

**Process additions:**

1. **At entry:** write one sentence: *which profile, which horizon, what would prove me wrong by [date]*. Store in a simple `positions.csv` (symbol, sleeve, entry_date, thesis_profile, exit_date, stop_pct).
2. **Daily (5 min):** re-run overlay profiles on **holdings only** — not the full universe. Flag anything with `trim_extended_long` or `hedge_or_short`.
3. **Weekly:** forced review — any tactical position older than 15 days without +5% progress → reduce 50% or exit. **Taking a small loss is cheaper than losing a concentration slot for 6 weeks.**
4. **No averaging down** on tactical sleeve. Core sleeve: one add only if value_recovery / forward_edge **re-confirms** on a second scan.

> Critique: Your tools already emit `trim_extended_long` and `hedge_or_short`. If you do not run them against the **portfolio watchlist**, they are research toys, not risk management.

---

### 3.3 — **Concentration contract** for ~$110k

High returns at this size require **fewer, larger bets** — but your multi-lens workflow spreads attention across 50+ symbols.

Suggested **hard limits:**

| Bucket | # positions | Size each | Total |
|--------|-------------|-----------|-------|
| **Conviction core** | 3–4 | 8–12% | ~35–45% |
| **Satellite** | 5–7 | 3–6% | ~25–35% |
| **Tactical / theme** | 2–4 | 2–5% | ~10–15% |
| **Cash / dry powder** | — | — | 15–25% |

**Rules:**

- Max **12–15** line items. Above that, you are indexing your own screens.
- No new name >5% until it appears on **two consecutive weekly** scans in the same sleeve.
- One **primary profile tag** per position — the lens that justified entry. Secondary lenses are confirmation only.

This aligns with the sleeve model in [active_manager_profile_and_pattern_discovery_plan.md](../improvements_plans/active_manager_profile_and_pattern_discovery_plan.md) but tightens it for your actual capital base.

---

### 3.4 — Capture **hot themes** without abandoning value (SpaceX, memory, “just buy the winner”)

The idiomatic critique is fair: **concentrated momentum in the right theme** often beats sophisticated multi-factor screens in risk-on regimes. Your stack can adapt — but not by forcing those names through `asymmetric_value`.

**What you are missing: a Theme layer** (Step 1 above). Implement conceptually first; automate second.

| Theme capture idea | How to screen with existing data | Value role |
|--------------------|----------------------------------|------------|
| **Sector/industry momentum** | Industry `Perf.1M` / `Perf.3M` rank; `sector_rotation_momentum` / Module2 `breakout_continuation` | Exclude only traps: liquidity, Altman, extreme dilution |
| **Memory / semis cycle** | Semiconductors industry + high `Perf.3M` + rising `volume_trend` + positive `eps_surprise_percent_fq` / forward growth | Require **balance-sheet safety**, not low P/E |
| **IPO / new listing / spinoff** | Separate watchlist: `type=stock`, recent listing, `relative_volume_10d` spike — **not** in main value scan | Max 1–2% probe size; no DCF |
| **“Obvious winner” FOMO** (SpaceX, etc.) | **Tracking sleeve** — paper or 1–2% max until your breakout + forward_edge profiles confirm persistence 2+ weeks | Accept you will be late; size for late entry |

**New direction — “Theme overlay” profile (future):**

- Hard gate: industry in top-decile `Perf.3M` vs market.
- Score: `continuation_strength` + `chaikin_money_flow_signal` + `eps_forward_growth` (Module2 fields already exist).
- **Explicitly zero valuation pillar weight** for this sleeve — valuation is a **trim** rule (trim if `range_position_52w` > 0.92), not entry.

**Honest critique:** You will not systematically catch SpaceX pre-IPO with a $1B+ market-cap scan. Accept **two parallel universes**: (1) listed liquid global scan, (2) manual theme watchlist fed by news/IPO calendar, scored by tactical profiles only.

---

### 3.5 — Make **value a veto layer**, not the default entry bar

Adapted value philosophy for active management:

| Sleeve | Value function |
|--------|----------------|
| **Core** | Entry requirement — margin of safety, peer-relative cheapness, quality floor |
| **Tactical / theme** | **Trap filter only** — `avoid_value_trap`, debt/liquidity, fund/trust exclusion (`type` filter) |
| **All sleeves** | **Trim trigger** — if extended (`mean_reversion_exhaustion`) AND overvalued vs targets (cross-scanner divergence #1) |

This lets Micron-like names through on **tape + forward estimates** while still blocking shell/debt disasters.

---

### 3.6 — Use **scan-period predictor tracking** to shrink the model (not expand it)

You are building ~3 months of all-fields history to correlate fields with forward returns. That is the right antidote to profile sprawl — **if you use the output to delete signals, not add them**.

**Proposed discipline after each batch run** (`run_scan_period_close_forward_predictor_tracking`, Mar–Jun window):

1. For each sleeve, keep only fields with **stable positive IC** across sub-periods (`min_runs_for_stability=3`).
2. Promote **5–10 fields per sleeve** into a “regime card” markdown — retire the rest from manual attention.
3. Compare: do your 11 profiles’ top-weighted signals appear in the stable field list? If not, profile weights may be **story-driven**, not **period-driven**.

Outputs to watch:

- `progression/period_field_quintile_progression.csv` — which fields actually separated winners.
- `progression/period_symbol_progression.parquet` — did your held names score well on those fields at entry?

**Critique:** Running correlation on 3,500 fields without narrowing to 20–50 **predictor_fields** (as your `main.py` comment suggests) will produce noise and more strategies. Start with Perf.* + volume + forward growth + sector-relative momentum.

---

### 3.7 — **Calibration loop** (close the gap between good tools and good P&L)

| Weekly question | Data source |
|-----------------|-------------|
| What did I **buy/sell** vs what did screens say? | Trade log vs `manager_action_signal` exports |
| Which profile had best **forward 2w return** on names I actually traded? | DuckDB history aggregation |
| Am I screen-hopping? (new lens every week) | Count of distinct `profile_suite_path` runs vs rule changes |
| Are tactical holds aging? | `positions.csv` age vs sleeve rules §3.2 |

**Target metric:** Hit rate on **acted** trades, not on top-10 backtests. A screen with 60% paper alpha but 30% implementation rate is a **process** problem.

---

### 3.8 — **Regime-aware sleeve weights** (quarterly, not daily)

Stop re-tuning profiles weekly. Shift **capital budgets** instead:

| Regime signal (simple) | Tactical % | Core % | Cash % |
|------------------------|------------|--------|--------|
| Risk-on (breadth ↑, fragility count low) | 20–25% | 55–65% | 10–15% |
| Mixed | 15% | 60% | 25% |
| Risk-off (fragility_short cluster, exhaustion trims widespread) | 5–10% | 50–55% | 30–40% |

Use `manager_action_counts__weeks.csv` (pattern discovery) as a regime read — many `trim_extended_long` signals = late-cycle caution.

---

## 4. New directions worth exploring

### 4.1 — **Portfolio-mode scoring** (high leverage, modest code)

Today: scans rank the **universe**.  
Need: daily pass that ranks **your 12 holdings** through all lenses + overlays.

Output: one table — symbol, sleeve, P&L, days_held, worst overlay signal, recommended action (`hold` / `trim` / `exit` / `add`).

This directly addresses exit discipline and stops the “always hunting new names” bias.

### 4.2 — **Theme-first industry packs**

Run move-prediction on **one industry at a time** when a theme is hot (semis, aerospace, utilities/power) instead of only global $1B scan. Peer-relative MAD within industry is cleaner for “find the Micron in memory” than global rank #847.

You already have industry-scoped examples in `main.py` (commented IT services run).

### 4.3 — **Earnings as a filter, not a discovery engine**

`defer_conviction_to_earnings` and pre-earnings drift are strong — but noisy if run on the full universe daily. Restrict to:

- Names already on watchlist from Core or Tactical step 2.
- Reduces false catalyst churn.

See [pre_earnings_profile_analysis.md](../pre_earnings_profile_analysis.md).

### 4.4 — **Price-driven decile for “overlooked in selloff”**

Your price-driven analysis scores profiles **inside** worst Perf buckets — this is the right tool for “value safety + technical reversal” (Game A + tactical entry). Run it **weekly** on bottom decile `Perf.1M` only, not daily on everything.

### 4.5 — **EDGAR composite as Core-only gate**

[active_screening_analysis_fundamentals.md](active_screening_analysis_fundamentals.md) describes CompositeAlpha (share momentum + quality + capital discipline + valuation). Use it to **veto Core entries** that lack fundamental support — not to block theme trades.

### 4.6 — **“Missed winner” post-mortem**

When MU / NVDA / etc. rip without you: log **which gate blocked** (valuation? safety? not in universe? no profile overlap?). After 5 post-mortems, you will see whether to widen tactical gates or accept intentional misses.

---

## 5. Behavioral traps (as important as model traps)

1. **Strategy tourism** — new profile suite / field batch every time returns feel flat. Freeze `active_manager_v3` + one Module2 suite for **one quarter**.
2. **Confirmation debt** — requiring value + momentum + earnings + consensus for entry → always late. Different sleeves, different confirmation bars.
3. **Loss aversion on tactical** — small losses feel like failure; they are **rent** for concentration capacity.
4. **FOMO sizing** — hot names get full size on first green day. Use probe → confirm → add (second weekly scan).
5. **Unrealized gains worship** — +$21k YTD can justify holding extended positions too long. Run `trim_extended_long` on winners, not just losers.

---

## 6. Suggested 30-day experiment

| Week | Focus | Success criterion |
|------|--------|-------------------|
| **1** | `positions.csv` + exit rules live; overlay scan on holdings only | Every holding has thesis + stop + review date |
| **2** | Funnel only — pick 2 themes; max 15-name master list | No trades from profiles outside funnel |
| **3** | One scan-period batch on 30 fields you hand-pick per theme | Regime card written |
| **4** | Review: acted trades vs screens; one forced tactical exit if stale | Document one “missed winner” gate |

---

## 7. Open questions (for continued discussion)

1. **Actual turnover** — how many round-trips per month today vs what tactical sleeve implies?
2. **Sector concentration** — are unrealized gains clustered in one theme (e.g. semis)? If yes, is that luck or repeatable process?
3. **Minimum edge to act** — what consensus score / action signal threshold justified your best 2025–2026 trades in hindsight?
4. **SpaceX / private exposure** — will you hold pre-IPO elsewhere? If not, define “late listed entry” rules now.
5. **Module2 vs v1** — should Module2 **replace** tactical v1 profiles for you, or only break ties?
6. **Off-book exposure after VUAA** — beyond the listed-equity sleeve plus `VUAA`, is there any other meaningful capital allocation (cash, funds, private holdings) that should be reflected in portfolio-mode review?

---

## 8. Current posture scan (June 17-18, 2026)

The latest holdings-scoring snapshot is useful because it measures the **listed-equity sleeve you actually hold**, not just the opportunity set. It does **not** natively include your `VUAA` ETF position yet because the holdings scorer does not support ETFs, and you are also using that sleeve as a **safety anchor** rather than as an actively scored idea.

For consistency, the portfolio roll-up below uses the same FX basis as the holdings scorer (`EURUSD = 1.1591`, 2026-06-17 ECB/frankfurter.app rate already used elsewhere in the run):

- **Tracked listed-equity sleeve:** `$67.4k` invested -> `$77.8k` current value -> `+$10.4k` / `+15.4%`
- **Excluded ETF safety anchor:** `VUAA`, avg price `EUR 91.7592`, invested `EUR 19,250` -> current value `EUR 26,157`
- **VUAA in USD (same FX basis):** invested `~$22.3k`, current value `~$30.3k`, unrealized `~+$8.0k`
- **Combined invested portfolio now reflected here:** `~$89.7k` invested -> `~$108.1k` current value -> `~+$18.4k` / `+20.5%`

That total sits much closer to the real portfolio context you described. It also changes the interpretation in an important way: the actively scored book is **only part of the total capital base**, while `VUAA` acts as a stabilizing ballast. So your **active sleeve is more concentrated than the whole portfolio**, and the whole portfolio is safer than the raw holdings-scoring output first suggested.

| Observation | Practical read |
|-----|----------|
| 21 tracked positions, ~$67.4k invested, ~$77.8k current value, **+15.4%** MTM | The tracked book is working overall; the main issue is selectivity and capital placement, not broad portfolio failure. |
| Plus excluded `VUAA` ETF anchor: `EUR 19,250` invested, `EUR 26,157` current (`~$22.3k` -> `~$30.3k`) | The real invested portfolio is closer to **$89.7k / $108.1k current**, and part of your concentration concern is softened by a passive safety sleeve. |
| Only **7 of 21** holdings have non-zero conviction | Most capital is sitting in names the current engine does not actively want to size up. |
| Explicit positive action signals are limited: `DUOL` = `add_long_breakout`; `FNV` and `VEEV` = `hold_quality_long` | Only a small slice of the book is clearly model-confirmed right now. |
| Mild but non-actionable positive reads: `ADBE`, `CF`, `GOOG`, `NTAP` | These look more like "possible holds / watch for repair" than "concentrate now." |
| Largest current weights include `UNH` and `NOVO_B`, both with `0.00` conviction and `neutral_watch` | The book's biggest weights are not the book's strongest current signals. |
| Several strongest winners (`VSH`, `DELL`, `STMPA`, `SNX`) also show `0.00` conviction | Legacy winners and present-tape opportunity are not the same thing; trim logic matters. |

At the same time, the Mar-Jun regime card is still saying **risk-on / volatility-rewarded tape**:

- Stable positive predictors: `ADRP|15`, `ADRP|1W`, `ATRP|1W`
- Stable negative / warning fields: `ebitda_ttm`, `oper_income_ttm`, `Recommend.MA|1M`
- Daily conviction focus is still surfacing tape-driven candidates more than fundamental compounders

That creates a very important nuance: the recent all-fields work is currently strongest as a **tactical confirmation layer**, not as a replacement for your core investing philosophy.

The current book therefore looks like a mix of three inventories:

1. **Model-confirmed core / hold candidates** - names the engine still supports (`FNV`, `VEEV`, and milder cases like `GOOG`, `NTAP`, `ADBE`).
2. **Tactical or regime-aligned positions inside a core book** - names the current tape likes more than the long-horizon model structure (`DUOL`, and some legacy winners with high recent tape strength).
3. **Legacy or unresolved capital** - names where the current system is mostly saying "not broken enough to short, not good enough to add."

This is probably the clearest articulation of where you are now: **concentration already exists in active-book capital terms, but not yet in evidence terms**. At the whole-portfolio level, `VUAA` reduces some of that concentration pressure because it functions as a passive safety anchor outside the scored opportunity sleeve.

One tooling caveat is worth calling out explicitly: the holdings scorer currently shows at least one **symbol-identity ambiguity** (`CF` surfaced as `Canaccord Genuity Group Inc.` in a summary row even though your note is `CF Industries`). That means any portfolio-mode process should move from naked tickers to **fully qualified symbols** (`NYSE:CF`, `NASDAQ:GOOG`, `CPH:NOVO_B`, etc.) before you rely on the overlay for trim / add decisions.

---

## 9. A thesis you may actually be able to believe in

You may not need a radically new investment ideology. You may need a **single concentration protocol** that works across different edge types.

Working thesis:

> Concentrate only when **structural case**, **current tape/regime fit**, and **multi-anchor persistence** agree.  
> If only one or two are present, own smaller or wait.  
> If none are present, do not confuse familiarity with conviction.

This keeps value from being your universal entry gate while still preserving a real sense of margin of safety.

| Edge type | What counts as "margin of safety" | What must be true before concentration | What invalidates it |
|-----|----------|----------|----------|
| **Core compounder** | Quality durability, balance-sheet resilience, not obviously overpriced vs peers / own history | Positive read from `quality_value_compounder`, `durable_value_compounder`, or `quality_continuation`; non-negative weeks/months RAS; no fragility overlay; backscan drift not deteriorating | Quality breaks, repeated negative anchor drift, or thesis becomes purely valuation-based |
| **Tactical / theme** | Liquidity, participation, ability to exit, strong regime alignment, no exhaustion | `conviction_daily_focus` or Playbook A alignment; relvol / ATRP support; positive score deltas vs `yesterday` and `last_week`; no `mean_reversion_exhaustion` warning | Score stalls, price diverges from score improvement, or exhaustion starts leading the read |
| **Recovery / catalyst** | Downside survivability plus evidence that the tape stopped getting worse | `forward_edge_active_v2` / `value_recovery_v2` positive; months score at least neutral-to-up; catalyst identifiable; backscan improving from weak base | It stays cheap but score progression never improves, or catalyst window passes with no confirmation |

This is the key reframing:

- For **cheap names**, low valuation alone is **not** margin of safety unless deterioration has stopped.
- For **expensive names**, low P/E is impossible, so margin of safety must come from **persistence, liquidity, multi-lens agreement, and a clear exit path**.

In other words, the thesis is not "buy value" or "buy hot stocks." It is:

> Buy the names where your current tools can prove that the market is rewarding the right kind of evidence **and** where you know exactly what would make you leave.

That is a model you can plausibly feel good using because it does not require pretending that high-multiple winners are value ideas, and it does not require pretending that every cheap stock is safe.

---

## 10. How to augment your scans with data already in hand

These are the most useful next augmentations from the tooling you already built.

### 10.1 — Add a holdings triage layer, not just a holdings report

Your current holdings scoring run is already halfway there. The next step is to classify every holding into one of five buckets:

- `confirmed_add`
- `confirmed_hold`
- `legacy_winner_trim_watch`
- `repair_or_revalidate`
- `dead_capital_watch`

Inputs can come from fields you already export:

- `conviction_score`
- `manager_action_signal`
- weeks / months `RAS`
- weight drift
- unrealized P&L vs cost
- backscan persistence (see next item)

This would stop the book from being one flat list of positions and turn it into a **decision board**.

### 10.2 — Promote backscan to a concentration gate

The new backwards-analysis workflow is probably the most promising augmentation you have right now because it can express **conviction as persistence**, not just as one-day rank.

Use it like this:

1. Run anchors: `yesterday`, `last_week`, `last_month`.
2. For candidate adds, require positive `score_delta` on at least **2 of 3** anchors.
3. Prefer names in `price_aligned` rather than `price_divergent`.
4. Treat "current score high but anchor drift flat/negative" as **watchlist, not concentration**.

This is especially useful for expensive growth / momentum names where valuation will not help you feel safe.

### 10.3 — Separate tactical field research from core field research

Your Mar-Jun scan-period batch validated a **risk-on tactical playbook**. That does **not** mean it validated a new core investing model.

Split the research lane:

- **Tactical lab:** `ADRP`, `ATRP`, relvol, momentum, event, theme participation
- **Core lab:** quality, revisions, operating improvement, EDGAR discipline, industry-relative valuation

If you keep asking one field-correlation run to answer both questions, it will keep pushing you toward either overfitting or philosophical confusion.

### 10.4 — Use industry packs when hunting "the next Micron"

Global cross-sectional ranks are too blunt for many theme trades. Use your existing scan stack to run **theme-local universes**:

- semiconductors
- power / grid / electrical equipment
- defense / aerospace
- selected software clusters

Why this matters:

- peer-relative normalization is better
- you stop comparing cyclical leaders to random defensive names
- a "hot stock" thesis becomes "best-in-theme with confirmation," which is easier to believe in than raw FOMO

### 10.5 — Harden identity and book completeness before relying on portfolio-mode outputs

Two process fixes are now high priority:

1. **Fully qualified symbols** in holdings config to eliminate ticker collisions.
2. **Explicit ETF / cash / off-book tracking** so the scorer can reflect real concentration and dry powder even before full ETF support exists.

Right now the holdings file is informative, but the portfolio review should explicitly carry `VUAA` as a separate safety bucket until the tooling can score or at least track ETFs directly.

### 10.6 — Add thesis metadata beside each position

The holdings config or an adjacent file should eventually include:

- `thesis_style` (`core_compounder`, `tactical_theme`, `recovery_catalyst`)
- `thesis_profile`
- `expected_horizon`
- `hard_stop_pct` or invalidation rule
- `review_cadence`
- `add_only_if`

That would let the overlay answer a more useful question:

> Is the position doing what it was bought to do?

instead of just:

> Does today's model like it?

### 10.7 — Stop blending every good signal into one mega-score

A likely source of discomfort is that a new "investment model" sounds like one more composite score. You probably do **not** want that.

Better approach:

- Let **lane fit** pick the edge type.
- Let **regime fit** confirm whether now is the right time.
- Let **backscan persistence** decide whether the move is strengthening.
- Let **overlays** (`fragility_short`, `mean_reversion_exhaustion`) decide risk control.

That is not a new ideology. It is a cleaner permission system for concentration.

---

## 11. Related docs (tooling map)

| Doc | Use when |
|-----|----------|
| [tradingview_move_prediction_profile_weighting_reference.md](../tradingview_move_prediction_profile_weighting_reference.md) | Profile behavior, v3 lanes, action signals |
| [prediction_module2_run_output_guide.md](../prediction_module2_run_output_guide.md) | Orthogonal pillars, less overlap |
| [pre_earnings_profile_analysis.md](../pre_earnings_profile_analysis.md) | Catalyst / earnings week |
| [active_screening_analysis_fundamentals.md](active_screening_analysis_fundamentals.md) | Core fundamental composite |
| [active_manager_implementation_usage.md](../active_manager_implementation_usage.md) | Weekly run cadence |
| [active_manager_profile_and_pattern_discovery_plan.md](../improvements_plans/active_manager_profile_and_pattern_discovery_plan.md) | Sleeve budgets, pattern discovery |
| [backwards_prediction_analysis_guide.md](../backwards_prediction_analysis_guide.md) | Current-vs-anchor score progression and persistence checks |

---

*This is a starting point. The next useful refinement is to turn the current holdings overlay + backwards anchors into a live concentration protocol rather than another research output.*
