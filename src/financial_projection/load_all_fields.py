"""Load latest TradingView all-fields snapshot rows for projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from constants.trading_view_constants import PREFERRED_MARKETS

from .config import DEFAULT_ALL_FIELDS_ROOT, DEFAULT_PREDICTION_ROOT
from .fields import PROJECTION_FIELD_WHITELIST


def _import_duckdb():
    import duckdb

    return duckdb


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def discover_latest_all_fields_db(
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
) -> Path | None:
    root = Path(all_fields_root)
    if not root.exists():
        return None
    candidates = sorted(
        root.rglob("tradingview_all_fields_*.duckdb"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def discover_latest_prediction_db(
    prediction_root: str | Path = DEFAULT_PREDICTION_ROOT,
) -> Path | None:
    """
    Prefer ISO year/week layout used by run_full_analysis_suite_duckdb:
    ``iso_year=YYYY/week=WW/move_prediction_*.duckdb``.
    Falls back to newest mtime match under the root.
    """
    root = Path(prediction_root)
    if not root.exists():
        return None

    ranked: list[tuple[int, int, float, Path]] = []
    for database_path in root.glob("iso_year=*/week=*/move_prediction_*.duckdb"):
        try:
            iso_year = int(database_path.parent.parent.name.split("=", 1)[1])
            iso_week = int(database_path.parent.name.split("=", 1)[1])
        except (IndexError, ValueError):
            continue
        ranked.append(
            (iso_year, iso_week, database_path.stat().st_mtime, database_path)
        )
    if ranked:
        return max(ranked)[3]

    fallback = sorted(
        root.rglob("move_prediction_*.duckdb"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return fallback[0] if fallback else None


def resolve_all_fields_day_database(
    day_label: str,
    *,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
) -> Path:
    """
    Resolve an all-fields day folder DuckDB, e.g. ``01_07_2026`` →
    ``.../trading_view_all_fields_data/01_07_2026/tradingview_all_fields_01_07_2026.duckdb``.

    The day DB may contain multiple runs; loaders use the latest ``run_id`` inside it.
    """
    normalized = str(day_label or "").strip()
    if not normalized:
        raise ValueError("day_label is required (format dd_mm_yyyy, e.g. '01_07_2026')")

    root = Path(all_fields_root)
    expected = root / normalized / f"tradingview_all_fields_{normalized}.duckdb"
    if expected.is_file():
        return expected.resolve()

    matches = [
        candidate
        for candidate in root.rglob(f"tradingview_all_fields_{normalized}.duckdb")
        if candidate.is_file()
    ]
    if len(matches) == 1:
        return matches[0].resolve()
    if not matches:
        raise FileNotFoundError(
            f"No all-fields DuckDB found for day_label={normalized!r} under {root.as_posix()}"
        )
    raise ValueError(
        f"Multiple all-fields DuckDB files found for day_label={normalized!r}: {matches}"
    )


def resolve_all_fields_database(
    *,
    all_fields_db: str | Path | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
) -> Path:
    if all_fields_db is not None:
        path = Path(all_fields_db)
        if not path.exists():
            raise FileNotFoundError(f"All-fields database not found: {path.as_posix()}")
        return path.resolve()

    discovered = discover_latest_all_fields_db(all_fields_root)
    if discovered is None:
        raise FileNotFoundError(
            "No tradingview_all_fields_*.duckdb found under "
            f"{Path(all_fields_root).as_posix()}"
        )
    return discovered.resolve()


def _available_columns(connection: Any, table_name: str = "all_fields_rows") -> set[str]:
    rows = connection.execute(f"DESCRIBE {table_name}").fetchall()
    return {str(row[0]) for row in rows}


def load_latest_all_fields_universe(
    *,
    database_path: str | Path,
    fields: Sequence[str] = PROJECTION_FIELD_WHITELIST,
    markets: Sequence[str] | None = None,
    min_market_cap_usd: float = 0.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Load one latest all-fields row per symbol with the projection whitelist.

    Returns (rows, load_metadata).
    """
    db_path = Path(database_path)
    if not db_path.exists():
        raise FileNotFoundError(f"All-fields database not found: {db_path.as_posix()}")

    duckdb = _import_duckdb()
    resolved_markets = [
        str(value).strip().lower()
        for value in (markets if markets is not None else PREFERRED_MARKETS)
        if str(value).strip()
    ]

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "all_fields_rows" not in tables:
            raise ValueError(
                f"Table all_fields_rows missing in {db_path.as_posix()}"
            )

        available = _available_columns(conn)
        select_parts: list[str] = []
        for field in fields:
            if field not in available:
                select_parts.append(f'CAST(NULL AS VARCHAR) AS "{field}"')
            else:
                select_parts.append(
                    f'CAST(r."{field}" AS VARCHAR) AS "{field}"'
                )

        has_run_metadata = "run_metadata" in tables
        if has_run_metadata:
            latest_run_cte = """
                latest_run AS (
                    SELECT run_id, created_at_utc
                    FROM run_metadata
                    WHERE suite_name = 'tradingview_all_fields_export_duckdb'
                       OR suite_name IS NULL
                       OR suite_name = ''
                    ORDER BY created_at_utc DESC NULLS LAST, run_id DESC
                    LIMIT 1
                )
            """
            from_clause = """
                FROM all_fields_rows AS r
                INNER JOIN latest_run AS lr ON r.run_id = lr.run_id
            """
            run_id_select = "lr.run_id AS source_run_id, CAST(lr.created_at_utc AS VARCHAR) AS source_created_at_utc"
        else:
            latest_run_cte = """
                latest_run AS (
                    SELECT run_id
                    FROM all_fields_rows
                    GROUP BY run_id
                    ORDER BY run_id DESC
                    LIMIT 1
                )
            """
            from_clause = """
                FROM all_fields_rows AS r
                INNER JOIN latest_run AS lr ON r.run_id = lr.run_id
            """
            run_id_select = "lr.run_id AS source_run_id, CAST(NULL AS VARCHAR) AS source_created_at_utc"

        market_filter_sql = "TRUE"
        if resolved_markets and "market" in available:
            market_literals = ", ".join(_q(m) for m in resolved_markets)
            market_filter_sql = (
                f"lower(COALESCE(CAST(r.market AS VARCHAR), '')) IN ({market_literals})"
            )

        market_cap_filter_sql = "TRUE"
        if min_market_cap_usd > 0 and "market_cap_basic" in available:
            market_cap_filter_sql = (
                f"TRY_CAST(r.market_cap_basic AS DOUBLE) >= {float(min_market_cap_usd)}"
            )

        query = f"""
            WITH {latest_run_cte}
            SELECT
                {run_id_select},
                {", ".join(select_parts)}
            {from_clause}
            WHERE {market_filter_sql}
              AND {market_cap_filter_sql}
        """
        cursor = conn.execute(query)
        columns = [str(item[0]) for item in cursor.description]
        raw_rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()

    rows = [_normalize_row(row) for row in raw_rows]
    source_run_id = ""
    source_created_at = ""
    if rows:
        source_run_id = str(rows[0].get("source_run_id") or "")
        source_created_at = str(rows[0].get("source_created_at_utc") or "")

    metadata = {
        "database_path": db_path.as_posix(),
        "source_run_id": source_run_id,
        "source_created_at_utc": source_created_at,
        "row_count_raw": len(rows),
        "markets_filter": resolved_markets,
        "min_market_cap_usd": float(min_market_cap_usd),
        "fields_requested": list(fields),
        "fields_available": sorted(available & set(fields)),
        "fields_missing": sorted(set(fields) - available),
    }
    return rows, metadata


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if key in {
            "symbol",
            "name",
            "exchange",
            "market",
            "sector",
            "industry",
            "source_run_id",
            "source_created_at_utc",
        }:
            out[key] = "" if value is None else str(value).strip()
            continue
        numeric = _safe_float(value)
        out[key] = numeric if numeric is not None else value
        # Keep numeric fields as float|None for model.
        if key not in {
            "symbol",
            "name",
            "exchange",
            "market",
            "sector",
            "industry",
            "source_run_id",
            "source_created_at_utc",
        }:
            out[key] = numeric
    return out


