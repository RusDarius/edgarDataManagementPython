"""Skeptical pattern-discovery tooling for move-prediction research.

Joins three data layers described in the active-manager plan:
  A — weekly move_prediction_*.duckdb (profile/consensus scores)
  B — tradingview_all_fields_*.duckdb (full TradingView catalog)
  C — historical_prediction_analysis.duckdb (calibration over time)

SQL templates live in:
    sql_connections_space/move_prediction_pattern_discovery.session.sql
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREDICTION_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "prediction_analysis"
    / "duckdb_runs"
)
DEFAULT_ALL_FIELDS_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "trading_view_all_fields_data"
)
DEFAULT_HISTORY_ROOT = (
    DEFAULT_PREDICTION_ROOT / "historical_prediction_analysis"
)
DEFAULT_QUERY_SESSION_PATH = (
    PROJECT_ROOT
    / "sql_connections_space"
    / "move_prediction_pattern_discovery.session.sql"
)

def _symbol_join(left_alias: str, right_alias: str = "a") -> str:
    return f"""(
        {left_alias}.symbol = {right_alias}.symbol
        OR {left_alias}.symbol LIKE '%:' || {right_alias}.symbol
        OR {right_alias}.symbol LIKE '%:' || {left_alias}.symbol
    )"""


@dataclass(frozen=True)
class PatternDiscoveryPaths:
    prediction_db: Path
    all_fields_db: Path | None = None
    history_db: Path | None = None
    output_dir: Path | None = None


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _read_sql_blocks(session_path: Path) -> dict[str, str]:
    text = session_path.read_text(encoding="utf-8")
    blocks: dict[str, str] = {}
    for match in re.finditer(
        r"--\s*@block\s+(\w+)\s*\n(.*?)(?=\n--\s*@block|\Z)",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        blocks[match.group(1)] = match.group(2).strip()
    return blocks


def _latest_file(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.rglob(pattern), key=lambda path: path.stat().st_mtime)
    return matches[-1] if matches else None


def discover_latest_prediction_db(
    prediction_root: Path = DEFAULT_PREDICTION_ROOT,
) -> Path | None:
    return _latest_file(prediction_root, "move_prediction_*.duckdb")


def discover_latest_all_fields_db(
    all_fields_root: Path = DEFAULT_ALL_FIELDS_ROOT,
) -> Path | None:
    return _latest_file(all_fields_root, "tradingview_all_fields_*.duckdb")


def discover_latest_history_db(
    history_root: Path = DEFAULT_HISTORY_ROOT,
) -> Path | None:
    return _latest_file(history_root, "historical_prediction_analysis.duckdb")


def _query_duckdb(database_path: Path, sql: str) -> list[dict[str, Any]]:
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        relation = connection.execute(sql)
        columns = [col[0] for col in relation.description]
        return [dict(zip(columns, row)) for row in relation.fetchall()]
    finally:
        connection.close()


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def run_false_positive_field_autopsy(
    paths: PatternDiscoveryPaths,
    *,
    profile_name: str = "breakout_long",
    horizon_name: str = "weeks",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Join score-improved but price-fell cohorts with raw all-fields features."""
    if paths.all_fields_db is None:
        raise ValueError("all_fields_db is required for false-positive autopsy")
    if paths.history_db is None:
        raise ValueError("history_db is required for false-positive autopsy")

    symbol_join = _symbol_join("fp", "a")
    sql = f"""
    ATTACH '{paths.history_db.as_posix()}' AS hist (READ_ONLY);
    ATTACH '{paths.all_fields_db.as_posix()}' AS fields (READ_ONLY);

    WITH false_pos AS (
        SELECT profile_name, symbol, score_delta_total, close_return_pct_total
        FROM hist.vw_profile_horizon_progression_core
        WHERE profile_name = '{profile_name}'
          AND score_delta_total > 0
          AND close_return_pct_total < 0
    ),
    latest_run AS (
        SELECT run_id
        FROM run_metadata
        ORDER BY created_at_utc DESC
        LIMIT 1
    )
    SELECT fp.profile_name,
        fp.symbol,
        fp.score_delta_total,
        fp.close_return_pct_total,
        h.score,
        h.risk_adjusted_score,
        a."RSI",
        a."ChaikinMoneyFlow",
        a."BBPower",
        a."DonchCh20.Upper",
        a."DonchCh20.Lower",
        a."P.SAR",
        a."total_debt_to_ebitda_fq",
        a."price_target_high",
        a."price_target_low"
    FROM false_pos fp
    JOIN latest_run lr ON TRUE
    LEFT JOIN profile_horizon_scores h
        ON h.run_id = lr.run_id
        AND h.profile_name = fp.profile_name
        AND h.symbol = fp.symbol
        AND h.horizon_name = '{horizon_name}'
    LEFT JOIN fields.all_fields_rows a
        ON {symbol_join}
    ORDER BY fp.score_delta_total DESC
    LIMIT {int(limit)}
    """
    return _query_duckdb(paths.prediction_db, sql)


