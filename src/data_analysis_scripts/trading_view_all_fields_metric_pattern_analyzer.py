"""All-fields metric pattern discovery and multi-run aggregation.

This module builds a two-layer workflow:
1) Per-run pattern summaries from ``all_fields_rows`` in daily all-fields DuckDB files.
2) Cross-run stability aggregation over those per-run summaries.

It also provides optional raw pooling and optional forward-return attachment from
the historical aggregation database.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import tempfile
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from db.trading_view_move_prediction_duckdb import (
    _quote_identifier,
    open_move_prediction_duckdb_connection,
    query_move_prediction_duckdb,
)
from constants.trading_view_constants import PREFERRED_MARKETS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALL_FIELDS_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
)
DEFAULT_PATTERN_ANALYSIS_ROOT = DEFAULT_ALL_FIELDS_ROOT / "pattern_analysis"
DEFAULT_FIELD_CATALOG_CSV = PROJECT_ROOT / "savedData" / "trading_view_stock_fields.csv"
DEFAULT_PATTERN_MAX_SYSTEM_MEMORY_GB = 30.0
DEFAULT_PATTERN_MEMORY_RESERVE_GB = 4.0
DEFAULT_PATTERN_ESTIMATED_RUN_MEMORY_GB = 8.0
DEFAULT_PATTERN_DEFAULT_DUCKDB_THREADS = 8
DEFAULT_HISTORY_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "prediction_analysis"
    / "duckdb_runs"
    / "historical_prediction_analysis"
)
ALL_FIELDS_SUITE_NAME = "tradingview_all_fields_export_duckdb"

DEFAULT_PERFORMANCE_FIELDS: tuple[str, ...] = (
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.Y",
    "Perf.YTD",
)
# Trailing Perf.* horizons used by default (excludes change/change_abs duplicates and
# Perf.*.MarketCap variants that restate the same move on a different scale).
CORE_PERFORMANCE_FIELDS: tuple[str, ...] = DEFAULT_PERFORMANCE_FIELDS
FORWARD_PERFORMANCE_FIELDS: tuple[str, ...] = (
    "close_forward_return_pct",
    "period_return_pct",
    "history_forward_return",
)
PERIOD_PERFORMANCE_FIELD = "period_return_pct"
PERFORMANCE_PREFIXES: tuple[str, ...] = ("Perf.", "change", "change_abs")
PREDICTOR_EXCLUDE_PREFIXES: tuple[str, ...] = PERFORMANCE_PREFIXES + (
    "close",
    "open",
    "high",
    "low",
)
PREDICTOR_EXCLUDE_EXACT: tuple[str, ...] = (
    "run_id",
    "row_number",
    "symbol",
    "name",
    "description",
    "logoid",
    "type",
    "typespecs",
    "exchange",
    "country",
    "market",
    "sector",
    "industry",
    "currency",
    "earnings_release_next_date",
)
# Catalog types treated as quantifiable predictor candidates in scan-period tracking.
QUANTIFIABLE_PREDICTOR_CATALOG_TYPES: tuple[str, ...] = ("number", "percent")
ALL_FIELDS_METADATA_COLUMNS: tuple[str, ...] = ("run_id", "row_number", "symbol")
TRAILING_LOOKAHEAD_WARNING = "trailing_performance_lookahead"
OVERVIEW_LOG_TOP_PATTERNS = 100

LOWER_IS_BETTER_MARKERS: tuple[str, ...] = (
    "enterprise_value_ebitda",
    "ev_ebitda",
    "enterprise_value_revenue",
    "ev_revenue",
    "price_earnings",
    "price_revenue",
    "price_sales",
    "price_book",
    "price_free_cash_flow",
    "peg_ratio",
    "total_debt_to_ebitda",
)

FIELD_PATTERN_EXPORT_COLUMNS: tuple[str, ...] = (
    "source_database_path",
    "source_day_label",
    "run_id",
    "run_created_at_utc",
    "run_label",
    "scan_data_count",
    "predictor_field",
    "predictor_display_name",
    "predictor_type",
    "predictor_direction",
    "predictor_fill_rate",
    "predictor_numeric_parse_rate",
    "performance_field",
    "performance_display_name",
    "performance_type",
    "pair_n",
    "pearson_corr",
    "spearman_corr",
    "pearson_corr_adjusted",
    "spearman_corr_adjusted",
    "top_quintile_avg_perf",
    "bottom_quintile_avg_perf",
    "quintile_spread",
    "quintile_spread_adjusted",
    "top_quintile_median_perf",
    "bottom_quintile_median_perf",
    "quintile_median_spread",
    "quintile_median_spread_adjusted",
    "performance_stddev",
    "performance_median",
    "predictor_median",
    "above_median_predictor_outperforms_rate",
    "below_median_predictor_outperforms_rate",
    "directional_outperforms_rate",
    "normalized_quintile_spread",
    "pattern_score",
    "warnings_json",
)

POOL_METADATA_SCHEMA: tuple[tuple[str, str], ...] = (
    ("pool_aggregation_id", "VARCHAR"),
    ("source_database_path", "VARCHAR"),
    ("source_day_label", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("run_created_at_utc", "TIMESTAMP"),
    ("run_label", "VARCHAR"),
    ("scan_data_count", "BIGINT"),
)


@dataclass(frozen=True)
class AllFieldsAnalysisConfig:
    performance_fields: tuple[str, ...] = DEFAULT_PERFORMANCE_FIELDS
    performance_prefixes: tuple[str, ...] = PERFORMANCE_PREFIXES
    predictor_exclude_prefixes: tuple[str, ...] = PREDICTOR_EXCLUDE_PREFIXES
    predictor_exclude_exact: tuple[str, ...] = PREDICTOR_EXCLUDE_EXACT
    exclude_perf_from_predictors: bool = True
    min_fill_rate: float = 0.15
    min_numeric_parse_rate: float = 0.90
    min_pair_n: int = 50
    quintile_count: int = 5
    field_batch_size: int = 40
    duckdb_threads: int = 8


@dataclass(frozen=True)
class AllFieldsUniverseFilterClause:
    """One row filter applied before all-fields analysis."""

    field: str
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: tuple[str, ...] | None = None
    excluded_values: tuple[str, ...] | None = None
    exclude_null: bool = True


@dataclass(frozen=True)
class AllFieldsUniverseFilter:
    """Row-level universe restriction applied before analysis starts."""

    clauses: tuple[AllFieldsUniverseFilterClause, ...] = ()


@dataclass(frozen=True)
class AllFieldsUniverseFilterResolution:
    sql_fragment: str
    applied_clauses: tuple[AllFieldsUniverseFilterClause, ...]
    skipped_clauses: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...]


AllFieldsUniverseFilterInput = (
    AllFieldsUniverseFilter | Mapping[str, Any] | Sequence[Mapping[str, Any]] | None
)


@dataclass(frozen=True)
class FieldCatalogEntry:
    name: str
    display_name: str
    field_type: str
    explanation: str
    model_use: str


@dataclass(frozen=True)
class FieldCatalogIndex:
    by_name: Mapping[str, FieldCatalogEntry]

    def get(self, field_name: str) -> FieldCatalogEntry | None:
        return self.by_name.get(field_name)


@dataclass(frozen=True)
class RunInventoryRow:
    run_id: str
    created_at_utc: datetime | None
    run_label: str
    suite_name: str
    scan_data_count: int


@dataclass(frozen=True)
class FieldQualityStat:
    field_name: str
    total_rows: int
    non_blank_count: int
    numeric_count: int
    fill_rate: float
    numeric_parse_rate: float


@dataclass(frozen=True)
class FieldPerformancePatternRow:
    source_database_path: str
    source_day_label: str
    run_id: str
    run_created_at_utc: datetime | None
    run_label: str
    scan_data_count: int
    predictor_field: str
    predictor_display_name: str
    predictor_type: str
    predictor_direction: str
    predictor_fill_rate: float
    predictor_numeric_parse_rate: float
    performance_field: str
    performance_display_name: str
    performance_type: str
    pair_n: int
    pearson_corr: float | None
    spearman_corr: float | None
    pearson_corr_adjusted: float | None
    spearman_corr_adjusted: float | None
    top_quintile_avg_perf: float | None
    bottom_quintile_avg_perf: float | None
    quintile_spread: float | None
    quintile_spread_adjusted: float | None
    top_quintile_median_perf: float | None
    bottom_quintile_median_perf: float | None
    quintile_median_spread: float | None
    quintile_median_spread_adjusted: float | None
    performance_stddev: float | None
    performance_median: float | None
    predictor_median: float | None
    above_median_predictor_outperforms_rate: float | None
    below_median_predictor_outperforms_rate: float | None
    directional_outperforms_rate: float | None
    normalized_quintile_spread: float
    pattern_score: float | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_database_path": self.source_database_path,
            "source_day_label": self.source_day_label,
            "run_id": self.run_id,
            "run_created_at_utc": (
                self.run_created_at_utc.isoformat() if self.run_created_at_utc else None
            ),
            "run_label": self.run_label,
            "scan_data_count": self.scan_data_count,
            "predictor_field": self.predictor_field,
            "predictor_display_name": self.predictor_display_name,
            "predictor_type": self.predictor_type,
            "predictor_direction": self.predictor_direction,
            "predictor_fill_rate": self.predictor_fill_rate,
            "predictor_numeric_parse_rate": self.predictor_numeric_parse_rate,
            "performance_field": self.performance_field,
            "performance_display_name": self.performance_display_name,
            "performance_type": self.performance_type,
            "pair_n": self.pair_n,
            "pearson_corr": self.pearson_corr,
            "spearman_corr": self.spearman_corr,
            "pearson_corr_adjusted": self.pearson_corr_adjusted,
            "spearman_corr_adjusted": self.spearman_corr_adjusted,
            "top_quintile_avg_perf": self.top_quintile_avg_perf,
            "bottom_quintile_avg_perf": self.bottom_quintile_avg_perf,
            "quintile_spread": self.quintile_spread,
            "quintile_spread_adjusted": self.quintile_spread_adjusted,
            "top_quintile_median_perf": self.top_quintile_median_perf,
            "bottom_quintile_median_perf": self.bottom_quintile_median_perf,
            "quintile_median_spread": self.quintile_median_spread,
            "quintile_median_spread_adjusted": self.quintile_median_spread_adjusted,
            "performance_stddev": self.performance_stddev,
            "performance_median": self.performance_median,
            "predictor_median": self.predictor_median,
            "above_median_predictor_outperforms_rate": self.above_median_predictor_outperforms_rate,
            "below_median_predictor_outperforms_rate": self.below_median_predictor_outperforms_rate,
            "directional_outperforms_rate": self.directional_outperforms_rate,
            "normalized_quintile_spread": self.normalized_quintile_spread,
            "pattern_score": self.pattern_score,
            "warnings_json": json.dumps(list(self.warnings), ensure_ascii=False),
        }


@dataclass(frozen=True)
class CrossRunFieldStabilityRow:
    predictor_field: str
    performance_field: str
    runs_seen: int
    runs_with_signal: int
    mean_pearson: float | None
    median_pearson: float | None
    stddev_pearson: float | None
    mean_quintile_spread: float | None
    median_quintile_spread: float | None
    sign_consistency_ratio: float | None
    best_run_id: str | None
    worst_run_id: str | None
    rank_stability_score: float | None


@dataclass
class AllFieldsRunPatternAnalysisResult:
    analysis_run_id: str
    run_lifecycle_id: str
    output_dir: Path
    database_path: Path
    source_table_name: str
    source_day_label: str
    run_id: str
    run_created_at_utc: datetime | None
    run_label: str
    scan_data_count: int
    predictor_fields: list[str]
    eligible_predictor_fields: list[str]
    performance_fields: list[str]
    skipped_predictors: dict[str, str]
    rows: list[FieldPerformancePatternRow]
    exports: dict[str, str]

    @property
    def top_patterns(self) -> list[FieldPerformancePatternRow]:
        return sorted(
            self.rows,
            key=lambda row: (
                row.pattern_score if row.pattern_score is not None else float("-inf")
            ),
            reverse=True,
        )


@dataclass
class AllFieldsPatternAggregateResult:
    aggregate_id: str
    output_dir: Path
    database_path: Path
    source_summary_paths: list[Path]
    rows: list[CrossRunFieldStabilityRow]
    exports: dict[str, str]


def _import_duckdb() -> Any:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ImportError(
            "DuckDB support requires the 'duckdb' package. "
            "Install it with 'python -m pip install duckdb'."
        ) from exc
    return duckdb


RUN_LIFECYCLE_ID_LENGTH = 8


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_run_lifecycle_id() -> str:
    """Short unique id shared by every artifact folder from one orchestrated run."""
    return uuid.uuid4().hex[:RUN_LIFECYCLE_ID_LENGTH]


def _resolve_run_lifecycle_id(run_lifecycle_id: str | None) -> str:
    if run_lifecycle_id:
        normalized = str(run_lifecycle_id).strip()
        if normalized:
            return normalized
    return _new_run_lifecycle_id()


def _slugify(value: str) -> str:
    slug = []
    previous_sep = False
    for char in str(value).lower():
        if char.isalnum():
            slug.append(char)
            previous_sep = False
            continue
        if previous_sep:
            continue
        slug.append("_")
        previous_sep = True
    return "".join(slug).strip("_")


def _quote_sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _quote_path_literal(path: str | Path) -> str:
    return _quote_sql_literal(Path(path).as_posix())


def _progress_bar(completed: int, total: int, width: int = 30) -> str:
    """Build ASCII progress bar for Git Bash: [=========>               ] 3/10"""
    if total <= 0:
        return "[" + " " * width + "] 0/0"
    filled = min(int(width * completed / total), width)
    if filled >= width:
        return f"[{'=' * width}] {completed}/{total}"
    return f"[{'=' * filled}>{' ' * (width - filled - 1)}] {completed}/{total}"


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {remainder}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes}m {remainder}s"


def _resolve_show_progress(show_progress: bool | None) -> bool:
    return sys.stdout.isatty() if show_progress is None else bool(show_progress)


class _AllFieldsPatternRunProgress:
    """Git Bash-friendly progress reporter for single-run pattern analysis."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.started_at = time.perf_counter()
        self.pairwise_started_at: float | None = None
        self._query_times: list[float] = []
        self.chunks_completed = 0
        self.total_chunks = 0
        self.max_parallel_chunks = 1

    def _print(self, message: str) -> None:
        if self.enabled:
            print(message, flush=True)

    def _elapsed(self) -> float:
        return time.perf_counter() - self.started_at

    def _pairwise_elapsed(self) -> float:
        if self.pairwise_started_at is None:
            return 0.0
        return time.perf_counter() - self.pairwise_started_at

    def _wall_clock_eta_seconds(self) -> float:
        """ETA from observed chunk completion throughput (correct for parallel workers)."""
        if self.chunks_completed <= 0 or self.pairwise_started_at is None:
            return 0.0
        remaining = self.total_chunks - self.chunks_completed
        return self._pairwise_elapsed() * remaining / self.chunks_completed

    def _recent_query_seconds(self, window: int = 20) -> float | None:
        if not self._query_times:
            return None
        recent = self._query_times[-window:]
        return sum(recent) / len(recent)

    def avg_query_seconds(self) -> float:
        if not self._query_times:
            return 0.0
        return sum(self._query_times) / len(self._query_times)

    def run_started(
        self,
        *,
        source_day_label: str,
        run_id: str,
        scan_data_count: int,
        performance_field_count: int,
        eligible_predictor_count: int,
        total_chunks: int,
        max_parallel_chunks: int,
        duckdb_threads: int,
        field_batch_size: int,
    ) -> None:
        self.total_chunks = total_chunks
        self.max_parallel_chunks = max(1, int(max_parallel_chunks))
        chunks_per_perf = (
            total_chunks / performance_field_count if performance_field_count else 0.0
        )
        self._print("=" * 80)
        self._print("All-fields pattern analysis started")
        self._print(
            f"  day={source_day_label} run_id={run_id} scan_rows={scan_data_count:,}"
        )
        self._print(
            f"  perf_fields={performance_field_count} "
            f"eligible_predictors={eligible_predictor_count} "
            f"batch_size={field_batch_size}"
        )
        self._print(
            f"  pairwise_chunks={total_chunks} "
            f"({performance_field_count} perf x ~{chunks_per_perf:.0f} batches)"
        )
        self._print(
            f"  parallel_chunks={self.max_parallel_chunks} duckdb_threads={duckdb_threads}"
        )
        if total_chunks > 40 and self.max_parallel_chunks <= 1:
            self._print(
                "  note: sequential chunk mode is memory-safe but slow; "
                "try max_parallel_chunks=2-3 with duckdb_memory_limit='8GB' per worker"
            )
        self._print("=" * 80)

    def pairwise_started(self) -> None:
        self.pairwise_started_at = time.perf_counter()

    def quality_stats_started(self, predictor_count: int, batch_count: int) -> None:
        self._print(
            f"[setup] Computing quality stats for {predictor_count} predictors "
            f"({batch_count} SQL batches)..."
        )

    def quality_stats_finished(self, eligible_count: int, elapsed: float) -> None:
        self._print(
            f"[setup] Quality stats done: {eligible_count} eligible predictors "
            f"in {_format_duration(elapsed)}"
        )

    def chunk_finished(
        self,
        *,
        performance_field: str,
        predictor_count: int,
        rows_emitted: int,
        query_seconds: float,
    ) -> None:
        self.chunks_completed += 1
        self._query_times.append(query_seconds)
        wall_eta = self._wall_clock_eta_seconds()
        phase_elapsed = self._pairwise_elapsed()
        recent_query = self._recent_query_seconds()
        eta_text = f" | ETA ~{_format_duration(wall_eta)}" if wall_eta > 0 else ""
        recent_text = (
            f" recent_query~{recent_query:.1f}s"
            if recent_query is not None and self.max_parallel_chunks > 1
            else ""
        )
        perf_short = (
            performance_field
            if len(performance_field) <= 28
            else (performance_field[:25] + "...")
        )
        self._print(
            f"[{_progress_bar(self.chunks_completed, self.total_chunks)}] "
            f"{perf_short} ({predictor_count} predictors, {rows_emitted} rows) "
            f"query={query_seconds:.1f}s phase={_format_duration(phase_elapsed)}"
            f"{recent_text}{eta_text}"
        )

    def export_started(self) -> None:
        self._print("[export] Writing CSV/Parquet/context outputs...")

    def export_finished(self, elapsed: float, rows_emitted: int) -> None:
        self._print(
            f"[export] Done: {rows_emitted:,} rows written in {_format_duration(elapsed)}"
        )

    def run_finished(
        self,
        *,
        rows_emitted: int,
        quality_stats_seconds: float,
        pairwise_seconds: float,
        export_seconds: float,
    ) -> None:
        total = self._elapsed()
        avg_query = (
            sum(self._query_times) / len(self._query_times)
            if self._query_times
            else 0.0
        )
        throughput = (
            self.chunks_completed / pairwise_seconds
            if pairwise_seconds > 0 and self.chunks_completed > 0
            else 0.0
        )
        self._print("=" * 80)
        self._print(
            f"All-fields pattern analysis complete: {rows_emitted:,} rows in "
            f"{_format_duration(total)}"
        )
        self._print(
            f"  quality_stats={_format_duration(quality_stats_seconds)} "
            f"pairwise={_format_duration(pairwise_seconds)} "
            f"export={_format_duration(export_seconds)} "
            f"chunks={self.chunks_completed}/{self.total_chunks} "
            f"avg_query={avg_query:.1f}s throughput={throughput:.2f} chunks/s"
        )
        self._print("=" * 80)


