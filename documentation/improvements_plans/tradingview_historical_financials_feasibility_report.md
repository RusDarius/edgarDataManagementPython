# TradingView Historical Financial Statement Data — Feasibility Report

**Purpose:** Assess whether `export_all_tradingview_fields_duckdb` (and the underlying
`ApiTradingViewClient` scanner API) can be extended to pull **backward/historical**
financial-statement data (income statement, balance sheet, cash flow — by fiscal year
and fiscal quarter) the way TradingView renders it on pages like
`https://www.tradingview.com/symbols/NASDAQ-CIFR/financials-income-statement/`.

**Verdict up front:** No. The scanner API this repo already uses only exposes the
**current** fiscal quarter / fiscal year / TTM value for each fundamental field, not a
historical series. The multi-year/multi-quarter table shown on TradingView's own
"Statements" pages is **paywalled** (verified live, see evidence below) and there is no
clean public JSON endpoint carrying it. This repo already has a better, ToS-clean
source for real historical statement data — SEC EDGAR, which this project ingests into
`edgar_financial_data_concepts` — and the right move is to join that with the existing
TradingView all-fields snapshot rather than scrape TradingView's gated statements UI.

---

## 1. How the existing pipeline fetches TradingView data today

`src/data_analysis_scripts/trading_view_export_all_tdfields.py` →
`export_all_tradingview_fields_duckdb()` and `src/data_loaders/api_tradingview_client.py`
(`ApiTradingViewClient`) both talk to TradingView's **screener/scan** endpoint:

```
POST https://scanner.tradingview.com/global/scan?label-product=screener-stock
POST https://scanner.tradingview.com/america/scan?label-product=screener-stock
Body: {
  "columns": [...field names...],
  "markets": [...market codes...],
  "range": [0, 100000],
  "ignore_unknown_fields": true,
  "filter"/"filter2": {...},
  "sort": {...}
}
```

This is an unauthenticated, unofficial-but-widely-used endpoint (no login/cookies
required). It returns **one row per symbol with one value per requested column** — a
single snapshot in time. The 3,515-column catalog in
`savedData/trading_view_stock_fields.csv` (already taxonomized in
`savedData/trading_view_field_taxonomy/`) confirms the field-naming pattern:

- `<metric>` — current/spot value
- `<metric>_fq` — current fiscal **quarter** value
- `<metric>_fy` — current fiscal **year** value
- `<metric>_ttm` — trailing twelve months
- `<metric>_yoy_growth_*`, `<metric>_qoq_growth_*`, `<metric>_5y_growth_fy` — a
  **growth rate** computed by TradingView server-side, not the underlying historical
  series itself

There is **no** `<metric>_fy_1`, `<metric>_fq[-4]`, or similar offset-indexed field in
the catalog. This means the scanner API is architecturally a snapshot API: it cannot
return "revenue for FY2021, FY2022, FY2023..." for a symbol — only "revenue right now"
(current FQ/FY/TTM) plus a handful of pre-computed growth ratios. This matches what the
existing `export_all_tradingview_fields_duckdb` run already captures daily — it is
already at the maximum historical depth obtainable from this endpoint.

## 2. Live investigation of the TradingView "Financials" symbol pages

I loaded both URLs the user referenced in a real browser (Playwright) and captured
network traffic, WebSocket frames, and the DOM/accessibility tree to see how the
multi-year/quarter table is actually populated.

**Pages checked:**
- `https://www.tradingview.com/symbols/NASDAQ-CIFR/financials-overview/`
- `https://www.tradingview.com/symbols/NASDAQ-CIFR/financials-income-statement/`

### 2.1 Network requests observed
No plain REST/JSON call returns the historical statement table. Requests seen (after
filtering analytics/ads/fonts):
- `GET /financial/fundamentals_config_v2/` — a config/labels payload (field
  definitions/labels for the UI), not statement values.
