# Book risk reference

Use with `.cursor/skills/book-risk/SKILL.md`. Formulas: `src/generic_utils/risk.py`.

## Inputs

| Source | What you get |
|---|---|
| `config/holdings_scoring/current_holdings.json` | cash, ticker, invested_sum, average_price, sleeve. **No close.** FX sums are native currency. |
| Latest `holdings_scoring_*/scans/move_prediction_v1/holdings__summary.csv` (+ ETF csv) | USD cost, shares, close, value_usd, vs_cost, industry |
| Pack `book[]` / `capital` | leftover, vs50, rsi, rng, rr, dte, mix, mtp, bo, ATR if all-fields had ATRP |
| All-fields ATRP / ATR / beta | via pack names after compile, or `named --fields ATRP` |

## CLI

```powershell
python src/generic_utils/tv_scan_cli.py risk --run latest --pack PACK.json --out-dir RUN/book_risk
python src/generic_utils/tv_scan_cli.py pack-focus --pack PACK.json --sleeve book --out book.csv
```

DuckDB tables: `book_risk`, `risk_levels`, `industry_exposure`, `capital`.

Always also writes **`book_risk.md`** (deterministic ladder dump, no course).
If `--out-dir` is omitted, CLI uses `logs/tradingview_analysis/operator_briefing/runs/book_risk_YYYYMMDD_HHMM_utc/`.

Agent canvas (judgment): `canvases/book-risk-YYYYMMDD.canvas.tsx` next to `daily-focus-YYYYMMDD` / `movers-3m-YYYYMMDD`. Not a Daily tab. Tabs: Guide / Plan / Positions / Ladders / Path / Setup / Concentration / Ledger.

Path (generic, no cutoff): `tv_scan_cli.py history` on Book ids + `span` on close / PT / **`val_field`**. Leftover opening vs dying vs cheap-and-broken is domain.
Setup: `tv_scan_cli.py setup` / pack `val_*` / `peer` / `tech_*`. Do not quote EV/Rev when it is not `val_field`.

## Formulas (no cutoff)

```
NAV = equity_mark + cash
wt_nav = value_usd / NAV * 100          # = wipe if price → 0
m10_usd = shares * (close * 0.90 - close)
m10_nav = m10_usd / NAV * 100
sma50_px = close / (1 + vs50/100)
atr_px  = close * (1 - atr_mult * ATRP/100)
c15_px  = average_price * 0.85
```

Ladders are recipe sizes (`book_risk.json`). Continuity −15% vs cost is **cited** as the `c15` rung, not an eligibility filter inside `risk.py`.

## Compile

Next `example_entry.py compile` stamps pack `capital`, `industry_exposure`, `risk_levels`, and per-book `wt_nav` / `m*_nav` / `trim*_cash`. DuckDB `capital` / `risk_levels`.
