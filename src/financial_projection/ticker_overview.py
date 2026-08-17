"""Single-ticker projection overview from an all-fields DuckDB snapshot.

Pulls the curated ~100 overview fields for one ``EXCHANGE:TICKER``, buckets them,
optionally adds industry peer percentiles, and writes a projection-log layout
similar in spirit to the ticker pattern scan overview.
"""

from __future__ import annotations

import csv
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import DEFAULT_ALL_FIELDS_ROOT, DEFAULT_OUTPUT_ROOT
from .load_all_fields import (
    resolve_all_fields_database,
    resolve_all_fields_day_database,
)
from .overview_fields import (
    OVERVIEW_FIELD_BUCKETS,
    OVERVIEW_FIELD_TO_BUCKET,
    OVERVIEW_FIELD_WHITELIST,
    PEER_PERCENTILE_BUCKETS,
    TEXT_OVERVIEW_FIELDS,
    bucket_field_counts,
    overview_field_count,
)

_DAY_LABEL_RE = re.compile(r"^\d{2}_\d{2}_\d{4}$")


def _import_duckdb():
    import duckdb

    return duckdb


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_symbol(symbol: str) -> str:
    normalized = _normalize_text(symbol).upper()
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    return normalized


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = _normalize_text(value)
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"none", "null", "nan", "n/a", "na", "-"}:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    text = text.replace(",", "")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _available_columns(connection: Any, table_name: str = "all_fields_rows") -> set[str]:
    rows = connection.execute(f"DESCRIBE {table_name}").fetchall()
    return {str(row[0]) for row in rows}


def _infer_source_day_label(database_path: Path) -> str | None:
    for part in database_path.resolve().parts[::-1]:
        if _DAY_LABEL_RE.match(part):
            return part
    match = re.search(r"(\d{2}_\d{2}_\d{4})", database_path.name)
    return match.group(1) if match else None


def _build_run_id(ticker: str, stamp: datetime) -> str:
    bare = _normalize_symbol(ticker).replace("/", "_")
    exchange = ""
    requested = _normalize_text(ticker).upper()
    if ":" in requested:
        exchange = requested.split(":", 1)[0]
    token = f"{exchange}_{bare}" if exchange else bare
    token = re.sub(r"[^A-Z0-9_]+", "_", token)
    short = uuid.uuid4().hex[:8]
    return f"tickoverview_{token}_{stamp.strftime('%Y%m%dT%H%M%SZ')}_{short}"


