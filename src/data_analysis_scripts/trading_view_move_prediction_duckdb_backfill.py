from __future__ import annotations

import csv
import gc
import json
import math
import multiprocessing
import os
import signal
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from generic_utils.log_to_files_util import log_rows_to_csv
from data_analysis_scripts._shared_analysis_utils import slugify as _slugify
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    BACKSCAN_RAW_CSV_GLOB,
    LOG_DIR as MOVE_PREDICTION_LOG_DIR,
    RAW_MARKET_DATA_DIR,
    _filter_scan_data_by_market_cap,
    run_full_analysis_suite_duckdb,
    run_full_analysis_suite_with_earnings_priority_duckdb,
)

# BACKFILL USAGE EXAMPLE BELOW
# # Historical DuckDB backfill path: replays the same CSV snapshots into the
# # new weekly DuckDB run structure (iso_year=YYYY/week=WW) and can also run
# # earnings-priority outputs. Use this to migrate old daily CSV snapshots.
# #
# # Default behavior skips dated folders that already have a completed
# # backfill run under prediction_analysis/duckdb_runs so reruns do not
# # duplicate work.
# # Point output_dir to a fresh folder to replay everything there, or pass
# # force_rerun=True to overwrite the default destination.
# backfill_result = replay_historical_raw_csvs_into_duckdb_runs(
#     raw_data_folders_or_csvs=[
#         Path(
#             r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\trading_view_all_fields_data"
#         ),
#     ],
#     min_market_cap_usd=1_000_000_000,
#     include_blind_spot_sections=True,
#     include_earnings_priority=True,
#     parallel_mode=True,
#     max_parallel_workers=3,
#     max_memory_gb=30.0,
#     max_cpu_percent=85.0,
#     max_memory_percent=85.0,
#     target_date_labels=[
#         # "16_04_2026",
#         # "17_04_2026",
#         # "20_04_2026",
#         # "21_04_2026",
#         # "22_04_2026",
#         # "23_04_2026",
#         # "24_04_2026",
#         # "27_04_2026",
#         # "28_04_2026",
#         # "29_04_2026",
#         # "30_04_2026",
#         # "01_05_2026",
#         # "04_05_2026",
#         # "05_05_2026",
#         # "06_05_2026",
#         # "07_05_2026",
#         # "08_05_2026",
#         # "11_05_2026",
#         # "12_05_2026",
#         # "13_05_2026",
#         # "14_05_2026",
#         # "15_05_2026",
#         # "18_05_2026",
#         # "19_05_2026",
#         # "20_05_2026",
#         # "21_05_2026",
#         "22_05_2026",
#         "26_05_2026",
#         "27_05_2026",
#         "28_05_2026",
#         "29_05_2026",
#         "01_06_2026",
#         "02_06_2026",
#     ],
#     # output_dir=Path(
#     #     r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis\prediction_analysis\duckdb_runs_replay_test"
#     # ),
#     # force_rerun=False,
# )
# print(
#     "DuckDB backfill finished: "
#     f"outcome={backfill_result['run_outcome']} "
#     f"processed={backfill_result['processed_count']} "
#     f"skipped={backfill_result['skipped_count']} "
#     f"interrupted={backfill_result['interrupted_count']} "
#     f"total_seconds={backfill_result['total_execution_seconds']:.3f} "
#     f"manifest={backfill_result['manifest_csv']}"
# )


DEFAULT_DUCKDB_RUNS_DIR = MOVE_PREDICTION_LOG_DIR / "duckdb_runs"
BACKFILL_OUTPUT_DIR = MOVE_PREDICTION_LOG_DIR / "duckdb_backfill"
BACKFILL_DATE_FORMAT = "%d_%m_%Y"
BACKFILL_MANIFEST_NAME = "duckdb_backfill_manifest.csv"
BACKFILL_SUMMARY_LABEL = "_backfill_run_summary"
BACKFILL_INTERRUPT_SKIP_REASON = "execution_stopped_by_user"
BACKFILL_RUN_ALREADY_COVERED_SKIP_REASON = "backfill_run_already_exists"
DEFAULT_MAX_PARALLEL_WORKERS = 4
DEFAULT_MAX_CPU_PERCENT = 85.0
DEFAULT_MAX_MEMORY_PERCENT = 85.0
DEFAULT_MAX_MEMORY_GB = 20.0
DEFAULT_MEMORY_RESERVE_GB = 1.0
DEFAULT_MAX_PREPARED_SNAPSHOTS = 1
DEFAULT_MAX_INFLIGHT_LOADS = 1
DEFAULT_MAX_ACTIVE_WRITES = 1
DEFAULT_MEMORY_CHECK_COOLDOWN_SECONDS = 0.5
DEFAULT_PARALLEL_WORKER_BACKEND = "process"
DEFAULT_RESOURCE_COOLDOWN_SECONDS = 10.0
DEFAULT_RESOURCE_CPU_SAMPLE_SECONDS = 0.2
DEFAULT_WEEKLY_LOCK_RETRY_ATTEMPTS = 12
DEFAULT_WEEKLY_LOCK_RETRY_SECONDS = 5.0
DUCKDB_WEEKLY_LOCK_ERROR_TEXT = "DuckDB weekly writer lock already exists"
BACKFILL_MANIFEST_HEADERS = [
    "source_date_label",
    "source_date_utc",
    "iso_year",
    "iso_week",
    "status",
    "skip_reason",
    "source_folder",
    "source_csv",
    "rows_loaded",
    "rows_used",
    "duckdb_run_id",
    "duckdb_period_dir",
    "duckdb_database",
    "duckdb_output_root",
    "execution_seconds",
]


class BackfillInterruptRequested(Exception):
    """Raised when a graceful shutdown has been requested."""


@dataclass(frozen=True)
class BackfillCsvInput:
    csv_path: Path
    source_date_utc: datetime
    source_date_label: str
    source_folder: Path


@dataclass(frozen=True)
class BackfillPreparedCsv:
    csv_input: BackfillCsvInput
    rows_loaded: int
    scan_data: list[dict[str, Any]]
    load_seconds: float


@dataclass
class BackfillMemoryBudget:
    """Tracks live RSS for the backfill process tree and applies backpressure."""

    max_gb: float
    reserve_gb: float = DEFAULT_MEMORY_RESERVE_GB
    max_prepared_snapshots: int = DEFAULT_MAX_PREPARED_SNAPSHOTS
    max_inflight_loads: int = DEFAULT_MAX_INFLIGHT_LOADS
    max_active_writes: int = DEFAULT_MAX_ACTIVE_WRITES
    cooldown_seconds: float = DEFAULT_MEMORY_CHECK_COOLDOWN_SECONDS
    peak_rss_gb: float = 0.0
    wait_seconds: float = 0.0
    wait_cycles: int = 0

    @property
    def max_bytes(self) -> int:
        return int(self.max_gb * (1024**3))

    @property
    def reserve_bytes(self) -> int:
        return int(self.reserve_gb * (1024**3))

    def measured_rss_bytes(self) -> int | None:
        return _get_process_tree_rss_bytes()

    def measured_rss_gb(self) -> float | None:
        rss_bytes = self.measured_rss_bytes()
        if rss_bytes is None:
            return None
        return rss_bytes / (1024**3)

    def record_sample(self) -> float | None:
        rss_gb = self.measured_rss_gb()
        if rss_gb is not None:
            self.peak_rss_gb = max(self.peak_rss_gb, rss_gb)
        return rss_gb

    def is_over_budget(self) -> bool:
        rss_bytes = self.measured_rss_bytes()
        if rss_bytes is None:
            return False
        return rss_bytes >= (self.max_bytes - self.reserve_bytes)

    def wait_until_under_budget(self) -> float:
        waited = 0.0
        while self.is_over_budget():
            self.wait_cycles += 1
            time.sleep(self.cooldown_seconds)
            waited += self.cooldown_seconds
            self.record_sample()
        self.wait_seconds += waited
        return waited

    def can_schedule_load(
        self,
        *,
        active_loads: int,
        prepared_waiting: int,
        active_writes: int,
    ) -> bool:
        if active_loads >= self.max_inflight_loads:
            return False
        if prepared_waiting >= self.max_prepared_snapshots:
            return False
        if active_writes >= self.max_active_writes:
            return False
        if self.is_over_budget():
            return False
        return True

    def can_schedule_write(self, *, active_writes: int) -> bool:
        if active_writes >= self.max_active_writes:
            return False
        if self.is_over_budget():
            return False
        return True


