from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

from data_analysis_scripts.trading_view_move_prediction_analysis import LOG_DIR
from db.trading_view_backwards_prediction_duckdb import (
    BackwardsPredictionDuckDBStore,
    query_backwards_prediction_duckdb,
)
from db.trading_view_move_prediction_duckdb import (
    _quote_identifier,
    _quote_path_literal,
    query_move_prediction_duckdb,
)

DEFAULT_BACKWARDS_ANALYSIS_ROOT = "backwards_prediction_analysis"
DEFAULT_BACKWARDS_RUN_PREFIX = "backwards_prediction_analysis"
DEFAULT_DUCKDB_RUNS_ROOT = LOG_DIR / "duckdb_runs"
MOVE_PREDICTION_PROFILES_ROOT = (
    Path(__file__).resolve().parents[2] / "config" / "move_prediction_profiles"
)

TRACKED_HORIZONS: tuple[str, ...] = ("days", "weeks", "months", "years")
TRACKED_PERFORMANCE_FIELDS: tuple[str, ...] = (
    "Perf.5D",
    "Perf.W",
    "Perf.1M",
    "Perf.YTD",
)
COMPONENT_FIELDS: tuple[str, ...] = (
    "attention",
    "event",
    "momentum",
    "trend",
    "quality",
    "valuation",
    "safety",
    "scale",
)

PRESET_OFFSET_DAYS: dict[str, int] = {
    "yesterday": 1,
    "last_week": 7,
    "last_month": 30,
}

DEFAULT_ANCHOR_PRESETS: tuple[str, ...] = (
    "yesterday",
    "last_week",
    "last_month",
    "oldest",
)

DUCKDB_RUN_SESSION_TIME_PATTERN = re.compile(
    r"(?P<hour>\d{2})(?P<minute>\d{2})_utc",
    re.IGNORECASE,
)

SYMBOL_KEY_SQL = """
COALESCE(
    NULLIF(split_part(CAST({alias}.symbol AS VARCHAR), ':', 2), ''),
    CAST({alias}.symbol AS VARCHAR)
)
"""

PROFILE_VERSION_SUFFIX_RE = re.compile(r"_v(\d+)$", re.IGNORECASE)

PROFILE_FAMILY_SQL = (
    "LOWER(REGEXP_REPLACE(TRIM(CAST({alias}.profile_name AS VARCHAR)), "
    "'_v[0-9]+$', ''))"
)

PROFILE_VERSION_RANK_SQL = (
    "TRY_CAST(NULLIF(REGEXP_EXTRACT(LOWER(TRIM(CAST({alias}.profile_name "
    "AS VARCHAR))), '_v([0-9]+)$', 1), '') AS INTEGER)"
)


def profile_family(profile_name: str) -> str:
    normalized = profile_name.strip().lower()
    return PROFILE_VERSION_SUFFIX_RE.sub("", normalized)


def profile_version_number(profile_name: str) -> int:
    match = PROFILE_VERSION_SUFFIX_RE.search(profile_name.strip().lower())
    return int(match.group(1)) if match else 0


def canonical_profiles_per_family(profiles: Iterable[str]) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for profile in profiles:
        normalized = profile.strip().lower()
        if not normalized:
            continue
        family = profile_family(normalized)
        existing = canonical.get(family)
        if existing is None or profile_version_number(
            normalized
        ) > profile_version_number(existing):
            canonical[family] = normalized
    return canonical


def _register_profile_family_variant(
    variants_by_family: dict[str, set[str]],
    name: str,
) -> None:
    normalized = name.strip().lower()
    if not normalized:
        return
    family = profile_family(normalized)
    bucket = variants_by_family.setdefault(family, set())
    bucket.add(normalized)
    bucket.add(family)


