from __future__ import annotations

import csv
import html as html_module
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    AnchorSpec,
    BackwardsAnalysisBuildOptions,
    DEFAULT_DUCKDB_RUNS_ROOT,
    profile_family as extract_profile_family,
    profile_version_number,
    resolve_weekly_progression_anchors,
    run_backwards_prediction_analysis,
    summarize_weekly_anchor_plan,
)
from data_analysis_scripts.trading_view_backwards_prediction_scout_report import (
    _fetch_comparable_anchor_counts,
    _fetch_persistent_risers,
    _fetch_price_aligned_improvers,
    _fetch_top_progressors,
    _load_anchor_names,
    _load_run_metadata,
    _order_anchors,
    _resolve_effective_primary_anchor,
)
from db.trading_view_backwards_prediction_duckdb import query_backwards_prediction_duckdb

DEFAULT_HORIZON_NAME = "weeks"
DEFAULT_RUNS_PER_WEEK = 2
DEFAULT_SELECTION = "first_last"
DEFAULT_MIN_SCAN_DATA_COUNT = 3000
DEFAULT_PRIMARY_ANCHOR = "last_week"
DEFAULT_TOP_N_HIGHLIGHTS = 30
DEFAULT_MIN_ABS_SCORE_DELTA = 0.35
DEFAULT_MIN_PERSISTENCE_ANCHORS = 2
COMPACT_TITLE_SYMBOL_THRESHOLD = 5
MAX_PLOT_SPEC_SUBTITLE_LINES = 14
DEFAULT_PLOT_HEIGHT = 720
DEFAULT_WATCHLIST_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "holdings_scoring"
    / "current_holdings.json"
)


@dataclass(frozen=True)
class ProgressionPoint:
    backwards_analysis_id: str
    point_time: datetime
    anchor_name: str
    is_current: bool
    run_id: str
    snapshot_label: str
    profile_name: str
    profile_family: str
    anchor_profile_name: str | None
    horizon_name: str
    symbol: str
    company: str | None
    sector: str | None
    industry: str | None
    score: float | None
    close: float | None
    profile_rank: int | None
    direction: str | None
    confidence: float | None


@dataclass(frozen=True)
class HighlightMarker:
    symbol: str
    profile_name: str
    highlight_type: str
    anchor_name: str
    point_time: datetime
    score: float | None
    score_delta: float | None
    label: str


@dataclass(frozen=True)
class ProgressionSymbolSpecLine:
    """One row in the plotted-symbol spec (request vs resolved label)."""

    plotted_label: str
    requested: str | None = None
    db_symbol: str | None = None
    company: str | None = None
    note: str | None = None


def build_symbol_set_slug(
    labels: Sequence[str],
    *,
    compact_threshold: int = COMPACT_TITLE_SYMBOL_THRESHOLD,
) -> str:
    normalized = [_slugify(label) for label in labels if _slugify(label)]
    if not normalized:
        return "symbols"
    if len(normalized) <= compact_threshold:
        return "_".join(normalized)
    return f"n{len(normalized)}__{normalized[0]}__{normalized[-1]}"


def build_progression_plot_title_and_spec(
    *,
    profile_name: str,
    horizon_name: str,
    symbol_lines: Sequence[ProgressionSymbolSpecLine],
    title_override: str | None = None,
    compact_threshold: int = COMPACT_TITLE_SYMBOL_THRESHOLD,
    include_chart_subtitle: bool = False,
) -> tuple[str, str, dict[str, Any]]:
    """
    Build a short chart title and a separate plotted-symbol spec section.

    When symbol count exceeds ``compact_threshold``, the title stays compact.
    Full symbol details are always written to plot_spec.json; the chart subtitle
    is optional (default off — use the Plotly legend instead).
    """
    ordered = list(symbol_lines)
    display_labels = [line.plotted_label for line in ordered]
    symbol_count = len(ordered)

    if title_override is not None:
        title = title_override
    elif symbol_count == 0:
        title = f"Backwards progression — {profile_name} · {horizon_name}"
    elif symbol_count <= compact_threshold:
        title = (
            f"Backwards progression: {', '.join(display_labels)} "
            f"({profile_name}, {horizon_name})"
        )
    else:
        title = (
            f"Backwards progression — {profile_name} · {horizon_name} · "
            f"{symbol_count} symbols"
        )

    spec_rows: list[dict[str, Any]] = []
    spec_text_lines: list[str] = []
    for line in ordered:
        row = {
            "requested": line.requested,
            "plotted_label": line.plotted_label,
            "db_symbol": line.db_symbol,
            "company": line.company,
            "note": line.note,
        }
        spec_rows.append(row)
        if line.requested and line.db_symbol and line.requested.upper() != line.db_symbol.upper():
            text = f"{line.requested} → {line.db_symbol}"
        elif line.requested and line.plotted_label and line.requested.upper() != _split_symbol_ticker(line.plotted_label):
            text = f"{line.requested} → {line.plotted_label}"
        else:
            text = line.plotted_label
        if line.company:
            text = f"{text} ({line.company})"
        if line.note:
            text = f"{text} [{line.note}]"
        spec_text_lines.append(text)

    if symbol_count == 0:
        spec_text = ""
    elif not include_chart_subtitle:
        spec_text = ""
    else:
        has_mapping_detail = any(
            (
                line.requested
                and line.db_symbol
                and line.requested.upper() != line.db_symbol.upper()
            )
            or line.note
            for line in ordered
        )
        if symbol_count <= compact_threshold and not has_mapping_detail:
            spec_text = ""
        else:
            shown = spec_text_lines[:MAX_PLOT_SPEC_SUBTITLE_LINES]
            header = f"Plotted symbols ({symbol_count}):"
            body = "<br>".join(shown)
            if len(spec_text_lines) > len(shown):
                remaining = len(spec_text_lines) - len(shown)
                body = f"{body}<br>… and {remaining} more (see plot_spec.json)"
            spec_text = f"{header}<br>{body}"

    spec_dict = {
        "profile_name": profile_name,
        "horizon_name": horizon_name,
        "symbol_count": symbol_count,
        "title": title,
        "symbols": spec_rows,
    }
    return title, spec_text, spec_dict