@dataclass(frozen=True)
class BackfillResourceThrottle:
    max_cpu_percent: float | None
    max_memory_percent: float | None
    cooldown_seconds: float
    cpu_sample_seconds: float


@dataclass
class BackfillInterruptState:
    requested: bool = False
    requested_at_utc: datetime | None = None


@dataclass(frozen=True)
class BackfillExecutionConfig:
    profile_names: tuple[str, ...] | None
    min_market_cap_usd: float | None
    max_market_cap_usd: float | None
    include_blind_spot_sections: bool
    include_earnings_priority: bool
    duckdb_output_root: Path
    export_parquet: bool
    create_indexes: bool
    run_label_prefix: str
    weekly_lock_retry_attempts: int
    weekly_lock_retry_seconds: float
    resource_throttle: BackfillResourceThrottle | None
    interrupt_state: BackfillInterruptState = field(
        default_factory=BackfillInterruptState
    )


def _set_safe_csv_field_size_limit() -> None:
    field_size_limit = (1 << 31) - 1
    while field_size_limit > 0:
        try:
            csv.field_size_limit(field_size_limit)
            return
        except OverflowError:
            field_size_limit //= 10
    raise OverflowError("Unable to configure csv.field_size_limit for this platform.")


def _deserialize_raw_csv_value(value: str | None) -> Any:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"none", "null", "nan"}:
        return None

    if text[0] in "[{" and text[-1] in "]}":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    try:
        numeric_value = float(text)
    except ValueError:
        return text
    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def load_tradingview_raw_csv_scan_data(csv_path: str | Path) -> list[dict[str, Any]]:
    """Load a saved all-fields CSV and deserialize values for scoring usage."""
    resolved_csv_path = Path(csv_path)
    _set_safe_csv_field_size_limit()
    rows: list[dict[str, Any]] = []
    with resolved_csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for raw_row in reader:
            row = {
                field_name: _deserialize_raw_csv_value(value)
                for field_name, value in raw_row.items()
            }
            if row.get("symbol"):
                rows.append(row)
    return rows