@lru_cache(maxsize=4)
def build_profile_family_variant_groups(
    profiles_root: str | None = None,
) -> dict[str, frozenset[str]]:
    """
    Map profile family -> all known profile name variants (versioned + unversioned).

    Built from profile JSON files (profile_id + base_profile_id), suite manifests,
    and frozen Python preset names so anchor matching can fall back to names like
    ``breakout_long`` when the current run uses ``breakout_long_v1``.
    """
    root = (
        Path(profiles_root)
        if profiles_root is not None
        else MOVE_PREDICTION_PROFILES_ROOT
    )
    variants_by_family: dict[str, set[str]] = {}

    profiles_dir = root / "profiles"
    if profiles_dir.is_dir():
        for path in sorted(profiles_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            _register_profile_family_variant(
                variants_by_family,
                str(payload.get("profile_id") or path.stem),
            )
            base_profile_id = payload.get("base_profile_id")
            if base_profile_id:
                _register_profile_family_variant(
                    variants_by_family,
                    str(base_profile_id),
                )

    suites_dir = root / "suites"
    if suites_dir.is_dir():
        for path in sorted(suites_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            profile_names = payload.get("profile_names")
            if not isinstance(profile_names, list):
                continue
            for profile_name in profile_names:
                _register_profile_family_variant(variants_by_family, str(profile_name))

    try:
        from data_analysis_scripts.trading_view_move_prediction_analysis import (
            PRESET_SCORING_PROFILES,
        )

        for preset_name in PRESET_SCORING_PROFILES:
            _register_profile_family_variant(variants_by_family, preset_name)
    except ImportError:
        pass

    return {
        family: frozenset(names) for family, names in sorted(variants_by_family.items())
    }


def resolve_anchor_profile_variant_names(
    canonical_profiles_by_family: dict[str, str],
    *,
    variant_groups: dict[str, frozenset[str]] | None = None,
) -> frozenset[str]:
    groups = variant_groups or build_profile_family_variant_groups()
    names: set[str] = set()
    for family in canonical_profiles_by_family:
        names.update(groups.get(family, frozenset({family})))
        names.add(family)
    return frozenset(names)


def variant_names_for_family(
    family: str,
    *,
    variant_groups: dict[str, frozenset[str]] | None = None,
) -> frozenset[str]:
    normalized_family = profile_family(family)
    groups = variant_groups or build_profile_family_variant_groups()
    return groups.get(normalized_family, frozenset({normalized_family}))


def _profile_family_expr(*, alias: str) -> str:
    return PROFILE_FAMILY_SQL.format(alias=alias)


def _profile_version_rank_expr(*, alias: str) -> str:
    return PROFILE_VERSION_RANK_SQL.format(alias=alias)


def _build_canonical_profiles_values_sql(
    canonical_profiles_by_family: dict[str, str],
) -> str:
    if not canonical_profiles_by_family:
        return (
            "SELECT CAST(NULL AS VARCHAR) AS profile_family, "
            "CAST(NULL AS VARCHAR) AS profile_name WHERE FALSE"
        )
    rows = ", ".join(
        f"({_quote_sql_literal(family)}, {_quote_sql_literal(name)})"
        for family, name in sorted(canonical_profiles_by_family.items())
    )
    return (
        "SELECT profile_family, profile_name "
        f"FROM (VALUES {rows}) AS canonical_profiles(profile_family, profile_name)"
    )


@dataclass(frozen=True)
class AnchorSpec:
    name: str | None = None
    preset: str | None = None
    offset_days: int | None = None
    runs_back: int | None = None
    run_id: str | None = None
    run_folder: str | None = None
    target_date: date | str | None = None

    def __post_init__(self) -> None:
        if self.name is not None:
            return
        if self.preset:
            object.__setattr__(self, "name", self.preset)
        elif self.run_id:
            object.__setattr__(self, "name", f"run_{self.run_id[-12:]}")
        elif self.run_folder:
            object.__setattr__(self, "name", f"folder_{self.run_folder[-12:]}")
        elif self.target_date is not None:
            object.__setattr__(self, "name", f"date_{self.target_date}")
        elif self.runs_back is not None:
            object.__setattr__(self, "name", f"runs_back_{self.runs_back}")
        elif self.offset_days is not None:
            object.__setattr__(self, "name", f"offset_days_{self.offset_days}")
        else:
            raise ValueError(
                "AnchorSpec requires name or a resolution field "
                "(preset, run_id, run_folder, target_date, runs_back, offset_days)."
            )

    @classmethod
    def preset(cls, name: str) -> AnchorSpec:
        return cls(name=name, preset=name)


@dataclass(frozen=True)
class RunIndexEntry:
    run_id: str
    created_at_utc: datetime
    scan_data_count: int | None
    database_path: Path
    run_label: str | None = None
    suite_name: str | None = None

    @property
    def snapshot_date(self) -> date:
        return self.created_at_utc.date()

    @property
    def snapshot_session(self) -> str:
        return _build_snapshot_session(
            run_id=self.run_id,
            created_at_utc=self.created_at_utc,
            run_label=self.run_label,
        )

    @property
    def snapshot_label(self) -> str:
        return f"{self.snapshot_date.isoformat()} {self.snapshot_session}"


@dataclass(frozen=True)
class ResolvedAnchor:
    spec: AnchorSpec
    entry: RunIndexEntry
    resolution_method: str
    offset_days_from_current: float | None = None
    runs_back_from_current: int | None = None


@dataclass(frozen=True)
class BackwardsAnalysisLayout:
    run_dir: Path
    database_path: Path
    parquet_dir: Path
    backwards_analysis_id: str
    created_at_utc: datetime
    overview_log_path: Path


@dataclass
class BackwardsAnalysisBuildOptions:
    include_deltas: bool = True
    include_consensus: bool = True
    include_components: bool = True
    include_snapshots: bool = True


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _slugify_text(value: Any) -> str:
    normalized = _normalize_text(value)
    if normalized is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")


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


def _parse_target_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    normalized = _normalize_text(value)
    if normalized is None:
        return None
    return date.fromisoformat(normalized)


def _parse_day_label_date(value: str | None) -> date | None:
    normalized = _normalize_text(value)
    if normalized is None:
        return None
    for parser in (
        lambda text: datetime.strptime(text, "%d_%m_%Y").date(),
        date.fromisoformat,
    ):
        try:
            return parser(normalized)
        except ValueError:
            continue
    return None


def _build_snapshot_session(
    *,
    run_id: str,
    created_at_utc: datetime,
    run_label: str | None,
) -> str:
    label_prefix = _slugify_text(run_label) or "run"
    return f"{label_prefix}_{created_at_utc.strftime('%H%M')}_utc_{run_id[-8:]}"


def _quote_sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _build_optional_in_clause(values: Sequence[str]) -> str:
    return ", ".join(_quote_sql_literal(value) for value in values)


def _iter_weekly_prediction_databases(duckdb_runs_root: Path) -> list[Path]:
    if not duckdb_runs_root.exists():
        return []
    return sorted(duckdb_runs_root.rglob("move_prediction_*.duckdb"))


def _build_run_index(
    *,
    duckdb_runs_root: Path,
    min_scan_data_count: int | None = None,
) -> list[RunIndexEntry]:
    entries: list[RunIndexEntry] = []
    for database_path in _iter_weekly_prediction_databases(duckdb_runs_root):
        rows = query_move_prediction_duckdb(
            database_path,
            """
            SELECT run_id,
                created_at_utc,
                scan_data_count,
                run_label,
                suite_name
            FROM run_metadata
            ORDER BY created_at_utc, run_id
            """,
        )
        for row in rows:
            run_id = _normalize_text(row.get("run_id"))
            created_at_utc = _ensure_datetime_utc(row.get("created_at_utc"))
            if run_id is None or created_at_utc is None:
                continue
            scan_data_count = row.get("scan_data_count")
            if min_scan_data_count is not None:
                if (
                    scan_data_count is None
                    or int(scan_data_count) < min_scan_data_count
                ):
                    continue
            entries.append(
                RunIndexEntry(
                    run_id=run_id,
                    created_at_utc=created_at_utc,
                    scan_data_count=(
                        int(scan_data_count) if scan_data_count is not None else None
                    ),
                    database_path=database_path,
                    run_label=_normalize_text(row.get("run_label")),
                    suite_name=_normalize_text(row.get("suite_name")),
                )
            )
    entries.sort(key=lambda entry: (entry.created_at_utc, entry.run_id))
    return entries


def _resolve_current_run(
    *,
    run_index: Sequence[RunIndexEntry],
    current_run_id: str | None,
    current_database_path: str | Path | None,
) -> RunIndexEntry:
    if current_run_id:
        normalized_run_id = _normalize_text(current_run_id)
        for entry in run_index:
            if entry.run_id == normalized_run_id:
                return entry
        raise ValueError(f"Current run_id not found in run index: {current_run_id}")

    if current_database_path is not None:
        resolved_path = Path(current_database_path)
        candidates = [
            entry for entry in run_index if entry.database_path == resolved_path
        ]
        if not candidates:
            raise ValueError(f"No runs found in current database path: {resolved_path}")
        return candidates[-1]

    if not run_index:
        raise ValueError("No move-prediction runs found to resolve current run.")
    return run_index[-1]


def _resolve_offset_days_anchor(
    *,
    run_index: Sequence[RunIndexEntry],
    current: RunIndexEntry,
    offset_days: int,
) -> RunIndexEntry | None:
    target_time = current.created_at_utc - timedelta(days=offset_days)
    candidates = [
        entry
        for entry in run_index
        if entry.run_id != current.run_id and entry.created_at_utc <= target_time
    ]
    if not candidates:
        return None
    return candidates[-1]


def _resolve_runs_back_anchor(
    *,
    run_index: Sequence[RunIndexEntry],
    current: RunIndexEntry,
    runs_back: int,
) -> RunIndexEntry | None:
    if runs_back <= 0:
        raise ValueError("runs_back must be positive.")
    prior_entries = [
        entry for entry in run_index if entry.created_at_utc < current.created_at_utc
    ]
    if len(prior_entries) < runs_back:
        return None
    return prior_entries[-runs_back]


def _resolve_target_date_anchor(
    *,
    run_index: Sequence[RunIndexEntry],
    current: RunIndexEntry,
    target_date: date,
) -> RunIndexEntry | None:
    candidates = [
        entry
        for entry in run_index
        if entry.run_id != current.run_id and entry.snapshot_date <= target_date
    ]
    if not candidates:
        return None
    return candidates[-1]


def _resolve_run_folder_anchor(
    *,
    run_index: Sequence[RunIndexEntry],
    run_folder: str,
) -> RunIndexEntry | None:
    normalized = _normalize_text(run_folder)
    if normalized is None:
        return None

    search = normalized.lower()
    exact_matches = [entry for entry in run_index if entry.run_id.lower() == search]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ValueError(
            f"Ambiguous run_folder {run_folder!r}; multiple exact matches found."
        )

    suffix_matches = [
        entry for entry in run_index if entry.run_id.lower().endswith(search)
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    if re.fullmatch(r"[0-9a-f]{8}", search):
        short_suffix = f"_utc_{search}"
        short_matches = [
            entry for entry in run_index if entry.run_id.lower().endswith(short_suffix)
        ]
        if len(short_matches) == 1:
            return short_matches[0]
        if len(short_matches) > 1:
            candidates = ", ".join(entry.run_id for entry in short_matches[:8])
            raise ValueError(
                f"Ambiguous run_folder {run_folder!r}; multiple run_id matches: {candidates}"
            )

    if len(suffix_matches) > 1:
        candidates = ", ".join(entry.run_id for entry in suffix_matches[:8])
        raise ValueError(
            f"Ambiguous run_folder {run_folder!r}; multiple run_id matches: {candidates}"
        )
    return None


def _is_in_iso_week_range(
    *,
    entry: RunIndexEntry,
    start_iso_week: tuple[int, int] | None,
    end_iso_week: tuple[int, int] | None,
) -> bool:
    iso_year, iso_week, _ = entry.created_at_utc.isocalendar()
    key = (iso_year, iso_week)
    if start_iso_week is not None and key < start_iso_week:
        return False
    if end_iso_week is not None and key > end_iso_week:
        return False
    return True


def _oldest_indexed_iso_week(
    run_index: Sequence[RunIndexEntry],
) -> tuple[int, int] | None:
    if not run_index:
        return None
    iso_year, iso_week, _ = run_index[0].created_at_utc.isocalendar()
    return (iso_year, iso_week)


def _resolve_weekly_anchor_iso_week_bounds(
    *,
    iso_year: int | None = None,
    start_week: int | None = None,
    end_week: int | None = None,
    start_iso_week: tuple[int, int] | None = None,
    end_iso_week: tuple[int, int] | None = None,
    default_iso_year: int | None = None,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Map iso_year/start_week/end_week convenience args to (year, week) bounds.

    When ``start_week`` is omitted, the lower bound is ``start_iso_week`` if given,
    otherwise ``None`` (include from the oldest indexed run). When ``end_week`` is
    omitted, the upper bound is ``end_iso_week`` if given, otherwise ``None``
    (include through the week before the current run).
    """
    resolved_start = start_iso_week
    resolved_end = end_iso_week

    if start_week is not None:
        year = iso_year if iso_year is not None else default_iso_year
        if year is None:
            raise ValueError(
                "iso_year is required when start_week is set "
                "(or omit start_week to begin at the oldest indexed week)."
            )
        if start_week < 1 or start_week > 53:
            raise ValueError("start_week must be between 1 and 53.")
        resolved_start = (year, start_week)

    if end_week is not None:
        year = iso_year if iso_year is not None else default_iso_year
        if year is None:
            raise ValueError("iso_year is required when end_week is set.")
        if end_week < 1 or end_week > 53:
            raise ValueError("end_week must be between 1 and 53.")
        resolved_end = (year, end_week)

    if (
        resolved_start is not None
        and resolved_end is not None
        and resolved_start > resolved_end
    ):
        raise ValueError(
            f"start week {resolved_start} cannot be after end week {resolved_end}."
        )
    return resolved_start, resolved_end


def _pick_weekly_anchor_entries(
    *,
    week_entries: Sequence[RunIndexEntry],
    runs_per_week: int,
    selection: str,
) -> list[tuple[str, RunIndexEntry]]:
    ordered = list(week_entries)
    if not ordered:
        return []

    if runs_per_week == 1:
        return [("only", ordered[-1])]

    if selection == "last_only":
        return [("only", ordered[-1])]

    if selection == "first_last":
        if len(ordered) == 1:
            return [("only", ordered[0])]
        return [("open", ordered[0]), ("close", ordered[-1])]

    if selection == "even_spread":
        if len(ordered) == 1:
            return [("only", ordered[0])]
        max_points = min(runs_per_week, len(ordered))
        if max_points == 1:
            return [("only", ordered[-1])]
        selected_indexes = sorted(
            {
                round(i * (len(ordered) - 1) / (max_points - 1))
                for i in range(max_points)
            }
        )
        selected: list[tuple[str, RunIndexEntry]] = []
        for idx, index in enumerate(selected_indexes):
            if max_points == 2:
                slot = ("open", "close")[idx]
            elif idx == 0:
                slot = "open"
            elif idx == len(selected_indexes) - 1:
                slot = "close"
            elif idx == 1 and max_points == 3:
                slot = "mid"
            else:
                slot = f"slot{idx + 1}"
            selected.append((slot, ordered[index]))
        return selected

    raise ValueError(
        "selection must be one of: 'first_last', 'last_only', 'even_spread'."
    )


def build_weekly_sample_anchors(
    run_index: Sequence[RunIndexEntry],
    current: RunIndexEntry,
    *,
    runs_per_week: int = 2,
    selection: str = "first_last",
    start_iso_week: tuple[int, int] | None = None,
    end_iso_week: tuple[int, int] | None = None,
    min_scan_data_count: int | None = None,
) -> list[AnchorSpec]:
    if runs_per_week < 1 or runs_per_week > 5:
        raise ValueError("runs_per_week must be between 1 and 5.")
    if runs_per_week > 2 and selection != "even_spread":
        raise ValueError(
            "When runs_per_week > 2, use selection='even_spread' "
            "(first_last supports at most 2 anchors per week)."
        )
    if selection not in {"first_last", "last_only", "even_spread"}:
        raise ValueError(
            "selection must be one of: 'first_last', 'last_only', 'even_spread'."
        )

    filtered_entries = [
        entry
        for entry in run_index
        if entry.run_id != current.run_id
        and entry.created_at_utc < current.created_at_utc
        and (
            min_scan_data_count is None
            or (
                entry.scan_data_count is not None
                and int(entry.scan_data_count) >= min_scan_data_count
            )
        )
        and _is_in_iso_week_range(
            entry=entry,
            start_iso_week=start_iso_week,
            end_iso_week=end_iso_week,
        )
    ]
    if not filtered_entries:
        return []

    weekly_groups: dict[tuple[int, int], list[RunIndexEntry]] = {}
    for entry in filtered_entries:
        iso_year, iso_week, _ = entry.created_at_utc.isocalendar()
        weekly_groups.setdefault((iso_year, iso_week), []).append(entry)

    anchors: list[AnchorSpec] = []
    seen_run_ids: set[str] = set()
    for iso_year, iso_week in sorted(weekly_groups.keys()):
        week_entries = sorted(
            weekly_groups[(iso_year, iso_week)],
            key=lambda item: (item.created_at_utc, item.run_id),
        )
        for slot, selected in _pick_weekly_anchor_entries(
            week_entries=week_entries,
            runs_per_week=runs_per_week,
            selection=selection,
        ):
            if selected.run_id in seen_run_ids:
                continue
            seen_run_ids.add(selected.run_id)
            anchor_name = f"w{iso_year}_{iso_week:02d}_{slot}"
            anchors.append(AnchorSpec(name=anchor_name, run_id=selected.run_id))
    return anchors


def resolve_weekly_progression_anchors(
    *,
    duckdb_runs_root: str | Path | None = None,
    current_run_id: str | None = None,
    current_database_path: str | Path | None = None,
    runs_per_week: int = 2,
    selection: str = "first_last",
    iso_year: int | None = None,
    start_week: int | None = None,
    end_week: int | None = None,
    start_iso_week: tuple[int, int] | None = None,
    end_iso_week: tuple[int, int] | None = None,
    min_scan_data_count: int | None = None,
    anchor_min_scan_data_count: int | None = None,
    extra_anchors: Sequence[AnchorSpec] | None = None,
) -> dict[str, Any]:
    root = (
        Path(duckdb_runs_root)
        if duckdb_runs_root is not None
        else DEFAULT_DUCKDB_RUNS_ROOT
    )
    current_index = _build_run_index(
        duckdb_runs_root=root,
        min_scan_data_count=min_scan_data_count,
    )
    anchor_index = _build_run_index(
        duckdb_runs_root=root,
        min_scan_data_count=anchor_min_scan_data_count,
    )
    current = _resolve_current_run(
        run_index=current_index,
        current_run_id=current_run_id,
        current_database_path=current_database_path,
    )
    default_iso_year, _, _ = current.created_at_utc.isocalendar()
    resolved_start_iso_week, resolved_end_iso_week = (
        _resolve_weekly_anchor_iso_week_bounds(
            iso_year=iso_year,
            start_week=start_week,
            end_week=end_week,
            start_iso_week=start_iso_week,
            end_iso_week=end_iso_week,
            default_iso_year=default_iso_year,
        )
    )
    weekly_anchors = build_weekly_sample_anchors(
        run_index=anchor_index,
        current=current,
        runs_per_week=runs_per_week,
        selection=selection,
        start_iso_week=resolved_start_iso_week,
        end_iso_week=resolved_end_iso_week,
        min_scan_data_count=anchor_min_scan_data_count,
    )
    anchors = list(weekly_anchors)
    if extra_anchors:
        anchors.extend(extra_anchors)
    resolved_anchors = resolve_anchor_specs(
        run_index=anchor_index,
        current=current,
        anchors=anchors,
    )
    return {
        "duckdb_runs_root": root,
        "run_index": anchor_index,
        "current_run_index": current_index,
        "current": current,
        "anchors": anchors,
        "resolved_anchors": resolved_anchors,
        "iso_year": iso_year,
        "start_week": start_week,
        "end_week": end_week,
        "start_iso_week": resolved_start_iso_week,
        "end_iso_week": resolved_end_iso_week,
        "oldest_indexed_iso_week": _oldest_indexed_iso_week(anchor_index),
        "runs_per_week": runs_per_week,
        "selection": selection,
    }


def _default_weekly_anchor_selection(runs_per_week: int) -> str:
    if runs_per_week == 1:
        return "last_only"
    if runs_per_week == 2:
        return "first_last"
    return "even_spread"


def summarize_weekly_anchor_plan(anchor_plan: dict[str, Any]) -> dict[str, Any]:
    """Human-readable summary of weekly anchor coverage for logging/debug."""
    resolved = list(anchor_plan.get("resolved_anchors") or [])
    if not resolved:
        return {
            "anchor_count": 0,
            "oldest_anchor_name": None,
            "oldest_anchor_time": None,
            "newest_anchor_name": None,
            "newest_anchor_time": None,
            "current_run_id": getattr(anchor_plan.get("current"), "run_id", None),
        }

    def _anchor_time(item: Any) -> datetime:
        entry = getattr(item, "entry", None)
        created = getattr(entry, "created_at_utc", None)
        if created is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return created

    ordered = sorted(resolved, key=_anchor_time)
    oldest = ordered[0]
    newest = ordered[-1]
    current = anchor_plan.get("current")
    summary: dict[str, Any] = {
        "anchor_count": len(resolved),
        "oldest_anchor_name": oldest.spec.name,
        "oldest_anchor_time": oldest.entry.created_at_utc.isoformat(),
        "oldest_run_id": oldest.entry.run_id,
        "newest_anchor_name": newest.spec.name,
        "newest_anchor_time": newest.entry.created_at_utc.isoformat(),
        "newest_run_id": newest.entry.run_id,
        "current_run_id": getattr(current, "run_id", None),
        "current_run_time": (
            getattr(current, "created_at_utc", None).isoformat()
            if getattr(current, "created_at_utc", None)
            else None
        ),
    }
    for key in (
        "iso_year",
        "start_week",
        "end_week",
        "start_iso_week",
        "end_iso_week",
        "oldest_indexed_iso_week",
        "runs_per_week",
        "selection",
    ):
        if key in anchor_plan:
            summary[key] = anchor_plan[key]
    return summary


def resolve_anchor_specs(
    *,
    run_index: Sequence[RunIndexEntry],
    current: RunIndexEntry,
    anchors: Sequence[AnchorSpec] | None,
) -> list[ResolvedAnchor]:
    if anchors is None:
        anchor_specs = [AnchorSpec.preset(name) for name in DEFAULT_ANCHOR_PRESETS]
    else:
        anchor_specs = list(anchors)

    resolved: list[ResolvedAnchor] = []
    seen_pairs: set[tuple[str, str]] = set()

    for spec in anchor_specs:
        entry: RunIndexEntry | None = None
        resolution_method = "unknown"
        offset_days_from_current: float | None = None
        runs_back_from_current: int | None = None

        if spec.run_id:
            normalized_run_id = _normalize_text(spec.run_id)
            entry = next(
                (
                    candidate
                    for candidate in run_index
                    if candidate.run_id == normalized_run_id
                ),
                None,
            )
            resolution_method = "explicit_run_id"
        elif spec.run_folder:
            entry = _resolve_run_folder_anchor(
                run_index=run_index,
                run_folder=spec.run_folder,
            )
            resolution_method = "explicit_run_folder"
        elif spec.target_date is not None:
            target_date = _parse_target_date(spec.target_date)
            if target_date is None:
                raise ValueError(f"Invalid target_date for anchor {spec.name!r}.")
            entry = _resolve_target_date_anchor(
                run_index=run_index,
                current=current,
                target_date=target_date,
            )
            resolution_method = "target_date"
        elif spec.runs_back is not None:
            entry = _resolve_runs_back_anchor(
                run_index=run_index,
                current=current,
                runs_back=spec.runs_back,
            )
            resolution_method = "runs_back"
            runs_back_from_current = spec.runs_back
        elif spec.offset_days is not None:
            entry = _resolve_offset_days_anchor(
                run_index=run_index,
                current=current,
                offset_days=spec.offset_days,
            )
            resolution_method = "offset_days"
            offset_days_from_current = float(spec.offset_days)
        elif spec.preset == "oldest":
            candidates = [
                candidate
                for candidate in run_index
                if candidate.run_id != current.run_id
            ]
            entry = candidates[0] if candidates else None
            resolution_method = "preset_oldest"
        elif spec.preset == "previous_run":
            entry = _resolve_runs_back_anchor(
                run_index=run_index,
                current=current,
                runs_back=1,
            )
            resolution_method = "preset_previous_run"
            runs_back_from_current = 1
        elif spec.preset in PRESET_OFFSET_DAYS:
            offset_days = PRESET_OFFSET_DAYS[spec.preset]
            entry = _resolve_offset_days_anchor(
                run_index=run_index,
                current=current,
                offset_days=offset_days,
            )
            resolution_method = f"preset_{spec.preset}"
            offset_days_from_current = float(offset_days)
        else:
            raise ValueError(f"Unsupported anchor spec: {spec!r}")

        if entry is None:
            continue
        if entry.run_id == current.run_id:
            continue

        pair_key = (spec.name, entry.run_id)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        if offset_days_from_current is None and runs_back_from_current is None:
            delta = current.created_at_utc - entry.created_at_utc
            offset_days_from_current = delta.total_seconds() / 86400.0

        resolved.append(
            ResolvedAnchor(
                spec=spec,
                entry=entry,
                resolution_method=resolution_method,
                offset_days_from_current=offset_days_from_current,
                runs_back_from_current=runs_back_from_current,
            )
        )

    if not resolved:
        raise ValueError("No anchor runs could be resolved for backwards analysis.")
    return resolved


def _resolve_included_profiles(
    include_profiles: Sequence[str] | None,
    current_database_path: Path,
    current_run_id: str,
) -> set[str]:
    if include_profiles:
        return {
            profile.strip().lower() for profile in include_profiles if profile.strip()
        }
    rows = query_move_prediction_duckdb(
        current_database_path,
        """
        SELECT DISTINCT profile_name
        FROM profile_horizon_scores
        WHERE run_id = ?
        """,
        parameters=[current_run_id],
    )
    profiles = {
        _normalize_text(row.get("profile_name"))
        for row in rows
        if _normalize_text(row.get("profile_name"))
    }
    normalized = {profile.lower() for profile in profiles if profile}
    return set(canonical_profiles_per_family(normalized).values())


def resolve_backwards_analysis_layout(
    output_dir: str | Path | None = None,
    *,
    backwards_analysis_id: str | None = None,
) -> BackwardsAnalysisLayout:
    created_at_utc = datetime.now(tz=timezone.utc)
    base_dir = (
        Path(output_dir)
        if output_dir is not None
        else Path(LOG_DIR) / "duckdb_runs" / DEFAULT_BACKWARDS_ANALYSIS_ROOT
    )
    analysis_id = backwards_analysis_id or (
        f"{DEFAULT_BACKWARDS_RUN_PREFIX}_"
        f"{created_at_utc.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    )
    run_dir = base_dir / "runs" / analysis_id
    return BackwardsAnalysisLayout(
        run_dir=run_dir,
        database_path=run_dir / "backwards_prediction_analysis.duckdb",
        parquet_dir=run_dir / "parquet",
        backwards_analysis_id=analysis_id,
        created_at_utc=created_at_utc,
        overview_log_path=run_dir / "_backwards_analysis_overview.log",
    )


def _describe_table_columns(conn: Any, relation_sql: str) -> set[str]:
    rows = conn.execute(f"DESCRIBE SELECT * FROM {relation_sql}").fetchall()
    return {str(row[0]) for row in rows}


def _attach_source_database(
    conn: Any,
    *,
    database_path: Path,
    alias: str,
) -> None:
    conn.execute(f"DETACH DATABASE IF EXISTS {_quote_identifier(alias)}")
    conn.execute(
        f"ATTACH {_quote_path_literal(database_path.resolve())} "
        f"AS {_quote_identifier(alias)} (READ_ONLY)"
    )


def _detach_source_database(conn: Any, *, alias: str) -> None:
    conn.execute(f"DETACH DATABASE IF EXISTS {_quote_identifier(alias)}")


def _profile_filter_sql(
    *,
    alias: str,
    include_profiles: set[str] | None,
) -> str:
    if not include_profiles:
        return ""
    values = ", ".join(
        _quote_sql_literal(profile) for profile in sorted(include_profiles)
    )
    return f" AND LOWER(TRIM(CAST({alias}.profile_name AS VARCHAR))) IN ({values})"


def _build_dense_day_spacing_anchor_specs(
    *,
    run_index: Sequence[RunIndexEntry],
    current: RunIndexEntry,
    start_date: date,
    end_date: date,
    spacing_days: int,
) -> tuple[list[AnchorSpec], list[date]]:
    if spacing_days < 1:
        raise ValueError("spacing_days must be at least 1.")
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date.")

    anchors: list[AnchorSpec] = []
    target_dates: list[date] = []
    seen_run_ids: set[str] = set()
    current_date = start_date
    while current_date <= end_date:
        target_dates.append(current_date)
        entry = _resolve_target_date_anchor(
            run_index=run_index,
            current=current,
            target_date=current_date,
        )
        if entry is not None and entry.run_id not in seen_run_ids:
            seen_run_ids.add(entry.run_id)
            anchors.append(
                AnchorSpec(
                    name=f"dense_{current_date.strftime('%Y_%m_%d')}",
                    run_id=entry.run_id,
                )
            )
        current_date += timedelta(days=spacing_days)
    return anchors, target_dates


def run_backwards_prediction_dense_day_spacing_analysis(
    *,
    start_day_label: str,
    end_day_label: str | None = None,
    spacing_days: int = 1,
    duckdb_runs_root: str | Path | None = None,
    current_run_id: str | None = None,
    current_database_path: str | Path | None = None,
    min_scan_data_count: int | None = None,
    anchor_min_scan_data_count: int | None = None,
    profile_suite_path: str | Path | None = None,
    include_profiles: Sequence[str] | None = None,
    include_consensus: bool = True,
    include_components: bool = True,
    output_dir: str | Path | None = None,
    backwards_analysis_id: str | None = None,
    export_parquet: bool = False,
    duckdb_threads: int | None = None,
    memory_gb: float | None = None,
) -> dict[str, Any]:
    """Backwards analysis using unique anchors resolved on a fixed day grid.

    Unlike the weekly sparse sampler, this helper requests target dates every
    ``spacing_days`` days across the chosen window and resolves the latest run on
    or before each date. Duplicate run resolutions are collapsed, so the final
    anchor set stays as dense as the indexed run history allows.
    """

    root = (
        Path(duckdb_runs_root)
        if duckdb_runs_root is not None
        else DEFAULT_DUCKDB_RUNS_ROOT
    )
    current_index = _build_run_index(
        duckdb_runs_root=root,
        min_scan_data_count=min_scan_data_count,
    )
    anchor_index = _build_run_index(
        duckdb_runs_root=root,
        min_scan_data_count=anchor_min_scan_data_count,
    )
    current = _resolve_current_run(
        run_index=current_index,
        current_run_id=current_run_id,
        current_database_path=current_database_path,
    )

    resolved_start_date = _parse_day_label_date(start_day_label)
    if resolved_start_date is None:
        raise ValueError(f"Invalid start_day_label: {start_day_label!r}")
    resolved_end_date = (
        _parse_day_label_date(end_day_label)
        if end_day_label is not None
        else current.snapshot_date
    )
    if resolved_end_date is None:
        raise ValueError(f"Invalid end_day_label: {end_day_label!r}")

    anchors, target_dates = _build_dense_day_spacing_anchor_specs(
        run_index=anchor_index,
        current=current,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        spacing_days=spacing_days,
    )
    if not anchors:
        raise ValueError(
            "No dense anchors could be resolved for the requested day range."
        )

    profiles: list[str] | None
    if include_profiles is not None:
        profiles = list(include_profiles)
    elif profile_suite_path is not None:
        from data_analysis_scripts.trading_view_move_prediction_profile_config import (
            resolve_profile_suite,
        )

        suite = resolve_profile_suite(profile_suite_path)
        profiles = list(suite.get("profile_names", []))
    else:
        profiles = None

    result = run_backwards_prediction_analysis(
        current_run_id=current_run_id,
        current_database_path=current_database_path,
        anchors=anchors,
        duckdb_runs_root=root,
        include_profiles=profiles,
        include_consensus=include_consensus,
        include_components=include_components,
        min_scan_data_count=min_scan_data_count,
        anchor_min_scan_data_count=anchor_min_scan_data_count,
        output_dir=output_dir,
        backwards_analysis_id=backwards_analysis_id,
        export_parquet=export_parquet,
        duckdb_threads=duckdb_threads,
        memory_gb=memory_gb,
    )
    result["dense_anchor_summary"] = {
        "spacing_days": spacing_days,
        "start_day_label": resolved_start_date.strftime("%d_%m_%Y"),
        "end_day_label": resolved_end_date.strftime("%d_%m_%Y"),
        "requested_target_dates_count": len(target_dates),
        "selected_anchor_count": len(anchors),
        "first_anchor_name": anchors[0].name if anchors else None,
        "last_anchor_name": anchors[-1].name if anchors else None,
    }
    result["spacing_days"] = spacing_days
    result["start_day_label"] = resolved_start_date.strftime("%d_%m_%Y")
    result["end_day_label"] = resolved_end_date.strftime("%d_%m_%Y")
    return result


def _profile_family_filter_sql(
    *,
    alias: str,
    profile_families: set[str],
) -> str:
    if not profile_families:
        return ""
    values = ", ".join(
        _quote_sql_literal(family) for family in sorted(profile_families)
    )
    return f" AND {_profile_family_expr(alias=alias)} IN ({values})"


def _profile_variant_names_filter_sql(
    *,
    alias: str,
    profile_names: Iterable[str],
) -> str:
    normalized_names = sorted(
        {name.strip().lower() for name in profile_names if str(name or "").strip()}
    )
    if not normalized_names:
        return " AND FALSE"
    values = ", ".join(_quote_sql_literal(name) for name in normalized_names)
    return f" AND LOWER(TRIM(CAST({alias}.profile_name AS VARCHAR))) IN ({values})"


def _anchor_profile_match_priority_sql(
    *,
    alias: str,
    canonical_alias: str = "cp",
) -> str:
    name_expr = f"LOWER(TRIM(CAST({alias}.profile_name AS VARCHAR)))"
    return f"""
        CASE
            WHEN {name_expr} = {canonical_alias}.profile_name THEN 0
            WHEN {name_expr} = {canonical_alias}.profile_family THEN 1
            ELSE 2
        END
    """.strip()


def _perf_column_sql(
    *,
    alias: str,
    raw_alias: str,
    column_name: str,
    output_alias: str,
    available_columns: set[str],
) -> str:
    if column_name in available_columns:
        return (
            f"TRY_CAST({raw_alias}.{_quote_identifier(column_name)} AS DOUBLE) "
            f"AS {output_alias}"
        )
    return f"CAST(NULL AS DOUBLE) AS {output_alias}"


def _build_raw_perf_select_columns(
    *,
    raw_alias: str,
    prefix: str,
    available_columns: set[str],
) -> list[str]:
    mapping = {
        "Perf.5D": f"{prefix}perf_5d",
        "Perf.W": f"{prefix}perf_w",
        "Perf.1M": f"{prefix}perf_1m",
        "Perf.YTD": f"{prefix}perf_ytd",
    }
    return [
        _perf_column_sql(
            alias=raw_alias,
            raw_alias=raw_alias,
            column_name=source_column,
            output_alias=output_alias,
            available_columns=available_columns,
        )
        for source_column, output_alias in mapping.items()
    ]


def _build_profile_horizon_delta_insert_sql(
    *,
    layout: BackwardsAnalysisLayout,
    anchor: ResolvedAnchor,
    current: RunIndexEntry,
    include_profiles: set[str],
    canonical_profiles_by_family: dict[str, str],
    current_alias: str,
    anchor_alias: str,
    current_raw_columns: set[str],
    anchor_raw_columns: set[str],
) -> str:
    analysis_id_sql = _quote_sql_literal(layout.backwards_analysis_id)
    anchor_name_sql = _quote_sql_literal(anchor.spec.name)
    current_run_sql = _quote_sql_literal(current.run_id)
    anchor_run_sql = _quote_sql_literal(anchor.entry.run_id)
    current_profile_filter = _profile_filter_sql(
        alias="phs", include_profiles=include_profiles
    )
    anchor_family_filter = _profile_family_filter_sql(
        alias="phs",
        profile_families=set(canonical_profiles_by_family),
    )
    canonical_profiles_cte = _build_canonical_profiles_values_sql(
        canonical_profiles_by_family
    )

    current_raw_perf = _build_raw_perf_select_columns(
        raw_alias="current_raw",
        prefix="current_",
        available_columns=current_raw_columns,
    )
    anchor_raw_perf = _build_raw_perf_select_columns(
        raw_alias="anchor_raw",
        prefix="anchor_",
        available_columns=anchor_raw_columns,
    )

    return f"""
        INSERT INTO backwards_profile_horizon_deltas
        WITH canonical_profiles AS (
            {canonical_profiles_cte}
        ),
        current_scores AS (
            SELECT
                phs.*,
                {SYMBOL_KEY_SQL.format(alias="phs")} AS symbol_key,
                {_profile_family_expr(alias="phs")} AS profile_family,
                ROW_NUMBER() OVER (
                    PARTITION BY phs.profile_name, phs.horizon_name
                    ORDER BY phs.score DESC NULLS LAST, phs.symbol ASC
                ) AS profile_rank
            FROM {_quote_identifier(current_alias)}.profile_horizon_scores AS phs
            INNER JOIN canonical_profiles AS cp
                ON {_profile_family_expr(alias="phs")} = cp.profile_family
               AND LOWER(TRIM(CAST(phs.profile_name AS VARCHAR))) = cp.profile_name
            WHERE phs.run_id = {current_run_sql}
            {current_profile_filter}
        ),
        anchor_scores_ranked AS (
            SELECT
                phs.*,
                {SYMBOL_KEY_SQL.format(alias="phs")} AS symbol_key,
                {_profile_family_expr(alias="phs")} AS profile_family,
                cp.profile_name AS canonical_profile_name,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        {SYMBOL_KEY_SQL.format(alias="phs")},
                        cp.profile_family,
                        phs.horizon_name
                    ORDER BY
                        {_anchor_profile_match_priority_sql(alias="phs")},
                        {_profile_version_rank_expr(alias="phs")} DESC NULLS LAST,
                        phs.score DESC NULLS LAST,
                        phs.profile_name ASC
                ) AS family_rank
            FROM {_quote_identifier(anchor_alias)}.profile_horizon_scores AS phs
            INNER JOIN canonical_profiles AS cp
                ON {_profile_family_expr(alias="phs")} = cp.profile_family
            WHERE phs.run_id = {anchor_run_sql}
            {anchor_family_filter}
        ),
        anchor_scores AS (
            SELECT
                ranked.* EXCLUDE (family_rank),
                ROW_NUMBER() OVER (
                    PARTITION BY ranked.profile_family, ranked.horizon_name
                    ORDER BY ranked.score DESC NULLS LAST, ranked.symbol ASC
                ) AS profile_rank
            FROM anchor_scores_ranked AS ranked
            WHERE ranked.family_rank = 1
        ),
        current_raw AS (
            SELECT
                raw.*,
                {SYMBOL_KEY_SQL.format(alias="raw")} AS symbol_key
            FROM {_quote_identifier(current_alias)}.raw_scan_rows AS raw
            WHERE raw.run_id = {current_run_sql}
        ),
        anchor_raw AS (
            SELECT
                raw.*,
                {SYMBOL_KEY_SQL.format(alias="raw")} AS symbol_key
            FROM {_quote_identifier(anchor_alias)}.raw_scan_rows AS raw
            WHERE raw.run_id = {anchor_run_sql}
        ),
        joined AS (
            SELECT
                COALESCE(current_scores.symbol, anchor_scores.symbol) AS symbol,
                COALESCE(current_scores.profile_name, canonical_profiles.profile_name) AS profile_name,
                COALESCE(current_scores.profile_family, anchor_scores.profile_family) AS profile_family,
                anchor_scores.profile_name AS anchor_profile_name,
                COALESCE(current_scores.horizon_name, anchor_scores.horizon_name) AS horizon_name,
                COALESCE(current_scores.company, anchor_scores.company) AS company,
                COALESCE(current_scores.sector, anchor_scores.sector) AS sector,
                COALESCE(current_scores.industry, anchor_scores.industry) AS industry,
                COALESCE(current_scores.market_cap_basic, anchor_scores.market_cap_basic) AS market_cap_basic,
                current_scores.run_id IS NOT NULL AS in_current,
                anchor_scores.run_id IS NOT NULL AS in_anchor,
                current_scores.score AS current_score,
                current_scores.direction AS current_direction,
                current_scores.risk_adjusted_score AS current_risk_adjusted_score,
                current_scores.confidence AS current_confidence,
                current_scores.profile_rank AS current_rank,
                COALESCE(current_scores.close, current_raw.close) AS current_close,
                anchor_scores.score AS anchor_score,
                anchor_scores.direction AS anchor_direction,
                anchor_scores.risk_adjusted_score AS anchor_risk_adjusted_score,
                anchor_scores.confidence AS anchor_confidence,
                anchor_scores.profile_rank AS anchor_rank,
                COALESCE(anchor_scores.close, anchor_raw.close) AS anchor_close,
                {", ".join(current_raw_perf)},
                {", ".join(anchor_raw_perf)}
            FROM current_scores
            FULL OUTER JOIN anchor_scores
                ON current_scores.symbol_key = anchor_scores.symbol_key
                AND current_scores.profile_family = anchor_scores.profile_family
                AND current_scores.horizon_name = anchor_scores.horizon_name
            LEFT JOIN canonical_profiles
                ON anchor_scores.profile_family = canonical_profiles.profile_family
            LEFT JOIN current_raw
                ON COALESCE(current_scores.symbol_key, anchor_scores.symbol_key) = current_raw.symbol_key
            LEFT JOIN anchor_raw
                ON COALESCE(current_scores.symbol_key, anchor_scores.symbol_key) = anchor_raw.symbol_key
        )
        SELECT
            {analysis_id_sql} AS backwards_analysis_id,
            {anchor_name_sql} AS anchor_name,
            profile_name,
            profile_family,
            anchor_profile_name,
            COALESCE(
                LOWER(TRIM(CAST(profile_name AS VARCHAR)))
                    = LOWER(TRIM(CAST(anchor_profile_name AS VARCHAR))),
                FALSE
            ) AS profile_version_exact_match,
            horizon_name,
            symbol,
            company,
            sector,
            industry,
            market_cap_basic,
            in_current,
            in_anchor,
            current_score,
            current_direction,
            current_risk_adjusted_score,
            current_confidence,
            current_rank,
            current_close,
            anchor_score,
            anchor_direction,
            anchor_risk_adjusted_score,
            anchor_confidence,
            anchor_rank,
            anchor_close,
            current_score - anchor_score AS score_delta,
            anchor_rank - current_rank AS rank_delta,
            CASE
                WHEN anchor_close IS NULL OR anchor_close = 0 OR current_close IS NULL THEN NULL
                ELSE ((current_close - anchor_close) / anchor_close) * 100.0
            END AS close_delta_pct,
            COALESCE(current_direction, '') <> COALESCE(anchor_direction, '') AS direction_changed,
            current_perf_5d,
            anchor_perf_5d,
            current_perf_5d - anchor_perf_5d AS perf_5d_delta,
            current_perf_w,
            anchor_perf_w,
            current_perf_w - anchor_perf_w AS perf_w_delta,
            current_perf_1m,
            anchor_perf_1m,
            current_perf_1m - anchor_perf_1m AS perf_1m_delta,
            current_perf_ytd,
            anchor_perf_ytd,
            current_perf_ytd - anchor_perf_ytd AS perf_ytd_delta
        FROM joined
    """


def _build_consensus_horizon_delta_insert_sql(
    *,
    layout: BackwardsAnalysisLayout,
    anchor: ResolvedAnchor,
    current: RunIndexEntry,
    current_alias: str,
    anchor_alias: str,
    current_raw_columns: set[str],
    anchor_raw_columns: set[str],
) -> str:
    analysis_id_sql = _quote_sql_literal(layout.backwards_analysis_id)
    anchor_name_sql = _quote_sql_literal(anchor.spec.name)
    current_run_sql = _quote_sql_literal(current.run_id)
    anchor_run_sql = _quote_sql_literal(anchor.entry.run_id)

    current_raw_perf = _build_raw_perf_select_columns(
        raw_alias="current_raw",
        prefix="current_",
        available_columns=current_raw_columns,
    )
    anchor_raw_perf = _build_raw_perf_select_columns(
        raw_alias="anchor_raw",
        prefix="anchor_",
        available_columns=anchor_raw_columns,
    )

    return f"""
        INSERT INTO backwards_consensus_horizon_deltas
        WITH current_scores AS (
            SELECT
                chs.*,
                {SYMBOL_KEY_SQL.format(alias="chs")} AS symbol_key,
                ROW_NUMBER() OVER (
                    PARTITION BY chs.horizon_name
                    ORDER BY chs.score DESC NULLS LAST, chs.symbol ASC
                ) AS consensus_rank
            FROM {_quote_identifier(current_alias)}.consensus_horizon_scores AS chs
            WHERE chs.run_id = {current_run_sql}
        ),
        anchor_scores AS (
            SELECT
                chs.*,
                {SYMBOL_KEY_SQL.format(alias="chs")} AS symbol_key,
                ROW_NUMBER() OVER (
                    PARTITION BY chs.horizon_name
                    ORDER BY chs.score DESC NULLS LAST, chs.symbol ASC
                ) AS consensus_rank
            FROM {_quote_identifier(anchor_alias)}.consensus_horizon_scores AS chs
            WHERE chs.run_id = {anchor_run_sql}
        ),
        current_raw AS (
            SELECT raw.*, {SYMBOL_KEY_SQL.format(alias="raw")} AS symbol_key
            FROM {_quote_identifier(current_alias)}.raw_scan_rows AS raw
            WHERE raw.run_id = {current_run_sql}
        ),
        anchor_raw AS (
            SELECT raw.*, {SYMBOL_KEY_SQL.format(alias="raw")} AS symbol_key
            FROM {_quote_identifier(anchor_alias)}.raw_scan_rows AS raw
            WHERE raw.run_id = {anchor_run_sql}
        ),
        joined AS (
            SELECT
                COALESCE(current_scores.symbol, anchor_scores.symbol) AS symbol,
                COALESCE(current_scores.horizon_name, anchor_scores.horizon_name) AS horizon_name,
                COALESCE(current_scores.company, anchor_scores.company) AS company,
                COALESCE(current_scores.sector, anchor_scores.sector) AS sector,
                COALESCE(current_scores.industry, anchor_scores.industry) AS industry,
                COALESCE(current_scores.market_cap, anchor_scores.market_cap) AS market_cap,
                current_scores.run_id IS NOT NULL AS in_current,
                anchor_scores.run_id IS NOT NULL AS in_anchor,
                current_scores.score AS current_score,
                current_scores.direction AS current_direction,
                current_scores.risk_adjusted_score AS current_risk_adjusted_score,
                current_scores.confidence AS current_confidence,
                current_scores.consensus_rank AS current_rank,
                current_raw.close AS current_close,
                anchor_scores.score AS anchor_score,
                anchor_scores.direction AS anchor_direction,
                anchor_scores.risk_adjusted_score AS anchor_risk_adjusted_score,
                anchor_scores.confidence AS anchor_confidence,
                anchor_scores.consensus_rank AS anchor_rank,
                anchor_raw.close AS anchor_close,
                {", ".join(current_raw_perf)},
                {", ".join(anchor_raw_perf)}
            FROM current_scores
            FULL OUTER JOIN anchor_scores
                ON current_scores.symbol_key = anchor_scores.symbol_key
                AND current_scores.horizon_name = anchor_scores.horizon_name
            LEFT JOIN current_raw
                ON COALESCE(current_scores.symbol_key, anchor_scores.symbol_key) = current_raw.symbol_key
            LEFT JOIN anchor_raw
                ON COALESCE(current_scores.symbol_key, anchor_scores.symbol_key) = anchor_raw.symbol_key
        )
        SELECT
            {analysis_id_sql} AS backwards_analysis_id,
            {anchor_name_sql} AS anchor_name,
            horizon_name,
            symbol,
            company,
            sector,
            industry,
            market_cap,
            in_current,
            in_anchor,
            current_score,
            current_direction,
            current_risk_adjusted_score,
            current_confidence,
            current_rank,
            current_close,
            anchor_score,
            anchor_direction,
            anchor_risk_adjusted_score,
            anchor_confidence,
            anchor_rank,
            anchor_close,
            current_score - anchor_score AS score_delta,
            anchor_rank - current_rank AS rank_delta,
            CASE
                WHEN anchor_close IS NULL OR anchor_close = 0 OR current_close IS NULL THEN NULL
                ELSE ((current_close - anchor_close) / anchor_close) * 100.0
            END AS close_delta_pct,
            COALESCE(current_direction, '') <> COALESCE(anchor_direction, '') AS direction_changed,
            current_perf_5d,
            anchor_perf_5d,
            current_perf_5d - anchor_perf_5d AS perf_5d_delta,
            current_perf_w,
            anchor_perf_w,
            current_perf_w - anchor_perf_w AS perf_w_delta,
            current_perf_1m,
            anchor_perf_1m,
            current_perf_1m - anchor_perf_1m AS perf_1m_delta,
            current_perf_ytd,
            anchor_perf_ytd,
            current_perf_ytd - anchor_perf_ytd AS perf_ytd_delta
        FROM joined
    """


def _build_component_delta_insert_sql(
    *,
    layout: BackwardsAnalysisLayout,
    anchor: ResolvedAnchor,
    current: RunIndexEntry,
    include_profiles: set[str],
    canonical_profiles_by_family: dict[str, str],
    current_alias: str,
    anchor_alias: str,
) -> str:
    analysis_id_sql = _quote_sql_literal(layout.backwards_analysis_id)
    anchor_name_sql = _quote_sql_literal(anchor.spec.name)
    current_run_sql = _quote_sql_literal(current.run_id)
    anchor_run_sql = _quote_sql_literal(anchor.entry.run_id)
    current_profile_filter = _profile_filter_sql(
        alias="current_components", include_profiles=include_profiles
    ).replace("current_components", "pc")
    anchor_family_filter = _profile_family_filter_sql(
        alias="pc",
        profile_families=set(canonical_profiles_by_family),
    )
    canonical_profiles_cte = _build_canonical_profiles_values_sql(
        canonical_profiles_by_family
    )

    component_delta_columns = []
    for component_name in COMPONENT_FIELDS:
        component_delta_columns.extend(
            [
                f"current_components.{component_name} AS current_{component_name}",
                f"anchor_components.{component_name} AS anchor_{component_name}",
                (
                    f"current_components.{component_name} - anchor_components.{component_name} "
                    f"AS {component_name}_delta"
                ),
            ]
        )

    return f"""
        INSERT INTO backwards_profile_component_deltas
        WITH canonical_profiles AS (
            {canonical_profiles_cte}
        ),
        current_components AS (
            SELECT
                pc.*,
                {SYMBOL_KEY_SQL.format(alias="pc")} AS symbol_key,
                {_profile_family_expr(alias="pc")} AS profile_family
            FROM {_quote_identifier(current_alias)}.profile_components AS pc
            INNER JOIN canonical_profiles AS cp
                ON {_profile_family_expr(alias="pc")} = cp.profile_family
               AND LOWER(TRIM(CAST(pc.profile_name AS VARCHAR))) = cp.profile_name
            WHERE pc.run_id = {current_run_sql}
            {current_profile_filter}
        ),
        anchor_components_ranked AS (
            SELECT
                pc.*,
                {SYMBOL_KEY_SQL.format(alias="pc")} AS symbol_key,
                {_profile_family_expr(alias="pc")} AS profile_family,
                cp.profile_name AS canonical_profile_name,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        {SYMBOL_KEY_SQL.format(alias="pc")},
                        cp.profile_family
                    ORDER BY
                        {_anchor_profile_match_priority_sql(alias="pc")},
                        {_profile_version_rank_expr(alias="pc")} DESC NULLS LAST,
                        pc.profile_name ASC
                ) AS family_rank
            FROM {_quote_identifier(anchor_alias)}.profile_components AS pc
            INNER JOIN canonical_profiles AS cp
                ON {_profile_family_expr(alias="pc")} = cp.profile_family
            WHERE pc.run_id = {anchor_run_sql}
            {anchor_family_filter}
        ),
        anchor_components AS (
            SELECT * EXCLUDE (family_rank)
            FROM anchor_components_ranked
            WHERE family_rank = 1
        )
        SELECT
            {analysis_id_sql} AS backwards_analysis_id,
            {anchor_name_sql} AS anchor_name,
            COALESCE(current_components.profile_name, canonical_profiles.profile_name) AS profile_name,
            COALESCE(current_components.profile_family, anchor_components.profile_family) AS profile_family,
            anchor_components.profile_name AS anchor_profile_name,
            COALESCE(
                LOWER(TRIM(CAST(current_components.profile_name AS VARCHAR)))
                    = LOWER(TRIM(CAST(anchor_components.profile_name AS VARCHAR))),
                FALSE
            ) AS profile_version_exact_match,
            COALESCE(current_components.symbol, anchor_components.symbol) AS symbol,
            COALESCE(current_components.company, anchor_components.company) AS company,
            COALESCE(current_components.sector, anchor_components.sector) AS sector,
            COALESCE(current_components.industry, anchor_components.industry) AS industry,
            current_components.run_id IS NOT NULL AS in_current,
            anchor_components.run_id IS NOT NULL AS in_anchor,
            {", ".join(component_delta_columns)},
            current_components.close AS current_close,
            anchor_components.close AS anchor_close,
            CASE
                WHEN anchor_components.close IS NULL
                    OR anchor_components.close = 0
                    OR current_components.close IS NULL THEN NULL
                ELSE ((current_components.close - anchor_components.close) / anchor_components.close) * 100.0
            END AS close_delta_pct
        FROM current_components
        FULL OUTER JOIN anchor_components
            ON current_components.symbol_key = anchor_components.symbol_key
            AND current_components.profile_family = anchor_components.profile_family
        LEFT JOIN canonical_profiles
            ON anchor_components.profile_family = canonical_profiles.profile_family
    """


def _build_anchor_snapshot_insert_sql(
    *,
    layout: BackwardsAnalysisLayout,
    anchor: ResolvedAnchor | None,
    current: RunIndexEntry,
    is_current: bool,
    include_profiles: set[str],
    canonical_profiles_by_family: dict[str, str],
    source_alias: str,
    anchor_name: str,
    snapshot_label: str,
) -> str:
    analysis_id_sql = _quote_sql_literal(layout.backwards_analysis_id)
    anchor_name_sql = _quote_sql_literal(anchor_name)
    run_sql = _quote_sql_literal(
        current.run_id if is_current else anchor.entry.run_id  # type: ignore[union-attr]
    )
    snapshot_label_sql = _quote_sql_literal(snapshot_label)
    canonical_profiles_cte = _build_canonical_profiles_values_sql(
        canonical_profiles_by_family
    )
    if is_current:
        source_profile_filter = _profile_filter_sql(
            alias="phs", include_profiles=include_profiles
        )
        canonical_name_match_sql = (
            " AND LOWER(TRIM(CAST(phs.profile_name AS VARCHAR))) = "
            "canonical_profiles.profile_name"
        )
        family_partition_sql = f"{_profile_family_expr(alias='phs')}"
        family_rank_order_sql = f"""
                        {_profile_version_rank_expr(alias="phs")} DESC NULLS LAST,
                        phs.score DESC NULLS LAST,
                        phs.profile_name ASC
        """.strip()
    else:
        source_profile_filter = _profile_family_filter_sql(
            alias="phs",
            profile_families=set(canonical_profiles_by_family),
        )
        canonical_name_match_sql = ""
        family_partition_sql = "canonical_profiles.profile_family"
        family_rank_order_sql = f"""
                        {_anchor_profile_match_priority_sql(alias="phs", canonical_alias="canonical_profiles")},
                        {_profile_version_rank_expr(alias="phs")} DESC NULLS LAST,
                        phs.score DESC NULLS LAST,
                        phs.profile_name ASC
        """.strip()

    return f"""
        INSERT INTO backwards_anchor_snapshots
        WITH canonical_profiles AS (
            {canonical_profiles_cte}
        ),
        scored_ranked AS (
            SELECT
                phs.*,
                {SYMBOL_KEY_SQL.format(alias="phs")} AS symbol_key,
                {_profile_family_expr(alias="phs")} AS profile_family,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        {SYMBOL_KEY_SQL.format(alias="phs")},
                        {family_partition_sql},
                        phs.horizon_name
                    ORDER BY
                        {family_rank_order_sql}
                ) AS family_rank
            FROM {_quote_identifier(source_alias)}.profile_horizon_scores AS phs
            INNER JOIN canonical_profiles
                ON {_profile_family_expr(alias="phs")} = canonical_profiles.profile_family
            WHERE phs.run_id = {run_sql}
            {source_profile_filter}
            {canonical_name_match_sql}
        ),
        scored AS (
            SELECT
                ranked.* EXCLUDE (family_rank),
                canonical_profiles.profile_name AS canonical_profile_name,
                ranked.profile_name AS source_profile_name,
                ROW_NUMBER() OVER (
                    PARTITION BY canonical_profiles.profile_name, ranked.horizon_name
                    ORDER BY ranked.score DESC NULLS LAST, ranked.symbol ASC
                ) AS profile_rank
            FROM scored_ranked AS ranked
            INNER JOIN canonical_profiles
                ON ranked.profile_family = canonical_profiles.profile_family
            WHERE ranked.family_rank = 1
        ),
        raw_rows AS (
            SELECT raw.*, {SYMBOL_KEY_SQL.format(alias="raw")} AS symbol_key
            FROM {_quote_identifier(source_alias)}.raw_scan_rows AS raw
            WHERE raw.run_id = {run_sql}
        )
        SELECT
            {analysis_id_sql} AS backwards_analysis_id,
            {anchor_name_sql} AS anchor_name,
            {str(is_current).lower()} AS is_current,
            {run_sql} AS run_id,
            {snapshot_label_sql} AS snapshot_label,
            scored.canonical_profile_name AS profile_name,
            scored.profile_family,
            CASE
                WHEN scored.source_profile_name = scored.canonical_profile_name THEN NULL
                ELSE scored.source_profile_name
            END AS anchor_profile_name,
            scored.horizon_name,
            scored.symbol,
            scored.company,
            scored.sector,
            scored.industry,
            scored.market_cap_basic,
            scored.score,
            scored.direction,
            scored.confidence,
            scored.risk_adjusted_score,
            scored.risk_tier,
            COALESCE(scored.close, raw_rows.close) AS close,
            TRY_CAST(raw_rows."Perf.5D" AS DOUBLE) AS perf_5d,
            TRY_CAST(raw_rows."Perf.W" AS DOUBLE) AS perf_w,
            TRY_CAST(raw_rows."Perf.1M" AS DOUBLE) AS perf_1m,
            TRY_CAST(raw_rows."Perf.YTD" AS DOUBLE) AS perf_ytd,
            scored.profile_rank
        FROM scored
        LEFT JOIN raw_rows
            ON scored.symbol_key = raw_rows.symbol_key
    """


def _table_exists_in_attached(conn: Any, alias: str, table_name: str) -> bool:
    result = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_catalog = ?
          AND table_name = ?
        """,
        [alias, table_name],
    ).fetchone()
    return bool(result and result[0])