- `GET /api/v1/offers/` — pricing/offer info for the upsell banner.
- `GET news-mediator.tradingview.com/.../documents?...symbol=NASDAQ:CIFR` — news feed,
  unrelated.
- A WebSocket connection: `wss://data.tradingview.com/socket.io/websocket?...auth=sessionid`

### 2.2 WebSocket protocol (quote stream, not statements)
The socket handshake sends `set_auth_token` (`unauthorized_user_token` for anonymous
sessions), then `quote_create_session`, `quote_add_symbols`, `quote_fast_symbols`. The
server pushes back `"qsd"` (quote-symbol-data) messages containing a **single current
snapshot** per symbol — the same shape as the scanner API (fields like
`dps_common_stock_prim_issue_fy`, `financials_availability`, `last_annual_eps`, etc.).
This is TradingView's real-time quote/fundamentals push channel used site-wide (charts,
watchlists, symbol pages) — it is not a historical-statement API either.

### 2.3 The multi-year/quarter table is paywalled
On the `financials-income-statement` page, the "Statements" table (Income
statement / Balance sheet / Cash flow tabs, Annual/Quarterly toggle, columns
2020–2025 + TTM) renders in the DOM, but **every metric row is wrapped in an
"Upgrade to get full access to financial data" control**, confirmed directly from the
accessibility tree of the live page on 2026-08-02:

```
- link "Total revenue" [...]
  - button "Upgrade to get full access to financial data"
  - ...year cells...
- link "Cost of goods sold" [...]
  - button "Upgrade to get full access to financial data"
  - ...year cells...
```

This is a soft paywall (values are present in the client bundle/DOM but the row is
gated/blurred behind an upsell for non-subscribed sessions) rather than a data gap. The
practical implication is the same either way: **TradingView deliberately restricts full
access to this historical statement view to paid plans.** No public/free endpoint
serves this table's numbers as clean JSON.

The `financials-overview` page's "Key facts" (market cap, employees, basic EPS TTM,
etc.) is **not** gated — those are single current-period values, consistent with what
the scanner API/quote stream already deliver for free.

### 2.4 Why we should not try to scrape the gated table
- It requires either bypassing an access-control/paywall UI or authenticating with a
  paid TradingView account and scripting extraction of premium content.
- TradingView's Terms of Use prohibit automated scraping/redistribution of their data,
  and that applies to paid content even more strictly than to the already-used,
  free/unauthenticated screener endpoint.
- Building this would mean helping bypass a deliberately placed access control, which
  this assistant will not do — and it is a real legal/ToS risk for the user's project
  regardless of who implements it.
- **Recommendation: do not build a scraper against the Statements pages.** Use section
  3 instead.

## 3. Recommended path: this repo already has the right historical data source

This project is an **EDGAR data management pipeline** — it already ingests full
historical, standardized (XBRL/US-GAAP) financial statement facts from SEC filings
(10-K/10-Q) into MySQL:

- **Table:** `edgar_financial_data_concepts`
  (schema in `sql_connections_space/financeManagerV1.session.sql`):
  `Cik, Ticker, FilingType, FiscalPeriod, FiscalYear, Concept, Value, ValueString,
  Unit, DataType, Adsh, PeriodEnd, Ddate, Segment, Qtrs, BatchTag`
  — i.e. one row per (ticker, concept, fiscal period), which is exactly a historical
  time series by fiscal year **and** fiscal quarter, for every standardized concept
  TradingView shows on its Income Statement / Balance Sheet / Cash Flow tabs.
