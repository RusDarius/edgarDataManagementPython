from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Sequence

from data_analysis_scripts.trading_view_backwards_prediction_analysis import (
    COMPONENT_FIELDS,
    DEFAULT_BACKWARDS_ANALYSIS_ROOT,
    LOG_DIR,
)
from db.trading_view_backwards_prediction_duckdb import (
    BackwardsPredictionDuckDBStore,
    query_backwards_prediction_duckdb,
)

DEFAULT_BACKWARDS_RUNS_ROOT = (
    LOG_DIR / "duckdb_runs" / DEFAULT_BACKWARDS_ANALYSIS_ROOT / "runs"
)
DEFAULT_PRIMARY_ANCHOR = "last_week"
DEFAULT_HORIZON_NAME = "weeks"
DEFAULT_TOP_N = 50
DEFAULT_MIN_ABS_SCORE_DELTA = 0.35
DEFAULT_MIN_PERSISTENCE_ANCHORS = 2
COMPACT_ANCHOR_TOP_N = 20

ANCHOR_DISPLAY_ORDER: tuple[str, ...] = (
    "yesterday",
    "last_week",
    "last_month",
    "oldest",
)


@dataclass(frozen=True)
class ScoutReportContext:
    database_path: Path
    run_dir: Path
    backwards_analysis_id: str
    current_run_id: str
    current_snapshot_label: str
    anchor_names: tuple[str, ...]
    profile_name: str
    horizon_name: str
    requested_primary_anchor_name: str
    effective_primary_anchor_name: str
    comparable_anchor_names: tuple[str, ...]
    anchor_coverage: dict[str, int]
    top_n: int


def _normalize_string_selection(
    value: str | Sequence[str],
    *,
    param_name: str,
) -> tuple[str, ...]:
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{param_name} must not be empty.")
        return (normalized,)
    items = tuple(str(item).strip() for item in value if str(item).strip())
    if not items:
        raise ValueError(f"{param_name} must contain at least one non-empty value.")
    return items


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return parsed


def _truncate_label(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _format_score(value: Any) -> str:
    numeric = _coerce_optional_float(value)
    if numeric is None:
        return "—"
    return f"{numeric:+.2f}"


def _format_pct(value: Any) -> str:
    numeric = _coerce_optional_float(value)
    if numeric is None:
        return "—"
    return f"{numeric:+.1f}%"


def _format_rank_delta(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):+d}"
    except (TypeError, ValueError):
        return "—"


def _alignment_flag(score_delta: Any, close_delta_pct: Any) -> str:
    score = _coerce_optional_float(score_delta)
    price = _coerce_optional_float(close_delta_pct)
    if score is None or price is None or score == 0 or price == 0:
        return "—"
    if (score > 0 and price > 0) or (score < 0 and price < 0):
        return "Y"
    return "N"


def resolve_backwards_analysis_database(
    *,
    run_folder_pattern: str | None = None,
    database_path: str | Path | None = None,
    backwards_runs_root: str | Path | None = None,
) -> Path:
    if database_path is not None:
        resolved = Path(database_path)
        if not resolved.exists():
            raise FileNotFoundError(f"Backwards analysis database not found: {resolved}")
        return resolved

    if run_folder_pattern is None:
        raise ValueError("Provide either run_folder_pattern or database_path.")

    search_root = (
        Path(backwards_runs_root)
        if backwards_runs_root is not None
        else DEFAULT_BACKWARDS_RUNS_ROOT
    )
    matching_dbs = list(search_root.rglob("backwards_prediction_analysis.duckdb"))
    for candidate in matching_dbs:
        if run_folder_pattern in str(candidate):
            return candidate

    raise FileNotFoundError(
        f"No backwards analysis database found matching pattern: {run_folder_pattern}\n"
        f"Searched in: {search_root}"
    )


def _load_run_metadata(database_path: Path) -> dict[str, Any]:
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
        raise ValueError(f"No backwards_analysis_runs row in {database_path}")
    return rows[0]


def _load_anchor_names(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    anchor_names: Sequence[str] | None,
) -> tuple[str, ...]:
    if anchor_names:
        return tuple(anchor_names)

    rows = query_backwards_prediction_duckdb(
        database_path,
        """
        SELECT anchor_name, offset_days_from_current
        FROM backwards_analysis_anchors
        WHERE backwards_analysis_id = ?
        ORDER BY offset_days_from_current ASC NULLS LAST, anchor_name ASC
        """,
        parameters=[backwards_analysis_id],
    )
    discovered = [str(row["anchor_name"]) for row in rows if row.get("anchor_name")]
    if not discovered:
        raise ValueError("No anchors found in backwards analysis database.")

    ordered: list[str] = []
    for preferred in ANCHOR_DISPLAY_ORDER:
        if preferred in discovered and preferred not in ordered:
            ordered.append(preferred)
    for name in discovered:
        if name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def _order_anchors(anchor_names: Sequence[str]) -> list[str]:
    names = list(anchor_names)
    ordered: list[str] = []
    for preferred in ANCHOR_DISPLAY_ORDER:
        if preferred in names and preferred not in ordered:
            ordered.append(preferred)
    for name in names:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _deduped_profile_horizon_deltas_sql(*, extra_where: str = "") -> str:
    return f"""
        WITH deduped_deltas AS (
            SELECT * EXCLUDE (row_num)
            FROM (
                SELECT deltas.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY deltas.anchor_name, deltas.symbol
                        ORDER BY ABS(deltas.score_delta) DESC NULLS LAST,
                            deltas.symbol ASC
                    ) AS row_num
                FROM backwards_profile_horizon_deltas AS deltas
                WHERE deltas.backwards_analysis_id = ?
                  AND deltas.profile_name = ?
                  AND deltas.horizon_name = ?
                  {extra_where}
            )
            WHERE row_num = 1
        )
    """


