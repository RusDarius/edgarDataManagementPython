from __future__ import annotations

import argparse
import csv
import json
import statistics
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from edge_research_tools.catalyst_event_policy import (
    CatalystEventPolicyConfig,
    build_catalyst_event_rows,
)
from edge_research_tools.config import PROJECT_ROOT
from edge_research_tools.market_timing_policy import (
    CHECKPOINTS,
    TimingPolicyConfig,
    build_timing_policy_rows,
)
from edge_research_tools.run_resolution import discover_latest_edge_parent_run_dir
from edge_research_tools.upside_move_potential_scanner import (
    compute_expected_move_proxy_pct,
)
from db.trading_view_all_fields_duckdb import resolve_latest_and_full_all_fields_run_ids

DEFAULT_ALL_FIELDS_ROOT = (
    PROJECT_ROOT / "logs/tradingview_analysis/trading_view_all_fields_data"
)
DEFAULT_PREDICTION_ROOT = (
    PROJECT_ROOT / "logs/tradingview_analysis/prediction_analysis/duckdb_runs"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "logs/tradingview_analysis/market_timing_policy/runs"
)
CATALYST_EVENT_MOVE_HORIZON_DAYS = 5


@dataclass(frozen=True)
class TimingArtifactRefs:
    edge_parent_dir: Path
    edge_scan_day: str | None
    foundation_database: Path
    all_fields_database: Path
    prediction_database: Path | None
    backwards_database: Path | None


def _import_duckdb():
    import duckdb

    return duckdb


