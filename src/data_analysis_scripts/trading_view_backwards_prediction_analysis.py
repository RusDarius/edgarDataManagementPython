from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
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
        if existing is None or profile_version_number(normalized) > profile_version_number(
            existing
        ):
            canonical[family] = normalized
    return canonical


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
    target_date: date | str | None = None

    def __post_init__(self) -> None:
        if self.name is not None:
            return
        if self.preset:
            object.__setattr__(self, "name", self.preset)
        elif self.run_id:
            object.__setattr__(self, "name", f"run_{self.run_id[-12:]}")
        elif self.target_date is not None:
            object.__setattr__(self, "name", f"date_{self.target_date}")
        elif self.runs_back is not None:
            object.__setattr__(self, "name", f"runs_back_{self.runs_back}")
        elif self.offset_days is not None:
            object.__setattr__(self, "name", f"offset_days_{self.offset_days}")
        else:
            raise ValueError(
                "AnchorSpec requires name or a resolution field "
                "(preset, run_id, target_date, runs_back, offset_days)."
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
                if scan_data_count is None or int(scan_data_count) < min_scan_data_count:
                    continue
            entries.append(
                RunIndexEntry(
                    run_id=run_id,
                    created_at_utc=created_at_utc,
                    scan_data_count=int(scan_data_count)
                    if scan_data_count is not None
                    else None,
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
        candidates = [entry for entry in run_index if entry.database_path == resolved_path]
        if not candidates:
            raise ValueError(
                f"No runs found in current database path: {resolved_path}"
            )
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
        if entry.run_id != current.run_id
        and entry.created_at_utc <= target_time
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
                (candidate for candidate in run_index if candidate.run_id == normalized_run_id),
                None,
            )
            resolution_method = "explicit_run_id"
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
        return {profile.strip().lower() for profile in include_profiles if profile.strip()}
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
    values = ", ".join(_quote_sql_literal(profile) for profile in sorted(include_profiles))
    return (
        f" AND LOWER(TRIM(CAST({alias}.profile_name AS VARCHAR))) IN ({values})"
    )


def _profile_family_filter_sql(
    *,
    alias: str,
    profile_families: set[str],
) -> str:
    if not profile_families:
        return ""
    values = ", ".join(_quote_sql_literal(family) for family in sorted(profile_families))
    return f" AND {_profile_family_expr(alias=alias)} IN ({values})"


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
    profile_families = set(canonical_profiles_by_family.keys())
    current_profile_filter = _profile_filter_sql(
        alias="phs", include_profiles=include_profiles
    )
    anchor_family_filter = _profile_family_filter_sql(
        alias="phs", profile_families=profile_families
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
                ROW_NUMBER() OVER (
                    PARTITION BY
                        {SYMBOL_KEY_SQL.format(alias="phs")},
                        {_profile_family_expr(alias="phs")},
                        phs.horizon_name
                    ORDER BY
                        {_profile_version_rank_expr(alias="phs")} DESC NULLS LAST,
                        phs.score DESC NULLS LAST,
                        phs.profile_name ASC
                ) AS family_rank
            FROM {_quote_identifier(anchor_alias)}.profile_horizon_scores AS phs
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
    profile_families = set(canonical_profiles_by_family.keys())
    current_profile_filter = _profile_filter_sql(
        alias="current_components", include_profiles=include_profiles
    ).replace("current_components", "pc")
    anchor_family_filter = _profile_family_filter_sql(
        alias="anchor_components", profile_families=profile_families
    ).replace("anchor_components", "pc")
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
                ROW_NUMBER() OVER (
                    PARTITION BY
                        {SYMBOL_KEY_SQL.format(alias="pc")},
                        {_profile_family_expr(alias="pc")}
                    ORDER BY
                        {_profile_version_rank_expr(alias="pc")} DESC NULLS LAST,
                        pc.profile_name ASC
                ) AS family_rank
            FROM {_quote_identifier(anchor_alias)}.profile_components AS pc
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
    else:
        profile_families = set(canonical_profiles_by_family.keys())
        source_profile_filter = _profile_family_filter_sql(
            alias="phs", profile_families=profile_families
        )
        canonical_name_match_sql = ""

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
                        {_profile_family_expr(alias="phs")},
                        phs.horizon_name
                    ORDER BY
                        {_profile_version_rank_expr(alias="phs")} DESC NULLS LAST,
                        phs.score DESC NULLS LAST,
                        phs.profile_name ASC
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
            MIN(LOWER(TRIM(CAST(phs.profile_name AS VARCHAR)))) AS anchor_profile_name
        FROM {_quote_identifier(anchor_alias)}.profile_horizon_scores AS phs
        WHERE phs.run_id = ?
          AND {_profile_family_expr(alias="phs")} IN ({families_sql})
        GROUP BY 1
        ORDER BY 1
        """,
        [anchor_run_id],
    ).fetchall()
    discovered = {
        str(row[0]): str(row[1])
        for row in rows
        if row and row[0] is not None and row[1] is not None
    }
    bindings: list[dict[str, str]] = []
    for family, canonical_name in sorted(canonical_profiles_by_family.items()):
        anchor_name = discovered.get(family)
        bindings.append(
            {
                "profile_family": family,
                "current_profile_name": canonical_name,
                "anchor_profile_name": anchor_name or "(not present in anchor run)",
                "match_mode": (
                    "exact"
                    if anchor_name == canonical_name
                    else "family_fallback"
                    if anchor_name
                    else "missing"
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

    _attach_source_database(conn, database_path=current.database_path, alias=current_alias)
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
        row_counts["profile_family_bindings"] = _discover_anchor_profile_family_bindings(
            conn,
            anchor_alias=anchor_alias,
            anchor_run_id=anchor.entry.run_id,
            canonical_profiles_by_family=canonical_profiles_by_family,
        )
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

        if build_options.include_consensus and _table_exists_in_attached(
            conn, current_alias, "consensus_horizon_scores"
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

        if build_options.include_components and _table_exists_in_attached(
            conn, current_alias, "profile_components"
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
    _attach_source_database(conn, database_path=current.database_path, alias=current_alias)
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


def _export_parquet_tables(store: BackwardsPredictionDuckDBStore, parquet_dir: Path) -> None:
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
    output_dir: str | Path | None = None,
    backwards_analysis_id: str | None = None,
    export_parquet: bool = False,
    duckdb_threads: int | None = None,
    memory_gb: float | None = None,
) -> dict[str, Any]:
    """Compare current move-prediction scores against selected historical anchor runs."""

    del include_performance_tracking  # reserved for a later extension

    started = time.perf_counter()
    root = Path(duckdb_runs_root) if duckdb_runs_root is not None else DEFAULT_DUCKDB_RUNS_ROOT
    layout = resolve_backwards_analysis_layout(
        output_dir=output_dir,
        backwards_analysis_id=backwards_analysis_id,
    )
    layout.run_dir.mkdir(parents=True, exist_ok=True)

    run_index = _build_run_index(
        duckdb_runs_root=root,
        min_scan_data_count=min_scan_data_count,
    )
    current = _resolve_current_run(
        run_index=run_index,
        current_run_id=current_run_id,
        current_database_path=current_database_path,
    )
    resolved_anchors = resolve_anchor_specs(
        run_index=run_index,
        current=current,
        anchors=anchors,
    )
    supported_profiles = _resolve_included_profiles(
        include_profiles,
        current.database_path,
        current.run_id,
    )
    canonical_profiles_by_family = canonical_profiles_per_family(supported_profiles)
    build_options = BackwardsAnalysisBuildOptions(
        include_consensus=include_consensus,
        include_components=include_components,
        include_snapshots=True,
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
                build_options=build_options,
            )
            anchor_row_counts.append(counts)

        current_snapshot_count = 0
        if build_options.include_snapshots:
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
    "BackwardsAnalysisLayout",
    "DEFAULT_ANCHOR_PRESETS",
    "DEFAULT_BACKWARDS_ANALYSIS_ROOT",
    "ResolvedAnchor",
    "RunIndexEntry",
    "canonical_profiles_per_family",
    "profile_family",
    "resolve_anchor_specs",
    "resolve_backwards_analysis_layout",
    "run_backwards_prediction_analysis",
]