def _deduped_profile_component_deltas_sql(*, extra_where: str = "") -> str:
    return f"""
        WITH deduped_components AS (
            SELECT * EXCLUDE (row_num)
            FROM (
                SELECT components.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY components.anchor_name, components.symbol
                        ORDER BY ABS(components.momentum_delta) DESC NULLS LAST,
                            components.symbol ASC
                    ) AS row_num
                FROM backwards_profile_component_deltas AS components
                WHERE components.backwards_analysis_id = ?
                  AND components.profile_name = ?
                  {extra_where}
            )
            WHERE row_num = 1
        )
    """


def _fetch_profile_anchor_coverage(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
) -> dict[str, int]:
    rows = query_backwards_prediction_duckdb(
        database_path,
        """
        SELECT anchor_name, COUNT(DISTINCT symbol) AS symbol_count
        FROM backwards_anchor_snapshots
        WHERE backwards_analysis_id = ?
          AND profile_name = ?
          AND horizon_name = ?
          AND NOT is_current
        GROUP BY anchor_name
        ORDER BY anchor_name ASC
        """,
        parameters=[backwards_analysis_id, profile_name, horizon_name],
    )
    return {
        str(row["anchor_name"]): int(row["symbol_count"] or 0)
        for row in rows
        if row.get("anchor_name")
    }


def _fetch_anchor_profile_bindings(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
    anchor_names: Sequence[str],
) -> dict[str, dict[str, Any]]:
    if not anchor_names:
        return {}
    placeholders = ", ".join("?" for _ in anchor_names)
    rows = query_backwards_prediction_duckdb(
        database_path,
        f"""
        SELECT anchor_name,
            MAX(anchor_profile_name) AS anchor_profile_name,
            BOOL_AND(COALESCE(profile_version_exact_match, FALSE)) AS profile_version_exact_match,
            COUNT(*) FILTER (
                WHERE in_current AND in_anchor AND score_delta IS NOT NULL
            ) AS comparable_count
        FROM backwards_profile_horizon_deltas
        WHERE backwards_analysis_id = ?
          AND profile_name = ?
          AND horizon_name = ?
          AND anchor_name IN ({placeholders})
        GROUP BY anchor_name
        """,
        parameters=[backwards_analysis_id, profile_name, horizon_name, *anchor_names],
    )
    return {str(row["anchor_name"]): row for row in rows}


def _fetch_comparable_anchor_counts(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
    anchor_names: Sequence[str],
) -> dict[str, int]:
    if not anchor_names:
        return {}
    placeholders = ", ".join("?" for _ in anchor_names)
    rows = query_backwards_prediction_duckdb(
        database_path,
        _deduped_profile_horizon_deltas_sql(
            extra_where=f"AND deltas.anchor_name IN ({placeholders})"
        )
        + f"""
        SELECT anchor_name, COUNT(*) AS comparable_count
        FROM deduped_deltas
        WHERE in_current
          AND in_anchor
          AND score_delta IS NOT NULL
        GROUP BY anchor_name
        """,
        parameters=[backwards_analysis_id, profile_name, horizon_name, *anchor_names],
    )
    return {
        str(row["anchor_name"]): int(row["comparable_count"] or 0) for row in rows
    }


def _resolve_effective_primary_anchor(
    *,
    requested_primary_anchor_name: str,
    anchor_names: Sequence[str],
    comparable_anchor_counts: dict[str, int],
) -> str:
    if comparable_anchor_counts.get(requested_primary_anchor_name, 0) > 0:
        return requested_primary_anchor_name

    for anchor_name in _order_anchors(anchor_names):
        if comparable_anchor_counts.get(anchor_name, 0) > 0:
            return anchor_name
    return requested_primary_anchor_name


def _fetch_top_progressors(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
    anchor_name: str,
    top_n: int,
) -> list[dict[str, Any]]:
    return query_backwards_prediction_duckdb(
        database_path,
        _deduped_profile_horizon_deltas_sql(
            extra_where="AND deltas.anchor_name = ?"
        )
        + """
        SELECT symbol,
            company,
            score_delta,
            rank_delta,
            close_delta_pct,
            current_score,
            anchor_score,
            current_close,
            anchor_close,
            current_direction,
            anchor_direction
        FROM deduped_deltas
        WHERE in_current
          AND in_anchor
          AND score_delta IS NOT NULL
          AND score_delta > 0
        ORDER BY score_delta DESC, rank_delta DESC NULLS LAST, symbol ASC
        LIMIT ?
        """,
        parameters=[
            backwards_analysis_id,
            profile_name,
            horizon_name,
            anchor_name,
            top_n,
        ],
    )