class _AllFieldsPatternBatchProgress:
    """Lightweight batch / pipeline progress for Git Bash (run-level, low overhead)."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.started_at = time.perf_counter()

    def _print(self, message: str) -> None:
        if self.enabled:
            print(message, flush=True)

    def banner(self, title: str) -> None:
        self._print("=" * 80)
        self._print(title)
        self._print("=" * 80)

    def phase(self, message: str) -> None:
        self._print(f"[phase] {message}")

    def batch_started(
        self,
        *,
        label: str,
        task_count: int,
        parallel_runs: int,
    ) -> None:
        self._print(
            f"[batch] {label}: {task_count} runs "
            f"(parallel={parallel_runs}) starting..."
        )

    def task_finished(
        self,
        *,
        completed: int,
        total: int,
        run_id: str,
        source_day_label: str,
        rows_emitted: int,
        elapsed_seconds: float,
        failed: bool = False,
    ) -> None:
        status = "FAIL" if failed else "OK"
        self._print(
            f"[{_progress_bar(completed, total)}] {status} "
            f"day={source_day_label} run_id={run_id} rows={rows_emitted:,} "
            f"elapsed={_format_duration(elapsed_seconds)}"
        )

    def batch_finished(
        self,
        *,
        success_count: int,
        failure_count: int,
    ) -> None:
        total_elapsed = time.perf_counter() - self.started_at
        self._print(
            f"[batch] Complete: {success_count} succeeded, {failure_count} failed "
            f"in {_format_duration(total_elapsed)}"
        )
        self._print("=" * 80)


class _AllFieldsCloseForwardProgress(_AllFieldsPatternBatchProgress):
    """Pipeline progress for close-forward pattern analysis."""

    def pipeline_started(
        self,
        *,
        start_day_label: str,
        end_day_label: str,
        predictor_count: int,
        run_count: int,
    ) -> None:
        self.banner("Close-forward pattern analysis pipeline")
        self._print(
            f"  window={start_day_label}..{end_day_label} "
            f"predictors={predictor_count} runs={run_count}"
        )


class _AllFieldsPeriodProgress(_AllFieldsPatternBatchProgress):
    """Pipeline progress for whole-period predictor analysis (Git Bash friendly)."""

    def __init__(self, *, enabled: bool) -> None:
        super().__init__(enabled=enabled)
        self._step_started_at: float | None = None

    def pipeline_started(
        self,
        *,
        start_day_label: str,
        end_day_label: str,
        predictor_count: int | None = None,
        scan_count: int | None = None,
        performance_target: str = PERIOD_PERFORMANCE_FIELD,
    ) -> None:
        self.banner("Whole-period predictor analysis")
        parts = [
            f"window={start_day_label}..{end_day_label}",
            f"target={performance_target}",
        ]
        if predictor_count is not None:
            parts.append(f"predictors={predictor_count:,}")
        if scan_count is not None:
            parts.append(f"scans={scan_count}")
        self._print("  " + " ".join(parts))

    def step_started(self, step: str, *, detail: str = "") -> None:
        self._step_started_at = time.perf_counter()
        suffix = f" — {detail}" if detail else ""
        self._print(f"[step] {step}{suffix}")

    def step_finished(self, step: str, **stats: Any) -> None:
        started_at = self._step_started_at or self.started_at
        elapsed = time.perf_counter() - started_at
        stat_parts = " ".join(
            f"{key}={value:,}" if isinstance(value, int) else f"{key}={value}"
            for key, value in stats.items()
            if value is not None
        )
        suffix = f" {stat_parts}" if stat_parts else ""
        self._print(
            f"[step] {step} done in {_format_duration(elapsed)}{suffix}"
        )

    def scan_attached(
        self,
        *,
        completed: int,
        total: int,
        day_label: str,
        elapsed_seconds: float,
    ) -> None:
        self._print(
            f"[{_progress_bar(completed, total)}] attach day={day_label} "
            f"elapsed={_format_duration(elapsed_seconds)}"
        )

    def rolling_window_finished(
        self,
        *,
        completed: int,
        total: int,
        window_label: str,
        rows_emitted: int,
        elapsed_seconds: float,
        failed: bool = False,
    ) -> None:
        status = "FAIL" if failed else "OK"
        self._print(
            f"[{_progress_bar(completed, total)}] {status} window={window_label} "
            f"rows={rows_emitted:,} elapsed={_format_duration(elapsed_seconds)}"
        )

    def pipeline_finished(self, *, label: str = "Complete", **stats: Any) -> None:
        elapsed = time.perf_counter() - self.started_at
        stat_parts = " ".join(
            f"{key}={value:,}" if isinstance(value, int) else f"{key}={value}"
            for key, value in stats.items()
            if value is not None
        )
        suffix = f" {stat_parts}" if stat_parts else ""
        self._print(
            f"[pipeline] {label} in {_format_duration(elapsed)}{suffix}"
        )
        self._print("=" * 80)


def _chunked(values: Sequence[str], chunk_size: int) -> Iterable[list[str]]:
    resolved_chunk = max(1, int(chunk_size))
    current: list[str] = []
    for value in values:
        current.append(value)
        if len(current) >= resolved_chunk:
            yield current
            current = []
    if current:
        yield current


def _coerce_datetime_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _try_parse_day_label(day_label: str) -> date | None:
    if not day_label:
        return None
    try:
        return datetime.strptime(day_label, "%d_%m_%Y").date()
    except ValueError:
        return None


def _extract_day_label_from_database_path(database_path: Path) -> str:
    parent_label = database_path.parent.name
    if _try_parse_day_label(parent_label):
        return parent_label
    stem = database_path.stem
    if "tradingview_all_fields_" in stem:
        suffix = stem.replace("tradingview_all_fields_", "", 1)
        if _try_parse_day_label(suffix):
            return suffix
    return "unknown_day"


def _normalize_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _normalize_universe_filter_field_name(value: Any) -> str | None:
    if value is None:
        return None
    field_name = str(value).strip()
    return field_name or None


def _normalize_text_filter_values(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if isinstance(raw, (str, bytes)):
        text = str(raw).strip().lower()
        return (text,) if text else None
    if isinstance(raw, Mapping):
        return None
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item).strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        values.append(text)
    return tuple(values) if values else None


def _parse_universe_filter_membership(
    raw: Mapping[str, Any],
) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    allowed_values = _normalize_text_filter_values(
        raw.get(
            "in",
            raw.get(
                "values",
                raw.get(
                    "one_of",
                    raw.get("allowed", raw.get("include", raw.get("allowed_values"))),
                ),
            ),
        )
    )
    excluded_values = _normalize_text_filter_values(
        raw.get(
            "not_in",
            raw.get(
                "exclude",
                raw.get("excluded", raw.get("exclude_values", raw.get("excluded_values"))),
            ),
        )
    )
    return allowed_values, excluded_values


def _parse_universe_filter_bounds(
    raw: Mapping[str, Any],
) -> tuple[float | None, float | None, bool]:
    min_value = _normalize_float(
        raw.get("min_value", raw.get("min", raw.get("gte", raw.get("minimum"))))
    )
    max_value = _normalize_float(
        raw.get("max_value", raw.get("max", raw.get("lte", raw.get("maximum"))))
    )
    exclude_null_raw = raw.get("exclude_null")
    if exclude_null_raw is None:
        exclude_null = min_value is not None or max_value is not None
    else:
        exclude_null = bool(exclude_null_raw)
    return min_value, max_value, exclude_null


def _parse_universe_filter_clause(
    field_name: str,
    raw: Mapping[str, Any],
) -> AllFieldsUniverseFilterClause | None:
    normalized_field = _normalize_universe_filter_field_name(field_name)
    if normalized_field is None:
        return None
    min_value, max_value, exclude_null = _parse_universe_filter_bounds(raw)
    allowed_values, excluded_values = _parse_universe_filter_membership(raw)
    has_numeric_bounds = min_value is not None or max_value is not None
    has_membership = bool(allowed_values or excluded_values)
    if not has_numeric_bounds and not has_membership and not exclude_null:
        return None
    if min_value is not None and max_value is not None and min_value > max_value:
        min_value, max_value = max_value, min_value
    return AllFieldsUniverseFilterClause(
        field=normalized_field,
        min_value=min_value,
        max_value=max_value,
        allowed_values=allowed_values,
        excluded_values=excluded_values,
        exclude_null=exclude_null,
    )


def normalize_all_fields_universe_filter(
    universe_filter: AllFieldsUniverseFilterInput = None,
) -> AllFieldsUniverseFilter:
    """Normalize flexible filter input into a stable clause list.

    Supported shapes:
    - ``None`` (no filter)
    - ``AllFieldsUniverseFilter``
    - ``{"market_cap_basic": {"min": 300_000_000}}`` (field-keyed object)
    - ``{"market": {"in": ["america", "uk"]}}`` or ``{"market": ["america", "uk"]}``
    - ``[{"field": "market_cap_basic", "min": 300_000_000}]`` (clause list)
    - ``{"field": "market_cap_basic", "min": 300_000_000}`` (single clause)
    """
    if universe_filter is None:
        return AllFieldsUniverseFilter()
    if isinstance(universe_filter, AllFieldsUniverseFilter):
        return universe_filter

    clauses: list[AllFieldsUniverseFilterClause] = []
    seen_fields: set[str] = set()

    if isinstance(universe_filter, Mapping):
        if "field" in universe_filter or "Field" in universe_filter:
            field_name = universe_filter.get("field", universe_filter.get("Field"))
            clause = _parse_universe_filter_clause(
                str(field_name or ""), universe_filter
            )
            if clause is not None and clause.field not in seen_fields:
                clauses.append(clause)
                seen_fields.add(clause.field)
            return AllFieldsUniverseFilter(clauses=tuple(clauses))

        for field_name, raw_clause in universe_filter.items():
            if isinstance(raw_clause, (list, tuple, set)):
                raw_clause = {"in": list(raw_clause)}
            if not isinstance(raw_clause, Mapping):
                numeric_value = _normalize_float(raw_clause)
                if numeric_value is None:
                    continue
                raw_clause = {"min": numeric_value}
            clause = _parse_universe_filter_clause(str(field_name), raw_clause)
            if clause is None or clause.field in seen_fields:
                continue
            clauses.append(clause)
            seen_fields.add(clause.field)
        return AllFieldsUniverseFilter(clauses=tuple(clauses))

    for raw_entry in universe_filter:
        if not isinstance(raw_entry, Mapping):
            continue
        field_name = raw_entry.get("field", raw_entry.get("Field"))
        clause = _parse_universe_filter_clause(str(field_name or ""), raw_entry)
        if clause is None or clause.field in seen_fields:
            continue
        clauses.append(clause)
        seen_fields.add(clause.field)
    return AllFieldsUniverseFilter(clauses=tuple(clauses))


def market_cap_basic_universe_filter(
    min_usd: float | None = None,
    *,
    max_usd: float | None = None,
    exclude_null: bool = True,
) -> dict[str, dict[str, float | bool | None]]:
    """Convenience builder for excluding penny/low-cap names via ``market_cap_basic``."""
    payload: dict[str, float | bool | None] = {"exclude_null": exclude_null}
    if min_usd is not None:
        payload["min"] = float(min_usd)
    if max_usd is not None:
        payload["max"] = float(max_usd)
    return {"market_cap_basic": payload}


def preferred_markets_universe_filter(
    markets: Sequence[str] | None = None,
    *,
    field_name: str = "market",
    exclude_null: bool = True,
) -> dict[str, dict[str, Any]]:
    """Restrict rows to TradingView market codes (defaults to ``PREFERRED_MARKETS``)."""
    resolved_markets = _normalize_text_filter_values(
        markets if markets is not None else PREFERRED_MARKETS
    )
    if not resolved_markets:
        return {}
    return {
        field_name: {
            "in": list(resolved_markets),
            "exclude_null": exclude_null,
        }
    }


def move_prediction_universe_filter(
    *,
    min_market_cap_usd: float | None = 500_000_000,
    max_market_cap_usd: float | None = None,
    markets: Sequence[str] | None = None,
    market_field: str = "market",
) -> dict[str, Any]:
    """Universe filter aligned with ``scan_global_market_move_prediction`` constraints."""
    payload: dict[str, Any] = {}
    if min_market_cap_usd is not None or max_market_cap_usd is not None:
        payload.update(
            market_cap_basic_universe_filter(
                min_market_cap_usd,
                max_usd=max_market_cap_usd,
            )
        )
    payload.update(
        preferred_markets_universe_filter(
            markets,
            field_name=market_field,
        )
    )
    return payload


def _universe_filter_clause_to_dict(
    clause: AllFieldsUniverseFilterClause,
) -> dict[str, Any]:
    return {
        "field": clause.field,
        "min_value": clause.min_value,
        "max_value": clause.max_value,
        "allowed_values": list(clause.allowed_values or ()),
        "excluded_values": list(clause.excluded_values or ()),
        "exclude_null": clause.exclude_null,
    }


def serialize_all_fields_universe_filter(
    universe_filter: AllFieldsUniverseFilter | None,
) -> dict[str, Any] | None:
    if universe_filter is None or not universe_filter.clauses:
        return None
    return {
        "clauses": [
            _universe_filter_clause_to_dict(clause)
            for clause in universe_filter.clauses
        ]
    }


def deserialize_all_fields_universe_filter(
    payload: Mapping[str, Any] | None,
) -> AllFieldsUniverseFilter:
    if not payload:
        return AllFieldsUniverseFilter()
    raw_clauses = payload.get("clauses")
    if not isinstance(raw_clauses, Sequence) or isinstance(raw_clauses, (str, bytes)):
        return normalize_all_fields_universe_filter(payload)
    return normalize_all_fields_universe_filter(list(raw_clauses))


def _build_universe_filter_clause_sql(
    clause: AllFieldsUniverseFilterClause,
    *,
    table_alias: str | None = None,
) -> str:
    field_ref = (
        f"{_quote_identifier(table_alias)}.{_quote_identifier(clause.field)}"
        if table_alias
        else _quote_identifier(clause.field)
    )
    numeric_expr = _numeric_sql_expression(field_ref)
    text_expr = f"LOWER(TRIM(CAST({field_ref} AS VARCHAR)))"
    parts: list[str] = []
    if clause.exclude_null:
        parts.append(
            f"NULLIF(TRIM(CAST({field_ref} AS VARCHAR)), '') IS NOT NULL"
        )
    if clause.allowed_values:
        allowed_literals = ", ".join(
            _quote_sql_literal(value) for value in clause.allowed_values
        )
        parts.append(f"{text_expr} IN ({allowed_literals})")
    if clause.excluded_values:
        excluded_literals = ", ".join(
            _quote_sql_literal(value) for value in clause.excluded_values
        )
        parts.append(f"{text_expr} NOT IN ({excluded_literals})")
    if clause.min_value is not None:
        parts.append(f"{numeric_expr} >= {float(clause.min_value)}")
    if clause.max_value is not None:
        parts.append(f"{numeric_expr} <= {float(clause.max_value)}")
    if not parts:
        return "TRUE"
    return "(" + " AND ".join(parts) + ")"


def resolve_all_fields_universe_filter_sql(
    universe_filter: AllFieldsUniverseFilterInput = None,
    *,
    available_columns: Sequence[str] | None = None,
    table_alias: str | None = None,
) -> AllFieldsUniverseFilterResolution:
    """Build a safe SQL fragment (`` AND (...)``) for row-level universe filters."""
    resolved_filter = normalize_all_fields_universe_filter(universe_filter)
    if not resolved_filter.clauses:
        return AllFieldsUniverseFilterResolution(
            sql_fragment="",
            applied_clauses=(),
            skipped_clauses=(),
            warnings=(),
        )

    column_set = set(available_columns or [])
    applied: list[AllFieldsUniverseFilterClause] = []
    skipped: list[tuple[str, str]] = []
    warnings: list[str] = []
    clause_sql_parts: list[str] = []

    for clause in resolved_filter.clauses:
        if available_columns is not None and clause.field not in column_set:
            skipped.append((clause.field, "missing_column"))
            warnings.append(f"Universe filter skipped unknown column '{clause.field}'.")
            continue
        applied.append(clause)
        clause_sql_parts.append(
            _build_universe_filter_clause_sql(clause, table_alias=table_alias)
        )

    if not clause_sql_parts:
        return AllFieldsUniverseFilterResolution(
            sql_fragment="",
            applied_clauses=(),
            skipped_clauses=tuple(skipped),
            warnings=tuple(warnings),
        )
    return AllFieldsUniverseFilterResolution(
        sql_fragment=" AND (" + " AND ".join(clause_sql_parts) + ")",
        applied_clauses=tuple(applied),
        skipped_clauses=tuple(skipped),
        warnings=tuple(warnings),
    )


def _count_universe_filtered_rows(
    conn: Any,
    *,
    source_table_name: str,
    run_id: str | None = None,
    universe_filter_sql: str = "",
) -> int:
    table_sql = _quote_identifier(source_table_name)
    where_parts = ["TRUE"]
    params: list[Any] = []
    if run_id is not None:
        where_parts.append("run_id = ?")
        params.append(run_id)
    if universe_filter_sql:
        where_parts.append(universe_filter_sql.removeprefix(" AND "))
    query_sql = f"SELECT COUNT(*) FROM {table_sql} WHERE " + " AND ".join(where_parts)
    row = conn.execute(query_sql, params).fetchone()
    return int(row[0] if row else 0)


def _resolve_universe_filter_from_task(
    task: Mapping[str, Any],
) -> AllFieldsUniverseFilter:
    raw_filter = task.get("universe_filter")
    if raw_filter is None:
        return AllFieldsUniverseFilter()
    if isinstance(raw_filter, AllFieldsUniverseFilter):
        return raw_filter
    if isinstance(raw_filter, Mapping) and "clauses" in raw_filter:
        return deserialize_all_fields_universe_filter(raw_filter)
    return normalize_all_fields_universe_filter(raw_filter)


def _build_run_analysis_id(
    prefix: str,
    *,
    scope_label: str | None = None,
    run_lifecycle_id: str | None = None,
) -> str:
    """Build a unique analysis folder id with optional scope in the name.

    Format: ``{prefix}[_{scope}]_{run_lifecycle_id}``

    Pass the same ``run_lifecycle_id`` from a parent orchestrator so every output
    folder created during one execution shares one grep-friendly suffix. When
    omitted, a new 8-character id is generated for standalone runs.
    """
    scope_part = ""
    if scope_label:
        slug = _slugify(scope_label)
        if slug:
            scope_part = f"_{slug}"
    lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    return f"{prefix}{scope_part}_{lifecycle_id}"


def _resolve_orchestrated_run_output_root(
    *,
    prefix: str,
    scope_label: str | None = None,
    run_lifecycle_id: str | None = None,
    output_dir: str | Path | None = None,
    default_parent: Path = DEFAULT_PATTERN_ANALYSIS_ROOT / "runs",
) -> tuple[str, str, Path]:
    """Return one orchestrated run id, lifecycle id, and output root folder.

    Top-level orchestrators should call this once, then pass ``output_root`` (or
    subfolders beneath it) to every downstream writer so one execution lands in
    one tree instead of scattering artifacts across multiple prefixed folders.
    """
    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    run_id = _build_run_analysis_id(
        prefix,
        scope_label=scope_label,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    output_root = (
        Path(output_dir) if output_dir is not None else default_parent / run_id
    )
    output_root.mkdir(parents=True, exist_ok=True)
    return run_id, resolved_run_lifecycle_id, output_root


def _format_day_range_scope_label(
    start_day_label: str | None,
    end_day_label: str | None,
) -> str | None:
    """Compact human-readable day range for folder names, e.g. ``08Jun-12Jun2026``."""
    start_label = (start_day_label or "").strip()
    end_label = (end_day_label or "").strip()
    if not start_label and not end_label:
        return None
    if not start_label:
        start_label = end_label
    if not end_label:
        end_label = start_label
    start_date = _try_parse_day_label(start_label)
    end_date = _try_parse_day_label(end_label)
    if start_date is None or end_date is None:
        return f"{start_label}_to_{end_label}"
    if start_date == end_date:
        return start_date.strftime("%d%b%Y")
    if start_date.year == end_date.year:
        return f"{start_date.strftime('%d%b')}-{end_date.strftime('%d%b%Y')}"
    return f"{start_date.strftime('%d%b%Y')}-" f"{end_date.strftime('%d%b%Y')}"


def _discover_next_all_fields_database_after_end(
    *,
    all_fields_root: str | Path,
    end_day_label: str,
) -> Path | None:
    """Return the first daily DuckDB strictly after ``end_day_label``, if any."""
    end_day = _try_parse_day_label(end_day_label)
    if end_day is None:
        return None
    root = Path(all_fields_root)
    if not root.exists():
        return None
    candidates: list[tuple[date, Path]] = []
    for candidate in root.rglob("tradingview_all_fields_*.duckdb"):
        if not candidate.is_file():
            continue
        day_label = _extract_day_label_from_database_path(candidate)
        day_value = _try_parse_day_label(day_label)
        if day_value is None or day_value <= end_day:
            continue
        candidates.append((day_value, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _resolve_close_forward_return_source_paths(
    interval_paths: Sequence[Path],
    *,
    all_fields_root: str | Path,
    end_day_label: str | None = None,
    include_end_scan_forward_return: bool = True,
) -> tuple[list[Path], list[Path]]:
    """Return interval paths and close-return source paths (interval + optional lookahead).

    The interval paths define which scan days are analyzed. When
    ``include_end_scan_forward_return`` is True, one additional database after
    ``end_day_label`` is appended only for computing LEAD close prices so the
    end scan in the interval receives a forward return.
    """
    resolved_interval = list(interval_paths)
    if not include_end_scan_forward_return or not resolved_interval:
        return resolved_interval, list(resolved_interval)

    resolved_end = (end_day_label or "").strip()
    if not resolved_end:
        resolved_end = _extract_day_label_from_database_path(resolved_interval[-1])
    lookahead = _discover_next_all_fields_database_after_end(
        all_fields_root=all_fields_root,
        end_day_label=resolved_end,
    )
    if lookahead is None:
        return resolved_interval, list(resolved_interval)

    interval_posix = {path.resolve().as_posix() for path in resolved_interval}
    lookahead_posix = lookahead.resolve().as_posix()
    if lookahead_posix in interval_posix:
        return resolved_interval, list(resolved_interval)
    return resolved_interval, resolved_interval + [lookahead]


def _pattern_row_sort_score(row: Any) -> float:
    if isinstance(row, FieldPerformancePatternRow):
        return abs(row.pattern_score or 0.0)
    if isinstance(row, dict):
        return abs(_normalize_float(row.get("pattern_score")) or 0.0)
    return 0.0


def _format_field_pattern_highlight_lines(
    rows: Sequence[Any],
    *,
    limit: int = OVERVIEW_LOG_TOP_PATTERNS,
) -> list[str]:
    sorted_rows = sorted(rows, key=_pattern_row_sort_score, reverse=True)
    lines: list[str] = []
    for row in sorted_rows[: max(1, int(limit))]:
        if isinstance(row, FieldPerformancePatternRow):
            lines.append(
                "  - "
                + f"{row.predictor_field} vs {row.performance_field}"
                + f" | pattern_score={row.pattern_score}"
                + f" | spearman_adj={row.spearman_corr_adjusted}"
                + f" | quintile_spread_adj={row.quintile_spread_adjusted}"
                + f" | pair_n={row.pair_n}"
            )
            continue
        if isinstance(row, dict):
            lines.append(
                "  - "
                + f"{row.get('predictor_field')} vs {row.get('performance_field')}"
                + f" | pattern_score={row.get('pattern_score')}"
                + f" | spearman_adj={row.get('spearman_corr_adjusted')}"
                + f" | quintile_spread_adj={row.get('quintile_spread_adjusted')}"
                + f" | pair_n={row.get('pair_n')}"
            )
    return lines


def _format_stability_highlight_lines(
    rows: Sequence[CrossRunFieldStabilityRow],
    catalog: FieldCatalogIndex,
    *,
    limit: int = OVERVIEW_LOG_TOP_PATTERNS,
) -> list[str]:
    lines: list[str] = []
    for row in rows[: max(1, int(limit))]:
        predictor_label = catalog.get(row.predictor_field)
        perf_label = catalog.get(row.performance_field)
        predictor_name = (
            predictor_label.display_name
            if predictor_label and predictor_label.display_name
            else row.predictor_field
        )
        performance_name = (
            perf_label.display_name
            if perf_label and perf_label.display_name
            else row.performance_field
        )
        lines.append(
            "  - "
            + f"{row.predictor_field} ({predictor_name})"
            + f" vs {row.performance_field} ({performance_name})"
            + f" | runs_seen={row.runs_seen}"
            + f" | sign_consistency={row.sign_consistency_ratio}"
            + f" | median_spread={row.median_quintile_spread}"
            + f" | score={row.rank_stability_score}"
        )
    return lines


def _write_overview_log(path: Path, lines: Sequence[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _is_performance_field(field_name: str, config: AllFieldsAnalysisConfig) -> bool:
    if field_name in set(config.performance_fields):
        return True
    if field_name in set(FORWARD_PERFORMANCE_FIELDS):
        return True
    normalized = field_name.strip()
    return any(normalized.startswith(prefix) for prefix in config.performance_prefixes)


def resolve_performance_fields(
    all_fields_columns: Sequence[str],
    *,
    explicit_fields: Sequence[str] | None = None,
    include_forward_targets: bool = True,
) -> list[str]:
    """Resolve performance targets from explicit override or core Perf.* horizons."""
    column_set = set(all_fields_columns)
    if explicit_fields is not None:
        return [
            field_name for field_name in explicit_fields if field_name in column_set
        ]

    resolved: list[str] = []
    seen: set[str] = set()
    for field_name in CORE_PERFORMANCE_FIELDS:
        if field_name in column_set and field_name not in seen:
            resolved.append(field_name)
            seen.add(field_name)
    if include_forward_targets:
        for field_name in FORWARD_PERFORMANCE_FIELDS:
            if field_name in column_set and field_name not in seen:
                resolved.append(field_name)
                seen.add(field_name)
    return resolved


def _is_predictor_excluded(field_name: str, config: AllFieldsAnalysisConfig) -> bool:
    normalized = field_name.strip()
    if normalized in set(ALL_FIELDS_METADATA_COLUMNS):
        return True
    if normalized in set(config.predictor_exclude_exact):
        return True
    return any(
        normalized.startswith(prefix) for prefix in config.predictor_exclude_prefixes
    )


def _numeric_sql_expression(column_sql: str) -> str:
    return (
        "TRY_CAST(REPLACE(TRIM(CAST("
        + column_sql
        + " AS VARCHAR)), ',', '') AS DOUBLE)"
    )


def _finite_numeric_filter_sql(value_sql: str, *, max_abs: float) -> str:
    return (
        f"({value_sql} IS NOT NULL"
        f" AND isfinite({value_sql})"
        f" AND abs({value_sql}) <= {float(max_abs)})"
    )


def _write_csv_rows(
    path: Path,
    rows: Sequence[dict[str, Any]],
    *,
    fieldnames: Sequence[str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    resolved_fieldnames = list(fieldnames or [])
    if not resolved_fieldnames and rows:
        resolved_fieldnames = list(rows[0].keys())

    with path.open("w", encoding="utf-8", newline="") as csv_file:
        if not resolved_fieldnames:
            csv_file.write("")
            return path
        writer = csv.DictWriter(csv_file, fieldnames=resolved_fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(rows)
    return path


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def _write_parquet_from_csv(
    csv_path: Path,
    parquet_path: Path,
    *,
    temp_directory: str | Path | None = None,
) -> Path:
    duckdb = _import_duckdb()
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect()
    try:
        if temp_directory is not None:
            resolved_temp_dir = Path(temp_directory)
            resolved_temp_dir.mkdir(parents=True, exist_ok=True)
            conn.execute("SET temp_directory = ?", [str(resolved_temp_dir)])
        conn.execute(
            "COPY (SELECT * FROM read_csv_auto("
            + _quote_path_literal(csv_path)
            + ", HEADER=true)) TO "
            + _quote_path_literal(parquet_path)
            + " (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.close()
    return parquet_path


def _read_table_columns(conn: Any, table_name: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'main'
          AND table_name = ?
        ORDER BY ordinal_position
        """,
        [table_name],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _read_run_metadata_row(
    database_path: Path,
    run_id: str,
) -> dict[str, Any]:
    rows = query_move_prediction_duckdb(
        database_path,
        """
        SELECT run_id,
            created_at_utc,
            run_label,
            suite_name,
            scan_data_count
        FROM run_metadata
        WHERE run_id = ?
        LIMIT 1
        """,
        [run_id],
    )
    return rows[0] if rows else {}


def load_field_catalog(
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
) -> FieldCatalogIndex:
    catalog_path = Path(field_catalog_csv)
    if not catalog_path.exists():
        raise FileNotFoundError(f"Field catalog CSV not found: {catalog_path}")

    by_name: dict[str, FieldCatalogEntry] = {}
    with catalog_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            name = str(row.get("Name", "")).strip()
            if not name:
                continue
            by_name[name] = FieldCatalogEntry(
                name=name,
                display_name=str(row.get("Display name", "")).strip(),
                field_type=str(row.get("Type", "")).strip(),
                explanation=str(row.get("explanation", "")).strip(),
                model_use=str(row.get("model_use", "")).strip(),
            )
    return FieldCatalogIndex(by_name=by_name)


def classify_columns(
    all_fields_columns: Sequence[str],
    catalog: FieldCatalogIndex | None = None,
    config: AllFieldsAnalysisConfig | None = None,
) -> tuple[list[str], list[str]]:
    resolved_config = config or AllFieldsAnalysisConfig()
    predictor_fields: list[str] = []
    performance_fields = resolve_performance_fields(all_fields_columns)
    performance_field_set = set(performance_fields)
    seen_predictors: set[str] = set()

    for column_name in all_fields_columns:
        if not column_name:
            continue
        if column_name in performance_field_set:
            continue
        if _is_predictor_excluded(column_name, resolved_config):
            continue
        if column_name in seen_predictors:
            continue
        if catalog is not None and not catalog.get(column_name):
            # Keep unknown columns as candidates because schema can evolve ahead
            # of the local catalog file.
            pass
        seen_predictors.add(column_name)
        predictor_fields.append(column_name)

    for preferred in resolved_config.performance_fields:
        if preferred in all_fields_columns and preferred not in performance_field_set:
            performance_fields.append(preferred)
            performance_field_set.add(preferred)

    return predictor_fields, performance_fields


def discover_all_fields_daily_databases(
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
) -> list[Path]:
    root = Path(all_fields_root)
    if not root.exists():
        return []

    start_day = _try_parse_day_label(start_day_label or "")
    end_day = _try_parse_day_label(end_day_label or "")
    resolved_paths: list[Path] = []

    for candidate in sorted(root.rglob("tradingview_all_fields_*.duckdb")):
        if not candidate.is_file():
            continue
        day_label = _extract_day_label_from_database_path(candidate)
        day_value = _try_parse_day_label(day_label)
        if day_value is None and (start_day or end_day):
            continue
        if start_day and day_value and day_value < start_day:
            continue
        if end_day and day_value and day_value > end_day:
            continue
        resolved_paths.append(candidate)
    return resolved_paths


def inventory_all_fields_runs(
    database_path: str | Path,
) -> list[RunInventoryRow]:
    rows = query_move_prediction_duckdb(
        database_path,
        """
        SELECT run_id,
            created_at_utc,
            run_label,
            suite_name,
            scan_data_count
        FROM run_metadata
        WHERE suite_name = ?
        ORDER BY created_at_utc DESC
        """,
        [ALL_FIELDS_SUITE_NAME],
    )
    inventory: list[RunInventoryRow] = []
    for row in rows:
        inventory.append(
            RunInventoryRow(
                run_id=str(row.get("run_id") or ""),
                created_at_utc=_coerce_datetime_utc(row.get("created_at_utc")),
                run_label=str(row.get("run_label") or ""),
                suite_name=str(row.get("suite_name") or ""),
                scan_data_count=int(row.get("scan_data_count") or 0),
            )
        )
    return inventory


def resolve_run_id(
    database_path: str | Path,
    run_id: str | None = None,
) -> str:
    if run_id:
        rows = query_move_prediction_duckdb(
            database_path,
            """
            SELECT run_id
            FROM run_metadata
            WHERE run_id = ?
              AND suite_name = ?
            LIMIT 1
            """,
            [run_id, ALL_FIELDS_SUITE_NAME],
        )
        if not rows:
            raise ValueError(
                f"run_id '{run_id}' was not found for suite '{ALL_FIELDS_SUITE_NAME}'."
            )
        return str(rows[0]["run_id"])

    rows = query_move_prediction_duckdb(
        database_path,
        """
        SELECT run_id
        FROM run_metadata
        WHERE suite_name = ?
        ORDER BY created_at_utc DESC
        LIMIT 1
        """,
        [ALL_FIELDS_SUITE_NAME],
    )
    if not rows:
        raise ValueError(
            "No all-fields runs found in run_metadata for suite "
            f"'{ALL_FIELDS_SUITE_NAME}'."
        )
    return str(rows[0]["run_id"])


def _compute_field_quality_stats(
    conn: Any,
    *,
    source_table_name: str,
    run_id: str,
    fields: Sequence[str],
    batch_size: int,
    universe_filter_sql: str = "",
) -> dict[str, FieldQualityStat]:
    stats: dict[str, FieldQualityStat] = {}
    if not fields:
        return stats

    table_sql = _quote_identifier(source_table_name)
    total_rows_result = conn.execute(
        f"""
        SELECT COUNT(*) AS row_count
        FROM {table_sql}
        WHERE run_id = ?{universe_filter_sql}
        """,
        [run_id],
    ).fetchone()
    total_rows = int(total_rows_result[0] if total_rows_result else 0)

    for chunk in _chunked(list(fields), batch_size):
        select_parts: list[str] = []
        for index, field_name in enumerate(chunk):
            field_sql = _quote_identifier(field_name)
            numeric_expr = _numeric_sql_expression(field_sql)
            select_parts.extend(
                [
                    (
                        "SUM(CASE WHEN NULLIF(TRIM(CAST("
                        + field_sql
                        + " AS VARCHAR)), '') IS NOT NULL THEN 1 ELSE 0 END) "
                        + f"AS {_quote_identifier(f'nb_{index}')}"
                    ),
                    (
                        "SUM(CASE WHEN "
                        + numeric_expr
                        + " IS NOT NULL THEN 1 ELSE 0 END) "
                        + f"AS {_quote_identifier(f'num_{index}')}"
                    ),
                ]
            )
        if not select_parts:
            continue
        query_sql = (
            "SELECT "
            + ", ".join(select_parts)
            + f" FROM {table_sql} WHERE run_id = ?{universe_filter_sql}"
        )
        row = conn.execute(query_sql, [run_id]).fetchone()
        if row is None:
            continue
        row_values = list(row)
        for index, field_name in enumerate(chunk):
            non_blank_count = int(row_values[index * 2] or 0)
            numeric_count = int(row_values[index * 2 + 1] or 0)
            fill_rate = (
                float(non_blank_count) / float(total_rows) if total_rows > 0 else 0.0
            )
            numeric_parse_rate = (
                float(numeric_count) / float(non_blank_count)
                if non_blank_count > 0
                else 0.0
            )
            stats[field_name] = FieldQualityStat(
                field_name=field_name,
                total_rows=total_rows,
                non_blank_count=non_blank_count,
                numeric_count=numeric_count,
                fill_rate=fill_rate,
                numeric_parse_rate=numeric_parse_rate,
            )
    return stats


def _build_pairwise_metrics_chunk_sql(
    *,
    source_table_name: str,
    run_id: str,
    predictor_fields: Sequence[str],
    performance_field: str,
    quintile_count: int,
    universe_filter_sql: str = "",
) -> str:
    if not predictor_fields:
        return (
            "SELECT CAST(NULL AS VARCHAR) AS predictor_field, "
            "CAST(NULL AS BIGINT) AS pair_n, "
            "CAST(NULL AS DOUBLE) AS pearson_corr, "
            "CAST(NULL AS DOUBLE) AS spearman_corr, "
            "CAST(NULL AS DOUBLE) AS top_quintile_avg_perf, "
            "CAST(NULL AS DOUBLE) AS bottom_quintile_avg_perf, "
            "CAST(NULL AS DOUBLE) AS top_quintile_median_perf, "
            "CAST(NULL AS DOUBLE) AS bottom_quintile_median_perf, "
            "CAST(NULL AS DOUBLE) AS performance_stddev, "
            "CAST(NULL AS DOUBLE) AS performance_median, "
            "CAST(NULL AS DOUBLE) AS predictor_median, "
            "CAST(NULL AS DOUBLE) AS above_median_predictor_outperforms_rate, "
            "CAST(NULL AS DOUBLE) AS below_median_predictor_outperforms_rate "
            "WHERE FALSE"
        )

    source_table_sql = _quote_identifier(source_table_name)
    run_literal = _quote_sql_literal(run_id)
    performance_sql = _quote_identifier(performance_field)
    performance_numeric = _numeric_sql_expression(performance_sql)
    predictor_numeric_select_sql = ",\n            ".join(
        (
            _numeric_sql_expression(_quote_identifier(field_name))
            + " AS "
            + _quote_identifier(field_name)
        )
        for field_name in predictor_fields
    )
    predictor_identifier_list_sql = ", ".join(
        _quote_identifier(field_name) for field_name in predictor_fields
    )

    return f"""
    WITH base_rows AS (
        SELECT {performance_numeric} AS performance_value,
            {predictor_numeric_select_sql}
        FROM {source_table_sql}
        WHERE run_id = {run_literal}{universe_filter_sql}
    ),
    long_values AS (
        SELECT predictor_field,
            predictor_value,
            performance_value
        FROM base_rows
        UNPIVOT(predictor_value FOR predictor_field IN ({predictor_identifier_list_sql}))
    ),
    filtered AS (
        SELECT predictor_field,
            predictor_value,
            performance_value
        FROM long_values
        WHERE {_finite_numeric_filter_sql("predictor_value", max_abs=1e12)}
          AND {_finite_numeric_filter_sql("performance_value", max_abs=1e4)}
    ),
    medians AS (
        SELECT predictor_field,
            MEDIAN(predictor_value) AS predictor_median,
            MEDIAN(performance_value) AS performance_median
        FROM filtered
        GROUP BY predictor_field
    ),
    ranked AS (
        SELECT f.predictor_field,
            f.predictor_value,
            f.performance_value,
            m.predictor_median,
            m.performance_median,
            NTILE({int(quintile_count)}) OVER (
                PARTITION BY f.predictor_field
                ORDER BY f.predictor_value
            ) AS predictor_ntile,
            RANK() OVER (
                PARTITION BY f.predictor_field
                ORDER BY f.predictor_value
            ) AS predictor_rank,
            RANK() OVER (
                PARTITION BY f.predictor_field
                ORDER BY f.performance_value
            ) AS performance_rank
        FROM filtered f
        JOIN medians m USING(predictor_field)
    )
    SELECT predictor_field,
        COUNT(*) AS pair_n,
        CORR(predictor_value, performance_value) AS pearson_corr,
        CORR(predictor_rank, performance_rank) AS spearman_corr,
        AVG(
            CASE
                WHEN predictor_ntile = {int(quintile_count)} THEN performance_value
                ELSE NULL
            END
        ) AS top_quintile_avg_perf,
        AVG(
            CASE
                WHEN predictor_ntile = 1 THEN performance_value
                ELSE NULL
            END
        ) AS bottom_quintile_avg_perf,
        MEDIAN(
            CASE
                WHEN predictor_ntile = {int(quintile_count)} THEN performance_value
                ELSE NULL
            END
        ) AS top_quintile_median_perf,
        MEDIAN(
            CASE
                WHEN predictor_ntile = 1 THEN performance_value
                ELSE NULL
            END
        ) AS bottom_quintile_median_perf,
        mad(performance_value) AS performance_stddev,
        ANY_VALUE(performance_median) AS performance_median,
        ANY_VALUE(predictor_median) AS predictor_median,
        AVG(
            CASE
                WHEN predictor_value > predictor_median THEN
                    CASE WHEN performance_value > performance_median THEN 1.0 ELSE 0.0 END
                ELSE NULL
            END
        ) AS above_median_predictor_outperforms_rate,
        AVG(
            CASE
                WHEN predictor_value < predictor_median THEN
                    CASE WHEN performance_value > performance_median THEN 1.0 ELSE 0.0 END
                ELSE NULL
            END
        ) AS below_median_predictor_outperforms_rate
    FROM ranked
    GROUP BY predictor_field
    """


