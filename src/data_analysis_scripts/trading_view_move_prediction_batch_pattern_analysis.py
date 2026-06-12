"""Batch pattern-analysis workflow for prediction_analysis DuckDB runs.

Focuses on weekly ``move_prediction_*.duckdb`` stores only (no all-fields CSV/DuckDB).
Designed for the active-manager research loop: inventory full-coverage runs,
export per-run leader/action splits, then aggregate multi-week calibration.

Typical first pass (weeks 13–24, one full-coverage run per calendar day)::

    from data_analysis_scripts.trading_view_move_prediction_batch_pattern_analysis import (
        run_batch_prediction_pattern_analysis,
    )

    result = run_batch_prediction_pattern_analysis(
        iso_year=2026,
        start_week=13,
        end_week=24,
    )
    print(result["overview_log"])
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

from data_analysis_scripts.trading_view_move_prediction_history_aggregator import (
    HistoricalAggregationBuildOptions,
    build_move_prediction_history_duckdb_inputs_from_week_folders,
    run_move_prediction_history_aggregation_duckdb_incremental,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    DEFAULT_MOVE_PREDICTION_PROFILE_SUITE,
)
from db.trading_view_move_prediction_duckdb import open_move_prediction_duckdb_connection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREDICTION_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "prediction_analysis"
)
DEFAULT_DUCKDB_RUNS_ROOT = DEFAULT_PREDICTION_ROOT / "duckdb_runs"
DEFAULT_BATCH_OUTPUT_ROOT = DEFAULT_PREDICTION_ROOT / "batch_pattern_analysis"

FULL_ANALYSIS_SUITE_NAME = "tradingview_move_prediction_full_analysis_duckdb"
DEFAULT_PRIMARY_PROFILE = "breakout_long"
DEFAULT_HORIZON_NAME = "weeks"

CALIBRATION_GATES = {
    "aligned_positive_ratio_min": 0.55,
    "conditional_hit_rate_min": 0.65,  # Alternative: hit rate among score-improved symbols only
    "score_price_corr_min": 0.15,
    "false_positive_rate_max": 0.35,
    "persistence_presence_ratio_min": 0.5,
    "persistence_min_snapshots": 2,
}

# Tuned for a 32 GB RAM / high-core desktop (e.g. i9-14900KF).
DEFAULT_BATCH_MEMORY_GB = 28.0
DEFAULT_BATCH_DUCKDB_THREADS = 20
DEFAULT_BATCH_PARALLEL_WORKERS = 12
DEFAULT_BATCH_ATTACH_BATCH_SIZE = 6
DEFAULT_EXPORT_PARALLEL_WORKERS = 4
DEFAULT_BATCH_INCLUDE_PROFILES: tuple[str, ...] = tuple(
    DEFAULT_MOVE_PREDICTION_PROFILE_SUITE
)

RUN_INVENTORY_SQL = """
WITH raw_counts AS (
    SELECT run_id, COUNT(*) AS raw_rows
    FROM raw_scan_rows
    GROUP BY run_id
),
profile_counts AS (
    SELECT run_id,
        COUNT(DISTINCT profile_name) AS profiles_scored,
        COUNT(DISTINCT horizon_name) AS profile_horizons,
        COUNT(*) AS profile_horizon_rows
    FROM profile_horizon_scores
    GROUP BY run_id
),
consensus_counts AS (
    SELECT run_id,
        COUNT(DISTINCT symbol) AS consensus_symbols,
        COUNT(DISTINCT horizon_name) AS consensus_horizons,
        COUNT(*) AS consensus_horizon_rows
    FROM consensus_horizon_scores
    GROUP BY run_id
),
report_counts AS (
    SELECT run_id, COUNT(*) AS report_count
    FROM generated_reports
    GROUP BY run_id
)
SELECT m.run_id,
    CAST(m.created_at_utc AS DATE) AS run_date_utc,
    DATE_TRUNC('minute', m.created_at_utc) AS run_minute_utc,
    m.created_at_utc,
    m.iso_year,
    m.iso_week,
    m.suite_name,
    m.scan_data_count,
    COALESCE(raw_counts.raw_rows, 0) AS raw_rows,
    COALESCE(profile_counts.profiles_scored, 0) AS profiles_scored,
    COALESCE(profile_counts.profile_horizons, 0) AS profile_horizons,
    COALESCE(profile_counts.profile_horizon_rows, 0) AS profile_horizon_rows,
    COALESCE(consensus_counts.consensus_symbols, 0) AS consensus_symbols,
    COALESCE(consensus_counts.consensus_horizons, 0) AS consensus_horizons,
    COALESCE(consensus_counts.consensus_horizon_rows, 0) AS consensus_horizon_rows,
    COALESCE(report_counts.report_count, 0) AS report_count,
    m.min_market_cap_usd,
    m.max_market_cap_usd,
    m.profile_names_json,
    m.notes