def prepare_projection_universe(
    rows: Sequence[dict[str, Any]],
    *,
    min_revenue_usd: float = 0.0,
    max_ev_to_revenue: float | None = None,
) -> list[dict[str, Any]]:
    """
    Keep rows with the minimum inputs for EV/Revenue projection.

    Uses exact TradingView all-fields names only (no alias columns):
    ``total_revenue_ttm``, ``enterprise_value_current``,
    ``enterprise_value_to_revenue_ttm``, ``net_debt``, etc.

    Prefer ``total_revenue_ttm``; when TTM is missing/non-positive, backfill
    ``total_revenue_ttm`` from ``total_revenue`` for modeling continuity.
    Reconstruct EV/Rev from EV / revenue when the ratio field is missing.
    If ``net_debt`` is missing, derive from ``total_debt`` − ``cash_n_equivalents_fq``.
    """
    prepared: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        close = _safe_float(row.get("close"))
        market_cap = _safe_float(row.get("market_cap_basic"))
        revenue_fy = _safe_float(row.get("total_revenue"))
        revenue_ttm = _safe_float(row.get("total_revenue_ttm"))
        # Prefer TTM for run-rate projections when both exist and TTM > 0.
        if revenue_ttm is not None and revenue_ttm > 0:
            revenue = revenue_ttm
        elif revenue_fy is not None and revenue_fy > 0:
            revenue = revenue_fy
        else:
            revenue = None
        ev = _safe_float(row.get("enterprise_value_current"))
        ev_rev = _safe_float(row.get("enterprise_value_to_revenue_ttm"))
        if (ev_rev is None or ev_rev <= 0) and ev is not None and revenue and revenue > 0:
            ev_rev = ev / revenue
        if (ev is None or ev <= 0) and ev_rev is not None and revenue and revenue > 0:
            ev = ev_rev * revenue

        net_debt = _safe_float(row.get("net_debt"))
        if net_debt is None:
            total_debt = _safe_float(row.get("total_debt"))
            cash = _safe_float(row.get("cash_n_equivalents_fq"))
            if total_debt is not None or cash is not None:
                net_debt = (total_debt or 0.0) - (cash or 0.0)

        if not symbol or close is None or close <= 0:
            continue
        if market_cap is None or market_cap <= 0:
            continue
        if revenue is None or revenue <= 0:
            continue
        if min_revenue_usd > 0 and revenue < min_revenue_usd:
            continue
        if ev_rev is None or ev_rev <= 0:
            continue
        if max_ev_to_revenue is not None and ev_rev > max_ev_to_revenue:
            continue

        prepared_row = dict(row)
        # Keep exact all-fields keys; only fill gaps so the model can read them.
        prepared_row["total_revenue_ttm"] = revenue
        if ev is not None:
            prepared_row["enterprise_value_current"] = ev
        prepared_row["enterprise_value_to_revenue_ttm"] = ev_rev
        prepared_row["net_debt"] = 0.0 if net_debt is None else net_debt
        prepared.append(prepared_row)
    return prepared
