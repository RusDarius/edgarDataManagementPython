"""Reduced-form historical backtest for the upside opportunity scanner.

Validates the ``compute_momentum_component`` sleeve (imported directly from
``upside_opportunity_scanner``, never re-derived) against forward returns
across the FULL historical date range available in a symbol-day feature
snapshot database. Every (symbol, day) observation is scored using only the
technical percentile fields that are actually recorded for every historical
day (``ADRP`` and relative-volume universe percentiles from
``symbol_day_feature_values``, and the ``momentum_context`` sleeve percentile
from ``symbol_day_sleeve_scores``) and then bucketed into deciles with
DuckDB's ``NTILE()`` window function. Each bucket's forward-return statistics
(median, average, win rate, sample count) show whether a higher momentum
score actually preceded better forward outcomes historically.

Explicit scope boundary: this backtest validates ONLY the momentum sleeve.
``opp_safety_quality_component``, ``opp_valuation_component``, and
``opp_binary_risk_score`` are NOT retroactively backtested here, because
their inputs (safety scan scores, forward valuation multiples, earnings
dates) are only available for the CURRENT scan day's already-computed lens
outputs -- they are not stored per historical day in the snapshot database.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Sequence

from .region_filters import (
    build_market_filter_sql,
    build_region_filter_sql,
    expand_us_aliases,
    normalize_market_list,
    normalize_string_list,
)
from .upside_opportunity_scanner import compute_momentum_component

DEFAULT_BUCKET_COUNT = 10


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


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


def _discover_horizons(connection: Any, *, database_name: str) -> tuple[int, ...]:
    pattern = re.compile(r"^forward_return_(\d+)d_pct$")
    columns = _table_columns(
        connection, database_name=database_name, table_name="symbol_day_forward_labels"
    )
    return tuple(
        sorted(
            {int(match.group(1)) for col in columns if (match := pattern.match(col))}
        )
    )


def _fetch_dict_rows(
    connection: Any, query: str, params: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    cursor = (
        connection.execute(query, list(params)) if params else connection.execute(query)
    )
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def load_historical_momentum_observations(
    *,
    snapshot_database_path: str | Path,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    ranking_horizon: int = 5,
    duckdb_threads: int = 16,
) -> tuple[list[dict[str, Any]], int]:
    """Return per (symbol, day) momentum-input rows plus the resolved horizon.

    Each row carries only the fields available for every historical day:
    ``adrp_pct_today``, ``relvol_pct_today``, ``mom_core_pct_today``, and the
    forward return over the resolved ranking horizon.
    """
    db_path = Path(snapshot_database_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Snapshot database not found: {db_path.as_posix()}")

    duckdb = _import_duckdb()
    conn = duckdb.connect(database=":memory:")
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        conn.execute(f"ATTACH {_q(db_path.as_posix())} AS src (READ_ONLY)")

        for table_name in (
            "symbol_day_feature_snapshot",
            "symbol_day_feature_values",
            "symbol_day_sleeve_scores",
            "symbol_day_forward_labels",
        ):
            if not _table_exists(conn, database_name="src", table_name=table_name):
                raise ValueError(
                    f"Required table 'src.{table_name}' not found in "
                    f"{db_path.as_posix()}. Rebuild the snapshot with the "
                    "expanded feature tables first."
                )

        horizons = _discover_horizons(conn, database_name="src")
        if not horizons:
            raise ValueError(
                f"No forward_return_Nd_pct columns found in src.symbol_day_forward_labels "
                f"({db_path.as_posix()})."
            )
        resolved_horizon = (
            ranking_horizon if ranking_horizon in horizons else max(horizons)
        )
        forward_return_col = f"forward_return_{resolved_horizon}d_pct"

        snapshot_columns = _table_columns(
            conn, database_name="src", table_name="symbol_day_feature_snapshot"
        )
        resolved_markets = normalize_market_list(markets)
        resolved_countries = expand_us_aliases(
            normalize_string_list(countries), us_only
        )
        resolved_exchanges = normalize_string_list(exchanges)

        region_filter_sql = build_region_filter_sql(
            country_expr="snap.country",
            exchange_expr="snap.exchange",
            countries=resolved_countries,
            exchanges=resolved_exchanges,
        )
        market_filter_sql = (
            build_market_filter_sql(market_expr="snap.market", markets=resolved_markets)
            if "market" in snapshot_columns
            else "1 = 1"
        )
        date_filter_parts = ["1 = 1"]
        if start_date:
            date_filter_parts.append(f"snap.source_date >= DATE {_q(start_date)}")
        if end_date:
            date_filter_parts.append(f"snap.source_date <= DATE {_q(end_date)}")
        date_filter_sql = " AND ".join(date_filter_parts)

        conn.execute("""
            CREATE OR REPLACE TEMP TABLE field_pivot AS
            SELECT
                source_day_label,
                source_date,
                symbol,
                MAX(CASE WHEN field_name = 'ADRP' THEN directional_universe_percentile END)
                    AS adrp_pct_today,
                MAX(CASE WHEN field_name = 'relative_volume_10d_calc' THEN directional_universe_percentile END)
                    AS relvol_pct_today
            FROM src.symbol_day_feature_values
            GROUP BY source_day_label, source_date, symbol
        """)
        conn.execute("""
            CREATE OR REPLACE TEMP TABLE sleeve_pivot AS
            SELECT
                source_day_label,
                source_date,
                symbol,
                MAX(CASE WHEN sleeve_name = 'momentum_context' THEN avg_directional_universe_percentile END)
                    AS mom_core_pct_today
            FROM src.symbol_day_sleeve_scores
            GROUP BY source_day_label, source_date, symbol
        """)

        query = f"""
            SELECT
                snap.source_day_label,
                snap.source_date,
                snap.symbol,
                fp.adrp_pct_today,
                fp.relvol_pct_today,
                sp.mom_core_pct_today,
                lbl.{forward_return_col} AS forward_return_pct
            FROM src.symbol_day_feature_snapshot AS snap
            LEFT JOIN field_pivot AS fp
                ON fp.source_day_label = snap.source_day_label
                AND fp.source_date = snap.source_date
                AND fp.symbol = snap.symbol
            LEFT JOIN sleeve_pivot AS sp
                ON sp.source_day_label = snap.source_day_label
                AND sp.source_date = snap.source_date
                AND sp.symbol = snap.symbol
            JOIN src.symbol_day_forward_labels AS lbl
                ON lbl.source_day_label = snap.source_day_label
                AND lbl.symbol = snap.symbol
            WHERE {region_filter_sql}
                AND {market_filter_sql}
                AND {date_filter_sql}
                AND lbl.{forward_return_col} IS NOT NULL
        """
        rows = _fetch_dict_rows(conn, query)
    finally:
        conn.close()

    return rows, resolved_horizon


def score_and_bucket_momentum_observations(
    observations: Sequence[dict[str, Any]],
    *,
    bucket_count: int = DEFAULT_BUCKET_COUNT,
    duckdb_threads: int = 16,
) -> list[dict[str, Any]]:
    """Score observations with `compute_momentum_component`, bucket via NTILE.

    Returns one row per decile (or ``bucket_count`` groups) with forward
    return statistics, ordered from lowest to highest momentum bucket.
    """
    if not observations:
        return []

    scored_rows = [
        (
            str(obs.get("symbol") or ""),
            str(obs.get("source_day_label") or ""),
            compute_momentum_component(obs),
            float(obs["forward_return_pct"]),
        )
        for obs in observations
    ]

    duckdb = _import_duckdb()
    conn = duckdb.connect(database=":memory:")
    conn.execute(f"SET threads TO {int(duckdb_threads)}")
    try:
        conn.execute("""
            CREATE TABLE observations (
                symbol VARCHAR,
                source_day_label VARCHAR,
                momentum_component DOUBLE,
                forward_return_pct DOUBLE
            )
        """)
        conn.executemany("INSERT INTO observations VALUES (?, ?, ?, ?)", scored_rows)

        query = f"""
            WITH bucketed AS (
                SELECT
                    *,
                    NTILE({int(max(2, bucket_count))}) OVER (ORDER BY momentum_component) AS momentum_bucket
                FROM observations
            )
            SELECT
                momentum_bucket,
                COUNT(*) AS sample_count,
                MIN(momentum_component) AS momentum_component_min,
                MAX(momentum_component) AS momentum_component_max,
                MEDIAN(forward_return_pct) AS median_forward_return_pct,
                AVG(forward_return_pct) AS avg_forward_return_pct,
                AVG(CASE WHEN forward_return_pct > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
            FROM bucketed
            GROUP BY momentum_bucket
            ORDER BY momentum_bucket
        """
        bucket_rows = _fetch_dict_rows(conn, query)
    finally:
        conn.close()

    for row in bucket_rows:
        for key in (
            "momentum_component_min",
            "momentum_component_max",
            "median_forward_return_pct",
            "avg_forward_return_pct",
            "win_rate",
        ):
            if row.get(key) is not None:
                row[key] = round(float(row[key]), 4)
    return bucket_rows


def _write_report(
    path: Path,
    *,
    snapshot_database_path: Path,
    resolved_horizon: int,
    observation_count: int,
    bucket_rows: Sequence[dict[str, Any]],
    buckets_csv: Path,
    database_path: Path,
) -> None:
    lines = [
        "# Upside Opportunity Momentum Backtest",
        "",
        "## What This Validates",
        "",
        "This is a reduced-form historical backtest of the "
        "`opp_momentum_component` sleeve used by the upside opportunity "
        "scanner. Every (symbol, day) observation across the full historical "
        "date range is scored with the same `compute_momentum_component` "
        "function used in live scans (imported directly, not re-derived), "
        f"using ADRP / relative-volume universe percentiles and the "
        "`momentum_context` sleeve percentile -- the only inputs actually "
        "recorded for every historical day. Observations are bucketed into "
        f"{len(bucket_rows)} groups via DuckDB's `NTILE()` window function, "
        "ordered from lowest to highest momentum score.",
        "",
        "## Explicit Scope Boundary",
        "",
        "`opp_safety_quality_component`, `opp_valuation_component`, and "
        "`opp_binary_risk_score` are NOT retroactively backtested here. "
        "Their inputs (safety scan scores, forward valuation multiples, "
        "earnings dates) are only available for the current scan day's "
        "already-computed lens outputs and are not stored per historical day "
        "in the snapshot database.",
        "",
        f"- forward return horizon: {resolved_horizon} trading days",
        f"- total observations: {observation_count}",
        f"- source snapshot database: `{snapshot_database_path.as_posix()}`",
        "",
        "## Momentum Bucket Results",
        "",
        "| bucket | sample_count | momentum_min | momentum_max | "
        "median_fwd_return_pct | avg_fwd_return_pct | win_rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in bucket_rows:
        lines.append(
            f"| {row['momentum_bucket']} | {row['sample_count']} | "
            f"{row['momentum_component_min']} | {row['momentum_component_max']} | "
            f"{row['median_forward_return_pct']} | {row['avg_forward_return_pct']} | "
            f"{row['win_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Output Files",
            "",
            f"- bucket results CSV: `{buckets_csv.as_posix()}`",
            f"- query database: `{database_path.as_posix()}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_csv_rows(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    import csv

    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_database(
    database_path: Path, *, bucket_rows: Sequence[dict[str, Any]]
) -> None:
    duckdb = _import_duckdb()
    database_path.unlink(missing_ok=True)
    conn = duckdb.connect(database_path.as_posix())
    try:
        conn.execute("""
            CREATE TABLE upside_momentum_backtest_buckets (
                momentum_bucket BIGINT,
                sample_count BIGINT,
                momentum_component_min DOUBLE,
                momentum_component_max DOUBLE,
                median_forward_return_pct DOUBLE,
                avg_forward_return_pct DOUBLE,
                win_rate DOUBLE
            )
        """)
        conn.executemany(
            "INSERT INTO upside_momentum_backtest_buckets VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["momentum_bucket"],
                    row["sample_count"],
                    row["momentum_component_min"],
                    row["momentum_component_max"],
                    row["median_forward_return_pct"],
                    row["avg_forward_return_pct"],
                    row["win_rate"],
                )
                for row in bucket_rows
            ],
        )
    finally:
        conn.close()


def run_upside_momentum_backtest(
    *,
    output_dir: str | Path,
    snapshot_database_path: str | Path,
    markets: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    exchanges: Sequence[str] | None = None,
    us_only: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
    ranking_horizon: int = 5,
    bucket_count: int = DEFAULT_BUCKET_COUNT,
    duckdb_threads: int = 16,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    observations, resolved_horizon = load_historical_momentum_observations(
        snapshot_database_path=snapshot_database_path,
        markets=markets,
        countries=countries,
        exchanges=exchanges,
        us_only=us_only,
        start_date=start_date,
        end_date=end_date,
        ranking_horizon=ranking_horizon,
        duckdb_threads=duckdb_threads,
    )
    bucket_rows = score_and_bucket_momentum_observations(
        observations,
        bucket_count=bucket_count,
        duckdb_threads=duckdb_threads,
    )

    buckets_csv = output_path / "upside_momentum_backtest_buckets.csv"
    database_path = output_path / "upside_momentum_backtest.duckdb"
    report_md = output_path / "upside_momentum_backtest_report.md"
    manifest_path = output_path / "upside_momentum_backtest_manifest.json"

    _write_csv_rows(buckets_csv, bucket_rows)
    resolved_snapshot_path = Path(snapshot_database_path)
    _write_database(database_path, bucket_rows=bucket_rows)
    _write_report(
        report_md,
        snapshot_database_path=resolved_snapshot_path,
        resolved_horizon=resolved_horizon,
        observation_count=len(observations),
        bucket_rows=bucket_rows,
        buckets_csv=buckets_csv,
        database_path=database_path,
    )

    manifest = {
        "command": "upside-momentum-backtest",
        "snapshot_database_path": resolved_snapshot_path.as_posix(),
        "ranking_horizon": resolved_horizon,
        "bucket_count": int(max(2, bucket_count)),
        "observation_count": len(observations),
        "markets": list(markets or ()),
        "countries": list(countries or ()),
        "exchanges": list(exchanges or ()),
        "us_only": bool(us_only),
        "start_date": start_date,
        "end_date": end_date,
        "output_dir": output_path.as_posix(),
        "buckets_csv": buckets_csv.as_posix(),
        "database_path": database_path.as_posix(),
        "report_md": report_md.as_posix(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "output_dir": output_path,
        "buckets_csv": buckets_csv,
        "database_path": database_path,
        "report_md": report_md,
        "manifest_path": manifest_path,
        "observation_count": len(observations),
        "resolved_horizon": resolved_horizon,
        "bucket_rows": bucket_rows,
    }