FROM run_metadata m
LEFT JOIN raw_counts ON m.run_id = raw_counts.run_id
LEFT JOIN profile_counts ON m.run_id = profile_counts.run_id
LEFT JOIN consensus_counts ON m.run_id = consensus_counts.run_id
LEFT JOIN report_counts ON m.run_id = report_counts.run_id
ORDER BY m.created_at_utc DESC NULLS LAST, m.run_id
"""


@dataclass(frozen=True)
class BatchExecutionConfig:
    """Resource knobs for batch inventory/export and history aggregation."""

    memory_gb: float = DEFAULT_BATCH_MEMORY_GB
    duckdb_threads: int = DEFAULT_BATCH_DUCKDB_THREADS
    history_parallel_workers: int = DEFAULT_BATCH_PARALLEL_WORKERS
    history_attach_batch_size: int = DEFAULT_BATCH_ATTACH_BATCH_SIZE
    export_parallel_workers: int = DEFAULT_EXPORT_PARALLEL_WORKERS
    duckdb_read_threads: int = 4


def resolve_batch_execution_config(
    *,
    memory_gb: float | None = None,
    duckdb_threads: int | None = None,
    history_parallel_workers: int | None = None,
    history_attach_batch_size: int | None = None,
    export_parallel_workers: int | None = None,
    duckdb_read_threads: int | None = None,
) -> BatchExecutionConfig:
    cpu_count = os.cpu_count() or 8
    return BatchExecutionConfig(
        memory_gb=memory_gb if memory_gb is not None else DEFAULT_BATCH_MEMORY_GB,
        duckdb_threads=(
            duckdb_threads
            if duckdb_threads is not None
            else min(DEFAULT_BATCH_DUCKDB_THREADS, cpu_count)
        ),
        history_parallel_workers=(
            history_parallel_workers
            if history_parallel_workers is not None
            else min(DEFAULT_BATCH_PARALLEL_WORKERS, max(4, cpu_count // 2))
        ),
        history_attach_batch_size=(
            history_attach_batch_size
            if history_attach_batch_size is not None
            else DEFAULT_BATCH_ATTACH_BATCH_SIZE
        ),
        export_parallel_workers=(
            export_parallel_workers
            if export_parallel_workers is not None
            else min(DEFAULT_EXPORT_PARALLEL_WORKERS, max(2, cpu_count // 4))
        ),
        duckdb_read_threads=(
            duckdb_read_threads
            if duckdb_read_threads is not None
            else min(8, max(2, cpu_count // 4))
        ),
    )


@dataclass(frozen=True)
class SelectedRun:
    run_id: str
    run_date_utc: date
    database_path: str
    week_folder: str
    scan_data_count: int
    profiles_scored: int
    expected_profiles: int
    selection_reason: str


@dataclass(frozen=True)
class WeeklySelectionGroup:
    database_path: Path
    week_folder: str
    runs: tuple[SelectedRun, ...]


@dataclass(frozen=True)
class BatchHistoryPlan:
    chunk_index: int
    chunk_total: int
    week_start: int
    week_end: int
    weekly_databases: tuple[Path, ...]


def _format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _progress_bar(completed: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[{'?' * width}]"
    filled = int(width * min(1.0, completed / total))
    return f"[{'=' * filled}{' ' * (width - filled)}] {completed}/{total}"


class BatchProgressReporter:
    """Emit real-time batch progress to stderr (Git Bash / TTY friendly)."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        stream: TextIO | None = None,
        label: str = "batch",
    ) -> None:
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self.label = label
        self._use_color = (
            enabled
            and hasattr(self.stream, "isatty")
            and self.stream.isatty()
        )
        self._lock = threading.Lock()
        self._week_times: list[float] = []

    def _emit(
        self,
        message: str,
        *,
        end: str = "\n",
        color: str | None = None,
        clear_line: bool = False,
    ) -> None:
        if not self.enabled:
            return
        text = message
        if color and self._use_color:
            text = f"\033[{color}m{message}\033[0m"
        if clear_line:
            text = f"{text:<100}"
        with self._lock:
            self.stream.write(text + end)
            self.stream.flush()

    def banner(self, message: str) -> None:
        self._emit(f"[{self.label}] {message}", color="1;36")

    def phase(self, message: str) -> None:
        self._emit(f"[{self.label}] {message}", color="1;34")

    def week_started(
        self,
        *,
        week_index: int,
        week_total: int,
        week_folder: str,
        database_name: str,
    ) -> None:
        bar = _progress_bar(week_index - 1, week_total)
        self._emit(
            f"[{self.label}] {bar} starting {week_folder} ({database_name})",
            color="36",
        )

    def week_step(self, week_folder: str, step: str, detail: str = "") -> None:
        suffix = f" {detail}" if detail else ""
        self._emit(f"[{self.label}]   {week_folder}: {step}{suffix}")

    def week_export_progress(
        self,
        *,
        week_folder: str,
        completed_runs: int,
        total_runs: int,
        run_date: str | None = None,
    ) -> None:
        if total_runs <= 0:
            return
        run_bar = _progress_bar(completed_runs, total_runs, width=12)
        run_hint = f" last={run_date}" if run_date else ""
        in_place = completed_runs < total_runs
        self._emit(
            f"[{self.label}]   {week_folder}: exports {run_bar}{run_hint}",
            end="\r" if in_place else "\n",
            color="33",
            clear_line=in_place,
        )

    def week_finished(
        self,
        *,
        week_index: int,
        week_total: int,
        week_folder: str,
        inventory_runs: int,
        selected_runs: int,
        elapsed_seconds: float,
        inventory_seconds: float,
        export_seconds: float,
    ) -> None:
        self._week_times.append(elapsed_seconds)
        bar = _progress_bar(week_index, week_total)
        remaining = week_total - week_index
        eta = (
            _format_duration(
                (sum(self._week_times) / len(self._week_times)) * remaining
            )
            if remaining > 0
            else "0s"
        )
        self._emit(
            f"[{self.label}] {bar} done {week_folder} | "
            f"inventory={inventory_runs} selected={selected_runs} | "
            f"timing inventory={_format_duration(inventory_seconds)} "
            f"export={_format_duration(export_seconds)} "
            f"week={_format_duration(elapsed_seconds)} | "
            f"ETA remaining ~{eta}",
            color="32",
        )

    def phase_timing(self, phase: str, elapsed_seconds: float) -> None:
        self._emit(
            f"[{self.label}] {phase} finished in {_format_duration(elapsed_seconds)}",
            color="1;32",
        )

    def summary(self, timing_seconds: Mapping[str, float]) -> None:
        if not self.enabled:
            return
        self._emit(f"[{self.label}] === timing summary ===", color="1;36")
        for phase, elapsed in timing_seconds.items():
            self._emit(f"[{self.label}]   {phase}: {_format_duration(elapsed)}")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
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


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return path