- **Ticker/CIK mapping:** `sec_cik_tickers_mapping` (Cik, Ticker, Title,
  SecondaryTickers) via `src/data_loaders/sec_cik_ticker_mapping.py`
  (SEC's own `company_tickers.json`).
- **Curated concept list already mirrors TradingView's Statements tabs:**
  `documentation/important_financial_tags.json` groups tags into
  `core_income_statement_tags`, `balance_sheet_tags`, `cash_flow_statement_tags`,
  `other_relevant_tags` — e.g. `Revenues`/`RevenueFromContractWithCustomerExcludingAssessedTax`,
  `CostOfRevenue`, `GrossProfit`, `OperatingIncomeLoss`, `NetIncomeLoss`,
  `EarningsPerShareBasic/Diluted`, `Assets`, `Liabilities`, `StockholdersEquity`,
  `NetCashProvidedByUsedInOperatingActivities`, etc. — the same line items as
  TradingView's `total-revenue`, `cost-of-goods`, `gross-profit`, `oper-income`,
  `pretax-income` rows seen on the live page.
- **Query helpers:** `src/db/edgar_financial_data_concepts_operations.py` (e.g.
  `get_edgar_financial_data_concepts_adshs_sorted_periodend`) and an existing
  agent (`financeFactsAnalysisIt1`) built specifically to analyze this table
  (trend series, YoY, peer comparisons).
- **Existing TradingView-side symbol table for the join key:**
  `trading_view_company_data_map` (symbol, name, exchange, market, market_cap_basic,
  perf_w/1m/y, fundamental_currency_code, `internal_id`) populated by
  `load_tradingview_company_data` from `ApiTradingViewClient.scan_world_market_all_priceperf_metrics`.

**Conclusion:** the "backward/historical" half of the user's goal (income statement,
balance sheet, cash flow, by year or quarter) is already solved architecturally in this
repo via SEC EDGAR — it just is not yet joined to the TradingView all-fields daily
snapshot. The remaining work is a join/enrichment layer, not a new scraper.

## 4. Proposed architecture for the follow-up implementation agent

Goal: for any symbol, produce **current TradingView stats/technicals** (already
exported daily by `export_all_tradingview_fields_duckdb`) alongside a **trailing N
fiscal years / N fiscal quarters** history of the standardized statement line items
from `edgar_financial_data_concepts`, joined by ticker.

### Phase 1 — Ticker/CIK reconciliation audit
- Compare `trading_view_company_data_map.symbol` (format like `NASDAQ:CIFR` or plain
  `CIFR` depending on loader — verify) against `sec_cik_tickers_mapping.Ticker`.
- Handle multi-listing/secondary-ticker edge cases via `SecondaryTickers` JSON column.
- Flag TradingView symbols with no EDGAR match (non-US filers, ADRs, funds, crypto,
  etc.) — these simply will not have EDGAR history; decide whether they're
  in-scope now or deferred.
- Output: a reconciliation table/report of match rate (%), with reasons for misses.

### Phase 2 — Concept-to-"statement line item" mapping table
- Formalize `documentation/important_financial_tags.json` into a small mapping
  module (e.g. `src/constants/edgar_statement_line_items.py`) grouping US-GAAP
  concepts into the same buckets TradingView shows: Income Statement, Balance Sheet,
  Cash Flow — with a friendly display name per concept (mirrors TradingView's
  `total-revenue`, `gross-profit`, `oper-income`, `pretax-income`, etc.).
- Where multiple XBRL tags map to the same conceptual line (e.g. `Revenues` vs.
  `RevenueFromContractWithCustomerExcludingAssessedTax`), define a fallback/priority
  order per line item (reuse patterns already established in
  `src/data_loaders/data_extractors/` / `arelle_data_loaders/` if such fallback
  logic already exists there — check before writing new logic).

### Phase 3 — Historical statement export function (mirrors existing conventions)
Build a new function analogous in shape to `export_all_tradingview_fields_duckdb`,
e.g. `export_edgar_statement_history_duckdb(tickers, periods=("FY","FQ"), ...)`:
- Pull rows from `edgar_financial_data_concepts` for the mapped concepts, per ticker,
  pivoted **long→wide** by `FiscalYear`/`FiscalPeriod` (columns like
  `total_revenue_fy2021`, `total_revenue_fy2022`, ... `total_revenue_fq_2025q3`, or a
  tidy long format `(ticker, concept, fiscal_year, fiscal_period, value)` — tidy long
  is usually easier to keep correct and is simpler to join/aggregate later; prefer it
  unless a wide table is specifically needed for a report).
