# Data used for ranking and advice

Companion to [scoring_layer_order.md](scoring_layer_order.md). This is the
**field inventory**: fundamentals vs technicals vs other, and which layer
consumes them.

Nothing here is a 0–100 score. Cite the field you used.

---

## How to read a pack name row

Compile compact (`_compact_name`) is what Radar / Build-50 / Book tables see.

| Pack key | Layer | Kind | Meaning |
|---|---|---|---|
| close, mcap, exchange, ind | 0–1 | identity | Tape close (leftover uses this, not stale holdings mark) |
| day, w, d5, m1, m3 | 1 | technical / tape | Session / week / 5D / 1M / 3M return % |
| relvol | 1 | technical | Volume vs ~10d average |
| rsi, adx | 1 | technical | RSI; ADX on movers keep |
| rng, vs50 | 5 | technical (derived) | 52w range used %; vs SMA50 % |
| pe, pe_fwd, peg, evebitda, pb, evfcf, opm, evrev | 1 | fundamental | Trailing / forward multiples, margin |
| pt, street_left, street_px | 1+5 | fundamental | Street PT $ and leftover |
| edge_left, opp, lean | 3 | edge | FV upside %, opportunity rank, directional lean |
| left, rr, target_px | 5 | derived | Pack leftover (max street, edge), rr, pack $ target |
| bo, cont, fwd, exh, frag | 2 | technical (profile) | Weeks profile scores |
| mix, conv, regime | 2 | mix / composite | Mix class, conviction (do not leftover-rank with it), regime fit |
| dte | 4 | calendar | Days to next earnings |
| mtp | 4 | timing | ENTER_SMALL / WATCH / AVOID_CHASE |

Peer columns on **movers** and pack names: `val_field`, `val`, `val_vs_ind`, `peer`
(generic `setup.attach_setup` after `derive.attach_vs_group`, US $2B industry
median, min n=6). `evrev_vs_ind` remains on the row but is **not** the Peer
quote unless `val_field` is `evrev`. Street/TV forward columns: `fwd_street_pct`,
`fwd_street_px`, `fwd_pack_pct`, `fwd_pack_px`, `fwd_pe`. Model join (`fp_*`) is
the separate forward suite, not a blend into leftover.

---

## A. Fundamentals (valuation, quality, street)

### All-fields → pack (layer 1)

Whitelist: `extract.AF_CANDIDATES`.

| Family | All-fields names | Pack | Used for |
|---|---|---|---|
| Size | `market_cap_basic` | mcap | Universe gate $2B / satellite |
| Street PT | `price_target_average`, `price_target_1y` | pt, street_px | Street leftover |
| Earnings calendar | `earnings_release_next_date` (+ fallbacks) | dte | Catalyst lanes |
| Trailing PE | `price_earnings_ttm` | pe | Current value |
| Forward PE | `price_earnings_forward_fy`, `non_gaap_price_to_earnings_per_share_forecast_next_fy` | pe_fwd | Often **null** — then leftover + PEG |
| PEG | `price_earnings_growth_ttm` | peg | Growth-adjusted PE |
| EV / sales | `enterprise_value_to_revenue_ttm` | evrev | Quote **only** when `val_field` is evrev |
| EV / EBITDA | `enterprise_value_ebitda_ttm` | evebitda | Current cash-earnings multiple |
| EV / FCF | `enterprise_value_to_free_cash_flow_ttm` | evfcf | Cash generation |
| Book | `price_book_ratio` | pb | Current |
| Margin | `operating_margin` | opm | Quality snapshot |

**Empty pe_fwd is a data gap**, not a reason to skip the Value tab. Do not
substitute EV/Rev. Use pack `val_field`.

### Move-prediction **quality** family (layer 2, inside profile JSON)

Used heavily by `quality_continuation_v1` and `forward_edge_active_v2`;
**near-zero on weeks** for `breakout_long_v1`. Typical inputs (see each profile
JSON `component_signal_weights.quality`): ROIC / FCF margin / Piotroski /
revenue efficiency / EPS forward growth / sequential improvement — whatever
that profile file lists. Do not assume every profile uses every quality field.

### Move-prediction **valuation** family (layer 2)

PE, PEG, EV/Rev-style signals inside the profile. Weeks `breakout_long_v1`
sets valuation weight **0**. `forward_edge_active_v2` weeks valuation **0.14**.

### Edge valuation (layer 3)

`forward_valuation_upside_pct` is a **model fair-value** leftover from the
forward-upside-valuation lens (long/med/near mix inside edge — see
`documentation/edge_upside_rankings_and_tradeable_safety_guide.md`).

Safety companion / balance-sheet flags feed `upside_opportunity_score`
**inside edge**. Compile only keeps `edge_left`, `opp`, `lean`.

---

## B. Technicals (tape, trend, participation)

### All-fields → pack (layer 1)

| Family | All-fields names | Pack |
|---|---|---|
| Price | `close` | close |
| Returns | `change`, `Perf.W`, `Perf.5D`, `Perf.1M`, `Perf.3M` | day, w, d5, m1, m3 |
| RSI | `RSI` | rsi |
| Moving averages | `SMA20`, `SMA50`, `SMA200` | sma20/50/200; vs50 derived |
| 52-week | `price_52_week_high` / `_low` (fallbacks `High.52Week` / `Low.52Week`) | rng |
| Volume | `relative_volume_10d_calc` | relvol |
| Trend strength | `ADX` | adx (movers keep) |

### Move-prediction component families (layer 2)

All profiles share seven **families**. Horizon weights change the mix.