def _fetch_significant_movers(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
    anchor_name: str,
    top_n: int,
    min_abs_score_delta: float,
) -> list[dict[str, Any]]:
    return query_backwards_prediction_duckdb(
        database_path,
        _deduped_profile_horizon_deltas_sql(
            extra_where="AND deltas.anchor_name = ?"
        )
        + """
        SELECT symbol,
            company,
            score_delta,
            rank_delta,
            close_delta_pct,
            current_score,
            anchor_score,
            current_close,
            anchor_close,
            CASE
                WHEN score_delta > 0 THEN 'up'
                WHEN score_delta < 0 THEN 'down'
                ELSE 'flat'
            END AS move_direction
        FROM deduped_deltas
        WHERE in_current
          AND in_anchor
          AND score_delta IS NOT NULL
          AND ABS(score_delta) >= ?
        ORDER BY ABS(score_delta) DESC, symbol ASC
        LIMIT ?
        """,
        parameters=[
            backwards_analysis_id,
            profile_name,
            horizon_name,
            anchor_name,
            min_abs_score_delta,
            top_n,
        ],
    )


def _fetch_price_aligned_improvers(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
    anchor_name: str,
    top_n: int,
) -> list[dict[str, Any]]:
    return query_backwards_prediction_duckdb(
        database_path,
        _deduped_profile_horizon_deltas_sql(
            extra_where="AND deltas.anchor_name = ?"
        )
        + """
        SELECT symbol,
            company,
            score_delta,
            close_delta_pct,
            current_score,
            anchor_score,
            current_close,
            anchor_close
        FROM deduped_deltas
        WHERE in_current
          AND in_anchor
          AND score_delta > 0
          AND close_delta_pct > 0
        ORDER BY score_delta DESC, close_delta_pct DESC, symbol ASC
        LIMIT ?
        """,
        parameters=[
            backwards_analysis_id,
            profile_name,
            horizon_name,
            anchor_name,
            top_n,
        ],
    )


def _fetch_price_divergent(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
    anchor_name: str,
    top_n: int,
) -> list[dict[str, Any]]:
    return query_backwards_prediction_duckdb(
        database_path,
        _deduped_profile_horizon_deltas_sql(
            extra_where="AND deltas.anchor_name = ?"
        )
        + """
        SELECT symbol,
            company,
            score_delta,
            close_delta_pct,
            current_score,
            anchor_score,
            current_close,
            anchor_close
        FROM deduped_deltas
        WHERE in_current
          AND in_anchor
          AND score_delta > 0
          AND close_delta_pct < 0
        ORDER BY score_delta DESC, close_delta_pct ASC, symbol ASC
        LIMIT ?
        """,
        parameters=[
            backwards_analysis_id,
            profile_name,
            horizon_name,
            anchor_name,
            top_n,
        ],
    )


def _fetch_persistent_risers(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
    comparable_anchor_names: Sequence[str],
    min_persistence_anchors: int,
    top_n: int,
) -> list[dict[str, Any]]:
    if not comparable_anchor_names:
        return []
    placeholders = ", ".join("?" for _ in comparable_anchor_names)
    return query_backwards_prediction_duckdb(
        database_path,
        _deduped_profile_horizon_deltas_sql(
            extra_where=f"AND deltas.anchor_name IN ({placeholders})"
        )
        + """
        , hits AS (
            SELECT symbol,
                company,
                anchor_name,
                score_delta,
                close_delta_pct
            FROM deduped_deltas
            WHERE in_current
              AND in_anchor
              AND score_delta > 0
        ),
        aggregated AS (
            SELECT symbol,
                MAX(company) AS company,
                COUNT(*) AS positive_anchor_hits,
                MIN(score_delta) AS min_positive_delta,
                AVG(score_delta) AS avg_delta,
                AVG(close_delta_pct) AS avg_price_delta_pct,
                STRING_AGG(anchor_name, '+' ORDER BY anchor_name) AS anchor_hits
            FROM hits
            GROUP BY symbol
            HAVING COUNT(*) >= ?
        )
        SELECT *
        FROM aggregated
        ORDER BY avg_delta DESC, positive_anchor_hits DESC, symbol ASC
        LIMIT ?
        """,
        parameters=[
            backwards_analysis_id,
            profile_name,
            horizon_name,
            *comparable_anchor_names,
            min_persistence_anchors,
            top_n,
        ],
    )


def _fetch_symbol_score_price_progression(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    horizon_name: str,
    anchor_names: Sequence[str],
    symbols: Sequence[str],
) -> list[dict[str, Any]]:
    if not symbols:
        return []

    symbol_placeholders = ", ".join("?" for _ in symbols)
    anchor_placeholders = ", ".join("?" for _ in anchor_names)
    score_columns = [
        (
            f"MAX(CASE WHEN NOT is_current AND anchor_name = '{anchor_name}' "
            f"THEN score END) AS score__{anchor_name}"
        )
        for anchor_name in anchor_names
    ]
    close_columns = [
        (
            f"MAX(CASE WHEN NOT is_current AND anchor_name = '{anchor_name}' "
            f"THEN close END) AS close__{anchor_name}"
        )
        for anchor_name in anchor_names
    ]
    return query_backwards_prediction_duckdb(
        database_path,
        f"""
        SELECT symbol,
            MAX(company) AS company,
            MAX(CASE WHEN is_current THEN score END) AS score__current,
            MAX(CASE WHEN is_current THEN close END) AS close__current,
            {", ".join(score_columns)},
            {", ".join(close_columns)}
        FROM backwards_anchor_snapshots
        WHERE backwards_analysis_id = ?
          AND profile_name = ?
          AND horizon_name = ?
          AND symbol IN ({symbol_placeholders})
          AND (
              is_current
              OR anchor_name IN ({anchor_placeholders})
          )
        GROUP BY symbol
        ORDER BY score__current DESC NULLS LAST, symbol ASC
        """,
        parameters=[
            backwards_analysis_id,
            profile_name,
            horizon_name,
            *symbols,
            *anchor_names,
        ],
    )


