# TradingView Scanner Field Availability, Run Fallbacks, and Authentication

## Purpose

This is the operating reference for the stock scanner export in
`src/data_analysis_scripts/trading_view_export_all_tdfields.py`. It records:

- what the unauthenticated scanner currently returns;
- why a column can be null in `all_fields_rows`;
- how full-baseline and focused intraday runs differ;
- which downstream readers combine those runs;
- how to evaluate a future paid-session transition without storing credentials.

The observations below were verified on 20 October 2026. TradingView is an
external, undocumented surface, so rerun the probes after payload, catalog, or
account changes.

## Endpoint and Current Access Mode

The exporter posts JSON to:

```text
https://scanner.tradingview.com/global/scan?label-product=screener-stock
```

The current exporter sends `User-Agent`, `Accept`, and `Content-Type` headers.
It does not attach a TradingView login cookie or another account credential.
The 20 October VICR probe returned:

```text
symbol       = NASDAQ:VICR
update_mode  = delayed_streaming_900
```

`delayed_streaming_900` is the observable evidence that this request receives
the delayed data mode. Fundamentals are nevertheless available on the same
unauthenticated request.

## Full and Focused Runs

Both export modes append to the same daily DuckDB table. Schema presence
therefore does not prove that a field was requested by a particular run.

| Run type | Catalog | Requested scanner columns on 20 Oct | Purpose |
|---|---|---:|---|
| Full baseline | `savedData/trading_view_stock_fields.csv` | 3,515 | Daily field-discovery and fallback baseline |
| Focused intraday | `savedData/trading_view_stock_fields_focused.csv` | 242 | Fresh critical/scoring fields in one request |

The focused CSV contains 243 rows because `symbol` is a local row key. The
exporter correctly removes it before building the scanner request.

On 20 October the full run was
`tradingview_all_fields_20261020_0923_utc_1949427b` with 49,851 rows. The
focused run was `focused_intraday_20261020_1626_utc_b7b47b70` with 49,791
rows.

### Why focused rows contain many null columns

`all_fields_rows` evolves by adding columns and all runs share the resulting
wide schema. A focused insert names only its 242 requested columns. Every
other table column is null for that focused row even when the morning full run
contains a valid value.

Use `run_metadata.api_request_columns_json` as the source of truth for whether
a run requested a column. Do not infer request coverage from `DESCRIBE
all_fields_rows`.

## Four Distinct Field States

Treat these states differently:

1. **Populated and requested**: use the latest value.
2. **Requested but null**: provider/source coverage is missing for this
   symbol and run. Do not silently call this an omitted focused field.
3. **Present in table schema but not requested**: structural null caused by a
   focused run. A same-day full baseline is an eligible fallback.
4. **Not in the maintained full catalog**: unsupported/unverified field name.
   A scanner request with `ignore_unknown_fields: true` can still succeed and
   return null, so HTTP success is not proof that the field exists.

This distinction is why unconditional `COALESCE(latest.value,
full.value)` is convenient but weaker than checking request provenance: it
can replace a genuine latest-run null with an older morning value.

## Verified VICR Income and Margin Coverage

An unauthenticated one-symbol request on 20 October returned:

| Field | Value | Focused catalog |
|---|---:|---|
| `net_income` | 118,556,000 | Yes |
| `net_income_ttm` | 145,261,000 | Yes |
| `net_income_fy` | 118,556,000 | No |
| `net_income_fq` | 49,772,000 | No |
| `net_margin` | 30.6453 | No |
| `net_margin_ttm` | 30.6453 | Yes |
| `net_margin_fy` | 29.0792 | No |
| `net_income_cagr_5y` | 45.9371 | Yes |
| `net_income_fy_h` | 20-value annual history array | No |
| `net_income_fq_h` | 32-value quarterly history array in the full run | No |
| `net_income_ttm_h` | 8-value TTM history array in the full run | No |
| `net_income_fy_1` | null; not in the maintained full catalog | No |

