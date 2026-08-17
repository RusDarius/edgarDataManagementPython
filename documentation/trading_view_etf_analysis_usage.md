# ETF analysis module

Daily world-primary ETF scan for **active management**: read what capital is paying for, then pick clean 1x vehicles inside those sleeves. This is not a second stock-fundamental model — company quality / valuation / earnings fields are empty on the ETF screener and are denylisted.

**Entrypoint:** `run_etf_scan_and_analysis_suite` in [`src/data_analysis_scripts/trading_view_etf_analysis.py`](../src/data_analysis_scripts/trading_view_etf_analysis.py)  
**Call site:** [`src/main.py`](../src/main.py) (ETF block)  
**Book logic:** [`src/data_analysis_scripts/trading_view_etf_active_book.py`](../src/data_analysis_scripts/trading_view_etf_active_book.py)  
**Scan payload:** `AMERICA_ETF_MOVE_PREDICTION_COLUMNS` in [`src/data_loaders/api_tradingview_client.py`](../src/data_loaders/api_tradingview_client.py) (~142 fields)  
**Config:** [`config/etf_analysis/active_manager_v1.json`](../config/etf_analysis/active_manager_v1.json)  
**Holdings overlay:** [`config/holdings_scoring/current_holdings.json`](../config/holdings_scoring/current_holdings.json)

---

## Mandate

ETFs are used two ways:

| Role | Question | Output |
| --- | --- | --- |
| Research / tracking baskets | What is the tape paying for? | Regime tape + sleeve heat + rotation |
| Tradable vehicles | If I need that exposure, which 1x share class? | Vehicle quality inside the sleeve |

Never z-score SPY against a 3x semiconductor product, a HY bond fund, and gold in one universe. Peer-score inside **product class → asset class → category → focus → niche**.

---

## Pipeline

```
scan_global_etf_move_prediction   (world primary listings, AUM filter)
        │
        ▼
v1 composite                      (global MAD-z: momentum / trend / attention / structure)
        │
        ▼
active book                       (derived fields → peer groups → 8 lenses → reports)
        │
        ▼
DuckDB week file + run logs/csv
```

1. Scan ETF screener (`is_primary=true`, `type=fund` + `typespecs has etf`).
2. Drop missing AUM / outside AUM band.
3. **v1 composite** — baseline global rank (kept as a column).
4. **Active book** — product-class tag, derived fund-economics, peer MAD-z, lenses, daily artifacts.
5. Persist raw rows + scores + book tables. Day-over-day uses the prior `run_id` in the same week DuckDB.

---

## v1 composite (baseline)

Global MAD z-scores (not peer-relative):

| Component | Weight | Fields |
| --- | --- | --- |
| Near momentum | 0.22 | `Perf.5D`, `Perf.W`, `Perf.1M` |
| Medium momentum | 0.18 | `Perf.3M`, `Perf.6M`, `Perf.YTD` |
| Long momentum | 0.12 | `Perf.Y`, `Perf.3Y`, `Perf.5Y` |
| Trend | 0.18 | `Recommend.All`, close vs SMA50/200, `RSI` |
| Attention | 0.15 | relative volume, `fund_flows.1M` / AUM, CMF |
| Structure | 0.15 | log AUM, lower expense, tighter NAV gap |

Use this as a coverage/baseline rank. Do not treat “top 40 global composite” as the book.

---

## Active book

### Derived fields

| Metric | From | Reads as |
| --- | --- | --- |
| `product_class` | name / niche / category | `1x`, `levered`, `inverse`, `inverse_levered` |
| `flow_to_aum_*` | `fund_flows` / `aum` | Creation/redemption intensity |
| `organic_demand_*` | `aum_perf` − NAV total return | AUM grew from flows, not mark-to-market |
| `tracking_gap_*` | market `Perf` − NAV total return | Traded price vs basket |
| `close_vs_sma*` / `% from high` | close vs SMA / 52w high | Trend vs extension |
| `dollar_liquidity` | `Value.Traded` (else 10d avg) | Can you trade it |

NAV total return = what the basket did. Market `Perf.*` = what you could have traded. The spread is a feature.