def _fetch_component_progression(
    database_path: Path,
    *,
    backwards_analysis_id: str,
    profile_name: str,
    anchor_name: str,
    symbols: Sequence[str],
) -> list[dict[str, Any]]:
    if not symbols:
        return []

    symbol_placeholders = ", ".join("?" for _ in symbols)
    component_select = ", ".join(
        f"{component}_delta" for component in COMPONENT_FIELDS
    )
    component_anchor_select = ", ".join(
        f"anchor_{component}" for component in COMPONENT_FIELDS
    )
    component_current_select = ", ".join(
        f"current_{component}" for component in COMPONENT_FIELDS
    )
    return query_backwards_prediction_duckdb(
        database_path,
        _deduped_profile_component_deltas_sql(
            extra_where=(
                f"AND components.anchor_name = ? "
                f"AND components.symbol IN ({symbol_placeholders})"
            )
        )
        + f"""
        SELECT symbol,
            company,
            close_delta_pct,
            {component_select},
            {component_anchor_select},
            {component_current_select}
        FROM deduped_components
        WHERE in_current
          AND in_anchor
        ORDER BY ABS(momentum_delta) DESC NULLS LAST, symbol ASC
        """,
        parameters=[
            backwards_analysis_id,
            profile_name,
            anchor_name,
            *symbols,
        ],
    )


def _fetch_universe_drift(
    database_path: Path,
    *,
    profile_name: str,
    horizon_name: str,
) -> list[dict[str, Any]]:
    return query_backwards_prediction_duckdb(
        database_path,
        """
        SELECT anchor_name,
            symbols_added,
            symbols_dropped,
            symbols_retained
        FROM vw_backwards_universe_drift
        WHERE profile_name = ?
          AND horizon_name = ?
        ORDER BY anchor_name ASC
        """,
        parameters=[profile_name, horizon_name],
    )


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return False
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return True


def _format_progressor_table(
    rows: Sequence[dict[str, Any]],
    *,
    title: str,
    limit: int | None = None,
) -> list[str]:
    lines = [title, "-" * 118]
    lines.append(
        f"{'#':>3}  {'Symbol':<14} {'Company':<22} {'ScoreΔ':>7} {'RankΔ':>6} "
        f"{'CloseΔ%':>8} {'Cur':>6} {'Anc':>6} {'Aln':>3}"
    )
    display_rows = rows if limit is None else rows[:limit]
    for index, row in enumerate(display_rows, 1):
        lines.append(
            f"{index:>3}  "
            f"{_truncate_label(row.get('symbol'), 14):<14} "
            f"{_truncate_label(row.get('company'), 22):<22} "
            f"{_format_score(row.get('score_delta')):>7} "
            f"{_format_rank_delta(row.get('rank_delta')):>6} "
            f"{_format_pct(row.get('close_delta_pct')):>8} "
            f"{_format_score(row.get('current_score')):>6} "
            f"{_format_score(row.get('anchor_score')):>6} "
            f"{_alignment_flag(row.get('score_delta'), row.get('close_delta_pct')):>3}"
        )
    if not display_rows:
        lines.append("  (none)")
    lines.append("")
    return lines


def _format_mover_table(
    rows: Sequence[dict[str, Any]],
    *,
    title: str,
    limit: int | None = None,
) -> list[str]:
    lines = [title, "-" * 118]
    lines.append(
        f"{'#':>3}  {'Symbol':<14} {'Company':<22} {'Dir':<5} {'ScoreΔ':>7} "
        f"{'CloseΔ%':>8} {'Cur':>6} {'Anc':>6}"
    )
    display_rows = rows if limit is None else rows[:limit]
    for index, row in enumerate(display_rows, 1):
        lines.append(
            f"{index:>3}  "
            f"{_truncate_label(row.get('symbol'), 14):<14} "
            f"{_truncate_label(row.get('company'), 22):<22} "
            f"{str(row.get('move_direction') or '—'):<5} "
            f"{_format_score(row.get('score_delta')):>7} "
            f"{_format_pct(row.get('close_delta_pct')):>8} "
            f"{_format_score(row.get('current_score')):>6} "
            f"{_format_score(row.get('anchor_score')):>6}"
        )
    if not display_rows:
        lines.append("  (none)")
    lines.append("")
    return lines