def _discover_anchor_profile_family_bindings(
    conn: Any,
    *,
    anchor_alias: str,
    anchor_run_id: str,
    canonical_profiles_by_family: dict[str, str],
) -> list[dict[str, str]]:
    if not canonical_profiles_by_family:
        return []

    families_sql = ", ".join(
        _quote_sql_literal(family) for family in sorted(canonical_profiles_by_family)
    )
    rows = conn.execute(
        f"""
        SELECT
            {_profile_family_expr(alias="phs")} AS profile_family,
            LOWER(TRIM(CAST(phs.profile_name AS VARCHAR))) AS anchor_profile_name,
            {_profile_version_rank_expr(alias="phs")} AS version_rank
        FROM {_quote_identifier(anchor_alias)}.profile_horizon_scores AS phs
        WHERE phs.run_id = ?
          AND {_profile_family_expr(alias="phs")} IN ({families_sql})
        """,
        [anchor_run_id],
    ).fetchall()

    candidates_by_family: dict[str, list[tuple[str, int]]] = {}
    for row in rows:
        if row is None or row[0] is None or row[1] is None:
            continue
        family = str(row[0])
        anchor_profile_name = str(row[1])
        version_rank = int(row[2] or 0)
        candidates_by_family.setdefault(family, []).append(
            (anchor_profile_name, version_rank)
        )

    def _pick_anchor_profile_name(
        *,
        family: str,
        canonical_name: str,
    ) -> str | None:
        candidates = candidates_by_family.get(family)
        if not candidates:
            return None

        def _sort_key(item: tuple[str, int]) -> tuple[int, int, str]:
            anchor_profile_name, version_rank = item
            if anchor_profile_name == canonical_name:
                priority = 0
            elif anchor_profile_name == family:
                priority = 1
            else:
                priority = 2
            return (priority, -version_rank, anchor_profile_name)

        return sorted(candidates, key=_sort_key)[0][0]

    bindings: list[dict[str, str]] = []
    for family, canonical_name in sorted(canonical_profiles_by_family.items()):
        anchor_name = _pick_anchor_profile_name(
            family=family,
            canonical_name=canonical_name,
        )
        bindings.append(
            {
                "profile_family": family,
                "current_profile_name": canonical_name,
                "anchor_profile_name": anchor_name or "(not present in anchor run)",
                "match_mode": (
                    "exact"
                    if anchor_name == canonical_name
                    else (
                        "unversioned_fallback"
                        if anchor_name == family
                        else "family_fallback" if anchor_name else "missing"
                    )
                ),
            }
        )
    return bindings