def _latest_path(paths: Iterable[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def _latest_all_fields_database(root: Path = DEFAULT_ALL_FIELDS_ROOT) -> Path | None:
    candidates: list[tuple[date, float, Path]] = []
    for database_path in root.glob("*/*.duckdb"):
        try:
            source_date = datetime.strptime(
                database_path.parent.name, "%d_%m_%Y"
            ).date()
        except ValueError:
            continue
        candidates.append((source_date, database_path.stat().st_mtime, database_path))
    return max(candidates)[2] if candidates else None


def _latest_prediction_database(root: Path = DEFAULT_PREDICTION_ROOT) -> Path | None:
    candidates: list[tuple[int, int, float, Path]] = []
    for database_path in root.glob("iso_year=*/week=*/move_prediction_*.duckdb"):
        try:
            iso_year = int(database_path.parent.parent.name.split("=", 1)[1])
            iso_week = int(database_path.parent.name.split("=", 1)[1])
        except (IndexError, ValueError):
            continue
        candidates.append(
            (iso_year, iso_week, database_path.stat().st_mtime, database_path)
        )
    return max(candidates)[3] if candidates else None


def _resolve_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    candidate = Path(path)
    return (
        candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()
    )


def resolve_timing_artifacts(
    *,
    edge_parent_dir: str | Path | None = None,
    all_fields_database: str | Path | None = None,
    prediction_database: str | Path | None = None,
    backwards_database: str | Path | None = None,
) -> TimingArtifactRefs:
    resolved_edge_parent = _resolve_path(edge_parent_dir)
    if resolved_edge_parent is None:
        resolved_edge_parent = discover_latest_edge_parent_run_dir(output_root=None)
    manifest_path = resolved_edge_parent / "parent_run_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Edge parent manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    foundation_database = Path(str(manifest.get("snapshot_db") or ""))
    if not foundation_database.exists():
        raise FileNotFoundError(
            f"Foundation snapshot from edge parent does not exist: {foundation_database}"
        )

    resolved_all_fields = (
        _resolve_path(all_fields_database) or _latest_all_fields_database()
    )
    if resolved_all_fields is None:
        raise FileNotFoundError(
            f"No all-fields DuckDB found under {DEFAULT_ALL_FIELDS_ROOT}"
        )
    resolved_prediction = (
        _resolve_path(prediction_database) or _latest_prediction_database()
    )
    resolved_backwards = _resolve_path(backwards_database) or _latest_path(
        DEFAULT_PREDICTION_ROOT.glob(
            "backwards_prediction_analysis/runs/*/backwards_prediction_analysis.duckdb"
        )
    )
    return TimingArtifactRefs(
        edge_parent_dir=resolved_edge_parent,
        edge_scan_day=(
            str(manifest.get("scan_day")) if manifest.get("scan_day") else None
        ),
        foundation_database=foundation_database,
        all_fields_database=resolved_all_fields,
        prediction_database=resolved_prediction,
        backwards_database=resolved_backwards,
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _normalize_percentile(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return number * 100.0 if 0.0 <= number <= 1.0 else number


def _load_edge_candidates(
    edge_parent_dir: Path, config: TimingPolicyConfig
) -> dict[str, dict[str, Any]]:
    aggregate = edge_parent_dir / "aggregate"
    upside_rows = _read_csv(
        aggregate / "highlights" / "edge_upside_prediction_ranked.csv"
    )
    screen_rows = _read_csv(aggregate / "screen" / "edge_screen_ranked.csv")
    unified_rows = _read_csv(
        aggregate / "edge_unified_highlights" / "edge_unified_highlights.csv"
    )
    candidates: dict[str, dict[str, Any]] = {}
    for row in upside_rows:
        rank = _integer(row.get("upside_prediction_rank"))
        if rank is None or rank > config.sticky_upside_rank_max:
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        candidates[symbol] = {
            **row,
            "symbol": symbol,
            "in_upside_watchlist_flag": 1,
            "in_screen_coverage_flag": 0,
            "adrp_percentile": _normalize_percentile(row.get("adrp_pct_today")),
        }
    for row in screen_rows:
        rank = _integer(row.get("screen_rank"))
        if rank is None or rank > config.coverage_screen_rank_max:
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        target = candidates.setdefault(symbol, {"symbol": symbol})
        target["in_screen_coverage_flag"] = 1
        target.setdefault("in_upside_watchlist_flag", 0)
        for key, value in row.items():
            if value not in (None, "") and key not in target:
                target[key] = value
        target["screen_rank"] = rank
        target["adrp_percentile"] = target.get(
            "adrp_percentile",
            _normalize_percentile(row.get("adrp_directional_universe_percentile")),
        )
        target["atrp"] = target.get("atrp", _number(row.get("atrp_raw")))
    for row in unified_rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol in candidates:
            for key, value in row.items():
                if value not in (None, "") and key not in candidates[symbol]:
                    candidates[symbol][key] = value
    for candidate in candidates.values():
        in_upside = bool(_integer(candidate.get("in_upside_watchlist_flag")))
        in_screen = bool(_integer(candidate.get("in_screen_coverage_flag")))
        candidate["candidate_source"] = (
            "both" if in_upside and in_screen else "upside" if in_upside else "screen"
        )
    return candidates


def _first_existing_csv(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _load_edge_unified_rows(edge_parent_dir: Path) -> dict[str, dict[str, Any]]:
    aggregate = edge_parent_dir / "aggregate"
    rows = _read_csv(
        aggregate / "edge_unified_highlights" / "edge_unified_highlights.csv"
    )
    return {
        symbol: row for row in rows if (symbol := str(row.get("symbol") or "").strip())
    }


def _load_edge_trade_plan_rows(edge_parent_dir: Path) -> dict[str, dict[str, Any]]:
    aggregate = edge_parent_dir / "aggregate"
    ranked_csv = _first_existing_csv(
        aggregate / "edge_trade_plan" / "edge_trade_plan_ranked.csv",
        edge_parent_dir / "edge_trade_plan" / "edge_trade_plan_ranked.csv",
    )
    rows = _read_csv(ranked_csv) if ranked_csv is not None else []
    return {
        symbol: row for row in rows if (symbol := str(row.get("symbol") or "").strip())
    }


def _load_earnings_priority_rows(edge_parent_dir: Path) -> dict[str, dict[str, Any]]:
    aggregate = edge_parent_dir / "aggregate"
    candidates_csv = _first_existing_csv(
        aggregate / "earnings_priority_lens" / "edge_earnings_priority_candidates.csv",
        edge_parent_dir
        / "earnings_priority_lens"
        / "edge_earnings_priority_candidates.csv",
    )
    rows = _read_csv(candidates_csv) if candidates_csv is not None else []
    return {
        symbol: row for row in rows if (symbol := str(row.get("symbol") or "").strip())
    }


def _load_rank_context_rows(edge_parent_dir: Path) -> dict[str, dict[str, Any]]:
    aggregate = edge_parent_dir / "aggregate"
    upside_rows = _read_csv(
        aggregate / "highlights" / "edge_upside_prediction_ranked.csv"
    )
    screen_rows = _read_csv(aggregate / "screen" / "edge_screen_ranked.csv")
    context: dict[str, dict[str, Any]] = {}

    for row in upside_rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        target = context.setdefault(symbol, {"symbol": symbol})
        target["in_upside_watchlist_flag"] = 1
        rank = _integer(row.get("upside_prediction_rank"))
        if rank is not None:
            target["upside_prediction_rank"] = rank
        if target.get("adrp_percentile") in (None, ""):
            adrp_percentile = _normalize_percentile(row.get("adrp_pct_today"))
            if adrp_percentile is not None:
                target["adrp_percentile"] = adrp_percentile
        if target.get("atrp") in (None, ""):
            atrp = _number(row.get("atrp"))
            if atrp is None:
                atrp = _number(row.get("atrp_raw"))
            if atrp is not None:
                target["atrp"] = atrp

    for row in screen_rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        target = context.setdefault(symbol, {"symbol": symbol})
        target["in_screen_coverage_flag"] = 1
        rank = _integer(row.get("screen_rank"))
        if rank is not None:
            target["screen_rank"] = rank
        if target.get("adrp_percentile") in (None, ""):
            adrp_percentile = _normalize_percentile(
                row.get("adrp_directional_universe_percentile")
            )
            if adrp_percentile is not None:
                target["adrp_percentile"] = adrp_percentile
        if target.get("atrp") in (None, ""):
            atrp = _number(row.get("atrp"))
            if atrp is None:
                atrp = _number(row.get("atrp_raw"))
            if atrp is not None:
                target["atrp"] = atrp

    for row in context.values():
        in_upside = bool(_integer(row.get("in_upside_watchlist_flag")))
        in_screen = bool(_integer(row.get("in_screen_coverage_flag")))
        row["candidate_source"] = (
            "both" if in_upside and in_screen else "upside" if in_upside else "screen"
        )
    return context


def _build_catalyst_event_universe_rows(
    *,
    prediction_rows: Mapping[str, Mapping[str, Any]],
    current_rows: Mapping[str, Mapping[str, Any]],
    backwards_rows: Mapping[str, Mapping[str, Any]],
    history_rows: Mapping[str, Sequence[tuple[str, float, float | None]]],
    rank_context_rows: Mapping[str, Mapping[str, Any]],
    unified_rows: Mapping[str, Mapping[str, Any]],
    trade_plan_rows: Mapping[str, Mapping[str, Any]],
    earnings_priority_rows: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, prediction in prediction_rows.items():
        clean_symbol = str(symbol or "").strip()
        if not clean_symbol:
            continue
        current = dict(current_rows.get(clean_symbol) or {})
        merged: dict[str, Any] = {
            "symbol": clean_symbol,
            **rank_context_rows.get(clean_symbol, {}),
            **unified_rows.get(clean_symbol, {}),
            **trade_plan_rows.get(clean_symbol, {}),
            **current,
            **prediction,
            **backwards_rows.get(clean_symbol, {}),
        }
        earnings_context = earnings_priority_rows.get(clean_symbol, {})
        for key, value in earnings_context.items():
            if merged.get(key) in (None, ""):
                merged[key] = value

        earnings_days_until = _integer(merged.get("earnings_days_until"))
        if earnings_days_until is None:
            earnings_days_until = _integer(current.get("earnings_days_until"))
        if earnings_days_until is not None:
            merged["earnings_days_until"] = earnings_days_until

        merged["in_earnings_priority_lens_flag"] = int(
            clean_symbol in earnings_priority_rows
        )

        current_close = _number(current.get("current_close"))
        merged.update(
            _history_features(history_rows.get(clean_symbol, []), current_close)
        )

        expected_move_proxy = compute_expected_move_proxy_pct(
            merged, horizon_days=CATALYST_EVENT_MOVE_HORIZON_DAYS
        )
        merged["expected_move_proxy_pct"] = (
            expected_move_proxy if expected_move_proxy is not None else ""
        )
        merged["expected_move_horizon_days"] = CATALYST_EVENT_MOVE_HORIZON_DAYS
        rows.append(merged)

    return rows


def _fetch_dicts(
    connection: Any, query: str, parameters: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    cursor = connection.execute(query, list(parameters))
    columns = [str(item[0]) for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _table_columns(connection: Any, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return {str(row[1]) for row in rows if len(row) > 1}


def _build_all_fields_snapshot(
    rows: Sequence[Mapping[str, Any]], *, checkpoint: str
) -> dict[str, dict[str, Any]]:
    breadth_field = (
        "premarket_change_pct" if checkpoint == "premarket" else "current_day_pct"
    )
    breadth_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        industry = str(row.get("industry") or "").strip()
        market_cap = _number(row.get("market_cap_basic"))
        change = _number(row.get(breadth_field))
        if (
            not industry
            or market_cap is None
            or market_cap < 500_000_000
            or change is None
        ):
            continue
        breadth_counts[industry][1] += 1
        if change >= 3.0:
            breadth_counts[industry][0] += 1

    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        industry = str(row.get("industry") or "").strip()
        up_count, total_count = breadth_counts.get(industry, [0, 0])
        gap = (
            _number(row.get("premarket_gap_pct"))
            if checkpoint == "premarket"
            else _number(row.get("regular_gap_pct"))
        )
        output[symbol] = {
            **row,
            "gap_pct": gap,
            "industry_breadth_up_3pct": (
                round(100.0 * up_count / total_count, 2) if total_count else None
            ),
            "industry_breadth_member_count": total_count,
        }
    return output


def _load_current_all_fields(
    database_path: Path,
    *,
    checkpoint: str,
    same_day_scan_limit: int = 3,
) -> tuple[dict[str, dict[str, Any]], list[str], str, str]:
    if not 1 <= same_day_scan_limit <= 3:
        raise ValueError("same_day_scan_limit must be between 1 and 3")
    duckdb = _import_duckdb()
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        metadata_rows = _fetch_dicts(
            connection,
            "SELECT run_id, created_at_utc, run_date_utc FROM run_metadata "
            "ORDER BY created_at_utc DESC",
        )
        if not metadata_rows:
            raise ValueError(f"No run_metadata rows found in {database_path}")
        scan_day = str(metadata_rows[0].get("run_date_utc") or "")[:10]
        same_day_metadata = [
            row
            for row in metadata_rows
            if str(row.get("run_date_utc") or "")[:10] == scan_day
        ][:same_day_scan_limit]
        available_columns = _table_columns(connection, "all_fields_rows")
        full_run_id = resolve_latest_and_full_all_fields_run_ids(connection).get(
            "full_run_id"
        )
        snapshots: list[dict[str, dict[str, Any]]] = []
        for index, metadata in enumerate(same_day_metadata):
            run_id = str(metadata["run_id"])
            # Only the freshest run (index 0, possibly a focused-catalog
            # refresh) needs a fallback join; older same-day rows only feed
            # intraday deltas and are read as-is.
            use_fallback = (
                index == 0 and full_run_id is not None and full_run_id != run_id
            )
            if use_fallback:
                source_sql = (
                    "(SELECT * FROM all_fields_rows WHERE run_id = ?) AS latest "
                    "FULL OUTER JOIN "
                    "(SELECT * FROM all_fields_rows WHERE run_id = ?) AS full_run "
                    "ON latest.symbol = full_run.symbol"
                )

                def _col(name: str) -> str:
                    return f'COALESCE(latest."{name}", full_run."{name}")'

                query_params = [run_id, full_run_id]
            else:
                source_sql = (
                    "(SELECT * FROM all_fields_rows WHERE run_id = ?) AS latest"
                )

                def _col(name: str) -> str:
                    return f'latest."{name}"'

                query_params = [run_id]
            rows = _fetch_dicts(
                connection,
                f"SELECT {_col('symbol')} AS symbol, {_col('industry')} AS industry, "
                f"TRY_CAST({_col('close')} AS DOUBLE) AS current_close, "
                f"TRY_CAST({_col('change')} AS DOUBLE) AS current_day_pct, "
                f'TRY_CAST({_col("Perf.5D")} AS DOUBLE) AS perf_5d, '
                f'TRY_CAST({_col("Perf.1M")} AS DOUBLE) AS perf_1m, '
                f"TRY_CAST({_col('gap')} AS DOUBLE) AS regular_gap_pct, "
                f"TRY_CAST({_col('premarket_gap')} AS DOUBLE) AS premarket_gap_pct, "
                f"TRY_CAST({_col('premarket_change')} AS DOUBLE) AS premarket_change_pct, "
                f'TRY_CAST({_col("ADRP")} AS DOUBLE) AS adrp, '
                f'TRY_CAST({_col("ATRP")} AS DOUBLE) AS atrp, '
                + (
                    f'TRY_CAST({_col("earnings_days_until")} AS DOUBLE) AS earnings_days_until, '
                    if "earnings_days_until" in available_columns
                    else "CAST(NULL AS DOUBLE) AS earnings_days_until, "
                )
                + f"TRY_CAST({_col('market_cap_basic')} AS DOUBLE) AS market_cap_basic "
                + f"FROM {source_sql}",
                query_params,
            )
            snapshots.append(_build_all_fields_snapshot(rows, checkpoint=checkpoint))
    finally:
        connection.close()

    latest_snapshot = snapshots[0]
    run_ids = [str(row["run_id"]) for row in same_day_metadata]
    latest_created_at = str(same_day_metadata[0].get("created_at_utc") or "")
    for symbol, latest_row in latest_snapshot.items():
        symbol_snapshots = [
            snapshot[symbol] for snapshot in snapshots if symbol in snapshot
        ]
        oldest_row = symbol_snapshots[-1]

        def _delta(field: str) -> float | None:
            latest_value = _number(latest_row.get(field))
            oldest_value = _number(oldest_row.get(field))
            if (
                len(symbol_snapshots) < 2
                or latest_value is None
                or oldest_value is None
            ):
                return None
            return round(latest_value - oldest_value, 4)

        breadth_delta = _delta("industry_breadth_up_3pct")
        latest_row.update(
            {
                "same_day_scan_count": len(symbol_snapshots),
                "same_day_scan_run_ids": "|".join(run_ids),
                "same_day_scan_change_delta_pct": _delta("current_day_pct"),
                "same_day_scan_gap_delta_pct": _delta("gap_pct"),
                "same_day_scan_breadth_delta_pct": breadth_delta,
                "industry_breadth_expanding_flag": (
                    breadth_delta is not None and breadth_delta > 0
                ),
                "latest_all_fields_created_at_utc": latest_created_at,
            }
        )
    return latest_snapshot, run_ids, scan_day, latest_created_at


def _load_prediction_context(
    database_path: Path | None,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    if database_path is None:
        return {}, None
    duckdb = _import_duckdb()
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        run_row = connection.execute(
            "SELECT run_id FROM run_metadata ORDER BY created_at_utc DESC LIMIT 1"
        ).fetchone()
        if not run_row:
            return {}, None
        rows = _fetch_dicts(
            connection,
            "SELECT symbol, sleeve, conviction_score, rank_overall, "
            "manager_action_signal, breakout_conviction_tier, entry_readiness, "
            "size_tier, tape_pass FROM conviction_rankings WHERE run_id = ?",
            [run_row[0]],
        )
    finally:
        connection.close()
    return {
        str(row["symbol"]): {
            **row,
            "conviction_state": (
                f"{row.get('sleeve') or ''}|{row.get('entry_readiness') or ''}|"
                f"{row.get('size_tier') or ''}|tape={row.get('tape_pass')}"
            ),
        }
        for row in rows
    }, str(run_row[0])


def _load_backwards_context(
    database_path: Path | None,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    if database_path is None:
        return {}, None
    duckdb = _import_duckdb()
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        analysis_row = connection.execute(
            "SELECT backwards_analysis_id FROM backwards_analysis_runs "
            "ORDER BY created_at_utc DESC LIMIT 1"
        ).fetchone()
        rows = _fetch_dicts(
            connection,
            """
            SELECT symbol, current_direction AS backwards_weeks_direction,
                current_score AS backwards_weeks_score,
                score_delta AS backwards_weeks_score_delta,
                close_delta_pct AS backwards_anchor_close_delta_pct,
                anchor_name AS backwards_nearest_anchor
            FROM (
                SELECT d.*, ROW_NUMBER() OVER (
                    PARTITION BY d.symbol
                    ORDER BY a.offset_days_from_current ASC NULLS LAST
                ) AS row_number
                FROM backwards_consensus_horizon_deltas d
                JOIN backwards_analysis_anchors a
                  ON a.backwards_analysis_id = d.backwards_analysis_id
                 AND a.anchor_name = d.anchor_name
                WHERE d.horizon_name = 'weeks' AND d.in_current
            ) ranked
            WHERE row_number = 1
            """,
        )
    finally:
        connection.close()
    return (
        {str(row["symbol"]): row for row in rows},
        str(analysis_row[0]) if analysis_row else None,
    )


def _load_daily_history(
    all_fields_root: Path,
    symbols: Sequence[str],
    scan_day: str,
    *,
    lookback_days: int = 220,
) -> dict[str, list[tuple[str, float, float | None]]]:
    if not symbols:
        return {}
    scan_date = date.fromisoformat(scan_day)
    earliest_date = scan_date - timedelta(days=lookback_days)
    daily_databases: list[tuple[date, Path]] = []
    for day_dir in all_fields_root.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            source_date = datetime.strptime(day_dir.name, "%d_%m_%Y").date()
        except ValueError:
            continue
        if not earliest_date <= source_date < scan_date:
            continue
        database_path = _latest_path(day_dir.glob("*.duckdb"))
        if database_path is not None:
            daily_databases.append((source_date, database_path))
    daily_databases.sort()

    duckdb = _import_duckdb()
    placeholders = ", ".join("?" for _ in symbols)
    history: dict[str, list[tuple[str, float, float | None]]] = defaultdict(list)
    for source_date, database_path in daily_databases:
        connection = duckdb.connect(str(database_path), read_only=True)
        try:
            run_row = connection.execute(
                "SELECT run_id FROM run_metadata ORDER BY created_at_utc DESC LIMIT 1"
            ).fetchone()
            if not run_row:
                continue
            rows = connection.execute(
                f"""
                SELECT symbol, TRY_CAST(close AS DOUBLE), TRY_CAST(change AS DOUBLE)
                FROM all_fields_rows
                WHERE run_id = ? AND symbol IN ({placeholders})
                  AND TRY_CAST(close AS DOUBLE) IS NOT NULL
                """,
                [run_row[0], *symbols],
            ).fetchall()
        finally:
            connection.close()
        for symbol, close_price, daily_change in rows:
            history[str(symbol)].append(
                (
                    source_date.isoformat(),
                    float(close_price),
                    _number(daily_change),
                )
            )
    return dict(history)


def _history_features(
    prices: Sequence[tuple[str, float, float | None]], current_close: float | None
) -> dict[str, Any]:
    daily_changes = [
        daily_change for _, _, daily_change in prices if daily_change is not None
    ]
    consecutive_down_days = 0
    for value in reversed(daily_changes):
        if value >= 0:
            break
        consecutive_down_days += 1
    recent_closes = [close for _, close, _ in prices[-30:]]
    if current_close is not None:
        recent_closes.append(current_close)
    high = max(recent_closes) if recent_closes else None
    drawdown = (
        ((current_close / high) - 1.0) * 100.0
        if current_close is not None and high is not None and high > 0
        else None
    )
    return {
        "consecutive_down_days": consecutive_down_days,
        "prior_day_pct": daily_changes[-1] if daily_changes else None,
        "drawdown_from_high_pct": drawdown,
        "history_observation_count": len(prices),
    }


def _calibration_rows(
    history_by_symbol: Mapping[str, Sequence[tuple[str, float, float | None]]],
) -> list[dict[str, Any]]:
    outcomes: dict[str, list[float]] = defaultdict(list)
    for prices in history_by_symbol.values():
        daily_returns = [
            daily_change for _, _, daily_change in prices if daily_change is not None
        ]
        for index in range(1, len(daily_returns) - 1):
            next_day = daily_returns[index + 1]
            if daily_returns[index] >= 5.0:
                outcomes["after_day_ge_5pct"].append(next_day)
            if daily_returns[index] <= -5.0:
                outcomes["after_day_le_minus_5pct"].append(next_day)
            if daily_returns[index - 1] < 0 and daily_returns[index] < 0:
                outcomes["after_2_consecutive_down_days"].append(next_day)
    rows: list[dict[str, Any]] = []
    for rule_name in (
        "after_2_consecutive_down_days",
        "after_day_le_minus_5pct",
        "after_day_ge_5pct",
    ):
        values = outcomes.get(rule_name, [])
        rows.append(
            {
                "rule_name": rule_name,
                "sample_count": len(values),
                "next_day_mean_pct": (
                    round(statistics.fmean(values), 4) if values else ""
                ),
                "next_day_median_pct": (
                    round(statistics.median(values), 4) if values else ""
                ),
                "next_day_win_rate": (
                    round(sum(value > 0 for value in values) / len(values), 4)
                    if values
                    else ""
                ),
                "scoring_role": "calibration_only",
            }
        )
    return rows


def _write_duckdb(
    database_path: Path,
    *,
    timing_csv: Path,
    policy_csv: Path,
    calibration_csv: Path,
    catalyst_score_csv: Path,
    catalyst_policy_csv: Path,
) -> None:
    duckdb = _import_duckdb()
    connection = duckdb.connect(str(database_path))
    try:
        # Produced data contract:
        # - timing_* and action_policy_* remain the WHO-limited edge 50/100 timing book.
        # - catalyst_event_* is the all prediction-universe catalyst-event book.
        for table_name, csv_path in (
            ("timing_checkpoint_scores", timing_csv),
            ("action_policy_recommendations", policy_csv),
            ("timing_rule_calibration", calibration_csv),
            ("catalyst_event_policy_scores", catalyst_score_csv),
            ("catalyst_event_action_recommendations", catalyst_policy_csv),
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table_name}")
            connection.execute(
                f"CREATE TABLE {table_name} AS SELECT * FROM read_csv_auto(?)",
                [str(csv_path)],
            )
    finally:
        connection.close()


def run_market_timing_policy(
    *,
    checkpoint: str = "latest",
    edge_parent_dir: str | Path | None = None,
    all_fields_database: str | Path | None = None,
    prediction_database: str | Path | None = None,
    backwards_database: str | Path | None = None,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    same_day_scan_limit: int = 3,
    config: TimingPolicyConfig = TimingPolicyConfig(),
    catalyst_config: CatalystEventPolicyConfig = CatalystEventPolicyConfig(),
) -> dict[str, Any]:
    if checkpoint not in CHECKPOINTS:
        raise ValueError(f"checkpoint must be one of {CHECKPOINTS}")
    refs = resolve_timing_artifacts(
        edge_parent_dir=edge_parent_dir,
        all_fields_database=all_fields_database,
        prediction_database=prediction_database,
        backwards_database=backwards_database,
    )
    candidates = _load_edge_candidates(refs.edge_parent_dir, config)
    current_rows, all_fields_run_ids, scan_day, all_fields_created_at = (
        _load_current_all_fields(
            refs.all_fields_database,
            checkpoint=checkpoint,
            same_day_scan_limit=same_day_scan_limit,
        )
    )
    prediction, prediction_run_id = _load_prediction_context(refs.prediction_database)
    backwards, backwards_analysis_id = _load_backwards_context(refs.backwards_database)

    timing_history = _load_daily_history(
        refs.all_fields_database.parent.parent,
        list(candidates),
        scan_day,
    )

    normalized_rows: list[dict[str, Any]] = []
    for symbol, candidate in candidates.items():
        current = current_rows.get(symbol, {})
        current_close = _number(current.get("current_close"))
        normalized_rows.append(
            {
                **candidate,
                **current,
                **prediction.get(symbol, {}),
                **backwards.get(symbol, {}),
                **_history_features(timing_history.get(symbol, []), current_close),
            }
        )
    timing_rows, policy_rows = build_timing_policy_rows(
        normalized_rows,
        checkpoint=checkpoint,
        config=config,
    )
    calibration_rows = _calibration_rows(timing_history)

    rank_context_rows = _load_rank_context_rows(refs.edge_parent_dir)
    unified_rows = _load_edge_unified_rows(refs.edge_parent_dir)
    trade_plan_rows = _load_edge_trade_plan_rows(refs.edge_parent_dir)
    earnings_priority_rows = _load_earnings_priority_rows(refs.edge_parent_dir)

    catalyst_input_rows: list[dict[str, Any]] = []
    catalyst_score_rows: list[dict[str, Any]] = []
    catalyst_policy_rows: list[dict[str, Any]] = []
    if prediction:
        catalyst_history = _load_daily_history(
            refs.all_fields_database.parent.parent,
            sorted(prediction.keys()),
            scan_day,
        )
        catalyst_input_rows = _build_catalyst_event_universe_rows(
            prediction_rows=prediction,
            current_rows=current_rows,
            backwards_rows=backwards,
            history_rows=catalyst_history,
            rank_context_rows=rank_context_rows,
            unified_rows=unified_rows,
            trade_plan_rows=trade_plan_rows,
            earnings_priority_rows=earnings_priority_rows,
        )
        catalyst_score_rows, catalyst_policy_rows = build_catalyst_event_rows(
            catalyst_input_rows,
            checkpoint=checkpoint,
            config=catalyst_config,
        )

    created_at = datetime.now(tz=timezone.utc)
    run_id = (
        f"market_timing_policy_{created_at.strftime('%Y%m%d_%H%M')}_utc_"
        f"{uuid.uuid4().hex[:8]}"
    )
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    timing_csv = run_dir / "timing_checkpoint_scores.csv"
    policy_csv = run_dir / "action_policy_recommendations.csv"
    calibration_csv = run_dir / "timing_rule_calibration.csv"
    catalyst_score_csv = run_dir / "catalyst_event_policy_scores.csv"
    catalyst_policy_csv = run_dir / "catalyst_event_action_recommendations.csv"
    database_path = run_dir / "market_timing_policy.duckdb"
    manifest_path = run_dir / "market_timing_policy_manifest.json"

    _write_csv(timing_csv, timing_rows)
    _write_csv(policy_csv, policy_rows)
    _write_csv(calibration_csv, calibration_rows)
    _write_csv(catalyst_score_csv, catalyst_score_rows)
    _write_csv(catalyst_policy_csv, catalyst_policy_rows)
    _write_duckdb(
        database_path,
        timing_csv=timing_csv,
        policy_csv=policy_csv,
        calibration_csv=calibration_csv,
        catalyst_score_csv=catalyst_score_csv,
        catalyst_policy_csv=catalyst_policy_csv,
    )

    action_counts: dict[str, int] = defaultdict(int)
    for row in policy_rows:
        action_counts[str(row["action"])] += 1

    catalyst_event_counts: dict[str, int] = defaultdict(int)
    for row in catalyst_policy_rows:
        catalyst_event_counts[str(row["event_action"])] += 1

    candidate_source_counts: dict[str, int] = defaultdict(int)
    for candidate in candidates.values():
        candidate_source_counts[
            str(candidate.get("candidate_source") or "unknown")
        ] += 1

    manifest = {
        "run_id": run_id,
        "created_at_utc": created_at.isoformat(),
        "checkpoint": checkpoint,
        "scan_day": scan_day,
        "all_fields_run_id": all_fields_run_ids[0],
        "all_fields_run_ids_used": all_fields_run_ids,
        "all_fields_same_day_scan_count": len(all_fields_run_ids),
        "same_day_scan_limit_requested": same_day_scan_limit,
        "all_fields_latest_created_at_utc": all_fields_created_at,
        "prediction_run_id": prediction_run_id,
        "backwards_analysis_id": backwards_analysis_id,
        "edge_parent_run_id": refs.edge_parent_dir.name,
        "edge_scan_day": refs.edge_scan_day,
        "candidate_count": len(candidates),
        "candidate_source_counts": dict(candidate_source_counts),
        "action_counts": dict(action_counts),
        "catalyst_event_candidate_count": len(catalyst_input_rows),
        "catalyst_event_counts": dict(catalyst_event_counts),
        "config": asdict(config),
        "catalyst_config": asdict(catalyst_config),
        "inputs": {
            field: str(value) if value is not None else None
            for field, value in asdict(refs).items()
        },
        "outputs": {
            "timing_csv": str(timing_csv),
            "policy_csv": str(policy_csv),
            "calibration_csv": str(calibration_csv),
            "catalyst_score_csv": str(catalyst_score_csv),
            "catalyst_policy_csv": str(catalyst_policy_csv),
            "database_path": str(database_path),
        },
        "produced_data": {
            "timing_book": "WHO-limited timing/action table for edge upside top-50 and screen top-100.",
            "catalyst_event_book": "All prediction-universe ticker catalyst-event policy rows from edge+prediction+all-fields context.",
        },
        "causality_notes": [
            "Backwards-analysis fields are evidence-only and do not change timing_score.",
            "Calibration outcomes are reported separately and do not change live actions.",
            "Existing conviction, safety, size, and tape fields are context, not vetoes.",
            "Catalyst-event labels are a separate sleeve and do not alter timing ENTER_SMALL/ENTER_PROBE actions.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {**manifest, "run_dir": str(run_dir), "manifest_path": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a latest-data timing score and normalized-risk policy book."
    )
    parser.add_argument("--checkpoint", choices=CHECKPOINTS, default="latest")
    parser.add_argument("--edge-parent-dir")
    parser.add_argument("--all-fields-database")
    parser.add_argument("--prediction-database")
    parser.add_argument("--backwards-database")
    parser.add_argument("--same-day-scan-limit", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    result = run_market_timing_policy(
        checkpoint=args.checkpoint,
        edge_parent_dir=args.edge_parent_dir,
        all_fields_database=args.all_fields_database,
        prediction_database=args.prediction_database,
        backwards_database=args.backwards_database,
        output_root=args.output_root,
        same_day_scan_limit=args.same_day_scan_limit,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
