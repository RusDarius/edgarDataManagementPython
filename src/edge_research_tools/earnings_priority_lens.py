"""Earnings-priority augmentation lens.

Builds a standalone view of every symbol with an upcoming earnings release
within a lookahead window (default 30 days), scoped directly from the raw
daily all-fields database rather than the (already filtered) quantitative
scan universe.

Design intent
-------------
- Coverage of this lens is *not* gated by whether a symbol passed the
  volatility/liquidity screen, the highlights shortlist thresholds, or any
  other quantitative lens. Any symbol with an earnings date inside the
  lookahead window is included, regardless of scan-universe membership.
- Fields already computed elsewhere (``unified_edge_highlight_rank``,
  ``big_mover_rank``, ``upside_prediction_score``, safety scores, historical
  validation, historical-edge progression, ...) are copied through verbatim
  when available. This lens never recomputes or renumbers those values
  relative to the (smaller) earnings-scoped subset, so cross-symbol relative
  comparisons made elsewhere in the suite remain valid here.
- A dedicated ``earnings_priority_rank`` column orders *this* view (soonest
  earnings first, richer data coverage as a tiebreaker). It is intentionally
  named apart from every inherited rank field so it is never confused with a
  re-ranking of upstream quantitative merit.
- Output is written to its own CSV + DuckDB database, separate from the
  unified highlights store, since its population and filtering semantics are
  fundamentally different (coverage lens vs. quantitative shortlist).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .region_filters import (
    build_market_filter_sql,
    build_region_filter_sql,
    expand_us_aliases,
    normalize_market_list,
    normalize_string_list,
)

DEFAULT_EARNINGS_LOOKAHEAD_DAYS = 30
DEFAULT_EARNINGS_DATE_FIELD = "earnings_release_next_trading_date_fq"

# (raw all_fields_rows column, output alias, SQL cast type)
EARNINGS_BASE_FIELD_SPECS: tuple[tuple[str, str, str], ...] = (
    ("name", "company_name", "VARCHAR"),
    ("market", "market", "VARCHAR"),
    ("exchange", "exchange", "VARCHAR"),
    ("country", "country", "VARCHAR"),
    ("sector", "sector", "VARCHAR"),
    ("industry", "industry", "VARCHAR"),
    ("close", "close", "DOUBLE"),
    ("Perf.5D", "perf_5d", "DOUBLE"),
    ("Perf.1M", "perf_1m", "DOUBLE"),
    ("Perf.3M", "perf_3m", "DOUBLE"),
    ("ATRP", "atrp", "DOUBLE"),
    ("relative_volume_10d_calc", "relative_volume_10d_calc", "DOUBLE"),
    ("Recommend.All", "recommend_all", "DOUBLE"),
    ("SMA20", "sma20", "DOUBLE"),
    ("SMA50", "sma50", "DOUBLE"),
    ("market_cap_basic", "market_cap_basic", "DOUBLE"),
    ("volume", "volume", "DOUBLE"),
)

EARNINGS_WINDOW_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0, 7, "0_7d"),
    (8, 14, "8_14d"),
    (15, 21, "15_21d"),
    (22, 30, "22_30d"),
)

# Fields backfilled from the broader safety-scored universe (covers every
# symbol in the region/market/date scope, independent of the highlights
# shortlist). Only used when a symbol is missing from the unified rows.
SAFETY_BACKFILL_FIELDS: tuple[str, ...] = (
    "balance_sheet_safety_score",
    "cash_generation_value_score",
    "safety_companion_score",
    "indicator_pass_count",
)

# Fields backfilled from the point-in-time screen output (broader than the
# final highlights shortlist since it has no historical/lane gating, but
# still bounded by ``screen_top_n``). Only used when a symbol is missing
# from the unified rows. Mapping is (source column in screen CSV, output
# column name in this lens).
SCREEN_BACKFILL_FIELDS: tuple[tuple[str, str], ...] = (
    ("screen_rank", "screen_rank"),
    ("composite_score", "composite_score"),
    ("adrp_directional_universe_percentile", "adrp_pct_today"),
    ("relative_volume_directional_universe_percentile", "relvol_pct_today"),
    ("hist_occurrence_count_in_setup", "hist_occurrence_count_in_setup"),
    ("adrp_raw", "adrp_raw"),
    ("atrp_raw", "atrp_raw"),
    ("relvol_raw", "relvol_raw"),
)

BASE_EARNINGS_COLUMNS: tuple[str, ...] = (
    "earnings_priority_rank",
    "data_coverage_tier",
    "in_unified_pipeline_flag",
    "in_screen_universe_flag",
    "in_safety_scored_universe_flag",
    "symbol",
    "bare_ticker",
    "company_name",
    "market",
    "exchange",
    "country",
    "sector",
    "industry",
    "earnings_date",
    "earnings_days_until",
    "earnings_window_bucket",
    "close",
    "perf_5d",
    "perf_1m",
    "perf_3m",
    "atrp",
    "relative_volume_10d_calc",
    "recommend_all",
    "sma20",
    "sma50",
    "market_cap_basic",
    "volume",
)

_TIER_SORT_PRIORITY: dict[str, int] = {
    "unified_pipeline": 0,
    "partial_scan_coverage": 1,
    "earnings_only": 2,
}

_BEST_RANK_FIELD_PRIORITY: tuple[str, ...] = (
    "unified_edge_highlight_rank",
    "big_mover_rank",
    "confidence_rank",
    "upside_prediction_rank",
    "screen_rank",
    "safety_rank_global",
)


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _qi(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _table_exists(connection: Any, *, database_name: str, table_name: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = ? AND table_name = ?",
        [database_name, table_name],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _table_columns(connection: Any, *, database_name: str, table_name: str) -> set[str]:
    rows = connection.execute(
        "SELECT column_name FROM duckdb_columns() WHERE database_name = ? AND table_name = ?",
        [database_name, table_name],
    ).fetchall()
    return {str(row[0]) for row in rows}


def _fetch_dict_rows(connection: Any, query: str) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _read_csv_rows(path: str | Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    csv_path = Path(path)
    if not csv_path.exists() or csv_path.is_dir():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _index_rows_by_symbol(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol and symbol not in indexed:
            indexed[symbol] = row
    return indexed


def _optional_select(
    *,
    field_name: str,
    alias: str,
    sql_type: str,
    available_columns: set[str],
) -> str:
    if field_name not in available_columns:
        return f"CAST(NULL AS {sql_type}) AS {_qi(alias)}"
    if sql_type == "VARCHAR":
        return f"CAST(r.{_qi(field_name)} AS VARCHAR) AS {_qi(alias)}"
    return f"TRY_CAST(r.{_qi(field_name)} AS {sql_type}) AS {_qi(alias)}"


def classify_earnings_window_bucket(days_until: int | None) -> str:
    if days_until is None:
        return "unknown"
    for low, high, label in EARNINGS_WINDOW_BUCKETS:
        if low <= days_until <= high:
            return label
    return "beyond_30d" if days_until > EARNINGS_WINDOW_BUCKETS[-1][1] else "past_due"


def load_earnings_window_candidates(
    *,
    source_database_path: str | Path,
    as_of_date: str,
    lookahead_days: int = DEFAULT_EARNINGS_LOOKAHEAD_DAYS,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    min_market_cap_usd: float | None = None,
    earnings_date_field: str = DEFAULT_EARNINGS_DATE_FIELD,
    duckdb_threads: int = 16,
) -> list[dict[str, Any]]:
    """Scope the raw daily all-fields universe to symbols with earnings due soon.

    Reads directly from ``all_fields_rows`` in the daily all-fields database
    (the same source used by ``edge_trade_plan`` and ``safety_highlights``),
    not from any already-filtered quantitative scan output. This is what
    lets earnings-priority coverage include symbols that never made the
    highlights shortlist or any other lens.
    """
    db_path = Path(source_database_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"Source daily all-fields database not found: {db_path.as_posix()}"
        )

    duckdb = _import_duckdb()
    conn = duckdb.connect(database=":memory:")
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        conn.execute(f"ATTACH {_q(db_path.as_posix())} AS src_daily (READ_ONLY)")
        available_columns = _table_columns(
            conn, database_name="src_daily", table_name="all_fields_rows"
        )
        if not available_columns:
            raise ValueError(
                "Table src_daily.all_fields_rows was not found or has no columns "
                f"in {db_path.as_posix()}."
            )
        if earnings_date_field not in available_columns:
            raise ValueError(
                f"Earnings date field '{earnings_date_field}' was not found in "
                f"all_fields_rows ({db_path.as_posix()}). Pass a different "
                "earnings_date_field."
            )

        resolved_markets = normalize_market_list(markets)
        resolved_countries = expand_us_aliases(
            normalize_string_list(countries), bool(us_only)
        )
        resolved_exchanges = normalize_string_list(exchanges)

        region_filter_sql = build_region_filter_sql(
            country_expr="r.country",
            exchange_expr="r.exchange",
            countries=resolved_countries,
            exchanges=resolved_exchanges,
        )
        market_filter_sql = (
            build_market_filter_sql(market_expr="r.market", markets=resolved_markets)
            if "market" in available_columns
            else "1 = 1"
        )
        market_cap_filter_sql = (
            f"TRY_CAST(r.market_cap_basic AS DOUBLE) >= {float(min_market_cap_usd)}"
            if min_market_cap_usd is not None
            and "market_cap_basic" in available_columns
            else "1 = 1"
        )

        base_select_sql = ",\n                    ".join(
            _optional_select(
                field_name=field_name,
                alias=alias,
                sql_type=sql_type,
                available_columns=available_columns,
            )
            for field_name, alias, sql_type in EARNINGS_BASE_FIELD_SPECS
        )

        has_run_metadata = _table_exists(
            conn, database_name="src_daily", table_name="run_metadata"
        )
        if has_run_metadata:
            latest_run_rank_sql = (
                "ROW_NUMBER() OVER ("
                "PARTITION BY CAST(r.symbol AS VARCHAR) "
                "ORDER BY m.created_at_utc DESC NULLS LAST, r.run_id DESC, r.row_number DESC"
                ")"
            )
            join_sql = "LEFT JOIN src_daily.run_metadata AS m USING (run_id)"
        else:
            latest_run_rank_sql = (
                "ROW_NUMBER() OVER ("
                "PARTITION BY CAST(r.symbol AS VARCHAR) "
                "ORDER BY r.run_id DESC, r.row_number DESC"
                ")"
            )
            join_sql = ""

        earnings_date_expr = f"TRY_CAST(r.{_qi(earnings_date_field)} AS DATE)"
        as_of_date_expr = f"CAST({_q(as_of_date)} AS DATE)"
        days_until_expr = f"DATE_DIFF('day', {as_of_date_expr}, {earnings_date_expr})"

        query = f"""
            SELECT * EXCLUDE (row_num)
            FROM (
                SELECT
                    CAST(r.symbol AS VARCHAR) AS symbol,
                    COALESCE(
                        NULLIF(split_part(CAST(r.symbol AS VARCHAR), ':', 2), ''),
                        CAST(r.symbol AS VARCHAR)
                    ) AS bare_ticker,
                    {base_select_sql},
                    {earnings_date_expr} AS earnings_date,
                    {days_until_expr} AS earnings_days_until,
                    {latest_run_rank_sql} AS row_num
                FROM src_daily.all_fields_rows AS r
                {join_sql}
                WHERE {earnings_date_expr} IS NOT NULL
                  AND {days_until_expr} BETWEEN 0 AND {int(lookahead_days)}
                  AND {region_filter_sql}
                  AND {market_filter_sql}
                  AND {market_cap_filter_sql}
            )
            WHERE row_num = 1
            ORDER BY earnings_days_until, symbol
        """
        rows = _fetch_dict_rows(conn, query)
    finally:
        conn.close()

    for row in rows:
        days_until = row.get("earnings_days_until")
        row["earnings_window_bucket"] = classify_earnings_window_bucket(
            int(days_until) if days_until is not None else None
        )
        earnings_date_value = row.get("earnings_date")
        if earnings_date_value is not None:
            row["earnings_date"] = str(earnings_date_value)
    return rows


def merge_earnings_priority_row(
    earnings_row: Mapping[str, Any],
    *,
    unified_row: Mapping[str, Any] | None,
    screen_row: Mapping[str, Any] | None,
    safety_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attach whatever already-computed context exists for one earnings candidate.

    Precedence (richest first): unified highlights row overlays everything;
    screen and safety rows only backfill fields the unified row does not
    already carry. No rank or score field copied from an existing source is
    recomputed here -- values are passed through unchanged.
    """
    row: dict[str, Any] = dict(earnings_row)

    in_safety_scored_universe = safety_row is not None
    in_screen_universe = screen_row is not None
    in_unified_pipeline = unified_row is not None

    if safety_row is not None:
        for key in SAFETY_BACKFILL_FIELDS:
            if key in safety_row and row.get(key) in (None, ""):
                row[key] = safety_row.get(key, "")
        if "safety_rank" in safety_row and row.get("safety_rank_global") in (None, ""):
            row["safety_rank_global"] = safety_row.get("safety_rank", "")

    if screen_row is not None:
        for source_key, target_key in SCREEN_BACKFILL_FIELDS:
            if source_key in screen_row and row.get(target_key) in (None, ""):
                row[target_key] = screen_row.get(source_key, "")

    if unified_row is not None:
        # Unified is the canonical, richest source already produced by the
        # daily pipeline: overlay every field verbatim (no recomputation).
        row.update(unified_row)
        # Earnings identity/date/market context only exists in earnings_row;
        # restore it in case any key name coincidentally collided above.
        row.update(earnings_row)

    if in_unified_pipeline:
        data_coverage_tier = "unified_pipeline"
    elif in_screen_universe or in_safety_scored_universe:
        data_coverage_tier = "partial_scan_coverage"
    else:
        data_coverage_tier = "earnings_only"

    row["data_coverage_tier"] = data_coverage_tier
    row["in_unified_pipeline_flag"] = int(in_unified_pipeline)
    row["in_screen_universe_flag"] = int(in_screen_universe)
    row["in_safety_scored_universe_flag"] = int(in_safety_scored_universe)
    return row


