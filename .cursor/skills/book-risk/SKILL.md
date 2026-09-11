---
name: book-risk
description: Data-driven Book risk exposure and loss tolerance (position %, net $, % of NAV including cash). Progressive ladders, SMA50/ATR structure, trim-and-reenter cash, industry concentration. Use when the user asks how much loss to tolerate, whether to trim or add, cut-and-go-back-in, NAV risk, or to run a standalone check on current_holdings.json / the latest holdings scan. Do not use for leftover radar, movers, or Build-50.
---

# Book risk (loss vs NAV)

**How much loss can this Book absorb — on the name, in dollars, and as % of NAV (equity mark + cash)?** Daily leftover answers **can this still pay**. This suite answers **what a drop costs**.

Do not invent a 0–100 "risk score". Do not write `logs/_tmp_*.py`.

Standalone: `current_holdings.json` and/or latest `holdings_scoring_*` scan. Join leftover / vs50 / ATR from the briefing pack when a pack exists.

On DAILY (full): cite pack `capital` / `wt_nav` / `wipe_nav_pct` on Book. Do not steal Daily tabs. Full ladders are this suite — **own canvas**, same pattern as `daily-focus-YYYYMMDD` and `movers-3m-YYYYMMDD`.

## Commands

```powershell
$env:PYTHONPATH='src;.'
python src/operator_briefing/example_entry.py inspect
python src/generic_utils/tv_scan_cli.py risk --run latest --pack logs/tradingview_analysis/operator_briefing/runs/<newest>/briefing_pack.json --out-dir logs/tradingview_analysis/operator_briefing/runs/<newest>/book_risk
python src/generic_utils/tv_scan_cli.py risk --holdings config/holdings_scoring/current_holdings.json --config-only --out-dir RUN/book_risk_cost
```

`--out-dir` is the **scan** folder: `book_risk.md`, `book_risk.csv`, `risk_levels.csv`, `industry_exposure.csv`, `capital.csv`, `book_risk.duckdb`, `overview.log`. If omitted, CLI writes `logs/tradingview_analysis/operator_briefing/runs/book_risk_YYYYMMDD_HHMM_utc/`. Pass `--out-dir` for a custom scan location.

`--config-only` is USD-naive on DKK/EUR names (NOVO, VUAA). Prefer `--run latest` for mark-to-market. Ladders use `close_usd` and cost USD/share.

Recipe (ladder sizes, not eligibility): `config/generic_utils/book_risk.json`.

## Deliverables (same turn — do not announce then stop)

RISK is a canvas-first suite, like DAILY and MOVERS (3M).

1. **Generic scan** (always): `tv_scan_cli.py risk --out-dir …` → `book_risk.md` in that folder. Numbers only. No TRIM/HOLD/EXIT in the md.
2. **Agent canvas** (always): write `book-risk-YYYYMMDD.canvas.tsx` in the Cursor project `canvases/` directory (same place as `daily-focus-YYYYMMDD` and `movers-3m-YYYYMMDD`). Cover **every** Book name. One Ledger. Tabs: **Guide / Plan / Positions / Ladders / Path / Setup / Concentration / Ledger**. Chat is a short snapshot **plus a markdown link to that canvas**.
3. **Fallback:** if the canvas Write fails, the brief still exists at `--out-dir/book_risk.md`. Link that path in chat and finish the course there. Never end a turn with “writing the canvas next”.

Do not put RISK tabs on `daily-focus-YYYYMMDD`. Do not skip the canvas because the operator “only asked for a look”.

## What the numbers mean

| Field | Meaning |
|---|---|
| NAV | equity mark + cash. Pack `capital.nav_usd`. |
| wt_nav / wipe_nav_pct | This name as % of NAV. Ceiling if it goes to zero. |
| vs_cost / nav_now_pct | Already-booked P&L vs cost, and that $ as % of NAV. |
| m5/m10/m15/m20 | Further drop **from today's mark**. `_usd` and `_nav`. |
| c15 | Price 15% below **cost** (continuity thesis-kill zone). `c15_usd` is additional vs mark. |
| sma50_nav / atr_nav | $ / % NAV if price prints SMA50 or close − 1.5×ATR(P). |
| trim33_cash / trim33_remain_nav | Sell 33%: cash raised, leftover NAV-at-risk. |
| stress_m10_nav | If **every** name is −10% from here, NAV change. |
| max_wipe_nav | Largest single-name wipe %. |