def _write_plot_spec_json(spec: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _slugify(value: Any) -> str:
    text = _normalize_text(value) or ""
    lowered = text.lower()
    return "".join(char if char.isalnum() else "_" for char in lowered).strip("_")


def _ensure_datetime_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = _normalize_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_symbol_candidates(symbols: Sequence[str] | None) -> list[str]:
    if not symbols:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        text = _normalize_text(symbol)
        if text is None:
            continue
        key = text.upper()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)
    return normalized


def _split_symbol_ticker(symbol: str) -> str:
    normalized = _normalize_text(symbol) or ""
    if ":" not in normalized:
        return normalized.upper()
    _, _, ticker = normalized.partition(":")
    return (ticker or normalized).upper()


def _symbol_match(symbol_value: str, requested_symbols: set[str]) -> bool:
    normalized = symbol_value.upper()
    ticker = _split_symbol_ticker(symbol_value)
    return normalized in requested_symbols or ticker in requested_symbols


@dataclass(frozen=True)
class ResolvedBackwardsProfileTarget:
    profile_family: str
    profile_name: str
    row_count: int = 0


def fetch_backwards_profile_catalog(
    database_path: str | Path,
    *,
    horizon_name: str = DEFAULT_HORIZON_NAME,
) -> list[ResolvedBackwardsProfileTarget]:
    """Distinct profile_name / profile_family pairs present in a backwards database."""
    rows = query_backwards_prediction_duckdb(
        Path(database_path),
        """
        SELECT profile_name,
            profile_family,
            COUNT(*) AS row_count
        FROM backwards_anchor_snapshots
        WHERE horizon_name = ?
        GROUP BY profile_name, profile_family
        ORDER BY profile_family, profile_name
        """,
        parameters=[horizon_name],
    )
    catalog: list[ResolvedBackwardsProfileTarget] = []
    for row in rows:
        profile_name = _normalize_text(row.get("profile_name"))
        profile_family_name = _normalize_text(row.get("profile_family"))
        if profile_name is None or profile_family_name is None:
            continue
        catalog.append(
            ResolvedBackwardsProfileTarget(
                profile_family=profile_family_name.lower(),
                profile_name=profile_name.lower(),
                row_count=int(row.get("row_count") or 0),
            )
        )
    return catalog


def _latest_profile_target_per_family(
    catalog: Sequence[ResolvedBackwardsProfileTarget],
) -> dict[str, ResolvedBackwardsProfileTarget]:
    grouped: dict[str, list[ResolvedBackwardsProfileTarget]] = {}
    for entry in catalog:
        grouped.setdefault(entry.profile_family, []).append(entry)

    latest: dict[str, ResolvedBackwardsProfileTarget] = {}
    for family, entries in grouped.items():
        latest[family] = max(
            entries,
            key=lambda item: (
                profile_version_number(item.profile_name),
                item.row_count,
                item.profile_name,
            ),
        )
    return latest


def _normalize_profile_family_candidates(
    values: Sequence[str] | None,
) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value)
        if text is None:
            continue
        family = extract_profile_family(text).lower()
        if family in seen:
            continue
        seen.add(family)
        normalized.append(family)
    return normalized


def _resolve_requested_profile_families(
    *,
    profile_family: str | None,
    profile_families: Sequence[str] | None,
    profile_names: Sequence[str] | None,
    profile_suite_path: str | Path | None,
) -> list[str]:
    families: list[str] = []
    if profile_suite_path is not None:
        from data_analysis_scripts.trading_view_move_prediction_profile_config import (
            resolve_profile_suite,
        )

        suite = resolve_profile_suite(profile_suite_path, list(profile_names) if profile_names else None)
        families.extend(
            extract_profile_family(name).lower() for name in suite.get("profile_names", [])
        )
    elif profile_names:
        families.extend(extract_profile_family(name).lower() for name in profile_names)
    if profile_families:
        families.extend(_normalize_profile_family_candidates(profile_families))
    if profile_family is not None:
        families.append(extract_profile_family(profile_family).lower())

    deduped: list[str] = []
    seen: set[str] = set()
    for family in families:
        if family in seen:
            continue
        seen.add(family)
        deduped.append(family)
    return deduped


def resolve_backwards_profile_targets(
    database_path: str | Path,
    *,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    profile_name: str | None = None,
    profile_family: str | None = None,
    profile_families: Sequence[str] | None = None,
    profile_names: Sequence[str] | None = None,
    profile_suite_path: str | Path | None = None,
) -> tuple[list[ResolvedBackwardsProfileTarget], list[str]]:
    """
    Resolve profile targets using the latest version per family found in a backwards DB.

    When a profile suite is provided, families are taken from the suite and each family
    is mapped to the highest-version profile_name present in backwards_anchor_snapshots.
    """
    catalog = fetch_backwards_profile_catalog(database_path, horizon_name=horizon_name)
    if not catalog:
        raise ValueError(
            f"No profile rows found in backwards_anchor_snapshots for horizon={horizon_name!r} "
            f"in {database_path}"
        )

    latest_by_family = _latest_profile_target_per_family(catalog)
    messages: list[str] = []

    if profile_name is not None:
        normalized_name = profile_name.strip().lower()
        matches = [entry for entry in catalog if entry.profile_name == normalized_name]
        if not matches:
            available = sorted({entry.profile_name for entry in catalog})
            raise ValueError(
                f"Profile {profile_name!r} not found in backwards database. "
                f"Available profiles: {available}"
            )
        best = max(
            matches,
            key=lambda item: (item.row_count, item.profile_name),
        )
        return [best], messages

    requested_families = _resolve_requested_profile_families(
        profile_family=profile_family,
        profile_families=profile_families,
        profile_names=profile_names,
        profile_suite_path=profile_suite_path,
    )
    if not requested_families:
        if len(latest_by_family) == 1:
            return [next(iter(latest_by_family.values()))], messages
        available = sorted(latest_by_family.keys())
        raise ValueError(
            "Specify profile_name, profile_family, profile_families, profile_names, "
            f"or profile_suite_path. Available families in backwards database: {available}"
        )

    resolved: list[ResolvedBackwardsProfileTarget] = []
    for family in requested_families:
        target = latest_by_family.get(family)
        if target is None:
            messages.append(
                f"Profile family {family!r} not present in backwards database; skipped."
            )
            continue
        resolved.append(target)

    if not resolved:
        available = sorted(latest_by_family.keys())
        detail = "; ".join(messages) if messages else "no families resolved"
        raise ValueError(
            f"No profile targets resolved for requested families. {detail} "
            f"Available families: {available}"
        )

    resolved.sort(key=lambda item: (item.profile_family, item.profile_name))
    return resolved, messages