def _format_anchor_coverage_table(
    *,
    anchor_names: Sequence[str],
    anchor_coverage: dict[str, int],
    comparable_anchor_counts: dict[str, int],
    anchor_profile_bindings: dict[str, dict[str, Any]],
    requested_primary_anchor_name: str,
    effective_primary_anchor_name: str,
) -> list[str]:
    lines = ["ANCHOR COVERAGE FOR PROFILE", "-" * 118]
    lines.append(
        "  Matching uses profile family (version suffix stripped); anchor runs may use a different "
        "profile version within the same family."
    )
    lines.append(
        f"{'Anchor':<16} {'SnapshotSyms':>13} {'Comparable':>11} {'AnchorProfile':<24} {'Notes'}"
    )
    for anchor_name in _order_anchors(anchor_names):
        snapshot_count = anchor_coverage.get(anchor_name, 0)
        comparable_count = comparable_anchor_counts.get(anchor_name, 0)
        binding = anchor_profile_bindings.get(anchor_name, {})
        anchor_profile_name = binding.get("anchor_profile_name") or "—"
        notes: list[str] = []
        if anchor_name == effective_primary_anchor_name:
            notes.append("primary")
        if (
            anchor_name == requested_primary_anchor_name
            and anchor_name != effective_primary_anchor_name
        ):
            notes.append("requested but no comparable rows")
        if snapshot_count == 0:
            notes.append("no profile scores in anchor run")
        elif binding.get("profile_version_exact_match") is False:
            notes.append("family fallback")
        lines.append(
            f"{anchor_name:<16} {snapshot_count:>13} {comparable_count:>11} "
            f"{_truncate_label(anchor_profile_name, 24):<24} {', '.join(notes)}"
        )
    if effective_primary_anchor_name != requested_primary_anchor_name:
        lines.append(
            f"  -> using {effective_primary_anchor_name!r} as effective primary "
            f"(requested {requested_primary_anchor_name!r} has no comparable rows)"
        )
    lines.append("")
    return lines


def _format_progression_table(
    rows: Sequence[dict[str, Any]],
    *,
    anchor_names: Sequence[str],
    effective_primary_anchor_name: str,
    title: str,
    limit: int | None = None,
) -> list[str]:
    ordered_anchors = _order_anchors(anchor_names)
    score_headers = " ".join(f"{name[:6]:>7}" for name in ordered_anchors)
    lines = [title, "-" * 118]
    lines.append(
        f"{'#':>3}  {'Symbol':<14} {'Company':<18} "
        f"{'CurScr':>7} {score_headers} {'CurCls':>8} "
        f"{'ClsΔ%':>8}"
    )
    display_rows = rows if limit is None else rows[:limit]
    for index, row in enumerate(display_rows, 1):
        anchor_scores = " ".join(
            f"{_format_score(row.get(f'score__{name}')):>7}"
            for name in ordered_anchors
        )
        primary_anchor = effective_primary_anchor_name
        close_delta = None
        if primary_anchor:
            current_close = _coerce_optional_float(row.get("close__current"))
            anchor_close = _coerce_optional_float(
                row.get(f"close__{primary_anchor}")
            )
            if (
                current_close is not None
                and anchor_close is not None
                and anchor_close != 0
            ):
                close_delta = ((current_close - anchor_close) / anchor_close) * 100.0
        lines.append(
            f"{index:>3}  "
            f"{_truncate_label(row.get('symbol'), 14):<14} "
            f"{_truncate_label(row.get('company'), 18):<18} "
            f"{_format_score(row.get('score__current')):>7} "
            f"{anchor_scores} "
            f"{_format_score(row.get('close__current')):>8} "
            f"{_format_pct(close_delta):>8}"
        )
    if not display_rows:
        lines.append("  (none)")
    lines.append("")
    return lines


def _format_component_progression_table(
    rows: Sequence[dict[str, Any]],
    *,
    title: str,
    limit: int | None = None,
) -> list[str]:
    component_headers = " ".join(f"{name[:4]:>6}" for name in COMPONENT_FIELDS)
    lines = [title, "-" * 118]
    lines.append(
        f"{'#':>3}  {'Symbol':<14} {'Company':<18} {component_headers} {'CloseΔ%':>8}"
    )
    display_rows = rows if limit is None else rows[:limit]
    for index, row in enumerate(display_rows, 1):
        component_values = " ".join(
            f"{_format_score(row.get(f'{name}_delta')):>6}"
            for name in COMPONENT_FIELDS
        )
        lines.append(
            f"{index:>3}  "
            f"{_truncate_label(row.get('symbol'), 14):<14} "
            f"{_truncate_label(row.get('company'), 18):<18} "
            f"{component_values} "
            f"{_format_pct(row.get('close_delta_pct')):>8}"
        )
    if not display_rows:
        lines.append("  (none)")
    lines.append("")
    return lines


def _format_persistent_table(rows: Sequence[dict[str, Any]]) -> list[str]:
    lines = ["PERSISTENT RISERS", "-" * 118]
    lines.append(
        f"{'#':>3}  {'Symbol':<14} {'Company':<22} {'Hits':>5} "
        f"{'AvgΔ':>7} {'MinΔ':>7} {'AvgCloseΔ%':>11} {'Anchors'}"
    )
    for index, row in enumerate(rows, 1):
        lines.append(
            f"{index:>3}  "
            f"{_truncate_label(row.get('symbol'), 14):<14} "
            f"{_truncate_label(row.get('company'), 22):<22} "
            f"{int(row.get('positive_anchor_hits') or 0):>5} "
            f"{_format_score(row.get('avg_delta')):>7} "
            f"{_format_score(row.get('min_positive_delta')):>7} "
            f"{_format_pct(row.get('avg_price_delta_pct')):>11} "
            f"{row.get('anchor_hits') or ''}"
        )
    if not rows:
        lines.append("  (none)")
    lines.append("")
    return lines