def _process_anchor_comparison(
    store: BackwardsPredictionDuckDBStore,
    *,
    layout: BackwardsAnalysisLayout,
    current: RunIndexEntry,
    anchor: ResolvedAnchor,
    include_profiles: set[str],
    canonical_profiles_by_family: dict[str, str],
    build_options: BackwardsAnalysisBuildOptions,
) -> dict[str, Any]:
    conn = store.conn
    current_alias = "src_current"
    anchor_alias = "src_anchor"
    row_counts: dict[str, Any] = {
        "profile_horizon_deltas": 0,
        "consensus_horizon_deltas": 0,
        "profile_component_deltas": 0,
        "anchor_snapshots": 0,
        "profile_family_bindings": [],
    }

    _attach_source_database(
        conn, database_path=current.database_path, alias=current_alias
    )
    if anchor.entry.database_path.resolve() == current.database_path.resolve():
        anchor_alias = current_alias
    else:
        _attach_source_database(
            conn, database_path=anchor.entry.database_path, alias=anchor_alias
        )

    try:
        current_raw_columns = _describe_table_columns(
            conn, f"{_quote_identifier(current_alias)}.raw_scan_rows"
        )
        anchor_raw_columns = (
            current_raw_columns
            if anchor_alias == current_alias
            else _describe_table_columns(
                conn, f"{_quote_identifier(anchor_alias)}.raw_scan_rows"
            )
        )

        row_counts["profile_family_bindings"] = (
            _discover_anchor_profile_family_bindings(
                conn,
                anchor_alias=anchor_alias,
                anchor_run_id=anchor.entry.run_id,
                canonical_profiles_by_family=canonical_profiles_by_family,
            )
        )

        if build_options.include_deltas:
            profile_sql = _build_profile_horizon_delta_insert_sql(
                layout=layout,
                anchor=anchor,
                current=current,
                include_profiles=include_profiles,
                canonical_profiles_by_family=canonical_profiles_by_family,
                current_alias=current_alias,
                anchor_alias=anchor_alias,
                current_raw_columns=current_raw_columns,
                anchor_raw_columns=anchor_raw_columns,
            )
            conn.execute(profile_sql)
            row_counts["profile_horizon_deltas"] = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM backwards_profile_horizon_deltas
                    WHERE backwards_analysis_id = ?
                      AND anchor_name = ?
                    """,
                    [layout.backwards_analysis_id, anchor.spec.name],
                ).fetchone()[0]
            )

        if (
            build_options.include_deltas
            and build_options.include_consensus
            and _table_exists_in_attached(
                conn, current_alias, "consensus_horizon_scores"
            )
        ):
            consensus_sql = _build_consensus_horizon_delta_insert_sql(
                layout=layout,
                anchor=anchor,
                current=current,
                current_alias=current_alias,
                anchor_alias=anchor_alias,
                current_raw_columns=current_raw_columns,
                anchor_raw_columns=anchor_raw_columns,
            )
            conn.execute(consensus_sql)
            row_counts["consensus_horizon_deltas"] = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM backwards_consensus_horizon_deltas
                    WHERE backwards_analysis_id = ?
                      AND anchor_name = ?
                    """,
                    [layout.backwards_analysis_id, anchor.spec.name],
                ).fetchone()[0]
            )

        if (
            build_options.include_deltas
            and build_options.include_components
            and _table_exists_in_attached(conn, current_alias, "profile_components")
        ):
            component_sql = _build_component_delta_insert_sql(
                layout=layout,
                anchor=anchor,
                current=current,
                include_profiles=include_profiles,
                canonical_profiles_by_family=canonical_profiles_by_family,
                current_alias=current_alias,
                anchor_alias=anchor_alias,
            )
            conn.execute(component_sql)
            row_counts["profile_component_deltas"] = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM backwards_profile_component_deltas
                    WHERE backwards_analysis_id = ?
                      AND anchor_name = ?
                    """,
                    [layout.backwards_analysis_id, anchor.spec.name],
                ).fetchone()[0]
            )

        if build_options.include_snapshots:
            snapshot_sql = _build_anchor_snapshot_insert_sql(
                layout=layout,
                anchor=anchor,
                current=current,
                is_current=False,
                include_profiles=include_profiles,
                canonical_profiles_by_family=canonical_profiles_by_family,
                source_alias=anchor_alias,
                anchor_name=anchor.spec.name,
                snapshot_label=anchor.entry.snapshot_label,
            )
            conn.execute(snapshot_sql)
            row_counts["anchor_snapshots"] += int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM backwards_anchor_snapshots
                    WHERE backwards_analysis_id = ?
                      AND anchor_name = ?
                      AND is_current = FALSE
                    """,
                    [layout.backwards_analysis_id, anchor.spec.name],
                ).fetchone()[0]
            )
    finally:
        if anchor_alias != current_alias:
            _detach_source_database(conn, alias=anchor_alias)
        _detach_source_database(conn, alias=current_alias)

    return row_counts