def _best_existing_rank(row: Mapping[str, Any]) -> int:
    for key in _BEST_RANK_FIELD_PRIORITY:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return 2**31 - 1


def rank_earnings_priority_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Order the earnings-priority view without touching any inherited rank field.

    Sort key: soonest earnings first, then richest data-coverage tier, then
    the best already-computed rank available (used only as a tiebreaker, not
    rewritten), then symbol for determinism. The resulting position is
    stored in a new ``earnings_priority_rank`` column distinct from every
    copied-through rank column.
    """

    def sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        days_until = row.get("earnings_days_until")
        try:
            days_value = int(days_until) if days_until not in (None, "") else 10**6
        except (TypeError, ValueError):
            days_value = 10**6
        tier_value = _TIER_SORT_PRIORITY.get(str(row.get("data_coverage_tier")), 9)
        return (
            days_value,
            tier_value,
            _best_existing_rank(row),
            str(row.get("symbol") or ""),
        )

    ordered = sorted((dict(row) for row in rows), key=sort_key)
    for position, row in enumerate(ordered, start=1):
        row["earnings_priority_rank"] = position
    return ordered


def _union_fieldnames(
    rows: Sequence[Mapping[str, Any]],
    *,
    priority_order: Sequence[str],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for key in priority_order:
        if key not in seen:
            ordered.append(key)
            seen.add(key)
    for row in rows:
        for key in row.keys():
            if key not in seen:
                ordered.append(key)
                seen.add(key)
    return ordered


def _write_normalized_csv_rows(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str],
) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _write_earnings_priority_database(
    database_path: Path,
    *,
    candidates_csv_path: Path,
    manifest_rows: Sequence[tuple[str, str]],
) -> None:
    duckdb = _import_duckdb()
    database_path.unlink(missing_ok=True)
    conn = duckdb.connect(database_path.as_posix())
    try:
        if candidates_csv_path.exists() and candidates_csv_path.stat().st_size > 0:
            conn.execute(f"""
                CREATE TABLE earnings_priority_candidates AS
                SELECT * FROM read_csv_auto({_q(candidates_csv_path.as_posix())}, HEADER=TRUE)
                """)
        else:
            conn.execute("CREATE TABLE earnings_priority_candidates(symbol VARCHAR)")
        conn.execute(
            "CREATE TABLE earnings_priority_run_manifest(key VARCHAR, value VARCHAR)"
        )
        conn.executemany(
            "INSERT INTO earnings_priority_run_manifest VALUES (?, ?)",
            list(manifest_rows),
        )
    finally:
        conn.close()


def _write_earnings_priority_report(
    path: Path,
    *,
    scan_day: str,
    lookahead_days: int,
    candidate_count: int,
    tier_counts: Mapping[str, int],
    bucket_counts: Mapping[str, int],
    source_database_path: Path,
    unified_csv_path: Path,
    screen_ranked_csv_path: Path | None,
    safety_scored_csv_path: Path | None,
    candidates_csv: Path,
    database_path: Path,
) -> None:
    lines = [
        "# Earnings Priority Lens",
        "",
        "## What This Output Does",
        "",
        "Every symbol with an earnings release due within the lookahead window is "
        "included here, regardless of whether it passed the volatility/liquidity "
        "screen, the highlights shortlist thresholds, or any other quantitative "
        "lens. Coverage is scoped directly from the raw daily all-fields database.",
        "",
        "Where a symbol already has computed context from the daily pipeline "
        "(unified highlights, the point-in-time screen, or the safety-scored "
        "universe), those fields are copied through unchanged -- ranks and "
        "scores are never recomputed or renumbered relative to this smaller "
        "earnings-scoped subset. This keeps every inherited rank/score field "
        "comparable to its value everywhere else in the suite.",
        "",
        "## Data Coverage Tiers",
        "",
        "- `unified_pipeline`: symbol is present in the final unified edge "
        "highlights output (all lens fields available: mover rank/score, "
        "confidence, upside prediction, forward valuation, tradeable safety, "
        "historical validation, historical-edge progression).",
        "- `partial_scan_coverage`: symbol did not make the unified highlights "
        "output, but is present in the point-in-time screen and/or the "
        "safety-scored universe (composite/mover context and/or balance-sheet "
        "and cash-generation safety scores available).",
        "- `earnings_only`: symbol is outside every quantitative scan universe "
        "(different market/exchange/market-cap scope, or simply did not clear "
        "any scan threshold). Only raw market/technical context from the daily "
        "all-fields database is available.",
        "",
        "## Ordering",
        "",
        "`earnings_priority_rank` orders this view by soonest earnings date "
        "first, then by data-coverage tier, then by the best already-computed "
        "rank available as a tiebreaker (not rewritten). It is a presentation "
        "order for this view only -- it is not a re-ranking of any upstream "
        "quantitative lens.",
        "",
        f"- scan day: {scan_day}",
        f"- lookahead window: {lookahead_days} days",
        f"- candidate rows: {candidate_count}",
        "",
        "## Data Coverage Tier Counts",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(tier_counts.items()))
    lines.extend(["", "## Earnings Window Bucket Counts", ""])
    lines.extend(f"- {key}: {value}" for key, value in sorted(bucket_counts.items()))
    lines.extend(
        [
            "",
            "## Inputs",
            "",
            f"- source daily all-fields database: `{source_database_path.as_posix()}`",
            f"- unified edge highlights CSV: `{unified_csv_path.as_posix()}`",
            (
                f"- screen ranked CSV: `{screen_ranked_csv_path.as_posix()}`"
                if screen_ranked_csv_path is not None
                else "- screen ranked CSV: (not supplied)"
            ),
            (
                f"- safety-scored universe CSV: `{safety_scored_csv_path.as_posix()}`"
                if safety_scored_csv_path is not None
                else "- safety-scored universe CSV: (not supplied)"
            ),
            "",
            "## Output Files",
            "",
            f"- candidates CSV: `{candidates_csv.as_posix()}`",
            f"- query database: `{database_path.as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_earnings_priority_lens(
    *,
    output_dir: str | Path,
    source_database_path: str | Path,
    unified_csv_path: str | Path,
    scan_day: str,
    screen_ranked_csv_path: str | Path | None = None,
    safety_scored_csv_path: str | Path | None = None,
    lookahead_days: int = DEFAULT_EARNINGS_LOOKAHEAD_DAYS,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    min_market_cap_usd: float | None = None,
    earnings_date_field: str = DEFAULT_EARNINGS_DATE_FIELD,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    """Build the earnings-priority augmentation as its own CSV + DuckDB store.

    This intentionally writes to a separate database from the unified
    highlights store: its population semantics (coverage lens, not a
    quantitative shortlist) differ fundamentally, and keeping it separate
    avoids implying that earnings-only rows passed any scan threshold.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    earnings_rows = load_earnings_window_candidates(
        source_database_path=source_database_path,
        as_of_date=scan_day,
        lookahead_days=lookahead_days,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        min_market_cap_usd=min_market_cap_usd,
        earnings_date_field=earnings_date_field,
        duckdb_threads=duckdb_threads,
    )

    unified_rows = _read_csv_rows(unified_csv_path)
    unified_by_symbol = _index_rows_by_symbol(unified_rows)
    unified_field_order = list(unified_rows[0].keys()) if unified_rows else []

    screen_by_symbol = _index_rows_by_symbol(_read_csv_rows(screen_ranked_csv_path))
    safety_by_symbol = _index_rows_by_symbol(_read_csv_rows(safety_scored_csv_path))

    merged_rows = [
        merge_earnings_priority_row(
            earnings_row,
            unified_row=unified_by_symbol.get(str(earnings_row.get("symbol") or "")),
            screen_row=screen_by_symbol.get(str(earnings_row.get("symbol") or "")),
            safety_row=safety_by_symbol.get(str(earnings_row.get("symbol") or "")),
        )
        for earnings_row in earnings_rows
    ]
    ordered_rows = rank_earnings_priority_rows(merged_rows)

    candidates_csv = output_path / "edge_earnings_priority_candidates.csv"
    database_path = output_path / "edge_earnings_priority.duckdb"
    report_md = output_path / "edge_earnings_priority_report.md"
    manifest_path = output_path / "edge_earnings_priority_manifest.json"

    fieldnames = _union_fieldnames(
        ordered_rows,
        priority_order=[*BASE_EARNINGS_COLUMNS, *unified_field_order],
    )
    _write_normalized_csv_rows(candidates_csv, ordered_rows, fieldnames=fieldnames)

    tier_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}
    for row in ordered_rows:
        tier = str(row.get("data_coverage_tier") or "unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        bucket = str(row.get("earnings_window_bucket") or "unknown")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    resolved_source_database_path = Path(source_database_path)
    resolved_unified_csv_path = Path(unified_csv_path)
    resolved_screen_csv_path = (
        Path(screen_ranked_csv_path) if screen_ranked_csv_path else None
    )
    resolved_safety_csv_path = (
        Path(safety_scored_csv_path) if safety_scored_csv_path else None
    )

    manifest_kv_rows: list[tuple[str, str]] = [
        ("scan_day", str(scan_day)),
        ("lookahead_days", str(int(lookahead_days))),
        ("candidate_count", str(len(ordered_rows))),
        ("source_database_path", resolved_source_database_path.as_posix()),
        ("unified_csv_path", resolved_unified_csv_path.as_posix()),
        (
            "screen_ranked_csv_path",
            resolved_screen_csv_path.as_posix() if resolved_screen_csv_path else "",
        ),
        (
            "safety_scored_csv_path",
            resolved_safety_csv_path.as_posix() if resolved_safety_csv_path else "",
        ),
        *[(f"tier_count:{key}", str(value)) for key, value in tier_counts.items()],
        *[(f"bucket_count:{key}", str(value)) for key, value in bucket_counts.items()],
    ]
    _write_earnings_priority_database(
        database_path,
        candidates_csv_path=candidates_csv,
        manifest_rows=manifest_kv_rows,
    )
    _write_earnings_priority_report(
        report_md,
        scan_day=str(scan_day),
        lookahead_days=int(lookahead_days),
        candidate_count=len(ordered_rows),
        tier_counts=tier_counts,
        bucket_counts=bucket_counts,
        source_database_path=resolved_source_database_path,
        unified_csv_path=resolved_unified_csv_path,
        screen_ranked_csv_path=resolved_screen_csv_path,
        safety_scored_csv_path=resolved_safety_csv_path,
        candidates_csv=candidates_csv,
        database_path=database_path,
    )

    manifest = {
        "command": "earnings-priority-lens",
        "scan_day": str(scan_day),
        "lookahead_days": int(lookahead_days),
        "earnings_date_field": str(earnings_date_field),
        "markets": list(markets or ()),
        "countries": list(countries or ()),
        "exchanges": list(exchanges or ()),
        "us_only": bool(us_only),
        "min_market_cap_usd": (
            float(min_market_cap_usd) if min_market_cap_usd is not None else None
        ),
        "source_database_path": resolved_source_database_path.as_posix(),
        "unified_csv_path": resolved_unified_csv_path.as_posix(),
        "screen_ranked_csv_path": (
            resolved_screen_csv_path.as_posix() if resolved_screen_csv_path else None
        ),
        "safety_scored_csv_path": (
            resolved_safety_csv_path.as_posix() if resolved_safety_csv_path else None
        ),
        "output_dir": output_path.as_posix(),
        "candidates_csv": candidates_csv.as_posix(),
        "database_path": database_path.as_posix(),
        "report_md": report_md.as_posix(),
        "row_count": len(ordered_rows),
        "tier_counts": tier_counts,
        "bucket_counts": bucket_counts,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "output_dir": output_path,
        "candidates_csv": candidates_csv,
        "database_path": database_path,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "row_count": len(ordered_rows),
        "tier_counts": tier_counts,
        "bucket_counts": bucket_counts,
    }