def run_profile_cohort_field_comparison(
    paths: PatternDiscoveryPaths,
    *,
    profile_name: str = "breakout_long",
    horizon_name: str = "weeks",
    field_name: str = "ChaikinMoneyFlow",
    top_decile: float = 0.10,
    bottom_decile: float = 0.10,
) -> list[dict[str, Any]]:
    """Compare a raw catalog field between top and bottom profile-score deciles."""
    if paths.all_fields_db is None:
        raise ValueError("all_fields_db is required for cohort field comparison")

    sql = f"""
    ATTACH '{paths.all_fields_db.as_posix()}' AS fields (READ_ONLY);

    WITH latest_run AS (
        SELECT run_id
        FROM run_metadata
        ORDER BY created_at_utc DESC
        LIMIT 1
    ),
    leaders AS (
        SELECT h.symbol, h.score,
            NTILE(10) OVER (ORDER BY h.score DESC) AS score_decile
        FROM profile_horizon_scores h
        JOIN latest_run lr ON h.run_id = lr.run_id
        WHERE h.profile_name = '{profile_name}'
          AND h.horizon_name = '{horizon_name}'
          AND h.score IS NOT NULL
    ),
    cohort AS (
        SELECT
            CASE
                WHEN score_decile <= CAST({top_decile} * 10 AS INTEGER) THEN 'top_decile'
                WHEN score_decile > CAST((1 - {bottom_decile}) * 10 AS INTEGER) THEN 'bottom_decile'
            END AS cohort_label,
            l.symbol,
            l.score,
            TRY_CAST(a."{field_name}" AS DOUBLE) AS field_value
        FROM leaders l
        LEFT JOIN fields.all_fields_rows a ON {_symbol_join("l", "a")}
        WHERE cohort_label IS NOT NULL
    )
    SELECT cohort_label,
        COUNT(*) AS symbol_count,
        AVG(score) AS avg_profile_score,
        AVG(field_value) AS avg_field_value,
        MEDIAN(field_value) AS median_field_value,
        STDDEV_POP(field_value) AS stddev_field_value
    FROM cohort
    GROUP BY cohort_label
    ORDER BY cohort_label
    """
    return _query_duckdb(paths.prediction_db, sql)