| Family | What it measures | Example raw / derived signals |
|---|---|---|
| attention | Participation | relvol, value traded, float/dollar turnover, volume trend, CMF, squeeze |
| event | Gaps / prints | pre/postmarket, gap, EPS surprise |
| momentum | Direction of price | change, Perf.5D/W/1M, ROC, MACD, Aroon, ADX spread, RSI-centered |
| trend | Structure vs MAs | close vs SMA/EMA 10–200, VWAP, Donchian, PSAR, BB position |
| quality | Operating substance | see Fundamentals above |
| valuation | Cheap/expensive vs growth | PE/PEG/EV style inside profile |
| safety | Dispersion / drawdown | PT dispersion, safety weights on longer horizons |

**Weeks weights (the pack’s bo/cont/fwd):**

| Profile | att | event | mom | trend | quality | val | safety |
|---|---:|---:|---:|---:|---:|---:|---:|
| breakout_long_v1 | 0.20 | 0.10 | 0.28 | 0.28 | 0.02 | 0.00 | 0.12 |
| quality_continuation_v1 | 0.12 | 0.06 | 0.20 | 0.22 | 0.18 | 0.08 | 0.14 |
| forward_edge_active_v2 | 0.05 | 0.08 | 0.11 | 0.21 | 0.26 | 0.14 | 0.15 |

Exact signal lists: `config/move_prediction_profiles/profiles/`.

### Edge technicals (layer 3, before valuation)

Screen `composite_score` = mean of six **directional universe percentiles**:

- ADRP
- relative volume 10d
- value traded
- volatility_core sleeve (ADRP, ATRP, Volatility.D/W/M, beta)
- liquidity_core sleeve
- momentum_context sleeve (`Recommend.All`, Perf.5D/1M/3M)

That is why quiet names miss edge highlights.

Upside-opportunity extra technicals (edge scanner, not all on pack):
beta, Volatility.D/W/M, ATR/ATRP, ADX, RSI, SMA20/50, Perf.5D/1M/3M.

### Movers Val / Peer / Proj / Tech (tape groovers, not Build-50)

Keep list: `config/generic_utils/movers_day.json` / `movers_3m.json`.

| Column group | Fields |
|---|---|
| Val | `val_label`, `val` (`generic_utils.setup`). Not always EV/Rev. |
| Peer | `val_vs_ind`, `peer` (DISCOUNT / PREMIUM / THIN / NA) |
| Proj | left, bo, cont, fwd, street_px, target_px |
| Tech | tech_sma, tech_rng, rsi, vs50, adx |
| down_class | BOUNCE / CONTINUE_DOWN / MIXED on laggards (`sleeves.classify_punished_tape`; cheap uses `val_vs_ind`, not leftover EV/Rev) |

Up tail is price rank (best → least), not leftover. Down tail is the punished
25; the canvas **chooses** bounce vs keep-falling from that pool.

---

## C. Other data (timing, Book, progression, climate)

| Data | Source | Role in advice |
|---|---|---|
| Mix / conviction / RAS | pred `conviction_rankings` | Classifier + trap flag; RAS for thesis-kill |
| Regime tier | `regime_context_scores` | Active-mgmt climate |
| MTP action | market_timing_policy CSV | Bounce vs chase climate |
| Holdings mark, vs-cost, weight | holdings scoring + `current_holdings.json` | Book actions; leftover still from tape close |
| NAV, wt_nav, loss ladders | `generic_utils.risk` / pack `capital` | How much a drop costs vs equity+cash; not a 0–100 |
| Prior operator action | priors JSON | Polar / continuity |
| Δbo Δcont Δfwd | `compare --session --exchanges NASDAQ,NYSE,AMEX` | Progression vs yesterday’s pred run |
| EV/Rev path | `history` + `span` on **`val_field`** | Rerating vs price; skip EV/Rev when it is not `val_field` |
| Industry 5D breadth | `aggregations.group_stats` on US $2B | Regime before naming stocks |
| sleeve_count | compile | How many domain sleeves confirm the name |

MTP universe note: recommendations cover edge upside ranks 1–50 **union**
screen 1–100. High-ADR bounce names dominate ENTER_SMALL. That is expected.

---

## D. What ranks the Build-50 (not a new score)

Sort key `sleeves.unpaid_sort_key`:

1. Live weeks profile (bo/cont/fwd ≥ 0.35) **before** dead tape
2. Edge `opp` rank (lower better; missing last)
3. Pack `left` descending

Then industry cap 5, Book exempt, take 50.

`rr` is shown for unused-range vs paid-range. It is **not** the sort key.
High rr on `radar_upside_100` that the cap hid is **omitted opportunity**, not
an automatic NEW.

---

## E. What the operator may add (not in the ranker)

Allowed as **judgment on top of** the 50, with a cited field:

- News / print date / implied move (options) — week map, not a new score
- Peer **primary** multiple vs industry (`val_vs_ind`), not a remembered competitor and not a spare EV/Rev
- Invalidation (SMA50, prior prior-invalidation close)

Not allowed: a homemade 0–100 “best trade” that averages leftover × bo × MTP.

---

## Pointers

| Need | Open |
|---|---|
| Layer order | [scoring_layer_order.md](scoring_layer_order.md) |
| All-fields aliases | `src/operator_briefing/extract.py` `AF_CANDIDATES` |
| Leftover formulas | `src/generic_utils/derive.py` |
| Forward street/TV + finproj join | `src/generic_utils/forward_value.py` |
| Manual suite runner | `src/run_operator_suites.py` (`--list`, `--gitbash`) |
| Git Bash data-flow commands | [gitbash_agent_data_flows.md](gitbash_agent_data_flows.md) |
| Sleeve cutoffs | `src/operator_briefing/sleeves.py` |
| Profile weights | `config/move_prediction_profiles/profiles/` |
| Edge pipeline | `documentation/edge_research_tools_active_management_playbook.md` |
| Pred SQL names | `src/generic_utils/recipes.py` |