`wt` on old pack Book is **cost** weight and **excludes cash**. Use `wt_nav` for risk.

## Work order

1. inspect. Prefer pack `capital` + `book[]` after compile. Else `risk --run latest --pack PACK.json --out-dir`.
2. Capital first: NAV, cash %, unrealized, `stress_m10_nav`, `max_wipe_nav`, industry `nav_pct`.
3. Per name: vs_cost already taken, leftover/rr/rsi/vs50/dte/mix, then the ladder (mark → SMA50/ATR → cost −15). Quote **three frames**: position %, $, % NAV. If vs50 > 0, SMA50 is the first stop *below*; if vs50 < 0, SMA50 is a **bounce above** — stop is ATR or c15.
3b. Path: `history` / `span` on Book tickers (all-fields close, street PT, RSI, ATRP, **and the row’s `val_field`** — not EV/Rev for every name). Leftover opening vs leftover dying vs cheap-and-broken. Do not invent a 0–100.
3c. Setup: `tv_scan_cli.py setup` / pack `val_field` `val_vs_ind` `peer` `tech_sma` `tech_rng`. Dedicated Value + Tech section. Do not quote EV/Rev when PE / EV/EBITDA / P/B is the native field.
4. Course: TRIM / HOLD / HOLD_NO_ADD / EXIT / ADD using Daily polar + leftover + path — **not** a new score. Size the TRIM with trim33/50 vs remaining wipe_nav and cash for re-entry. **Average down** only as polar ADD: leftover live, mix not avoid, cash from EXIT/TRIM, toward SMA50/ATR, remaining wipe still wanted. Default is no. Never average into dte ≤ 7.
5. Write `book_risk.md` via the CLI, then **write the canvas in this turn**, then chat (NAV snapshot + names that change capital + canvas link).
6. Generic-vs-domain note.

## How much to tolerate (judgment on top of the ladder)

There is no single stop %. Bound each name by **wipe_nav** (size) and the **next structure print** (sma50_nav or atr_nav).

- **Noise vs thesis.** Live leftover + unused range (low rng) + live bo/cont → a −5/−10 from mark is usually ATR noise. Do not cut a live thesis for m5. Continuity already codes the floor: **vs_cost ≤ −15 and lost SMA50 and weak RAS** → thesis-kill, not "wait for −25".
- **Paid / chase.** Leftover gone, rng ≥ 85, RSI ≥ 70 → further-loss budget is small. TRIM here **locks a win**, it is not "cutting a loser".
- **Mix `avoid_value_trap` + live leftover Book** → HOLD_NO_ADD, not EXIT. Polar ADD/NEW cannot become EXIT unless thesis_kill.
- **Cut some, go back in.** TRIM so (1) remaining `wipe_nav` is a size you still want if the name is right, and (2) `trim*_cash` can fund a starter at SMA50 or ATR without enlarging the original. Do not 100% EXIT then re-enter bigger.
- **Average down / double down.** Not the same as ADD. Adding to an underwater line is allowed only when leftover is live, mix is not avoid, cash comes from EXIT/TRIM (not from another live leftover), and the add is toward SMA50/ATR — not a chase of the mark. Thesis-kill, leftover-gone, street-PT-cut-with-price, and dte ≤ 7 are all **no**. Polar ADD ↛ EXIT unless thesis_kill.
- **Concentration.** Two names in one industry at 9% NAV each = 18% industry NAV. `industry_exposure.nav_pct`. Size is the risk even if leftover is live.
- **Cash** is reload, not a reason to hold a broken name. High `cash_pct` after a TRIM is the point of the TRIM.
- **General book.** `stress_m10_nav` is the "if the whole book is −10% from here" number. `max_wipe_nav` is the largest single-name disaster. If that is large, TRIM is de-risking **size**, not a leftover call.
- MTP ENTER_SMALL is climate, not a buy-the-dip list. Earnings dte ≤ 7: DERISK sticks; do not add into the print to "average down".

Standing spec: `src/focus_pool_screening/prompts/risk_generic.txt`. Field map: [reference.md](reference.md).