### Peer groups

Finest group with ≥ `min_peer_group_size` (default 8), else roll up:

`product_class + asset_class + category + focus + niche` → … → `product_class` → universe.

Levered / inverse are isolated from 1x. Vehicle-quality scores 1x only.

### Lenses

Peer-relative 0–100 scores. `crowded` and `dead_product` are **inverted** in consensus (high crowded → lower book score).

| Lens | Question |
| --- | --- |
| `sleeve_continuation` | Confirmed 1x sleeve trend (NAV return, MAs, ADX, weekly Recommend, flow) |
| `flow_confirmed` | Price paid for with creations (organic demand, CMF, relative volume) |
| `early_rotation` | Sleeve turning: 5D + flow inflecting, still off 52w high |
| `catch_up` | Strong book score, lagged 1M tape vs sleeve |
| `vehicle_quality` | If you need the exposure: AUM, ADV, fee, tight NAV (1x) |
| `macro_hedge` | Duration / USD / gold / credit / min-vol that is working |
| `crowded` | Extended: high RSI, near highs, huge inflows, rich premium |
| `dead_product` | Outflows, AUM decay, illiquid, wide NAV gap — avoid |

### Daily read order

1. Regime tape (canonical baskets: beta, sectors, factors, rates/credit, USD/commodities, crowded themes).
2. Sleeve heat (1x median 5D/1M + flow).
3. Flow vs price divergences (price up / flow out vs price down / flow in).
4. Catch-up vs extended inside focus/niche.
5. Vehicle quality for sleeves you might hold or hedge.
6. Day-over-day vs prior run.
7. Holdings overlay vs the single-name book.

Success test: after one scan you can answer (1) risk-on or not, (2) which sleeves are being paid, (3) where flows confirm price, (4) which 1x you would actually use, (5) whether the stock book is aligned.

---

## Usage

### Daily run (`main.py`)

Uncomment the ETF block. Same call as before — the book runs by default.

```python
etf_result = run_etf_scan_and_analysis_suite(
    TRADINGVIEW_API_CLIENT,
    min_aum_usd=500_000_000,
)

print(etf_result["run_output_dir"])
print(etf_result["regime_tape_log"])
print(etf_result["sleeve_heat_log"])
print(etf_result["catch_up_log"])
print(etf_result["_duckdb_database"])
```

```powershell
cd D:\FinanceProjects\edgarDataManagementPython
$env:PYTHONPATH="src"
python src/main.py
```

### Analyze an existing scan payload

```python
from pathlib import Path
from data_analysis_scripts.trading_view_etf_analysis import run_etf_analysis_suite_duckdb

result = run_etf_analysis_suite_duckdb(
    scan_data=scan_response,          # API payload or list of mapped rows
    min_aum_usd=500_000_000,
    run_label="etf_daily",
    output_dir=Path("logs/tradingview_analysis/etf_analysis/duckdb_runs"),
)
```

### Optional kwargs

| Kwarg | Default | Use |
| --- | --- | --- |
| `min_aum_usd` | none | Size floor (also `min_market_cap_usd` alias) |
| `categories` / `markets` / `industries` | all | Narrow the API scan |
| `include_active_book` | `True` | `False` = v1 composite only |
| `active_book_config_path` | `config/etf_analysis/active_manager_v1.json` | Baskets, lens weights, holdings map |
| `holdings_config_path` | `config/holdings_scoring/current_holdings.json` | Overlay tickers |
| `export_parquet` | `True` | Week parquet export |
| `run_label` | generated | Stable prefix on `run_id` |

### Query the week DuckDB

```python
from db.trading_view_etf_scan_duckdb import query_etf_scan_duckdb

db = str(etf_result["_duckdb_database"])
run_id = etf_result["run_id"]

query_etf_scan_duckdb(
    db,
    """
    SELECT symbol, product_class, sleeve_key, book_consensus, book_direction,
           flow_to_aum_1m, organic_demand_1m, vehicle_quality
    FROM etf_book_rows
    WHERE run_id = ? AND product_class = '1x'
    ORDER BY book_consensus DESC NULLS LAST
    LIMIT 40
    """,
    [run_id],
)
```