def _build_scout_report_log(
    context: ScoutReportContext,
    *,
    comparable_anchor_counts: dict[str, int],
    anchor_profile_bindings: dict[str, dict[str, Any]],
    per_anchor: dict[str, dict[str, list[dict[str, Any]]]],
    score_price_progression: Sequence[dict[str, Any]],
    component_progression: Sequence[dict[str, Any]],
    persistent_risers: Sequence[dict[str, Any]],
    universe_drift: Sequence[dict[str, Any]],
    min_abs_score_delta: float,
    min_persistence_anchors: int,
) -> str:
    progression_anchors = [
        anchor
        for anchor in _order_anchors(context.anchor_names)
        if context.anchor_coverage.get(anchor, 0) > 0
    ]
    lines = [
        "BACKWARDS SCOUT REPORT",
        "=" * 118,
        f"backwards_analysis_id: {context.backwards_analysis_id}",
        f"database_path: {context.database_path.as_posix()}",
        f"current_run_id: {context.current_run_id}",
        f"current_snapshot: {context.current_snapshot_label}",
        f"anchors: {', '.join(context.anchor_names)}",
        f"profile: {context.profile_name}",
        f"horizon: {context.horizon_name}",
        f"requested_primary_anchor: {context.requested_primary_anchor_name}",
        f"effective_primary_anchor: {context.effective_primary_anchor_name}",
        f"top_n: {context.top_n}",
        "",
    ]

    lines.extend(
        _format_anchor_coverage_table(
            anchor_names=context.anchor_names,
            anchor_coverage=context.anchor_coverage,
            comparable_anchor_counts=comparable_anchor_counts,
            anchor_profile_bindings=anchor_profile_bindings,
            requested_primary_anchor_name=context.requested_primary_anchor_name,
            effective_primary_anchor_name=context.effective_primary_anchor_name,
        )
    )

    lines.extend(
        _format_progression_table(
            score_price_progression,
            anchor_names=progression_anchors,
            effective_primary_anchor_name=context.effective_primary_anchor_name,
            title=(
                "SCORE + PRICE PROGRESSION ACROSS ANCHORS "
                f"(top symbols by {context.effective_primary_anchor_name} score gain)"
            ),
            limit=context.top_n,
        )
    )
    lines.extend(
        _format_component_progression_table(
            component_progression,
            title=(
                "COMPONENT DELTA PROGRESSION "
                f"({context.effective_primary_anchor_name} vs current)"
            ),
            limit=context.top_n,
        )
    )

    for anchor_name in _order_anchors(context.comparable_anchor_names):
        anchor_data = per_anchor.get(anchor_name, {})
        is_primary = anchor_name == context.effective_primary_anchor_name
        limit = context.top_n if is_primary else COMPACT_ANCHOR_TOP_N
        label = f"ANCHOR: {anchor_name}" + (" (primary)" if is_primary else "")
        lines.append(label)
        lines.append("=" * 118)

        lines.extend(
            _format_progressor_table(
                anchor_data.get("top_progressors", []),
                title="TOP SCORE PROGRESSORS",
                limit=limit,
            )
        )
        lines.extend(
            _format_mover_table(
                anchor_data.get("significant_movers", []),
                title=f"SIGNIFICANT MOVERS (|score_delta| >= {min_abs_score_delta})",
                limit=limit,
            )
        )
        if is_primary:
            lines.extend(
                _format_progressor_table(
                    anchor_data.get("price_aligned", []),
                    title="PRICE-ALIGNED IMPROVERS (score up, price up)",
                    limit=limit,
                )
            )
            lines.extend(
                _format_progressor_table(
                    anchor_data.get("price_divergent", []),
                    title="PRICE DIVERGENT (score up, price down)",
                    limit=limit,
                )
            )

    lines.extend(_format_persistent_table(persistent_risers))
    lines.append(
        f"(persistent = positive score_delta on >= {min_persistence_anchors} "
        f"comparable anchors: {', '.join(context.comparable_anchor_names) or 'none'})"
    )
    lines.append("")

    lines.append("SCOUT NOTES — UNIVERSE DRIFT")
    lines.append("-" * 118)
    for row in universe_drift:
        lines.append(
            f"  {row.get('anchor_name')}: "
            f"added={int(row.get('symbols_added') or 0)}, "
            f"dropped={int(row.get('symbols_dropped') or 0)}, "
            f"retained={int(row.get('symbols_retained') or 0)}"
        )
    if not universe_drift:
        lines.append("  (none)")
    lines.append("")
    return "\n".join(lines)