def _resolve_latest_run_id(connection: Any, available: set[str]) -> tuple[str, str | None]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    if "run_metadata" in tables:
        row = connection.execute(
            """
            SELECT run_id, CAST(created_at_utc AS VARCHAR)
            FROM run_metadata
            WHERE suite_name = 'tradingview_all_fields_export_duckdb'
               OR suite_name IS NULL
               OR suite_name = ''
            ORDER BY created_at_utc DESC NULLS LAST, run_id DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row[0]:
            return str(row[0]), (None if row[1] is None else str(row[1]))
    if "run_id" not in available:
        return "", None
    row = connection.execute(
        """
        SELECT run_id
        FROM all_fields_rows
        GROUP BY run_id
        ORDER BY run_id DESC
        LIMIT 1
        """
    ).fetchone()
    return (str(row[0]) if row and row[0] else ""), None


def _fetch_ticker_row(
    connection: Any,
    *,
    run_id: str,
    ticker: str,
    fields: Sequence[str],
    available: set[str],
) -> dict[str, Any] | None:
    requested_upper = _normalize_text(ticker).upper()
    bare_upper = _normalize_symbol(ticker)
    select_fields = [field for field in fields if field in available]
    select_parts = ["symbol"] + [
        f"{_quote_ident(field)} AS {_quote_ident(field)}" for field in select_fields
    ]
    where_run = "TRUE"
    params: list[Any] = []
    if run_id and "run_id" in available:
        where_run = "run_id = ?"
        params.append(run_id)

    order_bits = [
        "CASE WHEN UPPER(CAST(symbol AS VARCHAR)) = ? THEN 0 ELSE 1 END"
    ]
    params.append(requested_upper)
    if "is_primary" in available:
        order_bits.append(
            "CASE WHEN lower(CAST(is_primary AS VARCHAR)) IN ('true','1','yes') "
            "THEN 0 ELSE 1 END"
        )
    if "market_cap_basic" in available:
        order_bits.append("TRY_CAST(market_cap_basic AS DOUBLE) DESC NULLS LAST")
    order_bits.append("symbol")

    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM all_fields_rows
        WHERE {where_run}
          AND (
                UPPER(CAST(symbol AS VARCHAR)) = ?
             OR UPPER(list_element(string_split(CAST(symbol AS VARCHAR), ':'), 2)) = ?
          )
        ORDER BY {", ".join(order_bits)}
        LIMIT 1
    """
    params.extend([requested_upper, bare_upper])
    cursor = connection.execute(sql, params)
    columns = [str(item[0]) for item in cursor.description]
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


def _peer_percentiles(
    connection: Any,
    *,
    run_id: str,
    ticker_row: Mapping[str, Any],
    fields: Sequence[str],
    available: set[str],
) -> dict[str, dict[str, Any]]:
    industry = _normalize_text(ticker_row.get("industry"))
    if not industry or "industry" not in available:
        return {}

    numeric_fields = [
        field
        for field in fields
        if field in available
        and field not in TEXT_OVERVIEW_FIELDS
        and OVERVIEW_FIELD_TO_BUCKET.get(field) in PEER_PERCENTILE_BUCKETS
    ]
    if not numeric_fields:
        return {}

    where_parts = ["TRUE"]
    params: list[Any] = []
    if run_id and "run_id" in available:
        where_parts.append("run_id = ?")
        params.append(run_id)
    where_parts.append("CAST(industry AS VARCHAR) = ?")
    params.append(industry)
    if "type" in available:
        where_parts.append("lower(CAST(type AS VARCHAR)) = 'stock'")
    if "is_primary" in available:
        where_parts.append(
            "lower(CAST(is_primary AS VARCHAR)) IN ('true','1','yes')"
        )

    out: dict[str, dict[str, Any]] = {}
    for field in numeric_fields:
        own_value = _safe_float(ticker_row.get(field))
        if own_value is None:
            continue
        col = _quote_ident(field)
        sql = f"""
            WITH peers AS (
                SELECT TRY_CAST(REPLACE(TRIM(CAST({col} AS VARCHAR)), ',', '') AS DOUBLE) AS v
                FROM all_fields_rows
                WHERE {" AND ".join(where_parts)}
            ),
            clean AS (
                SELECT v FROM peers WHERE v IS NOT NULL AND isfinite(v)
            )
            SELECT
                COUNT(*) AS peer_n,
                AVG(CASE WHEN v <= ? THEN 1.0 ELSE 0.0 END) AS percentile_rank,
                median(v) AS peer_median,
                avg(v) AS peer_mean
            FROM clean
        """
        row = connection.execute(sql, [*params, own_value]).fetchone()
        if row is None:
            continue
        peer_n = int(row[0] or 0)
        if peer_n <= 0:
            continue
        out[field] = {
            "peer_n": peer_n,
            "peer_percentile": None if row[1] is None else round(float(row[1]) * 100.0, 2),
            "peer_median": None if row[2] is None else float(row[2]),
            "peer_mean": None if row[3] is None else float(row[3]),
            "peer_scope": "industry",
            "peer_industry": industry,
        }
    return out


def _computed_helpers(ticker_row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Small non-TV helpers derived from exact all-fields inputs."""
    close = _safe_float(ticker_row.get("close"))
    high_52 = _safe_float(ticker_row.get("price_52_week_high"))
    low_52 = _safe_float(ticker_row.get("price_52_week_low"))
    p_fcf = _safe_float(ticker_row.get("price_free_cash_flow_ttm"))
    pt_median = _safe_float(ticker_row.get("price_target_median"))
    pt_avg = _safe_float(ticker_row.get("price_target_average"))
    rev_ttm = _safe_float(ticker_row.get("total_revenue_ttm"))
    rev_next_fy = _safe_float(ticker_row.get("revenue_forecast_next_fy"))

    helpers: list[dict[str, Any]] = []

    def add(name: str, value: float | None, note: str) -> None:
        helpers.append(
            {
                "bucket": "computed",
                "field_name": name,
                "value": value,
                "available_in_database": True,
                "is_text": False,
                "note": note,
            }
        )

    if close is not None and high_52 not in (None, 0.0):
        add(
            "distance_to_52w_high_pct",
            round(100.0 * (close / high_52 - 1.0), 4),
            "100 * (close / price_52_week_high - 1)",
        )
    if close is not None and low_52 not in (None, 0.0):
        add(
            "distance_to_52w_low_pct",
            round(100.0 * (close / low_52 - 1.0), 4),
            "100 * (close / price_52_week_low - 1)",
        )
    if p_fcf not in (None, 0.0):
        add(
            "free_cash_flow_yield_proxy_pct",
            round(100.0 / p_fcf, 4),
            "100 / price_free_cash_flow_ttm",
        )
    if close not in (None, 0.0) and pt_median is not None:
        add(
            "street_upside_vs_close_median_pct",
            round(100.0 * (pt_median / close - 1.0), 4),
            "100 * (price_target_median / close - 1)",
        )
    if close not in (None, 0.0) and pt_avg is not None:
        add(
            "street_upside_vs_close_average_pct",
            round(100.0 * (pt_avg / close - 1.0), 4),
            "100 * (price_target_average / close - 1)",
        )
    if rev_ttm not in (None, 0.0) and rev_next_fy is not None:
        add(
            "street_next_fy_revenue_growth_pct",
            round(100.0 * (rev_next_fy / rev_ttm - 1.0), 4),
            "100 * (revenue_forecast_next_fy / total_revenue_ttm - 1)",
        )
    return helpers


def _metric_rows(
    *,
    ticker_row: Mapping[str, Any],
    available: set[str],
    peer_stats: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket, fields in OVERVIEW_FIELD_BUCKETS.items():
        for field in fields:
            present = field in available
            raw = ticker_row.get(field) if present else None
            is_text = field in TEXT_OVERVIEW_FIELDS
            if is_text:
                value: Any = None if raw is None else _normalize_text(raw)
            else:
                value = _safe_float(raw)
                if value is None and raw is not None and _normalize_text(raw):
                    value = _normalize_text(raw)
            peer = peer_stats.get(field, {})
            rows.append(
                {
                    "bucket": bucket,
                    "field_name": field,
                    "value": value,
                    "available_in_database": present,
                    "is_text": is_text,
                    "peer_n": peer.get("peer_n"),
                    "peer_percentile": peer.get("peer_percentile"),
                    "peer_median": peer.get("peer_median"),
                    "peer_mean": peer.get("peer_mean"),
                    "peer_scope": peer.get("peer_scope"),
                    "peer_industry": peer.get("peer_industry"),
                    "note": "",
                }
            )
    rows.extend(_computed_helpers(ticker_row))
    return rows


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _build_overview_log(
    *,
    run_id: str,
    ticker_requested: str,
    matched_symbol: str,
    source_day_label: str | None,
    database_path: Path,
    source_run_id: str,
    metric_rows: Sequence[Mapping[str, Any]],
    fields_missing: Sequence[str],
) -> list[str]:
    by_bucket: dict[str, list[Mapping[str, Any]]] = {}
    for row in metric_rows:
        by_bucket.setdefault(str(row["bucket"]), []).append(row)

    identity = {
        str(row["field_name"]): row.get("value")
        for row in by_bucket.get("identity", [])
    }
    lines = [
        f"Ticker Projection Overview | {run_id}",
        f"ticker_requested={ticker_requested}",
        f"matched_symbol={matched_symbol}",
        f"source_day_label={source_day_label or ''}",
        f"all_fields_db={database_path.as_posix()}",
        f"source_run_id={source_run_id}",
        f"overview_field_count={overview_field_count()}",
        f"bucket_counts={json.dumps(bucket_field_counts(), sort_keys=True)}",
        (
            f"name={identity.get('name')} | sector={identity.get('sector')} | "
            f"industry={identity.get('industry')} | mcap={identity.get('market_cap_basic')}"
        ),
        "",
    ]
    if fields_missing:
        lines.append(
            "fields_missing_from_database=" + ", ".join(fields_missing)
        )
        lines.append("")

    for bucket, fields in OVERVIEW_FIELD_BUCKETS.items():
        lines.append(f"[{bucket}]")
        present_rows = [
            row
            for row in by_bucket.get(bucket, [])
            if row.get("available_in_database")
        ]
        for row in present_rows:
            peer_bit = ""
            if row.get("peer_percentile") is not None:
                peer_bit = (
                    f" | peer_pct={row['peer_percentile']} "
                    f"n={row.get('peer_n')}"
                )
            lines.append(
                f"  - {row['field_name']} = {row.get('value')}{peer_bit}"
            )
        missing_here = [
            field
            for field in fields
            if field in fields_missing
        ]
        if missing_here:
            lines.append("  (missing) " + ", ".join(missing_here))
        lines.append("")

    computed = by_bucket.get("computed", [])
    if computed:
        lines.append("[computed]")
        for row in computed:
            note = f" | {row.get('note')}" if row.get("note") else ""
            lines.append(f"  - {row['field_name']} = {row.get('value')}{note}")
        lines.append("")
    return lines


def run_ticker_projection_overview(
    *,
    ticker: str,
    all_fields_db: str | Path | None = None,
    day_label: str | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    output_root: str | Path | None = None,
    include_peer_percentiles: bool = True,
    print_console: bool = True,
) -> dict[str, Any]:
    """
    Pull curated overview metrics for one ticker into a financial-projection log.

    Example::

        run_ticker_projection_overview(ticker=\"NASDAQ:PENG\")
        run_ticker_projection_overview(ticker=\"NASDAQ:TTD\", day_label=\"11_08_2026\")
    """
    requested = _normalize_text(ticker)
    if not requested:
        raise ValueError("ticker is required (e.g. NASDAQ:PENG).")

    if day_label:
        database_path = resolve_all_fields_day_database(
            day_label=day_label,
            all_fields_root=all_fields_root,
        )
    else:
        database_path = resolve_all_fields_database(
            all_fields_db=all_fields_db,
            all_fields_root=all_fields_root,
        )

    source_day_label = _infer_source_day_label(database_path) or (
        _normalize_text(day_label) or None
    )
    stamp = datetime.now(tz=timezone.utc)
    run_id = _build_run_id(requested, stamp)
    out_root = Path(output_root) if output_root is not None else Path(DEFAULT_OUTPUT_ROOT)
    day_folder = source_day_label or stamp.strftime("%d_%m_%Y")
    run_dir = out_root / day_folder / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    duckdb = _import_duckdb()
    conn = duckdb.connect(str(database_path), read_only=True)
    try:
        available = _available_columns(conn)
        if "symbol" not in available:
            raise ValueError(f"symbol column missing in {database_path.as_posix()}")
        source_run_id, source_created_at = _resolve_latest_run_id(conn, available)
        ticker_row = _fetch_ticker_row(
            conn,
            run_id=source_run_id,
            ticker=requested,
            fields=OVERVIEW_FIELD_WHITELIST,
            available=available,
        )
        if ticker_row is None:
            raise ValueError(
                f"Ticker {requested!r} not found in {database_path.as_posix()} "
                f"(run_id={source_run_id or 'n/a'})."
            )
        peer_stats: dict[str, dict[str, Any]] = {}
        if include_peer_percentiles:
            peer_stats = _peer_percentiles(
                conn,
                run_id=source_run_id,
                ticker_row=ticker_row,
                fields=OVERVIEW_FIELD_WHITELIST,
                available=available,
            )
    finally:
        conn.close()

    fields_missing = sorted(set(OVERVIEW_FIELD_WHITELIST) - available)
    fields_present = sorted(set(OVERVIEW_FIELD_WHITELIST) & available)
    matched_symbol = _normalize_text(ticker_row.get("symbol")) or requested
    metric_rows = _metric_rows(
        ticker_row=ticker_row,
        available=available,
        peer_stats=peer_stats,
    )

    overview_log_path = run_dir / "_ticker_overview.log"
    metrics_csv_path = run_dir / "metrics_by_bucket.csv"
    overview_json_path = run_dir / "ticker_overview.json"
    metadata_path = run_dir / "run_metadata.json"

    log_lines = _build_overview_log(
        run_id=run_id,
        ticker_requested=requested,
        matched_symbol=matched_symbol,
        source_day_label=source_day_label,
        database_path=database_path,
        source_run_id=source_run_id,
        metric_rows=metric_rows,
        fields_missing=fields_missing,
    )
    overview_log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    _write_csv(metrics_csv_path, metric_rows)

    payload = {
        "run_id": run_id,
        "created_at_utc": stamp.isoformat(),
        "ticker_requested": requested,
        "matched_symbol": matched_symbol,
        "source_day_label": source_day_label,
        "all_fields_db": database_path.as_posix(),
        "source_run_id": source_run_id,
        "source_created_at_utc": source_created_at,
        "overview_field_count": overview_field_count(),
        "bucket_counts": bucket_field_counts(),
        "fields_present": fields_present,
        "fields_missing": fields_missing,
        "include_peer_percentiles": bool(include_peer_percentiles),
        "metrics": metric_rows,
        "buckets": {
            bucket: [
                row for row in metric_rows if row.get("bucket") == bucket
            ]
            for bucket in list(OVERVIEW_FIELD_BUCKETS) + ["computed"]
        },
    }
    overview_json_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )

    metadata = {
        "run_id": run_id,
        "created_at_utc": stamp.isoformat(),
        "ticker_requested": requested,
        "matched_symbol": matched_symbol,
        "source_day_label": source_day_label,
        "all_fields_db": database_path.as_posix(),
        "source_run_id": source_run_id,
        "source_created_at_utc": source_created_at,
        "output_root": out_root.as_posix(),
        "run_dir": run_dir.as_posix(),
        "overview_field_count": overview_field_count(),
        "bucket_counts": bucket_field_counts(),
        "fields_present_count": len(fields_present),
        "fields_missing_count": len(fields_missing),
        "peer_percentile_field_count": len(peer_stats),
        "artifacts": {
            "overview_log": overview_log_path.as_posix(),
            "metrics_csv": metrics_csv_path.as_posix(),
            "overview_json": overview_json_path.as_posix(),
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    result = {
        **metadata,
        "metrics": metric_rows,
        "overview_log": overview_log_path.as_posix(),
        "metrics_csv": metrics_csv_path.as_posix(),
        "overview_json": overview_json_path.as_posix(),
        "metadata_json": metadata_path.as_posix(),
    }

    if print_console:
        print(f"Ticker overview: {matched_symbol}")
        print(f"Run dir: {run_dir.as_posix()}")
        print(f"Overview log: {overview_log_path.as_posix()}")
        print(
            f"Fields present/missing: {len(fields_present)}/"
            f"{len(fields_missing)} (whitelist={overview_field_count()})"
        )
        for line in log_lines[:40]:
            print(line)

    return result