def run_prediction_all_fields_join_preview(
    paths: PatternDiscoveryPaths,
    *,
    profile_name: str = "breakout_long",
    horizon_name: str = "weeks",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Preview join of latest prediction leaders with all-fields confirmation columns."""
    if paths.all_fields_db is None:
        raise ValueError("all_fields_db is required for join preview")

    sql = f"""
    ATTACH '{paths.all_fields_db.as_posix()}' AS fields (READ_ONLY);

    WITH latest_run AS (
        SELECT run_id
        FROM run_metadata
        ORDER BY created_at_utc DESC
        LIMIT 1
    )
    SELECT h.symbol,
        h.company,
        h.score,
        h.risk_adjusted_score,
        h.manager_action_signal,
        a."ChaikinMoneyFlow",
        a."BBPower",
        a."DonchCh20.Upper",
        a."DonchCh20.Lower",
        a."P.SAR",
        a."HullMA9",
        a."RSI",
        a."Stoch.RSI.K"
    FROM profile_horizon_scores h
    JOIN latest_run lr ON h.run_id = lr.run_id
    LEFT JOIN fields.all_fields_rows a ON {_symbol_join("h", "a")}
    WHERE h.profile_name = '{profile_name}'
      AND h.horizon_name = '{horizon_name}'
    ORDER BY h.risk_adjusted_score DESC NULLS LAST
    LIMIT {int(limit)}
    """
    return _query_duckdb(paths.prediction_db, sql)


def run_weekly_calibration_report(
    paths: PatternDiscoveryPaths,
    *,
    profile_name: str | None = None,
    min_snapshots: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Summarize calibration gates from the history aggregation database."""
    if paths.history_db is None:
        raise ValueError("history_db is required for weekly calibration report")

    profile_filter = ""
    if profile_name:
        profile_filter = f"WHERE profile_name = '{profile_name}'"

    alignment_sql = f"""
    SELECT profile_name,
        horizon_name,
        symbol_count,
        aligned_positive_count,
        false_positive_count,
        ROUND(
            aligned_positive_count * 1.0 / NULLIF(symbol_count, 0),
            4
        ) AS aligned_positive_ratio,
        ROUND(score_price_corr, 4) AS score_price_corr
    FROM vw_profile_horizon_alignment_stats
    {profile_filter}
    ORDER BY aligned_positive_ratio DESC
    """
    persistence_sql = f"""
    SELECT profile_name,
        symbol,
        horizon_name,
        presence_ratio,
        snapshots_seen,
        close_return_pct_total
    FROM vw_profile_horizon_progression_core
    WHERE presence_ratio >= 0.5
      AND snapshots_seen >= {int(min_snapshots)}
    ORDER BY close_return_pct_total DESC
    LIMIT 100
    """
    false_positive_sql = f"""
    SELECT profile_name,
        horizon_name,
        COUNT(*) AS false_positive_count
    FROM vw_profile_horizon_progression_core
    WHERE score_delta_total > 0
      AND close_return_pct_total < 0
    GROUP BY profile_name, horizon_name
    ORDER BY false_positive_count DESC
    """
    return {
        "alignment_stats": _query_duckdb(paths.history_db, alignment_sql),
        "persistent_winners": _query_duckdb(paths.history_db, persistence_sql),
        "false_positive_counts": _query_duckdb(paths.history_db, false_positive_sql),
    }


def run_pattern_discovery_suite(
    *,
    prediction_db: str | Path | None = None,
    all_fields_db: str | Path | None = None,
    history_db: str | Path | None = None,
    output_dir: str | Path | None = None,
    profile_name: str = "breakout_long",
) -> dict[str, Any]:
    """Run the standard Friday research export bundle."""
    resolved_prediction = Path(
        prediction_db or discover_latest_prediction_db() or ""
    )
    if not resolved_prediction.exists():
        raise FileNotFoundError(
            f"Prediction DuckDB not found: {resolved_prediction}"
        )

    resolved_all_fields = (
        Path(all_fields_db)
        if all_fields_db
        else discover_latest_all_fields_db()
    )
    resolved_history = (
        Path(history_db) if history_db else discover_latest_history_db()
    )
    timestamp = _utc_timestamp()
    resolved_output = Path(
        output_dir
        or (
            resolved_prediction.parent
            / "pattern_discovery"
            / timestamp
        )
    )
    resolved_output.mkdir(parents=True, exist_ok=True)

    paths = PatternDiscoveryPaths(
        prediction_db=resolved_prediction,
        all_fields_db=resolved_all_fields,
        history_db=resolved_history,
        output_dir=resolved_output,
    )

    exported: dict[str, Any] = {
        "prediction_db": str(resolved_prediction),
        "all_fields_db": str(resolved_all_fields) if resolved_all_fields else None,
        "history_db": str(resolved_history) if resolved_history else None,
        "output_dir": str(resolved_output),
    }

    join_rows = run_prediction_all_fields_join_preview(
        paths, profile_name=profile_name
    )
    exported["join_preview_csv"] = str(
        _write_csv(resolved_output / "join_preview.csv", join_rows)
    )

    comparison_rows = run_profile_cohort_field_comparison(
        paths, profile_name=profile_name, field_name="ChaikinMoneyFlow"
    )
    exported["cohort_field_comparison_csv"] = str(
        _write_csv(
            resolved_output / "cohort_field_comparison__ChaikinMoneyFlow.csv",
            comparison_rows,
        )
    )

    if resolved_history and resolved_history.exists():
        calibration = run_weekly_calibration_report(paths)
        for key, rows in calibration.items():
            exported[f"{key}_csv"] = str(
                _write_csv(resolved_output / f"{key}.csv", rows)
            )
        if resolved_all_fields and resolved_all_fields.exists():
            autopsy_rows = run_false_positive_field_autopsy(paths, profile_name=profile_name)
            exported["false_positive_autopsy_csv"] = str(
                _write_csv(
                    resolved_output / "false_positive_field_autopsy.csv",
                    autopsy_rows,
                )
            )

    overview_path = resolved_output / "pattern_discovery_overview.log"
    overview_lines = [
        "TradingView move-prediction pattern discovery export",
        f"prediction_db={resolved_prediction}",
        f"all_fields_db={resolved_all_fields}",
        f"history_db={resolved_history}",
        f"profile_name={profile_name}",
        "",
        "Exports:",
        *(f"  - {key}: {value}" for key, value in exported.items() if key.endswith("_csv")),
    ]
    overview_path.write_text("\n".join(overview_lines), encoding="utf-8")
    exported["overview_log"] = str(overview_path)
    return exported