def _load_progression_context(database_path: Path) -> dict[str, Any]:
    rows = query_backwards_prediction_duckdb(
        database_path,
        """
        SELECT backwards_analysis_id,
            current_run_id,
            current_snapshot_label,
            current_created_at_utc
        FROM backwards_analysis_runs
        LIMIT 1
        """,
    )
    if not rows:
        raise ValueError(f"No backwards_analysis_runs row found in {database_path}")
    return rows[0]


def extract_progression_series(
    database_path: str | Path,
    *,
    profile_name: str | None = None,
    profile_family: str | None = None,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    symbols: Sequence[str] | None = None,
    anchor_names: Sequence[str] | None = None,
    include_current: bool = True,
) -> list[ProgressionPoint]:
    resolved_db = Path(database_path)
    context = _load_progression_context(resolved_db)
    analysis_id = str(context["backwards_analysis_id"])
    normalized_symbols = set(_normalize_symbol_candidates(symbols))
    where_clauses = [
        "s.backwards_analysis_id = ?",
        "s.horizon_name = ?",
    ]
    params: list[Any] = [analysis_id, horizon_name]

    if profile_name is not None:
        where_clauses.append("LOWER(TRIM(s.profile_name)) = ?")
        params.append(profile_name.strip().lower())
    if profile_family is not None:
        where_clauses.append("LOWER(TRIM(s.profile_family)) = ?")
        params.append(profile_family.strip().lower())

    if anchor_names:
        placeholders = ", ".join("?" for _ in anchor_names)
        if include_current:
            where_clauses.append(f"(s.is_current OR s.anchor_name IN ({placeholders}))")
        else:
            where_clauses.append(f"(NOT s.is_current AND s.anchor_name IN ({placeholders}))")
        params.extend(anchor_names)
    elif not include_current:
        where_clauses.append("NOT s.is_current")

    rows = query_backwards_prediction_duckdb(
        resolved_db,
        f"""
        SELECT s.backwards_analysis_id,
            s.anchor_name,
            s.is_current,
            s.run_id,
            s.snapshot_label,
            s.profile_name,
            s.profile_family,
            s.anchor_profile_name,
            s.horizon_name,
            s.symbol,
            s.company,
            s.sector,
            s.industry,
            s.score,
            s.close,
            s.profile_rank,
            s.direction,
            s.confidence,
            runs.current_created_at_utc,
            anchors.anchor_created_at_utc
        FROM backwards_anchor_snapshots AS s
        INNER JOIN backwards_analysis_runs AS runs
            ON s.backwards_analysis_id = runs.backwards_analysis_id
        LEFT JOIN backwards_analysis_anchors AS anchors
            ON s.backwards_analysis_id = anchors.backwards_analysis_id
            AND s.anchor_name = anchors.anchor_name
        WHERE {" AND ".join(where_clauses)}
        ORDER BY s.profile_name,
            s.symbol,
            COALESCE(anchors.anchor_created_at_utc, runs.current_created_at_utc),
            s.anchor_name
        """,
        parameters=params,
    )

    points: list[ProgressionPoint] = []
    for row in rows:
        symbol = _normalize_text(row.get("symbol"))
        if symbol is None:
            continue
        if normalized_symbols and not _symbol_match(symbol, normalized_symbols):
            continue

        is_current = bool(row.get("is_current"))
        point_time = _ensure_datetime_utc(
            row.get("current_created_at_utc")
            if is_current
            else row.get("anchor_created_at_utc")
        )
        if point_time is None:
            continue

        points.append(
            ProgressionPoint(
                backwards_analysis_id=analysis_id,
                point_time=point_time,
                anchor_name=str(row.get("anchor_name") or "current"),
                is_current=is_current,
                run_id=str(row.get("run_id") or ""),
                snapshot_label=str(row.get("snapshot_label") or ""),
                profile_name=str(row.get("profile_name") or ""),
                profile_family=str(row.get("profile_family") or ""),
                anchor_profile_name=_normalize_text(row.get("anchor_profile_name")),
                horizon_name=str(row.get("horizon_name") or ""),
                symbol=symbol,
                company=_normalize_text(row.get("company")),
                sector=_normalize_text(row.get("sector")),
                industry=_normalize_text(row.get("industry")),
                score=_to_float(row.get("score")),
                close=_to_float(row.get("close")),
                profile_rank=_to_int(row.get("profile_rank")),
                direction=_normalize_text(row.get("direction")),
                confidence=_to_float(row.get("confidence")),
            )
        )

    points.sort(
        key=lambda point: (
            point.profile_name,
            point.symbol,
            point.point_time,
            1 if point.is_current else 0,
            point.anchor_name,
        )
    )
    return points


def _series_key(point: ProgressionPoint) -> tuple[str, str, str]:
    return (point.profile_name, point.horizon_name, point.symbol)