def _parse_date_label(label: str) -> datetime | None:
    try:
        parsed = datetime.strptime(label, BACKFILL_DATE_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _is_tdfields_chunk_csv(csv_path: Path) -> bool:
    """Return True for row-split exports like ``*_chunk_001.csv``."""
    return "_chunk_" in csv_path.stem


def _extract_date_label_from_csv_name(csv_path: Path) -> str | None:
    parts = csv_path.stem.split("_")
    if len(parts) < 3:
        return None
    candidate = "_".join(parts[-3:])
    if _parse_date_label(candidate) is None:
        return None
    return candidate


def _resolve_candidate_date_label(csv_path: Path) -> str | None:
    folder_label = csv_path.parent.name
    if _parse_date_label(folder_label) is not None:
        return folder_label
    return _extract_date_label_from_csv_name(csv_path)


def _normalize_target_date_labels(
    target_date_labels: list[str] | None,
) -> set[str] | None:
    if not target_date_labels:
        return None

    normalized_labels: set[str] = set()
    for raw_label in target_date_labels:
        label = str(raw_label).strip()
        if not label:
            raise ValueError("target_date_labels cannot contain empty values.")
        if _parse_date_label(label) is None:
            raise ValueError(
                "target_date_labels entries must use dd_mm_yyyy format. "
                f"Invalid label: {label}"
            )
        normalized_labels.add(label)

    return normalized_labels


def _discover_main_csv_in_dated_folder(
    folder_path: Path,
    csv_glob: str,
) -> list[Path]:
    folder_matches = sorted(folder_path.glob(csv_glob))
    if not folder_matches:
        folder_matches = sorted(folder_path.glob("*.csv"))

    return [
        candidate
        for candidate in folder_matches
        if not _is_tdfields_chunk_csv(candidate)
    ]


def _discover_candidate_csvs(
    raw_data_folders_or_csvs: Iterable[str | Path],
    csv_glob: str,
    target_date_labels: set[str] | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    for raw_path in raw_data_folders_or_csvs:
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"Backfill input path does not exist: {path}")
        if path.is_file():
            if path.suffix.lower() != ".csv":
                continue
            candidates.append(path)
            continue

        if target_date_labels is not None:
            folders_to_scan: list[Path] = []
            if _parse_date_label(path.name) is not None:
                if path.name in target_date_labels:
                    folders_to_scan.append(path)
            else:
                missing_folders: list[str] = []
                for label in sorted(target_date_labels):
                    dated_folder = path / label
                    if dated_folder.is_dir():
                        folders_to_scan.append(dated_folder)
                    else:
                        missing_folders.append(str(dated_folder))
                if missing_folders:
                    raise FileNotFoundError(
                        "Target dated folder(s) not found for backfill: "
                        + ", ".join(missing_folders)
                    )

            for dated_folder in folders_to_scan:
                candidates.extend(
                    _discover_main_csv_in_dated_folder(dated_folder, csv_glob)
                )
            continue

        folder_matches = sorted(path.rglob(csv_glob))
        if folder_matches:
            candidates.extend(folder_matches)
        else:
            candidates.extend(sorted(path.glob("*.csv")))

    main_csv_candidates = [
        candidate for candidate in candidates if not _is_tdfields_chunk_csv(candidate)
    ]
    if target_date_labels is not None:
        main_csv_candidates = [
            candidate
            for candidate in main_csv_candidates
            if _resolve_candidate_date_label(candidate) in target_date_labels
        ]
    return sorted(set(main_csv_candidates))


def _build_csv_input(csv_path: Path) -> BackfillCsvInput:
    folder_label = csv_path.parent.name
    source_date_label = folder_label
    source_date_utc = _parse_date_label(folder_label)

    if source_date_utc is None:
        csv_label = _extract_date_label_from_csv_name(csv_path)
        if csv_label is None:
            raise ValueError(
                "Could not infer source date from CSV path. Expected folder or file "
                f"name to contain dd_mm_yyyy: {csv_path}"
            )
        source_date_label = csv_label
        source_date_utc = _parse_date_label(csv_label)

    if source_date_utc is None:
        raise ValueError(f"Could not parse source date for CSV: {csv_path}")

    return BackfillCsvInput(
        csv_path=csv_path,
        source_date_utc=source_date_utc,
        source_date_label=source_date_label,
        source_folder=csv_path.parent,
    )


def _resolve_duckdb_output_root(output_dir: str | Path | None) -> Path:
    return Path(output_dir) if output_dir is not None else DEFAULT_DUCKDB_RUNS_DIR


def _resolve_manifest_dir(
    output_dir: str | Path | None,
    manifest_dir: str | Path | None,
) -> Path:
    if manifest_dir is not None:
        return Path(manifest_dir)
    if output_dir is not None:
        return Path(output_dir)
    return BACKFILL_OUTPUT_DIR


def _iso_week_key(source_date_utc: datetime) -> tuple[int, int]:
    iso_calendar = source_date_utc.isocalendar()
    return iso_calendar.year, iso_calendar.week


def _build_period_dir(output_root: Path, source_date_utc: datetime) -> Path:
    iso_year, iso_week = _iso_week_key(source_date_utc)
    return output_root / f"iso_year={iso_year}" / f"week={iso_week:02d}"


def _is_week_already_covered(period_dir: Path) -> bool:
    """Return True when a weekly DuckDB partition already has runs persisted."""
    if not period_dir.is_dir():
        return False

    runs_dir = period_dir / "runs"
    if not runs_dir.is_dir() or not any(runs_dir.iterdir()):
        return False

    return any(period_dir.glob("move_prediction_*.duckdb"))


def _build_backfill_run_label_prefix(source_date_label: str) -> str:
    return f"backfill_{_slugify(source_date_label)}"


def _build_backfill_run_label(
    source_date_label: str,
    run_label_prefix: str,
) -> str:
    return (
        f"{run_label_prefix}_" f"{_build_backfill_run_label_prefix(source_date_label)}"
    )


def _find_backfill_run_dirs(period_dir: Path, run_label: str) -> list[Path]:
    runs_dir = period_dir / "runs"
    if not runs_dir.is_dir():
        return []

    slugified_label = _slugify(run_label)
    if not slugified_label:
        return []

    run_dir_prefix = f"{slugified_label}_"
    return [
        run_dir
        for run_dir in runs_dir.iterdir()
        if run_dir.is_dir() and run_dir.name.startswith(run_dir_prefix)
    ]


def _is_backfill_csv_input_already_covered(
    csv_input: BackfillCsvInput,
    output_root: Path,
    run_label_prefix: str,
) -> bool:
    """Return True when a dated CSV day already has a completed backfill run."""
    period_dir = _build_period_dir(output_root, csv_input.source_date_utc)
    run_label = _build_backfill_run_label(
        csv_input.source_date_label,
        run_label_prefix,
    )
    return any(
        (run_dir / "_duckdb_run_overview.log").is_file()
        for run_dir in _find_backfill_run_dirs(period_dir, run_label)
    )


def _discover_pre_existing_covered_date_labels(
    csv_inputs: list[BackfillCsvInput],
    output_root: Path,
    run_label_prefix: str,
) -> set[str]:
    covered_date_labels: set[str] = set()
    for csv_input in csv_inputs:
        if csv_input.source_date_label in covered_date_labels:
            continue
        if _is_backfill_csv_input_already_covered(
            csv_input,
            output_root,
            run_label_prefix,
        ):
            covered_date_labels.add(csv_input.source_date_label)
    return covered_date_labels


def _validate_parallel_settings(
    *,
    max_parallel_workers: int | None,
    max_cpu_percent: float | None,
    max_memory_percent: float | None,
    max_memory_gb: float | None,
    parallel_worker_backend: str,
    resource_cooldown_seconds: float,
    resource_cpu_sample_seconds: float,
    weekly_lock_retry_attempts: int,
    weekly_lock_retry_seconds: float,
) -> None:
    if max_parallel_workers is not None and max_parallel_workers < 1:
        raise ValueError("max_parallel_workers must be >= 1 when provided.")

    for name, value in (
        ("max_cpu_percent", max_cpu_percent),
        ("max_memory_percent", max_memory_percent),
    ):
        if value is None:
            continue
        if value <= 0 or value > 100:
            raise ValueError(f"{name} must be between 0 and 100.")

    if max_memory_gb is not None and max_memory_gb <= 0:
        raise ValueError("max_memory_gb must be > 0 when provided.")

    if parallel_worker_backend not in {"process", "thread"}:
        raise ValueError("parallel_worker_backend must be 'process' or 'thread'.")

    if resource_cooldown_seconds <= 0:
        raise ValueError("resource_cooldown_seconds must be > 0.")
    if resource_cpu_sample_seconds <= 0:
        raise ValueError("resource_cpu_sample_seconds must be > 0.")
    if weekly_lock_retry_attempts < 0:
        raise ValueError("weekly_lock_retry_attempts must be >= 0.")
    if weekly_lock_retry_seconds < 0:
        raise ValueError("weekly_lock_retry_seconds must be >= 0.")


def _resolve_parallel_worker_count(
    week_group_count: int,
    max_parallel_workers: int | None,
) -> int:
    if week_group_count <= 1:
        return 1
    if max_parallel_workers is not None:
        return min(week_group_count, max_parallel_workers)

    cpu_count = os.cpu_count() or DEFAULT_MAX_PARALLEL_WORKERS
    return max(
        1,
        min(week_group_count, min(cpu_count, DEFAULT_MAX_PARALLEL_WORKERS)),
    )


def _resolve_load_worker_cap(max_parallel_workers: int | None) -> int:
    if max_parallel_workers is not None:
        return max_parallel_workers

    cpu_count = os.cpu_count() or DEFAULT_MAX_PARALLEL_WORKERS
    return min(cpu_count, DEFAULT_MAX_PARALLEL_WORKERS)


def _get_process_tree_rss_bytes() -> int | None:
    """Return RSS for the current process plus any child worker processes."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return _get_windows_process_rss_bytes()

    try:
        process = psutil.Process()
        total_rss = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total_rss += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return int(total_rss)
    except Exception:
        return _get_windows_process_rss_bytes()


def _get_windows_process_rss_bytes() -> int | None:
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    psapi = ctypes.windll.psapi
    kernel32 = ctypes.windll.kernel32
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    process_handle = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        process_handle,
        ctypes.byref(counters),
        counters.cb,
    ):
        return None
    return int(counters.WorkingSetSize)


def _build_backfill_memory_budget(
    max_memory_gb: float | None,
) -> BackfillMemoryBudget | None:
    if max_memory_gb is None:
        return None
    return BackfillMemoryBudget(max_gb=max_memory_gb)


def _count_prepared_snapshots(
    prepared_by_week: dict[tuple[int, int], dict[int, BackfillPreparedCsv]],
) -> int:
    return sum(len(bucket) for bucket in prepared_by_week.values())


def _prepare_backfill_csv_worker(
    csv_input: BackfillCsvInput,
    *,
    min_market_cap_usd: float | None,
    max_market_cap_usd: float | None,
) -> BackfillPreparedCsv:
    """Stage 1 worker: load and filter one historical CSV snapshot."""
    started_at = time.perf_counter()
    scan_data_raw = load_tradingview_raw_csv_scan_data(csv_input.csv_path)
    scan_data = _filter_scan_data_by_market_cap(
        scan_data_raw,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
    )
    return BackfillPreparedCsv(
        csv_input=csv_input,
        rows_loaded=len(scan_data_raw),
        scan_data=scan_data,
        load_seconds=time.perf_counter() - started_at,
    )


def _group_csv_inputs_by_iso_week(
    csv_inputs: list[BackfillCsvInput],
) -> list[tuple[tuple[int, int], list[BackfillCsvInput]]]:
    grouped: dict[tuple[int, int], list[BackfillCsvInput]] = defaultdict(list)
    for csv_input in sorted(csv_inputs, key=lambda item: item.source_date_utc):
        grouped[_iso_week_key(csv_input.source_date_utc)].append(csv_input)
    return sorted(grouped.items(), key=lambda item: item[1][0].source_date_utc)


def _is_weekly_lock_error(exc: Exception) -> bool:
    return DUCKDB_WEEKLY_LOCK_ERROR_TEXT in str(exc)


def _run_with_weekly_lock_retry(
    operation,
    *,
    retry_attempts: int,
    retry_seconds: float,
):
    retry_count = 0
    while True:
        try:
            return operation(), retry_count
        except RuntimeError as exc:
            if not _is_weekly_lock_error(exc) or retry_count >= retry_attempts:
                raise
            retry_count += 1
            if retry_seconds > 0:
                time.sleep(retry_seconds)


def _read_system_memory_percent_with_psutil() -> float | None:
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    return float(psutil.virtual_memory().percent)


def _read_windows_memory_percent() -> float | None:
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return float(status.dwMemoryLoad)


def _read_proc_meminfo_memory_percent() -> float | None:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return None

    values_kb: dict[str, int] = {}
    for line in meminfo_path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value_parts = raw_value.strip().split()
        if not value_parts:
            continue
        try:
            values_kb[key] = int(value_parts[0])
        except ValueError:
            continue

    total_kb = values_kb.get("MemTotal")
    available_kb = values_kb.get("MemAvailable")
    if not total_kb or available_kb is None:
        return None
    used_ratio = 1 - (available_kb / total_kb)
    return max(0.0, min(100.0, used_ratio * 100))


def _get_system_memory_percent() -> float | None:
    psutil_percent = _read_system_memory_percent_with_psutil()
    if psutil_percent is not None:
        return psutil_percent

    if os.name == "nt":
        return _read_windows_memory_percent()
    return _read_proc_meminfo_memory_percent()


def _read_system_cpu_percent_with_psutil(sample_seconds: float) -> float | None:
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    return float(psutil.cpu_percent(interval=sample_seconds))


def _read_windows_cpu_percent(sample_seconds: float) -> float | None:
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    def _snapshot() -> tuple[int, int, int] | None:
        idle_time = FILETIME()
        kernel_time = FILETIME()
        user_time = FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None

        def _to_int(filetime: FILETIME) -> int:
            return (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime

        return (
            _to_int(idle_time),
            _to_int(kernel_time),
            _to_int(user_time),
        )

    first = _snapshot()
    if first is None:
        return None
    time.sleep(sample_seconds)
    second = _snapshot()
    if second is None:
        return None

    idle_delta = second[0] - first[0]
    kernel_delta = second[1] - first[1]
    user_delta = second[2] - first[2]
    total_delta = kernel_delta + user_delta
    if total_delta <= 0:
        return None
    busy_ratio = 1 - (idle_delta / total_delta)
    return max(0.0, min(100.0, busy_ratio * 100))


def _read_load_average_cpu_percent() -> float | None:
    if not hasattr(os, "getloadavg"):
        return None
    try:
        load_average, _, _ = os.getloadavg()
    except OSError:
        return None
    cpu_count = os.cpu_count() or 1
    return max(0.0, min(100.0, (load_average / cpu_count) * 100))


def _get_system_cpu_percent(sample_seconds: float) -> float | None:
    psutil_percent = _read_system_cpu_percent_with_psutil(sample_seconds)
    if psutil_percent is not None:
        return psutil_percent

    if os.name == "nt":
        return _read_windows_cpu_percent(sample_seconds)
    return _read_load_average_cpu_percent()


def _wait_for_resource_capacity(
    throttle: BackfillResourceThrottle | None,
) -> dict[str, float | int | None]:
    wait_seconds = 0.0
    wait_cycles = 0
    last_cpu_percent: float | None = None
    last_memory_percent: float | None = None

    while throttle is not None:
        cpu_over_threshold = False
        if throttle.max_cpu_percent is not None:
            last_cpu_percent = _get_system_cpu_percent(throttle.cpu_sample_seconds)
            cpu_over_threshold = (
                last_cpu_percent is not None
                and last_cpu_percent >= throttle.max_cpu_percent
            )

        if throttle.max_memory_percent is not None:
            last_memory_percent = _get_system_memory_percent()
            memory_over_threshold = (
                last_memory_percent is not None
                and last_memory_percent >= throttle.max_memory_percent
            )
        else:
            memory_over_threshold = False

        if not cpu_over_threshold and not memory_over_threshold:
            break

        wait_cycles += 1
        time.sleep(throttle.cooldown_seconds)
        wait_seconds += throttle.cooldown_seconds

    return {
        "wait_seconds": wait_seconds,
        "wait_cycles": wait_cycles,
        "last_cpu_percent": last_cpu_percent,
        "last_memory_percent": last_memory_percent,
    }


def _format_execution_seconds(seconds: float | None) -> str:
    if seconds is None:
        return ""
    return f"{seconds:.3f}"


def _build_manifest_row(
    *,
    source_date_label: str,
    source_date_utc: datetime | None,
    iso_year: int | str,
    iso_week: int | str,
    status: str,
    skip_reason: str,
    source_folder: str | Path,
    source_csv: str | Path,
    rows_loaded: str | int = "",
    rows_used: str | int = "",
    duckdb_run_id: str = "",
    duckdb_period_dir: str | Path = "",
    duckdb_database: str | Path = "",
    duckdb_output_root: str | Path,
    execution_seconds: str | float | None = "",
) -> list[str]:
    return [
        source_date_label,
        source_date_utc.strftime("%Y-%m-%d") if source_date_utc else "",
        str(iso_year),
        str(iso_week),
        status,
        skip_reason,
        str(source_folder),
        str(source_csv),
        str(rows_loaded),
        str(rows_used),
        str(duckdb_run_id),
        str(duckdb_period_dir),
        str(duckdb_database),
        str(duckdb_output_root),
        (
            _format_execution_seconds(execution_seconds)
            if isinstance(execution_seconds, (int, float))
            else str(execution_seconds)
        ),
    ]


def _manifest_row_sort_key(label: str) -> datetime:
    if label == BACKFILL_SUMMARY_LABEL:
        return datetime.max.replace(tzinfo=timezone.utc)
    return datetime.strptime(label, BACKFILL_DATE_FORMAT).replace(tzinfo=timezone.utc)


def _register_graceful_interrupt_handler(
    interrupt_state: BackfillInterruptState,
    enabled: bool,
) -> Callable[[], None]:
    if not enabled:
        return lambda: None

    previous_handlers: dict[int, Any] = {}

    def _handler(signum: int, frame: Any) -> None:
        interrupt_state.requested = True
        interrupt_state.requested_at_utc = datetime.now(tz=timezone.utc)

    previous_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        previous_handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, _handler)

    def _restore_handlers() -> None:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    return _restore_handlers


def _check_interrupt_requested(interrupt_state: BackfillInterruptState) -> None:
    if interrupt_state.requested:
        raise BackfillInterruptRequested()


def _build_skip_artifacts(
    csv_input: BackfillCsvInput,
    *,
    iso_year: int,
    iso_week: int,
    period_dir: Path,
    duckdb_output_root: Path,
) -> tuple[dict[str, Any], list[str]]:
    skip_record = {
        "source_csv": csv_input.csv_path,
        "source_date_label": csv_input.source_date_label,
        "source_date_utc": csv_input.source_date_utc,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "duckdb_period_dir": period_dir,
        "skip_reason": BACKFILL_RUN_ALREADY_COVERED_SKIP_REASON,
        "execution_seconds": 0.0,
    }
    manifest_row = _build_manifest_row(
        source_date_label=csv_input.source_date_label,
        source_date_utc=csv_input.source_date_utc,
        iso_year=iso_year,
        iso_week=iso_week,
        status="skipped",
        skip_reason=BACKFILL_RUN_ALREADY_COVERED_SKIP_REASON,
        source_folder=csv_input.source_folder,
        source_csv=csv_input.csv_path,
        duckdb_period_dir=period_dir,
        duckdb_output_root=duckdb_output_root,
        execution_seconds=0.0,
    )
    return skip_record, manifest_row


def _build_interrupt_artifacts(
    csv_input: BackfillCsvInput,
    *,
    duckdb_output_root: Path,
) -> tuple[dict[str, Any], list[str]]:
    iso_year, iso_week = _iso_week_key(csv_input.source_date_utc)
    period_dir = _build_period_dir(duckdb_output_root, csv_input.source_date_utc)
    interrupt_record = {
        "source_csv": csv_input.csv_path,
        "source_date_label": csv_input.source_date_label,
        "source_date_utc": csv_input.source_date_utc,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "duckdb_period_dir": period_dir,
        "skip_reason": BACKFILL_INTERRUPT_SKIP_REASON,
        "execution_seconds": None,
    }
    manifest_row = _build_manifest_row(
        source_date_label=csv_input.source_date_label,
        source_date_utc=csv_input.source_date_utc,
        iso_year=iso_year,
        iso_week=iso_week,
        status="interrupted",
        skip_reason=BACKFILL_INTERRUPT_SKIP_REASON,
        source_folder=csv_input.source_folder,
        source_csv=csv_input.csv_path,
        duckdb_period_dir=period_dir,
        duckdb_output_root=duckdb_output_root,
        execution_seconds="",
    )
    return interrupt_record, manifest_row


def _collect_unprocessed_interrupt_artifacts(
    planned_csv_inputs: list[BackfillCsvInput],
    completed_date_labels: set[str],
    duckdb_output_root: Path,
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    interrupted_records: list[dict[str, Any]] = []
    manifest_rows: list[list[str]] = []

    for csv_input in sorted(planned_csv_inputs, key=lambda item: item.source_date_utc):
        if csv_input.source_date_label in completed_date_labels:
            continue
        interrupt_record, manifest_row = _build_interrupt_artifacts(
            csv_input,
            duckdb_output_root=duckdb_output_root,
        )
        interrupted_records.append(interrupt_record)
        manifest_rows.append(manifest_row)

    return interrupted_records, manifest_rows


def _build_summary_manifest_row(
    *,
    run_outcome: str,
    total_execution_seconds: float,
    candidate_count: int,
    processed_count: int,
    skipped_count: int,
    interrupted_count: int,
    duckdb_output_root: Path,
) -> list[str]:
    return _build_manifest_row(
        source_date_label=BACKFILL_SUMMARY_LABEL,
        source_date_utc=None,
        iso_year="",
        iso_week="",
        status=run_outcome,
        skip_reason="",
        source_folder="",
        source_csv="",
        rows_loaded=candidate_count,
        rows_used=processed_count,
        duckdb_run_id=str(skipped_count),
        duckdb_period_dir=str(interrupted_count),
        duckdb_database="",
        duckdb_output_root=duckdb_output_root,
        execution_seconds=total_execution_seconds,
    )


def _prepare_backfill_csv_input(
    csv_input: BackfillCsvInput,
    execution_config: BackfillExecutionConfig,
) -> BackfillPreparedCsv:
    started_at = time.perf_counter()
    scan_data_raw = load_tradingview_raw_csv_scan_data(csv_input.csv_path)
    scan_data = _filter_scan_data_by_market_cap(
        scan_data_raw,
        min_market_cap_usd=execution_config.min_market_cap_usd,
        max_market_cap_usd=execution_config.max_market_cap_usd,
    )
    return BackfillPreparedCsv(
        csv_input=csv_input,
        rows_loaded=len(scan_data_raw),
        scan_data=scan_data,
        load_seconds=time.perf_counter() - started_at,
    )


def _execute_backfill_duckdb_write(
    prepared: BackfillPreparedCsv,
    execution_config: BackfillExecutionConfig,
) -> tuple[dict[str, Any], list[str]]:
    _check_interrupt_requested(execution_config.interrupt_state)
    write_started_at = time.perf_counter()
    csv_input = prepared.csv_input
    iso_year, iso_week = _iso_week_key(csv_input.source_date_utc)
    resource_wait = _wait_for_resource_capacity(execution_config.resource_throttle)

    run_label = _build_backfill_run_label(
        csv_input.source_date_label,
        execution_config.run_label_prefix,
    )
    metadata = {
        "scan_input_type": "historical_all_fields_csv_backfill",
        "source_csv": str(csv_input.csv_path),
        "source_folder": str(csv_input.source_folder),
        "source_date_label": csv_input.source_date_label,
        "source_date_utc": csv_input.source_date_utc.isoformat(),
    }

    base_result, base_lock_retries = _run_with_weekly_lock_retry(
        lambda: run_full_analysis_suite_duckdb(
            scan_data=prepared.scan_data,
            profile_names=(
                list(execution_config.profile_names)
                if execution_config.profile_names is not None
                else None
            ),
            min_market_cap_usd=execution_config.min_market_cap_usd,
            max_market_cap_usd=execution_config.max_market_cap_usd,
            include_blind_spot_sections=execution_config.include_blind_spot_sections,
            output_dir=execution_config.duckdb_output_root,
            run_label=run_label,
            reference_time=csv_input.source_date_utc,
            api_request_metadata=metadata,
            export_parquet=execution_config.export_parquet,
            create_indexes=execution_config.create_indexes,
        ),
        retry_attempts=execution_config.weekly_lock_retry_attempts,
        retry_seconds=execution_config.weekly_lock_retry_seconds,
    )

    lock_retry_count = base_lock_retries
    if execution_config.include_earnings_priority:
        combined_result, earnings_lock_retries = _run_with_weekly_lock_retry(
            lambda: run_full_analysis_suite_with_earnings_priority_duckdb(
                scan_data=prepared.scan_data,
                profile_names=(
                    list(execution_config.profile_names)
                    if execution_config.profile_names is not None
                    else None
                ),
                min_market_cap_usd=execution_config.min_market_cap_usd,
                max_market_cap_usd=execution_config.max_market_cap_usd,
                include_blind_spot_sections=execution_config.include_blind_spot_sections,
                output_dir=execution_config.duckdb_output_root,
                run_label=run_label,
                reference_time=csv_input.source_date_utc,
                api_request_metadata=metadata,
                export_parquet=execution_config.export_parquet,
                create_indexes=execution_config.create_indexes,
                base_result=base_result,
            ),
            retry_attempts=execution_config.weekly_lock_retry_attempts,
            retry_seconds=execution_config.weekly_lock_retry_seconds,
        )
        lock_retry_count += earnings_lock_retries
    else:
        combined_result = base_result

    write_seconds = time.perf_counter() - write_started_at
    execution_seconds = prepared.load_seconds + write_seconds

    run_record = {
        "source_csv": csv_input.csv_path,
        "source_date_label": csv_input.source_date_label,
        "source_date_utc": csv_input.source_date_utc,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "rows_loaded": prepared.rows_loaded,
        "rows_used": len(prepared.scan_data),
        "duckdb_run_id": combined_result.get("_duckdb_run_id"),
        "duckdb_period_dir": combined_result.get("_duckdb_period_dir"),
        "duckdb_database": combined_result.get("_duckdb_database"),
        "duckdb_run_output_dir": combined_result.get("_duckdb_run_output_dir"),
        "duckdb_overview_log": combined_result.get("_duckdb_overview_log"),
        "load_seconds": prepared.load_seconds,
        "write_seconds": write_seconds,
        "resource_wait_seconds": float(resource_wait["wait_seconds"] or 0.0),
        "resource_wait_cycles": int(resource_wait["wait_cycles"] or 0),
        "resource_last_cpu_percent": resource_wait["last_cpu_percent"],
        "resource_last_memory_percent": resource_wait["last_memory_percent"],
        "lock_retry_count": lock_retry_count,
        "execution_seconds": execution_seconds,
    }
    manifest_row = _build_manifest_row(
        source_date_label=csv_input.source_date_label,
        source_date_utc=csv_input.source_date_utc,
        iso_year=iso_year,
        iso_week=iso_week,
        status="processed",
        skip_reason="",
        source_folder=csv_input.source_folder,
        source_csv=csv_input.csv_path,
        rows_loaded=prepared.rows_loaded,
        rows_used=len(prepared.scan_data),
        duckdb_run_id=str(run_record["duckdb_run_id"] or ""),
        duckdb_period_dir=str(run_record["duckdb_period_dir"] or ""),
        duckdb_database=str(run_record["duckdb_database"] or ""),
        duckdb_output_root=execution_config.duckdb_output_root,
        execution_seconds=execution_seconds,
    )
    return run_record, manifest_row


def _process_backfill_csv_input(
    csv_input: BackfillCsvInput,
    execution_config: BackfillExecutionConfig,
) -> tuple[dict[str, Any], list[str]]:
    _check_interrupt_requested(execution_config.interrupt_state)
    prepared = _prepare_backfill_csv_input(csv_input, execution_config)
    return _execute_backfill_duckdb_write(prepared, execution_config)


def _process_backfill_week_group(
    week_key: tuple[int, int],
    csv_inputs: list[BackfillCsvInput],
    execution_config: BackfillExecutionConfig,
    memory_budget: BackfillMemoryBudget | None = None,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    manifest_rows: list[list[str]] = []

    for csv_input in sorted(csv_inputs, key=lambda item: item.source_date_utc):
        try:
            _check_interrupt_requested(execution_config.interrupt_state)
            if memory_budget is not None:
                memory_budget.wait_until_under_budget()
            run_record, manifest_row = _process_backfill_csv_input(
                csv_input,
                execution_config,
            )
        except BackfillInterruptRequested:
            break
        runs.append(run_record)
        manifest_rows.append(manifest_row)
        gc.collect()
        if memory_budget is not None:
            memory_budget.record_sample()

    return {
        "week_key": week_key,
        "runs": runs,
        "manifest_rows": manifest_rows,
    }


def _run_backfill_week_groups_serial(
    pending_week_groups: list[tuple[tuple[int, int], list[BackfillCsvInput]]],
    execution_config: BackfillExecutionConfig,
    memory_budget: BackfillMemoryBudget | None = None,
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    runs: list[dict[str, Any]] = []
    manifest_rows: list[list[str]] = []

    for week_key, week_csv_inputs in pending_week_groups:
        if execution_config.interrupt_state.requested:
            break
        week_result = _process_backfill_week_group(
            week_key,
            week_csv_inputs,
            execution_config,
            memory_budget=memory_budget,
        )
        runs.extend(week_result["runs"])
        manifest_rows.extend(week_result["manifest_rows"])
        if execution_config.interrupt_state.requested:
            break

    return runs, manifest_rows


def _run_backfill_two_stage_pipeline(
    pending_week_groups: list[tuple[tuple[int, int], list[BackfillCsvInput]]],
    execution_config: BackfillExecutionConfig,
    *,
    max_load_workers: int,
    max_write_workers: int,
    memory_budget: BackfillMemoryBudget | None,
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """Load/filter CSVs, then write each ISO week serially with live RAM backpressure.

    When ``memory_budget`` is set, the pipeline keeps at most one prepared CSV
    snapshot and one active DuckDB write in memory, polling process-tree RSS
    before scheduling more work.
    """
    runs: list[dict[str, Any]] = []
    manifest_rows: list[list[str]] = []
    if not pending_week_groups:
        return runs, manifest_rows

    flat_jobs: list[tuple[tuple[int, int], int, BackfillCsvInput]] = []
    week_sizes: dict[tuple[int, int], int] = {}
    for week_key, week_csv_inputs in pending_week_groups:
        ordered_inputs = sorted(
            week_csv_inputs,
            key=lambda item: item.source_date_utc,
        )
        week_sizes[week_key] = len(ordered_inputs)
        for index, csv_input in enumerate(ordered_inputs):
            flat_jobs.append((week_key, index, csv_input))

    effective_load_workers = max_load_workers
    effective_write_workers = max_write_workers
    if memory_budget is not None:
        effective_load_workers = min(
            max_load_workers,
            memory_budget.max_inflight_loads,
        )
        effective_write_workers = min(
            max_write_workers,
            memory_budget.max_active_writes,
        )

    prepared_by_week: dict[tuple[int, int], dict[int, BackfillPreparedCsv]] = (
        defaultdict(dict)
    )
    next_write_index: dict[tuple[int, int], int] = defaultdict(int)
    pending_job_ids: deque[int] = deque(range(len(flat_jobs)))
    jobs_by_id = {job_id: flat_jobs[job_id] for job_id in range(len(flat_jobs))}
    active_loads: dict[Any, int] = {}
    active_writes: set[tuple[int, int]] = set()
    results_lock = threading.Lock()
    scheduling_lock = threading.Lock()

    mp_context = multiprocessing.get_context("spawn")
    load_executor = ProcessPoolExecutor(
        max_workers=effective_load_workers,
        mp_context=mp_context,
    )
    write_executor = ThreadPoolExecutor(max_workers=effective_write_workers)

    def _record_result(
        run_record: dict[str, Any],
        manifest_row: list[str],
    ) -> None:
        with results_lock:
            runs.append(run_record)
            manifest_rows.append(manifest_row)

    def _wait_for_memory_capacity() -> None:
        if memory_budget is None:
            return
        memory_budget.wait_until_under_budget()
        memory_budget.record_sample()

    def _submit_load_jobs() -> None:
        while pending_job_ids and not execution_config.interrupt_state.requested:
            with scheduling_lock:
                prepared_waiting = _count_prepared_snapshots(prepared_by_week)
                can_schedule = True
                if memory_budget is not None:
                    can_schedule = memory_budget.can_schedule_load(
                        active_loads=len(active_loads),
                        prepared_waiting=prepared_waiting,
                        active_writes=len(active_writes),
                    )
                elif len(active_loads) >= effective_load_workers:
                    can_schedule = False
                if not can_schedule:
                    break

            _wait_for_memory_capacity()

            with scheduling_lock:
                prepared_waiting = _count_prepared_snapshots(prepared_by_week)
                if memory_budget is not None:
                    if not memory_budget.can_schedule_load(
                        active_loads=len(active_loads),
                        prepared_waiting=prepared_waiting,
                        active_writes=len(active_writes),
                    ):
                        break
                elif len(active_loads) >= effective_load_workers:
                    break

                job_id = pending_job_ids.popleft()
                _, _, csv_input = jobs_by_id[job_id]
                future = load_executor.submit(
                    _prepare_backfill_csv_worker,
                    csv_input,
                    min_market_cap_usd=execution_config.min_market_cap_usd,
                    max_market_cap_usd=execution_config.max_market_cap_usd,
                )
                active_loads[future] = job_id
                if memory_budget is not None:
                    memory_budget.record_sample()

    def _schedule_writes() -> None:
        with scheduling_lock:
            if execution_config.interrupt_state.requested:
                return

            for week_key in sorted(week_sizes):
                if len(active_writes) >= effective_write_workers:
                    break
                if week_key in active_writes:
                    continue

                write_index = next_write_index[week_key]
                if write_index >= week_sizes[week_key]:
                    continue
                prepared = prepared_by_week[week_key].pop(write_index, None)
                if prepared is None:
                    continue

                if memory_budget is not None and not memory_budget.can_schedule_write(
                    active_writes=len(active_writes),
                ):
                    prepared_by_week[week_key][write_index] = prepared
                    break

                next_write_index[week_key] = write_index + 1
                active_writes.add(week_key)
                should_wait_for_memory = memory_budget is not None

                def _write_prepared_csv(
                    week_key=week_key,
                    prepared=prepared,
                    should_wait_for_memory=should_wait_for_memory,
                ) -> None:
                    try:
                        if should_wait_for_memory and memory_budget is not None:
                            memory_budget.wait_until_under_budget()
                        run_record, manifest_row = _execute_backfill_duckdb_write(
                            prepared,
                            execution_config,
                        )
                    except BackfillInterruptRequested:
                        execution_config.interrupt_state.requested = True
                    else:
                        _record_result(run_record, manifest_row)
                    finally:
                        del prepared
                        gc.collect()
                        if memory_budget is not None:
                            memory_budget.record_sample()
                        with scheduling_lock:
                            active_writes.discard(week_key)
                        _schedule_writes()
                        _submit_load_jobs()

                write_executor.submit(_write_prepared_csv)

    try:
        _submit_load_jobs()
        while active_loads or active_writes or pending_job_ids:
            if execution_config.interrupt_state.requested and not active_writes:
                break

            if memory_budget is not None:
                memory_budget.record_sample()

            if not active_loads:
                if active_writes:
                    time.sleep(memory_budget.cooldown_seconds if memory_budget else 0.1)
                    _schedule_writes()
                    _submit_load_jobs()
                    continue
                if pending_job_ids:
                    _submit_load_jobs()
                    if not active_loads and pending_job_ids:
                        time.sleep(
                            memory_budget.cooldown_seconds if memory_budget else 0.1
                        )
                    continue
                break

            with scheduling_lock:
                loads_to_wait = dict(active_loads)
            done, _ = wait(loads_to_wait, return_when=FIRST_COMPLETED)
            with scheduling_lock:
                for future in done:
                    job_id = active_loads.pop(future, None)
                    if job_id is None:
                        continue
                    if execution_config.interrupt_state.requested:
                        continue
                    try:
                        prepared = future.result()
                    except Exception:
                        load_executor.shutdown(wait=False, cancel_futures=True)
                        write_executor.shutdown(wait=False, cancel_futures=True)
                        raise

                    week_key, index_in_week, _ = jobs_by_id[job_id]
                    prepared_by_week[week_key][index_in_week] = prepared
                    if memory_budget is not None:
                        memory_budget.record_sample()

            _schedule_writes()
            _submit_load_jobs()

        while active_writes and not execution_config.interrupt_state.requested:
            time.sleep(memory_budget.cooldown_seconds if memory_budget else 0.1)
            if memory_budget is not None:
                memory_budget.record_sample()
            _schedule_writes()
    finally:
        if execution_config.interrupt_state.requested:
            for future in list(active_loads):
                future.cancel()
        load_executor.shutdown(wait=not execution_config.interrupt_state.requested)
        write_executor.shutdown(wait=True)
        prepared_by_week.clear()
        gc.collect()
        if memory_budget is not None:
            memory_budget.record_sample()

    return runs, manifest_rows


def _run_backfill_week_groups_parallel(
    pending_week_groups: list[tuple[tuple[int, int], list[BackfillCsvInput]]],
    execution_config: BackfillExecutionConfig,
    max_workers: int,
    memory_budget: BackfillMemoryBudget | None = None,
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    runs: list[dict[str, Any]] = []
    manifest_rows: list[list[str]] = []
    week_iter = iter(pending_week_groups)
    active_futures: dict[Any, tuple[int, int]] = {}
    effective_workers = max_workers
    if memory_budget is not None:
        effective_workers = min(max_workers, memory_budget.max_active_writes)

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        while True:
            while (
                len(active_futures) < effective_workers
                and not execution_config.interrupt_state.requested
            ):
                try:
                    week_key, week_csv_inputs = next(week_iter)
                except StopIteration:
                    break
                if memory_budget is not None:
                    memory_budget.wait_until_under_budget()
                future = executor.submit(
                    _process_backfill_week_group,
                    week_key,
                    week_csv_inputs,
                    execution_config,
                    memory_budget,
                )
                active_futures[future] = week_key

            if not active_futures:
                break

            done, _ = wait(active_futures, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    week_result = future.result()
                except BackfillInterruptRequested:
                    execution_config.interrupt_state.requested = True
                else:
                    runs.extend(week_result["runs"])
                    manifest_rows.extend(week_result["manifest_rows"])
                active_futures.pop(future, None)

            if execution_config.interrupt_state.requested:
                for future in list(active_futures):
                    future.cancel()
                break

    return runs, manifest_rows


def _write_backfill_manifest(
    manifest_csv: Path,
    manifest_rows: list[list[str]],
) -> None:
    manifest_csv.parent.mkdir(parents=True, exist_ok=True)
    merged_manifest_rows = _merge_manifest_rows(
        _load_manifest_rows(manifest_csv),
        manifest_rows,
    )
    log_rows_to_csv(
        manifest_csv,
        BACKFILL_MANIFEST_HEADERS,
        merged_manifest_rows,
    )


def _load_manifest_rows(manifest_csv: Path) -> list[dict[str, str]]:
    if not manifest_csv.exists():
        return []

    with manifest_csv.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        return [dict(row) for row in reader]


def _merge_manifest_rows(
    existing_rows: list[dict[str, str]],
    new_rows: list[list[str]],
) -> list[list[str]]:
    merged_by_date: dict[str, list[str]] = {}
    header_index = {
        header: index for index, header in enumerate(BACKFILL_MANIFEST_HEADERS)
    }

    for row in existing_rows:
        date_label = row.get("source_date_label", "")
        if not date_label:
            continue
        merged_by_date[date_label] = [
            row.get(header, "") for header in BACKFILL_MANIFEST_HEADERS
        ]

    for row in new_rows:
        date_label = row[header_index["source_date_label"]]
        merged_by_date[date_label] = row

    return [
        merged_by_date[date_label]
        for date_label in sorted(
            merged_by_date,
            key=_manifest_row_sort_key,
        )
    ]


def replay_historical_raw_csvs_into_duckdb_runs(
    raw_data_folders_or_csvs: list[str | Path] | None = None,
    target_date_labels: list[str] | None = None,
    profile_names: list[str] | None = None,
    min_market_cap_usd: float | None = None,
    max_market_cap_usd: float | None = None,
    include_blind_spot_sections: bool = False,
    include_earnings_priority: bool = True,
    output_dir: str | Path | None = None,
    manifest_dir: str | Path | None = None,
    csv_glob: str = BACKSCAN_RAW_CSV_GLOB,
    export_parquet: bool = True,
    create_indexes: bool = False,
    run_label_prefix: str = "raw_csv_duckdb_backfill",
    skip_existing_coverage: bool | None = None,
    force_rerun: bool = False,
    parallel_mode: bool = False,
    max_parallel_workers: int | None = None,
    parallel_worker_backend: Literal[
        "process", "thread"
    ] = DEFAULT_PARALLEL_WORKER_BACKEND,
    max_cpu_percent: float | None = DEFAULT_MAX_CPU_PERCENT,
    max_memory_percent: float | None = DEFAULT_MAX_MEMORY_PERCENT,
    max_memory_gb: float | None = DEFAULT_MAX_MEMORY_GB,
    resource_cooldown_seconds: float = DEFAULT_RESOURCE_COOLDOWN_SECONDS,
    resource_cpu_sample_seconds: float = DEFAULT_RESOURCE_CPU_SAMPLE_SECONDS,
    weekly_lock_retry_attempts: int = DEFAULT_WEEKLY_LOCK_RETRY_ATTEMPTS,
    weekly_lock_retry_seconds: float = DEFAULT_WEEKLY_LOCK_RETRY_SECONDS,
    graceful_shutdown_on_interrupt: bool = True,
) -> dict[str, Any]:
    """Replay old all-fields CSV snapshots into weekly DuckDB move-prediction runs.

    The function scans dated folders (or direct CSV paths), infers each CSV date,
    then runs the same DuckDB suites used by live flow with ``reference_time`` set
    to that source day, so outputs land in historical ``iso_year/week`` folders.

    By default, DuckDB outputs are written under
    ``prediction_analysis/duckdb_runs``. When ``output_dir`` is provided, runs are
    written to that custom root instead.

    On reruns against the default output root, dated CSV days that already have
    a completed backfill run under their ``iso_year=YYYY/week=WW`` partition are
    skipped so historical CSV days are not converted twice. Other days in the
    same ISO week are still processed. Set ``force_rerun=True`` to override
    this, or point ``output_dir`` to a fresh destination to replay everything
    there.

    ``parallel_mode=True`` uses a two-stage pipeline by default:

    1. Stage 1 loads and filters CSV snapshots in parallel process workers.
    2. Stage 2 writes DuckDB outputs serially within each ISO week while
       allowing different weeks to write concurrently.

    Set ``parallel_worker_backend='thread'`` to fall back to the older
    thread-per-week model. Optional CPU and memory-percent thresholds can
    pause workers before each CSV replay when the machine is already busy.

    ``max_memory_gb`` applies a live RAM cap using process-tree RSS sampling.
    When set, the backfill keeps at most one prepared CSV snapshot and one
    active DuckDB write in memory and waits until RSS drops before starting
    more work. Install ``psutil`` for the most reliable measurement on Windows.

    ``target_date_labels`` limits a run to specific dated folders such as
    ``["13_04_2026", "14_04_2026", "15_04_2026"]``. When a container root is
    passed in ``raw_data_folders_or_csvs``, only those ``dd_mm_yyyy`` subfolders
    are scanned. Main CSV detection and ISO-week aggregation behavior stay the
    same for the selected days.

    Execution timing is recorded per CSV in the manifest ``execution_seconds``
    column. A final ``_backfill_run_summary`` row records total wall-clock time
    and run counts. When ``graceful_shutdown_on_interrupt=True``, Ctrl+C / SIGINT
    stops scheduling new work, writes a partial manifest, and marks unfinished
    CSV days with status ``interrupted``.
    """
    if raw_data_folders_or_csvs is None:
        raw_data_folders_or_csvs = [RAW_MARKET_DATA_DIR]

    normalized_target_date_labels = _normalize_target_date_labels(target_date_labels)

    _validate_parallel_settings(
        max_parallel_workers=max_parallel_workers,
        max_cpu_percent=max_cpu_percent,
        max_memory_percent=max_memory_percent,
        max_memory_gb=max_memory_gb,
        parallel_worker_backend=parallel_worker_backend,
        resource_cooldown_seconds=resource_cooldown_seconds,
        resource_cpu_sample_seconds=resource_cpu_sample_seconds,
        weekly_lock_retry_attempts=weekly_lock_retry_attempts,
        weekly_lock_retry_seconds=weekly_lock_retry_seconds,
    )

    if skip_existing_coverage is None:
        skip_existing_coverage = output_dir is None

    duckdb_output_root = _resolve_duckdb_output_root(output_dir)
    manifest_parent = _resolve_manifest_dir(output_dir, manifest_dir)
    should_skip_existing = skip_existing_coverage and not force_rerun

    csv_paths = _discover_candidate_csvs(
        raw_data_folders_or_csvs,
        csv_glob=csv_glob,
        target_date_labels=normalized_target_date_labels,
    )
    if not csv_paths:
        if normalized_target_date_labels:
            requested_labels = ", ".join(sorted(normalized_target_date_labels))
            raise FileNotFoundError(
                "No candidate historical CSV files were found for the requested "
                f"target_date_labels: {requested_labels}"
            )
        raise FileNotFoundError(
            "No candidate historical CSV files were found for backfill inputs."
        )

    csv_inputs = [_build_csv_input(csv_path) for csv_path in csv_paths]
    csv_inputs.sort(key=lambda item: item.source_date_utc)

    pre_existing_covered_date_labels = (
        _discover_pre_existing_covered_date_labels(
            csv_inputs,
            duckdb_output_root,
            run_label_prefix,
        )
        if should_skip_existing
        else set()
    )

    run_started_at = time.perf_counter()
    interrupt_state = BackfillInterruptState()
    restore_interrupt_handlers = _register_graceful_interrupt_handler(
        interrupt_state,
        graceful_shutdown_on_interrupt,
    )
    manifest_csv = manifest_parent / BACKFILL_MANIFEST_NAME
    run_outcome = "completed"

    result: dict[str, Any] = {
        "runs": [],
        "skipped": [],
        "interrupted": [],
        "duckdb_output_root": duckdb_output_root,
        "skip_existing_coverage": should_skip_existing,
        "parallel_mode": False,
        "pipeline_mode": "serial",
        "parallel_worker_backend": parallel_worker_backend,
        "max_memory_gb": max_memory_gb,
        "target_date_labels": sorted(normalized_target_date_labels or []),
        "graceful_shutdown_on_interrupt": graceful_shutdown_on_interrupt,
        "run_outcome": run_outcome,
        "manifest_csv": manifest_csv,
    }
    manifest_rows: list[list[str]] = []
    pending_week_groups: list[tuple[tuple[int, int], list[BackfillCsvInput]]] = []
    planned_csv_inputs: list[BackfillCsvInput] = []

    for week_key, week_csv_inputs in _group_csv_inputs_by_iso_week(csv_inputs):
        week_csv_inputs_to_process: list[BackfillCsvInput] = []
        for csv_input in week_csv_inputs:
            if (
                should_skip_existing
                and csv_input.source_date_label in pre_existing_covered_date_labels
            ):
                iso_year, iso_week = _iso_week_key(csv_input.source_date_utc)
                period_dir = _build_period_dir(
                    duckdb_output_root,
                    csv_input.source_date_utc,
                )
                skip_record, manifest_row = _build_skip_artifacts(
                    csv_input,
                    iso_year=iso_year,
                    iso_week=iso_week,
                    period_dir=period_dir,
                    duckdb_output_root=duckdb_output_root,
                )
                result["skipped"].append(skip_record)
                manifest_rows.append(manifest_row)
                continue

            week_csv_inputs_to_process.append(csv_input)

        if not week_csv_inputs_to_process:
            continue

        pending_week_groups.append((week_key, week_csv_inputs_to_process))
        planned_csv_inputs.extend(week_csv_inputs_to_process)

    execution_config = BackfillExecutionConfig(
        profile_names=tuple(profile_names) if profile_names is not None else None,
        min_market_cap_usd=min_market_cap_usd,
        max_market_cap_usd=max_market_cap_usd,
        include_blind_spot_sections=include_blind_spot_sections,
        include_earnings_priority=include_earnings_priority,
        duckdb_output_root=duckdb_output_root,
        export_parquet=export_parquet,
        create_indexes=create_indexes,
        run_label_prefix=run_label_prefix,
        weekly_lock_retry_attempts=weekly_lock_retry_attempts,
        weekly_lock_retry_seconds=weekly_lock_retry_seconds,
        resource_throttle=(
            BackfillResourceThrottle(
                max_cpu_percent=max_cpu_percent,
                max_memory_percent=max_memory_percent,
                cooldown_seconds=resource_cooldown_seconds,
                cpu_sample_seconds=resource_cpu_sample_seconds,
            )
            if parallel_mode
            else None
        ),
        interrupt_state=interrupt_state,
    )

    memory_budget = _build_backfill_memory_budget(max_memory_gb)
    resolved_write_workers = _resolve_parallel_worker_count(
        len(pending_week_groups),
        max_parallel_workers,
    )
    resolved_load_worker_cap = _resolve_load_worker_cap(max_parallel_workers)
    should_run_parallel = parallel_mode and bool(pending_week_groups)

    try:
        if should_run_parallel:
            result["parallel_mode"] = True
            if parallel_worker_backend == "process":
                result["pipeline_mode"] = "two_stage"
                processed_runs, processed_manifest_rows = (
                    _run_backfill_two_stage_pipeline(
                        pending_week_groups,
                        execution_config,
                        max_load_workers=resolved_load_worker_cap,
                        max_write_workers=resolved_write_workers,
                        memory_budget=memory_budget,
                    )
                )
            else:
                result["pipeline_mode"] = "thread_per_week"
                processed_runs, processed_manifest_rows = (
                    _run_backfill_week_groups_parallel(
                        pending_week_groups,
                        execution_config,
                        resolved_write_workers,
                        memory_budget=memory_budget,
                    )
                )
        else:
            processed_runs, processed_manifest_rows = _run_backfill_week_groups_serial(
                pending_week_groups,
                execution_config,
                memory_budget=memory_budget,
            )
        result["runs"].extend(processed_runs)
        manifest_rows.extend(processed_manifest_rows)
        if interrupt_state.requested:
            run_outcome = "interrupted"
    except BackfillInterruptRequested:
        run_outcome = "interrupted"
    except KeyboardInterrupt:
        interrupt_state.requested = True
        interrupt_state.requested_at_utc = datetime.now(tz=timezone.utc)
        run_outcome = "interrupted"
    finally:
        completed_date_labels = {run["source_date_label"] for run in result["runs"]}
        interrupted_records, interrupted_manifest_rows = (
            _collect_unprocessed_interrupt_artifacts(
                planned_csv_inputs,
                completed_date_labels,
                duckdb_output_root,
            )
            if interrupt_state.requested
            else ([], [])
        )
        if interrupt_state.requested:
            result["interrupted"].extend(interrupted_records)
            manifest_rows.extend(interrupted_manifest_rows)

        result["runs"].sort(key=lambda item: item["source_date_utc"])
        result["skipped"].sort(key=lambda item: item["source_date_utc"])
        result["interrupted"].sort(key=lambda item: item["source_date_utc"])

        total_execution_seconds = time.perf_counter() - run_started_at
        manifest_rows.append(
            _build_summary_manifest_row(
                run_outcome=run_outcome,
                total_execution_seconds=total_execution_seconds,
                candidate_count=len(csv_inputs),
                processed_count=len(result["runs"]),
                skipped_count=len(result["skipped"]),
                interrupted_count=len(result["interrupted"]),
                duckdb_output_root=duckdb_output_root,
            )
        )
        _write_backfill_manifest(manifest_csv, manifest_rows)
        restore_interrupt_handlers()

        result["run_outcome"] = run_outcome
        result["manifest_csv"] = manifest_csv
        result["parallel_workers"] = resolved_write_workers
        result["load_workers"] = (
            min(
                resolved_load_worker_cap,
                memory_budget.max_inflight_loads,
            )
            if memory_budget is not None
            else resolved_load_worker_cap
        )
        result["parallel_week_groups"] = len(pending_week_groups)
        result["memory_peak_rss_gb"] = (
            memory_budget.peak_rss_gb if memory_budget is not None else None
        )
        result["memory_wait_seconds"] = (
            memory_budget.wait_seconds if memory_budget is not None else 0.0
        )
        result["memory_wait_cycles"] = (
            memory_budget.wait_cycles if memory_budget is not None else 0
        )
        result["total_execution_seconds"] = total_execution_seconds
        result["csv_execution_seconds"] = {
            run["source_date_label"]: float(run.get("execution_seconds") or 0.0)
            for run in result["runs"]
        }
        result["resource_wait_seconds"] = sum(
            float(run.get("resource_wait_seconds") or 0.0) for run in result["runs"]
        )
        result["lock_retry_count"] = sum(
            int(run.get("lock_retry_count") or 0) for run in result["runs"]
        )
        result["processed_count"] = len(result["runs"])
        result["skipped_count"] = len(result["skipped"])
        result["interrupted_count"] = len(result["interrupted"])
        result["interrupted_at_utc"] = interrupt_state.requested_at_utc

    return result
