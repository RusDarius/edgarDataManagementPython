from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

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
    "first_snapshot_date",
    "first_snapshot_session",
    "first_snapshot_label",
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


@dataclass(frozen=True)
class SnapshotFileData:
    metadata: SnapshotMetadata
    rows: list[dict[str, str | None]]
    horizon_ranks: dict[str, dict[str, int]]
    horizon_percentiles: dict[str, dict[str, float]]


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
    if not snapshot_files:
        raise ValueError(
            "No move-prediction profile CSV files were found in the provided input paths."
        )

    manifest_rows: list[dict[str, Any]] = []
    all_history_rows: list[dict[str, Any]] = []
    all_summary_rows: list[dict[str, Any]] = []
    all_price_progression_rows: list[dict[str, Any]] = []
    profile_outputs: dict[str, dict[str, Any]] = {}

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
        all_price_progression_rows.extend(price_progression_rows)

        history_csv = output_root / f"profile_{profile_name}__history.csv"
        summary_csv = output_root / f"profile_{profile_name}__summary.csv"
        price_progression_csv = (
            output_root / f"profile_{profile_name}__price_progression.csv"
        )
        _write_dict_rows_to_csv(history_csv, HISTORY_HEADERS, history_rows)
        _write_dict_rows_to_csv(summary_csv, SUMMARY_HEADERS, summary_rows)
        _write_dict_rows_to_csv(
            price_progression_csv,
            PRICE_PROGRESSION_BASE_HEADERS + profile_snapshot_columns,
            price_progression_rows,
        )

        score_progression_csv_by_horizon: dict[str, Path] = {}
        for horizon_name in TRACKED_HORIZONS:
            score_rows = _build_score_progression_rows(
                profile_name=profile_name,
                snapshots=profile_snapshots,
                horizon_name=horizon_name,
                include_profile_column=True,
            )
            score_progression_csv = (
                output_root
                / f"profile_{profile_name}__score_progression__{horizon_name}.csv"
            )
            _write_dict_rows_to_csv(
                score_progression_csv,
                SCORE_PROGRESSION_BASE_HEADERS + profile_snapshot_columns,
                score_rows,
            )
            score_progression_csv_by_horizon[horizon_name] = score_progression_csv

        profile_outputs[profile_name] = {
            "history_csv": history_csv,
            "summary_csv": summary_csv,
            "score_progression_csv_by_horizon": score_progression_csv_by_horizon,
            "price_progression_csv": price_progression_csv,
            "snapshot_count": len(profile_snapshots),
            "symbol_count": len(summary_rows),
        }

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
    overview_log = output_root / "_aggregation_overview.log"

    aggregate_price_progression_rows = _build_price_progression_rows(
        profile_name=None,
        snapshots=snapshot_files,
        snapshot_labels=aggregate_snapshot_labels,
        include_profile_column=False,
    )

    all_score_progression_csv_by_horizon: dict[str, Path] = {}
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
        all_score_progression_csv = (
            output_root / f"_all_profiles_score_progression__{horizon_name}.csv"
        )
        _write_dict_rows_to_csv(
            all_score_progression_csv,
            SCORE_PROGRESSION_BASE_HEADERS + aggregate_snapshot_columns,
            aggregate_score_rows,
        )
        all_score_progression_csv_by_horizon[horizon_name] = all_score_progression_csv

    all_price_progression_rows.sort(
        key=lambda row: (
            str(row.get("profile_name") or ""),
            -(_coerce_float(row.get("close_return_pct_total")) or float("-inf")),
            str(row.get("symbol") or ""),
        )
    )

    _write_dict_rows_to_csv(manifest_csv, MANIFEST_HEADERS, manifest_rows)
    _write_dict_rows_to_csv(all_history_csv, HISTORY_HEADERS, all_history_rows)
    _write_dict_rows_to_csv(all_summary_csv, SUMMARY_HEADERS, all_summary_rows)
    _write_dict_rows_to_csv(
        all_price_progression_csv,
        PRICE_PROGRESSION_AGGREGATE_BASE_HEADERS + aggregate_snapshot_columns,
        aggregate_price_progression_rows,
    )
    _write_overview_log(
        overview_log=overview_log,
        input_paths=_normalize_input_paths(input_paths),
        snapshot_files=snapshot_files,
        profile_outputs=profile_outputs,
    )

    return {
        "output_dir": output_root,
        "manifest_csv": manifest_csv,
        "history_csv": all_history_csv,
        "summary_csv": all_summary_csv,
        "score_progression_csv_by_horizon": all_score_progression_csv_by_horizon,
        "price_progression_csv": all_price_progression_csv,
        "overview_log": overview_log,
        "profiles": profile_outputs,
    }


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
                "source_file": snapshot.metadata.csv_path.as_posix(),
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
        _finalize_history_timeline(timeline)
        history_rows.extend(timeline)
        summary_rows.append(
            _build_symbol_summary_row(
                profile_name=profile_name,
                timeline=timeline,
                profile_snapshot_count=profile_snapshot_count,
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


def _finalize_history_timeline(timeline: list[dict[str, Any]]) -> None:
    previous_row: dict[str, Any] | None = None
    total_snapshots = len(timeline)

    for snapshot_index, row in enumerate(timeline, start=1):
        row["snapshot_index_for_symbol"] = snapshot_index
        row["snapshots_seen_for_symbol"] = total_snapshots

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
) -> dict[str, Any]:
    first_row = timeline[0]
    last_row = timeline[-1]

    first_close = _first_non_null_value(timeline, "close")
    last_close = _last_non_null_value(timeline, "close")
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
                "source_file": snapshot.metadata.csv_path.as_posix(),
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
) -> None:
    overview_log.parent.mkdir(parents=True, exist_ok=True)
    overview_log.write_text("", encoding="utf-8")

    ordered_snapshots = sorted(
        snapshot_files,
        key=lambda snapshot: snapshot.metadata.sort_key,
    )
    unique_symbols = set()
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
        "Progression CSVs are wide matrices: one row per (profile, symbol[, horizon]) and one column per snapshot label (prefixed with 'snap__'); empty cells indicate the symbol was not present in that snapshot.",
    )
    log_to_file(overview_log, "")
    log_to_file(overview_log, "Per-profile outputs")
    log_to_file(overview_log, "-" * 160)
    for profile_name in sorted(profile_outputs):
        profile_data = profile_outputs[profile_name]
        score_files_text = ", ".join(
            f"{horizon_name}={Path(csv_path).name}"
            for horizon_name, csv_path in profile_data[
                "score_progression_csv_by_horizon"
            ].items()
        )
        log_to_file(
            overview_log,
            (
                f"{profile_name}: snapshots={profile_data['snapshot_count']} | "
                f"symbols={profile_data['symbol_count']} | "
                f"history={Path(profile_data['history_csv']).name} | "
                f"summary={Path(profile_data['summary_csv']).name} | "
                f"score_progression=[{score_files_text}] | "
                f"price_progression={Path(profile_data['price_progression_csv']).name}"
            ),
        )


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
        SESSION_SORT_ORDER.get(snapshot_session.lower(), 99),
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
        SESSION_SORT_ORDER.get(session_text.lower(), 99),
        str(fallback_text or "").lower(),
    )


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
    "build_move_prediction_history_inputs_from_folder_names",
    "run_move_prediction_history_aggregation",
]