The scanner exposes current FY/FQ/TTM values and several `_h` history arrays.
It does not expose catalogued scalar names such as `net_income_fy_1`.
Consumers that need individual historical periods must parse and label the
`_h` arrays only after verifying their ordering, or use a statement-specific
data source. Do not assume a paid scanner session creates new prior-year
scalar columns.

### Population counts on 20 October

These are nonblank rows, not total rows:

| Field | Full run | Focused run | Interpretation |
|---|---:|---:|---|
| `net_income` | 45,341 | 45,298 | Requested in both |
| `net_income_ttm` | 42,673 | 42,633 | Requested in both |
| `net_income_fy` | 45,341 | 0 | Omitted from focused |
| `net_income_fq` | 35,273 | 0 | Omitted from focused |
| `net_income_fy_h` | 47,127 | 0 | Omitted from focused |
| `net_income_fq_h` | 38,003 | 0 | Omitted from focused |
| `net_income_ttm_h` | 42,680 | 0 | Omitted from focused |
| `net_margin` | 39,445 | 0 | Omitted from focused |
| `net_margin_ttm` | 39,445 | 39,407 | Requested in both |
| `net_margin_fy` | 41,617 | 0 | Omitted from focused |
| `update_mode` | 49,850 | 0 | Omitted from focused |

VICR follows this exact pattern: bare `net_margin` is valid in the full run,
while the focused row contains the fresh `net_margin_ttm`.

## Historical Holes Are Not Always Focused-Run Omissions

The 24 July full run requested all 3,515 fields. For VICR, `net_margin` was
present while EV/EBITDA, P/B, P/FCF, FCF margin, Altman Z, and Piotroski were
all null. Because those columns were requested, this is a provider/source
coverage hole rather than a focused-catalog omission.

The 24 April backfill shows the same value pattern, but its backfill metadata
does not retain a request catalog. Classify that date as a source-data hole
with weaker request-provenance evidence.

Do not forward-fill these holes automatically across days. Same-day full
fallback addresses request omission; cross-day imputation is a separate
analysis policy and must be labelled stale.

## Downstream Flow Audit

| Flow | Current run behavior | Full baseline behavior | Status / required check |
|---|---|---|---|
| All-fields exporter | Appends every run and stores `api_request_columns_json` | Full is recognized at 1,000+ requested columns | Correct provenance is available |
| Symbol intelligence current snapshot | Uses latest populous run | Metadata-aware fallback only for columns omitted from latest request | Corrected; report JSON lists fallback fields |
| Symbol intelligence weekly history | Newest file per ISO week, latest populous run | Same-day metadata-aware fallback for omitted history columns | Corrected; log reports fallback-week count |
| Symbol intelligence peer percentiles | Latest populous run | Same-day full values for omitted metrics; requested nulls stay null | Corrected |
| Financial projections | Freshest run joined to latest full run | Per-column `COALESCE` | Fallback exists; recheck requested-null masking if null transitions become important |
| Edge-research dataset builder | Freshest run joined to latest full run | Per-column `COALESCE` | Fallback exists; same requested-null caveat |
| Market timing current snapshot | Latest same-day scan joined to full run | Per-column `COALESCE` for latest snapshot only | Fallback exists; older intraday rows intentionally remain as-is |
| Direct SQL / generic scan tools | Whatever `run_id` the query selects | No implicit fallback | Query both runs or use request metadata explicitly |

All current fallbacks are same-database/same-day. If a day has no qualifying
full run, the resolver makes the full ID equal the latest ID and fallback is a
no-op. Do not silently reach into a previous day.

## SQL Checks

### Determine whether a run requested a field

