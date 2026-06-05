from __future__ import annotations

import csv
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from db.trading_view_move_prediction_duckdb import (
    MovePredictionDuckDBStore,
    query_move_prediction_duckdb,
)
from generic_utils.log_to_files_util import log_rows_to_csv, log_to_file

PROFILE_CSV_PREFIX = "tradingview_move_prediction__"
RAW_CSV_SUFFIX = "__raw_data.csv"
DEFAULT_OUTPUT_FOLDER_FORMAT = "%d_%m_%Y__%H_%M_%S"
DATE_FOLDER_FORMAT = "%d_%m_%Y"
TRACKED_HORIZONS: tuple[str, ...] = ("days", "weeks", "months", "years")
TRACKED_PERFORMANCE_FIELDS: tuple[str, ...] = (
    "close",
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.YTD",
    "Perf.Y",
    "Perf.5Y",
)
SESSION_SORT_ORDER = {
    "marketopen": 0,
    "marketclose": 1,
}
REQUIRED_SNAPSHOT_SESSION_FOLDERS: tuple[str, ...] = ("marketOpen", "marketClose")

PROFILE_NAME_PATTERN = re.compile(
    r"__profile_(?P<profile>[a-z0-9_]+)\.csv$",
    re.IGNORECASE,
)
DATE_FOLDER_PATTERN = re.compile(r"^\d{2}_\d{2}_\d{4}$")
DUCKDB_FILE_NAME_PATTERN = re.compile(
    r"^move_prediction_\d{4}_W\d{2}\.duckdb$",
    re.IGNORECASE,
)
DUCKDB_WEEK_FOLDER_PATTERN = re.compile(r"^week=(?P<week>\d{1,2})$", re.IGNORECASE)
DUCKDB_RUN_SESSION_TIME_PATTERN = re.compile(
    r"(?P<hour>\d{2})(?P<minute>\d{2})_utc",
    re.IGNORECASE,
)
DEFAULT_DUCKDB_HISTORY_ROOT_FOLDER = "historical_prediction_analysis"
DEFAULT_DUCKDB_HISTORY_RUN_PREFIX = "history_aggregation"
LOG_PROFILE_PATTERN = re.compile(
    r"scoring_profile=(?P<profile>[a-z0-9_]+)",
    re.IGNORECASE,
)

HISTORY_HEADERS = [
    "profile_name",
    "symbol",
    "company_name",
    "name",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "profile_snapshot_index",
    "snapshot_index_for_symbol",
    "snapshots_seen_for_symbol",
    "trading_sessions_seen_for_symbol",
    "profile_trading_session_count",
    "trading_session_presence_ratio_for_symbol",
    "snapshot_date",
    "snapshot_session",
    "snapshot_label",
    "source_file",
    "market_cap_basic",
    "close",
    "close_return_pct_vs_previous_snapshot",
    "Perf.5D",
    "Perf.5D_delta_vs_previous",
    "Perf.W",
    "Perf.W_delta_vs_previous",
    "Perf.1M",
    "Perf.1M_delta_vs_previous",
    "Perf.YTD",
    "Perf.YTD_delta_vs_previous",
    "Perf.Y",
    "Perf.Y_delta_vs_previous",
    "Perf.5Y",
    "Perf.5Y_delta_vs_previous",
]

SUMMARY_HEADERS = [
    "profile_name",
    "symbol",
    "company_name",
    "name",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "snapshots_seen",
    "profile_snapshot_count",
    "presence_ratio",
    "trading_sessions_seen",
    "profile_trading_session_count",
    "trading_session_presence_ratio",
    "first_snapshot_date",
    "first_snapshot_session",
    "last_snapshot_date",
    "last_snapshot_session",
    "last_snapshot_label",
    "total_snapshot_span_days",
    "first_close",
    "last_close",
    "close_return_pct_total",
    "Perf.5D_first",
    "Perf.5D_last",
    "Perf.5D_delta_total",
    "Perf.W_first",
    "Perf.W_last",
    "Perf.W_delta_total",
    "Perf.1M_first",
    "Perf.1M_last",
    "Perf.1M_delta_total",
    "Perf.YTD_first",
    "Perf.YTD_last",
    "Perf.YTD_delta_total",
    "Perf.Y_first",
    "Perf.Y_last",
    "Perf.Y_delta_total",
    "Perf.5Y_first",
    "Perf.5Y_last",
    "Perf.5Y_delta_total",
]

MANIFEST_HEADERS = [
    "profile_name",
    "profile_snapshot_index",
    "snapshot_date",
    "snapshot_session",
    "snapshot_label",
    "source_file",
    "rows_read",
    "unique_symbols",
]

SCORE_PROGRESSION_BASE_HEADERS = [
    "profile_name",
    "symbol",
    "company_name",
    "sector",
    "industry",
    "snapshots_seen",
    "profile_snapshot_count",
    "presence_ratio",
    "first_score",
    "last_score",
    "score_delta_total",
    "avg_score",
    "first_rank",
    "last_rank",
    "best_rank",
    "worst_rank",
    "rank_improvement_total",
]

PRICE_PROGRESSION_BASE_HEADERS = [
    "profile_name",
    "symbol",
    "company_name",
    "sector",
    "industry",
    "snapshots_seen",
    "profile_snapshot_count",
    "presence_ratio",
    "first_close",
    "last_close",
    "close_return_pct_total",
    "max_close",
    "min_close",
    "max_drawdown_pct",
]

PRICE_PROGRESSION_AGGREGATE_BASE_HEADERS = [
    header_name
    for header_name in PRICE_PROGRESSION_BASE_HEADERS
    if header_name != "profile_name"
]

SNAPSHOT_COLUMN_PREFIX = "snap__"
PRICE_SNAPSHOT_COLUMN_PREFIX = "price__snap__"

# Price summary fields appended to score progression rows (shared identity fields
# like symbol/company_name/sector/industry are already present via the score row).
PRICE_EXTRA_BASE_HEADERS = [
    "first_close",
    "last_close",
    "close_return_pct_total",
    "max_close",
    "min_close",
    "max_drawdown_pct",
]

HISTORICAL_ANALYSIS_RUNS_SCHEMA = [
    ("analysis_run_id", "VARCHAR"),
    ("created_at_utc", "TIMESTAMP"),
    ("output_dir", "VARCHAR"),
    ("analysis_database_path", "VARCHAR"),
    ("parquet_dir", "VARCHAR"),
    ("input_paths_json", "VARCHAR"),
    ("include_profiles_json", "VARCHAR"),
    ("input_database_count", "BIGINT"),
    ("input_run_count", "BIGINT"),
    ("tabular_output_mode", "VARCHAR"),
    ("legacy_csv_outputs_enabled", "BOOLEAN"),
    ("parquet_exports_enabled", "BOOLEAN"),
    ("notes", "VARCHAR"),
]