def _write_text(path: Path, lines: Sequence[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _expected_profile_count(profile_names_json: str | None) -> int:
    if not profile_names_json:
        return 0
    try:
        parsed = json.loads(profile_names_json)
    except json.JSONDecodeError:
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def _week_folder_name(iso_week: int) -> str:
    return f"week={iso_week:02d}"


def _resolve_iso_year_root(
    iso_year: int,
    duckdb_runs_root: Path | None = None,
) -> Path:
    root = duckdb_runs_root or DEFAULT_DUCKDB_RUNS_ROOT
    year_root = root / f"iso_year={iso_year}"
    if not year_root.is_dir():
        raise FileNotFoundError(f"ISO year folder not found: {year_root}")
    return year_root


def _resolve_end_week(end_week: int | None) -> int:
    if end_week is not None:
        return end_week
    return datetime.now(timezone.utc).isocalendar().week


def discover_weekly_prediction_databases(
    *,
    iso_year: int = 2026,
    start_week: int = 13,
    end_week: int | None = None,
    duckdb_runs_root: str | Path | None = None,
) -> list[Path]:
    """Resolve weekly move_prediction DuckDB files for an ISO week range."""
    resolved_end_week = _resolve_end_week(end_week)
    if start_week > resolved_end_week:
        raise ValueError(
            f"start_week ({start_week}) cannot be greater than end_week ({resolved_end_week})"
        )
    year_root = _resolve_iso_year_root(iso_year, Path(duckdb_runs_root) if duckdb_runs_root else None)
    folder_names = [_week_folder_name(week_number) for week_number in range(start_week, resolved_end_week + 1)]
    return build_move_prediction_history_duckdb_inputs_from_week_folders(
        base_dir=year_root,
        folder_names=folder_names,
    )


def _fetch_duckdb_rows(
    database_path: str | Path,
    sql: str,
    *,
    threads: int | None = None,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    if connection is not None:
        cursor = connection.execute(sql)
        if cursor.description is None:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    with open_move_prediction_duckdb_connection(
        database_path,
        read_only=True,
        threads=threads,
        enable_object_cache=True,
    ) as conn:
        return _fetch_duckdb_rows(database_path, sql, connection=conn)


def inventory_runs_in_database(
    database_path: str | Path,
    *,
    duckdb_read_threads: int | None = None,
) -> list[dict[str, Any]]:
    """Return completeness inventory rows for one weekly prediction DuckDB."""
    rows = _fetch_duckdb_rows(
        database_path,
        RUN_INVENTORY_SQL,
        threads=duckdb_read_threads,
    )
    for row in rows:
        row["expected_profiles"] = _expected_profile_count(row.get("profile_names_json"))
        row["raw_rows_match"] = row.get("raw_rows") == row.get("scan_data_count")
        row["is_full_coverage"] = is_full_coverage_run(row)
    return rows


def is_full_coverage_run(
    row: Mapping[str, Any],
    *,
    min_scan_data_count: int = 3000,
    require_suite_name: str = FULL_ANALYSIS_SUITE_NAME,
) -> bool:
    """True when a run has a complete profile + consensus write for its suite."""
    expected_profiles = int(row.get("expected_profiles") or 0)
    profiles_scored = int(row.get("profiles_scored") or 0)
    scan_data_count = int(row.get("scan_data_count") or 0)
    raw_rows = int(row.get("raw_rows") or 0)
    consensus_symbols = int(row.get("consensus_symbols") or 0)
    suite_name = row.get("suite_name")
    return (
        suite_name == require_suite_name
        and scan_data_count >= min_scan_data_count
        and raw_rows == scan_data_count
        and expected_profiles > 0
        and profiles_scored >= expected_profiles
        and consensus_symbols > 0
        and int(row.get("consensus_horizons") or 0) >= 4
    )


def select_full_coverage_runs_per_day(
    inventory_rows: Sequence[Mapping[str, Any]],
    *,
    database_path: str | Path,
    week_folder: str,
    min_scan_data_count: int = 3000,
    require_suite_name: str = FULL_ANALYSIS_SUITE_NAME,
) -> list[SelectedRun]:
    """Pick one best full-coverage run per calendar day within a weekly database."""
    candidates_by_day: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    for row in inventory_rows:
        if not is_full_coverage_run(
            row,
            min_scan_data_count=min_scan_data_count,
            require_suite_name=require_suite_name,
        ):
            continue
        run_date = row.get("run_date_utc")
        if run_date is None:
            continue
        if isinstance(run_date, str):
            run_date = date.fromisoformat(run_date)
        candidates_by_day[run_date].append(row)

    selected: list[SelectedRun] = []
    for run_date in sorted(candidates_by_day):
        day_rows = sorted(
            candidates_by_day[run_date],
            key=lambda row: (
                int(row.get("scan_data_count") or 0),
                str(row.get("created_at_utc") or ""),
            ),
            reverse=True,
        )
        best = day_rows[0]
        selected.append(
            SelectedRun(
                run_id=str(best["run_id"]),
                run_date_utc=run_date,
                database_path=str(database_path),
                week_folder=week_folder,
                scan_data_count=int(best.get("scan_data_count") or 0),
                profiles_scored=int(best.get("profiles_scored") or 0),
                expected_profiles=int(best.get("expected_profiles") or 0),
                selection_reason=(
                    "highest scan_data_count among full-coverage runs for day; "
                    f"candidates={len(day_rows)}"
                ),
            )
        )
    return selected


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _run_id_filter_sql(run_id: str) -> str:
    return f"run_id = '{_sql_literal(run_id)}'"


def _build_per_run_query_bundle(
    run_id: str,
    *,
    primary_profile_name: str,
    horizon_name: str,
    leader_limit: int,
) -> dict[str, str]:
    run_filter = _run_id_filter_sql(run_id)
    profile_literal = _sql_literal(primary_profile_name)
    horizon_literal = _sql_literal(horizon_name)
    limit = int(leader_limit)
    return {
        "metadata": f"""
            SELECT run_id, created_at_utc, run_date_utc, iso_year, iso_week,
                suite_name, scan_data_count, profile_names_json, notes
            FROM run_metadata
            WHERE {run_filter}
        """,
        f"consensus_leaders__{horizon_name}": f"""
            SELECT run_id, symbol, company, sector, industry, horizon_name,
                score, direction, confidence, agreement_ratio, opinions,
                risk_adjusted_score, risk_tier, manager_action_signal
            FROM consensus_horizon_scores
            WHERE {run_filter}
              AND horizon_name = '{horizon_literal}'
              AND score IS NOT NULL
            ORDER BY risk_adjusted_score DESC NULLS LAST
            LIMIT {limit}
        """,
        f"profile_leaders__{primary_profile_name}__{horizon_name}": f"""
            SELECT run_id, profile_name, symbol, company, sector, industry,
                horizon_name, score, direction, confidence, coverage, setup,
                risk_adjusted_score, risk_tier, manager_action_signal
            FROM profile_horizon_scores
            WHERE {run_filter}
              AND profile_name = '{profile_literal}'
              AND horizon_name = '{horizon_literal}'
              AND score IS NOT NULL
            ORDER BY risk_adjusted_score DESC NULLS LAST
            LIMIT {limit}
        """,
        f"pattern__{primary_profile_name}_validated__{horizon_name}": f"""
            WITH run_raw AS (
                SELECT *
                FROM raw_scan_rows
                WHERE {run_filter}
            )
            SELECT h.run_id, h.symbol, h.company, h.score, h.confidence, h.coverage,
                h.risk_adjusted_score, h.manager_action_signal,
                r.relative_volume_10d_calc,
                r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0) AS volume_trend_raw,
                r."ADX+DI" - r."ADX-DI" AS adx_directional_spread_raw
            FROM profile_horizon_scores h
            INNER JOIN run_raw r
                ON r.run_id = h.run_id
                AND (
                    r.symbol = h.symbol
                    OR r.symbol LIKE '%:' || h.symbol
                    OR h.symbol LIKE '%:' || r.symbol
                )
            WHERE h.{run_filter}
              AND h.profile_name = '{profile_literal}'
              AND h.horizon_name = '{horizon_literal}'
              AND h.coverage >= 0.70
              AND r.relative_volume_10d_calc > 1.3
              AND (r.average_volume_10d_calc / NULLIF(r.average_volume_30d_calc, 0)) > 1.05
              AND (r."ADX+DI" - r."ADX-DI") > 0
            ORDER BY h.risk_adjusted_score DESC NULLS LAST
            LIMIT {limit}
        """,
        f"manager_action_counts__{horizon_name}": f"""
            SELECT manager_action_signal,
                COUNT(*) AS symbol_count,
                AVG(risk_adjusted_score) AS avg_risk_adjusted_score
            FROM consensus_horizon_scores
            WHERE {run_filter}
              AND horizon_name = '{horizon_literal}'
              AND manager_action_signal IS NOT NULL
            GROUP BY manager_action_signal
            ORDER BY symbol_count DESC
        """,
        f"pattern__multi_lens_agree__{horizon_name}": f"""
            SELECT run_id, symbol, company, score, agreement_ratio, opinions,
                risk_adjusted_score, manager_action_signal
            FROM consensus_horizon_scores
            WHERE {run_filter}
              AND horizon_name = '{horizon_literal}'
              AND score >= 0.35
              AND agreement_ratio >= 0.60
              AND opinions >= 3
            ORDER BY risk_adjusted_score DESC NULLS LAST
            LIMIT {limit}
        """,
        f"pattern__fragility_long_conflict__{horizon_name}": f"""
            WITH fragility AS (
                SELECT symbol, score AS fragility_score, risk_adjusted_score AS fragility_ras
                FROM profile_horizon_scores
                WHERE {run_filter}
                  AND profile_name = 'fragility_short'
                  AND horizon_name = '{horizon_literal}'
                  AND score IS NOT NULL
            ),
            breakout AS (
                SELECT symbol, score AS breakout_score, risk_adjusted_score AS breakout_ras
                FROM profile_horizon_scores
                WHERE {run_filter}
                  AND profile_name = '{profile_literal}'
                  AND horizon_name = '{horizon_literal}'
                  AND score IS NOT NULL
            )
            SELECT f.symbol,
                f.fragility_score,
                f.fragility_ras,
                b.breakout_score,
                b.breakout_ras
            FROM fragility f
            INNER JOIN breakout b ON f.symbol = b.symbol
            WHERE f.fragility_score >= 0.35
              AND b.breakout_score >= 0.35
            ORDER BY f.fragility_ras DESC NULLS LAST
            LIMIT {limit}
        """,
        f"pattern__{primary_profile_name}_decile_summary__{horizon_name}": f"""
            WITH scored AS (
                SELECT symbol, score, coverage, risk_adjusted_score,
                    NTILE(10) OVER (ORDER BY score DESC) AS score_decile
                FROM profile_horizon_scores
                WHERE {run_filter}
                  AND profile_name = '{profile_literal}'
                  AND horizon_name = '{horizon_literal}'
                  AND score IS NOT NULL
            )
            SELECT CASE
                    WHEN score_decile <= 1 THEN 'top_decile'
                    WHEN score_decile >= 10 THEN 'bottom_decile'
                END AS cohort_label,
                COUNT(*) AS symbol_count,
                AVG(score) AS avg_score,
                AVG(coverage) AS avg_coverage,
                AVG(risk_adjusted_score) AS avg_risk_adjusted_score
            FROM scored
            WHERE score_decile <= 1 OR score_decile >= 10
            GROUP BY cohort_label
            ORDER BY cohort_label
        """,
    }


def export_per_run_pattern_checks(
    database_path: str | Path,
    run_id: str,
    output_dir: str | Path,
    *,
    primary_profile_name: str = DEFAULT_PRIMARY_PROFILE,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    leader_limit: int = 50,
    duckdb_read_threads: int | None = None,
) -> dict[str, str]:
    """Export prediction-only pattern slices for one run_id."""
    resolved_output = Path(output_dir)
    resolved_output.mkdir(parents=True, exist_ok=True)
    exports: dict[str, str] = {}
    queries = _build_per_run_query_bundle(
        run_id,
        primary_profile_name=primary_profile_name,
        horizon_name=horizon_name,
        leader_limit=leader_limit,
    )

    with open_move_prediction_duckdb_connection(
        database_path,
        read_only=True,
        threads=duckdb_read_threads,
        enable_object_cache=True,
    ) as connection:
        metadata_rows = _fetch_duckdb_rows(
            database_path,
            queries["metadata"],
            connection=connection,
        )
        exports["run_metadata_json"] = str(
            _write_json(
                resolved_output / "run_metadata.json",
                metadata_rows[0] if metadata_rows else {},
            )
        )

        for query_key, sql in queries.items():
            if query_key == "metadata":
                continue
            rows = _fetch_duckdb_rows(database_path, sql, connection=connection)
            exports[f"{query_key}_csv"] = str(
                _write_csv(resolved_output / f"{query_key}.csv", rows)
            )

    return exports


def _export_selected_runs_for_week(
    *,
    database_path: Path,
    week_folder: str,
    week_output: Path,
    week_selected: Sequence[SelectedRun],
    primary_profile_name: str,
    horizon_name: str,
    leader_limit: int,
    export_parallel_workers: int,
    duckdb_read_threads: int,
    on_run_exported: Callable[[int, int, SelectedRun], None] | None = None,
) -> list[str]:
    week_lines = [
        f"week_folder={week_folder}",
        f"database_path={database_path}",
    ]

    def _export_one(run: SelectedRun) -> str:
        run_dir = week_output / "runs" / _safe_run_folder_name(run.run_id)
        export_per_run_pattern_checks(
            database_path,
            run.run_id,
            run_dir,
            primary_profile_name=primary_profile_name,
            horizon_name=horizon_name,
            leader_limit=leader_limit,
            duckdb_read_threads=duckdb_read_threads,
        )
        return (
            f"  run_date={run.run_date_utc.isoformat()} run_id={run.run_id} "
            f"scan_data_count={run.scan_data_count}"
        )

    total_runs = len(week_selected)
    if export_parallel_workers <= 1 or total_runs <= 1:
        for completed_runs, run in enumerate(week_selected, start=1):
            week_lines.append(_export_one(run))
            if on_run_exported is not None:
                on_run_exported(completed_runs, total_runs, run)
        return week_lines

    completed_runs = 0
    completion_lock = threading.Lock()

    def _export_one_with_progress(run: SelectedRun) -> str:
        nonlocal completed_runs
        line = _export_one(run)
        if on_run_exported is not None:
            with completion_lock:
                completed_runs += 1
                on_run_exported(completed_runs, total_runs, run)
        return line

    with ThreadPoolExecutor(max_workers=export_parallel_workers) as executor:
        futures = {
            executor.submit(_export_one_with_progress, run): run
            for run in week_selected
        }
        for future in as_completed(futures):
            week_lines.append(future.result())
    return week_lines


def export_calibration_reports(
    history_database_path: str | Path,
    output_dir: str | Path,
    *,
    primary_profile_name: str = DEFAULT_PRIMARY_PROFILE,
    min_snapshots: int = CALIBRATION_GATES["persistence_min_snapshots"],
    duckdb_read_threads: int | None = None,
) -> dict[str, Any]:
    """Export multi-week calibration tables from a history aggregation DuckDB."""
    resolved_output = Path(output_dir)
    resolved_output.mkdir(parents=True, exist_ok=True)
    exports: dict[str, Any] = {"history_database_path": str(history_database_path)}

    alignment_sql = """
        SELECT a.profile_name,
            a.horizon_name,
            a.symbol_count,
            a.aligned_positive_count,
            a.false_positive_count,
            ROUND(
                a.aligned_positive_count * 1.0 / NULLIF(a.symbol_count, 0),
                4
            ) AS aligned_positive_ratio,
            -- Conditional hit rate: among symbols with score improvement, what % had price up?
            ROUND(
                a.aligned_positive_count * 1.0 / NULLIF(p.score_improved_count, 0),
                4
            ) AS conditional_hit_rate,
            p.score_improved_count,
            ROUND(a.score_price_corr, 4) AS score_price_corr
        FROM vw_profile_horizon_alignment_stats a
        LEFT JOIN (
            SELECT profile_name, horizon_name, COUNT(*) AS score_improved_count
            FROM vw_profile_horizon_progression_core
            WHERE score_delta_total > 0
            GROUP BY profile_name, horizon_name
        ) p ON a.profile_name = p.profile_name AND a.horizon_name = p.horizon_name
        ORDER BY aligned_positive_ratio DESC
    """
    persistence_sql = f"""
        SELECT profile_name, symbol, horizon_name, presence_ratio,
            snapshots_seen, close_return_pct_total
        FROM vw_profile_horizon_progression_core
        WHERE presence_ratio >= {CALIBRATION_GATES["persistence_presence_ratio_min"]}
          AND snapshots_seen >= {int(min_snapshots)}
        ORDER BY close_return_pct_total DESC
        LIMIT 200
    """
    false_positive_sql = """
        SELECT profile_name,
            horizon_name,
            COUNT(*) AS false_positive_count
        FROM vw_profile_horizon_progression_core
        WHERE score_delta_total > 0
          AND close_return_pct_total < 0
        GROUP BY profile_name, horizon_name
        ORDER BY false_positive_count DESC
    """

    with open_move_prediction_duckdb_connection(
        history_database_path,
        read_only=True,
        threads=duckdb_read_threads,
        enable_object_cache=True,
    ) as connection:
        alignment_rows = _fetch_duckdb_rows(
            history_database_path,
            alignment_sql,
            connection=connection,
        )
        persistence_rows = _fetch_duckdb_rows(
            history_database_path,
            persistence_sql,
            connection=connection,
        )
        false_positive_rows = _fetch_duckdb_rows(
            history_database_path,
            false_positive_sql,
            connection=connection,
        )

    exports["alignment_stats_csv"] = str(
        _write_csv(resolved_output / "alignment_stats.csv", alignment_rows)
    )
    exports["persistent_winners_csv"] = str(
        _write_csv(resolved_output / "persistent_winners.csv", persistence_rows)
    )
    exports["false_positive_counts_csv"] = str(
        _write_csv(resolved_output / "false_positive_counts.csv", false_positive_rows)
    )

    gate_rows: list[dict[str, Any]] = []
    for row in alignment_rows:
        horizon_name = row.get("horizon_name")
        aligned_ratio = float(row.get("aligned_positive_ratio") or 0.0)
        conditional_hit_rate = float(row.get("conditional_hit_rate") or 0.0)
        score_improved_count = int(row.get("score_improved_count") or 0)
        score_corr = float(row.get("score_price_corr") or 0.0)
        symbol_count = int(row.get("symbol_count") or 0)
        false_positive_count = int(row.get("false_positive_count") or 0)
        false_positive_rate = (
            false_positive_count / symbol_count if symbol_count else None
        )

        # Dual gate evaluation:
        # - aligned_positive_ratio >= 0.55 (universe-wide alignment)
        # - OR conditional_hit_rate >= 0.65 (hit rate among score-improved cohort)
        # The conditional gate is more lenient but requires meaningful sample size
        passes_aligned_ratio = aligned_ratio >= CALIBRATION_GATES["aligned_positive_ratio_min"]
        passes_conditional = (
            score_improved_count >= 20  # Minimum sample size for conditional gate
            and conditional_hit_rate >= CALIBRATION_GATES["conditional_hit_rate_min"]
        )
        passes_alignment = passes_aligned_ratio or passes_conditional

        passes = (
            passes_alignment
            and (
                horizon_name not in {DEFAULT_HORIZON_NAME, "months"}
                or score_corr >= CALIBRATION_GATES["score_price_corr_min"]
            )
            and (
                false_positive_rate is None
                or false_positive_rate <= CALIBRATION_GATES["false_positive_rate_max"]
            )
        )
        gate_rows.append(
            {
                "profile_name": row.get("profile_name"),
                "horizon_name": horizon_name,
                "aligned_positive_ratio": aligned_ratio,
                "conditional_hit_rate": conditional_hit_rate,
                "score_improved_count": score_improved_count,
                "score_price_corr": score_corr,
                "false_positive_rate": false_positive_rate,
                "passes_aligned_ratio_gate": passes_aligned_ratio,
                "passes_conditional_hit_gate": passes_conditional,
                "passes_calibration_gates": passes,
                "gate_used": "aligned_ratio" if passes_aligned_ratio else ("conditional_hit" if passes_conditional else "none"),
            }
        )
    exports["calibration_gate_summary_csv"] = str(
        _write_csv(resolved_output / "calibration_gate_summary.csv", gate_rows)
    )

    primary_gate_rows = [
        row for row in gate_rows if row.get("profile_name") == primary_profile_name
    ]
    exports["primary_profile_gate_summary_json"] = str(
        _write_json(
            resolved_output / f"primary_profile_gate_summary__{primary_profile_name}.json",
            {
                "primary_profile_name": primary_profile_name,
                "gates": CALIBRATION_GATES,
                "rows": primary_gate_rows,
            },
        )
    )
    return exports


def run_batch_prediction_pattern_analysis(
    *,
    iso_year: int = 2026,
    start_week: int = 13,
    end_week: int | None = None,
    output_dir: str | Path | None = None,
    duckdb_runs_root: str | Path | None = None,
    primary_profile_name: str = DEFAULT_PRIMARY_PROFILE,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    min_scan_data_count: int = 3000,
    require_suite_name: str = FULL_ANALYSIS_SUITE_NAME,
    aggregate_history: bool = True,
    include_profiles: Sequence[str] | None = DEFAULT_BATCH_INCLUDE_PROFILES,
    leader_limit: int = 50,
    memory_gb: float | None = None,
    duckdb_threads: int | None = None,
    history_parallel_workers: int | None = None,
    history_attach_batch_size: int | None = None,
    export_parallel_workers: int | None = None,
    duckdb_read_threads: int | None = None,
    calibration_detail: str = "gates_only",
    chunk_weeks: int | None = None,
    resume_from_batch_dir: str | Path | None = None,
    rolling_history_output_dir: str | Path | None = None,
    show_progress: bool = True,
) -> dict[str, Any]:
    """Run prediction-only batch pattern analysis for an ISO week range.

    Workflow:
      1. Discover weekly ``move_prediction_*.duckdb`` files (weeks 13–24 by default).
      2. Inventory runs and select one full-coverage run per calendar day.
      3. Export per-run leader/pattern CSV splits under ``by_week/`` and ``runs/``.
      4. Optionally aggregate selected runs into a history DB for calibration gates.

    Output root default::
        logs/tradingview_analysis/prediction_analysis/batch_pattern_analysis/
            batch_<timestamp>_utc/
    """
    resolved_end_week = _resolve_end_week(end_week)
    timestamp = _utc_timestamp()
    resume_batch_root = Path(resume_from_batch_dir) if resume_from_batch_dir else None
    batch_output_root = Path(
        output_dir
        or resume_batch_root
        or (DEFAULT_BATCH_OUTPUT_ROOT / f"batch_{timestamp}_utc")
    )
    batch_output_root.mkdir(parents=True, exist_ok=True)
    execution_config = resolve_batch_execution_config(
        memory_gb=memory_gb,
        duckdb_threads=duckdb_threads,
        history_parallel_workers=history_parallel_workers,
        history_attach_batch_size=history_attach_batch_size,
        export_parallel_workers=export_parallel_workers,
        duckdb_read_threads=duckdb_read_threads,
    )
    timing_seconds: dict[str, float] = {}
    batch_started = time.perf_counter()
    progress = BatchProgressReporter(enabled=show_progress)
    resolved_include_profiles = _resolve_batch_include_profiles(include_profiles)
    history_build_options = _resolve_history_build_options(calibration_detail)

    weekly_databases = discover_weekly_prediction_databases(
        iso_year=iso_year,
        start_week=start_week,
        end_week=resolved_end_week,
        duckdb_runs_root=duckdb_runs_root,
    )
    chunk_plans = _chunk_history_plans(
        weekly_databases,
        start_week=start_week,
        chunk_weeks=chunk_weeks,
    )
    week_total = len(weekly_databases)
    progress.banner(
        f"iso_year={iso_year} weeks={start_week:02d}-{resolved_end_week:02d} "
        f"weekly_databases={week_total} output={batch_output_root}"
    )
    progress.phase("phase 1/3: inventory + per-week exports")

    all_inventory_rows: list[dict[str, Any]] = []
    selected_runs: list[SelectedRun] = []
    per_week_summary: list[str] = []
    per_week_timing: list[dict[str, Any]] = []

    inventory_export_started = time.perf_counter()
    for week_index, database_path in enumerate(weekly_databases, start=1):
        week_folder = database_path.parent.name
        week_started = time.perf_counter()
        progress.week_started(
            week_index=week_index,
            week_total=week_total,
            week_folder=week_folder,
            database_name=database_path.name,
        )

        inventory_started = time.perf_counter()
        inventory_rows = inventory_runs_in_database(
            database_path,
            duckdb_read_threads=execution_config.duckdb_read_threads,
        )
        for row in inventory_rows:
            row["database_path"] = str(database_path)
            row["week_folder"] = week_folder
        all_inventory_rows.extend(inventory_rows)
        inventory_seconds = time.perf_counter() - inventory_started
        progress.week_step(
            week_folder,
            "inventory",
            f"{len(inventory_rows)} runs in {_format_duration(inventory_seconds)}",
        )

        week_selected = select_full_coverage_runs_per_day(
            inventory_rows,
            database_path=database_path,
            week_folder=week_folder,
            min_scan_data_count=min_scan_data_count,
            require_suite_name=require_suite_name,
        )
        selected_runs.extend(week_selected)
        progress.week_step(
            week_folder,
            "selection",
            f"{len(week_selected)} full-coverage days",
        )

        week_output = batch_output_root / "by_week" / week_folder
        week_output.mkdir(parents=True, exist_ok=True)
        _write_csv(week_output / "run_inventory.csv", inventory_rows)
        _write_csv(
            week_output / "selected_runs.csv",
            [
                {
                    "run_id": run.run_id,
                    "run_date_utc": run.run_date_utc.isoformat(),
                    "database_path": run.database_path,
                    "scan_data_count": run.scan_data_count,
                    "profiles_scored": run.profiles_scored,
                    "expected_profiles": run.expected_profiles,
                    "selection_reason": run.selection_reason,
                }
                for run in week_selected
            ],
        )

        export_started = time.perf_counter()

        def _on_run_exported(
            completed_runs: int,
            total_runs: int,
            run: SelectedRun,
            *,
            _week_folder: str = week_folder,
        ) -> None:
            progress.week_export_progress(
                week_folder=_week_folder,
                completed_runs=completed_runs,
                total_runs=total_runs,
                run_date=run.run_date_utc.isoformat(),
            )

        week_lines = _export_selected_runs_for_week(
            database_path=database_path,
            week_folder=week_folder,
            week_output=week_output,
            week_selected=week_selected,
            primary_profile_name=primary_profile_name,
            horizon_name=horizon_name,
            leader_limit=leader_limit,
            export_parallel_workers=execution_config.export_parallel_workers,
            duckdb_read_threads=execution_config.duckdb_read_threads,
            on_run_exported=_on_run_exported,
        )
        export_seconds = time.perf_counter() - export_started
        week_elapsed = time.perf_counter() - week_started
        per_week_timing.append(
            {
                "week_folder": week_folder,
                "inventory_seconds": round(inventory_seconds, 2),
                "export_seconds": round(export_seconds, 2),
                "week_total_seconds": round(week_elapsed, 2),
                "inventory_runs": len(inventory_rows),
                "selected_runs": len(week_selected),
            }
        )
        progress.week_finished(
            week_index=week_index,
            week_total=week_total,
            week_folder=week_folder,
            inventory_runs=len(inventory_rows),
            selected_runs=len(week_selected),
            elapsed_seconds=week_elapsed,
            inventory_seconds=inventory_seconds,
            export_seconds=export_seconds,
        )
        week_lines[1:1] = [
            f"inventory_runs={len(inventory_rows)}",
            f"selected_full_coverage_days={len(week_selected)}",
            f"timing_inventory_seconds={inventory_seconds:.2f}",
            f"timing_export_seconds={export_seconds:.2f}",
            f"timing_week_total_seconds={week_elapsed:.2f}",
        ]
        _write_text(week_output / "week_summary.log", week_lines)
        per_week_summary.extend(week_lines)
        per_week_summary.append("")

    timing_seconds["inventory_and_exports"] = time.perf_counter() - inventory_export_started
    progress.phase_timing("inventory + exports", timing_seconds["inventory_and_exports"])

    _write_csv(batch_output_root / "run_inventory_all.csv", all_inventory_rows)
    selected_run_rows = [
        {
            "run_id": run.run_id,
            "run_date_utc": run.run_date_utc.isoformat(),
            "week_folder": run.week_folder,
            "database_path": run.database_path,
            "scan_data_count": run.scan_data_count,
            "profiles_scored": run.profiles_scored,
            "expected_profiles": run.expected_profiles,
            "selection_reason": run.selection_reason,
        }
        for run in selected_runs
    ]
    _write_csv(batch_output_root / "selected_runs_all.csv", selected_run_rows)

    result: dict[str, Any] = {
        "output_dir": str(batch_output_root),
        "iso_year": iso_year,
        "start_week": start_week,
        "end_week": resolved_end_week,
        "weekly_database_count": len(weekly_databases),
        "inventory_run_count": len(all_inventory_rows),
        "selected_run_count": len(selected_runs),
        "selected_runs_csv": str(batch_output_root / "selected_runs_all.csv"),
        "primary_profile_name": primary_profile_name,
        "horizon_name": horizon_name,
        "include_profiles": list(resolved_include_profiles),
        "calibration_detail": calibration_detail,
        "chunk_weeks": chunk_weeks,
        "resume_from_batch_dir": str(resume_from_batch_dir) if resume_from_batch_dir else None,
        "rolling_history_output_dir": (
            str(rolling_history_output_dir) if rolling_history_output_dir else None
        ),
        "execution_config": {
            "memory_gb": execution_config.memory_gb,
            "duckdb_threads": execution_config.duckdb_threads,
            "history_parallel_workers": execution_config.history_parallel_workers,
            "history_attach_batch_size": execution_config.history_attach_batch_size,
            "export_parallel_workers": execution_config.export_parallel_workers,
            "duckdb_read_threads": execution_config.duckdb_read_threads,
        },
    }

    if aggregate_history and selected_runs:
        history_output_dir = batch_output_root / "calibration"
        use_rolling_history = (
            bool(rolling_history_output_dir)
            or len(chunk_plans) > 1
            or resume_batch_root is not None
        )
        canonical_history_output_dir = (
            Path(rolling_history_output_dir)
            if rolling_history_output_dir
            else (
                history_output_dir / "rolling"
                if use_rolling_history
                else history_output_dir
            )
        )
        history_started = time.perf_counter()
        selected_run_ids = [run.run_id for run in selected_runs]
        selected_week_groups = _group_selected_runs_by_week(selected_runs)
        progress.phase(
            f"phase 2/3: history aggregation ({len(selected_runs)} selected runs / "
            f"{len(selected_week_groups)} weekly databases)"
        )

        def _on_history_stage_completed(
            completed_sources: int,
            total_sources: int,
            database_path: Path,
            staged_runs: int,
            staged_rows: int,
            elapsed_seconds: float,
        ) -> None:
            week_folder = database_path.parent.name
            progress.week_step(
                week_folder,
                "history-stage",
                (
                    f"{_progress_bar(completed_sources, total_sources, width=12)} "
                    f"runs={staged_runs} rows={staged_rows} "
                    f"in {_format_duration(elapsed_seconds)}"
                ),
            )

        chunk_results: list[dict[str, Any]] = []
        resume_manifest_path = batch_output_root / "history_chunk_manifest.json"
        resumed_chunks: set[int] = set()
        if resume_from_batch_dir:
            previous_manifest = Path(resume_from_batch_dir) / "history_chunk_manifest.json"
            if previous_manifest.is_file():
                try:
                    resumed_payload = json.loads(previous_manifest.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    resumed_payload = {}
                for chunk_record in resumed_payload.get("chunks", []):
                    if chunk_record.get("status") == "completed":
                        resumed_chunks.add(int(chunk_record.get("chunk_index") or 0))

        manifest_chunks: list[dict[str, Any]] = []
        history_database_path: str | Path | None = None
        final_history_result: dict[str, Any] | None = None
        history_staging_seconds_total = 0.0
        history_materialize_seconds_total = 0.0

        for chunk_plan in chunk_plans:
            chunk_week_paths = set(chunk_plan.weekly_databases)
            chunk_output_dir = canonical_history_output_dir
            chunk_group_paths = [
                group.database_path
                for group in selected_week_groups
                if group.database_path in chunk_week_paths
            ]
            manifest_entry = {
                "chunk_index": chunk_plan.chunk_index,
                "chunk_total": chunk_plan.chunk_total,
                "week_start": chunk_plan.week_start,
                "week_end": chunk_plan.week_end,
                "output_dir": str(chunk_output_dir),
                "status": "skipped" if chunk_plan.chunk_index in resumed_chunks else "pending",
            }
            if chunk_plan.chunk_index in resumed_chunks:
                manifest_chunks.append(manifest_entry | {"status": "completed"})
                continue

            chunk_history_result = run_move_prediction_history_aggregation_duckdb_incremental(
                input_paths=chunk_group_paths,
                output_dir=chunk_output_dir,
                include_profiles=resolved_include_profiles,
                include_run_ids=selected_run_ids,
                write_legacy_csv_outputs=False,
                export_parquet=False,
                prefer_parquet_inputs=True,
                max_memory_gb=execution_config.memory_gb,
                duckdb_threads=execution_config.duckdb_threads,
                duckdb_memory_limit=f"{max(max(1.0, execution_config.memory_gb - 2.0), 1.0):.2f}GB",
                on_stage_completed=_on_history_stage_completed,
                build_options=history_build_options,
                rolling=use_rolling_history,
            )
            chunk_timings = chunk_history_result.get("materialization_timings") or {}
            history_materialize_seconds_total += sum(
                float(value or 0.0) for value in chunk_timings.values()
            )
            history_staging_seconds_total += max(
                0.0,
                float(chunk_history_result.get("execution_config", {}).get("staging_seconds", 0.0) or 0.0),
            )
            manifest_chunks.append(manifest_entry | {"status": "completed"})
            chunk_results.append(
                {
                    "chunk_index": chunk_plan.chunk_index,
                    "chunk_total": chunk_plan.chunk_total,
                    "week_start": chunk_plan.week_start,
                    "week_end": chunk_plan.week_end,
                    "output_dir": str(chunk_output_dir),
                    "history_database_path": str(
                        chunk_history_result.get("analysis_database")
                        or chunk_history_result.get("database_path")
                    ),
                    "materialization_timings": chunk_timings,
                }
            )
            final_history_result = chunk_history_result
            history_database_path = (
                chunk_history_result.get("analysis_database")
                or chunk_history_result.get("database_path")
            )

            _write_json(
                resume_manifest_path,
                {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "batch_output_dir": str(batch_output_root),
                    "calibration_detail": calibration_detail,
                    "chunk_weeks": chunk_weeks,
                    "canonical_history_output_dir": str(canonical_history_output_dir),
                    "chunks": manifest_chunks,
                },
            )

        history_result = final_history_result or {
            "chunk_results": chunk_results,
            "history_database_path": (
                history_database_path
                or str(
                    canonical_history_output_dir / "historical_prediction_analysis.duckdb"
                )
            ),
        }
        if final_history_result is not None:
            history_result["chunk_results"] = chunk_results
        timing_seconds["history_aggregation"] = time.perf_counter() - history_started
        timing_seconds["history_staging"] = history_staging_seconds_total
        timing_seconds["history_materialize"] = history_materialize_seconds_total
        progress.phase_timing(
            "history aggregation", timing_seconds["history_aggregation"]
        )
        analysis_tables = history_result.get("analysis_tables") or {}
        history_database_path = history_database_path or (
            history_result.get("analysis_database")
            or analysis_tables.get("database_path")
            or history_result.get("database_path")
            or history_result.get("_duckdb_database")
        )
        if history_database_path:
            calibration_started = time.perf_counter()
            progress.phase("phase 3/3: calibration report exports")
            calibration_exports = export_calibration_reports(
                history_database_path,
                history_output_dir / "reports",
                primary_profile_name=primary_profile_name,
                duckdb_read_threads=execution_config.duckdb_read_threads,
            )
            timing_seconds["calibration_exports"] = (
                time.perf_counter() - calibration_started
            )
            progress.phase_timing(
                "calibration exports", timing_seconds["calibration_exports"]
            )
            result["history_database_path"] = str(history_database_path)
            result.update(
                {f"calibration_{key}": value for key, value in calibration_exports.items()}
            )
        result["history_chunk_results"] = chunk_results
        result["history_chunk_manifest"] = str(resume_manifest_path)
        result["history_aggregation_result"] = history_result
        result["history_staging_seconds"] = timing_seconds["history_staging"]
        result["history_materialize_seconds"] = timing_seconds["history_materialize"]
    elif aggregate_history:
        result["history_aggregation_skipped"] = "no full-coverage runs selected"
        progress.phase("history aggregation skipped (no full-coverage runs selected)")

    timing_seconds["total"] = time.perf_counter() - batch_started
    result["timing_seconds"] = timing_seconds
    result["per_week_timing"] = per_week_timing
    progress.summary(timing_seconds)

    overview_lines = [
        "TradingView prediction_analysis batch pattern analysis",
        f"generated_at_utc={datetime.now(timezone.utc).isoformat()}",
        f"iso_year={iso_year}",
        f"week_range={start_week:02d}-{resolved_end_week:02d}",
        f"output_dir={batch_output_root}",
        f"weekly_databases={len(weekly_databases)}",
        f"inventory_runs={len(all_inventory_rows)}",
        f"selected_full_coverage_runs={len(selected_runs)}",
        f"primary_profile_name={primary_profile_name}",
        f"horizon_name={horizon_name}",
        f"min_scan_data_count={min_scan_data_count}",
        f"include_profiles={','.join(resolved_include_profiles)}",
        f"calibration_detail={calibration_detail}",
        f"chunk_weeks={chunk_weeks if chunk_weeks is not None else 'none'}",
        f"resume_from_batch_dir={resume_from_batch_dir or ''}",
        f"rolling_history_output_dir={rolling_history_output_dir or ''}",
        "",
        "Execution config:",
        f"  memory_gb={execution_config.memory_gb}",
        f"  duckdb_threads={execution_config.duckdb_threads}",
        f"  history_parallel_workers={execution_config.history_parallel_workers}",
        f"  history_attach_batch_size={execution_config.history_attach_batch_size}",
        f"  export_parallel_workers={execution_config.export_parallel_workers}",
        f"  duckdb_read_threads={execution_config.duckdb_read_threads}",
        "",
        "Timing (seconds):",
        *(f"  {phase}={elapsed:.2f}" for phase, elapsed in timing_seconds.items()),
        "",
        "Core starting flow:",
        "  1. Review selected_runs_all.csv (one full-coverage run per day).",
        f"  2. Open by_week/week=NN/runs/<run_id>/profile_leaders__{primary_profile_name}__{horizon_name}.csv.",
        f"  3. Compare pattern__{primary_profile_name}_validated__{horizon_name}.csv across weeks.",
        "  4. Read calibration/reports/calibration_gate_summary.csv for promotion gates.",
        "  5. Inspect pattern__fragility_long_conflict and pattern__multi_lens_agree per run.",
        "",
        "Per-week summary:",
        *per_week_summary,
    ]
    if result.get("history_database_path"):
        overview_lines.extend(
            [
                "",
                f"history_database_path={result['history_database_path']}",
                f"history_staging_seconds={timing_seconds.get('history_staging', 0.0):.2f}",
                f"history_materialize_seconds={timing_seconds.get('history_materialize', 0.0):.2f}",
                "calibration reports under calibration/reports/",
            ]
        )
    if result.get("history_chunk_results"):
        overview_lines.extend(
            [
                "",
                "History chunks:",
                *(
                    "  "
                    + (
                        f"chunk_{chunk_row['chunk_index']:02d}/{chunk_row['chunk_total']:02d} "
                        f"weeks={chunk_row['week_start']:02d}-{chunk_row['week_end']:02d} "
                        f"output_dir={chunk_row['output_dir']}"
                    )
                    for chunk_row in result["history_chunk_results"]
                ),
            ]
        )
    overview_path = batch_output_root / "batch_overview.log"
    _write_text(overview_path, overview_lines)
    result["overview_log"] = str(overview_path)
    return result


def _safe_run_folder_name(run_id: str) -> str:
    sanitized = re.sub(r"[^\w.\-]+", "_", run_id).strip("_")
    return sanitized or "run"


def _resolve_batch_include_profiles(
    include_profiles: Sequence[str] | None,
) -> list[str]:
    if include_profiles is None:
        return list(DEFAULT_BATCH_INCLUDE_PROFILES)
    resolved = []
    seen: set[str] = set()
    for profile_name in include_profiles:
        normalized = str(profile_name).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return resolved or list(DEFAULT_BATCH_INCLUDE_PROFILES)


def _resolve_history_build_options(
    calibration_detail: str,
) -> HistoricalAggregationBuildOptions:
    normalized = str(calibration_detail).strip().lower()
    if normalized == "full":
        return HistoricalAggregationBuildOptions()
    if normalized == "gates_only":
        return HistoricalAggregationBuildOptions(
            build_profile_outputs=False,
            build_wide_progression_tables=False,
            build_cross_comparison=False,
            include_snapshot_delta_view=False,
            calibration_only=True,
        )
    raise ValueError(
        "Unsupported calibration_detail. Expected 'full' or 'gates_only'."
    )


def _chunk_history_plans(
    weekly_databases: Sequence[Path],
    *,
    start_week: int,
    chunk_weeks: int | None,
) -> list[BatchHistoryPlan]:
    if chunk_weeks is None or chunk_weeks <= 0 or len(weekly_databases) <= chunk_weeks:
        if not weekly_databases:
            return []
        end_week = start_week + len(weekly_databases) - 1
        return [
            BatchHistoryPlan(
                chunk_index=1,
                chunk_total=1,
                week_start=start_week,
                week_end=end_week,
                weekly_databases=tuple(weekly_databases),
            )
        ]

    plans: list[BatchHistoryPlan] = []
    total = (len(weekly_databases) + chunk_weeks - 1) // chunk_weeks
    for chunk_index, offset in enumerate(range(0, len(weekly_databases), chunk_weeks), start=1):
        chunk = tuple(weekly_databases[offset : offset + chunk_weeks])
        chunk_start = start_week + offset
        chunk_end = chunk_start + len(chunk) - 1
        plans.append(
            BatchHistoryPlan(
                chunk_index=chunk_index,
                chunk_total=total,
                week_start=chunk_start,
                week_end=chunk_end,
                weekly_databases=chunk,
            )
        )
    return plans


def _group_selected_runs_by_week(
    selected_runs: Sequence[SelectedRun],
) -> list[WeeklySelectionGroup]:
    grouped: dict[tuple[str, str], list[SelectedRun]] = defaultdict(list)
    for run in selected_runs:
        grouped[(run.database_path, run.week_folder)].append(run)
    grouped_groups: list[WeeklySelectionGroup] = []
    for (database_path, week_folder), runs in sorted(
        grouped.items(), key=lambda item: (item[0][1], item[0][0])
    ):
        grouped_groups.append(
            WeeklySelectionGroup(
                database_path=Path(database_path),
                week_folder=week_folder,
                runs=tuple(sorted(runs, key=lambda candidate: candidate.run_date_utc)),
            )
        )
    return grouped_groups


def run_batch_prediction_pattern_analysis_example() -> dict[str, Any]:
    """Convenience entry point for weeks 13–current ISO week."""
    return run_batch_prediction_pattern_analysis(
        iso_year=2026,
        start_week=13,
        end_week=24,
        primary_profile_name=DEFAULT_PRIMARY_PROFILE,
        horizon_name=DEFAULT_HORIZON_NAME,
    )


if __name__ == "__main__":
    example_result = run_batch_prediction_pattern_analysis_example()
    print(example_result["overview_log"])