```sql
SELECT
    run_id,
    created_at_utc,
    json_array_length(api_request_columns_json) AS requested_column_count,
    list_contains(
        from_json(api_request_columns_json, '["VARCHAR"]'),
        'net_margin'
    ) AS requested_net_margin,
    list_contains(
        from_json(api_request_columns_json, '["VARCHAR"]'),
        'net_margin_ttm'
    ) AS requested_net_margin_ttm
FROM run_metadata
ORDER BY created_at_utc DESC;
```

### Compare VICR across all same-day runs

```sql
SELECT
    r.run_id,
    m.created_at_utc,
    json_array_length(m.api_request_columns_json) AS requested_column_count,
    r.net_income,
    r.net_income_ttm,
    r.net_income_fy,
    r.net_income_fq,
    r.net_margin,
    r.net_margin_ttm,
    r.net_margin_fy,
    r.update_mode
FROM all_fields_rows AS r
JOIN run_metadata AS m USING (run_id)
WHERE r.symbol = 'NASDAQ:VICR'
ORDER BY m.created_at_utc DESC;
```

### Find structural nulls in the latest focused run

```sql
WITH runs AS (
    SELECT
        run_id,
        api_request_columns_json,
        ROW_NUMBER() OVER (ORDER BY created_at_utc DESC, run_id DESC) AS recency
    FROM run_metadata
)
SELECT
    r.symbol,
    r.net_margin,
    r.net_margin_ttm,
    list_contains(
        from_json(m.api_request_columns_json, '["VARCHAR"]'),
        'net_margin'
    ) AS requested_net_margin
FROM all_fields_rows AS r
JOIN run_metadata AS m USING (run_id)
WHERE r.run_id = (SELECT run_id FROM runs WHERE recency = 1)
  AND r.symbol = 'NASDAQ:VICR';
```

## Paid-Session Transition Plan

### What a paid session may change

The acceptance target is observable response behavior, not the account label:

- `update_mode` should change from `delayed_streaming_900` to `streaming` for
  exchanges covered by the subscription;
- price/tape timestamps should satisfy the expected latency;
- row count and critical-field fill rates must not regress.

Do not assume payment expands the scanner field catalog. Re-run the same
one-symbol probes for unsupported names and compare response shapes.

### Credential handling

Browser sessions commonly authenticate scanner requests through cookies such
as `sessionid` and, when present, `sessionid_sign`. These are secrets.

- Never commit cookie values, browser profiles, exported cookie files, or
  authenticated response headers.
- Supply credentials at runtime from a secret manager or local environment
  variables excluded from version control.
- Redact cookies from logs, exception text, request metadata, and test
  fixtures.
- Use a dedicated `requests.Session`; do not append cookies to the JSON body.
- Confirm that automated access complies with the account terms before use.

The current export functions create their own unauthenticated
`requests.Session`. A paid implementation should therefore add an explicit
session/authentication injection point rather than adding global headers or
hard-coded cookies.

### Migration sequence

1. Keep unauthenticated mode as the default.
2. Add an opt-in session factory or cookie provider outside the field
   catalogs.
3. Load secrets only at runtime and set them on the `requests.Session`.
4. Run a one-symbol canary for `NASDAQ:VICR`.
5. Assert HTTP success, `totalCount = 1`, expected symbol identity, and
   `update_mode`.
6. Compare the exact same columns under anonymous and authenticated sessions.
7. Run one focused export and validate row count plus critical fill rates.
8. Run one full export and compare catalog-level fill rates.
9. Verify that `api_request_columns_json` is identical between access modes;
   authentication should not silently change the requested field set.
10. Enable authenticated mode only after logs and generated metadata have
    been checked for secret leakage.

### Recheck triggers

Repeat the canary and catalog comparison when:

- the TradingView plan or exchange entitlements change;
- the endpoint or payload changes;
- the full or focused CSV catalog changes;
- `update_mode` changes unexpectedly;
- response array length no longer matches requested column count;
- row count or critical-field fill rates move materially;
- TradingView begins returning authorization or rate-limit errors.