def _insert_current_anchor_snapshots(
    store: BackwardsPredictionDuckDBStore,
    *,
    layout: BackwardsAnalysisLayout,
    current: RunIndexEntry,
    include_profiles: set[str],
    canonical_profiles_by_family: dict[str, str],
) -> int:
    conn = store.conn
    current_alias = "src_current_snapshot"
    _attach_source_database(
        conn, database_path=current.database_path, alias=current_alias
    )
    try:
        snapshot_sql = _build_anchor_snapshot_insert_sql(
            layout=layout,
            anchor=None,
            current=current,
            is_current=True,
            include_profiles=include_profiles,
            canonical_profiles_by_family=canonical_profiles_by_family,
            source_alias=current_alias,
            anchor_name="current",
            snapshot_label=current.snapshot_label,
        )
        conn.execute(snapshot_sql)
        return int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM backwards_anchor_snapshots
                WHERE backwards_analysis_id = ?
                  AND is_current = TRUE
                """,
                [layout.backwards_analysis_id],
            ).fetchone()[0]
        )
    finally:
        _detach_source_database(conn, alias=current_alias)


def _export_parquet_tables(
    store: BackwardsPredictionDuckDBStore, parquet_dir: Path
) -> None:
    parquet_dir.mkdir(parents=True, exist_ok=True)
    tables = [
        "backwards_analysis_runs",
        "backwards_analysis_anchors",
        "backwards_profile_horizon_deltas",
        "backwards_consensus_horizon_deltas",
        "backwards_profile_component_deltas",
        "backwards_anchor_snapshots",
    ]
    for table_name in tables:
        if not store._table_exists(table_name):
            continue
        output_path = parquet_dir / f"{table_name}.parquet"
        store.conn.execute(
            f"""
            COPY (SELECT * FROM {_quote_identifier(table_name)})
            TO {_quote_sql_literal(output_path.as_posix())}
            (FORMAT PARQUET)
            """
        )


def _write_overview_log(
    layout: BackwardsAnalysisLayout,
    *,
    current: RunIndexEntry,
    resolved_anchors: Sequence[ResolvedAnchor],
    anchor_row_counts: Sequence[dict[str, Any]],
    canonical_profiles_by_family: dict[str, str],
    view_names: Sequence[str],
    timing_seconds: dict[str, float],
) -> Path:
    lines = [
        "Backwards Prediction Analysis Overview",
        f"backwards_analysis_id: {layout.backwards_analysis_id}",
        f"created_at_utc: {layout.created_at_utc.isoformat()}",
        f"database_path: {layout.database_path.as_posix()}",
        "",
        "Current run:",
        f"  run_id: {current.run_id}",
        f"  created_at_utc: {current.created_at_utc.isoformat()}",
        f"  snapshot_label: {current.snapshot_label}",
        f"  database_path: {current.database_path.as_posix()}",
        "",
        "Profile family matching:",
        "  Comparisons join on profile family (version suffix stripped, e.g. breakout_long_v1 -> breakout_long).",
        "  Current canonical profile names:",
    ]
    for family, profile_name in sorted(canonical_profiles_by_family.items()):
        lines.append(f"    - {family}: {profile_name}")
    lines.append("")
    lines.append(f"Resolved anchors ({len(resolved_anchors)}):")
    for anchor, counts in zip(resolved_anchors, anchor_row_counts, strict=True):
        lines.extend(
            [
                f"  - {anchor.spec.name}",
                f"      run_id: {anchor.entry.run_id}",
                f"      snapshot_label: {anchor.entry.snapshot_label}",
                f"      resolution_method: {anchor.resolution_method}",
                f"      offset_days_from_current: {anchor.offset_days_from_current}",
                f"      profile_horizon_deltas: {counts.get('profile_horizon_deltas', 0)}",
                f"      consensus_horizon_deltas: {counts.get('consensus_horizon_deltas', 0)}",
                f"      profile_component_deltas: {counts.get('profile_component_deltas', 0)}",
            ]
        )
        bindings = counts.get("profile_family_bindings") or []
        if bindings:
            lines.append("      profile_family_bindings:")
            for binding in bindings:
                lines.append(
                    "        "
                    f"{binding['profile_family']}: current={binding['current_profile_name']}, "
                    f"anchor={binding['anchor_profile_name']} ({binding['match_mode']})"
                )
    lines.extend(
        [
            "",
            f"Starter views: {', '.join(view_names)}",
            "",
            "Timing (seconds):",
        ]
    )
    for key, value in timing_seconds.items():
        lines.append(f"  {key}: {value:.3f}")
    layout.overview_log_path.parent.mkdir(parents=True, exist_ok=True)
    layout.overview_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return layout.overview_log_path


def run_backwards_prediction_sparse_weekly_analysis(
    *,
    duckdb_runs_root: str | Path | None = None,
    current_run_id: str | None = None,
    current_database_path: str | Path | None = None,
    runs_per_week: int = 2,
    selection: str | None = None,
    iso_year: int | None = None,
    start_week: int | None = None,
    end_week: int | None = None,
    start_iso_week: tuple[int, int] | None = None,
    end_iso_week: tuple[int, int] | None = None,
    min_scan_data_count: int | None = None,
    anchor_min_scan_data_count: int | None = None,
    profile_suite_path: str | Path | None = None,
    include_profiles: Sequence[str] | None = None,
    include_consensus: bool = True,
    include_components: bool = True,
    output_dir: str | Path | None = None,
    backwards_analysis_id: str | None = None,
    export_parquet: bool = False,
    duckdb_threads: int | None = None,
    memory_gb: float | None = None,
) -> dict[str, Any]:
    """Backwards analysis from oldest indexed run through today with sparse weekly anchors.

      Anchors are chosen from the move-prediction DuckDB run index (``duckdb_runs/``),
      not raw CSV folders. By default every ISO week between the oldest eligible run
      and the current run contributes up to ``runs_per_week`` anchors on distinct days
      (2 = first/last run of the week, 3 = evenly spread).

    Week range (omit ``start_week`` to begin at the oldest indexed week)::

          # Oldest indexed week -> current (2 anchors/week)
          run_backwards_prediction_sparse_weekly_analysis(runs_per_week=2)

          # From ISO week 16 of 2026 -> current (2 anchors/week)
          run_backwards_prediction_sparse_weekly_analysis(
              iso_year=2026, start_week=16, runs_per_week=2,
          )

      ``min_scan_data_count`` filters which run is treated as *current* (latest live
      scan by default). ``anchor_min_scan_data_count`` (default ``None``) controls
      anchor eligibility separately so low-row CSV backfill weeks are still anchored.
    """

    resolved_selection = selection or _default_weekly_anchor_selection(runs_per_week)
    anchor_plan = resolve_weekly_progression_anchors(
        duckdb_runs_root=duckdb_runs_root,
        current_run_id=current_run_id,
        current_database_path=current_database_path,
        runs_per_week=runs_per_week,
        selection=resolved_selection,
        iso_year=iso_year,
        start_week=start_week,
        end_week=end_week,
        start_iso_week=start_iso_week,
        end_iso_week=end_iso_week,
        min_scan_data_count=min_scan_data_count,
        anchor_min_scan_data_count=anchor_min_scan_data_count,
    )

    profiles: list[str] | None
    if include_profiles is not None:
        profiles = list(include_profiles)
    elif profile_suite_path is not None:
        from data_analysis_scripts.trading_view_move_prediction_profile_config import (
            resolve_profile_suite,
        )

        suite = resolve_profile_suite(profile_suite_path)
        profiles = list(suite.get("profile_names", []))
    else:
        profiles = None

    result = run_backwards_prediction_analysis(
        current_run_id=current_run_id,
        current_database_path=current_database_path,
        anchors=anchor_plan["anchors"],
        duckdb_runs_root=duckdb_runs_root,
        include_profiles=profiles,
        include_consensus=include_consensus,
        include_components=include_components,
        min_scan_data_count=min_scan_data_count,
        anchor_min_scan_data_count=anchor_min_scan_data_count,
        output_dir=output_dir,
        backwards_analysis_id=backwards_analysis_id,
        export_parquet=export_parquet,
        duckdb_threads=duckdb_threads,
        memory_gb=memory_gb,
    )
    result["anchor_plan_summary"] = summarize_weekly_anchor_plan(anchor_plan)
    result["runs_per_week"] = runs_per_week
    result["selection"] = resolved_selection
    result["start_iso_week"] = anchor_plan.get("start_iso_week")
    result["end_iso_week"] = anchor_plan.get("end_iso_week")
    result["oldest_indexed_iso_week"] = anchor_plan.get("oldest_indexed_iso_week")
    return result


def run_backwards_prediction_analysis(
    *,
    current_run_id: str | None = None,
    current_database_path: str | Path | None = None,
    anchors: Sequence[AnchorSpec] | None = None,
    duckdb_runs_root: str | Path | None = None,
    include_profiles: Sequence[str] | None = None,
    include_consensus: bool = True,
    include_components: bool = True,
    include_performance_tracking: bool = False,
    min_scan_data_count: int | None = None,
    anchor_min_scan_data_count: int | None = None,
    output_dir: str | Path | None = None,
    backwards_analysis_id: str | None = None,
    export_parquet: bool = False,
    duckdb_threads: int | None = None,
    memory_gb: float | None = None,
    build_options: BackwardsAnalysisBuildOptions | None = None,
) -> dict[str, Any]:
    """Compare current move-prediction scores against selected historical anchor runs."""

    del include_performance_tracking  # reserved for a later extension

    started = time.perf_counter()
    root = (
        Path(duckdb_runs_root)
        if duckdb_runs_root is not None
        else DEFAULT_DUCKDB_RUNS_ROOT
    )
    layout = resolve_backwards_analysis_layout(
        output_dir=output_dir,
        backwards_analysis_id=backwards_analysis_id,
    )
    layout.run_dir.mkdir(parents=True, exist_ok=True)

    current_index = _build_run_index(
        duckdb_runs_root=root,
        min_scan_data_count=min_scan_data_count,
    )
    anchor_index = _build_run_index(
        duckdb_runs_root=root,
        min_scan_data_count=anchor_min_scan_data_count,
    )
    current = _resolve_current_run(
        run_index=current_index,
        current_run_id=current_run_id,
        current_database_path=current_database_path,
    )
    resolved_anchors = resolve_anchor_specs(
        run_index=anchor_index,
        current=current,
        anchors=anchors,
    )
    supported_profiles = _resolve_included_profiles(
        include_profiles,
        current.database_path,
        current.run_id,
    )
    canonical_profiles_by_family = canonical_profiles_per_family(supported_profiles)
    resolved_build_options = (
        BackwardsAnalysisBuildOptions(
            include_deltas=True,
            include_consensus=include_consensus,
            include_components=include_components,
            include_snapshots=True,
        )
        if build_options is None
        else BackwardsAnalysisBuildOptions(
            include_deltas=build_options.include_deltas,
            include_consensus=build_options.include_consensus and include_consensus,
            include_components=build_options.include_components and include_components,
            include_snapshots=build_options.include_snapshots,
        )
    )

    anchor_records = [
        {
            "backwards_analysis_id": layout.backwards_analysis_id,
            "anchor_name": anchor.spec.name,
            "anchor_run_id": anchor.entry.run_id,
            "anchor_created_at_utc": anchor.entry.created_at_utc,
            "anchor_snapshot_label": anchor.entry.snapshot_label,
            "source_database_path": anchor.entry.database_path.as_posix(),
            "resolution_method": anchor.resolution_method,
            "resolution_spec_json": json.dumps(
                {
                    key: value
                    for key, value in asdict(anchor.spec).items()
                    if value is not None
                },
                default=str,
            ),
            "offset_days_from_current": anchor.offset_days_from_current,
            "runs_back_from_current": anchor.runs_back_from_current,
        }
        for anchor in resolved_anchors
    ]

    anchor_row_counts: list[dict[str, Any]] = []
    with BackwardsPredictionDuckDBStore(
        database_path=layout.database_path,
        parquet_dir=layout.parquet_dir if export_parquet else None,
        threads=duckdb_threads,
        memory_limit=memory_gb,
    ) as store:
        store.append_backwards_analysis_run(
            {
                "backwards_analysis_id": layout.backwards_analysis_id,
                "created_at_utc": layout.created_at_utc,
                "current_run_id": current.run_id,
                "current_created_at_utc": current.created_at_utc,
                "current_database_path": current.database_path.as_posix(),
                "current_snapshot_label": current.snapshot_label,
                "duckdb_runs_root": root.as_posix(),
                "anchor_count": len(resolved_anchors),
                "notes": "",
            }
        )
        store.append_backwards_analysis_anchors(anchor_records)

        for anchor in resolved_anchors:
            counts = _process_anchor_comparison(
                store,
                layout=layout,
                current=current,
                anchor=anchor,
                include_profiles=supported_profiles,
                canonical_profiles_by_family=canonical_profiles_by_family,
                build_options=resolved_build_options,
            )
            anchor_row_counts.append(counts)

        current_snapshot_count = 0
        if resolved_build_options.include_snapshots:
            current_snapshot_count = _insert_current_anchor_snapshots(
                store,
                layout=layout,
                current=current,
                include_profiles=supported_profiles,
                canonical_profiles_by_family=canonical_profiles_by_family,
            )

        view_names = store.create_starter_views()
        store.create_analysis_indexes()

        if export_parquet:
            _export_parquet_tables(store, layout.parquet_dir)

    timing_seconds = {
        "total": time.perf_counter() - started,
    }
    overview_log = _write_overview_log(
        layout,
        current=current,
        resolved_anchors=resolved_anchors,
        anchor_row_counts=anchor_row_counts,
        canonical_profiles_by_family=canonical_profiles_by_family,
        view_names=view_names,
        timing_seconds=timing_seconds,
    )

    return {
        "backwards_analysis_id": layout.backwards_analysis_id,
        "output_dir": layout.run_dir.as_posix(),
        "database_path": layout.database_path.as_posix(),
        "overview_log": overview_log.as_posix(),
        "current_run_id": current.run_id,
        "current_snapshot_label": current.snapshot_label,
        "canonical_profiles_by_family": canonical_profiles_by_family,
        "resolved_anchors": [
            {
                "anchor_name": anchor.spec.name,
                "run_id": anchor.entry.run_id,
                "snapshot_label": anchor.entry.snapshot_label,
                "resolution_method": anchor.resolution_method,
                "database_path": anchor.entry.database_path.as_posix(),
            }
            for anchor in resolved_anchors
        ],
        "anchor_row_counts": anchor_row_counts,
        "current_snapshot_count": current_snapshot_count,
        "view_names": view_names,
        "timing_seconds": timing_seconds,
    }


__all__ = [
    "AnchorSpec",
    "BackwardsAnalysisBuildOptions",
    "BackwardsAnalysisLayout",
    "DEFAULT_ANCHOR_PRESETS",
    "DEFAULT_BACKWARDS_ANALYSIS_ROOT",
    "ResolvedAnchor",
    "RunIndexEntry",
    "build_profile_family_variant_groups",
    "build_weekly_sample_anchors",
    "canonical_profiles_per_family",
    "profile_family",
    "resolve_anchor_profile_variant_names",
    "resolve_anchor_specs",
    "resolve_backwards_analysis_layout",
    "resolve_weekly_progression_anchors",
    "run_backwards_prediction_analysis",
    "run_backwards_prediction_dense_day_spacing_analysis",
    "run_backwards_prediction_sparse_weekly_analysis",
    "summarize_weekly_anchor_plan",
    "variant_names_for_family",
]