def _run_single_scout_report(
    *,
    resolved_db: Path,
    run_metadata: dict[str, Any],
    resolved_anchors: tuple[str, ...],
    profile_name: str,
    horizon_name: str,
    primary_anchor_name: str,
    top_n: int,
    min_abs_score_delta: float,
    min_persistence_anchors: int,
    export_csv: bool,
    report_root: Path,
) -> dict[str, Any]:
    backwards_analysis_id = str(run_metadata["backwards_analysis_id"])
    run_dir = resolved_db.parent
    report_root.mkdir(parents=True, exist_ok=True)
    for stale_path in report_root.glob("*.csv"):
        stale_path.unlink(missing_ok=True)
    highlights_log = report_root / "backwards_scout_report.log"

    anchor_coverage = _fetch_profile_anchor_coverage(
        resolved_db,
        backwards_analysis_id=backwards_analysis_id,
        profile_name=profile_name,
        horizon_name=horizon_name,
    )
    comparable_anchor_counts = _fetch_comparable_anchor_counts(
        resolved_db,
        backwards_analysis_id=backwards_analysis_id,
        profile_name=profile_name,
        horizon_name=horizon_name,
        anchor_names=resolved_anchors,
    )
    anchor_profile_bindings = _fetch_anchor_profile_bindings(
        resolved_db,
        backwards_analysis_id=backwards_analysis_id,
        profile_name=profile_name,
        horizon_name=horizon_name,
        anchor_names=resolved_anchors,
    )
    effective_primary_anchor_name = _resolve_effective_primary_anchor(
        requested_primary_anchor_name=primary_anchor_name,
        anchor_names=resolved_anchors,
        comparable_anchor_counts=comparable_anchor_counts,
    )
    comparable_anchor_names = tuple(
        anchor
        for anchor in _order_anchors(resolved_anchors)
        if comparable_anchor_counts.get(anchor, 0) > 0
    )

    context = ScoutReportContext(
        database_path=resolved_db,
        run_dir=run_dir,
        backwards_analysis_id=backwards_analysis_id,
        current_run_id=str(run_metadata.get("current_run_id") or ""),
        current_snapshot_label=str(run_metadata.get("current_snapshot_label") or ""),
        anchor_names=resolved_anchors,
        profile_name=profile_name,
        horizon_name=horizon_name,
        requested_primary_anchor_name=primary_anchor_name,
        effective_primary_anchor_name=effective_primary_anchor_name,
        comparable_anchor_names=comparable_anchor_names,
        anchor_coverage=anchor_coverage,
        top_n=top_n,
    )

    persistent_risers = _fetch_persistent_risers(
        resolved_db,
        backwards_analysis_id=backwards_analysis_id,
        profile_name=profile_name,
        horizon_name=horizon_name,
        comparable_anchor_names=comparable_anchor_names,
        min_persistence_anchors=min_persistence_anchors,
        top_n=top_n,
    )
    universe_drift = _fetch_universe_drift(
        resolved_db,
        profile_name=profile_name,
        horizon_name=horizon_name,
    )

    per_anchor: dict[str, dict[str, list[dict[str, Any]]]] = {}
    exported_files: dict[str, str] = {}

    for anchor_name in comparable_anchor_names:
        limit = (
            top_n
            if anchor_name == effective_primary_anchor_name
            else COMPACT_ANCHOR_TOP_N
        )
        anchor_data = {
            "top_progressors": _fetch_top_progressors(
                resolved_db,
                backwards_analysis_id=backwards_analysis_id,
                profile_name=profile_name,
                horizon_name=horizon_name,
                anchor_name=anchor_name,
                top_n=limit,
            ),
            "significant_movers": _fetch_significant_movers(
                resolved_db,
                backwards_analysis_id=backwards_analysis_id,
                profile_name=profile_name,
                horizon_name=horizon_name,
                anchor_name=anchor_name,
                top_n=limit,
                min_abs_score_delta=min_abs_score_delta,
            ),
        }
        if anchor_name == effective_primary_anchor_name:
            anchor_data["price_aligned"] = _fetch_price_aligned_improvers(
                resolved_db,
                backwards_analysis_id=backwards_analysis_id,
                profile_name=profile_name,
                horizon_name=horizon_name,
                anchor_name=anchor_name,
                top_n=top_n,
            )
            anchor_data["price_divergent"] = _fetch_price_divergent(
                resolved_db,
                backwards_analysis_id=backwards_analysis_id,
                profile_name=profile_name,
                horizon_name=horizon_name,
                anchor_name=anchor_name,
                top_n=top_n,
            )
        per_anchor[anchor_name] = anchor_data

        if export_csv:
            for key, rows in anchor_data.items():
                csv_path = report_root / f"{key}__{anchor_name}.csv"
                if _write_csv(csv_path, rows):
                    exported_files[f"{key}__{anchor_name}"] = csv_path.as_posix()

    primary_progressors = per_anchor.get(effective_primary_anchor_name, {}).get(
        "top_progressors", []
    )
    progression_symbols = [str(row["symbol"]) for row in primary_progressors[:top_n]]
    if not progression_symbols:
        fallback_movers = per_anchor.get(effective_primary_anchor_name, {}).get(
            "significant_movers", []
        )
        progression_symbols = [
            str(row["symbol"]) for row in fallback_movers[:top_n]
        ]

    progression_anchors = [
        anchor
        for anchor in _order_anchors(resolved_anchors)
        if anchor_coverage.get(anchor, 0) > 0
    ]
    score_price_progression = _fetch_symbol_score_price_progression(
        resolved_db,
        backwards_analysis_id=backwards_analysis_id,
        profile_name=profile_name,
        horizon_name=horizon_name,
        anchor_names=progression_anchors,
        symbols=progression_symbols,
    )
    component_progression = _fetch_component_progression(
        resolved_db,
        backwards_analysis_id=backwards_analysis_id,
        profile_name=profile_name,
        anchor_name=effective_primary_anchor_name,
        symbols=progression_symbols,
    )

    if export_csv:
        progression_csv = report_root / "score_price_progression.csv"
        if _write_csv(progression_csv, score_price_progression):
            exported_files["score_price_progression"] = progression_csv.as_posix()
        component_csv = report_root / "component_progression.csv"
        if _write_csv(component_csv, component_progression):
            exported_files["component_progression"] = component_csv.as_posix()
        persistent_csv = report_root / "persistent_risers.csv"
        if _write_csv(persistent_csv, persistent_risers):
            exported_files["persistent_risers"] = persistent_csv.as_posix()

    log_text = _build_scout_report_log(
        context,
        comparable_anchor_counts=comparable_anchor_counts,
        anchor_profile_bindings=anchor_profile_bindings,
        per_anchor=per_anchor,
        score_price_progression=score_price_progression,
        component_progression=component_progression,
        persistent_risers=persistent_risers,
        universe_drift=universe_drift,
        min_abs_score_delta=min_abs_score_delta,
        min_persistence_anchors=min_persistence_anchors,
    )
    highlights_log.write_text(log_text, encoding="utf-8")

    return {
        "profile_name": profile_name,
        "horizon_name": horizon_name,
        "output_dir": report_root.as_posix(),
        "highlights_log": highlights_log.as_posix(),
        "requested_primary_anchor_name": primary_anchor_name,
        "effective_primary_anchor_name": effective_primary_anchor_name,
        "comparable_anchor_names": list(comparable_anchor_names),
        "anchor_coverage": anchor_coverage,
        "score_price_progression_count": len(score_price_progression),
        "component_progression_count": len(component_progression),
        "persistent_risers_count": len(persistent_risers),
        "per_anchor_counts": {
            anchor: {key: len(rows) for key, rows in data.items()}
            for anchor, data in per_anchor.items()
        },
        "exported_files": exported_files,
    }