- Persist using the **same DuckDB/run/parquet conventions** already used elsewhere in
  this repo (`TradingViewAllFieldsDuckDBStore` pattern: `register_run`,
  provenance metadata, parquet export) so it fits the existing tooling and doesn't
  fragment storage conventions.
- Store under a new dated folder alongside `trading_view_all_fields_data/`, e.g.
  `logs/tradingview_analysis/edgar_statement_history_data/<dd_mm_yyyy>/`.

### Phase 4 — Join/enrichment layer ("current stats + historical context")
- Build a view/query (or export) that, per ticker and as-of date, combines:
  - Latest row from `all_fields_rows` (current TradingView stats/technicals/overview
    fields — what the user called "current stats").
  - Trailing 5 fiscal years + trailing 8 fiscal quarters of the mapped EDGAR concepts
    (what the user called "context for prev years and/or quarters").
- This directly satisfies the user's stated end goal: "all fields data which are
  mostly current stats" + "overview/technicals... with some context for prev years
  and/or quarters" — without touching TradingView's gated pages at all.

### Phase 5 — Coverage gap handling (optional, only if needed later)
- For symbols with no EDGAR history (non-US listings) and where historical statement
  depth is truly required, evaluate a **properly licensed** data provider instead of
  scraping TradingView (e.g. a vendor with a documented API and terms that permit the
  intended use — Financial Modeling Prep, Alpha Vantage, Polygon.io, etc. are common
  examples, but licensing terms must be checked against the intended use — internal
  research vs. redistribution — before adopting any of them). This is explicitly
  out of scope unless the user asks for it; EDGAR alone covers the large majority of
  the current universe (US-listed operating companies), which is what the existing
  move-prediction/all-fields pipeline already targets.

## 5. What NOT to build
- No scraper/automation against
  `financials-overview` / `financials-income-statement` / `financials-balance-sheet` /
  `financials-cash-flow` pages, with or without a paid login — the multi-period table
  is paywalled by TradingView's own UI and scraping it (logged in or not) risks
  violating their Terms of Use.
- No attempt to widen the `scanner.tradingview.com` payload with invented
  offset/history field names — the field catalog and taxonomy in this repo confirm
  no such fields exist; `ignore_unknown_fields: true` will just silently drop them,
  which could produce misleadingly "successful" but empty results if someone tries.

## 6. Evidence summary (for traceability)
- Confirmed via live Playwright network capture on 2026-08-02 against
  `NASDAQ:CIFR` financials pages:
  - No REST/XHR endpoint returns statement history JSON.
  - `wss://data.tradingview.com/socket.io/websocket` quote stream returns only
    current-period fields (same shape as scanner API), not a series.
  - `GET /financial/fundamentals_config_v2/` is a labels/config payload, not values.
  - Accessibility-tree snapshot shows "Upgrade to get full access to financial data"
    gating every line item in the Statements table for an anonymous session.
- Confirmed via repo inspection:
  - `savedData/trading_view_stock_fields.csv` +
    `savedData/trading_view_field_taxonomy/` — exhaustive 3,515-field catalog with no
    historical-offset fields, only current `_fq`/`_fy`/`_ttm` + growth-rate variants.
  - `edgar_financial_data_concepts` (MySQL) already stores per-ticker, per-concept,
    per-fiscal-period historical values sourced from SEC filings.
  - `documentation/important_financial_tags.json` already curates the exact
    income-statement/balance-sheet/cash-flow concept groups needed.
  - `trading_view_company_data_map` + `sec_cik_tickers_mapping` provide the join keys
    needed to connect TradingView symbols to EDGAR CIK/Ticker.