def _fetch_query_rows(conn: Any, query_sql: str) -> list[dict[str, Any]]:
    cursor = conn.execute(query_sql)
    if cursor.description is None:
        return []
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _execute_pairwise_metric_query_chunk(
    database_path: Path | None = None,
    *,
    conn: Any | None = None,
    source_table_name: str,
    run_id: str,
    predictor_fields: Sequence[str],
    performance_field: str,
    quintile_count: int,
    universe_filter_sql: str = "",
    duckdb_threads: int | None = None,
    duckdb_memory_limit: str | int | float | None = None,
    duckdb_temp_directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    query_sql = _build_pairwise_metrics_chunk_sql(
        source_table_name=source_table_name,
        run_id=run_id,
        predictor_fields=predictor_fields,
        performance_field=performance_field,
        quintile_count=quintile_count,
        universe_filter_sql=universe_filter_sql,
    )
    if conn is not None:
        return _fetch_query_rows(conn, query_sql)

    if database_path is None:
        raise ValueError("database_path is required when conn is not provided.")

    with open_move_prediction_duckdb_connection(
        database_path,
        read_only=True,
        threads=duckdb_threads,
        memory_limit=duckdb_memory_limit,
        temp_directory=duckdb_temp_directory,
    ) as managed_conn:
        return _fetch_query_rows(managed_conn, query_sql)


def _build_chunk_tasks(
    *,
    performance_fields: Sequence[str],
    eligible_predictors: Sequence[str],
    field_batch_size: int,
) -> list[tuple[str, list[str]]]:
    tasks: list[tuple[str, list[str]]] = []
    for performance_field in performance_fields:
        for predictor_chunk in _chunked(eligible_predictors, field_batch_size):
            if predictor_chunk:
                tasks.append((performance_field, list(predictor_chunk)))
    return tasks


def _execute_parallel_chunk_task(task: dict[str, Any]) -> dict[str, Any]:
    predictor_fields = list(task["predictor_fields"])
    if not predictor_fields:
        return {"rows": [], "query_seconds": 0.0}
    query_started_at = time.perf_counter()
    rows = _execute_pairwise_metric_query_chunk(
        database_path=Path(task["database_path"]),
        source_table_name=str(task["source_table_name"]),
        run_id=str(task["run_id"]),
        predictor_fields=predictor_fields,
        performance_field=str(task["performance_field"]),
        quintile_count=int(task["quintile_count"]),
        universe_filter_sql=str(task.get("universe_filter_sql") or ""),
        duckdb_threads=(
            int(task["duckdb_threads"]) if task.get("duckdb_threads") else None
        ),
        duckdb_memory_limit=task.get("duckdb_memory_limit"),
        duckdb_temp_directory=task.get("duckdb_temp_directory"),
    )
    return {
        "rows": rows,
        "query_seconds": time.perf_counter() - query_started_at,
    }


def _resolve_pattern_analysis_worker_resources(
    *,
    max_parallel_runs: int,
    duckdb_threads: int | None,
    duckdb_memory_limit: str | int | float | None,
    max_system_memory_gb: float,
    memory_reserve_gb: float,
    estimated_run_memory_gb: float = DEFAULT_PATTERN_ESTIMATED_RUN_MEMORY_GB,
) -> tuple[int, int, float]:
    available_memory_gb = max(
        1.0, float(max_system_memory_gb) - float(memory_reserve_gb)
    )
    memory_per_run = max(1.0, float(estimated_run_memory_gb))
    max_workers_by_memory = max(1, int(available_memory_gb // memory_per_run))
    effective_workers = max(1, min(int(max_parallel_runs), max_workers_by_memory))

    if duckdb_memory_limit is None:
        per_worker_memory_gb = available_memory_gb / effective_workers
    elif isinstance(duckdb_memory_limit, str):
        parsed = _normalize_float(duckdb_memory_limit.lower().replace("gb", ""))
        per_worker_memory_gb = (
            min(parsed, available_memory_gb / effective_workers)
            if parsed is not None and parsed > 0
            else available_memory_gb / effective_workers
        )
    else:
        numeric_limit = _normalize_float(duckdb_memory_limit)
        per_worker_memory_gb = (
            min(numeric_limit, available_memory_gb / effective_workers)
            if numeric_limit is not None and numeric_limit > 0
            else available_memory_gb / effective_workers
        )

    cpu_count = os.cpu_count() or DEFAULT_PATTERN_DEFAULT_DUCKDB_THREADS
    base_threads = (
        int(duckdb_threads)
        if duckdb_threads is not None and int(duckdb_threads) > 0
        else min(DEFAULT_PATTERN_DEFAULT_DUCKDB_THREADS, cpu_count)
    )
    if effective_workers <= 1:
        per_worker_threads = max(1, base_threads)
    else:
        per_worker_threads = max(1, base_threads // effective_workers)
    return effective_workers, per_worker_threads, per_worker_memory_gb


def _format_memory_limit_text(value: str | int | float | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    parsed = _normalize_float(value)
    if parsed is None or parsed <= 0:
        return None
    return f"{parsed:.2f}GB"


def _normalized_close_return_period_label(
    *, source_day_label: str | None, run_created_at_utc: datetime | None
) -> str:
    day_label = str(source_day_label or "").strip()
    if day_label and _try_parse_day_label(day_label):
        return day_label
    if run_created_at_utc is not None:
        return run_created_at_utc.astimezone(timezone.utc).strftime("%d_%m_%Y")
    return "unknown_day"


def _choose_close_return_granularity(day_labels: Sequence[str]) -> str:
    parsed_days = [_try_parse_day_label(label) for label in day_labels]
    parsed_days = [day for day in parsed_days if day is not None]
    if len(parsed_days) <= 1:
        return "run"
    unique_days = sorted(set(parsed_days))
    if len(unique_days) <= 1:
        return "run"
    average_gap = sum(
        (current - previous).days
        for previous, current in zip(unique_days, unique_days[1:])
    ) / max(1, len(unique_days) - 1)
    return "week" if average_gap <= 8 else "run"


def _rolling_week_start_label(day_label: str) -> str:
    parsed = _try_parse_day_label(day_label)
    if parsed is None:
        return day_label
    monday = parsed.fromordinal(parsed.toordinal() - parsed.weekday())
    return monday.strftime("%d_%m_%Y")


def _infer_predictor_direction(
    predictor_field: str,
    catalog_entry: FieldCatalogEntry | None,
) -> str:
    normalized = predictor_field.lower()
    if any(marker in normalized for marker in LOWER_IS_BETTER_MARKERS):
        return "lower_is_better"
    if catalog_entry:
        joined = (
            f"{catalog_entry.display_name} {catalog_entry.explanation} {catalog_entry.model_use}"
        ).lower()
        if any(
            marker.replace("_", " ") in joined for marker in LOWER_IS_BETTER_MARKERS
        ):
            return "lower_is_better"
    return "higher_is_better"


def _normalize_spread(
    spread_value: float | None,
    performance_stddev: float | None,
) -> float:
    if spread_value is None:
        return 0.0
    std_value = (
        performance_stddev if performance_stddev and performance_stddev > 0 else None
    )
    if std_value is None:
        return 0.0
    normalized = abs(spread_value) / float(std_value)
    bounded = min(max(normalized, 0.0), 5.0)
    return bounded / 5.0


def _compute_pattern_score(
    *,
    adjusted_spearman_corr: float | None,
    adjusted_pearson_corr: float | None,
    adjusted_quintile_spread: float | None,
    normalized_quintile_spread: float,
    directional_outperforms_rate: float | None,
    pair_n: int,
    scan_data_count: int,
) -> float | None:
    if pair_n <= 0:
        return None

    sign_anchor = (
        adjusted_spearman_corr
        if adjusted_spearman_corr is not None
        else adjusted_pearson_corr
    )
    if sign_anchor is None:
        sign_anchor = adjusted_quintile_spread
    if sign_anchor is None:
        return None

    sign = 1.0 if sign_anchor >= 0 else -1.0
    corr_component = abs(adjusted_spearman_corr or adjusted_pearson_corr or 0.0)
    hit_rate_component = abs((directional_outperforms_rate or 0.5) - 0.5) * 2.0
    base_strength = (
        0.45 * corr_component
        + 0.35 * normalized_quintile_spread
        + 0.20 * hit_rate_component
    )
    coverage_ratio = (
        math.sqrt(max(0.0, min(1.0, pair_n / float(scan_data_count))))
        if scan_data_count > 0
        else 0.0
    )
    return sign * base_strength * coverage_ratio


def _append_pattern_rows_from_batch(
    *,
    result_rows: list[FieldPerformancePatternRow],
    batch_rows: Sequence[dict[str, Any]],
    performance_field: str,
    quality_stats: Mapping[str, FieldQualityStat],
    catalog: FieldCatalogIndex,
    min_pair_n: int,
    scan_data_count: int,
    source_database_path: str,
    source_day_label: str,
    run_id: str,
    run_created_at_utc: datetime | None,
    run_label: str,
) -> None:
    for batch_row in batch_rows:
        predictor_field = str(batch_row.get("predictor_field") or "")
        if not predictor_field:
            continue
        stats = quality_stats.get(predictor_field)
        if stats is None:
            continue
        pair_n = int(batch_row.get("pair_n") or 0)
        if pair_n <= 0:
            continue

        pearson_corr = _normalize_float(batch_row.get("pearson_corr"))
        spearman_corr = _normalize_float(batch_row.get("spearman_corr"))
        top_avg = _normalize_float(batch_row.get("top_quintile_avg_perf"))
        bottom_avg = _normalize_float(batch_row.get("bottom_quintile_avg_perf"))
        top_median = _normalize_float(batch_row.get("top_quintile_median_perf"))
        bottom_median = _normalize_float(batch_row.get("bottom_quintile_median_perf"))
        perf_stddev = _normalize_float(batch_row.get("performance_stddev"))
        perf_median = _normalize_float(batch_row.get("performance_median"))
        predictor_median = _normalize_float(batch_row.get("predictor_median"))
        above_rate = _normalize_float(
            batch_row.get("above_median_predictor_outperforms_rate")
        )
        below_rate = _normalize_float(
            batch_row.get("below_median_predictor_outperforms_rate")
        )
        quintile_spread = (
            (top_avg - bottom_avg)
            if top_avg is not None and bottom_avg is not None
            else None
        )
        quintile_median_spread = (
            (top_median - bottom_median)
            if top_median is not None and bottom_median is not None
            else None
        )

        predictor_catalog = catalog.get(predictor_field)
        performance_catalog = catalog.get(performance_field)
        predictor_direction = _infer_predictor_direction(
            predictor_field, predictor_catalog
        )
        direction_factor = -1.0 if predictor_direction == "lower_is_better" else 1.0

        pearson_adjusted = (
            pearson_corr * direction_factor if pearson_corr is not None else None
        )
        spearman_adjusted = (
            spearman_corr * direction_factor if spearman_corr is not None else None
        )
        quintile_spread_adjusted = (
            quintile_spread * direction_factor if quintile_spread is not None else None
        )
        quintile_median_spread_adjusted = (
            quintile_median_spread * direction_factor
            if quintile_median_spread is not None
            else None
        )
        directional_rate = (
            below_rate if predictor_direction == "lower_is_better" else above_rate
        )
        normalized_spread = _normalize_spread(
            quintile_spread_adjusted,
            perf_stddev,
        )

        warnings: list[str] = []
        if any(performance_field.startswith(prefix) for prefix in PERFORMANCE_PREFIXES):
            warnings.append(TRAILING_LOOKAHEAD_WARNING)
        if pair_n < min_pair_n:
            warnings.append("low_pair_n")

        pattern_score = _compute_pattern_score(
            adjusted_spearman_corr=spearman_adjusted,
            adjusted_pearson_corr=pearson_adjusted,
            adjusted_quintile_spread=quintile_spread_adjusted,
            normalized_quintile_spread=normalized_spread,
            directional_outperforms_rate=directional_rate,
            pair_n=pair_n,
            scan_data_count=scan_data_count,
        )

        result_rows.append(
            FieldPerformancePatternRow(
                source_database_path=source_database_path,
                source_day_label=source_day_label,
                run_id=run_id,
                run_created_at_utc=run_created_at_utc,
                run_label=run_label,
                scan_data_count=scan_data_count,
                predictor_field=predictor_field,
                predictor_display_name=(
                    predictor_catalog.display_name
                    if predictor_catalog and predictor_catalog.display_name
                    else predictor_field
                ),
                predictor_type=(
                    predictor_catalog.field_type if predictor_catalog else ""
                ),
                predictor_direction=predictor_direction,
                predictor_fill_rate=stats.fill_rate,
                predictor_numeric_parse_rate=stats.numeric_parse_rate,
                performance_field=performance_field,
                performance_display_name=(
                    performance_catalog.display_name
                    if performance_catalog and performance_catalog.display_name
                    else performance_field
                ),
                performance_type=(
                    performance_catalog.field_type if performance_catalog else ""
                ),
                pair_n=pair_n,
                pearson_corr=pearson_corr,
                spearman_corr=spearman_corr,
                pearson_corr_adjusted=pearson_adjusted,
                spearman_corr_adjusted=spearman_adjusted,
                top_quintile_avg_perf=top_avg,
                bottom_quintile_avg_perf=bottom_avg,
                quintile_spread=quintile_spread,
                quintile_spread_adjusted=quintile_spread_adjusted,
                top_quintile_median_perf=top_median,
                bottom_quintile_median_perf=bottom_median,
                quintile_median_spread=quintile_median_spread,
                quintile_median_spread_adjusted=quintile_median_spread_adjusted,
                performance_stddev=perf_stddev,
                performance_median=perf_median,
                predictor_median=predictor_median,
                above_median_predictor_outperforms_rate=above_rate,
                below_median_predictor_outperforms_rate=below_rate,
                directional_outperforms_rate=directional_rate,
                normalized_quintile_spread=normalized_spread,
                pattern_score=pattern_score,
                warnings=tuple(warnings),
            )
        )


def analyze_all_fields_run_performance_patterns(
    *,
    database_path: str | Path,
    run_id: str | None = None,
    performance_fields: Sequence[str] | None = None,
    predictor_fields: Sequence[str] | None = None,
    universe_filter: AllFieldsUniverseFilterInput = None,
    min_fill_rate: float = 0.15,
    min_pair_n: int = 50,
    quintile_count: int = 5,
    exclude_perf_from_predictors: bool = True,
    output_dir: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    write_exports: bool = True,
    duckdb_threads: int = 8,
    field_batch_size: int = 40,
    max_parallel_chunks: int = 1,
    duckdb_memory_limit: str | int | float | None = None,
    duckdb_temp_directory: str | Path | None = None,
    min_numeric_parse_rate: float = 0.90,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    source_table_name: str = "all_fields_rows",
    show_progress: bool | None = None,
) -> AllFieldsRunPatternAnalysisResult:
    resolved_database = Path(database_path)
    resolved_config = AllFieldsAnalysisConfig(
        min_fill_rate=min_fill_rate,
        min_pair_n=min_pair_n,
        quintile_count=quintile_count,
        field_batch_size=field_batch_size,
        duckdb_threads=duckdb_threads,
        min_numeric_parse_rate=min_numeric_parse_rate,
        exclude_perf_from_predictors=exclude_perf_from_predictors,
    )
    catalog = load_field_catalog(field_catalog_csv)
    resolved_run_id = resolve_run_id(resolved_database, run_id=run_id)
    run_metadata = _read_run_metadata_row(resolved_database, resolved_run_id)
    run_created_at_utc = _coerce_datetime_utc(run_metadata.get("created_at_utc"))
    run_label = str(run_metadata.get("run_label") or "")
    scan_data_count = int(run_metadata.get("scan_data_count") or 0)
    source_day_label = _extract_day_label_from_database_path(resolved_database)
    resolved_memory_limit = _format_memory_limit_text(duckdb_memory_limit)
    resolved_show_progress = (
        sys.stdout.isatty() if show_progress is None else bool(show_progress)
    )
    progress = _AllFieldsPatternRunProgress(enabled=resolved_show_progress)
    run_started_at = time.perf_counter()
    quality_stats_seconds = 0.0
    pairwise_seconds = 0.0
    export_seconds = 0.0
    chunk_task_count = 0
    filter_resolution = AllFieldsUniverseFilterResolution(
        sql_fragment="",
        applied_clauses=(),
        skipped_clauses=(),
        warnings=(),
    )
    universe_filter_sql = ""
    filtered_row_count = 0

    with open_move_prediction_duckdb_connection(
        resolved_database,
        read_only=True,
        threads=duckdb_threads,
        memory_limit=resolved_memory_limit,
        temp_directory=duckdb_temp_directory,
    ) as conn:
        all_columns = _read_table_columns(conn, source_table_name)
        filter_resolution = resolve_all_fields_universe_filter_sql(
            universe_filter,
            available_columns=all_columns,
        )
        universe_filter_sql = filter_resolution.sql_fragment
        for warning in filter_resolution.warnings:
            progress._print(f"[setup] {warning}")
        default_predictors, default_performance = classify_columns(
            all_columns,
            catalog=catalog,
            config=resolved_config,
        )
        resolved_performance_fields = list(
            performance_fields
            if performance_fields is not None
            else default_performance
        )
        resolved_performance_fields = [
            field_name
            for field_name in resolved_performance_fields
            if field_name in set(all_columns)
        ]
        if not resolved_performance_fields:
            raise ValueError(
                "No valid performance fields were resolved from the source table."
            )

        resolved_predictor_fields = list(
            predictor_fields if predictor_fields is not None else default_predictors
        )
        if resolved_config.exclude_perf_from_predictors:
            resolved_predictor_fields = [
                field_name
                for field_name in resolved_predictor_fields
                if field_name not in set(resolved_performance_fields)
            ]
        resolved_predictor_fields = [
            field_name
            for field_name in resolved_predictor_fields
            if field_name in set(all_columns)
            and field_name not in set(ALL_FIELDS_METADATA_COLUMNS)
        ]

        quality_batches = list(
            _chunked(list(resolved_predictor_fields), resolved_config.field_batch_size)
        )
        progress.quality_stats_started(
            len(resolved_predictor_fields),
            len(quality_batches),
        )
        quality_started_at = time.perf_counter()
        quality_stats = _compute_field_quality_stats(
            conn,
            source_table_name=source_table_name,
            run_id=resolved_run_id,
            fields=resolved_predictor_fields,
            batch_size=resolved_config.field_batch_size,
            universe_filter_sql=universe_filter_sql,
        )
        quality_stats_seconds = time.perf_counter() - quality_started_at
        skipped_predictors: dict[str, str] = {}
        eligible_predictors: list[str] = []
        for field_name in resolved_predictor_fields:
            stats = quality_stats.get(field_name)
            if stats is None:
                skipped_predictors[field_name] = "missing_quality_stats"
                continue
            if stats.fill_rate < resolved_config.min_fill_rate:
                skipped_predictors[field_name] = (
                    f"fill_rate<{resolved_config.min_fill_rate:.2f}"
                )
                continue
            if stats.numeric_parse_rate < resolved_config.min_numeric_parse_rate:
                skipped_predictors[field_name] = (
                    f"numeric_parse_rate<{resolved_config.min_numeric_parse_rate:.2f}"
                )
                continue
            eligible_predictors.append(field_name)
        progress.quality_stats_finished(
            len(eligible_predictors),
            quality_stats_seconds,
        )

        result_rows: list[FieldPerformancePatternRow] = []
        filtered_row_count = _count_universe_filtered_rows(
            conn,
            source_table_name=source_table_name,
            run_id=resolved_run_id,
            universe_filter_sql=universe_filter_sql,
        )
        if filter_resolution.applied_clauses:
            scan_data_count = filtered_row_count
        elif scan_data_count <= 0 and quality_stats:
            scan_data_count = max(stat.total_rows for stat in quality_stats.values())
        elif scan_data_count <= 0:
            scan_data_count = filtered_row_count
        if filter_resolution.applied_clauses and filtered_row_count <= 0:
            progress._print(
                "[setup] Universe filter removed all rows; analysis will emit no patterns."
            )
        chunk_tasks = _build_chunk_tasks(
            performance_fields=resolved_performance_fields,
            eligible_predictors=eligible_predictors,
            field_batch_size=resolved_config.field_batch_size,
        )
        resolved_parallel_chunks = max(1, int(max_parallel_chunks))
        progress.run_started(
            source_day_label=source_day_label,
            run_id=resolved_run_id,
            scan_data_count=scan_data_count,
            performance_field_count=len(resolved_performance_fields),
            eligible_predictor_count=len(eligible_predictors),
            total_chunks=len(chunk_tasks),
            max_parallel_chunks=resolved_parallel_chunks,
            duckdb_threads=duckdb_threads,
            field_batch_size=resolved_config.field_batch_size,
        )
        progress.pairwise_started()
        pairwise_started_at = time.perf_counter()
        if resolved_parallel_chunks <= 1:
            for performance_field, predictor_chunk in chunk_tasks:
                chunk_started_at = time.perf_counter()
                batch_rows = _execute_pairwise_metric_query_chunk(
                    conn=conn,
                    source_table_name=source_table_name,
                    run_id=resolved_run_id,
                    predictor_fields=predictor_chunk,
                    performance_field=performance_field,
                    quintile_count=resolved_config.quintile_count,
                    universe_filter_sql=universe_filter_sql,
                )
                rows_before = len(result_rows)
                _append_pattern_rows_from_batch(
                    result_rows=result_rows,
                    batch_rows=batch_rows,
                    performance_field=performance_field,
                    quality_stats=quality_stats,
                    catalog=catalog,
                    min_pair_n=resolved_config.min_pair_n,
                    scan_data_count=scan_data_count,
                    source_database_path=resolved_database.as_posix(),
                    source_day_label=source_day_label,
                    run_id=resolved_run_id,
                    run_created_at_utc=run_created_at_utc,
                    run_label=run_label,
                )
                progress.chunk_finished(
                    performance_field=performance_field,
                    predictor_count=len(predictor_chunk),
                    rows_emitted=len(result_rows) - rows_before,
                    query_seconds=time.perf_counter() - chunk_started_at,
                )
        else:
            process_tasks = [
                {
                    "database_path": resolved_database.as_posix(),
                    "source_table_name": source_table_name,
                    "run_id": resolved_run_id,
                    "predictor_fields": predictor_chunk,
                    "performance_field": performance_field,
                    "quintile_count": resolved_config.quintile_count,
                    "universe_filter_sql": universe_filter_sql,
                    "duckdb_threads": duckdb_threads,
                    "duckdb_memory_limit": resolved_memory_limit,
                    "duckdb_temp_directory": (
                        Path(duckdb_temp_directory).as_posix()
                        if duckdb_temp_directory is not None
                        else None
                    ),
                }
                for performance_field, predictor_chunk in chunk_tasks
            ]
            with ProcessPoolExecutor(max_workers=resolved_parallel_chunks) as executor:
                future_map = {
                    executor.submit(_execute_parallel_chunk_task, task): task
                    for task in process_tasks
                }
                for future in as_completed(future_map):
                    task = future_map[future]
                    performance_field = str(task["performance_field"])
                    predictor_chunk = list(task["predictor_fields"])
                    chunk_result = future.result()
                    batch_rows = chunk_result["rows"]
                    query_seconds = float(chunk_result["query_seconds"])
                    rows_before = len(result_rows)
                    _append_pattern_rows_from_batch(
                        result_rows=result_rows,
                        batch_rows=batch_rows,
                        performance_field=performance_field,
                        quality_stats=quality_stats,
                        catalog=catalog,
                        min_pair_n=resolved_config.min_pair_n,
                        scan_data_count=scan_data_count,
                        source_database_path=resolved_database.as_posix(),
                        source_day_label=source_day_label,
                        run_id=resolved_run_id,
                        run_created_at_utc=run_created_at_utc,
                        run_label=run_label,
                    )
                    progress.chunk_finished(
                        performance_field=performance_field,
                        predictor_count=len(predictor_chunk),
                        rows_emitted=len(result_rows) - rows_before,
                        query_seconds=query_seconds,
                    )
        pairwise_seconds = time.perf_counter() - pairwise_started_at
        chunk_task_count = len(chunk_tasks)

    result_rows_sorted = sorted(
        result_rows,
        key=lambda row: (
            row.pattern_score if row.pattern_score is not None else float("-inf")
        ),
        reverse=True,
    )

    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    analysis_run_id = _build_run_analysis_id(
        "all_fields_pattern",
        scope_label=source_day_label,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    base_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else DEFAULT_PATTERN_ANALYSIS_ROOT / "runs"
    )
    run_output_dir = base_output_dir / analysis_run_id
    per_run_dir = run_output_dir / "per_run" / resolved_run_id
    exports: dict[str, str] = {}

    if write_exports:
        progress.export_started()
        export_started_at = time.perf_counter()
        per_run_dir.mkdir(parents=True, exist_ok=True)
        rows_as_dict = [row.to_dict() for row in result_rows_sorted]
        csv_path = _write_csv_rows(
            per_run_dir / "field_performance_patterns.csv",
            rows_as_dict,
            fieldnames=FIELD_PATTERN_EXPORT_COLUMNS,
        )
        parquet_path = _write_parquet_from_csv(
            csv_path,
            per_run_dir / "field_performance_patterns.parquet",
            temp_directory=duckdb_temp_directory,
        )
        export_seconds = time.perf_counter() - export_started_at
        progress.export_finished(len(result_rows_sorted), export_seconds)
        context_payload = {
            "analysis_run_id": analysis_run_id,
            "run_lifecycle_id": resolved_run_lifecycle_id,
            "database_path": resolved_database.as_posix(),
            "source_table_name": source_table_name,
            "run_id": resolved_run_id,
            "run_created_at_utc": (
                run_created_at_utc.isoformat() if run_created_at_utc else None
            ),
            "run_label": run_label,
            "scan_data_count": scan_data_count,
            "filtered_row_count": filtered_row_count,
            "universe_filter": serialize_all_fields_universe_filter(
                normalize_all_fields_universe_filter(universe_filter)
            ),
            "universe_filter_applied_clauses": [
                _universe_filter_clause_to_dict(clause)
                for clause in filter_resolution.applied_clauses
            ],
            "universe_filter_skipped_clauses": [
                {"field": field_name, "reason": reason}
                for field_name, reason in filter_resolution.skipped_clauses
            ],
            "source_day_label": source_day_label,
            "predictor_fields_requested": resolved_predictor_fields,
            "predictor_fields_eligible": eligible_predictors,
            "predictor_fields_skipped": skipped_predictors,
            "performance_fields": resolved_performance_fields,
            "timing": {
                "total_seconds": time.perf_counter() - run_started_at,
                "quality_stats_seconds": quality_stats_seconds,
                "pairwise_seconds": pairwise_seconds,
                "export_seconds": export_seconds,
                "chunk_count": chunk_task_count,
                "avg_query_seconds": progress.avg_query_seconds(),
                "throughput_chunks_per_second": (
                    chunk_task_count / pairwise_seconds
                    if pairwise_seconds > 0 and chunk_task_count
                    else 0.0
                ),
            },
            "config": {
                "min_fill_rate": resolved_config.min_fill_rate,
                "min_numeric_parse_rate": resolved_config.min_numeric_parse_rate,
                "min_pair_n": resolved_config.min_pair_n,
                "quintile_count": resolved_config.quintile_count,
                "field_batch_size": resolved_config.field_batch_size,
                "duckdb_threads": resolved_config.duckdb_threads,
                "max_parallel_chunks": resolved_parallel_chunks,
                "duckdb_memory_limit": resolved_memory_limit,
                "duckdb_temp_directory": (
                    Path(duckdb_temp_directory).as_posix()
                    if duckdb_temp_directory is not None
                    else None
                ),
            },
        }
        context_path = _write_json(per_run_dir / "run_context.json", context_payload)
        overview_lines = [
            "TradingView all-fields run pattern analysis",
            f"analysis_run_id={analysis_run_id}",
            f"run_lifecycle_id={resolved_run_lifecycle_id}",
            f"database_path={resolved_database.as_posix()}",
            f"source_table_name={source_table_name}",
            f"run_id={resolved_run_id}",
            f"source_day_label={source_day_label}",
            f"scan_data_count={scan_data_count}",
            f"filtered_row_count={filtered_row_count}",
            f"universe_filter_clauses={len(filter_resolution.applied_clauses)}",
            f"predictor_fields_requested={len(resolved_predictor_fields)}",
            f"predictor_fields_eligible={len(eligible_predictors)}",
            f"rows_emitted={len(result_rows_sorted)}",
            "",
            f"Top patterns (up to {OVERVIEW_LOG_TOP_PATTERNS}):",
        ]
        overview_lines.extend(
            _format_field_pattern_highlight_lines(
                result_rows_sorted,
                limit=OVERVIEW_LOG_TOP_PATTERNS,
            )
        )
        overview_path = run_output_dir / "_pattern_analysis_overview.log"
        _write_overview_log(overview_path, overview_lines)

        exports = {
            "csv": csv_path.as_posix(),
            "parquet": parquet_path.as_posix(),
            "context_json": context_path.as_posix(),
            "overview_log": overview_path.as_posix(),
            "output_dir": run_output_dir.as_posix(),
        }

    progress.run_finished(
        rows_emitted=len(result_rows_sorted),
        quality_stats_seconds=quality_stats_seconds,
        pairwise_seconds=pairwise_seconds,
        export_seconds=export_seconds,
    )

    return AllFieldsRunPatternAnalysisResult(
        analysis_run_id=analysis_run_id,
        run_lifecycle_id=resolved_run_lifecycle_id,
        output_dir=run_output_dir,
        database_path=resolved_database,
        source_table_name=source_table_name,
        source_day_label=source_day_label,
        run_id=resolved_run_id,
        run_created_at_utc=run_created_at_utc,
        run_label=run_label,
        scan_data_count=scan_data_count,
        predictor_fields=resolved_predictor_fields,
        eligible_predictor_fields=eligible_predictors,
        performance_fields=resolved_performance_fields,
        skipped_predictors=skipped_predictors,
        rows=result_rows_sorted,
        exports=exports,
    )


def _resolve_summary_paths(
    *,
    per_run_summary_paths: Sequence[str | Path] | None,
    per_run_summary_dirs: Sequence[str | Path] | None,
) -> list[Path]:
    resolved: list[Path] = []
    seen: set[str] = set()
    for raw_path in per_run_summary_paths or []:
        path = Path(raw_path)
        if not path.exists():
            continue
        normalized = path.resolve().as_posix().lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(path)

    for raw_dir in per_run_summary_dirs or []:
        directory = Path(raw_dir)
        if not directory.exists():
            continue
        for candidate in sorted(directory.rglob("field_performance_patterns.parquet")):
            normalized = candidate.resolve().as_posix().lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            resolved.append(candidate)
        for candidate in sorted(directory.rglob("field_performance_patterns.csv")):
            normalized = candidate.resolve().as_posix().lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            resolved.append(candidate)
    return resolved


def aggregate_all_fields_pattern_summaries(
    *,
    per_run_summary_paths: Sequence[str | Path] | None = None,
    per_run_summary_dirs: Sequence[str | Path] | None = None,
    output_dir: str | Path,
    min_runs_for_stability: int = 3,
    require_sign_consistency: float = 0.6,
    group_by: Sequence[str] = (),
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    include_close_forward_return: bool = False,
    close_forward_return_paths: Sequence[str | Path] | None = None,
    scope_label: str | None = None,
    run_lifecycle_id: str | None = None,
    nest_output_dir: bool = True,
) -> AllFieldsPatternAggregateResult:
    del group_by  # Reserved for future segmentation outputs.
    resolved_paths = _resolve_summary_paths(
        per_run_summary_paths=per_run_summary_paths,
        per_run_summary_dirs=per_run_summary_dirs,
    )
    if not resolved_paths:
        raise ValueError("No per-run summary files were provided for aggregation.")

    aggregate_scope = scope_label
    if not aggregate_scope and include_close_forward_return:
        aggregate_scope = "trailing_plus_close_forward"
    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    aggregate_id = _build_run_analysis_id(
        "all_fields_pattern_aggregate",
        scope_label=aggregate_scope,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    aggregate_output_root = (
        Path(output_dir) / aggregate_id
        if nest_output_dir
        else Path(output_dir)
    )
    aggregate_output_root.mkdir(parents=True, exist_ok=True)
    aggregate_db_path = aggregate_output_root / "all_fields_pattern_aggregate.duckdb"

    with open_move_prediction_duckdb_connection(
        aggregate_db_path,
        read_only=False,
        threads=8,
    ) as conn:
        conn.execute("DROP TABLE IF EXISTS per_run_patterns")
        for index, summary_path in enumerate(resolved_paths):
            relation_sql = (
                f"SELECT * FROM read_parquet({_quote_path_literal(summary_path)})"
                if summary_path.suffix.lower() == ".parquet"
                else f"SELECT * FROM read_csv_auto({_quote_path_literal(summary_path)}, HEADER=true)"
            )
            if index == 0:
                conn.execute(f"CREATE TABLE per_run_patterns AS {relation_sql}")
            else:
                conn.execute("INSERT INTO per_run_patterns BY NAME " + relation_sql)
        if include_close_forward_return:
            extra_paths = [
                Path(path)
                for path in (close_forward_return_paths or [])
                if Path(path).exists()
            ]
            if extra_paths:
                for extra_path in extra_paths:
                    relation_sql = (
                        f"SELECT * FROM read_parquet({_quote_path_literal(extra_path)})"
                        if extra_path.suffix.lower() == ".parquet"
                        else f"SELECT * FROM read_csv_auto({_quote_path_literal(extra_path)}, HEADER=true)"
                    )
                    conn.execute("INSERT INTO per_run_patterns BY NAME " + relation_sql)

        conn.execute(
            f"""
            CREATE OR REPLACE TABLE cross_run_field_stability AS
            WITH base AS (
                SELECT predictor_field,
                    performance_field,
                    run_id,
                    TRY_CAST(pearson_corr_adjusted AS DOUBLE) AS pearson_corr,
                    TRY_CAST(quintile_spread_adjusted AS DOUBLE) AS quintile_spread,
                    TRY_CAST(pattern_score AS DOUBLE) AS pattern_score
                FROM per_run_patterns
            ),
            majority_sign AS (
                SELECT predictor_field,
                    performance_field,
                    CASE
                        WHEN SUM(
                            CASE
                                WHEN COALESCE(pearson_corr, 0.0) >= 0 THEN 1
                                ELSE -1
                            END
                        ) >= 0 THEN 1
                        ELSE -1
                    END AS majority_sign
                FROM base
                GROUP BY predictor_field, performance_field
            )
            SELECT b.predictor_field,
                b.performance_field,
                COUNT(*) AS runs_seen,
                SUM(CASE WHEN b.pattern_score IS NOT NULL THEN 1 ELSE 0 END) AS runs_with_signal,
                AVG(b.pearson_corr) AS mean_pearson,
                MEDIAN(b.pearson_corr) AS median_pearson,
                STDDEV_POP(b.pearson_corr) AS stddev_pearson,
                AVG(b.quintile_spread) AS mean_quintile_spread,
                MEDIAN(b.quintile_spread) AS median_quintile_spread,
                AVG(
                    CASE
                        WHEN (
                            CASE
                                WHEN COALESCE(b.pearson_corr, 0.0) >= 0 THEN 1
                                ELSE -1
                            END
                        ) = ms.majority_sign THEN 1.0
                        ELSE 0.0
                    END
                ) AS sign_consistency_ratio,
                ARG_MAX(b.run_id, b.pattern_score) AS best_run_id,
                ARG_MIN(b.run_id, b.pattern_score) AS worst_run_id,
                MEDIAN(b.quintile_spread)
                    * AVG(
                        CASE
                            WHEN (
                                CASE
                                    WHEN COALESCE(b.pearson_corr, 0.0) >= 0 THEN 1
                                    ELSE -1
                                END
                            ) = ms.majority_sign THEN 1.0
                            ELSE 0.0
                        END
                    ) AS rank_stability_score
            FROM base b
            JOIN majority_sign ms
                ON ms.predictor_field = b.predictor_field
                AND ms.performance_field = b.performance_field
            GROUP BY b.predictor_field, b.performance_field, ms.majority_sign
            HAVING COUNT(*) >= {int(min_runs_for_stability)}
                AND AVG(
                    CASE
                        WHEN (
                            CASE
                                WHEN COALESCE(b.pearson_corr, 0.0) >= 0 THEN 1
                                ELSE -1
                            END
                        ) = ms.majority_sign THEN 1.0
                        ELSE 0.0
                    END
                ) >= {float(require_sign_consistency)}
            """
        )
        conn.execute(
            "COPY (SELECT * FROM cross_run_field_stability) TO "
            + _quote_path_literal(
                aggregate_output_root / "cross_run_field_stability.parquet"
            )
            + " (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        conn.execute(
            "COPY (SELECT * FROM cross_run_field_stability "
            "ORDER BY ABS(rank_stability_score) DESC NULLS LAST, runs_seen DESC) TO "
            + _quote_path_literal(aggregate_output_root / "top_patterns_report.csv")
            + " (FORMAT CSV, HEADER true)"
        )
        aggregate_rows = conn.execute(
            """
            SELECT predictor_field,
                performance_field,
                runs_seen,
                runs_with_signal,
                mean_pearson,
                median_pearson,
                stddev_pearson,
                mean_quintile_spread,
                median_quintile_spread,
                sign_consistency_ratio,
                best_run_id,
                worst_run_id,
                rank_stability_score
            FROM cross_run_field_stability
            ORDER BY ABS(rank_stability_score) DESC NULLS LAST, runs_seen DESC
            """
        ).fetchall()
        aggregate_columns = [col[0] for col in conn.description]

    catalog = load_field_catalog(field_catalog_csv)
    rows: list[CrossRunFieldStabilityRow] = []
    for row in aggregate_rows:
        payload = dict(zip(aggregate_columns, row))
        rows.append(
            CrossRunFieldStabilityRow(
                predictor_field=str(payload.get("predictor_field") or ""),
                performance_field=str(payload.get("performance_field") or ""),
                runs_seen=int(payload.get("runs_seen") or 0),
                runs_with_signal=int(payload.get("runs_with_signal") or 0),
                mean_pearson=_normalize_float(payload.get("mean_pearson")),
                median_pearson=_normalize_float(payload.get("median_pearson")),
                stddev_pearson=_normalize_float(payload.get("stddev_pearson")),
                mean_quintile_spread=_normalize_float(
                    payload.get("mean_quintile_spread")
                ),
                median_quintile_spread=_normalize_float(
                    payload.get("median_quintile_spread")
                ),
                sign_consistency_ratio=_normalize_float(
                    payload.get("sign_consistency_ratio")
                ),
                best_run_id=str(payload.get("best_run_id") or ""),
                worst_run_id=str(payload.get("worst_run_id") or ""),
                rank_stability_score=_normalize_float(
                    payload.get("rank_stability_score")
                ),
            )
        )

    overview_lines = [
        "TradingView all-fields cross-run pattern aggregation",
        f"aggregate_id={aggregate_id}",
        f"run_lifecycle_id={resolved_run_lifecycle_id}",
        f"summary_file_count={len(resolved_paths)}",
        f"min_runs_for_stability={min_runs_for_stability}",
        f"require_sign_consistency={require_sign_consistency}",
        f"aggregate_database={aggregate_db_path.as_posix()}",
        "",
        f"Top patterns (up to {OVERVIEW_LOG_TOP_PATTERNS}):",
    ]
    overview_lines.extend(
        _format_stability_highlight_lines(
            rows,
            catalog,
            limit=OVERVIEW_LOG_TOP_PATTERNS,
        )
    )
    overview_path = aggregate_output_root / "_aggregate_overview.log"
    _write_overview_log(overview_path, overview_lines)

    exports = {
        "aggregate_database": aggregate_db_path.as_posix(),
        "cross_run_field_stability_parquet": (
            aggregate_output_root / "cross_run_field_stability.parquet"
        ).as_posix(),
        "top_patterns_report_csv": (
            aggregate_output_root / "top_patterns_report.csv"
        ).as_posix(),
        "overview_log": overview_path.as_posix(),
        "output_dir": aggregate_output_root.as_posix(),
    }
    return AllFieldsPatternAggregateResult(
        aggregate_id=aggregate_id,
        output_dir=aggregate_output_root,
        database_path=aggregate_db_path,
        source_summary_paths=resolved_paths,
        rows=rows,
        exports=exports,
    )


def _run_single_batch_analysis_task(task: dict[str, Any]) -> dict[str, Any]:
    result = analyze_all_fields_run_performance_patterns(
        database_path=Path(task["database_path"]),
        run_id=task["run_id"],
        performance_fields=task.get("performance_fields"),
        predictor_fields=task.get("predictor_fields"),
        universe_filter=task.get("universe_filter"),
        min_fill_rate=float(task["min_fill_rate"]),
        min_pair_n=int(task["min_pair_n"]),
        quintile_count=int(task["quintile_count"]),
        exclude_perf_from_predictors=bool(task["exclude_perf_from_predictors"]),
        output_dir=Path(task["output_dir"]),
        write_exports=bool(task["write_exports"]),
        duckdb_threads=int(task["duckdb_threads"]),
        field_batch_size=int(task["field_batch_size"]),
        max_parallel_chunks=int(task.get("max_parallel_chunks") or 1),
        duckdb_memory_limit=task.get("duckdb_memory_limit"),
        duckdb_temp_directory=task.get("duckdb_temp_directory"),
        min_numeric_parse_rate=float(task["min_numeric_parse_rate"]),
        field_catalog_csv=Path(task["field_catalog_csv"]),
        source_table_name=task.get("source_table_name", "all_fields_rows"),
        run_lifecycle_id=task.get("run_lifecycle_id"),
        show_progress=False,
    )
    return {
        "database_path": result.database_path.as_posix(),
        "run_id": result.run_id,
        "analysis_run_id": result.analysis_run_id,
        "summary_csv": result.exports.get("csv"),
        "summary_parquet": result.exports.get("parquet"),
        "overview_log": result.exports.get("overview_log"),
        "rows_emitted": len(result.rows),
        "eligible_predictors": len(result.eligible_predictor_fields),
    }


def run_all_fields_pattern_analysis_batch(
    *,
    start_day_label: str,
    end_day_label: str,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    max_parallel_runs: int = 4,
    aggregate_after: bool = True,
    min_scan_data_count: int = 3000,
    performance_fields: Sequence[str] | None = None,
    predictor_fields: Sequence[str] | None = None,
    universe_filter: AllFieldsUniverseFilterInput = None,
    min_fill_rate: float = 0.15,
    min_pair_n: int = 50,
    quintile_count: int = 5,
    exclude_perf_from_predictors: bool = True,
    write_exports: bool = True,
    duckdb_threads: int = 8,
    field_batch_size: int = 40,
    max_parallel_chunks: int = 1,
    duckdb_memory_limit: str | int | float | None = None,
    duckdb_temp_directory: str | Path | None = None,
    max_system_memory_gb: float = DEFAULT_PATTERN_MAX_SYSTEM_MEMORY_GB,
    memory_reserve_gb: float = DEFAULT_PATTERN_MEMORY_RESERVE_GB,
    estimated_run_memory_gb: float = DEFAULT_PATTERN_ESTIMATED_RUN_MEMORY_GB,
    min_numeric_parse_rate: float = 0.90,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    output_dir: str | Path | None = None,
    aggregate_output_dir: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    show_progress: bool | None = None,
) -> dict[str, Any]:
    resolved_databases = discover_all_fields_daily_databases(
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not resolved_databases:
        raise ValueError(
            "No all-fields daily databases were discovered for the provided range."
        )

    batch_scope = _format_day_range_scope_label(start_day_label, end_day_label)
    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    batch_id = _build_run_analysis_id(
        "all_fields_pattern_batch",
        scope_label=batch_scope,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    batch_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else DEFAULT_PATTERN_ANALYSIS_ROOT / "runs" / batch_id
    )
    batch_output_dir.mkdir(parents=True, exist_ok=True)
    effective_runs, effective_threads, per_worker_memory_gb = (
        _resolve_pattern_analysis_worker_resources(
            max_parallel_runs=max_parallel_runs,
            duckdb_threads=duckdb_threads,
            duckdb_memory_limit=duckdb_memory_limit,
            max_system_memory_gb=max_system_memory_gb,
            memory_reserve_gb=memory_reserve_gb,
            estimated_run_memory_gb=estimated_run_memory_gb,
        )
    )
    resolved_memory_limit = _format_memory_limit_text(
        duckdb_memory_limit
        if duckdb_memory_limit is not None
        else f"{per_worker_memory_gb:.2f}GB"
    )
    serialized_universe_filter = serialize_all_fields_universe_filter(
        normalize_all_fields_universe_filter(universe_filter)
    )

    tasks: list[dict[str, Any]] = []
    for database_path in resolved_databases:
        for run in inventory_all_fields_runs(database_path):
            if run.scan_data_count < min_scan_data_count:
                continue
            tasks.append(
                {
                    "database_path": database_path.as_posix(),
                    "run_id": run.run_id,
                    "performance_fields": (
                        list(performance_fields)
                        if performance_fields is not None
                        else None
                    ),
                    "predictor_fields": (
                        list(predictor_fields) if predictor_fields is not None else None
                    ),
                    "universe_filter": serialized_universe_filter,
                    "min_fill_rate": min_fill_rate,
                    "min_pair_n": min_pair_n,
                    "quintile_count": quintile_count,
                    "exclude_perf_from_predictors": exclude_perf_from_predictors,
                    "output_dir": batch_output_dir.as_posix(),
                    "write_exports": write_exports,
                    "duckdb_threads": effective_threads,
                    "field_batch_size": field_batch_size,
                    "max_parallel_chunks": max_parallel_chunks,
                    "duckdb_memory_limit": resolved_memory_limit,
                    "duckdb_temp_directory": (
                        Path(duckdb_temp_directory).as_posix()
                        if duckdb_temp_directory is not None
                        else None
                    ),
                    "min_numeric_parse_rate": min_numeric_parse_rate,
                    "field_catalog_csv": str(field_catalog_csv),
                    "source_table_name": "all_fields_rows",
                    "run_lifecycle_id": resolved_run_lifecycle_id,
                }
            )

    if not tasks:
        raise ValueError(
            "No runs qualified for batch analysis after applying min_scan_data_count."
        )

    resolved_show_progress = (
        sys.stdout.isatty() if show_progress is None else bool(show_progress)
    )
    batch_progress = _AllFieldsPatternBatchProgress(enabled=resolved_show_progress)
    batch_progress.banner(
        f"All-fields pattern batch {start_day_label}..{end_day_label}"
    )
    batch_progress.batch_started(
        label=f"{start_day_label}..{end_day_label}",
        task_count=len(tasks),
        parallel_runs=effective_runs,
    )

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    completed_tasks = 0
    if effective_runs <= 1:
        for task in tasks:
            task_started_at = time.perf_counter()
            try:
                result = _run_single_batch_analysis_task(task)
                successes.append(result)
                completed_tasks += 1
                batch_progress.task_finished(
                    completed=completed_tasks,
                    total=len(tasks),
                    run_id=str(result.get("run_id") or task["run_id"]),
                    source_day_label=_extract_day_label_from_database_path(
                        Path(task["database_path"])
                    ),
                    rows_emitted=int(result.get("rows_emitted") or 0),
                    elapsed_seconds=time.perf_counter() - task_started_at,
                )
            except Exception as exc:  # pragma: no cover - surfaced in return payload
                completed_tasks += 1
                failures.append(
                    {
                        "database_path": task["database_path"],
                        "run_id": task["run_id"],
                        "error": str(exc),
                    }
                )
                batch_progress.task_finished(
                    completed=completed_tasks,
                    total=len(tasks),
                    run_id=str(task["run_id"]),
                    source_day_label=_extract_day_label_from_database_path(
                        Path(task["database_path"])
                    ),
                    rows_emitted=0,
                    elapsed_seconds=time.perf_counter() - task_started_at,
                    failed=True,
                )
    else:
        with ProcessPoolExecutor(max_workers=effective_runs) as executor:
            future_map = {
                executor.submit(_run_single_batch_analysis_task, task): task
                for task in tasks
            }
            for future in as_completed(future_map):
                task = future_map[future]
                task_started_at = time.perf_counter()
                try:
                    result = future.result()
                    successes.append(result)
                    completed_tasks += 1
                    batch_progress.task_finished(
                        completed=completed_tasks,
                        total=len(tasks),
                        run_id=str(result.get("run_id") or task["run_id"]),
                        source_day_label=_extract_day_label_from_database_path(
                            Path(task["database_path"])
                        ),
                        rows_emitted=int(result.get("rows_emitted") or 0),
                        elapsed_seconds=time.perf_counter() - task_started_at,
                    )
                except Exception as exc:  # pragma: no cover - process error path
                    completed_tasks += 1
                    failures.append(
                        {
                            "database_path": task["database_path"],
                            "run_id": task["run_id"],
                            "error": str(exc),
                        }
                    )
                    batch_progress.task_finished(
                        completed=completed_tasks,
                        total=len(tasks),
                        run_id=str(task["run_id"]),
                        source_day_label=_extract_day_label_from_database_path(
                            Path(task["database_path"])
                        ),
                        rows_emitted=0,
                        elapsed_seconds=time.perf_counter() - task_started_at,
                        failed=True,
                    )
    batch_progress.batch_finished(
        success_count=len(successes),
        failure_count=len(failures),
    )

    aggregate_result: AllFieldsPatternAggregateResult | None = None
    if aggregate_after and successes:
        summary_paths = [
            row["summary_parquet"] or row["summary_csv"]
            for row in successes
            if row.get("summary_parquet") or row.get("summary_csv")
        ]
        aggregate_result = aggregate_all_fields_pattern_summaries(
            per_run_summary_paths=[Path(path) for path in summary_paths],
            output_dir=(
                Path(aggregate_output_dir)
                if aggregate_output_dir is not None
                else batch_output_dir / "aggregates"
            ),
            min_runs_for_stability=3,
            require_sign_consistency=0.6,
            field_catalog_csv=field_catalog_csv,
            scope_label=batch_scope,
            run_lifecycle_id=resolved_run_lifecycle_id,
            nest_output_dir=False,
        )

    overview_payload = {
        "batch_id": batch_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "all_fields_root": Path(all_fields_root).as_posix(),
        "databases_discovered": [path.as_posix() for path in resolved_databases],
        "effective_parallel_runs": effective_runs,
        "per_worker_threads": effective_threads,
        "per_worker_memory_gb": per_worker_memory_gb,
        "duckdb_memory_limit": resolved_memory_limit,
        "tasks_total": len(tasks),
        "success_count": len(successes),
        "failure_count": len(failures),
        "universe_filter": serialized_universe_filter,
        "successes": successes,
        "failures": failures,
        "aggregate_result": (
            aggregate_result.exports if aggregate_result is not None else None
        ),
    }
    overview_path = batch_output_dir / "_batch_overview.json"
    _write_json(overview_path, overview_payload)
    batch_log_lines = [
        "TradingView all-fields pattern batch",
        f"batch_id={batch_id}",
        f"run_lifecycle_id={resolved_run_lifecycle_id}",
        f"window={start_day_label}..{end_day_label}",
        f"tasks_total={len(tasks)}",
        f"success_count={len(successes)}",
        f"failure_count={len(failures)}",
        f"output_dir={batch_output_dir.as_posix()}",
        "",
        "Runs completed:",
    ]
    for row in sorted(
        successes,
        key=lambda item: int(item.get("rows_emitted") or 0),
        reverse=True,
    ):
        batch_log_lines.append(
            "  - "
            + f"run_id={row.get('run_id')} "
            + f"rows={row.get('rows_emitted')} "
            + f"eligible_predictors={row.get('eligible_predictors')}"
        )
    if failures:
        batch_log_lines.extend(["", "Failures:"])
        for row in failures:
            batch_log_lines.append(
                "  - " + f"run_id={row.get('run_id')} " + f"error={row.get('error')}"
            )
    if aggregate_result is not None:
        batch_log_lines.extend(
            [
                "",
                f"Aggregate id={aggregate_result.aggregate_id}",
                f"Aggregate highlights log={aggregate_result.exports.get('overview_log')}",
            ]
        )
    _write_overview_log(batch_output_dir / "_batch_overview.log", batch_log_lines)

    return {
        "batch_id": batch_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": batch_output_dir.as_posix(),
        "overview_json": overview_path.as_posix(),
        "successes": successes,
        "failures": failures,
        "aggregate_result": (
            aggregate_result.exports if aggregate_result is not None else None
        ),
    }


def run_all_fields_multi_day_pattern_suite(
    *,
    start_day_label: str,
    end_day_label: str,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    close_forward_days: int = 7,
    min_runs_for_stability: int = 3,
    require_sign_consistency: float = 0.6,
    aggregate_after: bool = True,
    include_close_forward_analysis: bool = True,
    merge_trailing_and_close_aggregates: bool = True,
    max_parallel_runs: int = 2,
    min_scan_data_count: int = 3000,
    performance_fields: Sequence[str] | None = None,
    universe_filter: AllFieldsUniverseFilterInput = None,
    duckdb_threads: int = 20,
    field_batch_size: int = 80,
    max_parallel_chunks: int = 1,
    duckdb_memory_limit: str | int | float | None = "28GB",
    max_system_memory_gb: float = DEFAULT_PATTERN_MAX_SYSTEM_MEMORY_GB,
    memory_reserve_gb: float = DEFAULT_PATTERN_MEMORY_RESERVE_GB,
    estimated_run_memory_gb: float = DEFAULT_PATTERN_ESTIMATED_RUN_MEMORY_GB,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    run_lifecycle_id: str | None = None,
) -> dict[str, Any]:
    """Run trailing Perf.* pattern batch plus optional close-forward price analysis.

    Typical one-week pilot (5+ daily DuckDB files):
      1) Per-day predictor vs core Perf.* (Perf.5D … Perf.YTD, no change* duplicates)
      2) Cross-run stability on trailing performance targets
      3) Actual close-to-next-scan returns (close_forward_return_pct)
      4) Optional merged stability report (trailing + forward price)

    All suite artifacts are grouped under::

        pattern_analysis/suites/all_fields_multi_day_suite_{scope}_{run_lifecycle_id}/
          _suite_overview.json
          batch/              # per-day Perf.* pattern exports
          close_forward/      # close_forward_return_pct patterns
          aggregates/         # trailing, merged cross-run stability

    Scan-period close-forward tracking uses one root folder::

        pattern_analysis/runs/scan_period_close_forward_tracking_{scope}_{run_lifecycle_id}/
          _scan_period_close_forward_tracking.json
          close_forward/        # pool, close_returns, analysis input, summaries
          aggregates/           # cross-run stability aggregate
    """
    suite_scope = _format_day_range_scope_label(start_day_label, end_day_label)
    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    suite_id = _build_run_analysis_id(
        "all_fields_multi_day_suite",
        scope_label=suite_scope,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    suite_started_at_utc = _utc_now()
    suite_output_dir = DEFAULT_PATTERN_ANALYSIS_ROOT / "suites" / suite_id
    suite_output_dir.mkdir(parents=True, exist_ok=True)
    suite_batch_dir = suite_output_dir / "batch"
    suite_close_forward_dir = suite_output_dir / "close_forward"
    suite_aggregate_dir = suite_output_dir / "aggregates"

    batch_result = run_all_fields_pattern_analysis_batch(
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        all_fields_root=all_fields_root,
        max_parallel_runs=max_parallel_runs,
        aggregate_after=aggregate_after,
        min_scan_data_count=min_scan_data_count,
        performance_fields=performance_fields,
        universe_filter=universe_filter,
        duckdb_threads=duckdb_threads,
        field_batch_size=field_batch_size,
        max_parallel_chunks=max_parallel_chunks,
        duckdb_memory_limit=duckdb_memory_limit,
        max_system_memory_gb=max_system_memory_gb,
        memory_reserve_gb=memory_reserve_gb,
        estimated_run_memory_gb=estimated_run_memory_gb,
        field_catalog_csv=field_catalog_csv,
        output_dir=suite_batch_dir,
        aggregate_output_dir=suite_aggregate_dir,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )

    close_forward_result: dict[str, Any] | None = None
    merged_aggregate: AllFieldsPatternAggregateResult | None = None

    if include_close_forward_analysis:
        close_forward_result = analyze_cross_run_close_performance_patterns(
            start_day_label=start_day_label,
            end_day_label=end_day_label,
            all_fields_root=all_fields_root,
            output_dir=suite_close_forward_dir,
            close_forward_days=close_forward_days,
            field_catalog_csv=field_catalog_csv,
            quintile_count=5,
            universe_filter=universe_filter,
            duckdb_threads=duckdb_threads,
            field_batch_size=field_batch_size,
            max_parallel_chunks=max_parallel_chunks,
            max_parallel_runs=max_parallel_runs,
            duckdb_memory_limit=duckdb_memory_limit,
            max_system_memory_gb=max_system_memory_gb,
            memory_reserve_gb=memory_reserve_gb,
            estimated_run_memory_gb=estimated_run_memory_gb,
            run_lifecycle_id=resolved_run_lifecycle_id,
        )

    if merge_trailing_and_close_aggregates and batch_result.get("successes"):
        summary_paths = [
            row["summary_parquet"] or row["summary_csv"]
            for row in batch_result["successes"]
            if row.get("summary_parquet") or row.get("summary_csv")
        ]
        close_paths: list[Path] = []
        if close_forward_result is not None:
            close_parquet = close_forward_result.get("summary_parquet")
            if close_parquet:
                close_paths.append(Path(close_parquet))
        merged_aggregate = aggregate_all_fields_pattern_summaries(
            per_run_summary_paths=[Path(path) for path in summary_paths],
            output_dir=suite_aggregate_dir,
            min_runs_for_stability=min_runs_for_stability,
            require_sign_consistency=require_sign_consistency,
            field_catalog_csv=field_catalog_csv,
            include_close_forward_return=bool(close_paths),
            close_forward_return_paths=close_paths,
            scope_label=f"{suite_scope}_merged",
            run_lifecycle_id=resolved_run_lifecycle_id,
            nest_output_dir=False,
        )

    suite_overview_path = suite_output_dir / "_suite_overview.json"
    suite_overview: dict[str, Any] = {
        "suite_id": suite_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "suite_type": "all_fields_multi_day_pattern_suite",
        "started_at_utc": suite_started_at_utc.isoformat(),
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "close_forward_days": close_forward_days,
        "performance_fields": list(
            performance_fields
            if performance_fields is not None
            else CORE_PERFORMANCE_FIELDS
        ),
        "universe_filter": serialize_all_fields_universe_filter(
            normalize_all_fields_universe_filter(universe_filter)
        ),
        "suite_output_dir": suite_output_dir.as_posix(),
        "artifacts": {
            "suite_overview_json": suite_overview_path.as_posix(),
            "batch_overview_json": batch_result.get("overview_json"),
            "batch_output_dir": batch_result.get("output_dir"),
            "trailing_aggregate": batch_result.get("aggregate_result"),
            "close_forward_overview_json": (
                close_forward_result.get("overview_json")
                if close_forward_result is not None
                else None
            ),
            "close_forward_output_dir": (
                close_forward_result.get("output_dir")
                if close_forward_result is not None
                else None
            ),
            "close_forward_summary_parquet": (
                close_forward_result.get("summary_parquet")
                if close_forward_result is not None
                else None
            ),
            "merged_aggregate": (
                merged_aggregate.exports if merged_aggregate is not None else None
            ),
        },
        "folder_naming": {
            "pattern": "{artifact_prefix}[_{scope}]_{run_lifecycle_id}",
            "run_lifecycle_id": resolved_run_lifecycle_id,
            "example_batch": batch_result.get("batch_id"),
        },
    }
    _write_json(suite_overview_path, suite_overview)

    return {
        "suite_id": suite_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "suite_output_dir": suite_output_dir.as_posix(),
        "suite_overview_json": suite_overview_path.as_posix(),
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "close_forward_days": close_forward_days,
        "batch_result": batch_result,
        "close_forward_result": close_forward_result,
        "merged_aggregate": (
            merged_aggregate.exports if merged_aggregate is not None else None
        ),
        "core_performance_fields": list(
            performance_fields
            if performance_fields is not None
            else CORE_PERFORMANCE_FIELDS
        ),
    }


def resolve_scan_period_all_field_predictors(
    *,
    start_day_label: str,
    end_day_label: str,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    input_paths: Sequence[str | Path] | None = None,
    predictor_fields: Sequence[str] | None = None,
    intersect_columns_across_databases: bool = True,
    exclude_perf_from_predictors: bool = True,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    source_table_name: str = "all_fields_rows",
) -> dict[str, Any]:
    """Resolve the full predictor universe shared across a scan period.

    Uses ``classify_columns`` on columns present in every daily database (intersection
    by default) so each predictor can be evaluated on each source day in the window.
    """
    if input_paths is not None:
        resolved_databases = [Path(path) for path in input_paths if Path(path).exists()]
    else:
        resolved_databases = discover_all_fields_daily_databases(
            all_fields_root=all_fields_root,
            start_day_label=start_day_label,
            end_day_label=end_day_label,
        )
    if not resolved_databases:
        raise ValueError(
            "No all-fields daily databases were discovered for the provided range."
        )

    catalog = load_field_catalog(field_catalog_csv)
    config = AllFieldsAnalysisConfig(
        exclude_perf_from_predictors=exclude_perf_from_predictors
    )

    column_sets: list[set[str]] = []
    for database_path in resolved_databases:
        with open_move_prediction_duckdb_connection(
            database_path, read_only=True, threads=4
        ) as conn:
            column_sets.append(set(_read_table_columns(conn, source_table_name)))

    if intersect_columns_across_databases:
        shared_columns = set.intersection(*column_sets) if column_sets else set()
    else:
        shared_columns = set.union(*column_sets) if column_sets else set()

    classified_predictors, classified_performance = classify_columns(
        sorted(shared_columns),
        catalog=catalog,
        config=config,
    )
    performance_field_set = set(classified_performance)
    skipped_predictors: dict[str, str] = {}

    if predictor_fields is not None:
        candidate_predictors = [
            str(field_name)
            for field_name in predictor_fields
            if str(field_name).strip()
        ]
    else:
        candidate_predictors = classified_predictors

    resolved_predictors: list[str] = []
    seen_predictors: set[str] = set()
    for field_name in candidate_predictors:
        if field_name in seen_predictors:
            continue
        seen_predictors.add(field_name)
        if field_name not in shared_columns:
            skipped_predictors[field_name] = "missing_in_scan_period_columns"
            continue
        if exclude_perf_from_predictors and field_name in performance_field_set:
            skipped_predictors[field_name] = "performance_field"
            continue
        if _is_predictor_excluded(field_name, config):
            skipped_predictors[field_name] = "predictor_excluded"
            continue
        resolved_predictors.append(field_name)

    if not resolved_predictors:
        raise ValueError("No predictor fields resolved for the scan period.")

    return {
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "all_fields_root": Path(all_fields_root).as_posix(),
        "database_paths": [path.as_posix() for path in resolved_databases],
        "database_count": len(resolved_databases),
        "shared_column_count": len(shared_columns),
        "performance_target": PERIOD_PERFORMANCE_FIELD,
        "predictor_fields": resolved_predictors,
        "predictor_fields_skipped": skipped_predictors,
        "intersect_columns_across_databases": intersect_columns_across_databases,
    }


def run_scan_period_close_forward_predictor_tracking(
    *,
    start_day_label: str,
    end_day_label: str,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    close_forward_days: int = 7,
    predictor_fields: Sequence[str] | None = None,
    intersect_columns_across_databases: bool = True,
    min_runs_for_stability: int = 3,
    require_sign_consistency: float = 0.6,
    quintile_count: int = 5,
    min_fill_rate: float = 0.10,
    min_pair_n: int = 20,
    min_numeric_parse_rate: float = 0.80,
    exclude_perf_from_predictors: bool = True,
    universe_filter: AllFieldsUniverseFilterInput = None,
    write_exports: bool = True,
    duckdb_threads: int = 20,
    field_batch_size: int = 100,
    max_parallel_chunks: int = 1,
    max_parallel_runs: int = 1,
    duckdb_memory_limit: str | int | float | None = "9GB",
    max_system_memory_gb: float = DEFAULT_PATTERN_MAX_SYSTEM_MEMORY_GB,
    memory_reserve_gb: float = DEFAULT_PATTERN_MEMORY_RESERVE_GB,
    estimated_run_memory_gb: float = DEFAULT_PATTERN_ESTIMATED_RUN_MEMORY_GB,
    duckdb_temp_directory: str | Path | None = None,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    output_dir: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    show_progress: bool | None = None,
) -> dict[str, Any]:
    """Scan-period lens using full-period close return as the performance target.

    Pipeline:
    1) pool all eligible predictor columns shared across the period
    2) compute ``period_return_pct`` from first-scan close to last-scan close
    3) anchor predictors at period start and quantify one pooled cross-sectional fit
    4) run rolling-window period analyses for stability aggregation
    5) export close-price progression from period start for visualization

    ``close_forward_days`` is kept for backward signature compatibility but ignored.
    """
    if close_forward_days != 7:
        print(
            "[tracking] close_forward_days is deprecated in period mode and is ignored.",
            flush=True,
        )
    progress = _AllFieldsPeriodProgress(enabled=_resolve_show_progress(show_progress))
    progress.step_started("1/5 resolve predictor field universe")
    universe_started = time.perf_counter()
    field_universe = resolve_scan_period_all_field_predictors(
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        all_fields_root=all_fields_root,
        predictor_fields=predictor_fields,
        intersect_columns_across_databases=intersect_columns_across_databases,
        exclude_perf_from_predictors=exclude_perf_from_predictors,
        field_catalog_csv=field_catalog_csv,
    )
    resolved_predictor_fields = list(field_universe["predictor_fields"])
    progress.step_finished(
        "1/5 resolve predictor field universe",
        predictors=len(resolved_predictor_fields),
        scans=field_universe["database_count"],
        elapsed_s=round(time.perf_counter() - universe_started, 1),
    )
    tracking_scope = _format_day_range_scope_label(start_day_label, end_day_label)
    tracking_id, resolved_run_lifecycle_id, tracking_output_root = (
        _resolve_orchestrated_run_output_root(
            prefix="scan_period_close_forward_tracking",
            scope_label=tracking_scope,
            run_lifecycle_id=run_lifecycle_id,
            output_dir=output_dir,
        )
    )

    progress.pipeline_started(
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        predictor_count=len(resolved_predictor_fields),
        scan_count=field_universe["database_count"],
    )
    progress.step_started("2/5 period pooled correlation profile")
    period_total_result = analyze_period_pooled_performance_patterns(
        input_paths=[Path(path) for path in field_universe["database_paths"]],
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        output_dir=tracking_output_root / "period_total",
        field_catalog_csv=field_catalog_csv,
        include_predictor_fields=resolved_predictor_fields,
        min_fill_rate=min_fill_rate,
        min_pair_n=min_pair_n,
        min_numeric_parse_rate=min_numeric_parse_rate,
        quintile_count=quintile_count,
        exclude_perf_from_predictors=exclude_perf_from_predictors,
        universe_filter=universe_filter,
        write_exports=write_exports,
        duckdb_threads=duckdb_threads,
        field_batch_size=field_batch_size,
        max_parallel_chunks=max_parallel_chunks,
        duckdb_memory_limit=duckdb_memory_limit,
        duckdb_temp_directory=duckdb_temp_directory,
        run_lifecycle_id=resolved_run_lifecycle_id,
        show_progress=False,
    )
    progress.step_finished(
        "2/5 period pooled correlation profile",
        rows=period_total_result.get("rows_emitted"),
        symbols=period_total_result["period_input_result"].get("row_count"),
    )
    progress.step_started("3/5 close-price progression exports")
    progression_result = compute_period_close_progression(
        input_paths=[Path(path) for path in field_universe["database_paths"]],
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        period_returns_database_path=Path(
            period_total_result["period_returns_result"]["database_path"]
        ),
        period_returns_table_name=period_total_result["period_returns_result"][
            "table_name"
        ],
        output_dir=tracking_output_root / "progression",
        run_lifecycle_id=resolved_run_lifecycle_id,
        universe_filter=universe_filter,
        show_progress=False,
    )
    progress.step_finished(
        "3/5 close-price progression exports",
        rows=progression_result.get("row_count"),
        days=progression_result.get("day_count"),
        symbols=progression_result.get("symbol_count"),
    )
    progress.step_started("4/5 field quintile progression")
    field_quintile_progression_csv = _compute_period_field_quintile_progression(
        period_analysis_input_database_path=Path(
            period_total_result["period_input_result"]["database_path"]
        ),
        period_progression_database_path=Path(progression_result["database_path"]),
        output_csv_path=Path(tracking_output_root)
        / "progression"
        / "period_field_quintile_progression.csv",
        top_n_predictors=min(200, max(50, len(resolved_predictor_fields))),
        show_progress=False,
    )
    progression_result["field_quintile_progression_csv"] = (
        field_quintile_progression_csv
    )
    progress.step_finished("4/5 field quintile progression")
    progress.step_started("5/5 rolling-window stability")
    rolling_result = analyze_period_rolling_stability(
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
        output_dir=tracking_output_root,
        field_catalog_csv=field_catalog_csv,
        include_predictor_fields=resolved_predictor_fields,
        min_fill_rate=min_fill_rate,
        min_pair_n=min_pair_n,
        min_numeric_parse_rate=min_numeric_parse_rate,
        quintile_count=quintile_count,
        exclude_perf_from_predictors=exclude_perf_from_predictors,
        universe_filter=universe_filter,
        write_exports=write_exports,
        duckdb_threads=duckdb_threads,
        field_batch_size=field_batch_size,
        max_parallel_chunks=max_parallel_chunks,
        duckdb_memory_limit=duckdb_memory_limit,
        duckdb_temp_directory=duckdb_temp_directory,
        min_runs_for_stability=min_runs_for_stability,
        require_sign_consistency=require_sign_consistency,
        run_lifecycle_id=resolved_run_lifecycle_id,
        show_progress=False,
    )
    progress.step_finished(
        "5/5 rolling-window stability",
        windows=rolling_result.get("window_count"),
    )

    tracking_overview_path = (
        tracking_output_root / "_scan_period_close_forward_tracking.json"
    )
    tracking_payload: dict[str, Any] = {
        "tracking_id": tracking_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": tracking_output_root.as_posix(),
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "performance_lens": "period_total_return",
        "predictor_anchor": "period_start",
        "performance_target": PERIOD_PERFORMANCE_FIELD,
        "universe_filter": serialize_all_fields_universe_filter(
            normalize_all_fields_universe_filter(universe_filter)
        ),
        "scan_period_lens": {
            "database_count": field_universe["database_count"],
            "shared_column_count": field_universe["shared_column_count"],
            "predictor_field_count": len(resolved_predictor_fields),
            "predictor_fields_skipped_count": len(
                field_universe["predictor_fields_skipped"]
            ),
        },
        "field_universe": field_universe,
        "min_runs_for_stability": min_runs_for_stability,
        "require_sign_consistency": require_sign_consistency,
        "period_total_result": {
            "analysis_id": period_total_result.get("analysis_id"),
            "output_dir": period_total_result.get("output_dir"),
            "summary_parquet": period_total_result.get("summary_parquet"),
            "rows_emitted": period_total_result.get("rows_emitted"),
            "period_returns_result": period_total_result.get("period_returns_result"),
            "period_input_result": period_total_result.get("period_input_result"),
        },
        "progression_result": progression_result,
        "holding_period": {
            "start_day_label": start_day_label,
            "end_day_label": end_day_label,
            "start_run_id": period_total_result["period_returns_result"].get(
                "resolved_boundary_start_run"
            ),
            "end_run_id": period_total_result["period_returns_result"].get(
                "resolved_boundary_end_run"
            ),
        },
        "rolling_window_result": {
            "window_count": rolling_result.get("window_count"),
            "summary_paths": rolling_result.get("summary_paths"),
        },
    }
    _write_json(tracking_overview_path, tracking_payload)

    aggregate_exports: dict[str, str] | None = None
    if rolling_result.get("aggregate_result"):
        aggregate_exports = rolling_result["aggregate_result"]
        tracking_payload["stability_aggregate"] = aggregate_exports
    _write_json(tracking_overview_path, tracking_payload)

    progress.pipeline_finished(
        label="Scan-period tracking complete",
        tracking_id=tracking_id,
        rows=period_total_result.get("rows_emitted"),
        windows=rolling_result.get("window_count"),
    )

    return {
        "tracking_id": tracking_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": tracking_output_root.as_posix(),
        "start_day_label": start_day_label,
        "end_day_label": end_day_label,
        "performance_target": PERIOD_PERFORMANCE_FIELD,
        "tracking_overview_json": tracking_overview_path.as_posix(),
        "field_universe": field_universe,
        "predictor_fields": resolved_predictor_fields,
        "period_total_result": period_total_result,
        "progression_result": progression_result,
        "rolling_window_result": rolling_result,
        "aggregate_result": aggregate_exports,
    }


def _parse_all_fields_pool_input_paths(
    *,
    input_paths: Sequence[str | Path] | str | Path | None,
    all_fields_root: str | Path,
    start_day_label: str | None,
    end_day_label: str | None,
) -> list[Path]:
    if input_paths is None:
        return discover_all_fields_daily_databases(
            all_fields_root=all_fields_root,
            start_day_label=start_day_label,
            end_day_label=end_day_label,
        )
    if isinstance(input_paths, (str, Path)):
        resolved = [Path(input_paths)]
    else:
        resolved = [Path(path) for path in input_paths]
    return [path for path in resolved if path.exists()]


def compute_cross_run_close_returns(
    *,
    input_paths: Sequence[str | Path] | str | Path | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    output_dir: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    output_table_name: str = "cross_run_close_returns",
    max_forward_days: int = 20,
    min_valid_close: float = 0.000001,
    include_end_scan_forward_return: bool = True,
    universe_filter: AllFieldsUniverseFilterInput = None,
) -> dict[str, Any]:
    resolved_interval = _parse_all_fields_pool_input_paths(
        input_paths=input_paths,
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not resolved_interval:
        raise ValueError(
            "No input all-fields databases resolved for close-forward returns."
        )

    interval_paths, close_return_paths = _resolve_close_forward_return_source_paths(
        [Path(path) for path in resolved_interval],
        all_fields_root=all_fields_root,
        end_day_label=end_day_label,
        include_end_scan_forward_return=include_end_scan_forward_return,
    )
    resolved_end_day = end_day_label or _extract_day_label_from_database_path(
        interval_paths[-1]
    )
    close_scope = _format_day_range_scope_label(
        start_day_label or _extract_day_label_from_database_path(interval_paths[0]),
        resolved_end_day,
    )
    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    close_id = _build_run_analysis_id(
        "all_fields_close_forward",
        scope_label=close_scope,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    output_root = (
        Path(output_dir)
        if output_dir is not None
        else DEFAULT_PATTERN_ANALYSIS_ROOT / "aggregates" / close_id
    )
    output_root.mkdir(parents=True, exist_ok=True)
    close_db_path = output_root / "cross_run_close_returns.duckdb"
    close_parquet_path = output_root / "cross_run_close_returns.parquet"

    day_labels = [
        _normalized_close_return_period_label(
            source_day_label=_extract_day_label_from_database_path(path),
            run_created_at_utc=None,
        )
        for path in interval_paths
    ]
    period_granularity = _choose_close_return_granularity(day_labels)
    interval_path_literals = ", ".join(
        _quote_sql_literal(path.as_posix()) for path in interval_paths
    )

    with open_move_prediction_duckdb_connection(
        close_db_path, read_only=False, threads=8
    ) as conn:
        conn.execute("DROP TABLE IF EXISTS close_symbol_points")
        conn.execute(
            """
            CREATE TABLE close_symbol_points (
                source_database_path VARCHAR,
                source_day_label VARCHAR,
                period_label VARCHAR,
                run_id VARCHAR,
                run_created_at_utc TIMESTAMP,
                symbol VARCHAR,
                close_price DOUBLE
            )
            """
        )
        for index, database_path in enumerate(close_return_paths):
            alias = f"src_close_{index}"
            source_day_label = _extract_day_label_from_database_path(database_path)
            normalized_day_label = _normalized_close_return_period_label(
                source_day_label=source_day_label,
                run_created_at_utc=None,
            )
            period_label = (
                _rolling_week_start_label(normalized_day_label)
                if period_granularity == "week"
                else normalized_day_label
            )
            conn.execute(
                f"ATTACH { _quote_path_literal(database_path) } AS {_quote_identifier(alias)} (READ_ONLY)"
            )
            source_columns = _attached_all_fields_columns(conn, alias)
            filter_resolution = resolve_all_fields_universe_filter_sql(
                universe_filter,
                available_columns=source_columns,
                table_alias="rows",
            )
            universe_filter_sql = filter_resolution.sql_fragment
            conn.execute(
                f"""
                INSERT INTO close_symbol_points
                SELECT {_quote_sql_literal(database_path.as_posix())} AS source_database_path,
                    {_quote_sql_literal(normalized_day_label)} AS source_day_label,
                    {_quote_sql_literal(period_label)} AS period_label,
                    rows.run_id,
                    COALESCE(
                        TRY_CAST(
                            STRPTIME({_quote_sql_literal(normalized_day_label)}, '%d_%m_%Y')
                            AS TIMESTAMP
                        ),
                        meta.created_at_utc
                    ) AS run_created_at_utc,
                    rows.symbol,
                    {_numeric_sql_expression(f'{_quote_identifier("rows")}.{_quote_identifier("close")}')} AS close_price
                FROM {_quote_identifier(alias)}.all_fields_rows rows
                JOIN {_quote_identifier(alias)}.run_metadata meta
                    ON meta.run_id = rows.run_id
                WHERE meta.suite_name = {_quote_sql_literal(ALL_FIELDS_SUITE_NAME)}
                {universe_filter_sql}
                """
            )
            conn.execute(f"DETACH {_quote_identifier(alias)}")
        conn.execute(f"DROP TABLE IF EXISTS {_quote_identifier(output_table_name)}")
        conn.execute(
            f"""
            CREATE TABLE {_quote_identifier(output_table_name)} AS
            WITH run_level AS (
                SELECT source_database_path,
                    source_day_label,
                    period_label,
                    run_id,
                    MAX(run_created_at_utc) AS run_created_at_utc,
                    symbol,
                    AVG(close_price) AS close_price
                FROM close_symbol_points
                WHERE close_price IS NOT NULL
                  AND close_price >= {float(min_valid_close)}
                GROUP BY source_database_path, source_day_label, period_label, run_id, symbol
            ),
            sequenced AS (
                SELECT source_database_path,
                    source_day_label,
                    period_label,
                    run_id,
                    run_created_at_utc,
                    symbol,
                    close_price,
                    LEAD(close_price) OVER (
                        PARTITION BY symbol
                        ORDER BY run_created_at_utc, run_id
                    ) AS next_close_price,
                    LEAD(run_id) OVER (
                        PARTITION BY symbol
                        ORDER BY run_created_at_utc, run_id
                    ) AS next_run_id,
                    LEAD(period_label) OVER (
                        PARTITION BY symbol
                        ORDER BY run_created_at_utc, run_id
                    ) AS next_period_label,
                    LEAD(run_created_at_utc) OVER (
                        PARTITION BY symbol
                        ORDER BY run_created_at_utc, run_id
                    ) AS next_run_created_at_utc
                FROM run_level
            )
            SELECT source_database_path,
                source_day_label,
                period_label,
                run_id,
                run_created_at_utc,
                symbol,
                close_price,
                next_close_price,
                next_run_id,
                next_period_label,
                next_run_created_at_utc,
                DATE_DIFF(
                    'day',
                    CAST(run_created_at_utc AS DATE),
                    CAST(next_run_created_at_utc AS DATE)
                ) AS forward_day_delta,
                CASE
                    WHEN close_price IS NULL OR close_price = 0 THEN NULL
                    WHEN next_close_price IS NULL THEN NULL
                    ELSE ((next_close_price / close_price) - 1.0) * 100.0
                END AS close_forward_return_pct
            FROM sequenced
            WHERE next_close_price IS NOT NULL
              AND next_run_created_at_utc IS NOT NULL
              AND DATE_DIFF(
                    'day',
                    CAST(run_created_at_utc AS DATE),
                    CAST(next_run_created_at_utc AS DATE)
                ) >= 1
              AND DATE_DIFF(
                    'day',
                    CAST(run_created_at_utc AS DATE),
                    CAST(next_run_created_at_utc AS DATE)
                ) <= {int(max_forward_days)}
              AND source_database_path IN ({interval_path_literals})
            """
        )
        conn.execute(
            "COPY (SELECT * FROM "
            + _quote_identifier(output_table_name)
            + ") TO "
            + _quote_path_literal(close_parquet_path)
            + " (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        row_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(output_table_name)}"
            ).fetchone()[0]
        )
    overview_payload = {
        "close_forward_id": close_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": output_root.as_posix(),
        "database_path": close_db_path.as_posix(),
        "table_name": output_table_name,
        "parquet_path": close_parquet_path.as_posix(),
        "interval_database_count": len(interval_paths),
        "close_return_source_database_count": len(close_return_paths),
        "include_end_scan_forward_return": include_end_scan_forward_return,
        "lookahead_database_path": (
            close_return_paths[-1].as_posix()
            if len(close_return_paths) > len(interval_paths)
            else None
        ),
        "row_count": row_count,
        "period_granularity": period_granularity,
        "max_forward_days": int(max_forward_days),
        "universe_filter": serialize_all_fields_universe_filter(
            normalize_all_fields_universe_filter(universe_filter)
        ),
        "start_day_label": start_day_label
        or _extract_day_label_from_database_path(interval_paths[0]),
        "end_day_label": resolved_end_day,
    }
    overview_path = output_root / "_close_forward_overview.json"
    _write_json(overview_path, overview_payload)
    close_log_lines = [
        "TradingView cross-run close-forward returns",
        f"close_forward_id={close_id}",
        f"run_lifecycle_id={resolved_run_lifecycle_id}",
        f"window={overview_payload['start_day_label']}..{overview_payload['end_day_label']}",
        f"interval_databases={len(interval_paths)}",
        f"close_return_sources={len(close_return_paths)}",
        f"lookahead_database={overview_payload['lookahead_database_path']}",
        f"row_count={row_count}",
        f"max_forward_days={int(max_forward_days)}",
    ]
    _write_overview_log(output_root / "_close_forward_overview.log", close_log_lines)
    return {
        "close_forward_id": close_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": output_root.as_posix(),
        "database_path": close_db_path.as_posix(),
        "table_name": output_table_name,
        "parquet_path": close_parquet_path.as_posix(),
        "overview_json": overview_path.as_posix(),
        "overview_log": (output_root / "_close_forward_overview.log").as_posix(),
        "row_count": row_count,
        "period_granularity": period_granularity,
        "interval_database_count": len(interval_paths),
        "close_return_source_database_count": len(close_return_paths),
        "include_end_scan_forward_return": include_end_scan_forward_return,
        "lookahead_database_path": overview_payload["lookahead_database_path"],
        "interval_paths": [path.as_posix() for path in interval_paths],
        "close_return_paths": [path.as_posix() for path in close_return_paths],
    }


def _run_close_forward_run_analysis_task(task: dict[str, Any]) -> dict[str, Any]:
    run_result = analyze_all_fields_run_performance_patterns(
        database_path=Path(task["database_path"]),
        run_id=str(task["run_id"]),
        performance_fields=["close_forward_return_pct"],
        predictor_fields=list(task["predictor_fields"]),
        universe_filter=task.get("universe_filter"),
        min_fill_rate=float(task["min_fill_rate"]),
        min_pair_n=int(task["min_pair_n"]),
        quintile_count=int(task["quintile_count"]),
        exclude_perf_from_predictors=bool(task["exclude_perf_from_predictors"]),
        output_dir=Path(task["output_dir"]),
        write_exports=bool(task["write_exports"]),
        duckdb_threads=int(task["duckdb_threads"]),
        field_batch_size=int(task["field_batch_size"]),
        max_parallel_chunks=int(task.get("max_parallel_chunks") or 1),
        duckdb_memory_limit=task.get("duckdb_memory_limit"),
        duckdb_temp_directory=task.get("duckdb_temp_directory"),
        min_numeric_parse_rate=float(task["min_numeric_parse_rate"]),
        field_catalog_csv=Path(task["field_catalog_csv"]),
        source_table_name="all_fields_rows",
        run_lifecycle_id=task.get("run_lifecycle_id"),
        show_progress=False,
    )
    return {
        "summary": {
            "run_id": str(task["run_id"]),
            "rows_emitted": len(run_result.rows),
            "summary_csv": run_result.exports.get("csv"),
            "summary_parquet": run_result.exports.get("parquet"),
        },
        "rows": [row.to_dict() for row in run_result.rows],
    }


def compute_period_boundary_returns(
    *,
    input_paths: Sequence[str | Path] | str | Path | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    output_dir: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    output_table_name: str = "period_boundary_returns",
    min_valid_close: float = 0.000001,
    universe_filter: AllFieldsUniverseFilterInput = None,
    show_progress: bool | None = None,
) -> dict[str, Any]:
    resolved_interval = _parse_all_fields_pool_input_paths(
        input_paths=input_paths,
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not resolved_interval:
        raise ValueError(
            "No input all-fields databases resolved for period boundary returns."
        )

    interval_paths = [Path(path) for path in resolved_interval]
    resolved_start_day = start_day_label or _extract_day_label_from_database_path(
        interval_paths[0]
    )
    resolved_end_day = end_day_label or _extract_day_label_from_database_path(
        interval_paths[-1]
    )
    period_scope = _format_day_range_scope_label(resolved_start_day, resolved_end_day)
    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    period_id = _build_run_analysis_id(
        "all_fields_period_boundary",
        scope_label=period_scope,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    output_root = (
        Path(output_dir)
        if output_dir is not None
        else DEFAULT_PATTERN_ANALYSIS_ROOT / "aggregates" / period_id
    )
    output_root.mkdir(parents=True, exist_ok=True)
    period_db_path = output_root / "period_boundary_returns.duckdb"
    period_parquet_path = output_root / "period_boundary_returns.parquet"
    progress = _AllFieldsPeriodProgress(enabled=_resolve_show_progress(show_progress))
    progress.step_started(
        "period boundary returns",
        detail=f"{resolved_start_day}..{resolved_end_day} scans={len(interval_paths)}",
    )

    with open_move_prediction_duckdb_connection(
        period_db_path, read_only=False, threads=8
    ) as conn:
        conn.execute("DROP TABLE IF EXISTS close_symbol_points")
        conn.execute(
            """
            CREATE TABLE close_symbol_points (
                source_database_path VARCHAR,
                source_day_label VARCHAR,
                run_id VARCHAR,
                run_created_at_utc TIMESTAMP,
                symbol VARCHAR,
                close_price DOUBLE
            )
            """
        )
        for index, database_path in enumerate(interval_paths):
            attach_started = time.perf_counter()
            alias = f"src_period_{index}"
            source_day = _extract_day_label_from_database_path(database_path)
            normalized_day = _normalized_close_return_period_label(
                source_day_label=source_day,
                run_created_at_utc=None,
            )
            conn.execute(
                f"ATTACH { _quote_path_literal(database_path) } AS {_quote_identifier(alias)} (READ_ONLY)"
            )
            source_columns = _attached_all_fields_columns(conn, alias)
            filter_resolution = resolve_all_fields_universe_filter_sql(
                universe_filter,
                available_columns=source_columns,
                table_alias="rows",
            )
            universe_filter_sql = filter_resolution.sql_fragment
            conn.execute(
                f"""
                INSERT INTO close_symbol_points
                SELECT {_quote_sql_literal(database_path.as_posix())} AS source_database_path,
                    {_quote_sql_literal(normalized_day)} AS source_day_label,
                    rows.run_id,
                    COALESCE(
                        TRY_CAST(
                            STRPTIME({_quote_sql_literal(normalized_day)}, '%d_%m_%Y')
                            AS TIMESTAMP
                        ),
                        meta.created_at_utc
                    ) AS run_created_at_utc,
                    rows.symbol,
                    {_numeric_sql_expression(f'{_quote_identifier("rows")}.{_quote_identifier("close")}')} AS close_price
                FROM {_quote_identifier(alias)}.all_fields_rows rows
                JOIN {_quote_identifier(alias)}.run_metadata meta
                    ON meta.run_id = rows.run_id
                WHERE meta.suite_name = {_quote_sql_literal(ALL_FIELDS_SUITE_NAME)}
                {universe_filter_sql}
                """
            )
            conn.execute(f"DETACH {_quote_identifier(alias)}")
            progress.scan_attached(
                completed=index + 1,
                total=len(interval_paths),
                day_label=normalized_day,
                elapsed_seconds=time.perf_counter() - attach_started,
            )

        conn.execute(f"DROP TABLE IF EXISTS {_quote_identifier(output_table_name)}")
        conn.execute(
            f"""
            CREATE TABLE {_quote_identifier(output_table_name)} AS
            WITH run_level AS (
                SELECT source_database_path,
                    source_day_label,
                    run_id,
                    MAX(run_created_at_utc) AS run_created_at_utc,
                    symbol,
                    AVG(close_price) AS close_price
                FROM close_symbol_points
                WHERE close_price IS NOT NULL
                  AND close_price >= {float(min_valid_close)}
                GROUP BY source_database_path, source_day_label, run_id, symbol
            ),
            sequenced AS (
                SELECT source_database_path,
                    source_day_label,
                    run_id,
                    run_created_at_utc,
                    symbol,
                    close_price,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol
                        ORDER BY run_created_at_utc, run_id
                    ) AS seq_asc,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol
                        ORDER BY run_created_at_utc DESC, run_id DESC
                    ) AS seq_desc
                FROM run_level
            ),
            start_rows AS (
                SELECT symbol,
                    source_database_path AS start_source_database_path,
                    source_day_label AS start_day_label,
                    run_id AS start_run_id,
                    run_created_at_utc AS start_run_created_at_utc,
                    close_price AS start_close_price
                FROM sequenced
                WHERE seq_asc = 1
            ),
            end_rows AS (
                SELECT symbol,
                    source_database_path AS end_source_database_path,
                    source_day_label AS end_day_label,
                    run_id AS end_run_id,
                    run_created_at_utc AS end_run_created_at_utc,
                    close_price AS end_close_price
                FROM sequenced
                WHERE seq_desc = 1
            )
            SELECT s.symbol,
                s.start_source_database_path,
                s.start_day_label,
                s.start_run_id,
                s.start_run_created_at_utc,
                s.start_close_price,
                e.end_source_database_path,
                e.end_day_label,
                e.end_run_id,
                e.end_run_created_at_utc,
                e.end_close_price,
                DATE_DIFF(
                    'day',
                    CAST(s.start_run_created_at_utc AS DATE),
                    CAST(e.end_run_created_at_utc AS DATE)
                ) AS holding_calendar_days,
                CASE
                    WHEN s.start_close_price IS NULL OR s.start_close_price = 0 THEN NULL
                    WHEN e.end_close_price IS NULL THEN NULL
                    ELSE ((e.end_close_price / s.start_close_price) - 1.0) * 100.0
                END AS period_return_pct
            FROM start_rows s
            JOIN end_rows e
                ON e.symbol = s.symbol
            WHERE s.start_close_price >= {float(min_valid_close)}
              AND e.end_close_price >= {float(min_valid_close)}
            """
        )
        conn.execute(
            "COPY (SELECT * FROM "
            + _quote_identifier(output_table_name)
            + ") TO "
            + _quote_path_literal(period_parquet_path)
            + " (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        row_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(output_table_name)}"
            ).fetchone()[0]
        )
        non_null_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {_quote_identifier(output_table_name)}
                WHERE period_return_pct IS NOT NULL
                """
            ).fetchone()[0]
        )
        boundary_rows = conn.execute(
            f"""
            SELECT MIN(start_day_label),
                MAX(end_day_label),
                MIN(start_run_id),
                MAX(end_run_id)
            FROM {_quote_identifier(output_table_name)}
            """
        ).fetchone()

    progress.step_finished(
        "period boundary returns",
        symbols=row_count,
        returns=non_null_count,
    )
    overview_payload = {
        "period_boundary_id": period_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": output_root.as_posix(),
        "database_path": period_db_path.as_posix(),
        "table_name": output_table_name,
        "parquet_path": period_parquet_path.as_posix(),
        "row_count": row_count,
        "non_null_period_return_count": non_null_count,
        "start_day_label": resolved_start_day,
        "end_day_label": resolved_end_day,
        "universe_filter": serialize_all_fields_universe_filter(
            normalize_all_fields_universe_filter(universe_filter)
        ),
        "resolved_boundary_start_day": str(boundary_rows[0] or resolved_start_day),
        "resolved_boundary_end_day": str(boundary_rows[1] or resolved_end_day),
        "resolved_boundary_start_run": str(boundary_rows[2] or ""),
        "resolved_boundary_end_run": str(boundary_rows[3] or ""),
    }
    overview_path = output_root / "_period_boundary_overview.json"
    _write_json(overview_path, overview_payload)
    overview_log_lines = [
        "TradingView period boundary returns",
        f"period_boundary_id={period_id}",
        f"run_lifecycle_id={resolved_run_lifecycle_id}",
        f"window={resolved_start_day}..{resolved_end_day}",
        f"row_count={row_count}",
        f"non_null_period_return_count={non_null_count}",
        f"start_run={overview_payload['resolved_boundary_start_run']}",
        f"end_run={overview_payload['resolved_boundary_end_run']}",
    ]
    _write_overview_log(output_root / "_period_boundary_overview.log", overview_log_lines)
    return {
        "period_boundary_id": period_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": output_root.as_posix(),
        "database_path": period_db_path.as_posix(),
        "table_name": output_table_name,
        "parquet_path": period_parquet_path.as_posix(),
        "overview_json": overview_path.as_posix(),
        "overview_log": (output_root / "_period_boundary_overview.log").as_posix(),
        "row_count": row_count,
        "non_null_period_return_count": non_null_count,
        "start_day_label": resolved_start_day,
        "end_day_label": resolved_end_day,
        "resolved_boundary_start_day": overview_payload["resolved_boundary_start_day"],
        "resolved_boundary_end_day": overview_payload["resolved_boundary_end_day"],
        "resolved_boundary_start_run": overview_payload["resolved_boundary_start_run"],
        "resolved_boundary_end_run": overview_payload["resolved_boundary_end_run"],
    }


def build_period_anchored_analysis_input(
    *,
    pool_database_path: str | Path,
    pool_rows_table_name: str = "pool_all_fields_rows",
    pool_metadata_table_name: str = "pool_run_metadata",
    predictor_fields: Sequence[str],
    period_returns_database_path: str | Path,
    period_returns_table_name: str = "period_boundary_returns",
    output_dir: str | Path,
    run_lifecycle_id: str,
    start_day_label: str,
    end_day_label: str,
    universe_filter: AllFieldsUniverseFilterInput = None,
    synthetic_run_id: str | None = None,
    duckdb_threads: int = 8,
    show_progress: bool | None = None,
) -> dict[str, Any]:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    analysis_db_path = output_root / "period_analysis_input.duckdb"
    resolved_synthetic_run_id = (
        str(synthetic_run_id).strip()
        if synthetic_run_id is not None and str(synthetic_run_id).strip()
        else f"period_pooled_{run_lifecycle_id}"
    )
    source_day_label = f"{start_day_label}..{end_day_label}"
    progress = _AllFieldsPeriodProgress(enabled=_resolve_show_progress(show_progress))
    progress.step_started(
        "period anchored input",
        detail=(
            f"{source_day_label} predictors={len(predictor_fields):,} "
            f"run_id={resolved_synthetic_run_id}"
        ),
    )

    with open_move_prediction_duckdb_connection(
        analysis_db_path, read_only=False, threads=max(1, int(duckdb_threads))
    ) as conn:
        conn.execute(
            f"ATTACH { _quote_path_literal(pool_database_path) } AS pool (READ_ONLY)"
        )
        conn.execute(
            f"ATTACH { _quote_path_literal(period_returns_database_path) } AS period_ret (READ_ONLY)"
        )
        conn.execute("DROP TABLE IF EXISTS period_boundaries")
        conn.execute(
            f"""
            CREATE TABLE period_boundaries AS
            SELECT symbol,
                start_run_id,
                start_day_label,
                start_run_created_at_utc,
                end_run_id,
                end_day_label,
                end_run_created_at_utc,
                period_return_pct,
                holding_calendar_days
            FROM period_ret.{_quote_identifier(period_returns_table_name)}
            WHERE period_return_pct IS NOT NULL
            """
        )
        conn.execute("DROP TABLE IF EXISTS run_metadata")
        conn.execute(
            f"""
            CREATE TABLE run_metadata AS
            SELECT {_quote_sql_literal(resolved_synthetic_run_id)} AS run_id,
                CAST(MIN(start_run_created_at_utc) AS VARCHAR) AS created_at_utc,
                {_quote_sql_literal(f'period_total_return_{source_day_label}')} AS run_label,
                {_quote_sql_literal(ALL_FIELDS_SUITE_NAME)} AS suite_name,
                COUNT(*) AS scan_data_count
            FROM period_boundaries
            """
        )
        conn.execute("DROP TABLE IF EXISTS all_fields_rows")
        conn.execute(
            """
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR
            )
            """
        )
        for predictor_field in predictor_fields:
            conn.execute(
                "ALTER TABLE all_fields_rows ADD COLUMN "
                + _quote_identifier(predictor_field)
                + " VARCHAR"
            )
        conn.execute(
            f"ALTER TABLE all_fields_rows ADD COLUMN {_quote_identifier(PERIOD_PERFORMANCE_FIELD)} VARCHAR"
        )
        conn.execute("ALTER TABLE all_fields_rows ADD COLUMN source_day_label VARCHAR")

        conn.execute("DROP TABLE IF EXISTS period_start_features_raw")
        conn.execute(
            """
            CREATE TABLE period_start_features_raw (
                symbol VARCHAR,
                run_id VARCHAR,
                source_day_label VARCHAR,
                period_return_pct VARCHAR
            )
            """
        )
        for predictor_field in predictor_fields:
            conn.execute(
                "ALTER TABLE period_start_features_raw ADD COLUMN "
                + _quote_identifier(predictor_field)
                + " VARCHAR"
            )

        predictor_projection_sql = ", ".join(
            _quote_identifier(field_name) for field_name in predictor_fields
        )
        predictor_insert_sql = (
            predictor_projection_sql + ","
            if predictor_projection_sql
            else ""
        )
        pool_columns = [
            str(row[0])
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_catalog = 'pool'
                  AND table_schema = 'main'
                  AND table_name = ?
                ORDER BY ordinal_position
                """,
                [pool_rows_table_name],
            ).fetchall()
        ]
        filter_resolution = resolve_all_fields_universe_filter_sql(
            universe_filter,
            available_columns=pool_columns,
            table_alias="rows",
        )
        universe_filter_sql = filter_resolution.sql_fragment
        conn.execute(
            f"""
            INSERT INTO period_start_features_raw (
                symbol,
                run_id,
                source_day_label,
                {predictor_insert_sql}
                {_quote_identifier(PERIOD_PERFORMANCE_FIELD)}
            )
            SELECT rows.symbol,
                rows.run_id,
                rows.source_day_label,
                {predictor_insert_sql}
                CAST(NULL AS VARCHAR) AS {_quote_identifier(PERIOD_PERFORMANCE_FIELD)}
            FROM pool.{_quote_identifier(pool_rows_table_name)} rows
            JOIN period_boundaries p
                ON p.symbol = rows.symbol
                AND p.start_run_id = rows.run_id
            WHERE TRUE
            {universe_filter_sql}
            """
        )

        conn.execute("DROP TABLE IF EXISTS period_start_features")
        conn.execute(
            """
            CREATE TABLE period_start_features AS
            SELECT *
            FROM (
                SELECT r.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY r.symbol
                        ORDER BY r.source_day_label, r.run_id
                    ) AS row_rank
                FROM period_start_features_raw r
            ) x
            WHERE row_rank = 1
            """
        )

        conn.execute(
            f"""
            INSERT INTO all_fields_rows (
                run_id,
                row_number,
                symbol,
                {predictor_insert_sql}
                {_quote_identifier(PERIOD_PERFORMANCE_FIELD)},
                source_day_label
            )
            SELECT {_quote_sql_literal(resolved_synthetic_run_id)} AS run_id,
                ROW_NUMBER() OVER (ORDER BY p.symbol) AS row_number,
                p.symbol,
                {predictor_insert_sql}
                CAST(p.period_return_pct AS VARCHAR) AS {_quote_identifier(PERIOD_PERFORMANCE_FIELD)},
                {_quote_sql_literal(source_day_label)} AS source_day_label
            FROM period_boundaries p
            JOIN period_start_features f
                ON f.symbol = p.symbol
            """
        )
        row_count = int(
            conn.execute("SELECT COUNT(*) FROM all_fields_rows").fetchone()[0]
        )
        conn.execute("DETACH pool")
        conn.execute("DETACH period_ret")

    progress.step_finished(
        "period anchored input",
        symbols=row_count,
        predictors=len(predictor_fields),
    )
    return {
        "database_path": analysis_db_path.as_posix(),
        "table_name": "all_fields_rows",
        "run_id": resolved_synthetic_run_id,
        "row_count": row_count,
        "source_day_label": source_day_label,
        "performance_target": PERIOD_PERFORMANCE_FIELD,
    }


def analyze_period_pooled_performance_patterns(
    *,
    input_paths: Sequence[str | Path] | str | Path | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    output_dir: str | Path | None = None,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    include_predictor_fields: Sequence[str] | None = None,
    intersect_columns_across_databases: bool = True,
    min_fill_rate: float = 0.10,
    min_pair_n: int = 20,
    min_numeric_parse_rate: float = 0.80,
    quintile_count: int = 5,
    exclude_perf_from_predictors: bool = True,
    universe_filter: AllFieldsUniverseFilterInput = None,
    write_exports: bool = True,
    duckdb_threads: int = 8,
    field_batch_size: int = 80,
    max_parallel_chunks: int = 1,
    duckdb_memory_limit: str | int | float | None = "8GB",
    duckdb_temp_directory: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    show_progress: bool | None = None,
    synthetic_run_id: str | None = None,
) -> dict[str, Any]:
    del intersect_columns_across_databases
    resolved_interval = _parse_all_fields_pool_input_paths(
        input_paths=input_paths,
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not resolved_interval:
        raise ValueError(
            "No input all-fields databases resolved for period pooled analysis."
        )
    interval_paths = [Path(path) for path in resolved_interval]
    resolved_start_day = start_day_label or _extract_day_label_from_database_path(
        interval_paths[0]
    )
    resolved_end_day = end_day_label or _extract_day_label_from_database_path(
        interval_paths[-1]
    )
    period_scope = _format_day_range_scope_label(resolved_start_day, resolved_end_day)
    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    analysis_id = _build_run_analysis_id(
        "period_total_pattern",
        scope_label=period_scope,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    analysis_output_root = (
        Path(output_dir)
        if output_dir is not None
        else DEFAULT_PATTERN_ANALYSIS_ROOT / "aggregates" / analysis_id
    )
    analysis_output_root.mkdir(parents=True, exist_ok=True)

    if include_predictor_fields is not None:
        resolved_predictors = [
            str(value)
            for value in include_predictor_fields
            if str(value) and str(value) not in set(ALL_FIELDS_METADATA_COLUMNS)
        ]
    else:
        field_universe = resolve_scan_period_all_field_predictors(
            start_day_label=resolved_start_day,
            end_day_label=resolved_end_day,
            all_fields_root=all_fields_root,
            input_paths=interval_paths,
            predictor_fields=None,
            intersect_columns_across_databases=True,
            exclude_perf_from_predictors=exclude_perf_from_predictors,
            field_catalog_csv=field_catalog_csv,
        )
        resolved_predictors = list(field_universe["predictor_fields"])
    if not resolved_predictors:
        raise ValueError("No predictor fields available for period pooled analysis.")

    progress = _AllFieldsPeriodProgress(enabled=_resolve_show_progress(show_progress))
    resolved_nested_show_progress = _resolve_show_progress(show_progress)
    progress.pipeline_started(
        start_day_label=resolved_start_day,
        end_day_label=resolved_end_day,
        predictor_count=len(resolved_predictors),
        scan_count=len(interval_paths),
    )

    progress.step_started("1/4 pool scan rows")
    pool_result = aggregate_all_fields_run_pool(
        input_paths=interval_paths,
        include_predictor_fields=resolved_predictors,
        performance_fields=[],
        output_dir=analysis_output_root / "pool",
        group_by_sector=False,
        universe_filter=universe_filter,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    progress.step_finished(
        "1/4 pool scan rows",
        scans=len(interval_paths),
    )

    progress.step_started("2/4 period boundary returns")
    period_returns_result = compute_period_boundary_returns(
        input_paths=interval_paths,
        all_fields_root=all_fields_root,
        start_day_label=resolved_start_day,
        end_day_label=resolved_end_day,
        output_dir=analysis_output_root / "period_returns",
        universe_filter=universe_filter,
        run_lifecycle_id=resolved_run_lifecycle_id,
        show_progress=False,
    )
    progress.step_finished(
        "2/4 period boundary returns",
        symbols=period_returns_result.get("row_count"),
        returns=period_returns_result.get("non_null_period_return_count"),
    )

    progress.step_started("3/4 period-start anchored input")
    period_input = build_period_anchored_analysis_input(
        pool_database_path=Path(pool_result["database_path"]),
        pool_rows_table_name="pool_all_fields_rows",
        pool_metadata_table_name="pool_run_metadata",
        predictor_fields=resolved_predictors,
        period_returns_database_path=Path(period_returns_result["database_path"]),
        period_returns_table_name=period_returns_result["table_name"],
        output_dir=analysis_output_root,
        run_lifecycle_id=resolved_run_lifecycle_id,
        start_day_label=resolved_start_day,
        end_day_label=resolved_end_day,
        universe_filter=universe_filter,
        synthetic_run_id=synthetic_run_id,
        duckdb_threads=duckdb_threads,
        show_progress=False,
    )
    progress.step_finished(
        "3/4 period-start anchored input",
        symbols=period_input.get("row_count"),
    )

    progress.step_started("4/4 pooled correlation profile")
    run_result = analyze_all_fields_run_performance_patterns(
        database_path=Path(period_input["database_path"]),
        run_id=period_input["run_id"],
        performance_fields=[PERIOD_PERFORMANCE_FIELD],
        predictor_fields=resolved_predictors,
        min_fill_rate=min_fill_rate,
        min_pair_n=min_pair_n,
        min_numeric_parse_rate=min_numeric_parse_rate,
        quintile_count=quintile_count,
        exclude_perf_from_predictors=exclude_perf_from_predictors,
        output_dir=analysis_output_root / "period_runs",
        run_lifecycle_id=resolved_run_lifecycle_id,
        write_exports=write_exports,
        duckdb_threads=duckdb_threads,
        field_batch_size=field_batch_size,
        max_parallel_chunks=max_parallel_chunks,
        duckdb_memory_limit=duckdb_memory_limit,
        duckdb_temp_directory=duckdb_temp_directory,
        field_catalog_csv=field_catalog_csv,
        source_table_name="all_fields_rows",
        show_progress=resolved_nested_show_progress,
    )
    rows = [row.to_dict() for row in run_result.rows]
    progress.step_finished("4/4 pooled correlation profile", rows=len(rows))

    export_started = time.perf_counter()
    progress.step_started("export period pattern summary")
    summary_csv_path = analysis_output_root / "period_field_performance_patterns.csv"
    summary_parquet_path = (
        analysis_output_root / "period_field_performance_patterns.parquet"
    )
    _write_csv_rows(summary_csv_path, rows, fieldnames=FIELD_PATTERN_EXPORT_COLUMNS)
    _write_parquet_from_csv(
        summary_csv_path,
        summary_parquet_path,
        temp_directory=analysis_output_root / "duckdb_temp",
    )
    progress.step_finished(
        "export period pattern summary",
        rows=len(rows),
        export_s=round(time.perf_counter() - export_started, 1),
    )
    progress.pipeline_finished(
        label="Period pooled analysis complete",
        rows=len(rows),
        symbols=period_input["row_count"],
    )
    overview = {
        "analysis_id": analysis_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": analysis_output_root.as_posix(),
        "summary_csv": summary_csv_path.as_posix(),
        "summary_parquet": summary_parquet_path.as_posix(),
        "rows_emitted": len(rows),
        "predictor_field_count": len(resolved_predictors),
        "performance_target": PERIOD_PERFORMANCE_FIELD,
        "predictor_anchor": "period_start",
        "start_day_label": resolved_start_day,
        "end_day_label": resolved_end_day,
        "pool_result": pool_result,
        "period_returns_result": period_returns_result,
        "period_input_result": period_input,
        "run_exports": run_result.exports,
    }
    overview_path = analysis_output_root / "_period_pattern_overview.json"
    _write_json(overview_path, overview)
    overview_log_lines = [
        "TradingView period-total pattern analysis",
        f"analysis_id={analysis_id}",
        f"run_lifecycle_id={resolved_run_lifecycle_id}",
        f"window={resolved_start_day}..{resolved_end_day}",
        f"performance_target={PERIOD_PERFORMANCE_FIELD}",
        f"predictor_anchor=period_start",
        f"predictor_field_count={len(resolved_predictors)}",
        f"rows_emitted={len(rows)}",
        f"period_symbol_count={period_input['row_count']}",
        "",
        f"Top patterns (up to {OVERVIEW_LOG_TOP_PATTERNS}):",
    ]
    overview_log_lines.extend(
        _format_field_pattern_highlight_lines(rows, limit=OVERVIEW_LOG_TOP_PATTERNS)
    )
    overview_log_path = analysis_output_root / "_period_pattern_overview.log"
    _write_overview_log(overview_log_path, overview_log_lines)
    return {
        "analysis_id": analysis_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": analysis_output_root.as_posix(),
        "summary_csv": summary_csv_path.as_posix(),
        "summary_parquet": summary_parquet_path.as_posix(),
        "overview_json": overview_path.as_posix(),
        "overview_log": overview_log_path.as_posix(),
        "rows_emitted": len(rows),
        "predictor_fields": resolved_predictors,
        "period_returns_result": period_returns_result,
        "period_input_result": period_input,
    }


def compute_period_close_progression(
    *,
    input_paths: Sequence[str | Path] | str | Path | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    period_returns_database_path: str | Path,
    period_returns_table_name: str = "period_boundary_returns",
    output_dir: str | Path,
    run_lifecycle_id: str | None = None,
    min_valid_close: float = 0.000001,
    universe_filter: AllFieldsUniverseFilterInput = None,
    show_progress: bool | None = None,
) -> dict[str, Any]:
    resolved_interval = _parse_all_fields_pool_input_paths(
        input_paths=input_paths,
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not resolved_interval:
        raise ValueError(
            "No input all-fields databases resolved for period progression."
        )
    interval_paths = [Path(path) for path in resolved_interval]
    resolved_start_day = start_day_label or _extract_day_label_from_database_path(
        interval_paths[0]
    )
    resolved_end_day = end_day_label or _extract_day_label_from_database_path(
        interval_paths[-1]
    )
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    progression_db_path = output_root / "period_progression.duckdb"
    progression_parquet_path = output_root / "period_symbol_progression.parquet"
    universe_csv_path = output_root / "period_universe_progression.csv"
    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    progress = _AllFieldsPeriodProgress(enabled=_resolve_show_progress(show_progress))
    progress.step_started(
        "period close progression",
        detail=f"{resolved_start_day}..{resolved_end_day} scans={len(interval_paths)}",
    )

    with open_move_prediction_duckdb_connection(
        progression_db_path, read_only=False, threads=8
    ) as conn:
        conn.execute(
            f"ATTACH { _quote_path_literal(period_returns_database_path) } AS period_ret (READ_ONLY)"
        )
        conn.execute("DROP TABLE IF EXISTS close_symbol_points")
        conn.execute(
            """
            CREATE TABLE close_symbol_points (
                source_database_path VARCHAR,
                source_day_label VARCHAR,
                run_id VARCHAR,
                run_created_at_utc TIMESTAMP,
                symbol VARCHAR,
                close_price DOUBLE
            )
            """
        )
        for index, database_path in enumerate(interval_paths):
            attach_started = time.perf_counter()
            alias = f"src_progression_{index}"
            source_day = _extract_day_label_from_database_path(database_path)
            normalized_day = _normalized_close_return_period_label(
                source_day_label=source_day,
                run_created_at_utc=None,
            )
            conn.execute(
                f"ATTACH { _quote_path_literal(database_path) } AS {_quote_identifier(alias)} (READ_ONLY)"
            )
            source_columns = _attached_all_fields_columns(conn, alias)
            filter_resolution = resolve_all_fields_universe_filter_sql(
                universe_filter,
                available_columns=source_columns,
                table_alias="rows",
            )
            universe_filter_sql = filter_resolution.sql_fragment
            conn.execute(
                f"""
                INSERT INTO close_symbol_points
                SELECT {_quote_sql_literal(database_path.as_posix())} AS source_database_path,
                    {_quote_sql_literal(normalized_day)} AS source_day_label,
                    rows.run_id,
                    COALESCE(
                        TRY_CAST(
                            STRPTIME({_quote_sql_literal(normalized_day)}, '%d_%m_%Y')
                            AS TIMESTAMP
                        ),
                        meta.created_at_utc
                    ) AS run_created_at_utc,
                    rows.symbol,
                    {_numeric_sql_expression(f'{_quote_identifier("rows")}.{_quote_identifier("close")}')} AS close_price
                FROM {_quote_identifier(alias)}.all_fields_rows rows
                JOIN {_quote_identifier(alias)}.run_metadata meta
                    ON meta.run_id = rows.run_id
                WHERE meta.suite_name = {_quote_sql_literal(ALL_FIELDS_SUITE_NAME)}
                {universe_filter_sql}
                """
            )
            conn.execute(f"DETACH {_quote_identifier(alias)}")
            progress.scan_attached(
                completed=index + 1,
                total=len(interval_paths),
                day_label=normalized_day,
                elapsed_seconds=time.perf_counter() - attach_started,
            )

        conn.execute("DROP TABLE IF EXISTS period_symbol_progression")
        conn.execute(
            f"""
            CREATE TABLE period_symbol_progression AS
            WITH boundaries AS (
                SELECT symbol,
                    start_close_price,
                    end_close_price,
                    start_day_label,
                    end_day_label,
                    period_return_pct
                FROM period_ret.{_quote_identifier(period_returns_table_name)}
                WHERE period_return_pct IS NOT NULL
            ),
            run_level AS (
                SELECT source_database_path,
                    source_day_label,
                    run_id,
                    MAX(run_created_at_utc) AS run_created_at_utc,
                    symbol,
                    AVG(close_price) AS close_price
                FROM close_symbol_points
                WHERE close_price IS NOT NULL
                  AND close_price >= {float(min_valid_close)}
                GROUP BY source_database_path, source_day_label, run_id, symbol
            )
            SELECT r.source_database_path,
                r.source_day_label,
                r.run_id,
                r.run_created_at_utc,
                r.symbol,
                r.close_price,
                b.start_close_price,
                b.end_close_price,
                b.start_day_label,
                b.end_day_label,
                CASE
                    WHEN b.start_close_price IS NULL OR b.start_close_price = 0 THEN NULL
                    ELSE ((r.close_price / b.start_close_price) - 1.0) * 100.0
                END AS cumulative_return_from_start_pct,
                b.period_return_pct
            FROM run_level r
            JOIN boundaries b
                ON b.symbol = r.symbol
            """
        )
        conn.execute(
            "COPY (SELECT * FROM period_symbol_progression ORDER BY run_created_at_utc, symbol) TO "
            + _quote_path_literal(progression_parquet_path)
            + " (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        conn.execute(
            "COPY ("
            "SELECT source_day_label, run_id, run_created_at_utc, "
            "COUNT(*) AS symbol_count, "
            "AVG(cumulative_return_from_start_pct) AS mean_cumulative_return_pct, "
            "MEDIAN(cumulative_return_from_start_pct) AS median_cumulative_return_pct, "
            "AVG(period_return_pct) AS mean_full_period_return_pct, "
            "MEDIAN(period_return_pct) AS median_full_period_return_pct "
            "FROM period_symbol_progression "
            "GROUP BY source_day_label, run_id, run_created_at_utc "
            "ORDER BY run_created_at_utc, run_id"
            ") TO "
            + _quote_path_literal(universe_csv_path)
            + " (HEADER, DELIMITER ',')"
        )
        row_count = int(
            conn.execute("SELECT COUNT(*) FROM period_symbol_progression").fetchone()[0]
        )
        day_count = int(
            conn.execute(
                "SELECT COUNT(DISTINCT source_day_label) FROM period_symbol_progression"
            ).fetchone()[0]
        )
        symbol_count = int(
            conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM period_symbol_progression"
            ).fetchone()[0]
        )
        conn.execute("DETACH period_ret")

    progress.step_finished(
        "period close progression",
        rows=row_count,
        days=day_count,
        symbols=symbol_count,
    )
    overview_payload = {
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": output_root.as_posix(),
        "database_path": progression_db_path.as_posix(),
        "parquet_path": progression_parquet_path.as_posix(),
        "universe_csv_path": universe_csv_path.as_posix(),
        "row_count": row_count,
        "day_count": day_count,
        "symbol_count": symbol_count,
        "start_day_label": resolved_start_day,
        "end_day_label": resolved_end_day,
    }
    overview_path = output_root / "_period_progression_overview.json"
    _write_json(overview_path, overview_payload)
    _write_overview_log(
        output_root / "_period_progression_overview.log",
        [
            "TradingView period close progression",
            f"run_lifecycle_id={resolved_run_lifecycle_id}",
            f"window={resolved_start_day}..{resolved_end_day}",
            f"row_count={row_count}",
            f"day_count={day_count}",
            f"symbol_count={symbol_count}",
        ],
    )
    return {
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": output_root.as_posix(),
        "database_path": progression_db_path.as_posix(),
        "symbol_progression_parquet": progression_parquet_path.as_posix(),
        "universe_progression_csv": universe_csv_path.as_posix(),
        "overview_json": overview_path.as_posix(),
        "overview_log": (output_root / "_period_progression_overview.log").as_posix(),
        "row_count": row_count,
        "day_count": day_count,
        "symbol_count": symbol_count,
    }


def _compute_period_field_quintile_progression(
    *,
    period_analysis_input_database_path: str | Path,
    period_progression_database_path: str | Path,
    output_csv_path: str | Path,
    top_n_predictors: int = 100,
    show_progress: bool | None = None,
) -> str:
    output_path = Path(output_csv_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    progress = _AllFieldsPeriodProgress(enabled=_resolve_show_progress(show_progress))
    progress.step_started(
        "field quintile progression",
        detail=f"top_n={top_n_predictors}",
    )
    step_started = time.perf_counter()
    with open_move_prediction_duckdb_connection(
        Path(period_progression_database_path), read_only=False, threads=8
    ) as conn:
        conn.execute(
            f"ATTACH { _quote_path_literal(period_analysis_input_database_path) } AS period_in (READ_ONLY)"
        )
        pattern_parquet_path = (
            Path(period_analysis_input_database_path).parent
            / "period_field_performance_patterns.parquet"
        )
        predictor_rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT predictor_field,
                    ABS(COALESCE(pattern_score, 0.0)) AS abs_pattern_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY predictor_field
                        ORDER BY ABS(COALESCE(pattern_score, 0.0)) DESC
                    ) AS row_rank
                FROM read_parquet({_quote_path_literal(pattern_parquet_path)})
            )
            SELECT predictor_field
            FROM ranked
            WHERE row_rank = 1
            ORDER BY abs_pattern_score DESC
            LIMIT {max(1, int(top_n_predictors))}
            """
        ).fetchall()
        predictors = [str(row[0]) for row in predictor_rows if str(row[0] or "").strip()]
        if not predictors:
            _write_csv_rows(
                output_path,
                rows=[],
                fieldnames=[
                    "source_day_label",
                    "run_id",
                    "run_created_at_utc",
                    "predictor_field",
                    "q1_avg_cum_return_pct",
                    "q5_avg_cum_return_pct",
                    "quintile_spread_pp",
                ],
            )
            conn.execute("DETACH period_in")
            progress.step_finished(
                "field quintile progression",
                predictors=0,
                elapsed_s=round(time.perf_counter() - step_started, 1),
            )
            return output_path.as_posix()

        predictor_count = len(predictors)
        union_parts: list[str] = []
        for predictor in predictors:
            predictor_escaped = predictor.replace("'", "''")
            predictor_col = _quote_identifier(predictor)
            union_parts.append(
                f"""
                SELECT psp.source_day_label,
                    psp.run_id,
                    psp.run_created_at_utc,
                    psp.symbol,
                    '{predictor_escaped}' AS predictor_field,
                    TRY_CAST(inp.{predictor_col} AS DOUBLE) AS predictor_value,
                    psp.cumulative_return_from_start_pct
                FROM period_symbol_progression psp
                JOIN period_in.all_fields_rows inp
                    ON inp.symbol = psp.symbol
                WHERE TRY_CAST(inp.{predictor_col} AS DOUBLE) IS NOT NULL
                """.strip()
            )
        union_sql = "\nUNION ALL\n".join(union_parts)
        conn.execute("DROP TABLE IF EXISTS period_field_quintile_progression")
        conn.execute(
            f"""
            CREATE TABLE period_field_quintile_progression AS
            WITH long_vals AS (
                {union_sql}
            ),
            quintiled AS (
                SELECT source_day_label,
                    run_id,
                    run_created_at_utc,
                    symbol,
                    predictor_field,
                    predictor_value,
                    cumulative_return_from_start_pct,
                    NTILE(5) OVER (
                        PARTITION BY source_day_label, predictor_field
                        ORDER BY predictor_value
                    ) AS predictor_quintile
                FROM long_vals
                WHERE cumulative_return_from_start_pct IS NOT NULL
            ),
            spread AS (
                SELECT source_day_label,
                    run_id,
                    run_created_at_utc,
                    predictor_field,
                    AVG(
                        CASE WHEN predictor_quintile = 5 THEN cumulative_return_from_start_pct END
                    ) AS q5_avg_cum_return_pct,
                    AVG(
                        CASE WHEN predictor_quintile = 1 THEN cumulative_return_from_start_pct END
                    ) AS q1_avg_cum_return_pct,
                    AVG(
                        CASE WHEN predictor_quintile = 5 THEN cumulative_return_from_start_pct END
                    ) - AVG(
                        CASE WHEN predictor_quintile = 1 THEN cumulative_return_from_start_pct END
                    ) AS quintile_spread_pp
                FROM quintiled
                GROUP BY source_day_label, run_id, run_created_at_utc, predictor_field
            )
            SELECT source_day_label,
                run_id,
                run_created_at_utc,
                predictor_field,
                q1_avg_cum_return_pct,
                q5_avg_cum_return_pct,
                quintile_spread_pp
            FROM spread
            ORDER BY run_created_at_utc, predictor_field
            """
        )
        conn.execute(
            "COPY (SELECT * FROM period_field_quintile_progression) TO "
            + _quote_path_literal(output_path)
            + " (HEADER, DELIMITER ',')"
        )
        conn.execute("DETACH period_in")
    progress.step_finished(
        "field quintile progression",
        predictors=predictor_count,
        elapsed_s=round(time.perf_counter() - step_started, 1),
    )
    return output_path.as_posix()


def analyze_period_rolling_stability(
    *,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str,
    end_day_label: str,
    output_dir: str | Path,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    include_predictor_fields: Sequence[str] | None = None,
    min_fill_rate: float = 0.10,
    min_pair_n: int = 20,
    min_numeric_parse_rate: float = 0.80,
    quintile_count: int = 5,
    exclude_perf_from_predictors: bool = True,
    universe_filter: AllFieldsUniverseFilterInput = None,
    write_exports: bool = False,
    duckdb_threads: int = 8,
    field_batch_size: int = 80,
    max_parallel_chunks: int = 1,
    duckdb_memory_limit: str | int | float | None = "8GB",
    duckdb_temp_directory: str | Path | None = None,
    min_runs_for_stability: int = 3,
    require_sign_consistency: float = 0.6,
    rolling_window_days: int = 7,
    rolling_window_step_days: int = 3,
    run_lifecycle_id: str | None = None,
    show_progress: bool | None = None,
) -> dict[str, Any]:
    resolved_paths = discover_all_fields_daily_databases(
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if len(resolved_paths) < 2:
        return {
            "window_count": 0,
            "window_results": [],
            "aggregate_result": None,
            "summary_paths": [],
        }

    parsed_paths: list[tuple[date, Path]] = []
    for path in resolved_paths:
        parsed_day = _try_parse_day_label(_extract_day_label_from_database_path(path))
        if parsed_day is None:
            continue
        parsed_paths.append((parsed_day, path))
    parsed_paths.sort(key=lambda item: item[0])
    if len(parsed_paths) < 2:
        return {
            "window_count": 0,
            "window_results": [],
            "aggregate_result": None,
            "summary_paths": [],
        }

    window_results: list[dict[str, Any]] = []
    summary_paths: list[Path] = []
    window_span = max(2, int(rolling_window_days))
    step_span = max(1, int(rolling_window_step_days))
    rolling_windows: list[tuple[str, str, list[Path]]] = []
    cursor = 0
    while cursor < len(parsed_paths):
        start_index = cursor
        end_index = min(len(parsed_paths) - 1, start_index + window_span - 1)
        if end_index <= start_index:
            break
        window_subset = [path for _, path in parsed_paths[start_index : end_index + 1]]
        window_start_label = _extract_day_label_from_database_path(window_subset[0])
        window_end_label = _extract_day_label_from_database_path(window_subset[-1])
        rolling_windows.append((window_start_label, window_end_label, window_subset))
        if end_index >= len(parsed_paths) - 1:
            break
        next_day = parsed_paths[start_index][0] + timedelta(days=step_span)
        next_index = start_index + 1
        while next_index < len(parsed_paths) and parsed_paths[next_index][0] < next_day:
            next_index += 1
        cursor = min(next_index, len(parsed_paths) - 1)

    progress = _AllFieldsPeriodProgress(enabled=_resolve_show_progress(show_progress))
    progress.step_started(
        "rolling period stability",
        detail=(
            f"{start_day_label}..{end_day_label} "
            f"windows={len(rolling_windows)} span={window_span}d step={step_span}d"
        ),
    )

    for window_index, (window_start_label, window_end_label, window_subset) in enumerate(
        rolling_windows, start=1
    ):
        window_started = time.perf_counter()
        window_failed = False
        rows_emitted = 0
        try:
            window_result = analyze_period_pooled_performance_patterns(
                input_paths=window_subset,
                all_fields_root=all_fields_root,
                start_day_label=window_start_label,
                end_day_label=window_end_label,
                output_dir=Path(output_dir)
                / "rolling_windows"
                / f"{window_start_label}_to_{window_end_label}",
                field_catalog_csv=field_catalog_csv,
                include_predictor_fields=include_predictor_fields,
                min_fill_rate=min_fill_rate,
                min_pair_n=min_pair_n,
                min_numeric_parse_rate=min_numeric_parse_rate,
                quintile_count=quintile_count,
                exclude_perf_from_predictors=exclude_perf_from_predictors,
                universe_filter=universe_filter,
                write_exports=write_exports,
                duckdb_threads=duckdb_threads,
                field_batch_size=field_batch_size,
                max_parallel_chunks=max_parallel_chunks,
                duckdb_memory_limit=duckdb_memory_limit,
                duckdb_temp_directory=duckdb_temp_directory,
                run_lifecycle_id=run_lifecycle_id,
                show_progress=False,
                synthetic_run_id=(
                    "period_pooled_"
                    + _slugify(f"{window_start_label}_{window_end_label}")
                    + "_"
                    + _resolve_run_lifecycle_id(run_lifecycle_id)
                ),
            )
            window_results.append(window_result)
            summary_paths.append(Path(window_result["summary_parquet"]))
            rows_emitted = int(window_result.get("rows_emitted") or 0)
        except Exception:
            window_failed = True
            raise
        finally:
            progress.rolling_window_finished(
                completed=window_index,
                total=len(rolling_windows),
                window_label=f"{window_start_label}..{window_end_label}",
                rows_emitted=rows_emitted,
                elapsed_seconds=time.perf_counter() - window_started,
                failed=window_failed,
            )

    aggregate_result = None
    if summary_paths:
        progress.step_started("aggregate rolling stability")
        aggregate_started = time.perf_counter()
        aggregate_result = aggregate_all_fields_pattern_summaries(
            per_run_summary_paths=summary_paths,
            output_dir=Path(output_dir) / "aggregates",
            min_runs_for_stability=min_runs_for_stability,
            require_sign_consistency=require_sign_consistency,
            field_catalog_csv=field_catalog_csv,
            run_lifecycle_id=run_lifecycle_id,
            nest_output_dir=False,
        )
        progress.step_finished(
            "aggregate rolling stability",
            windows=len(summary_paths),
            elapsed_s=round(time.perf_counter() - aggregate_started, 1),
        )
    progress.step_finished(
        "rolling period stability",
        windows=len(window_results),
    )
    return {
        "window_count": len(window_results),
        "window_results": window_results,
        "summary_paths": [path.as_posix() for path in summary_paths],
        "aggregate_result": aggregate_result.exports if aggregate_result else None,
    }


def analyze_cross_run_close_performance_patterns(
    *,
    input_paths: Sequence[str | Path] | str | Path | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    output_dir: str | Path | None = None,
    quintile_count: int = 5,
    close_forward_days: int = 20,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    include_predictor_fields: Sequence[str] | None = None,
    intersect_columns_across_databases: bool = True,
    min_fill_rate: float = 0.10,
    min_pair_n: int = 20,
    min_numeric_parse_rate: float = 0.80,
    exclude_perf_from_predictors: bool = True,
    universe_filter: AllFieldsUniverseFilterInput = None,
    write_exports: bool = False,
    duckdb_threads: int = 8,
    field_batch_size: int = 80,
    max_parallel_chunks: int = 1,
    max_parallel_runs: int = 1,
    duckdb_memory_limit: str | int | float | None = "8GB",
    max_system_memory_gb: float = DEFAULT_PATTERN_MAX_SYSTEM_MEMORY_GB,
    memory_reserve_gb: float = DEFAULT_PATTERN_MEMORY_RESERVE_GB,
    estimated_run_memory_gb: float = DEFAULT_PATTERN_ESTIMATED_RUN_MEMORY_GB,
    duckdb_temp_directory: str | Path | None = None,
    include_end_scan_forward_return: bool = True,
    run_lifecycle_id: str | None = None,
    show_progress: bool | None = None,
) -> dict[str, Any]:
    resolved_interval = _parse_all_fields_pool_input_paths(
        input_paths=input_paths,
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not resolved_interval:
        raise ValueError(
            "No input all-fields databases resolved for close-forward analysis."
        )

    interval_paths = [Path(path) for path in resolved_interval]
    interval_path_literals = ", ".join(
        _quote_sql_literal(path.as_posix()) for path in interval_paths
    )
    serialized_universe_filter = serialize_all_fields_universe_filter(
        normalize_all_fields_universe_filter(universe_filter)
    )

    resolved_start_day = start_day_label or _extract_day_label_from_database_path(
        interval_paths[0]
    )
    resolved_end_day = end_day_label or _extract_day_label_from_database_path(
        interval_paths[-1]
    )
    close_scope = _format_day_range_scope_label(resolved_start_day, resolved_end_day)

    resolved_show_progress = (
        sys.stdout.isatty() if show_progress is None else bool(show_progress)
    )
    pipeline_progress = _AllFieldsCloseForwardProgress(enabled=resolved_show_progress)

    if include_predictor_fields is not None:
        resolved_predictors = [
            str(value)
            for value in include_predictor_fields
            if str(value) and str(value) not in set(ALL_FIELDS_METADATA_COLUMNS)
        ]
    else:
        field_universe = resolve_scan_period_all_field_predictors(
            start_day_label=resolved_start_day,
            end_day_label=resolved_end_day,
            all_fields_root=all_fields_root,
            input_paths=interval_paths,
            intersect_columns_across_databases=intersect_columns_across_databases,
            exclude_perf_from_predictors=exclude_perf_from_predictors,
            field_catalog_csv=field_catalog_csv,
        )
        resolved_predictors = list(field_universe["predictor_fields"])
    if not resolved_predictors:
        raise ValueError(
            "No predictor fields available for close-forward return analysis."
        )

    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    analysis_id = _build_run_analysis_id(
        "close_forward_pattern",
        scope_label=close_scope,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    analysis_output_root = (
        Path(output_dir)
        if output_dir is not None
        else DEFAULT_PATTERN_ANALYSIS_ROOT / "aggregates" / analysis_id
    )
    analysis_output_root.mkdir(parents=True, exist_ok=True)
    resolved_temp_directory = (
        Path(duckdb_temp_directory)
        if duckdb_temp_directory is not None
        else analysis_output_root / "duckdb_temp"
    )
    cpu_count = os.cpu_count() or DEFAULT_PATTERN_DEFAULT_DUCKDB_THREADS
    resolved_join_threads = (
        int(duckdb_threads)
        if duckdb_threads is not None and int(duckdb_threads) > 0
        else min(DEFAULT_PATTERN_DEFAULT_DUCKDB_THREADS, cpu_count)
    )
    pipeline_progress.pipeline_started(
        start_day_label=resolved_start_day,
        end_day_label=resolved_end_day,
        predictor_count=len(resolved_predictors),
        run_count=len(interval_paths),
    )
    pipeline_progress.phase("1/4 pooling predictor columns across interval scans")
    pool_result = aggregate_all_fields_run_pool(
        input_paths=interval_paths,
        include_predictor_fields=resolved_predictors,
        performance_fields=[],
        output_dir=analysis_output_root / "pool",
        group_by_sector=False,
        quintile_count=quintile_count,
        universe_filter=universe_filter,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    pipeline_progress.phase(
        "2/4 computing close-forward returns (interval includes end scan; "
        "lookahead DB used only for LEAD close)"
    )
    close_result = compute_cross_run_close_returns(
        input_paths=interval_paths,
        all_fields_root=all_fields_root,
        start_day_label=resolved_start_day,
        end_day_label=resolved_end_day,
        output_dir=analysis_output_root / "close_returns",
        max_forward_days=close_forward_days,
        include_end_scan_forward_return=include_end_scan_forward_return,
        universe_filter=universe_filter,
        run_lifecycle_id=resolved_run_lifecycle_id,
    )

    pipeline_progress.phase("3/4 joining pooled predictors with forward close returns")
    enriched_db_path = analysis_output_root / "close_forward_analysis_input.duckdb"
    with open_move_prediction_duckdb_connection(
        enriched_db_path, read_only=False, threads=resolved_join_threads
    ) as conn:
        conn.execute(
            f"ATTACH { _quote_path_literal(pool_result['database_path']) } AS pool (READ_ONLY)"
        )
        conn.execute(
            f"ATTACH { _quote_path_literal(close_result['database_path']) } AS close_db (READ_ONLY)"
        )
        conn.execute("DROP TABLE IF EXISTS run_metadata")
        conn.execute(
            """
            CREATE TABLE run_metadata AS
            SELECT runs.run_id,
                COALESCE(
                    CAST(MAX(meta.run_created_at_utc) AS VARCHAR),
                    '1970-01-01T00:00:00+00:00'
                ) AS created_at_utc,
                COALESCE(NULLIF(ANY_VALUE(meta.run_label), ''), runs.run_id) AS run_label,
                'tradingview_all_fields_export_duckdb' AS suite_name,
                COALESCE(MAX(meta.scan_data_count), 0) AS scan_data_count
            FROM (
                SELECT DISTINCT run_id
                FROM close_db.cross_run_close_returns
            ) runs
            LEFT JOIN pool.pool_run_metadata meta
                ON meta.run_id = runs.run_id
            GROUP BY runs.run_id
            """
        )
        conn.execute("DROP TABLE IF EXISTS all_fields_rows")
        conn.execute(
            """
            CREATE TABLE all_fields_rows (
                run_id VARCHAR,
                row_number BIGINT,
                symbol VARCHAR
            )
            """
        )
        for predictor_field in resolved_predictors:
            conn.execute(
                "ALTER TABLE all_fields_rows ADD COLUMN "
                + _quote_identifier(predictor_field)
                + " VARCHAR"
            )
        conn.execute(
            "ALTER TABLE all_fields_rows ADD COLUMN close_forward_return_pct VARCHAR"
        )
        if resolved_predictors:
            predictor_projection_sql = ", ".join(
                _quote_identifier(field) for field in resolved_predictors
            )
            predictor_insert_column_sql = predictor_projection_sql + ","
            predictor_insert_value_sql = predictor_projection_sql + ","
        else:
            predictor_insert_column_sql = ""
            predictor_insert_value_sql = ""
        conn.execute(
            f"""
            INSERT INTO all_fields_rows (
                run_id,
                row_number,
                symbol,
                {predictor_insert_column_sql}
                close_forward_return_pct
            )
            SELECT p.run_id,
                p.row_number,
                p.symbol,
                {predictor_insert_value_sql}
                CAST(c.close_forward_return_pct AS VARCHAR) AS close_forward_return_pct
            FROM pool.pool_all_fields_rows p
            JOIN close_db.cross_run_close_returns c
                ON c.run_id = p.run_id
                AND c.symbol = p.symbol
            WHERE c.close_forward_return_pct IS NOT NULL
                AND c.source_database_path IN ({interval_path_literals})
            """
        )
        conn.execute("DETACH close_db")
        conn.execute("DETACH pool")

    run_rows = query_move_prediction_duckdb(
        enriched_db_path,
        """
        SELECT run_id
        FROM run_metadata
        ORDER BY created_at_utc ASC, run_id ASC
        """,
    )
    run_ids = [
        str(row.get("run_id") or "") for row in run_rows if str(row.get("run_id") or "")
    ]
    (
        effective_parallel_runs,
        effective_threads,
        per_worker_memory_gb,
    ) = _resolve_pattern_analysis_worker_resources(
        max_parallel_runs=max_parallel_runs,
        duckdb_threads=duckdb_threads,
        duckdb_memory_limit=duckdb_memory_limit,
        max_system_memory_gb=max_system_memory_gb,
        memory_reserve_gb=memory_reserve_gb,
        estimated_run_memory_gb=estimated_run_memory_gb,
    )
    resolved_memory_limit = _format_memory_limit_text(
        duckdb_memory_limit
        if duckdb_memory_limit is not None
        else f"{per_worker_memory_gb:.2f}GB"
    )
    per_run_tasks = [
        {
            "database_path": enriched_db_path.as_posix(),
            "run_id": run_id,
            "predictor_fields": resolved_predictors,
            "quintile_count": quintile_count,
            "min_fill_rate": min_fill_rate,
            "min_pair_n": min_pair_n,
            "min_numeric_parse_rate": min_numeric_parse_rate,
            "exclude_perf_from_predictors": exclude_perf_from_predictors,
            "output_dir": (analysis_output_root / "per_run").as_posix(),
            "write_exports": write_exports,
            "duckdb_threads": effective_threads,
            "field_batch_size": field_batch_size,
            "max_parallel_chunks": max_parallel_chunks,
            "duckdb_memory_limit": resolved_memory_limit,
            "duckdb_temp_directory": resolved_temp_directory.as_posix(),
            "field_catalog_csv": str(field_catalog_csv),
            "run_lifecycle_id": resolved_run_lifecycle_id,
            "universe_filter": serialized_universe_filter,
        }
        for run_id in run_ids
    ]

    all_rows: list[dict[str, Any]] = []
    per_run_exports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    pipeline_progress.phase(
        f"4/4 per-run pattern analysis ({len(per_run_tasks)} runs, "
        f"parallel={effective_parallel_runs})"
    )
    completed_runs = 0
    if effective_parallel_runs <= 1:
        for task in per_run_tasks:
            task_started_at = time.perf_counter()
            try:
                run_payload = _run_close_forward_run_analysis_task(task)
                per_run_exports.append(run_payload["summary"])
                all_rows.extend(run_payload["rows"])
                completed_runs += 1
                pipeline_progress.task_finished(
                    completed=completed_runs,
                    total=len(per_run_tasks),
                    run_id=str(task["run_id"]),
                    source_day_label=resolved_end_day,
                    rows_emitted=len(run_payload["rows"]),
                    elapsed_seconds=time.perf_counter() - task_started_at,
                )
            except Exception as exc:  # pragma: no cover - surfaced in return payload
                completed_runs += 1
                failures.append(
                    {
                        "run_id": task["run_id"],
                        "error": str(exc),
                    }
                )
                pipeline_progress.task_finished(
                    completed=completed_runs,
                    total=len(per_run_tasks),
                    run_id=str(task["run_id"]),
                    source_day_label=resolved_end_day,
                    rows_emitted=0,
                    elapsed_seconds=time.perf_counter() - task_started_at,
                    failed=True,
                )
    else:
        with ProcessPoolExecutor(max_workers=effective_parallel_runs) as executor:
            future_map = {
                executor.submit(_run_close_forward_run_analysis_task, task): task
                for task in per_run_tasks
            }
            for future in as_completed(future_map):
                task = future_map[future]
                task_started_at = time.perf_counter()
                try:
                    run_payload = future.result()
                    per_run_exports.append(run_payload["summary"])
                    all_rows.extend(run_payload["rows"])
                    completed_runs += 1
                    pipeline_progress.task_finished(
                        completed=completed_runs,
                        total=len(per_run_tasks),
                        run_id=str(task["run_id"]),
                        source_day_label=resolved_end_day,
                        rows_emitted=len(run_payload["rows"]),
                        elapsed_seconds=time.perf_counter() - task_started_at,
                    )
                except Exception as exc:  # pragma: no cover - process error path
                    completed_runs += 1
                    failures.append(
                        {
                            "run_id": task["run_id"],
                            "error": str(exc),
                        }
                    )
                    pipeline_progress.task_finished(
                        completed=completed_runs,
                        total=len(per_run_tasks),
                        run_id=str(task["run_id"]),
                        source_day_label=resolved_end_day,
                        rows_emitted=0,
                        elapsed_seconds=time.perf_counter() - task_started_at,
                        failed=True,
                    )
    per_run_exports.sort(key=lambda row: str(row.get("run_id") or ""))
    pipeline_progress.batch_finished(
        success_count=len(per_run_exports),
        failure_count=len(failures),
    )

    summary_csv_path = (
        analysis_output_root / "close_forward_field_performance_patterns.csv"
    )
    summary_parquet_path = (
        analysis_output_root / "close_forward_field_performance_patterns.parquet"
    )
    _write_csv_rows(
        summary_csv_path,
        all_rows,
        fieldnames=FIELD_PATTERN_EXPORT_COLUMNS,
    )
    _write_parquet_from_csv(
        summary_csv_path,
        summary_parquet_path,
        temp_directory=analysis_output_root / "duckdb_temp",
    )
    overview = {
        "analysis_id": analysis_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "pool_database": pool_result["database_path"],
        "close_forward_database": close_result["database_path"],
        "summary_csv": summary_csv_path.as_posix(),
        "summary_parquet": summary_parquet_path.as_posix(),
        "rows_emitted": len(all_rows),
        "runs_analyzed": len(per_run_exports),
        "per_run_rows": per_run_exports,
        "predictor_field_count": len(resolved_predictors),
        "performance_target": "close_forward_return_pct",
        "close_forward_days": int(close_forward_days),
        "universe_filter": serialized_universe_filter,
        "include_end_scan_forward_return": include_end_scan_forward_return,
        "start_day_label": resolved_start_day,
        "end_day_label": resolved_end_day,
        "lookahead_database_path": (
            close_result.get("close_return_paths", [])[-1]
            if len(close_result.get("close_return_paths", [])) > len(interval_paths)
            else None
        ),
        "duckdb_threads": duckdb_threads,
        "field_batch_size": field_batch_size,
        "max_parallel_runs": int(max_parallel_runs),
        "effective_parallel_runs": effective_parallel_runs,
        "per_worker_threads": effective_threads,
        "per_worker_memory_gb": per_worker_memory_gb,
        "duckdb_memory_limit": resolved_memory_limit,
        "success_count": len(per_run_exports),
        "failure_count": len(failures),
        "failures": failures,
    }
    overview_path = analysis_output_root / "_close_forward_pattern_overview.json"
    _write_json(overview_path, overview)
    overview_log_lines = [
        "TradingView close-forward pattern analysis",
        f"analysis_id={analysis_id}",
        f"run_lifecycle_id={resolved_run_lifecycle_id}",
        f"window={resolved_start_day}..{resolved_end_day}",
        f"performance_target=close_forward_return_pct",
        f"predictor_field_count={len(resolved_predictors)}",
        f"runs_analyzed={len(per_run_exports)}",
        f"rows_emitted={len(all_rows)}",
        f"effective_parallel_runs={effective_parallel_runs}",
        f"per_worker_threads={effective_threads}",
        f"per_worker_memory_gb={per_worker_memory_gb:.2f}",
        f"failure_count={len(failures)}",
        f"include_end_scan_forward_return={include_end_scan_forward_return}",
        f"lookahead_database={close_result.get('close_return_paths', [])[-1] if len(close_result.get('close_return_paths', [])) > len(interval_paths) else None}",
        "",
        f"Top patterns (up to {OVERVIEW_LOG_TOP_PATTERNS}):",
    ]
    overview_log_lines.extend(
        _format_field_pattern_highlight_lines(
            all_rows,
            limit=OVERVIEW_LOG_TOP_PATTERNS,
        )
    )
    overview_log_path = analysis_output_root / "_close_forward_pattern_overview.log"
    _write_overview_log(overview_log_path, overview_log_lines)
    return {
        "analysis_id": analysis_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": analysis_output_root.as_posix(),
        "summary_csv": summary_csv_path.as_posix(),
        "summary_parquet": summary_parquet_path.as_posix(),
        "overview_json": overview_path.as_posix(),
        "overview_log": overview_log_path.as_posix(),
        "rows_emitted": len(all_rows),
        "predictor_fields": resolved_predictors,
        "failures": failures,
        "close_returns_result": close_result,
    }


def build_close_forward_pattern_summary(
    *,
    close_returns_database_path: str | Path,
    output_dir: str | Path,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    include_predictor_fields: Sequence[str] | None = None,
    quintile_count: int = 5,
    run_lifecycle_id: str | None = None,
) -> dict[str, Any]:
    resolved_db = Path(close_returns_database_path)
    if not resolved_db.exists():
        raise FileNotFoundError(
            f"Close-return database not found: {resolved_db.as_posix()}"
        )
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    close_rows = query_move_prediction_duckdb(
        resolved_db,
        """
        SELECT DISTINCT source_database_path
        FROM cross_run_close_returns
        ORDER BY source_database_path
        """,
    )
    source_paths = [
        Path(str(row.get("source_database_path") or ""))
        for row in close_rows
        if str(row.get("source_database_path") or "")
    ]
    source_paths = [path for path in source_paths if path.exists()]
    if not source_paths:
        raise ValueError(
            "No valid source database paths found in close-return database."
        )
    analysis_result = analyze_cross_run_close_performance_patterns(
        input_paths=source_paths,
        output_dir=output_root,
        quintile_count=quintile_count,
        field_catalog_csv=field_catalog_csv,
        include_predictor_fields=include_predictor_fields,
        run_lifecycle_id=run_lifecycle_id,
    )
    return {
        "analysis_id": analysis_result["analysis_id"],
        "output_dir": analysis_result["output_dir"],
        "summary_csv": analysis_result["summary_csv"],
        "summary_parquet": analysis_result["summary_parquet"],
        "overview_json": analysis_result["overview_json"],
        "rows_emitted": analysis_result["rows_emitted"],
    }


def _ensure_pool_metadata_table(conn: Any) -> None:
    columns_sql = ", ".join(
        f"{_quote_identifier(column_name)} {column_type}"
        for column_name, column_type in POOL_METADATA_SCHEMA
    )
    conn.execute(f"CREATE TABLE IF NOT EXISTS pool_run_metadata ({columns_sql})")


def _ensure_pool_rows_table(conn: Any, selected_fields: Sequence[str]) -> None:
    fixed_schema = [
        ("pool_aggregation_id", "VARCHAR"),
        ("source_database_path", "VARCHAR"),
        ("source_day_label", "VARCHAR"),
        ("run_id", "VARCHAR"),
        ("row_number", "BIGINT"),
        ("symbol", "VARCHAR"),
    ]
    columns_sql = ", ".join(
        f"{_quote_identifier(name)} {column_type}" for name, column_type in fixed_schema
    )
    conn.execute(f"CREATE TABLE IF NOT EXISTS pool_all_fields_rows ({columns_sql})")
    existing_columns = {
        row[0]
        for row in conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'main'
              AND table_name = 'pool_all_fields_rows'
            """
        ).fetchall()
    }
    for field_name in selected_fields:
        if field_name in existing_columns:
            continue
        conn.execute(
            f"ALTER TABLE pool_all_fields_rows "
            f"ADD COLUMN {_quote_identifier(field_name)} VARCHAR"
        )


def _attached_all_fields_columns(conn: Any, alias: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_catalog = ?
          AND table_schema = 'main'
          AND table_name = 'all_fields_rows'
        """,
        [alias],
    ).fetchall()
    return {str(row[0]) for row in rows}


def _create_pool_views(
    conn: Any,
    *,
    predictor_fields: Sequence[str],
    performance_fields: Sequence[str],
    quintile_count: int,
    group_by_sector: bool,
) -> None:
    if not predictor_fields or not performance_fields:
        conn.execute(
            """
            CREATE OR REPLACE VIEW vw_pool_field_quintile_performance AS
            SELECT CAST(NULL AS VARCHAR) AS source_day_label,
                CAST(NULL AS VARCHAR) AS run_id,
                CAST(NULL AS VARCHAR) AS predictor_field,
                CAST(NULL AS VARCHAR) AS performance_field,
                CAST(NULL AS DOUBLE) AS top_quintile_avg_perf,
                CAST(NULL AS DOUBLE) AS bottom_quintile_avg_perf,
                CAST(NULL AS DOUBLE) AS quintile_spread
            WHERE FALSE
            """
        )
        if group_by_sector:
            conn.execute(
                """
                CREATE OR REPLACE VIEW vw_pool_sector_field_performance AS
                SELECT CAST(NULL AS VARCHAR) AS source_day_label,
                    CAST(NULL AS VARCHAR) AS run_id,
                    CAST(NULL AS VARCHAR) AS sector,
                    CAST(NULL AS VARCHAR) AS predictor_field,
                    CAST(NULL AS VARCHAR) AS performance_field,
                    CAST(NULL AS DOUBLE) AS top_quintile_avg_perf,
                    CAST(NULL AS DOUBLE) AS bottom_quintile_avg_perf,
                    CAST(NULL AS DOUBLE) AS quintile_spread
                WHERE FALSE
                """
            )
        return

    available_columns = {
        row[0]
        for row in conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'main'
              AND table_name = 'pool_all_fields_rows'
            """
        ).fetchall()
    }
    predictors_available = [
        field_name for field_name in predictor_fields if field_name in available_columns
    ]
    performance_available = [
        field_name
        for field_name in performance_fields
        if field_name in available_columns
    ]
    if not predictors_available or not performance_available:
        conn.execute(
            """
            CREATE OR REPLACE VIEW vw_pool_field_quintile_performance AS
            SELECT CAST(NULL AS VARCHAR) AS source_day_label,
                CAST(NULL AS VARCHAR) AS run_id,
                CAST(NULL AS VARCHAR) AS predictor_field,
                CAST(NULL AS VARCHAR) AS performance_field,
                CAST(NULL AS DOUBLE) AS top_quintile_avg_perf,
                CAST(NULL AS DOUBLE) AS bottom_quintile_avg_perf,
                CAST(NULL AS DOUBLE) AS quintile_spread
            WHERE FALSE
            """
        )
        if group_by_sector:
            conn.execute(
                """
                CREATE OR REPLACE VIEW vw_pool_sector_field_performance AS
                SELECT CAST(NULL AS VARCHAR) AS source_day_label,
                    CAST(NULL AS VARCHAR) AS run_id,
                    CAST(NULL AS VARCHAR) AS sector,
                    CAST(NULL AS VARCHAR) AS predictor_field,
                    CAST(NULL AS VARCHAR) AS performance_field,
                    CAST(NULL AS DOUBLE) AS top_quintile_avg_perf,
                    CAST(NULL AS DOUBLE) AS bottom_quintile_avg_perf,
                    CAST(NULL AS DOUBLE) AS quintile_spread
                WHERE FALSE
                """
            )
        return

    union_parts: list[str] = []
    sector_union_parts: list[str] = []
    has_sector_column = bool(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = 'main'
              AND table_name = 'pool_all_fields_rows'
              AND column_name = 'sector'
            """
        ).fetchone()[0]
    )
    for predictor_field in predictors_available:
        predictor_sql = _quote_identifier(predictor_field)
        predictor_num = _numeric_sql_expression(predictor_sql)
        for performance_field in performance_available:
            performance_sql = _quote_identifier(performance_field)
            performance_num = _numeric_sql_expression(performance_sql)
            union_parts.append(
                f"""
                SELECT source_day_label,
                    run_id,
                    {_quote_sql_literal(predictor_field)} AS predictor_field,
                    {_quote_sql_literal(performance_field)} AS performance_field,
                    AVG(CASE WHEN predictor_ntile = {int(quintile_count)} THEN perf_value END) AS top_quintile_avg_perf,
                    AVG(CASE WHEN predictor_ntile = 1 THEN perf_value END) AS bottom_quintile_avg_perf,
                    AVG(CASE WHEN predictor_ntile = {int(quintile_count)} THEN perf_value END)
                        - AVG(CASE WHEN predictor_ntile = 1 THEN perf_value END) AS quintile_spread
                FROM (
                    SELECT source_day_label,
                        run_id,
                        {predictor_num} AS predictor_value,
                        {performance_num} AS perf_value,
                        NTILE({int(quintile_count)}) OVER (
                            PARTITION BY source_day_label, run_id
                            ORDER BY {predictor_num}
                        ) AS predictor_ntile
                    FROM pool_all_fields_rows
                ) AS q
                WHERE predictor_value IS NOT NULL
                  AND perf_value IS NOT NULL
                GROUP BY source_day_label, run_id
                """
            )
            if group_by_sector and has_sector_column:
                sector_union_parts.append(
                    f"""
                    SELECT source_day_label,
                        run_id,
                        COALESCE(NULLIF(TRIM(CAST(sector AS VARCHAR)), ''), 'UNKNOWN') AS sector,
                        {_quote_sql_literal(predictor_field)} AS predictor_field,
                        {_quote_sql_literal(performance_field)} AS performance_field,
                        AVG(CASE WHEN predictor_ntile = {int(quintile_count)} THEN perf_value END) AS top_quintile_avg_perf,
                        AVG(CASE WHEN predictor_ntile = 1 THEN perf_value END) AS bottom_quintile_avg_perf,
                        AVG(CASE WHEN predictor_ntile = {int(quintile_count)} THEN perf_value END)
                            - AVG(CASE WHEN predictor_ntile = 1 THEN perf_value END) AS quintile_spread
                    FROM (
                        SELECT source_day_label,
                            run_id,
                            sector,
                            {predictor_num} AS predictor_value,
                            {performance_num} AS perf_value,
                            NTILE({int(quintile_count)}) OVER (
                                PARTITION BY source_day_label, run_id, COALESCE(NULLIF(TRIM(CAST(sector AS VARCHAR)), ''), 'UNKNOWN')
                                ORDER BY {predictor_num}
                            ) AS predictor_ntile
                        FROM pool_all_fields_rows
                    ) AS q
                    WHERE predictor_value IS NOT NULL
                      AND perf_value IS NOT NULL
                    GROUP BY source_day_label, run_id, sector
                    """
                )

    conn.execute(
        "CREATE OR REPLACE VIEW vw_pool_field_quintile_performance AS "
        + "\nUNION ALL\n".join(union_parts)
    )
    if group_by_sector:
        if sector_union_parts:
            conn.execute(
                "CREATE OR REPLACE VIEW vw_pool_sector_field_performance AS "
                + "\nUNION ALL\n".join(sector_union_parts)
            )
        else:
            conn.execute(
                """
                CREATE OR REPLACE VIEW vw_pool_sector_field_performance AS
                SELECT CAST(NULL AS VARCHAR) AS source_day_label,
                    CAST(NULL AS VARCHAR) AS run_id,
                    CAST(NULL AS VARCHAR) AS sector,
                    CAST(NULL AS VARCHAR) AS predictor_field,
                    CAST(NULL AS VARCHAR) AS performance_field,
                    CAST(NULL AS DOUBLE) AS top_quintile_avg_perf,
                    CAST(NULL AS DOUBLE) AS bottom_quintile_avg_perf,
                    CAST(NULL AS DOUBLE) AS quintile_spread
                WHERE FALSE
                """
            )


def aggregate_all_fields_run_pool(
    *,
    input_paths: Sequence[str | Path] | str | Path | None = None,
    all_fields_root: str | Path = DEFAULT_ALL_FIELDS_ROOT,
    start_day_label: str | None = None,
    end_day_label: str | None = None,
    include_run_ids: Sequence[str] | None = None,
    include_predictor_fields: Sequence[str] | None = None,
    performance_fields: Sequence[str] | None = None,
    universe_filter: AllFieldsUniverseFilterInput = None,
    output_dir: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    group_by_sector: bool = False,
    quintile_count: int = 5,
) -> dict[str, Any]:
    resolved_inputs = _parse_all_fields_pool_input_paths(
        input_paths=input_paths,
        all_fields_root=all_fields_root,
        start_day_label=start_day_label,
        end_day_label=end_day_label,
    )
    if not resolved_inputs:
        raise ValueError("No input all-fields databases resolved for pooling.")

    resolved_predictors = list(include_predictor_fields or [])
    resolved_performance = list(performance_fields or DEFAULT_PERFORMANCE_FIELDS)
    selected_fields: list[str] = []
    for field_name in [*resolved_predictors, *resolved_performance, "sector"]:
        if not field_name:
            continue
        if field_name in selected_fields:
            continue
        selected_fields.append(field_name)

    resolved_run_lifecycle_id = _resolve_run_lifecycle_id(run_lifecycle_id)
    pool_id = _build_run_analysis_id(
        "all_fields_raw_pool",
        run_lifecycle_id=resolved_run_lifecycle_id,
    )
    pool_output_root = (
        Path(output_dir)
        if output_dir is not None
        else DEFAULT_PATTERN_ANALYSIS_ROOT / "aggregates" / pool_id
    )
    pool_output_root.mkdir(parents=True, exist_ok=True)
    pool_db_path = pool_output_root / "pooled_all_fields_patterns.duckdb"

    include_run_ids_set = {str(value) for value in include_run_ids or [] if str(value)}
    inserted_rows_total = 0

    with open_move_prediction_duckdb_connection(pool_db_path, read_only=False) as conn:
        _ensure_pool_metadata_table(conn)
        _ensure_pool_rows_table(conn, selected_fields)

        for index, database_path in enumerate(resolved_inputs):
            alias = f"src_{index}"
            conn.execute(
                f"ATTACH { _quote_path_literal(database_path) } AS {_quote_identifier(alias)} (READ_ONLY)"
            )
            day_label = _extract_day_label_from_database_path(database_path)
            source_columns = _attached_all_fields_columns(conn, alias)
            filter_resolution = resolve_all_fields_universe_filter_sql(
                universe_filter,
                available_columns=source_columns,
            )
            universe_filter_sql = filter_resolution.sql_fragment
            run_filter_sql = ""
            if include_run_ids_set:
                run_filter_sql = (
                    " AND run_id IN ("
                    + ", ".join(
                        _quote_sql_literal(run_value)
                        for run_value in sorted(include_run_ids_set)
                    )
                    + ")"
                )

            conn.execute(
                f"""
                INSERT INTO pool_run_metadata
                SELECT {_quote_sql_literal(pool_id)} AS pool_aggregation_id,
                    {_quote_sql_literal(database_path.as_posix())} AS source_database_path,
                    {_quote_sql_literal(day_label)} AS source_day_label,
                    run_id,
                    created_at_utc,
                    run_label,
                    scan_data_count
                FROM {_quote_identifier(alias)}.run_metadata
                WHERE suite_name = {_quote_sql_literal(ALL_FIELDS_SUITE_NAME)}
                    {run_filter_sql}
                """
            )

            select_field_expressions = []
            for field in selected_fields:
                if field in source_columns:
                    select_field_expressions.append(_quote_identifier(field))
                else:
                    select_field_expressions.append(
                        f"NULL AS {_quote_identifier(field)}"
                    )
            select_field_sql = ", ".join(select_field_expressions)
            insert_field_sql = ", ".join(
                _quote_identifier(field) for field in selected_fields
            )
            conn.execute(
                f"""
                INSERT INTO pool_all_fields_rows (
                    pool_aggregation_id,
                    source_database_path,
                    source_day_label,
                    run_id,
                    row_number,
                    symbol,
                    {insert_field_sql}
                )
                SELECT {_quote_sql_literal(pool_id)} AS pool_aggregation_id,
                    {_quote_sql_literal(database_path.as_posix())} AS source_database_path,
                    {_quote_sql_literal(day_label)} AS source_day_label,
                    run_id,
                    row_number,
                    symbol,
                    {select_field_sql}
                FROM {_quote_identifier(alias)}.all_fields_rows
                WHERE run_id IN (
                    SELECT run_id
                    FROM {_quote_identifier(alias)}.run_metadata
                    WHERE suite_name = {_quote_sql_literal(ALL_FIELDS_SUITE_NAME)}
                    {run_filter_sql}
                ){universe_filter_sql}
                """
            )
            inserted_rows_total += int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM pool_all_fields_rows
                    WHERE source_database_path = ?
                      AND source_day_label = ?
                    """,
                    [database_path.as_posix(), day_label],
                ).fetchone()[0]
            )
            conn.execute(f"DETACH {_quote_identifier(alias)}")

        _create_pool_views(
            conn,
            predictor_fields=resolved_predictors,
            performance_fields=resolved_performance,
            quintile_count=quintile_count,
            group_by_sector=group_by_sector,
        )

    overview_payload = {
        "pool_id": pool_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "database_path": pool_db_path.as_posix(),
        "source_database_count": len(resolved_inputs),
        "selected_predictor_fields": resolved_predictors,
        "selected_performance_fields": resolved_performance,
        "selected_fields": selected_fields,
        "group_by_sector": group_by_sector,
        "universe_filter": serialize_all_fields_universe_filter(
            normalize_all_fields_universe_filter(universe_filter)
        ),
        "rows_inserted_running_count": inserted_rows_total,
    }
    overview_path = pool_output_root / "_pool_overview.json"
    _write_json(overview_path, overview_payload)
    return {
        "pool_id": pool_id,
        "run_lifecycle_id": resolved_run_lifecycle_id,
        "output_dir": pool_output_root.as_posix(),
        "database_path": pool_db_path.as_posix(),
        "overview_json": overview_path.as_posix(),
    }


def attach_history_performance_targets(
    *,
    database_path: str | Path,
    history_database_path: str | Path,
    run_id: str,
    horizon_name: str = "weeks",
    max_day_delta: int = 3,
    output_database_path: str | Path | None = None,
    output_table_name: str = "all_fields_rows_with_history_targets",
) -> dict[str, Any]:
    # Required by plan: reuse _symbol_join from pattern discovery.
    from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
        _symbol_join,
    )

    resolved_source = Path(database_path)
    resolved_history = Path(history_database_path)
    if not resolved_source.exists():
        raise FileNotFoundError(
            f"Source all-fields database not found: {resolved_source}"
        )
    if not resolved_history.exists():
        raise FileNotFoundError(f"History database not found: {resolved_history}")

    if output_database_path is None:
        output_database_path = (
            Path(tempfile.mkdtemp(prefix="all_fields_history_targets_"))
            / "all_fields_with_history_targets.duckdb"
        )
    resolved_output_db = Path(output_database_path)
    resolved_output_db.parent.mkdir(parents=True, exist_ok=True)

    with open_move_prediction_duckdb_connection(
        resolved_output_db, read_only=False
    ) as conn:
        conn.execute(
            f"ATTACH { _quote_path_literal(resolved_source) } AS fields (READ_ONLY)"
        )
        conn.execute(
            f"ATTACH { _quote_path_literal(resolved_history) } AS hist (READ_ONLY)"
        )
        conn.execute("DROP TABLE IF EXISTS run_metadata")
        conn.execute(
            "CREATE TABLE run_metadata AS "
            "SELECT * FROM fields.run_metadata WHERE run_id = "
            + _quote_sql_literal(run_id)
        )
        conn.execute(f"DROP TABLE IF EXISTS {_quote_identifier(output_table_name)}")
        conn.execute(
            f"""
            CREATE TABLE {_quote_identifier(output_table_name)} AS
            WITH target_run AS (
                SELECT run_id, created_at_utc
                FROM fields.run_metadata
                WHERE run_id = {_quote_sql_literal(run_id)}
                LIMIT 1
            ),
            history_candidates AS (
                SELECT h.symbol,
                    h.close_return_pct_total AS history_forward_return,
                    h.snapshot_date,
                    h.horizon_name,
                    ABS(
                        DATE_DIFF(
                            'day',
                            CAST(tr.created_at_utc AS DATE),
                            CAST(h.snapshot_date AS DATE)
                        )
                    ) AS day_delta,
                    ROW_NUMBER() OVER (
                        PARTITION BY h.symbol
                        ORDER BY ABS(
                            DATE_DIFF(
                                'day',
                                CAST(tr.created_at_utc AS DATE),
                                CAST(h.snapshot_date AS DATE)
                            )
                        ) ASC,
                        h.snapshot_date DESC
                    ) AS row_rank
                FROM hist.vw_profile_horizon_progression_core h
                CROSS JOIN target_run tr
                WHERE h.horizon_name = {_quote_sql_literal(horizon_name)}
                  AND ABS(
                        DATE_DIFF(
                            'day',
                            CAST(tr.created_at_utc AS DATE),
                            CAST(h.snapshot_date AS DATE)
                        )
                    ) <= {int(max_day_delta)}
            )
            SELECT r.*,
                hc.history_forward_return,
                hc.snapshot_date AS history_snapshot_date,
                hc.day_delta AS history_day_delta
            FROM fields.all_fields_rows r
            LEFT JOIN (
                SELECT symbol,
                    history_forward_return,
                    snapshot_date,
                    day_delta
                FROM history_candidates
                WHERE row_rank = 1
            ) hc
                ON {_symbol_join("r", "hc")}
            WHERE r.run_id = {_quote_sql_literal(run_id)}
            """
        )
        row_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(output_table_name)}"
            ).fetchone()[0]
        )
        matched_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM {_quote_identifier(output_table_name)}
                WHERE history_forward_return IS NOT NULL
                """
            ).fetchone()[0]
        )
        conn.execute("DETACH fields")
        conn.execute("DETACH hist")

    return {
        "database_path": resolved_output_db.as_posix(),
        "table_name": output_table_name,
        "row_count": row_count,
        "matched_history_rows": matched_count,
    }


def analyze_all_fields_run_with_forward_returns(
    *,
    database_path: str | Path,
    history_database_path: str | Path,
    run_id: str | None = None,
    horizon_name: str = "weeks",
    max_day_delta: int = 3,
    predictor_fields: Sequence[str] | None = None,
    universe_filter: AllFieldsUniverseFilterInput = None,
    output_dir: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    write_exports: bool = True,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    min_fill_rate: float = 0.15,
    min_pair_n: int = 50,
    quintile_count: int = 5,
    duckdb_threads: int = 8,
    field_batch_size: int = 40,
    min_numeric_parse_rate: float = 0.90,
) -> AllFieldsRunPatternAnalysisResult:
    resolved_database = Path(database_path)
    resolved_run_id = resolve_run_id(resolved_database, run_id=run_id)
    attached = attach_history_performance_targets(
        database_path=resolved_database,
        history_database_path=history_database_path,
        run_id=resolved_run_id,
        horizon_name=horizon_name,
        max_day_delta=max_day_delta,
    )
    return analyze_all_fields_run_performance_patterns(
        database_path=Path(attached["database_path"]),
        run_id=resolved_run_id,
        performance_fields=["history_forward_return"],
        predictor_fields=predictor_fields,
        universe_filter=universe_filter,
        min_fill_rate=min_fill_rate,
        min_pair_n=min_pair_n,
        quintile_count=quintile_count,
        exclude_perf_from_predictors=True,
        output_dir=output_dir,
        run_lifecycle_id=run_lifecycle_id,
        write_exports=write_exports,
        duckdb_threads=duckdb_threads,
        field_batch_size=field_batch_size,
        max_parallel_chunks=1,
        duckdb_memory_limit=None,
        duckdb_temp_directory=None,
        min_numeric_parse_rate=min_numeric_parse_rate,
        field_catalog_csv=field_catalog_csv,
        source_table_name=attached["table_name"],
    )


def benchmark_all_fields_pattern_analysis(
    *,
    database_path: str | Path,
    run_id: str | None = None,
    output_dir: str | Path | None = None,
    run_lifecycle_id: str | None = None,
    field_catalog_csv: str | Path = DEFAULT_FIELD_CATALOG_CSV,
    source_table_name: str = "all_fields_rows",
    universe_filter: AllFieldsUniverseFilterInput = None,
    duckdb_threads: int = 20,
    field_batch_size: int = 80,
    max_parallel_chunks: int = 1,
    duckdb_memory_limit: str | int | float | None = "28GB",
    duckdb_temp_directory: str | Path | None = None,
    min_fill_rate: float = 0.15,
    min_numeric_parse_rate: float = 0.90,
    min_pair_n: int = 50,
    quintile_count: int = 5,
    predictor_fields: Sequence[str] | None = None,
    performance_fields: Sequence[str] | None = None,
    write_exports: bool = True,
) -> dict[str, Any]:
    import time

    started = time.perf_counter()
    result = analyze_all_fields_run_performance_patterns(
        database_path=database_path,
        run_id=run_id,
        performance_fields=performance_fields,
        predictor_fields=predictor_fields,
        universe_filter=universe_filter,
        min_fill_rate=min_fill_rate,
        min_pair_n=min_pair_n,
        quintile_count=quintile_count,
        exclude_perf_from_predictors=True,
        output_dir=output_dir,
        run_lifecycle_id=run_lifecycle_id,
        write_exports=write_exports,
        duckdb_threads=duckdb_threads,
        field_batch_size=field_batch_size,
        max_parallel_chunks=max_parallel_chunks,
        duckdb_memory_limit=duckdb_memory_limit,
        duckdb_temp_directory=duckdb_temp_directory,
        min_numeric_parse_rate=min_numeric_parse_rate,
        field_catalog_csv=field_catalog_csv,
        source_table_name=source_table_name,
    )
    elapsed = time.perf_counter() - started
    benchmark_payload = {
        "elapsed_seconds": elapsed,
        "elapsed_minutes": elapsed / 60.0,
        "analysis_run_id": result.analysis_run_id,
        "run_lifecycle_id": result.run_lifecycle_id,
        "database_path": result.database_path.as_posix(),
        "run_id": result.run_id,
        "source_table_name": result.source_table_name,
        "performance_fields_count": len(result.performance_fields),
        "predictor_fields_count": len(result.predictor_fields),
        "eligible_predictor_fields_count": len(result.eligible_predictor_fields),
        "rows_emitted": len(result.rows),
        "duckdb_threads": duckdb_threads,
        "field_batch_size": field_batch_size,
        "max_parallel_chunks": max_parallel_chunks,
        "duckdb_memory_limit": _format_memory_limit_text(duckdb_memory_limit),
        "universe_filter": serialize_all_fields_universe_filter(
            normalize_all_fields_universe_filter(universe_filter)
        ),
    }
    benchmark_path = result.output_dir / "benchmark_metrics.json"
    _write_json(benchmark_path, benchmark_payload)
    return {
        "benchmark_path": benchmark_path.as_posix(),
        "metrics": benchmark_payload,
        "exports": result.exports,
    }


__all__ = [
    "QUANTIFIABLE_PREDICTOR_CATALOG_TYPES",
    "CORE_PERFORMANCE_FIELDS",
    "FORWARD_PERFORMANCE_FIELDS",
    "AllFieldsAnalysisConfig",
    "AllFieldsUniverseFilter",
    "AllFieldsUniverseFilterClause",
    "AllFieldsUniverseFilterInput",
    "AllFieldsUniverseFilterResolution",
    "AllFieldsPatternAggregateResult",
    "AllFieldsRunPatternAnalysisResult",
    "CrossRunFieldStabilityRow",
    "FieldCatalogEntry",
    "FieldCatalogIndex",
    "FieldPerformancePatternRow",
    "FieldQualityStat",
    "RunInventoryRow",
    "aggregate_all_fields_pattern_summaries",
    "aggregate_all_fields_run_pool",
    "analyze_cross_run_close_performance_patterns",
    "analyze_period_pooled_performance_patterns",
    "analyze_period_rolling_stability",
    "analyze_all_fields_run_performance_patterns",
    "analyze_all_fields_run_with_forward_returns",
    "attach_history_performance_targets",
    "build_period_anchored_analysis_input",
    "compute_cross_run_close_returns",
    "compute_period_boundary_returns",
    "compute_period_close_progression",
    "classify_columns",
    "benchmark_all_fields_pattern_analysis",
    "discover_all_fields_daily_databases",
    "inventory_all_fields_runs",
    "load_field_catalog",
    "market_cap_basic_universe_filter",
    "move_prediction_universe_filter",
    "normalize_all_fields_universe_filter",
    "preferred_markets_universe_filter",
    "resolve_all_fields_universe_filter_sql",
    "resolve_run_id",
    "resolve_performance_fields",
    "resolve_scan_period_all_field_predictors",
    "run_all_fields_multi_day_pattern_suite",
    "run_all_fields_pattern_analysis_batch",
    "run_scan_period_close_forward_predictor_tracking",
    "build_close_forward_pattern_summary",
]