HISTORICAL_ANALYSIS_INPUT_RUNS_SCHEMA = [
    ("analysis_run_id", "VARCHAR"),
    ("database_path", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("created_at_utc", "TIMESTAMP"),
    ("run_label", "VARCHAR"),
    ("suite_name", "VARCHAR"),
    ("snapshot_date", "VARCHAR"),
    ("snapshot_session", "VARCHAR"),
    ("snapshot_label", "VARCHAR"),
    ("profile_names_json", "VARCHAR"),
    ("profile_count", "BIGINT"),
]

HISTORICAL_ANALYSIS_VIEW_NAMES: tuple[str, ...] = (
    "vw_analysis_input_runs",
    "vw_profile_horizon_progression_core",
    "vw_profile_horizon_alignment_stats",
    "vw_profile_horizon_current_leaders",
    "vw_profile_horizon_snapshot_deltas",
)


for horizon_name in TRACKED_HORIZONS:
    HISTORY_HEADERS.extend(
        [
            f"{horizon_name}_score",
            f"{horizon_name}_score_delta_vs_previous",
            f"{horizon_name}_direction",
            f"{horizon_name}_confidence",
            f"{horizon_name}_coverage",
            f"{horizon_name}_setup",
            f"{horizon_name}_rank",
            f"{horizon_name}_rank_improvement_vs_previous",
            f"{horizon_name}_rank_percentile",
        ]
    )
    SUMMARY_HEADERS.extend(
        [
            f"{horizon_name}_first_score",
            f"{horizon_name}_last_score",
            f"{horizon_name}_score_delta_total",
            f"{horizon_name}_best_rank",
            f"{horizon_name}_worst_rank",
            f"{horizon_name}_last_rank",
            f"{horizon_name}_rank_improvement_total",
            f"{horizon_name}_avg_score",
            f"{horizon_name}_avg_rank",
            f"{horizon_name}_last_direction",
            f"{horizon_name}_last_setup",
            f"{horizon_name}_last_confidence",
            f"{horizon_name}_last_coverage",
        ]
    )


@dataclass(frozen=True)
class SnapshotMetadata:
    csv_path: Path
    profile_name: str
    snapshot_date: date | None
    snapshot_date_label: str
    snapshot_session: str
    snapshot_label: str
    sort_key: tuple[int, int, str]
    source_reference: str | None = None

    @property
    def resolved_source_reference(self) -> str:
        return self.source_reference or self.csv_path.as_posix()


@dataclass(frozen=True)
class SnapshotFileData:
    metadata: SnapshotMetadata
    rows: list[dict[str, str | None]]
    horizon_ranks: dict[str, dict[str, int]]
    horizon_percentiles: dict[str, dict[str, float]]


@dataclass(frozen=True)
class DuckDBInputRun:
    database_path: Path
    run_id: str
    created_at_utc: datetime
    run_label: str
    suite_name: str
    snapshot_date_label: str
    snapshot_session: str
    snapshot_label: str
    profile_names: tuple[str, ...]


@dataclass(frozen=True)
class DuckDBHistoricalAggregationLayout:
    run_dir: Path
    database_path: Path
    parquet_dir: Path
    analysis_run_id: str
    created_at_utc: datetime


def _aggregate_snapshot_files(
    *,
    snapshot_files: list[SnapshotFileData],
    output_root: Path,
    input_paths: list[Path],
    include_profiles: Sequence[str] | None = None,
    analysis_layout: DuckDBHistoricalAggregationLayout | None = None,
    analysis_input_runs: Sequence[DuckDBInputRun] | None = None,
    write_csv_outputs: bool = True,
    export_parquet: bool = False,
) -> dict[str, Any]:
    if not snapshot_files:
        raise ValueError(
            "No move-prediction profile snapshots were found in the provided input paths."
        )

    if analysis_layout is None and not write_csv_outputs:
        raise ValueError(
            "CSV output can only be disabled for DuckDB historical aggregation runs."
        )

    output_root.mkdir(parents=True, exist_ok=True)

    def _report_locator(table_name: str, csv_path: Path) -> str | Path:
        if write_csv_outputs or analysis_layout is None:
            return csv_path
        return f"{analysis_layout.database_path.as_posix()}::{table_name}"

    manifest_rows: list[dict[str, Any]] = []
    all_history_rows: list[dict[str, Any]] = []
    all_summary_rows: list[dict[str, Any]] = []
    profile_outputs: dict[str, dict[str, Any]] = {}
    table_exports: list[dict[str, Any]] = []
    all_profile_score_rows: dict[str, dict[str, list[dict[str, Any]]]] = {}

    snapshots_by_profile: dict[str, list[SnapshotFileData]] = defaultdict(list)
    for snapshot in snapshot_files:
        snapshots_by_profile[snapshot.metadata.profile_name].append(snapshot)

    aggregate_snapshot_labels = _ordered_unique_snapshot_labels(snapshot_files)
    aggregate_snapshot_columns = [
        _snapshot_column_key(label) for label in aggregate_snapshot_labels
    ]

    for profile_name in sorted(snapshots_by_profile):
        profile_snapshots = sorted(
            snapshots_by_profile[profile_name],
            key=lambda snapshot: snapshot.metadata.sort_key,
        )
        profile_snapshot_labels = _ordered_unique_snapshot_labels(profile_snapshots)
        profile_snapshot_columns = [
            _snapshot_column_key(label) for label in profile_snapshot_labels
        ]
        profile_price_snapshot_columns = [
            _price_snapshot_column_key(label) for label in profile_snapshot_labels
        ]

        history_rows, summary_rows = _build_profile_outputs(
            profile_name=profile_name,
            profile_snapshots=profile_snapshots,
        )
        price_progression_rows = _build_price_progression_rows(
            profile_name=profile_name,
            snapshots=profile_snapshots,
            snapshot_labels=profile_snapshot_labels,
            include_profile_column=True,
        )

        manifest_rows.extend(_build_manifest_rows(profile_snapshots))
        all_history_rows.extend(history_rows)
        all_summary_rows.extend(summary_rows)

        history_csv = output_root / f"profile_{profile_name}__history.csv"
        summary_csv = output_root / f"profile_{profile_name}__summary.csv"
        price_progression_csv = (
            output_root / f"profile_{profile_name}__price_progression.csv"
        )
        history_table = _table_name_from_file_stem(history_csv.stem)
        summary_table = _table_name_from_file_stem(summary_csv.stem)
        price_progression_table = _table_name_from_file_stem(price_progression_csv.stem)
        if write_csv_outputs:
            _write_dict_rows_to_csv(history_csv, HISTORY_HEADERS, history_rows)
            _write_dict_rows_to_csv(summary_csv, SUMMARY_HEADERS, summary_rows)
            _write_dict_rows_to_csv(
                price_progression_csv,
                PRICE_PROGRESSION_BASE_HEADERS + profile_snapshot_columns,
                price_progression_rows,
            )
        table_exports.extend(
            [
                {
                    "table_name": history_table,
                    "headers": HISTORY_HEADERS,
                    "rows": history_rows,
                    "file_path": _report_locator(history_table, history_csv),
                },
                {
                    "table_name": summary_table,
                    "headers": SUMMARY_HEADERS,
                    "rows": summary_rows,
                    "file_path": _report_locator(summary_table, summary_csv),
                },
                {
                    "table_name": price_progression_table,
                    "headers": PRICE_PROGRESSION_BASE_HEADERS
                    + profile_snapshot_columns,
                    "rows": price_progression_rows,
                    "file_path": _report_locator(
                        price_progression_table,
                        price_progression_csv,
                    ),
                },
            ]
        )

        score_progression_csv_by_horizon: dict[str, Path] = {}
        score_progression_tables_by_horizon: dict[str, str] = {}
        profile_score_rows_by_horizon: dict[str, list[dict[str, Any]]] = {}
        for horizon_name in TRACKED_HORIZONS:
            score_rows = _build_score_progression_rows(
                profile_name=profile_name,
                snapshots=profile_snapshots,
                horizon_name=horizon_name,
                include_profile_column=True,
            )
            combined_rows = _build_combined_score_price_rows(
                score_rows=score_rows,
                price_rows=price_progression_rows,
                snapshot_labels=profile_snapshot_labels,
            )
            score_progression_csv = (
                output_root
                / f"profile_{profile_name}__score_progression__{horizon_name}.csv"
            )
            score_progression_table = _table_name_from_file_stem(
                score_progression_csv.stem
            )
            if write_csv_outputs:
                _write_dict_rows_to_csv(
                    score_progression_csv,
                    SCORE_PROGRESSION_BASE_HEADERS
                    + PRICE_EXTRA_BASE_HEADERS
                    + profile_snapshot_columns
                    + profile_price_snapshot_columns,
                    combined_rows,
                )
            table_exports.append(
                {
                    "table_name": score_progression_table,
                    "headers": SCORE_PROGRESSION_BASE_HEADERS
                    + PRICE_EXTRA_BASE_HEADERS
                    + profile_snapshot_columns
                    + profile_price_snapshot_columns,
                    "rows": combined_rows,
                    "file_path": _report_locator(
                        score_progression_table,
                        score_progression_csv,
                    ),
                }
            )
            if write_csv_outputs:
                score_progression_csv_by_horizon[horizon_name] = score_progression_csv
            score_progression_tables_by_horizon[horizon_name] = score_progression_table
            profile_score_rows_by_horizon[horizon_name] = score_rows

        all_profile_score_rows[profile_name] = profile_score_rows_by_horizon
        profile_outputs[profile_name] = {
            "history_table": history_table,
            "summary_table": summary_table,
            "price_progression_table": price_progression_table,
            "score_progression_tables_by_horizon": score_progression_tables_by_horizon,
            "history_output_name": (
                history_csv.name if write_csv_outputs else history_table
            ),
            "summary_output_name": (
                summary_csv.name if write_csv_outputs else summary_table
            ),
            "price_progression_output_name": (
                price_progression_csv.name
                if write_csv_outputs
                else price_progression_table
            ),
            "score_progression_output_names": {
                horizon_name: (
                    score_progression_csv_by_horizon[horizon_name].name
                    if write_csv_outputs
                    else score_progression_tables_by_horizon[horizon_name]
                )
                for horizon_name in TRACKED_HORIZONS
            },
            "snapshot_count": len(profile_snapshots),
            "trading_session_count": len(profile_snapshot_labels),
            "symbol_count": len(summary_rows),
        }
        if write_csv_outputs:
            profile_outputs[profile_name].update(
                {
                    "history_csv": history_csv,
                    "summary_csv": summary_csv,
                    "price_progression_csv": price_progression_csv,
                    "score_progression_csv_by_horizon": score_progression_csv_by_horizon,
                }
            )

    manifest_rows.sort(
        key=lambda row: (
            _snapshot_sort_key_from_row(
                row.get("snapshot_date"),
                row.get("snapshot_session"),
                row.get("source_file"),
            ),
            str(row.get("profile_name") or ""),
        )
    )
    all_history_rows.sort(
        key=lambda row: (
            _snapshot_sort_key_from_row(
                row.get("snapshot_date"),
                row.get("snapshot_session"),
                row.get("source_file"),
            ),
            _sort_rank_value(row.get("days_rank")),
            str(row.get("profile_name") or ""),
            str(row.get("symbol") or ""),
        )
    )
    all_summary_rows.sort(
        key=lambda row: (
            str(row.get("profile_name") or ""),
            _summary_rank_sort_key(row),
            str(row.get("symbol") or ""),
        )
    )

    manifest_csv = output_root / "_aggregation_manifest.csv"
    all_history_csv = output_root / "_all_profiles_history.csv"
    all_summary_csv = output_root / "_all_profiles_summary.csv"
    all_price_progression_csv = output_root / "_all_profiles_price_progression.csv"
    cross_comparison_csv = output_root / "_all_profiles_cross_comparison.csv"
    manifest_table = _table_name_from_file_stem(manifest_csv.stem)
    all_history_table = _table_name_from_file_stem(all_history_csv.stem)
    all_summary_table = _table_name_from_file_stem(all_summary_csv.stem)
    all_price_progression_table = _table_name_from_file_stem(
        all_price_progression_csv.stem
    )
    cross_comparison_table = _table_name_from_file_stem(cross_comparison_csv.stem)
    overview_log = output_root / "_aggregation_overview.log"

    aggregate_price_progression_rows = _build_price_progression_rows(
        profile_name=None,
        snapshots=snapshot_files,
        snapshot_labels=aggregate_snapshot_labels,
        include_profile_column=False,
    )
    aggregate_price_snapshot_columns = [
        _price_snapshot_column_key(label) for label in aggregate_snapshot_labels
    ]

    all_score_progression_csv_by_horizon: dict[str, Path] = {}
    all_score_progression_tables_by_horizon: dict[str, str] = {}
    for horizon_name in TRACKED_HORIZONS:
        aggregate_score_rows: list[dict[str, Any]] = []
        for profile_name in sorted(snapshots_by_profile):
            profile_snapshots = sorted(
                snapshots_by_profile[profile_name],
                key=lambda snapshot: snapshot.metadata.sort_key,
            )
            aggregate_score_rows.extend(
                _build_score_progression_rows(
                    profile_name=profile_name,
                    snapshots=profile_snapshots,
                    horizon_name=horizon_name,
                    include_profile_column=True,
                )
            )
        aggregate_score_rows.sort(
            key=lambda row: (
                str(row.get("profile_name") or ""),
                _sort_rank_value(row.get("last_rank")),
                str(row.get("symbol") or ""),
            )
        )
        combined_aggregate_rows = _build_combined_score_price_rows(
            score_rows=aggregate_score_rows,
            price_rows=aggregate_price_progression_rows,
            snapshot_labels=aggregate_snapshot_labels,
        )
        all_score_progression_csv = (
            output_root / f"_all_profiles_score_progression__{horizon_name}.csv"
        )
        all_score_progression_table = _table_name_from_file_stem(
            all_score_progression_csv.stem
        )
        if write_csv_outputs:
            _write_dict_rows_to_csv(
                all_score_progression_csv,
                SCORE_PROGRESSION_BASE_HEADERS
                + PRICE_EXTRA_BASE_HEADERS
                + aggregate_snapshot_columns
                + aggregate_price_snapshot_columns,
                combined_aggregate_rows,
            )
        table_exports.append(
            {
                "table_name": all_score_progression_table,
                "headers": SCORE_PROGRESSION_BASE_HEADERS
                + PRICE_EXTRA_BASE_HEADERS
                + aggregate_snapshot_columns
                + aggregate_price_snapshot_columns,
                "rows": combined_aggregate_rows,
                "file_path": _report_locator(
                    all_score_progression_table,
                    all_score_progression_csv,
                ),
            }
        )
        if write_csv_outputs:
            all_score_progression_csv_by_horizon[horizon_name] = (
                all_score_progression_csv
            )
        all_score_progression_tables_by_horizon[horizon_name] = (
            all_score_progression_table
        )

    sorted_profile_names = sorted(snapshots_by_profile.keys())
    cross_comparison_rows = _build_cross_profile_comparison_rows(
        all_profile_score_rows=all_profile_score_rows,
        aggregate_price_rows=aggregate_price_progression_rows,
        profile_names=sorted_profile_names,
    )

    if write_csv_outputs:
        _write_dict_rows_to_csv(manifest_csv, MANIFEST_HEADERS, manifest_rows)
        _write_dict_rows_to_csv(all_history_csv, HISTORY_HEADERS, all_history_rows)
        _write_dict_rows_to_csv(all_summary_csv, SUMMARY_HEADERS, all_summary_rows)
        _write_dict_rows_to_csv(
            all_price_progression_csv,
            PRICE_PROGRESSION_AGGREGATE_BASE_HEADERS + aggregate_snapshot_columns,
            aggregate_price_progression_rows,
        )
        _write_dict_rows_to_csv(
            cross_comparison_csv,
            _build_cross_comparison_headers(sorted_profile_names),
            cross_comparison_rows,
        )
    table_exports.extend(
        [
            {
                "table_name": manifest_table,
                "headers": MANIFEST_HEADERS,
                "rows": manifest_rows,
                "file_path": _report_locator(manifest_table, manifest_csv),
            },
            {
                "table_name": all_history_table,
                "headers": HISTORY_HEADERS,
                "rows": all_history_rows,
                "file_path": _report_locator(all_history_table, all_history_csv),
            },
            {
                "table_name": all_summary_table,
                "headers": SUMMARY_HEADERS,
                "rows": all_summary_rows,
                "file_path": _report_locator(all_summary_table, all_summary_csv),
            },
            {
                "table_name": all_price_progression_table,
                "headers": PRICE_PROGRESSION_AGGREGATE_BASE_HEADERS
                + aggregate_snapshot_columns,
                "rows": aggregate_price_progression_rows,
                "file_path": _report_locator(
                    all_price_progression_table,
                    all_price_progression_csv,
                ),
            },
            {
                "table_name": cross_comparison_table,
                "headers": _build_cross_comparison_headers(sorted_profile_names),
                "rows": cross_comparison_rows,
                "file_path": _report_locator(
                    cross_comparison_table,
                    cross_comparison_csv,
                ),
            },
        ]
    )

    overview_sections = _build_overview_highlight_sections(
        all_summary_rows=all_summary_rows,
        aggregate_price_progression_rows=aggregate_price_progression_rows,
    )
    if analysis_layout is not None:
        overview_sections.insert(
            0,
            (
                "DuckDB analysis output",
                [
                    f"Analysis run id: {analysis_layout.analysis_run_id}",
                    f"Analysis DuckDB: {analysis_layout.database_path.as_posix()}",
                    (
                        "Parquet export directory: "
                        f"{analysis_layout.parquet_dir.as_posix()}"
                        if export_parquet
                        else "Parquet export directory: disabled"
                    ),
                    (
                        "Tabular output mode: legacy CSV + DuckDB"
                        if write_csv_outputs
                        else "Tabular output mode: DuckDB-only"
                    ),
                ],
            ),
        )
        overview_sections.insert(
            1,
            (
                "DuckDB starter views",
                _build_historical_analysis_view_lines(),
            ),
        )
        overview_sections.insert(
            2,
            (
                "DuckDB core tables",
                [
                    manifest_table,
                    all_history_table,
                    all_summary_table,
                    all_price_progression_table,
                    cross_comparison_table,
                    *[
                        all_score_progression_tables_by_horizon[horizon_name]
                        for horizon_name in TRACKED_HORIZONS
                    ],
                ],
            ),
        )
    if analysis_input_runs:
        overview_sections.insert(
            0,
            (
                "DuckDB input runs",
                _build_duckdb_input_run_lines(analysis_input_runs),
            ),
        )

    _write_overview_log(
        overview_log=overview_log,
        input_paths=input_paths,
        snapshot_files=snapshot_files,
        profile_outputs=profile_outputs,
        extra_sections=overview_sections,
    )

    result: dict[str, Any] = {
        "output_dir": output_root,
        "output_mode": "csv" if write_csv_outputs else "duckdb",
        "manifest_table": manifest_table,
        "history_table": all_history_table,
        "summary_table": all_summary_table,
        "price_progression_table": all_price_progression_table,
        "score_progression_tables_by_horizon": all_score_progression_tables_by_horizon,
        "cross_comparison_table": cross_comparison_table,
        "overview_log": overview_log,
        "profiles": profile_outputs,
    }
    if write_csv_outputs:
        result.update(
            {
                "manifest_csv": manifest_csv,
                "history_csv": all_history_csv,
                "summary_csv": all_summary_csv,
                "price_progression_csv": all_price_progression_csv,
                "score_progression_csv_by_horizon": all_score_progression_csv_by_horizon,
                "cross_comparison_csv": cross_comparison_csv,
            }
        )

    if analysis_layout is not None:
        analysis_tables = _write_aggregation_outputs_to_duckdb(
            analysis_layout=analysis_layout,
            input_paths=input_paths,
            include_profiles=include_profiles,
            input_runs=analysis_input_runs or [],
            table_exports=table_exports,
            overview_log=overview_log,
            write_csv_outputs=write_csv_outputs,
            export_parquet=export_parquet,
        )
        result["analysis_database"] = analysis_layout.database_path
        result["analysis_run_id"] = analysis_layout.analysis_run_id
        result["analysis_tables"] = analysis_tables
        result["analysis_views"] = analysis_tables.get("views", [])
        result["analysis_parquet_dir"] = analysis_tables.get("parquet_dir")
        result["analysis_parquet_exports"] = analysis_tables.get(
            "parquet_exports",
            {},
        )
        result["input_runs"] = [
            {
                "database_path": input_run.database_path,
                "run_id": input_run.run_id,
                "created_at_utc": input_run.created_at_utc,
                "run_label": input_run.run_label,
                "suite_name": input_run.suite_name,
                "snapshot_label": input_run.snapshot_label,
                "profile_names": list(input_run.profile_names),
            }
            for input_run in analysis_input_runs or []
        ]

    return result


def aggregate_move_prediction_history(
    input_paths: Sequence[str | Path] | str | Path,
    output_dir: str | Path | None = None,
    include_profiles: Sequence[str] | None = None,
    recursive: bool = True,
) -> dict[str, Any]:
    """Aggregate exported move-prediction profile CSVs into cross-run timelines.

    The input should point at one or more folders or CSV files previously generated
    by the move-prediction suite. Only per-profile score CSVs are processed.
    Raw CSVs and non-profile exports are ignored.

    Output files written under ``output_dir``:

    - ``_aggregation_manifest.csv``: one row per processed snapshot file
    - ``_all_profiles_history.csv``: one row per ``profile + symbol + snapshot``
    - ``_all_profiles_summary.csv``: one row per ``profile + symbol``
    - ``_all_profiles_score_progression__<horizon>.csv``: wide score matrix per
      ``profile + symbol`` for that horizon, one column per snapshot
      (one file per horizon: ``days``, ``weeks``, ``months``, ``years``)
    - ``_all_profiles_price_progression.csv``: wide matrix of close prices per
      symbol (deduplicated across profiles) with one column per snapshot
    - ``profile_<name>__history.csv``: profile-specific timeline rows
    - ``profile_<name>__summary.csv``: profile-specific summary rows
    - ``profile_<name>__score_progression__<horizon>.csv``: per-profile score
      matrix, one file per horizon
    - ``profile_<name>__price_progression.csv``: per-profile price matrix
    - ``_aggregation_overview.log``: human-readable run summary
    """

    supported_profiles = _resolve_included_profiles(include_profiles)
    output_root = _resolve_output_dir(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    snapshot_files = _load_snapshot_files(
        input_paths=input_paths,
        supported_profiles=supported_profiles,
        recursive=recursive,
    )
    return _aggregate_snapshot_files(
        snapshot_files=snapshot_files,
        output_root=output_root,
        input_paths=_normalize_input_paths(input_paths),
        include_profiles=include_profiles,
    )


def run_move_prediction_history_aggregation(
    input_paths: Sequence[str | Path] | str | Path,
    output_dir: str | Path | None = None,
    include_profiles: Sequence[str] | None = None,
    recursive: bool = True,
) -> dict[str, Any]:
    return aggregate_move_prediction_history(
        input_paths=input_paths,
        output_dir=output_dir,
        include_profiles=include_profiles,
        recursive=recursive,
    )


def aggregate_move_prediction_history_duckdb(
    input_paths: Sequence[str | Path] | str | Path,
    output_dir: str | Path | None = None,
    include_profiles: Sequence[str] | None = None,
    include_run_ids: Sequence[str] | None = None,
    recursive: bool = True,
    write_legacy_csv_outputs: bool = False,
    export_parquet: bool = False,
) -> dict[str, Any]:
    supported_profiles = _resolve_included_profiles(include_profiles)
    layout = _resolve_duckdb_history_output_layout(output_dir)
    snapshot_files, input_runs = _load_duckdb_snapshot_files(
        input_paths=input_paths,
        supported_profiles=supported_profiles,
        recursive=recursive,
        include_run_ids=include_run_ids,
    )
    _prepare_duckdb_history_output_layout(layout)
    return _aggregate_snapshot_files(
        snapshot_files=snapshot_files,
        output_root=layout.run_dir,
        input_paths=_normalize_input_paths(input_paths),
        include_profiles=include_profiles,
        analysis_layout=layout,
        analysis_input_runs=input_runs,
        write_csv_outputs=write_legacy_csv_outputs,
        export_parquet=export_parquet,
    )


def run_move_prediction_history_aggregation_duckdb(
    input_paths: Sequence[str | Path] | str | Path,
    output_dir: str | Path | None = None,
    include_profiles: Sequence[str] | None = None,
    include_run_ids: Sequence[str] | None = None,
    recursive: bool = True,
    write_legacy_csv_outputs: bool = False,
    export_parquet: bool = False,
) -> dict[str, Any]:
    return aggregate_move_prediction_history_duckdb(
        input_paths=input_paths,
        output_dir=output_dir,
        include_profiles=include_profiles,
        include_run_ids=include_run_ids,
        recursive=recursive,
        write_legacy_csv_outputs=write_legacy_csv_outputs,
        export_parquet=export_parquet,
    )


def build_move_prediction_history_inputs_from_folder_names(
    base_dir: str | Path,
    folder_names: Sequence[str],
    required_session_folders: Sequence[str] = REQUIRED_SNAPSHOT_SESSION_FOLDERS,
) -> list[Path]:
    """Resolve dated snapshot folder names under a common parent directory."""

    base_path = Path(base_dir)
    if not base_path.is_dir():
        raise ValueError(f"Snapshot base directory does not exist: {base_path}")

    resolved_paths: list[Path] = []
    seen_paths: set[str] = set()
    missing_folders: list[str] = []
    invalid_folders: list[str] = []

    for folder_name in folder_names:
        normalized_name = _normalize_text(folder_name)
        if normalized_name is None:
            continue

        snapshot_dir = base_path / normalized_name
        if not snapshot_dir.is_dir():
            missing_folders.append(normalized_name)
            continue

        missing_sessions = [
            session_name
            for session_name in required_session_folders
            if not (snapshot_dir / session_name).is_dir()
        ]
        if missing_sessions:
            invalid_folders.append(
                f"{normalized_name} (missing: {', '.join(missing_sessions)})"
            )
            continue

        normalized_path = snapshot_dir.as_posix().lower()
        if normalized_path in seen_paths:
            continue
        seen_paths.add(normalized_path)
        resolved_paths.append(snapshot_dir)

    if missing_folders or invalid_folders:
        message_parts: list[str] = []
        if missing_folders:
            message_parts.append(
                f"missing folders: {', '.join(sorted(missing_folders))}"
            )
        if invalid_folders:
            message_parts.append(
                "folders without required sessions: "
                + ", ".join(sorted(invalid_folders))
            )
        raise ValueError(
            "Invalid snapshot folder selection: " + " | ".join(message_parts)
        )

    if not resolved_paths:
        raise ValueError("No valid snapshot folders were resolved from folder_names.")

    return resolved_paths


def build_move_prediction_history_duckdb_inputs_from_week_folders(
    base_dir: str | Path,
    folder_names: Sequence[str],
) -> list[Path]:
    """Resolve weekly move-prediction DuckDB files under an iso_year folder."""

    base_path = Path(base_dir)
    if not base_path.is_dir():
        raise ValueError(f"DuckDB week base directory does not exist: {base_path}")

    resolved_paths: list[Path] = []
    seen_paths: set[str] = set()
    missing_folders: list[str] = []
    invalid_folders: list[str] = []

    for folder_name in folder_names:
        normalized_name = _normalize_duckdb_week_folder_name(folder_name)
        if normalized_name is None:
            continue

        week_dir = base_path / normalized_name
        if not week_dir.is_dir():
            missing_folders.append(normalized_name)
            continue

        database_candidates = sorted(
            candidate
            for candidate in week_dir.glob("*.duckdb")
            if candidate.is_file() and DUCKDB_FILE_NAME_PATTERN.match(candidate.name)
        )
        if len(database_candidates) != 1:
            invalid_folders.append(
                f"{normalized_name} (expected 1 weekly move_prediction_*.duckdb, found {len(database_candidates)})"
            )
            continue

        database_path = database_candidates[0]
        normalized_path = database_path.as_posix().lower()
        if normalized_path in seen_paths:
            continue
        seen_paths.add(normalized_path)
        resolved_paths.append(database_path)

    if missing_folders or invalid_folders:
        message_parts: list[str] = []
        if missing_folders:
            message_parts.append(
                f"missing week folders: {', '.join(sorted(missing_folders))}"
            )
        if invalid_folders:
            message_parts.append(
                "invalid week folders: " + ", ".join(sorted(invalid_folders))
            )
        raise ValueError("Invalid DuckDB week selection: " + " | ".join(message_parts))

    if not resolved_paths:
        raise ValueError(
            "No valid weekly DuckDB databases were resolved from folder_names."
        )

    return resolved_paths


def _resolve_duckdb_history_output_layout(
    output_dir: str | Path | None,
) -> DuckDBHistoricalAggregationLayout:
    from data_analysis_scripts.trading_view_move_prediction_analysis import LOG_DIR

    created_at_utc = datetime.now(tz=timezone.utc)
    base_dir = (
        Path(output_dir)
        if output_dir is not None
        else Path(LOG_DIR) / "duckdb_runs" / DEFAULT_DUCKDB_HISTORY_ROOT_FOLDER
    )
    run_id = (
        f"{DEFAULT_DUCKDB_HISTORY_RUN_PREFIX}_"
        f"{created_at_utc.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    )
    run_dir = base_dir / "runs" / run_id
    return DuckDBHistoricalAggregationLayout(
        run_dir=run_dir,
        database_path=run_dir / "historical_prediction_analysis.duckdb",
        parquet_dir=run_dir / "parquet",
        analysis_run_id=run_id,
        created_at_utc=created_at_utc,
    )


def _prepare_duckdb_history_output_layout(
    layout: DuckDBHistoricalAggregationLayout,
) -> None:
    layout.run_dir.mkdir(parents=True, exist_ok=True)


def _load_duckdb_snapshot_files(
    input_paths: Sequence[str | Path] | str | Path,
    supported_profiles: set[str],
    recursive: bool,
    include_run_ids: Sequence[str] | None = None,
) -> tuple[list[SnapshotFileData], list[DuckDBInputRun]]:
    normalized_run_ids = {
        run_id
        for run_id in (
            _normalize_text(candidate_run_id)
            for candidate_run_id in include_run_ids or []
        )
        if run_id is not None
    }

    snapshot_files: list[SnapshotFileData] = []
    selected_runs_by_key: dict[tuple[str, str], DuckDBInputRun] = {}

    for database_path in _iter_candidate_duckdb_paths(input_paths, recursive=recursive):
        try:
            metadata_rows = query_move_prediction_duckdb(
                database_path,
                """
                SELECT *
                FROM run_metadata
                ORDER BY created_at_utc, run_id
                """,
            )
        except Exception as exc:
            raise ValueError(
                f"Failed to read run_metadata from DuckDB history input {database_path}: {exc}"
            ) from exc

        if not metadata_rows:
            continue

        metadata_by_run_id: dict[str, dict[str, Any]] = {}
        for metadata_row in metadata_rows:
            run_id = _normalize_text(metadata_row.get("run_id"))
            if run_id is None:
                continue
            if normalized_run_ids and run_id not in normalized_run_ids:
                continue

            created_at_utc = _ensure_datetime_utc(metadata_row.get("created_at_utc"))
            if created_at_utc is None:
                continue
            metadata_by_run_id[run_id] = {
                "created_at_utc": created_at_utc,
                "run_label": _normalize_text(metadata_row.get("run_label")) or "",
                "suite_name": _normalize_text(metadata_row.get("suite_name")) or "",
            }

        if not metadata_by_run_id:
            continue

        try:
            profile_rows = query_move_prediction_duckdb(
                database_path,
                """
                SELECT *
                FROM profile_prediction_rows
                ORDER BY run_id, profile_name, row_number
                """,
            )
        except Exception as exc:
            raise ValueError(
                "Failed to read profile_prediction_rows from DuckDB history input "
                f"{database_path}: {exc}"
            ) from exc

        rows_by_run_profile: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(
            list
        )
        profile_names_by_run_id: dict[str, set[str]] = defaultdict(set)
        for row in profile_rows:
            run_id = _normalize_text(row.get("run_id"))
            profile_name = _normalize_text(row.get("profile_name"))
            if run_id is None or profile_name is None:
                continue
            if run_id not in metadata_by_run_id:
                continue
            if profile_name not in supported_profiles:
                continue

            normalized_row: dict[str, Any] = {}
            for key, value in row.items():
                normalized_key = (str(key) or "").lstrip("\ufeff").strip()
                normalized_row[normalized_key] = (
                    value.strip() if isinstance(value, str) else value
                )
            rows_by_run_profile[(run_id, profile_name)].append(normalized_row)
            profile_names_by_run_id[run_id].add(profile_name)

        for (run_id, profile_name), rows in rows_by_run_profile.items():
            run_metadata = metadata_by_run_id[run_id]
            created_at_utc = run_metadata["created_at_utc"]
            snapshot_date = created_at_utc.date()
            snapshot_date_label = snapshot_date.isoformat()
            snapshot_session = _build_duckdb_snapshot_session(
                run_id=run_id,
                created_at_utc=created_at_utc,
                run_label=run_metadata["run_label"],
            )
            snapshot_label = f"{snapshot_date_label} {snapshot_session}"
            source_reference = (
                f"{database_path.as_posix()}::run_id={run_id}::profile={profile_name}"
            )
            sort_key = (
                snapshot_date.toordinal(),
                _session_sort_value(snapshot_session),
                f"{created_at_utc.isoformat()}::{database_path.as_posix()}::{run_id}::{profile_name}",
            )

            input_run_key = (database_path.as_posix().lower(), run_id)
            if input_run_key not in selected_runs_by_key:
                selected_runs_by_key[input_run_key] = DuckDBInputRun(
                    database_path=database_path,
                    run_id=run_id,
                    created_at_utc=created_at_utc,
                    run_label=run_metadata["run_label"],
                    suite_name=run_metadata["suite_name"],
                    snapshot_date_label=snapshot_date_label,
                    snapshot_session=snapshot_session,
                    snapshot_label=snapshot_label,
                    profile_names=tuple(
                        sorted(profile_names_by_run_id.get(run_id, set()))
                    ),
                )

            horizon_ranks, horizon_percentiles = _build_horizon_rank_maps(rows)
            snapshot_files.append(
                SnapshotFileData(
                    metadata=SnapshotMetadata(
                        csv_path=database_path,
                        profile_name=profile_name,
                        snapshot_date=snapshot_date,
                        snapshot_date_label=snapshot_date_label,
                        snapshot_session=snapshot_session,
                        snapshot_label=snapshot_label,
                        sort_key=sort_key,
                        source_reference=source_reference,
                    ),
                    rows=rows,
                    horizon_ranks=horizon_ranks,
                    horizon_percentiles=horizon_percentiles,
                )
            )

    selected_runs = sorted(
        selected_runs_by_key.values(),
        key=lambda run: (
            run.created_at_utc,
            run.database_path.as_posix().lower(),
            run.run_id,
        ),
    )
    return snapshot_files, selected_runs


def _iter_candidate_duckdb_paths(
    input_paths: Sequence[str | Path] | str | Path,
    recursive: bool,
) -> Iterable[Path]:
    seen_paths: set[str] = set()

    for input_path in _normalize_input_paths(input_paths):
        candidate_paths: Iterable[Path]
        if input_path.is_file():
            candidate_paths = [input_path]
        elif recursive:
            candidate_paths = sorted(input_path.rglob("*.duckdb"))
        else:
            candidate_paths = sorted(input_path.glob("*.duckdb"))

        for candidate_path in candidate_paths:
            if not candidate_path.is_file():
                continue
            if not DUCKDB_FILE_NAME_PATTERN.match(candidate_path.name):
                continue
            normalized_parts = {part.lower() for part in candidate_path.parts}
            if DEFAULT_DUCKDB_HISTORY_ROOT_FOLDER in normalized_parts:
                continue
            normalized_path = candidate_path.as_posix().lower()
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            yield candidate_path


def _write_aggregation_outputs_to_duckdb(
    analysis_layout: DuckDBHistoricalAggregationLayout,
    input_paths: Sequence[Path],
    include_profiles: Sequence[str] | None,
    input_runs: Sequence[DuckDBInputRun],
    table_exports: Sequence[dict[str, Any]],
    overview_log: Path,
    write_csv_outputs: bool,
    export_parquet: bool,
) -> dict[str, Any]:
    input_database_count = len(
        {input_run.database_path.as_posix().lower() for input_run in input_runs}
    )
    included_profile_names = sorted(
        {
            profile_name
            for input_run in input_runs
            for profile_name in input_run.profile_names
        }
    )

    with MovePredictionDuckDBStore(
        database_path=analysis_layout.database_path,
        parquet_dir=analysis_layout.parquet_dir if export_parquet else None,
    ) as store:
        store.delete_run_data(analysis_layout.analysis_run_id)
        store.register_run(
            run_id=analysis_layout.analysis_run_id,
            created_at_utc=analysis_layout.created_at_utc,
            suite_name="tradingview_move_prediction_history_aggregation_duckdb",
            scan_data_count=len(input_runs),
            profile_names=included_profile_names,
            industries=None,
            min_market_cap_usd=None,
            max_market_cap_usd=None,
            include_blind_spot_sections=False,
            run_label=analysis_layout.analysis_run_id,
            run_id_generated=True,
            notes=(
                "DuckDB history aggregation over stored weekly move-prediction runs."
            ),
        )
        store.append_records(
            "historical_analysis_runs",
            [
                {
                    "analysis_run_id": analysis_layout.analysis_run_id,
                    "created_at_utc": analysis_layout.created_at_utc,
                    "output_dir": analysis_layout.run_dir.as_posix(),
                    "analysis_database_path": analysis_layout.database_path.as_posix(),
                    "parquet_dir": (
                        analysis_layout.parquet_dir.as_posix() if export_parquet else ""
                    ),
                    "input_paths_json": json.dumps(
                        [path.as_posix() for path in input_paths],
                        ensure_ascii=False,
                    ),
                    "include_profiles_json": json.dumps(
                        sorted(
                            {
                                profile_name
                                for profile_name in (
                                    _normalize_text(profile_name)
                                    for profile_name in include_profiles or []
                                )
                                if profile_name is not None
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "input_database_count": input_database_count,
                    "input_run_count": len(input_runs),
                    "tabular_output_mode": (
                        "duckdb_and_csv" if write_csv_outputs else "duckdb_only"
                    ),
                    "legacy_csv_outputs_enabled": write_csv_outputs,
                    "parquet_exports_enabled": export_parquet,
                    "notes": (
                        "DuckDB-native historical aggregation dataset from stored weekly "
                        "move-prediction runs."
                        if not write_csv_outputs
                        else "Historical aggregation dataset stored in DuckDB with legacy CSV sidecars enabled."
                    ),
                }
            ],
            HISTORICAL_ANALYSIS_RUNS_SCHEMA,
        )
        store.append_records(
            "historical_analysis_input_runs",
            [
                {
                    "analysis_run_id": analysis_layout.analysis_run_id,
                    "database_path": input_run.database_path.as_posix(),
                    "run_id": input_run.run_id,
                    "created_at_utc": input_run.created_at_utc,
                    "run_label": input_run.run_label,
                    "suite_name": input_run.suite_name,
                    "snapshot_date": input_run.snapshot_date_label,
                    "snapshot_session": input_run.snapshot_session,
                    "snapshot_label": input_run.snapshot_label,
                    "profile_names_json": json.dumps(
                        list(input_run.profile_names),
                        ensure_ascii=False,
                    ),
                    "profile_count": len(input_run.profile_names),
                }
                for input_run in input_runs
            ],
            HISTORICAL_ANALYSIS_INPUT_RUNS_SCHEMA,
        )

        exported_table_names: list[str] = []
        for table_export in table_exports:
            table_name = str(table_export["table_name"])
            headers = list(table_export["headers"])
            rows = list(table_export["rows"])
            store.append_tabular_output(
                table_name,
                headers,
                [
                    [_csv_cell_value(row.get(header)) for header in headers]
                    for row in rows
                ],
                context={"analysis_run_id": analysis_layout.analysis_run_id},
            )
            store.register_report(
                run_id=analysis_layout.analysis_run_id,
                report_key=table_name,
                report_type=(
                    "history_aggregation_csv"
                    if write_csv_outputs
                    else "history_aggregation_duckdb_table"
                ),
                file_path=table_export["file_path"],
            )
            exported_table_names.append(table_name)

        store.register_report(
            run_id=analysis_layout.analysis_run_id,
            report_key="aggregation_overview_log",
            report_type="history_aggregation_log",
            file_path=overview_log,
        )
        view_names = _create_historical_analysis_views(store)
        parquet_exports = (
            store.export_tables_to_parquet(
                run_id=analysis_layout.analysis_run_id,
                parquet_dir=analysis_layout.parquet_dir,
            )
            if export_parquet
            else {}
        )

    return {
        "database_path": analysis_layout.database_path,
        "exported_tables": sorted(exported_table_names),
        "views": view_names,
        "parquet_dir": analysis_layout.parquet_dir if export_parquet else None,
        "parquet_exports": parquet_exports,
    }


def _create_historical_analysis_views(
    store: MovePredictionDuckDBStore,
) -> list[str]:
    for view_name, view_sql in _historical_analysis_view_definitions().items():
        store.conn.execute(f"CREATE OR REPLACE VIEW {view_name} AS {view_sql}")
    return list(HISTORICAL_ANALYSIS_VIEW_NAMES)


def _historical_analysis_view_definitions() -> dict[str, str]:
    progression_union_sql = "\nUNION ALL\n".join(
        _historical_progression_view_select_sql(horizon_name)
        for horizon_name in TRACKED_HORIZONS
    )
    snapshot_union_sql = "\nUNION ALL\n".join(
        _historical_snapshot_delta_view_select_sql(horizon_name)
        for horizon_name in TRACKED_HORIZONS
    )

    return {
        "vw_analysis_input_runs": """
            SELECT analysis_run_id,
                database_path,
                run_id,
                created_at_utc,
                CAST(created_at_utc AS DATE) AS created_date_utc,
                STRFTIME(created_at_utc, '%H:%M:%S') AS created_time_utc,
                run_label,
                suite_name,
                snapshot_date,
                snapshot_session,
                snapshot_label,
                profile_count,
                profile_names_json
            FROM historical_analysis_input_runs
        """,
        "vw_profile_horizon_progression_core": progression_union_sql,
        "vw_profile_horizon_alignment_stats": """
            SELECT analysis_run_id,
                profile_name,
                horizon_name,
                COUNT(*) AS symbol_count,
                AVG(presence_ratio) AS avg_presence_ratio,
                MEDIAN(presence_ratio) AS median_presence_ratio,
                AVG(close_return_pct_total) AS avg_close_return_pct_total,
                MEDIAN(close_return_pct_total) AS median_close_return_pct_total,
                AVG(score_delta_total) AS avg_score_delta_total,
                MEDIAN(score_delta_total) AS median_score_delta_total,
                AVG(rank_improvement_total) AS avg_rank_improvement_total,
                AVG(max_drawdown_pct) AS avg_max_drawdown_pct,
                SUM(CASE WHEN score_delta_total > 0 THEN 1 ELSE 0 END) AS positive_score_delta_count,
                SUM(CASE WHEN close_return_pct_total > 0 THEN 1 ELSE 0 END) AS positive_price_return_count,
                SUM(CASE WHEN score_price_alignment_flag = 1 THEN 1 ELSE 0 END) AS aligned_direction_count,
                SUM(CASE WHEN score_delta_total > 0 AND close_return_pct_total > 0 THEN 1 ELSE 0 END) AS aligned_positive_count,
                SUM(CASE WHEN score_delta_total > 0 AND close_return_pct_total < 0 THEN 1 ELSE 0 END) AS false_positive_count,
                CORR(score_delta_total, close_return_pct_total) AS score_price_corr,
                CORR(rank_improvement_total, close_return_pct_total) AS rank_price_corr
            FROM vw_profile_horizon_progression_core
            GROUP BY analysis_run_id,
                profile_name,
                horizon_name
        """,
        "vw_profile_horizon_current_leaders": """
            SELECT analysis_run_id,
                profile_name,
                horizon_name,
                symbol,
                company_name,
                sector,
                industry,
                snapshots_seen,
                profile_snapshot_count,
                presence_ratio,
                first_score,
                last_score,
                score_delta_total,
                avg_score,
                first_rank,
                last_rank,
                best_rank,
                worst_rank,
                rank_improvement_total,
                first_close,
                last_close,
                close_return_pct_total,
                max_close,
                min_close,
                max_drawdown_pct,
                score_price_alignment_flag,
                ROW_NUMBER() OVER (
                    PARTITION BY analysis_run_id, profile_name, horizon_name
                    ORDER BY last_rank ASC NULLS LAST,
                        last_score DESC NULLS LAST,
                        symbol ASC
                ) AS leader_rank
            FROM vw_profile_horizon_progression_core
        """,
        "vw_profile_horizon_snapshot_deltas": snapshot_union_sql,
    }


def _historical_progression_view_select_sql(horizon_name: str) -> str:
    table_name = f"all_profiles_score_progression__{horizon_name}"
    return f"""
        SELECT analysis_run_id,
            profile_name,
            symbol,
            company_name,
            sector,
            industry,
            '{horizon_name}' AS horizon_name,
            snapshots_seen,
            profile_snapshot_count,
            presence_ratio,
            first_score,
            last_score,
            score_delta_total,
            avg_score,
            first_rank,
            last_rank,
            best_rank,
            worst_rank,
            rank_improvement_total,
            first_close,
            last_close,
            close_return_pct_total,
            max_close,
            min_close,
            max_drawdown_pct,
            CASE
                WHEN score_delta_total IS NULL THEN NULL
                WHEN score_delta_total > 0 THEN 1
                WHEN score_delta_total < 0 THEN -1
                ELSE 0
            END AS score_direction_sign,
            CASE
                WHEN close_return_pct_total IS NULL THEN NULL
                WHEN close_return_pct_total > 0 THEN 1
                WHEN close_return_pct_total < 0 THEN -1
                ELSE 0
            END AS price_direction_sign,
            CASE
                WHEN score_delta_total IS NULL OR close_return_pct_total IS NULL THEN NULL
                WHEN score_delta_total = 0 OR close_return_pct_total = 0 THEN 0
                WHEN (score_delta_total > 0 AND close_return_pct_total > 0)
                    OR (score_delta_total < 0 AND close_return_pct_total < 0) THEN 1
                ELSE -1
            END AS score_price_alignment_flag,
            CASE
                WHEN snapshots_seen IS NULL OR snapshots_seen <= 1 OR score_delta_total IS NULL THEN NULL
                ELSE score_delta_total / NULLIF(snapshots_seen - 1, 0)
            END AS score_delta_per_observation,
            CASE
                WHEN snapshots_seen IS NULL OR snapshots_seen <= 1 OR close_return_pct_total IS NULL THEN NULL
                ELSE close_return_pct_total / NULLIF(snapshots_seen - 1, 0)
            END AS close_return_pct_per_observation
        FROM {table_name}
    """


def _historical_snapshot_delta_view_select_sql(horizon_name: str) -> str:
    score_key = f"{horizon_name}_score"
    score_delta_key = f"{horizon_name}_score_delta_vs_previous"
    direction_key = f"{horizon_name}_direction"
    confidence_key = f"{horizon_name}_confidence"
    coverage_key = f"{horizon_name}_coverage"
    setup_key = f"{horizon_name}_setup"
    rank_key = f"{horizon_name}_rank"
    rank_improvement_key = f"{horizon_name}_rank_improvement_vs_previous"
    rank_percentile_key = f"{horizon_name}_rank_percentile"

    return f"""
        SELECT analysis_run_id,
            profile_name,
            symbol,
            company_name,
            name,
            exchange,
            country,
            sector,
            industry,
            market,
            profile_snapshot_index,
            snapshot_index_for_symbol,
            snapshots_seen_for_symbol,
            trading_sessions_seen_for_symbol,
            profile_trading_session_count,
            trading_session_presence_ratio_for_symbol,
            snapshot_date,
            snapshot_session,
            snapshot_label,
            source_file,
            market_cap_basic,
            close,
            close_return_pct_vs_previous_snapshot,
            "Perf.5D" AS perf_5d,
            "Perf.5D_delta_vs_previous" AS perf_5d_delta_vs_previous,
            "Perf.W" AS perf_w,
            "Perf.W_delta_vs_previous" AS perf_w_delta_vs_previous,
            "Perf.1M" AS perf_1m,
            "Perf.1M_delta_vs_previous" AS perf_1m_delta_vs_previous,
            "Perf.YTD" AS perf_ytd,
            "Perf.YTD_delta_vs_previous" AS perf_ytd_delta_vs_previous,
            "Perf.Y" AS perf_y,
            "Perf.Y_delta_vs_previous" AS perf_y_delta_vs_previous,
            "Perf.5Y" AS perf_5y,
            "Perf.5Y_delta_vs_previous" AS perf_5y_delta_vs_previous,
            '{horizon_name}' AS horizon_name,
            {score_key} AS score,
            {score_delta_key} AS score_delta_vs_previous,
            {direction_key} AS direction,
            {confidence_key} AS confidence,
            {coverage_key} AS coverage,
            {setup_key} AS setup,
            {rank_key} AS rank,
            {rank_improvement_key} AS rank_improvement_vs_previous,
            {rank_percentile_key} AS rank_percentile,
            CASE
                WHEN {score_delta_key} IS NULL OR close_return_pct_vs_previous_snapshot IS NULL THEN NULL
                WHEN {score_delta_key} = 0 OR close_return_pct_vs_previous_snapshot = 0 THEN 0
                WHEN ({score_delta_key} > 0 AND close_return_pct_vs_previous_snapshot > 0)
                    OR ({score_delta_key} < 0 AND close_return_pct_vs_previous_snapshot < 0) THEN 1
                ELSE -1
            END AS score_price_alignment_flag,
            CASE
                WHEN {score_delta_key} IS NULL THEN NULL
                WHEN {score_delta_key} > 0 THEN 1
                WHEN {score_delta_key} < 0 THEN -1
                ELSE 0
            END AS score_direction_sign,
            CASE
                WHEN close_return_pct_vs_previous_snapshot IS NULL THEN NULL
                WHEN close_return_pct_vs_previous_snapshot > 0 THEN 1
                WHEN close_return_pct_vs_previous_snapshot < 0 THEN -1
                ELSE 0
            END AS price_direction_sign
        FROM all_profiles_history
    """


def _historical_analysis_view_descriptions() -> list[tuple[str, str]]:
    return [
        (
            "vw_analysis_input_runs",
            "source weekly run inventory with snapshot labels and profile counts",
        ),
        (
            "vw_profile_horizon_progression_core",
            "long-form profile and horizon summary rows with score deltas, price return, drawdown, and alignment flags",
        ),
        (
            "vw_profile_horizon_alignment_stats",
            "profile and horizon level score-versus-price hit-rate and correlation summary",
        ),
        (
            "vw_profile_horizon_current_leaders",
            "latest leaderboard-ready rows ranked by last_rank inside each profile and horizon",
        ),
        (
            "vw_profile_horizon_snapshot_deltas",
            "one row per profile, symbol, snapshot, and horizon for score-delta versus realized snapshot-return drill-down",
        ),
    ]


def _build_historical_analysis_view_lines() -> list[str]:
    return [
        f"{view_name}: {description}"
        for view_name, description in _historical_analysis_view_descriptions()
    ]


def _build_overview_highlight_sections(
    all_summary_rows: Sequence[dict[str, Any]],
    aggregate_price_progression_rows: Sequence[dict[str, Any]],
) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []

    if aggregate_price_progression_rows:
        price_lines = []
        for row in list(aggregate_price_progression_rows)[:10]:
            price_lines.append(
                (
                    f"{row.get('symbol')}: close_return_pct_total="
                    f"{_format_metric_value(row.get('close_return_pct_total'))} | "
                    f"last_close={_format_metric_value(row.get('last_close'))} | "
                    f"max_drawdown_pct={_format_metric_value(row.get('max_drawdown_pct'))}"
                )
            )
        if price_lines:
            sections.append(("Price highlights", price_lines))

    rows_by_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_summary_rows:
        profile_name = str(row.get("profile_name") or "")
        if not profile_name:
            continue
        rows_by_profile[profile_name].append(row)

    leader_lines = []
    for profile_name in sorted(rows_by_profile):
        leaders = rows_by_profile[profile_name][:3]
        leader_text = "; ".join(
            (
                f"{leader.get('symbol')} "
                f"days_last_rank={_format_metric_value(leader.get('days_last_rank'))} "
                f"days_last_score={_format_metric_value(leader.get('days_last_score'))} "
                f"close_return_pct_total={_format_metric_value(leader.get('close_return_pct_total'))}"
            )
            for leader in leaders
        )
        if leader_text:
            leader_lines.append(f"{profile_name}: {leader_text}")
    if leader_lines:
        sections.append(("Current leaders by profile", leader_lines))

    return sections


def _build_duckdb_input_run_lines(
    input_runs: Sequence[DuckDBInputRun],
) -> list[str]:
    lines: list[str] = []
    for input_run in input_runs:
        lines.append(
            (
                f"{input_run.snapshot_label} | run_id={input_run.run_id} | "
                f"database={input_run.database_path.name} | "
                f"profiles={len(input_run.profile_names)}"
            )
        )
    return lines


def _table_name_from_file_stem(file_stem: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", str(file_stem)).strip("_")
    return sanitized or "history_output"


def _normalize_duckdb_week_folder_name(folder_name: Any) -> str | None:
    normalized = _normalize_text(folder_name)
    if normalized is None:
        return None
    match = DUCKDB_WEEK_FOLDER_PATTERN.match(normalized)
    if match:
        return f"week={int(match.group('week')):02d}"
    if normalized.isdigit():
        return f"week={int(normalized):02d}"
    return normalized


def _build_duckdb_snapshot_session(
    run_id: str,
    created_at_utc: datetime,
    run_label: str | None,
) -> str:
    label_prefix = _slugify_text(run_label) or "run"
    return f"{label_prefix}_{created_at_utc.strftime('%H%M')}_utc_{run_id[-8:]}"


def _ensure_datetime_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    normalized = _normalize_text(value)
    if normalized is None:
        return None

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _slugify_text(value: Any) -> str:
    normalized = _normalize_text(value)
    if normalized is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")


def _format_metric_value(value: Any) -> str:
    numeric_value = _coerce_float(value)
    if numeric_value is not None:
        return f"{numeric_value:.4f}".rstrip("0").rstrip(".")
    normalized = _normalize_text(value)
    return normalized or "n/a"


def _load_snapshot_files(
    input_paths: Sequence[str | Path] | str | Path,
    supported_profiles: set[str],
    recursive: bool,
) -> list[SnapshotFileData]:
    snapshot_files: list[SnapshotFileData] = []

    for csv_path in _iter_candidate_csv_paths(input_paths, recursive=recursive):
        rows = _read_csv_rows(csv_path)
        if not rows:
            continue

        profile_name = _resolve_profile_name(csv_path, rows, supported_profiles)
        if profile_name is None:
            continue

        metadata = _build_snapshot_metadata(csv_path, profile_name)
        horizon_ranks, horizon_percentiles = _build_horizon_rank_maps(rows)
        snapshot_files.append(
            SnapshotFileData(
                metadata=metadata,
                rows=rows,
                horizon_ranks=horizon_ranks,
                horizon_percentiles=horizon_percentiles,
            )
        )

    return snapshot_files


def _build_profile_outputs(
    profile_name: str,
    profile_snapshots: list[SnapshotFileData],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    timelines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    profile_snapshot_count = len(profile_snapshots)
    profile_snapshot_labels = _ordered_unique_snapshot_labels(profile_snapshots)
    profile_trading_session_count = len(profile_snapshot_labels)

    for profile_snapshot_index, snapshot in enumerate(profile_snapshots, start=1):
        for row in snapshot.rows:
            symbol = _normalize_text(row.get("symbol"))
            if symbol is None:
                continue

            record = {
                "profile_name": profile_name,
                "symbol": symbol,
                "company_name": _normalize_text(row.get("Company")) or "",
                "name": _normalize_text(row.get("name")) or "",
                "exchange": _normalize_text(row.get("exchange")) or "",
                "country": _normalize_text(row.get("country")) or "",
                "sector": _normalize_text(row.get("sector")) or "",
                "industry": _normalize_text(row.get("industry")) or "",
                "market": _normalize_text(row.get("market")) or "",
                "profile_snapshot_index": profile_snapshot_index,
                "snapshot_date": snapshot.metadata.snapshot_date_label,
                "snapshot_session": snapshot.metadata.snapshot_session,
                "snapshot_label": snapshot.metadata.snapshot_label,
                "source_file": snapshot.metadata.resolved_source_reference,
                "market_cap_basic": _coerce_float(row.get("market_cap_basic")),
                "close": _coerce_float(row.get("close")),
                "_sort_key": snapshot.metadata.sort_key,
            }

            for performance_field in TRACKED_PERFORMANCE_FIELDS[1:]:
                record[performance_field] = _coerce_float(row.get(performance_field))

            for horizon_name in TRACKED_HORIZONS:
                score_key = f"{horizon_name}_score"
                direction_key = f"{horizon_name}_direction"
                confidence_key = f"{horizon_name}_confidence"
                coverage_key = f"{horizon_name}_coverage"
                setup_key = f"{horizon_name}_setup"
                record[score_key] = _coerce_float(row.get(score_key))
                record[direction_key] = _normalize_text(row.get(direction_key)) or ""
                record[confidence_key] = _coerce_float(row.get(confidence_key))
                record[coverage_key] = _coerce_float(row.get(coverage_key))
                record[setup_key] = _normalize_text(row.get(setup_key)) or ""
                record[f"{horizon_name}_rank"] = snapshot.horizon_ranks[
                    horizon_name
                ].get(symbol)
                record[f"{horizon_name}_rank_percentile"] = (
                    snapshot.horizon_percentiles[horizon_name].get(symbol)
                )

            timelines[symbol].append(record)

    history_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for symbol in sorted(timelines):
        timeline = sorted(timelines[symbol], key=lambda row: row["_sort_key"])
        _finalize_history_timeline(
            timeline=timeline,
            profile_trading_session_count=profile_trading_session_count,
        )
        history_rows.extend(timeline)
        summary_rows.append(
            _build_symbol_summary_row(
                profile_name=profile_name,
                timeline=timeline,
                profile_snapshot_count=profile_snapshot_count,
                profile_trading_session_count=profile_trading_session_count,
            )
        )

    history_rows.sort(
        key=lambda row: (
            row["_sort_key"],
            _sort_rank_value(row.get("days_rank")),
            str(row.get("symbol") or ""),
        )
    )
    for row in history_rows:
        row.pop("_sort_key", None)

    summary_rows.sort(key=_summary_rank_sort_key)
    return history_rows, summary_rows


def _finalize_history_timeline(
    timeline: list[dict[str, Any]],
    profile_trading_session_count: int,
) -> None:
    previous_row: dict[str, Any] | None = None
    total_snapshots = len(timeline)
    trading_sessions_seen_for_symbol = len(
        {
            snapshot_label
            for row in timeline
            if (snapshot_label := _normalize_text(row.get("snapshot_label")))
            is not None
        }
    )
    trading_session_presence_ratio_for_symbol = (
        trading_sessions_seen_for_symbol / profile_trading_session_count
        if profile_trading_session_count
        else None
    )

    for snapshot_index, row in enumerate(timeline, start=1):
        row["snapshot_index_for_symbol"] = snapshot_index
        row["snapshots_seen_for_symbol"] = total_snapshots
        row["trading_sessions_seen_for_symbol"] = trading_sessions_seen_for_symbol
        row["profile_trading_session_count"] = profile_trading_session_count
        row["trading_session_presence_ratio_for_symbol"] = (
            trading_session_presence_ratio_for_symbol
        )

        if previous_row is None:
            row["close_return_pct_vs_previous_snapshot"] = None
            for performance_field in TRACKED_PERFORMANCE_FIELDS[1:]:
                row[f"{performance_field}_delta_vs_previous"] = None
            for horizon_name in TRACKED_HORIZONS:
                row[f"{horizon_name}_score_delta_vs_previous"] = None
                row[f"{horizon_name}_rank_improvement_vs_previous"] = None
            previous_row = row
            continue

        row["close_return_pct_vs_previous_snapshot"] = _pct_change(
            row.get("close"),
            previous_row.get("close"),
        )
        for performance_field in TRACKED_PERFORMANCE_FIELDS[1:]:
            row[f"{performance_field}_delta_vs_previous"] = _delta(
                row.get(performance_field),
                previous_row.get(performance_field),
            )
        for horizon_name in TRACKED_HORIZONS:
            row[f"{horizon_name}_score_delta_vs_previous"] = _delta(
                row.get(f"{horizon_name}_score"),
                previous_row.get(f"{horizon_name}_score"),
            )
            row[f"{horizon_name}_rank_improvement_vs_previous"] = _rank_improvement(
                previous_row.get(f"{horizon_name}_rank"),
                row.get(f"{horizon_name}_rank"),
            )
        previous_row = row


def _build_symbol_summary_row(
    profile_name: str,
    timeline: list[dict[str, Any]],
    profile_snapshot_count: int,
    profile_trading_session_count: int,
) -> dict[str, Any]:
    first_row = timeline[0]
    last_row = timeline[-1]

    first_close = _first_non_null_value(timeline, "close")
    last_close = _last_non_null_value(timeline, "close")
    trading_sessions_seen = len(
        {
            snapshot_label
            for row in timeline
            if (snapshot_label := _normalize_text(row.get("snapshot_label")))
            is not None
        }
    )
    summary_row: dict[str, Any] = {
        "profile_name": profile_name,
        "symbol": last_row.get("symbol") or "",
        "company_name": last_row.get("company_name")
        or first_row.get("company_name")
        or "",
        "name": last_row.get("name") or first_row.get("name") or "",
        "exchange": last_row.get("exchange") or first_row.get("exchange") or "",
        "country": last_row.get("country") or first_row.get("country") or "",
        "sector": last_row.get("sector") or first_row.get("sector") or "",
        "industry": last_row.get("industry") or first_row.get("industry") or "",
        "market": last_row.get("market") or first_row.get("market") or "",
        "snapshots_seen": len(timeline),
        "profile_snapshot_count": profile_snapshot_count,
        "presence_ratio": (
            len(timeline) / profile_snapshot_count if profile_snapshot_count else None
        ),
        "trading_sessions_seen": trading_sessions_seen,
        "profile_trading_session_count": profile_trading_session_count,
        "trading_session_presence_ratio": (
            (
                trading_sessions_seen / profile_trading_session_count
                if profile_trading_session_count
                else None
            )
        ),
        "first_snapshot_date": first_row.get("snapshot_date") or "",
        "first_snapshot_session": first_row.get("snapshot_session") or "",
        "first_snapshot_label": first_row.get("snapshot_label") or "",
        "last_snapshot_date": last_row.get("snapshot_date") or "",
        "last_snapshot_session": last_row.get("snapshot_session") or "",
        "last_snapshot_label": last_row.get("snapshot_label") or "",
        "total_snapshot_span_days": _snapshot_span_days(
            first_row.get("snapshot_date"),
            last_row.get("snapshot_date"),
        ),
        "first_close": first_close,
        "last_close": last_close,
        "close_return_pct_total": _pct_change(last_close, first_close),
    }

    for performance_field in TRACKED_PERFORMANCE_FIELDS[1:]:
        first_value = _first_non_null_value(timeline, performance_field)
        last_value = _last_non_null_value(timeline, performance_field)
        summary_row[f"{performance_field}_first"] = first_value
        summary_row[f"{performance_field}_last"] = last_value
        summary_row[f"{performance_field}_delta_total"] = _delta(
            last_value,
            first_value,
        )

    for horizon_name in TRACKED_HORIZONS:
        score_key = f"{horizon_name}_score"
        rank_key = f"{horizon_name}_rank"
        direction_key = f"{horizon_name}_direction"
        setup_key = f"{horizon_name}_setup"
        confidence_key = f"{horizon_name}_confidence"
        coverage_key = f"{horizon_name}_coverage"

        scores = _non_null_numeric_values(timeline, score_key)
        ranks = _non_null_numeric_values(timeline, rank_key)
        first_score = _first_non_null_value(timeline, score_key)
        last_score = _last_non_null_value(timeline, score_key)
        first_rank = _first_non_null_value(timeline, rank_key)
        last_rank = _last_non_null_value(timeline, rank_key)

        summary_row[f"{horizon_name}_first_score"] = first_score
        summary_row[f"{horizon_name}_last_score"] = last_score
        summary_row[f"{horizon_name}_score_delta_total"] = _delta(
            last_score,
            first_score,
        )
        summary_row[f"{horizon_name}_best_rank"] = int(min(ranks)) if ranks else None
        summary_row[f"{horizon_name}_worst_rank"] = int(max(ranks)) if ranks else None
        summary_row[f"{horizon_name}_last_rank"] = (
            int(last_rank) if last_rank is not None else None
        )
        summary_row[f"{horizon_name}_rank_improvement_total"] = _rank_improvement(
            first_rank,
            last_rank,
        )
        summary_row[f"{horizon_name}_avg_score"] = (
            sum(scores) / len(scores) if scores else None
        )
        summary_row[f"{horizon_name}_avg_rank"] = (
            sum(ranks) / len(ranks) if ranks else None
        )
        summary_row[f"{horizon_name}_last_direction"] = (
            _last_non_null_value(timeline, direction_key) or ""
        )
        summary_row[f"{horizon_name}_last_setup"] = (
            _last_non_null_value(timeline, setup_key) or ""
        )
        summary_row[f"{horizon_name}_last_confidence"] = _last_non_null_value(
            timeline,
            confidence_key,
        )
        summary_row[f"{horizon_name}_last_coverage"] = _last_non_null_value(
            timeline,
            coverage_key,
        )

    return summary_row


def _ordered_unique_snapshot_labels(
    snapshots: Sequence[SnapshotFileData],
) -> list[str]:
    seen_labels: set[str] = set()
    ordered_labels: list[str] = []
    for snapshot in sorted(snapshots, key=lambda item: item.metadata.sort_key):
        label = snapshot.metadata.snapshot_label
        if label in seen_labels:
            continue
        seen_labels.add(label)
        ordered_labels.append(label)
    return ordered_labels


def _snapshot_column_key(snapshot_label: str) -> str:
    return SNAPSHOT_COLUMN_PREFIX + snapshot_label.replace(" ", "__")


def _price_snapshot_column_key(snapshot_label: str) -> str:
    return PRICE_SNAPSHOT_COLUMN_PREFIX + snapshot_label.replace(" ", "__")


def _max_drawdown_pct(values: list[float]) -> float | None:
    if not values:
        return None
    running_peak = values[0]
    max_drawdown = 0.0
    for value in values[1:]:
        if value > running_peak:
            running_peak = value
            continue
        if running_peak == 0.0:
            continue
        drawdown = ((value - running_peak) / running_peak) * 100.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    return max_drawdown


def _build_score_progression_rows(
    profile_name: str | None,
    snapshots: list[SnapshotFileData],
    horizon_name: str,
    include_profile_column: bool,
) -> list[dict[str, Any]]:
    snapshot_count = len(_ordered_unique_snapshot_labels(snapshots))
    score_key = f"{horizon_name}_score"

    metadata_by_symbol: dict[str, dict[str, str]] = {}
    rows_by_symbol: dict[str, dict[str, Any]] = {}
    score_series_by_symbol: dict[str, list[tuple[Any, float]]] = defaultdict(list)
    rank_series_by_symbol: dict[str, list[tuple[Any, int]]] = defaultdict(list)

    for snapshot in snapshots:
        column_key = _snapshot_column_key(snapshot.metadata.snapshot_label)
        for row in snapshot.rows:
            symbol = _normalize_text(row.get("symbol"))
            if symbol is None:
                continue

            metadata = metadata_by_symbol.setdefault(
                symbol,
                {"company_name": "", "sector": "", "industry": ""},
            )
            metadata["company_name"] = (
                _normalize_text(row.get("Company")) or metadata["company_name"]
            )
            metadata["sector"] = (
                _normalize_text(row.get("sector")) or metadata["sector"]
            )
            metadata["industry"] = (
                _normalize_text(row.get("industry")) or metadata["industry"]
            )

            progression_row = rows_by_symbol.setdefault(
                symbol,
                {"symbol": symbol},
            )
            if include_profile_column and profile_name is not None:
                progression_row["profile_name"] = profile_name

            score_value = _coerce_float(row.get(score_key))
            if score_value is not None and column_key not in progression_row:
                progression_row[column_key] = score_value
                score_series_by_symbol[symbol].append(
                    (snapshot.metadata.sort_key, score_value)
                )

            rank_value = snapshot.horizon_ranks[horizon_name].get(symbol)
            if rank_value is not None:
                existing_ranks = rank_series_by_symbol[symbol]
                if (
                    not existing_ranks
                    or existing_ranks[-1][0] != snapshot.metadata.sort_key
                ):
                    existing_ranks.append((snapshot.metadata.sort_key, rank_value))

    finalized_rows: list[dict[str, Any]] = []
    for symbol, progression_row in rows_by_symbol.items():
        metadata = metadata_by_symbol[symbol]
        progression_row["company_name"] = metadata["company_name"]
        progression_row["sector"] = metadata["sector"]
        progression_row["industry"] = metadata["industry"]

        score_series = [score for _, score in sorted(score_series_by_symbol[symbol])]
        rank_series = [rank for _, rank in sorted(rank_series_by_symbol[symbol])]

        progression_row["snapshots_seen"] = len(score_series)
        progression_row["profile_snapshot_count"] = snapshot_count
        progression_row["presence_ratio"] = (
            len(score_series) / snapshot_count if snapshot_count else None
        )
        progression_row["first_score"] = score_series[0] if score_series else None
        progression_row["last_score"] = score_series[-1] if score_series else None
        progression_row["score_delta_total"] = (
            score_series[-1] - score_series[0] if len(score_series) >= 2 else None
        )
        progression_row["avg_score"] = (
            sum(score_series) / len(score_series) if score_series else None
        )
        progression_row["first_rank"] = int(rank_series[0]) if rank_series else None
        progression_row["last_rank"] = int(rank_series[-1]) if rank_series else None
        progression_row["best_rank"] = int(min(rank_series)) if rank_series else None
        progression_row["worst_rank"] = int(max(rank_series)) if rank_series else None
        progression_row["rank_improvement_total"] = (
            int(rank_series[0] - rank_series[-1]) if len(rank_series) >= 2 else None
        )

        finalized_rows.append(progression_row)

    finalized_rows.sort(
        key=lambda row: (
            str(row.get("profile_name") or ""),
            _sort_rank_value(row.get("last_rank")),
            row["symbol"],
        )
    )
    return finalized_rows


def _build_price_progression_rows(
    profile_name: str | None,
    snapshots: list[SnapshotFileData],
    snapshot_labels: list[str],
    include_profile_column: bool,
) -> list[dict[str, Any]]:
    snapshot_count = len(snapshot_labels)
    metadata_by_symbol: dict[str, dict[str, str]] = {}
    rows_by_symbol: dict[str, dict[str, Any]] = {}
    close_series_by_symbol: dict[str, list[tuple[Any, float]]] = defaultdict(list)

    for snapshot in snapshots:
        column_key = _snapshot_column_key(snapshot.metadata.snapshot_label)
        for row in snapshot.rows:
            symbol = _normalize_text(row.get("symbol"))
            if symbol is None:
                continue

            metadata = metadata_by_symbol.setdefault(
                symbol,
                {"company_name": "", "sector": "", "industry": ""},
            )
            metadata["company_name"] = (
                _normalize_text(row.get("Company")) or metadata["company_name"]
            )
            metadata["sector"] = (
                _normalize_text(row.get("sector")) or metadata["sector"]
            )
            metadata["industry"] = (
                _normalize_text(row.get("industry")) or metadata["industry"]
            )

            progression_row = rows_by_symbol.setdefault(symbol, {"symbol": symbol})
            if include_profile_column and profile_name is not None:
                progression_row["profile_name"] = profile_name

            close_value = _coerce_float(row.get("close"))
            if close_value is None:
                continue

            existing_value = progression_row.get(column_key)
            if existing_value is None:
                progression_row[column_key] = close_value
                close_series_by_symbol[symbol].append(
                    (snapshot.metadata.sort_key, close_value)
                )

    finalized_rows: list[dict[str, Any]] = []
    for symbol, progression_row in rows_by_symbol.items():
        metadata = metadata_by_symbol[symbol]
        progression_row["company_name"] = metadata["company_name"]
        progression_row["sector"] = metadata["sector"]
        progression_row["industry"] = metadata["industry"]

        close_series = [
            close_value for _, close_value in sorted(close_series_by_symbol[symbol])
        ]
        progression_row["snapshots_seen"] = len(close_series)
        progression_row["profile_snapshot_count"] = snapshot_count
        progression_row["presence_ratio"] = (
            len(close_series) / snapshot_count if snapshot_count else None
        )
        progression_row["first_close"] = close_series[0] if close_series else None
        progression_row["last_close"] = close_series[-1] if close_series else None
        progression_row["close_return_pct_total"] = (
            _pct_change(close_series[-1], close_series[0])
            if len(close_series) >= 2
            else None
        )
        progression_row["max_close"] = max(close_series) if close_series else None
        progression_row["min_close"] = min(close_series) if close_series else None
        progression_row["max_drawdown_pct"] = _max_drawdown_pct(close_series)

        finalized_rows.append(progression_row)

    finalized_rows.sort(
        key=lambda row: (
            -(_coerce_float(row.get("close_return_pct_total")) or float("-inf")),
            row["symbol"],
        )
    )
    return finalized_rows


def _build_combined_score_price_rows(
    score_rows: list[dict[str, Any]],
    price_rows: list[dict[str, Any]],
    snapshot_labels: list[str],
) -> list[dict[str, Any]]:
    """Merge score progression rows with price data for the same symbols.

    Price snapshot columns are stored under PRICE_SNAPSHOT_COLUMN_PREFIX so they
    sit alongside score snapshot columns without name collisions.
    """
    price_by_symbol: dict[str, dict[str, Any]] = {
        row["symbol"]: row for row in price_rows
    }

    merged: list[dict[str, Any]] = []
    for score_row in score_rows:
        symbol = score_row["symbol"]
        price_row = price_by_symbol.get(symbol, {})
        combined = dict(score_row)

        for field in PRICE_EXTRA_BASE_HEADERS:
            combined[field] = price_row.get(field)

        for label in snapshot_labels:
            price_col = _price_snapshot_column_key(label)
            score_col = _snapshot_column_key(label)
            combined[price_col] = price_row.get(score_col)

        merged.append(combined)

    return merged


def _build_cross_comparison_headers(profile_names: list[str]) -> list[str]:
    """Return column headers for the cross-profile comparison file."""
    headers = [
        "symbol",
        "company_name",
        "sector",
        "industry",
        "first_close",
        "last_close",
        "close_return_pct_total",
        "max_close",
        "min_close",
        "max_drawdown_pct",
    ]
    for horizon_name in TRACKED_HORIZONS:
        for profile_name in profile_names:
            prefix = f"{profile_name}__{horizon_name}__"
            headers.extend(
                [
                    prefix + "last_score",
                    prefix + "last_rank",
                    prefix + "avg_score",
                    prefix + "score_delta_total",
                    prefix + "rank_improvement_total",
                ]
            )
    return headers


def _build_cross_profile_comparison_rows(
    all_profile_score_rows: dict[str, dict[str, list[dict[str, Any]]]],
    aggregate_price_rows: list[dict[str, Any]],
    profile_names: list[str],
) -> list[dict[str, Any]]:
    """Build a cross-profile comparison table.

    One row per symbol showing price performance (profile-agnostic) plus score
    and rank metrics from every profile for every horizon, so profiles can be
    compared side-by-side on both dimensions.

    Args:
        all_profile_score_rows: dict[profile_name][horizon_name] -> score rows.
        aggregate_price_rows: aggregate price progression rows (no profile column).
        profile_names: ordered list of profile names for consistent column layout.
    """
    price_by_symbol: dict[str, dict[str, Any]] = {
        row["symbol"]: row for row in aggregate_price_rows
    }

    # Build symbol -> profile_name -> horizon_name -> score_row index
    score_index: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    for profile_name, score_rows_by_horizon in all_profile_score_rows.items():
        for horizon_name, score_rows in score_rows_by_horizon.items():
            for row in score_rows:
                symbol = row["symbol"]
                score_index.setdefault(symbol, {}).setdefault(profile_name, {})[
                    horizon_name
                ] = row

    all_symbols: set[str] = set(price_by_symbol.keys()) | set(score_index.keys())

    comparison_rows: list[dict[str, Any]] = []
    for symbol in sorted(all_symbols):
        price_row = price_by_symbol.get(symbol, {})
        company_name = str(price_row.get("company_name") or "")
        sector = str(price_row.get("sector") or "")
        industry = str(price_row.get("industry") or "")
        # Fall back to any score row for identity metadata when missing from price
        if not (company_name and sector and industry):
            for profile_data in score_index.get(symbol, {}).values():
                for horizon_row in profile_data.values():
                    company_name = company_name or str(
                        horizon_row.get("company_name") or ""
                    )
                    sector = sector or str(horizon_row.get("sector") or "")
                    industry = industry or str(horizon_row.get("industry") or "")
                    if company_name and sector and industry:
                        break
                if company_name and sector and industry:
                    break

        row: dict[str, Any] = {
            "symbol": symbol,
            "company_name": company_name,
            "sector": sector,
            "industry": industry,
            "first_close": price_row.get("first_close"),
            "last_close": price_row.get("last_close"),
            "close_return_pct_total": price_row.get("close_return_pct_total"),
            "max_close": price_row.get("max_close"),
            "min_close": price_row.get("min_close"),
            "max_drawdown_pct": price_row.get("max_drawdown_pct"),
        }

        for horizon_name in TRACKED_HORIZONS:
            for profile_name in profile_names:
                score_row = (
                    score_index.get(symbol, {})
                    .get(profile_name, {})
                    .get(horizon_name, {})
                )
                prefix = f"{profile_name}__{horizon_name}__"
                row[prefix + "last_score"] = score_row.get("last_score")
                row[prefix + "last_rank"] = score_row.get("last_rank")
                row[prefix + "avg_score"] = score_row.get("avg_score")
                row[prefix + "score_delta_total"] = score_row.get("score_delta_total")
                row[prefix + "rank_improvement_total"] = score_row.get(
                    "rank_improvement_total"
                )

        comparison_rows.append(row)

    comparison_rows.sort(
        key=lambda r: (
            -(_coerce_float(r.get("close_return_pct_total")) or float("-inf")),
            str(r.get("symbol") or ""),
        )
    )
    return comparison_rows


def _build_manifest_rows(
    profile_snapshots: list[SnapshotFileData],
) -> list[dict[str, Any]]:
    manifest_rows: list[dict[str, Any]] = []
    for profile_snapshot_index, snapshot in enumerate(profile_snapshots, start=1):
        unique_symbols = {
            _normalize_text(row.get("symbol"))
            for row in snapshot.rows
            if _normalize_text(row.get("symbol")) is not None
        }
        manifest_rows.append(
            {
                "profile_name": snapshot.metadata.profile_name,
                "profile_snapshot_index": profile_snapshot_index,
                "snapshot_date": snapshot.metadata.snapshot_date_label,
                "snapshot_session": snapshot.metadata.snapshot_session,
                "snapshot_label": snapshot.metadata.snapshot_label,
                "source_file": snapshot.metadata.resolved_source_reference,
                "rows_read": len(snapshot.rows),
                "unique_symbols": len(unique_symbols),
            }
        )
    return manifest_rows


def _write_overview_log(
    overview_log: Path,
    input_paths: list[Path],
    snapshot_files: list[SnapshotFileData],
    profile_outputs: dict[str, dict[str, Any]],
    extra_sections: Sequence[tuple[str, Sequence[str]]] | None = None,
) -> None:
    overview_log.parent.mkdir(parents=True, exist_ok=True)
    overview_log.write_text("", encoding="utf-8")

    ordered_snapshots = sorted(
        snapshot_files,
        key=lambda snapshot: snapshot.metadata.sort_key,
    )
    unique_symbols = set()
    unique_snapshot_labels = {
        snapshot.metadata.snapshot_label for snapshot in snapshot_files
    }
    for snapshot in snapshot_files:
        for row in snapshot.rows:
            symbol = _normalize_text(row.get("symbol"))
            if symbol is not None:
                unique_symbols.add((snapshot.metadata.profile_name, symbol))

    log_to_file(
        overview_log,
        f"Move prediction history aggregation | generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    )
    log_to_file(overview_log, "=" * 160)
    log_to_file(
        overview_log,
        f"Input paths: {', '.join(path.as_posix() for path in input_paths)}",
    )
    log_to_file(
        overview_log,
        (
            f"Processed snapshot files: {len(snapshot_files)} | "
            f"Unique trading sessions: {len(unique_snapshot_labels)} | "
            f"Profiles: {len(profile_outputs)} | "
            f"Profile-symbol timelines: {len(unique_symbols)}"
        ),
    )
    if ordered_snapshots:
        log_to_file(
            overview_log,
            (
                "Snapshot range: "
                f"{ordered_snapshots[0].metadata.snapshot_label} -> "
                f"{ordered_snapshots[-1].metadata.snapshot_label}"
            ),
        )
    log_to_file(overview_log, "")
    log_to_file(overview_log, "Notes")
    log_to_file(overview_log, "-" * 160)
    log_to_file(
        overview_log,
        "close_return_pct_vs_previous_snapshot is computed from successive exported close values, not from TradingView trailing performance windows.",
    )
    log_to_file(
        overview_log,
        "Perf.* columns are preserved exactly as exported in each snapshot, and *_delta fields measure how those reported trailing values changed between snapshots.",
    )
    log_to_file(
        overview_log,
        "Progression outputs are wide matrices: one row per (profile, symbol[, horizon]) and one column per snapshot label (prefixed with 'snap__'); empty cells indicate the symbol was not present in that snapshot. Legacy mode writes CSV files; DuckDB mode stores the same structures as queryable tables.",
    )
    log_to_file(overview_log, "")
    log_to_file(overview_log, "Per-profile outputs")
    log_to_file(overview_log, "-" * 160)
    for profile_name in sorted(profile_outputs):
        profile_data = profile_outputs[profile_name]
        score_files_text = ", ".join(
            f"{horizon_name}={output_name}"
            for horizon_name, output_name in profile_data[
                "score_progression_output_names"
            ].items()
        )
        log_to_file(
            overview_log,
            (
                f"{profile_name}: snapshots={profile_data['snapshot_count']} | "
                f"trading_sessions={profile_data['trading_session_count']} | "
                f"symbols={profile_data['symbol_count']} | "
                f"history={profile_data['history_output_name']} | "
                f"summary={profile_data['summary_output_name']} | "
                f"price_progression={profile_data['price_progression_output_name']} | "
                f"score_progression=[{score_files_text}]"
            ),
        )

    for section_title, section_lines in extra_sections or []:
        if not section_lines:
            continue
        log_to_file(overview_log, "")
        log_to_file(overview_log, section_title)
        log_to_file(overview_log, "-" * 160)
        for line in section_lines:
            log_to_file(overview_log, str(line))


def _write_dict_rows_to_csv(
    csv_path: Path,
    headers: Sequence[str],
    rows: Iterable[dict[str, Any]],
) -> None:
    serialized_rows = [
        [_csv_cell_value(row.get(header)) for header in headers] for row in rows
    ]
    log_rows_to_csv(csv_path, list(headers), serialized_rows)


def _iter_candidate_csv_paths(
    input_paths: Sequence[str | Path] | str | Path,
    recursive: bool,
) -> Iterable[Path]:
    seen_paths: set[str] = set()

    for input_path in _normalize_input_paths(input_paths):
        candidate_paths: Iterable[Path]
        if input_path.is_file():
            candidate_paths = [input_path]
        elif recursive:
            candidate_paths = sorted(input_path.rglob("*.csv"))
        else:
            candidate_paths = sorted(input_path.glob("*.csv"))

        for candidate_path in candidate_paths:
            if not candidate_path.is_file():
                continue
            candidate_name = candidate_path.name.lower()
            if not candidate_name.startswith(PROFILE_CSV_PREFIX):
                continue
            if candidate_name.endswith(RAW_CSV_SUFFIX):
                continue
            normalized_path = candidate_path.as_posix().lower()
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            yield candidate_path


def _read_csv_rows(csv_path: Path) -> list[dict[str, str | None]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        rows: list[dict[str, str | None]] = []
        for row in reader:
            normalized_row: dict[str, str | None] = {}
            for key, value in row.items():
                normalized_key = (key or "").lstrip("\ufeff").strip()
                normalized_row[normalized_key] = (
                    value.strip() if value is not None else None
                )
            rows.append(normalized_row)
        return rows


def _resolve_profile_name(
    csv_path: Path,
    rows: list[dict[str, str | None]],
    supported_profiles: set[str],
) -> str | None:
    name_match = PROFILE_NAME_PATTERN.search(csv_path.name)
    if name_match:
        candidate = name_match.group("profile").lower()
        if candidate in supported_profiles:
            return candidate

    csv_profile = _normalize_text(rows[0].get("scoring_profile")) if rows else None
    if csv_profile is not None and csv_profile in supported_profiles:
        return csv_profile

    log_path = csv_path.with_suffix(".log")
    if not log_path.exists():
        return None

    with log_path.open("r", encoding="utf-8", errors="ignore") as log_file:
        for index, line in enumerate(log_file, start=1):
            if index > 40:
                break
            match = LOG_PROFILE_PATTERN.search(line)
            if not match:
                continue
            candidate = match.group("profile").lower()
            if candidate in supported_profiles:
                return candidate

    return None


def _build_snapshot_metadata(csv_path: Path, profile_name: str) -> SnapshotMetadata:
    snapshot_date: date | None = None
    snapshot_date_label = ""
    for path_part in reversed(csv_path.parts):
        if not DATE_FOLDER_PATTERN.match(path_part):
            continue
        snapshot_date = datetime.strptime(path_part, DATE_FOLDER_FORMAT).date()
        snapshot_date_label = snapshot_date.isoformat()
        break

    snapshot_session = _extract_snapshot_session(csv_path)
    if snapshot_date is None:
        modified_time = datetime.fromtimestamp(csv_path.stat().st_mtime)
        snapshot_date = modified_time.date()
        snapshot_date_label = snapshot_date.isoformat()

    snapshot_label = f"{snapshot_date_label} {snapshot_session}".strip()
    sort_key = (
        snapshot_date.toordinal(),
        _session_sort_value(snapshot_session),
        csv_path.as_posix().lower(),
    )
    return SnapshotMetadata(
        csv_path=csv_path,
        profile_name=profile_name,
        snapshot_date=snapshot_date,
        snapshot_date_label=snapshot_date_label,
        snapshot_session=snapshot_session,
        snapshot_label=snapshot_label,
        sort_key=sort_key,
    )


def _build_horizon_rank_maps(
    rows: list[dict[str, str | None]],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, float]]]:
    horizon_ranks: dict[str, dict[str, int]] = {
        horizon_name: {} for horizon_name in TRACKED_HORIZONS
    }
    horizon_percentiles: dict[str, dict[str, float]] = {
        horizon_name: {} for horizon_name in TRACKED_HORIZONS
    }

    for horizon_name in TRACKED_HORIZONS:
        score_key = f"{horizon_name}_score"
        scored_symbols: list[tuple[str, float]] = []

        for row in rows:
            symbol = _normalize_text(row.get("symbol"))
            score = _coerce_float(row.get(score_key))
            if symbol is None or score is None:
                continue
            scored_symbols.append((symbol, score))

        scored_symbols.sort(key=lambda item: (-item[1], item[0]))
        total_ranked = len(scored_symbols)
        for rank, (symbol, _) in enumerate(scored_symbols, start=1):
            horizon_ranks[horizon_name][symbol] = rank
            horizon_percentiles[horizon_name][symbol] = (
                (total_ranked - rank + 1) / total_ranked if total_ranked else 0.0
            )

    return horizon_ranks, horizon_percentiles


def _resolve_included_profiles(
    include_profiles: Sequence[str] | None,
) -> set[str]:
    supported_profiles = _supported_profile_names()
    if include_profiles is None:
        return supported_profiles

    normalized = {
        normalized_name.lower()
        for profile_name in include_profiles
        if (normalized_name := _normalize_text(profile_name)) is not None
    }
    invalid_profiles = sorted(normalized - supported_profiles)
    if invalid_profiles:
        raise ValueError(
            f"Unknown move-prediction profiles requested: {', '.join(invalid_profiles)}"
        )
    return normalized


def _resolve_output_dir(output_dir: str | Path | None) -> Path:
    if output_dir is not None:
        return Path(output_dir)

    from data_analysis_scripts.trading_view_move_prediction_analysis import LOG_DIR

    timestamp = datetime.now().strftime(DEFAULT_OUTPUT_FOLDER_FORMAT)
    return Path(LOG_DIR) / f"history_aggregation_{timestamp}"


def _supported_profile_names() -> set[str]:
    from data_analysis_scripts.trading_view_move_prediction_analysis import (
        PRESET_SCORING_PROFILES,
    )

    return set(PRESET_SCORING_PROFILES.keys())


def _normalize_input_paths(
    input_paths: Sequence[str | Path] | str | Path,
) -> list[Path]:
    if isinstance(input_paths, (str, Path)):
        return [Path(input_paths)]
    return [Path(input_path) for input_path in input_paths]


def _extract_snapshot_session(csv_path: Path) -> str:
    for path_part in reversed(csv_path.parts[:-1]):
        if path_part.lower() in SESSION_SORT_ORDER:
            return path_part
    return csv_path.parent.name


def _snapshot_sort_key_from_row(
    snapshot_date: Any,
    snapshot_session: Any,
    fallback_text: Any,
) -> tuple[int, int, str]:
    date_text = _normalize_text(snapshot_date)
    date_ordinal = 999999999
    if date_text is not None:
        try:
            date_ordinal = datetime.strptime(date_text, "%Y-%m-%d").date().toordinal()
        except ValueError:
            date_ordinal = 999999999

    session_text = _normalize_text(snapshot_session) or ""
    return (
        date_ordinal,
        _session_sort_value(session_text),
        str(fallback_text or "").lower(),
    )


def _session_sort_value(snapshot_session: Any) -> int:
    normalized = _normalize_text(snapshot_session)
    if normalized is None:
        return 999999

    session_key = normalized.lower()
    mapped_value = SESSION_SORT_ORDER.get(session_key)
    if mapped_value is not None:
        return mapped_value

    match = DUCKDB_RUN_SESSION_TIME_PATTERN.search(session_key)
    if match:
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        return 100 + (hour * 60) + minute

    return 999999


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    return text


def _coerce_float(value: Any) -> float | None:
    normalized = _normalize_text(value)
    if normalized is None:
        return None
    try:
        return float(normalized.replace(",", ""))
    except ValueError:
        return None


def _delta(current_value: Any, previous_value: Any) -> float | None:
    current_number = _coerce_float(current_value)
    previous_number = _coerce_float(previous_value)
    if current_number is None or previous_number is None:
        return None
    return current_number - previous_number


def _pct_change(current_value: Any, previous_value: Any) -> float | None:
    current_number = _coerce_float(current_value)
    previous_number = _coerce_float(previous_value)
    if current_number is None or previous_number in (None, 0.0):
        return None
    return ((current_number - previous_number) / abs(previous_number)) * 100.0


def _rank_improvement(previous_rank: Any, current_rank: Any) -> int | None:
    previous_number = _coerce_float(previous_rank)
    current_number = _coerce_float(current_rank)
    if previous_number is None or current_number is None:
        return None
    return int(previous_number - current_number)


def _snapshot_span_days(
    first_snapshot_date: Any,
    last_snapshot_date: Any,
) -> int | None:
    first_text = _normalize_text(first_snapshot_date)
    last_text = _normalize_text(last_snapshot_date)
    if first_text is None or last_text is None:
        return None
    first_date = datetime.strptime(first_text, "%Y-%m-%d").date()
    last_date = datetime.strptime(last_text, "%Y-%m-%d").date()
    return (last_date - first_date).days


def _first_non_null_value(
    timeline: list[dict[str, Any]],
    key: str,
) -> Any:
    for row in timeline:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _last_non_null_value(
    timeline: list[dict[str, Any]],
    key: str,
) -> Any:
    for row in reversed(timeline):
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _non_null_numeric_values(
    timeline: list[dict[str, Any]],
    key: str,
) -> list[float]:
    values: list[float] = []
    for row in timeline:
        numeric_value = _coerce_float(row.get(key))
        if numeric_value is not None:
            values.append(numeric_value)
    return values


def _sort_rank_value(value: Any) -> float:
    numeric_value = _coerce_float(value)
    if numeric_value is None:
        return float("inf")
    return numeric_value


def _summary_rank_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        _sort_rank_value(row.get("days_last_rank")),
        _sort_rank_value(row.get("weeks_last_rank")),
        _sort_rank_value(row.get("months_last_rank")),
        _sort_rank_value(row.get("years_last_rank")),
    )


def _csv_cell_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


__all__ = [
    "aggregate_move_prediction_history",
    "aggregate_move_prediction_history_duckdb",
    "build_move_prediction_history_inputs_from_folder_names",
    "build_move_prediction_history_duckdb_inputs_from_week_folders",
    "run_move_prediction_history_aggregation",
    "run_move_prediction_history_aggregation_duckdb",
]