---

## Output layout

```
logs/tradingview_analysis/etf_analysis/duckdb_runs/
  iso_year=YYYY/week=WW/
    etf_analysis_YYYY_WWW.duckdb
    parquet/
    runs/<run_id>/
      etf_analysis_overview.log      # v1 top 40
      etf_category_leaders.log
      etf_ranked.csv                 # v1 composite
      etf_regime_tape.log
      etf_sleeve_heat.log / .csv
      etf_divergences.log
      etf_catch_up_vs_extended.log
      etf_vehicle_quality.log / .csv
      etf_lens_leaders.log
      etf_holdings_overlay.log
      etf_dod_changes.log            # empty/"first session" on first run in the week file
      etf_book_ranked.csv            # peer-relative book
```

Multiple daily runs in the same ISO week share one DuckDB file. DoD diffs against the previous `run_id` in that file.

### DuckDB tables

| Table | Contents |
| --- | --- |
| `raw_scan_rows` | Full scan payload |
| `etf_ranked_scores` | v1 composite |
| `etf_category_leaders` | Category top-5 (v1) |
| `etf_book_rows` | Derived fields + lenses + consensus |
| `etf_regime_tape` | Canonical basket prints |
| `etf_sleeve_heat` | 1x sleeve medians |
| `etf_divergences` | Flow vs price flags |
| `etf_catch_up` | `catch_up` / `extended` roles |
| `etf_vehicle_quality` | Best 1x per sleeve |
| `etf_lens_leaders` | Top names per lens |
| `etf_holdings_overlay` | Book tickers → thermometer ETFs |
| `etf_dod_changes` | Notable consensus / flow flips |

---

## Config

[`config/etf_analysis/active_manager_v1.json`](../config/etf_analysis/active_manager_v1.json)

- `canonical_baskets` — thermometer tickers (VOO/QQQ/IWM, XLK…, TLT/HYG, GLD/UUP, SMH…).
- `holdings_sleeve_map` — map `current_holdings.json` tickers to those ETFs (software → XLK/IGV/QQQ, AMD → SMH, FNV → GLD/GDX, China → MCHI/FXI, …).
- `lens_weights` / `inverted_lenses` / `min_peer_group_size`.

Edit aliases there; do not hardcode baskets in Python.

---

## Holdings scoring (actual ETF positions)

The ETF suite's `etf_holdings_overlay.log` is a **research overlay**: it maps stock tickers in `current_holdings.json` to thermometer ETFs. To score an ETF you actually hold, add it to the same holdings file with `"instrument_type": "etf"`. `run_holdings_scoring_analysis` then auto-routes:

| `instrument_type` | Default | Joins |
| --- | --- | --- |
| omitted / `stock` / `equity` | stock | latest move-prediction DuckDB |
| `etf` / `fund` / `etp` | etf | latest ETF analysis DuckDB (`etf_book_rows`) |

Example entry (do not invent size; use your real cost basis):

```json
{
  "ticker": "VOO",
  "symbol": "AMEX:VOO",
  "instrument_type": "etf",
  "sleeve": "core",
  "invested_sum": 10000,
  "invested_currency": "USD",
  "average_price": 500,
  "price_currency": "USD"
}
```

`symbol` should be the TradingView listing (`AMEX:VOO`, `NASDAQ:QQQ`, …) when the bare ticker is ambiguous. Mixed stock + ETF books are one holdings run: stocks export under `scans/move_prediction_v1/`, ETFs under `scans/etf_active_book_v1/`. The shortlist Conv column is book consensus for ETFs (not stock conviction).

Run the ETF suite first (or pass `etf_database_path` / `etf_run_id`) so there is an ETF DuckDB to join.

---

## Tests

```powershell
$env:PYTHONPATH="src"
python -m unittest src.tests.test_trading_view_etf_scan src.tests.test_trading_view_etf_analysis src.tests.test_trading_view_holdings_scoring_analysis -v
```

Covers scan payload, v1 persist, product-class tagging, organic demand / tracking gap, peer roll-up, levered isolation from vehicle quality, parent entrypoint, and day-over-day in one week DB.