def _flatten_profile_family_bindings(
    anchor_row_counts: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for row in anchor_row_counts:
        row_bindings = row.get("profile_family_bindings") or []
        for binding in row_bindings:
            if isinstance(binding, dict):
                bindings.append(binding)
    return bindings


def validate_progression_integrity(
    points: Sequence[ProgressionPoint],
    *,
    expected_anchor_count: int | None = None,
    scan_data_count_by_anchor: dict[str, int | None] | None = None,
    profile_family_bindings: Sequence[dict[str, Any]] | None = None,
    min_presence_ratio: float = 0.5,
    scan_spread_warn_ratio: float = 0.15,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    if not points:
        errors.append("No progression points were extracted.")
        return {
            "point_count": 0,
            "series_count": 0,
            "anchor_count": 0,
            "warnings": warnings,
            "errors": errors,
        }

    anchor_names = sorted({point.anchor_name for point in points if not point.is_current})
    series_by_key: dict[tuple[str, str, str], list[ProgressionPoint]] = {}
    for point in points:
        series_by_key.setdefault(_series_key(point), []).append(point)

    include_current = any(point.is_current for point in points)
    expected_slots = len(anchor_names) + (1 if include_current else 0)
    coverage_rows: list[dict[str, Any]] = []
    for key, series_points in sorted(series_by_key.items()):
        ordered = sorted(series_points, key=lambda point: (point.point_time, point.anchor_name))
        seen_times: set[datetime] = set()
        for point in ordered:
            if point.point_time in seen_times:
                errors.append(
                    "Duplicate point_time detected for "
                    f"profile={point.profile_name}, symbol={point.symbol}, "
                    f"horizon={point.horizon_name}, time={point.point_time.isoformat()}"
                )
            seen_times.add(point.point_time)
        observed_slots = len({point.anchor_name for point in ordered})
        presence_ratio = (
            (observed_slots / expected_slots) if expected_slots > 0 else 0.0
        )
        coverage_rows.append(
            {
                "profile_name": key[0],
                "horizon_name": key[1],
                "symbol": key[2],
                "observed_slots": observed_slots,
                "expected_slots": expected_slots,
                "presence_ratio": round(presence_ratio, 4),
            }
        )
        if expected_slots > 0 and presence_ratio < min_presence_ratio:
            warnings.append(
                "Low presence ratio for "
                f"profile={key[0]}, symbol={key[2]}, horizon={key[1]}: {presence_ratio:.3f}"
            )

    if expected_anchor_count is not None and len(anchor_names) < expected_anchor_count:
        warnings.append(
            f"Expected {expected_anchor_count} anchors but found {len(anchor_names)} in output."
        )

    if scan_data_count_by_anchor:
        available = [
            int(value)
            for value in scan_data_count_by_anchor.values()
            if value is not None
        ]
        if available:
            min_count = min(available)
            max_count = max(available)
            spread_ratio = ((max_count - min_count) / max_count) if max_count else 0.0
            if spread_ratio > scan_spread_warn_ratio:
                warnings.append(
                    "Anchor scan_data_count spread is high "
                    f"({spread_ratio:.3f}; min={min_count}, max={max_count})."
                )
        else:
            min_count = None
            max_count = None
            spread_ratio = None
    else:
        min_count = None
        max_count = None
        spread_ratio = None

    missing_family_bindings = [
        binding
        for binding in (profile_family_bindings or [])
        if str(binding.get("match_mode") or "").lower() == "missing"
    ]
    if missing_family_bindings:
        warnings.append(
            "Missing profile-family anchor matches detected: "
            + ", ".join(
                str(binding.get("profile_family") or "?")
                for binding in missing_family_bindings[:10]
            )
        )

    version_drift_rows = [
        point
        for point in points
        if point.anchor_profile_name
        and point.anchor_profile_name.lower() != point.profile_name.lower()
    ]
    if version_drift_rows:
        warnings.append(
            f"Version drift detected on {len(version_drift_rows)} rows "
            "(anchor profile differs from canonical profile)."
        )

    return {
        "point_count": len(points),
        "series_count": len(series_by_key),
        "anchor_count": len(anchor_names),
        "expected_anchor_count": expected_anchor_count,
        "warnings": warnings,
        "errors": errors,
        "coverage_rows": coverage_rows,
        "scan_data_count_summary": {
            "min": min_count,
            "max": max_count,
            "spread_ratio": round(spread_ratio, 4)
            if isinstance(spread_ratio, float)
            else None,
        },
        "missing_profile_family_bindings": missing_family_bindings,
    }


def _load_watchlist_symbols(
    watchlist_path: str | Path | None,
    extra_symbols: Sequence[str] | None,
) -> list[str]:
    requested = _normalize_symbol_candidates(extra_symbols)
    path = Path(watchlist_path) if watchlist_path is not None else DEFAULT_WATCHLIST_PATH
    if not path.exists():
        return requested

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return requested

    from_holdings: list[str] = []
    for row in payload.get("holdings", []):
        if not isinstance(row, dict):
            continue
        symbol = _normalize_text(row.get("symbol")) or _normalize_text(row.get("ticker"))
        if symbol is None:
            continue
        from_holdings.append(symbol)

    combined = requested + _normalize_symbol_candidates(from_holdings)
    seen: set[str] = set()
    deduped: list[str] = []
    for symbol in combined:
        if symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(symbol)
    return deduped


def _point_lookup(
    points: Sequence[ProgressionPoint],
) -> dict[tuple[str, str, str, str], ProgressionPoint]:
    lookup: dict[tuple[str, str, str, str], ProgressionPoint] = {}
    for point in points:
        key = (
            point.profile_name.lower(),
            point.horizon_name.lower(),
            point.symbol.upper(),
            point.anchor_name.lower(),
        )
        lookup[key] = point
        ticker_key = (
            point.profile_name.lower(),
            point.horizon_name.lower(),
            _split_symbol_ticker(point.symbol),
            point.anchor_name.lower(),
        )
        lookup[ticker_key] = point
    return lookup


def _build_watchlist_highlights(
    points: Sequence[ProgressionPoint],
    watchlist_symbols: Sequence[str],
) -> list[HighlightMarker]:
    if not points or not watchlist_symbols:
        return []

    watchlist = set(_normalize_symbol_candidates(watchlist_symbols))
    highlights: list[HighlightMarker] = []
    best_points_by_symbol: dict[str, ProgressionPoint] = {}
    for point in points:
        symbol_key = point.symbol.upper()
        ticker_key = _split_symbol_ticker(point.symbol)
        if symbol_key not in watchlist and ticker_key not in watchlist:
            continue
        existing = best_points_by_symbol.get(symbol_key)
        if existing is None or (
            (not existing.is_current and point.is_current)
            or point.point_time > existing.point_time
        ):
            best_points_by_symbol[symbol_key] = point

    for symbol_key in sorted(best_points_by_symbol):
        point = best_points_by_symbol[symbol_key]
        highlights.append(
            HighlightMarker(
                symbol=point.symbol,
                profile_name=point.profile_name,
                highlight_type="watchlist",
                anchor_name=point.anchor_name,
                point_time=point.point_time,
                score=point.score,
                score_delta=None,
                label="watchlist",
            )
        )
    return highlights


def extract_scout_highlight_markers(
    database_path: str | Path,
    *,
    points: Sequence[ProgressionPoint],
    profile_names: Sequence[str],
    horizon_name: str,
    primary_anchor_name: str = DEFAULT_PRIMARY_ANCHOR,
    top_n: int = DEFAULT_TOP_N_HIGHLIGHTS,
    min_abs_score_delta: float = DEFAULT_MIN_ABS_SCORE_DELTA,
    min_persistence_anchors: int = DEFAULT_MIN_PERSISTENCE_ANCHORS,
) -> list[HighlightMarker]:
    if not points or not profile_names:
        return []

    resolved_db = Path(database_path)
    run_metadata = _load_run_metadata(resolved_db)
    backwards_analysis_id = str(run_metadata["backwards_analysis_id"])
    anchor_names = _load_anchor_names(
        resolved_db,
        backwards_analysis_id=backwards_analysis_id,
        anchor_names=None,
    )
    point_lookup = _point_lookup(points)
    markers: list[HighlightMarker] = []
    marker_keys: set[tuple[str, str, str, str]] = set()

    for profile_name in profile_names:
        comparable_anchor_counts = _fetch_comparable_anchor_counts(
            resolved_db,
            backwards_analysis_id=backwards_analysis_id,
            profile_name=profile_name,
            horizon_name=horizon_name,
            anchor_names=anchor_names,
        )
        effective_primary_anchor = _resolve_effective_primary_anchor(
            requested_primary_anchor_name=primary_anchor_name,
            anchor_names=anchor_names,
            comparable_anchor_counts=comparable_anchor_counts,
        )
        comparable_anchor_names = tuple(
            anchor
            for anchor in _order_anchors(anchor_names)
            if comparable_anchor_counts.get(anchor, 0) > 0
        )
        top_progressors = _fetch_top_progressors(
            resolved_db,
            backwards_analysis_id=backwards_analysis_id,
            profile_name=profile_name,
            horizon_name=horizon_name,
            anchor_name=effective_primary_anchor,
            top_n=top_n,
        )
        price_aligned = _fetch_price_aligned_improvers(
            resolved_db,
            backwards_analysis_id=backwards_analysis_id,
            profile_name=profile_name,
            horizon_name=horizon_name,
            anchor_name=effective_primary_anchor,
            top_n=top_n,
        )
        persistent = _fetch_persistent_risers(
            resolved_db,
            backwards_analysis_id=backwards_analysis_id,
            profile_name=profile_name,
            horizon_name=horizon_name,
            comparable_anchor_names=comparable_anchor_names,
            min_persistence_anchors=min_persistence_anchors,
            top_n=top_n,
        )

        def add_marker(
            row: dict[str, Any],
            *,
            marker_type: str,
            anchor_name: str,
            label_prefix: str,
        ) -> None:
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                return
            point = point_lookup.get(
                (
                    profile_name.lower(),
                    horizon_name.lower(),
                    symbol.upper(),
                    "current",
                )
            ) or point_lookup.get(
                (
                    profile_name.lower(),
                    horizon_name.lower(),
                    symbol.upper(),
                    anchor_name.lower(),
                )
            )
            if point is None:
                return
            marker_key = (
                marker_type,
                profile_name.lower(),
                point.symbol.upper(),
                anchor_name.lower(),
            )
            if marker_key in marker_keys:
                return
            marker_keys.add(marker_key)
            score_delta = _to_float(row.get("score_delta") or row.get("avg_delta"))
            score_delta_text = f"{score_delta:+.3f}" if score_delta is not None else "n/a"
            markers.append(
                HighlightMarker(
                    symbol=point.symbol,
                    profile_name=profile_name,
                    highlight_type=marker_type,
                    anchor_name=anchor_name,
                    point_time=point.point_time,
                    score=point.score,
                    score_delta=score_delta,
                    label=f"{label_prefix} ({anchor_name}, score_delta={score_delta_text})",
                )
            )

        for row in top_progressors[:top_n]:
            add_marker(
                row,
                marker_type="scout_top_progressor",
                anchor_name=effective_primary_anchor,
                label_prefix="top progressor",
            )
        for row in price_aligned[:top_n]:
            add_marker(
                row,
                marker_type="scout_price_aligned",
                anchor_name=effective_primary_anchor,
                label_prefix="price aligned",
            )
        for row in persistent[:top_n]:
            add_marker(
                row,
                marker_type="scout_persistent",
                anchor_name=str(row.get("anchor_hits") or "multi_anchor"),
                label_prefix="persistent riser",
            )

    return markers


def render_score_progression_plotly_html(
    points: Sequence[ProgressionPoint],
    highlights: Sequence[HighlightMarker],
    *,
    title: str,
    output_path: str | Path,
    plot_spec: str | None = None,
    include_close_secondary_axis: bool = True,
    close_price_visible_by_default: bool = False,
    plot_height: int = DEFAULT_PLOT_HEIGHT,
    include_plotlyjs: bool | str = True,
) -> Path:
    try:
        import plotly.graph_objects as go
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "plotly is required for render_score_progression_plotly_html. "
            "Install it and retry."
        ) from exc

    if not points:
        raise ValueError("No points provided for plotting.")

    resolved_output = Path(output_path)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    write_target = resolved_output
    figure = go.Figure()

    profile_count = len({point.profile_name for point in points})
    grouped: dict[tuple[str, str], list[ProgressionPoint]] = {}
    for point in points:
        grouped.setdefault((point.profile_name, point.symbol), []).append(point)

    has_close_traces = False
    close_trace_visibility: bool | str = (
        True if close_price_visible_by_default else "legendonly"
    )
    for (profile_name, symbol), series_points in sorted(grouped.items()):
        ordered = sorted(series_points, key=lambda point: point.point_time)
        x_values = [point.point_time for point in ordered]
        y_score = [point.score for point in ordered]
        if profile_count == 1:
            trace_name = symbol
        else:
            trace_name = f"{symbol} ({profile_name})"
        figure.add_trace(
            go.Scatter(
                x=x_values,
                y=y_score,
                mode="lines+markers",
                name=trace_name,
                hovertemplate=(
                    "symbol=%{text}<br>time=%{x}<br>score=%{y:.4f}<extra></extra>"
                ),
                text=[point.symbol for point in ordered],
            )
        )
        if include_close_secondary_axis:
            y_close = [point.close for point in ordered]
            if any(value is not None for value in y_close):
                has_close_traces = True
                figure.add_trace(
                    go.Scatter(
                        x=x_values,
                        y=y_close,
                        mode="lines+markers",
                        name=f"{trace_name} close",
                        yaxis="y2",
                        visible=close_trace_visibility,
                        hovertemplate=(
                            "symbol=%{text}<br>time=%{x}<br>close=%{y:.4f}<extra></extra>"
                        ),
                        text=[point.symbol for point in ordered],
                    )
                )

    if highlights:
        figure.add_trace(
            go.Scatter(
                x=[marker.point_time for marker in highlights],
                y=[marker.score for marker in highlights],
                mode="markers",
                name="highlights",
                text=[marker.label for marker in highlights],
                marker={"size": 10, "symbol": "diamond"},
                hovertemplate=(
                    "symbol=%{customdata[0]}<br>type=%{customdata[1]}<br>"
                    "anchor=%{customdata[2]}<br>note=%{text}<extra></extra>"
                ),
                customdata=[
                    [marker.symbol, marker.highlight_type, marker.anchor_name]
                    for marker in highlights
                ],
            )
        )

    layout_kwargs: dict[str, Any] = {
        "xaxis_title": "time",
        "yaxis_title": "score",
        "height": plot_height,
        "autosize": True,
    }
    series_count = len(grouped)
    legend_right_margin = 40 if series_count <= 5 else min(40 + series_count * 10, 220)
    layout_kwargs["legend"] = {
        "title": {"text": "series"},
        "x": 1.02,
        "y": 1,
        "xanchor": "left",
        "yanchor": "top",
    }
    if plot_spec:
        line_count = plot_spec.count("<br>") + 1
        top_margin = min(180 + line_count * 14, 420)
        layout_kwargs["margin"] = {"t": top_margin, "b": 60, "l": 60, "r": legend_right_margin}
        layout_kwargs["height"] = plot_height + min(line_count * 10, 180)
        layout_kwargs["title"] = {
            "text": title,
            "subtitle": {"text": plot_spec},
            "x": 0,
            "xanchor": "left",
        }
    else:
        layout_kwargs["margin"] = {"t": 80, "b": 60, "l": 60, "r": legend_right_margin}
        layout_kwargs["title"] = title

    figure.update_layout(**layout_kwargs)
    if include_close_secondary_axis and has_close_traces:
        figure.update_layout(
            yaxis2={
                "title": "close",
                "overlaying": "y",
                "side": "right",
            }
        )

    figure.write_html(
        str(write_target),
        include_plotlyjs=include_plotlyjs,
        config={"displayModeBar": True, "responsive": False},
        full_html=True,
        default_height=f"{int(layout_kwargs['height'])}px",
        default_width="100%",
    )
    _patch_plotly_html_for_local_file(
        write_target,
        plot_height_px=int(layout_kwargs["height"]),
        title=title,
    )
    return write_target


def _patch_plotly_html_for_local_file(
    html_path: Path,
    *,
    plot_height_px: int,
    title: str,
) -> None:
    """Make Plotly exports reliably visible when opened via file:// on Windows."""
    text = html_path.read_text(encoding="utf-8")
    if not text.startswith("<!DOCTYPE"):
        text = "<!DOCTYPE html>\n" + text

    head_patch = (
        '<head><meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        f"<title>{html_module.escape(title)}</title>"
        "<style>"
        "html,body{margin:0;padding:0;background:#fff;}"
        f"#plot-loading{{padding:12px 16px;font:14px/1.4 Segoe UI,Arial,sans-serif;color:#444;}}"
        f".plotly-graph-div{{height:{plot_height_px}px !important;"
        f"min-height:{plot_height_px}px !important;width:100% !important;}}"
        "</style>"
        "</head>"
    )
    text = text.replace("<head><meta charset=\"utf-8\" /></head>", head_patch, 1)
    text = re.sub(
        r'class="plotly-graph-div" style="height:100%; width:100%;"',
        f'class="plotly-graph-div" style="height:{plot_height_px}px; width:100%;"',
        text,
        count=1,
    )
    loading = (
        f'<div id="plot-loading">Loading chart ({plot_height_px}px)… '
        "Large embedded Plotly file; this can take 10–20 seconds on first open.</div>"
    )
    text = text.replace("<body>", f"<body>{loading}", 1)
    html_path.write_text(text, encoding="utf-8")


def _write_points_csv(points: Sequence[ProgressionPoint], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(asdict(points[0]).keys()) if points else []
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            for point in points:
                row = asdict(point)
                row["point_time"] = point.point_time.isoformat()
                writer.writerow(row)


def _write_quality_json(quality: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(quality, indent=2, default=str), encoding="utf-8")


def _progression_symbol_lines_from_points(
    points: Sequence[ProgressionPoint],
    *,
    requested_symbols: Sequence[str] | None = None,
) -> list[ProgressionSymbolSpecLine]:
    company_by_symbol: dict[str, str | None] = {}
    for point in points:
        company_by_symbol.setdefault(point.symbol, point.company)

    if requested_symbols:
        lines: list[ProgressionSymbolSpecLine] = []
        for requested in requested_symbols:
            matched_label = next(
                (
                    symbol
                    for symbol in company_by_symbol
                    if _symbol_match(symbol, {requested.upper()})
                ),
                requested,
            )
            lines.append(
                ProgressionSymbolSpecLine(
                    requested=requested,
                    plotted_label=matched_label,
                    db_symbol=matched_label,
                    company=company_by_symbol.get(matched_label),
                )
            )
        return lines

    return [
        ProgressionSymbolSpecLine(
            plotted_label=symbol,
            db_symbol=symbol,
            company=company_by_symbol.get(symbol),
        )
        for symbol in sorted(company_by_symbol)
    ]


def _plot_progression_for_profile_target(
    *,
    database_path: Path,
    run_slug: str,
    profile_target: ResolvedBackwardsProfileTarget,
    horizon_name: str,
    symbol_filter: Sequence[str] | None,
    watchlist_symbols: Sequence[str],
    run_output_dir: Path,
    title: str | None,
    include_scout_highlights: bool,
    primary_anchor_name: str,
    top_n_highlights: int,
    expected_anchor_count: int | None,
    scan_data_count_by_anchor: dict[str, int | None] | None,
    profile_family_bindings: Sequence[dict[str, Any]] | None,
) -> dict[str, Any]:
    points = extract_progression_series(
        database_path,
        profile_name=profile_target.profile_name,
        horizon_name=horizon_name,
        symbols=symbol_filter,
    )
    highlights: list[HighlightMarker] = _build_watchlist_highlights(points, watchlist_symbols)
    if include_scout_highlights and points:
        highlights.extend(
            extract_scout_highlight_markers(
                database_path,
                points=points,
                profile_names=[profile_target.profile_name],
                horizon_name=horizon_name,
                primary_anchor_name=primary_anchor_name,
                top_n=top_n_highlights,
            )
        )

    quality = validate_progression_integrity(
        points,
        expected_anchor_count=expected_anchor_count,
        scan_data_count_by_anchor=scan_data_count_by_anchor,
        profile_family_bindings=profile_family_bindings,
    )

    chart_slug = _slugify(profile_target.profile_name)
    chart_title, plot_spec_text, plot_spec_dict = build_progression_plot_title_and_spec(
        profile_name=profile_target.profile_name,
        horizon_name=horizon_name,
        symbol_lines=_progression_symbol_lines_from_points(
            points,
            requested_symbols=symbol_filter,
        ),
        title_override=title,
    )
    from data_analysis_scripts.trading_view_plot_browser_view import (
        BackwardsPlotRef,
        register_backwards_plot,
        resolve_backwards_plot_html_path,
        write_plot_locations_sidecar,
    )

    symbol_slug = "universe"
    html_path = resolve_backwards_plot_html_path(run_slug, symbol_slug, chart_slug)
    render_score_progression_plotly_html(
        points,
        highlights,
        title=chart_title,
        plot_spec=plot_spec_text or None,
        output_path=html_path,
        include_close_secondary_axis=True,
        close_price_visible_by_default=False,
    )
    plot_entry = register_backwards_plot(
        html_path,
        plot_ref=BackwardsPlotRef(
            run_slug=run_slug,
            symbol_slug=symbol_slug,
            profile_slug=chart_slug,
            horizon_name=horizon_name,
            database_path=database_path.as_posix(),
            run_data_dir=run_output_dir.as_posix(),
            title=chart_title,
        ),
    )
    write_plot_locations_sidecar(
        run_output_dir,
        plot_entry=plot_entry,
        profile_slug=chart_slug,
    )
    plot_spec_path = run_output_dir / f"plot_spec__{chart_slug}__{horizon_name}.json"
    _write_plot_spec_json(plot_spec_dict, plot_spec_path)
    points_csv = run_output_dir / f"score_progression_points__{chart_slug}__{horizon_name}.csv"
    _write_points_csv(points, points_csv)
    quality_path = run_output_dir / f"progression_data_quality__{chart_slug}__{horizon_name}.json"
    _write_quality_json(quality, quality_path)

    return {
        "profile_family": profile_target.profile_family,
        "resolved_profile_name": profile_target.profile_name,
        "html_path": plot_entry["html_path"],
        "browser_html_path": plot_entry["browser_html_path"],
        "browser_file_uri": plot_entry["browser_file_uri"],
        "plots_index_file_uri": plot_entry.get("plots_index_file_uri"),
        "latest_index_file_uri": plot_entry.get("latest_index_file_uri"),
        "run_index_file_uri": plot_entry.get("run_index_file_uri"),
        "plot_spec_path": plot_spec_path.as_posix(),
        "points_csv": points_csv.as_posix(),
        "quality_report_path": quality_path.as_posix(),
        "point_count": len(points),
        "highlight_count": len(highlights),
        "quality": quality,
    }


def run_backwards_progression_plot(
    *,
    duckdb_runs_root: str | Path | None = None,
    current_run_id: str | None = None,
    current_database_path: str | Path | None = None,
    anchors: Sequence[AnchorSpec] | None = None,
    runs_per_week: int = DEFAULT_RUNS_PER_WEEK,
    selection: str = DEFAULT_SELECTION,
    start_iso_week: tuple[int, int] | None = None,
    end_iso_week: tuple[int, int] | None = None,
    iso_year: int | None = None,
    start_week: int | None = None,
    end_week: int | None = None,
    min_scan_data_count: int | None = DEFAULT_MIN_SCAN_DATA_COUNT,
    anchor_min_scan_data_count: int | None = None,
    profile_name: str | None = None,
    profile_family: str | None = None,
    profile_families: Sequence[str] | None = None,
    profile_names: Sequence[str] | None = None,
    profile_suite_path: str | Path | None = None,
    horizon_name: str = DEFAULT_HORIZON_NAME,
    symbols: Sequence[str] | None = None,
    watchlist_path: str | Path | None = None,
    include_scout_highlights: bool = True,
    include_deltas_for_highlights: bool = True,
    top_n_highlights: int = DEFAULT_TOP_N_HIGHLIGHTS,
    primary_anchor_name: str = DEFAULT_PRIMARY_ANCHOR,
    output_dir: str | Path | None = None,
    backwards_analysis_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(duckdb_runs_root) if duckdb_runs_root is not None else DEFAULT_DUCKDB_RUNS_ROOT

    anchor_plan: dict[str, Any] | None = None
    anchors_to_use: list[AnchorSpec]
    if anchors is None:
        anchor_plan = resolve_weekly_progression_anchors(
            duckdb_runs_root=root,
            current_run_id=current_run_id,
            current_database_path=current_database_path,
            runs_per_week=runs_per_week,
            selection=selection,
            start_iso_week=start_iso_week,
            end_iso_week=end_iso_week,
            iso_year=iso_year,
            start_week=start_week,
            end_week=end_week,
            min_scan_data_count=min_scan_data_count,
            anchor_min_scan_data_count=anchor_min_scan_data_count,
        )
        anchors_to_use = list(anchor_plan["anchors"])
    else:
        anchors_to_use = list(anchors)

    include_profiles_for_run: list[str] | None
    if profile_name:
        include_profiles_for_run = [profile_name]
    elif profile_suite_path is not None:
        from data_analysis_scripts.trading_view_move_prediction_profile_config import (
            resolve_profile_suite,
        )

        suite = resolve_profile_suite(
            profile_suite_path,
            list(profile_names) if profile_names else None,
        )
        include_profiles_for_run = list(suite.get("profile_names", []))
    elif profile_names:
        include_profiles_for_run = list(profile_names)
    else:
        include_profiles_for_run = None

    need_deltas = include_deltas_for_highlights or include_scout_highlights
    backwards_result = run_backwards_prediction_analysis(
        current_run_id=current_run_id,
        current_database_path=current_database_path,
        anchors=anchors_to_use,
        duckdb_runs_root=root,
        include_profiles=include_profiles_for_run,
        include_consensus=False,
        include_components=False,
        min_scan_data_count=min_scan_data_count,
        anchor_min_scan_data_count=anchor_min_scan_data_count,
        output_dir=output_dir,
        backwards_analysis_id=backwards_analysis_id,
        build_options=BackwardsAnalysisBuildOptions(
            include_deltas=need_deltas,
            include_consensus=False,
            include_components=False,
            include_snapshots=True,
        ),
    )

    database_path = Path(backwards_result["database_path"])
    profile_targets, profile_messages = resolve_backwards_profile_targets(
        database_path,
        horizon_name=horizon_name,
        profile_name=profile_name,
        profile_family=profile_family,
        profile_families=profile_families,
        profile_names=profile_names,
        profile_suite_path=profile_suite_path,
    )

    watchlist_symbols = _load_watchlist_symbols(watchlist_path, symbols)
    symbol_filter = list(_normalize_symbol_candidates(symbols)) or watchlist_symbols or None

    anchor_row_counts = backwards_result.get("anchor_row_counts") or []
    expected_anchor_count = len(backwards_result.get("resolved_anchors") or [])
    scan_data_count_by_anchor: dict[str, int | None] = {}
    if anchor_plan is not None:
        run_lookup = {
            entry.run_id: entry.scan_data_count for entry in anchor_plan.get("run_index", [])
        }
        for resolved_anchor in backwards_result.get("resolved_anchors") or []:
            anchor_name = str(resolved_anchor.get("anchor_name") or "")
            run_id = str(resolved_anchor.get("run_id") or "")
            scan_data_count_by_anchor[anchor_name] = run_lookup.get(run_id)

    run_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else database_path.parent / "progression_plots"
    )
    run_output_dir.mkdir(parents=True, exist_ok=True)
    from data_analysis_scripts.trading_view_plot_browser_view import extract_backwards_run_slug

    run_slug = extract_backwards_run_slug(database_path.parent)

    profile_plots: list[dict[str, Any]] = []
    for index, profile_target in enumerate(profile_targets):
        profile_title = title
        if title is None and len(profile_targets) > 1:
            profile_title = (
                f"Backwards progression: {profile_target.profile_name} ({horizon_name})"
            )
        profile_plots.append(
            _plot_progression_for_profile_target(
                database_path=database_path,
                run_slug=run_slug,
                profile_target=profile_target,
                horizon_name=horizon_name,
                symbol_filter=symbol_filter,
                watchlist_symbols=watchlist_symbols,
                run_output_dir=run_output_dir,
                title=profile_title,
                include_scout_highlights=include_scout_highlights,
                primary_anchor_name=primary_anchor_name,
                top_n_highlights=top_n_highlights,
                expected_anchor_count=expected_anchor_count,
                scan_data_count_by_anchor=scan_data_count_by_anchor or None,
                profile_family_bindings=_flatten_profile_family_bindings(anchor_row_counts),
            )
        )

    primary_plot = profile_plots[0]
    result = {
        "database_path": database_path.as_posix(),
        "run_slug": run_slug,
        "html_path": primary_plot["html_path"],
        "browser_html_path": primary_plot["browser_html_path"],
        "browser_file_uri": primary_plot["browser_file_uri"],
        "plots_index_file_uri": primary_plot.get("plots_index_file_uri"),
        "latest_index_file_uri": primary_plot.get("latest_index_file_uri"),
        "run_index_file_uri": primary_plot.get("run_index_file_uri"),
        "points_csv": primary_plot["points_csv"],
        "quality_report_path": primary_plot["quality_report_path"],
        "point_count": sum(plot["point_count"] for plot in profile_plots),
        "highlight_count": sum(plot["highlight_count"] for plot in profile_plots),
        "quality": primary_plot["quality"],
        "resolved_profile_name": primary_plot["resolved_profile_name"],
        "resolved_profile_family": primary_plot["profile_family"],
        "resolved_profile_targets": profile_targets,
        "profile_plots": profile_plots,
        "profile_resolution_messages": profile_messages,
        "resolved_anchors": backwards_result.get("resolved_anchors", []),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }
    if anchor_plan is not None:
        result["anchor_plan_summary"] = summarize_weekly_anchor_plan(anchor_plan)
    if len(profile_plots) > 1:
        manifest_path = run_output_dir / f"progression_plot_manifest__{horizon_name}.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "profile_plots": profile_plots,
                    "profile_resolution_messages": profile_messages,
                    "resolved_profile_targets": [
                        asdict(target) for target in profile_targets
                    ],
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        result["plot_manifest_path"] = manifest_path.as_posix()
    return result


__all__ = [
    "COMPACT_TITLE_SYMBOL_THRESHOLD",
    "DEFAULT_HORIZON_NAME",
    "DEFAULT_PRIMARY_ANCHOR",
    "HighlightMarker",
    "ProgressionPoint",
    "ProgressionSymbolSpecLine",
    "ResolvedBackwardsProfileTarget",
    "build_progression_plot_title_and_spec",
    "build_symbol_set_slug",
    "extract_progression_series",
    "extract_scout_highlight_markers",
    "fetch_backwards_profile_catalog",
    "render_score_progression_plotly_html",
    "resolve_backwards_profile_targets",
    "run_backwards_progression_plot",
    "validate_progression_integrity",
]