def run_backwards_prediction_scout_report(
    *,
    run_folder_pattern: str | None = None,
    database_path: str | Path | None = None,
    backwards_runs_root: str | Path | None = None,
    profile_name: str | Sequence[str],
    horizon_name: str | Sequence[str] = DEFAULT_HORIZON_NAME,
    anchor_names: Sequence[str] | None = None,
    primary_anchor_name: str = DEFAULT_PRIMARY_ANCHOR,
    top_n: int = DEFAULT_TOP_N,
    min_abs_score_delta: float = DEFAULT_MIN_ABS_SCORE_DELTA,
    min_persistence_anchors: int = DEFAULT_MIN_PERSISTENCE_ANCHORS,
    export_csv: bool = True,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile_names = _normalize_string_selection(profile_name, param_name="profile_name")
    horizon_names = _normalize_string_selection(horizon_name, param_name="horizon_name")

    resolved_db = resolve_backwards_analysis_database(
        run_folder_pattern=run_folder_pattern,
        database_path=database_path,
        backwards_runs_root=backwards_runs_root,
    )
    with BackwardsPredictionDuckDBStore(database_path=resolved_db) as store:
        store.create_starter_views()

    run_metadata = _load_run_metadata(resolved_db)
    backwards_analysis_id = str(run_metadata["backwards_analysis_id"])
    resolved_anchors = _load_anchor_names(
        resolved_db,
        backwards_analysis_id=backwards_analysis_id,
        anchor_names=anchor_names,
    )
    if primary_anchor_name not in resolved_anchors:
        raise ValueError(
            f"primary_anchor_name {primary_anchor_name!r} not in available anchors: "
            f"{list(resolved_anchors)}"
        )

    run_dir = resolved_db.parent
    scout_reports_root = (
        Path(output_dir) if output_dir is not None else run_dir / "scout_reports"
    )

    combination_results: list[dict[str, Any]] = []
    for selected_profile, selected_horizon in product(profile_names, horizon_names):
        combo_output_dir = scout_reports_root / f"{selected_profile}__{selected_horizon}"
        combination_results.append(
            _run_single_scout_report(
                resolved_db=resolved_db,
                run_metadata=run_metadata,
                resolved_anchors=resolved_anchors,
                profile_name=selected_profile,
                horizon_name=selected_horizon,
                primary_anchor_name=primary_anchor_name,
                top_n=top_n,
                min_abs_score_delta=min_abs_score_delta,
                min_persistence_anchors=min_persistence_anchors,
                export_csv=export_csv,
                report_root=combo_output_dir,
            )
        )

    elapsed = time.perf_counter() - started
    batch_result: dict[str, Any] = {
        "backwards_analysis_id": backwards_analysis_id,
        "database_path": resolved_db.as_posix(),
        "scout_reports_root": scout_reports_root.as_posix(),
        "profile_names": list(profile_names),
        "horizon_names": list(horizon_names),
        "combination_count": len(combination_results),
        "results": combination_results,
        "primary_anchor_name": primary_anchor_name,
        "anchor_names": list(resolved_anchors),
        "top_n": top_n,
        "timing_seconds": {"total": elapsed},
    }
    if len(combination_results) == 1:
        batch_result.update(combination_results[0])
    return batch_result


__all__ = [
    "DEFAULT_PRIMARY_ANCHOR",
    "DEFAULT_TOP_N",
    "resolve_backwards_analysis_database",
    "run_backwards_prediction_scout_report",
]
