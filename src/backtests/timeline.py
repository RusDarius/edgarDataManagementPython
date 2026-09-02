from __future__ import annotations

import json
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from backtests.config import BacktestConfig
from backtests.contracts import ArtifactDay, OutcomeRow


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _import_duckdb():
    import duckdb

    return duckdb


def _parse_day_label(day_label: str) -> date | None:
    text = str(day_label or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d_%m_%Y").date()
    except ValueError:
        return None


def _day_label(value: date) -> str:
    return value.strftime("%d_%m_%Y")


def _parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    head = text[:10]
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _date_in_window(
    value: date,
    *,
    start_day_label: str | None,
    end_day_label: str | None,
) -> bool:
    start = _parse_day_label(start_day_label) if start_day_label else None
    end = _parse_day_label(end_day_label) if end_day_label else None
    if start is not None and value < start:
        return False
    if end is not None and value > end:
        return False
    return True


# ---------------------------------------------------------------------------
# Artifact calendar discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AllFieldsRef:
    database: Path
    run_id: str | None
    created_at_utc: datetime | None


@dataclass(frozen=True)
class _PredictionRef:
    database: Path
    run_id: str
    created_at_utc: datetime | None


@dataclass(frozen=True)
class _ProjectionRef:
    metadata_path: Path
    created_at_utc: datetime | None


def _latest_all_fields_run(database_path: Path) -> tuple[str | None, datetime | None]:
    duckdb = _import_duckdb()
    conn = duckdb.connect(database_path.as_posix(), read_only=True)
    try:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "run_metadata" in tables:
            row = conn.execute(
                """
                SELECT run_id, created_at_utc
                FROM run_metadata
                WHERE suite_name = 'tradingview_all_fields_export_duckdb'
                   OR suite_name IS NULL
                   OR suite_name = ''
                ORDER BY created_at_utc DESC NULLS LAST, run_id DESC
                LIMIT 1
                """
            ).fetchone()
            if row:
                return str(row[0] or ""), _parse_iso_datetime(row[1])
        if "all_fields_rows" in tables:
            row = conn.execute(
                """
                SELECT run_id
                FROM all_fields_rows
                GROUP BY run_id
                ORDER BY run_id DESC
                LIMIT 1
                """
            ).fetchone()
            if row:
                return str(row[0] or ""), None
    finally:
        conn.close()
    return None, None


def discover_all_fields_days(
    *,
    all_fields_root: Path,
    start_day_label: str | None,
    end_day_label: str | None,
    max_days: int | None,
) -> dict[date, _AllFieldsRef]:
    per_day: dict[date, Path] = {}
    for database_path in all_fields_root.glob("*/*.duckdb"):
        if not database_path.name.startswith("tradingview_all_fields_"):
            continue
        day = _parse_day_label(database_path.parent.name)
        if day is None:
            continue
        if not _date_in_window(
            day, start_day_label=start_day_label, end_day_label=end_day_label
        ):
            continue
        existing = per_day.get(day)
        if existing is None or database_path.stat().st_mtime > existing.stat().st_mtime:
            per_day[day] = database_path

    ordered_days = sorted(per_day)
    if max_days is not None and max_days > 0:
        ordered_days = ordered_days[-max_days:]
    resolved: dict[date, _AllFieldsRef] = {}
    for day in ordered_days:
        database = per_day[day]
        run_id, created_at = _latest_all_fields_run(database)
        resolved[day] = _AllFieldsRef(
            database=database.resolve(), run_id=run_id, created_at_utc=created_at
        )
    return resolved


def discover_prediction_runs(prediction_root: Path) -> dict[date, _PredictionRef]:
    db_paths = list(prediction_root.glob("iso_year=*/week=*/move_prediction_*.duckdb"))
    if not db_paths:
        db_paths = list(prediction_root.rglob("move_prediction_*.duckdb"))

    by_day: dict[date, _PredictionRef] = {}
    duckdb = _import_duckdb()
    for db_path in sorted(db_paths):
        conn = duckdb.connect(db_path.as_posix(), read_only=True)
        try:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'main'"
                ).fetchall()
            }
            if "run_metadata" not in tables:
                continue
            rows = conn.execute(
                """
                SELECT run_id, run_date_utc, created_at_utc
                FROM run_metadata
                ORDER BY created_at_utc ASC NULLS LAST, run_id ASC
                """
            ).fetchall()
        finally:
            conn.close()
        for run_id, run_date_utc, created_at_utc in rows:
            as_of = _parse_iso_date(run_date_utc) or _parse_iso_date(created_at_utc)
            if as_of is None:
                continue
            created = _parse_iso_datetime(created_at_utc)
            previous = by_day.get(as_of)
            if previous is None:
                by_day[as_of] = _PredictionRef(
                    database=db_path.resolve(),
                    run_id=str(run_id or ""),
                    created_at_utc=created,
                )
                continue
            prior_stamp = previous.created_at_utc or datetime.min
            next_stamp = created or datetime.min
            if next_stamp >= prior_stamp:
                by_day[as_of] = _PredictionRef(
                    database=db_path.resolve(),
                    run_id=str(run_id or ""),
                    created_at_utc=created,
                )
    return by_day


def _edge_scan_day(payload: Mapping[str, Any]) -> date | None:
    scan_day = _parse_iso_date(payload.get("scan_day"))
    if scan_day is not None:
        return scan_day
    end_day = _parse_day_label(str(payload.get("end_day_label") or ""))
    return end_day


def discover_edge_parent_runs(edge_runs_root: Path) -> dict[date, Path]:
    per_day: dict[date, tuple[float, Path]] = {}
    if not edge_runs_root.exists():
        return {}
    for candidate in edge_runs_root.iterdir():
        if not candidate.is_dir():
            continue
        manifest_path = candidate / "parent_run_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        scan_day = _edge_scan_day(payload)
        if scan_day is None:
            continue
        mtime = candidate.stat().st_mtime
        previous = per_day.get(scan_day)
        if previous is None or mtime >= previous[0]:
            per_day[scan_day] = (mtime, candidate.resolve())
    return {day: value[1] for day, value in per_day.items()}


def discover_market_timing_runs(market_timing_root: Path) -> dict[date, Path]:
    by_day: dict[date, tuple[datetime | None, Path]] = {}
    if not market_timing_root.exists():
        return {}
    for manifest_path in market_timing_root.glob("*/market_timing_policy_manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        scan_day = _parse_iso_date(payload.get("scan_day"))
        if scan_day is None:
            continue
        created_at = _parse_iso_datetime(payload.get("created_at_utc"))
        previous = by_day.get(scan_day)
        if previous is None or (created_at or datetime.min) >= (previous[0] or datetime.min):
            by_day[scan_day] = (created_at, manifest_path.resolve())
    return {day: value[1] for day, value in by_day.items()}


def discover_financial_projection_runs(
    financial_projection_root: Path,
) -> dict[date, dict[str, _ProjectionRef]]:
    by_day: dict[date, dict[str, _ProjectionRef]] = {}
    if not financial_projection_root.exists():
        return by_day
    for metadata_path in financial_projection_root.glob("*/*/run_metadata.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        source_day: date | None = None
        all_fields_database = str(payload.get("all_fields_database") or "")
        if all_fields_database:
            source_day = _parse_day_label(Path(all_fields_database).parent.name)
        if source_day is None:
            load_meta = payload.get("load_metadata") or {}
            source_day = _parse_iso_date(load_meta.get("source_created_at_utc"))
        if source_day is None:
            source_day = _parse_day_label(payload.get("day_label") or "")
        if source_day is None:
            continue

        run_id = str(payload.get("run_id") or metadata_path.parent.name)
        suite_name = str(payload.get("suite_name") or "").lower()
        mode = "growth" if ("growth" in suite_name or run_id.startswith("fingrowth_")) else "price"
        created_at = _parse_iso_datetime(payload.get("created_at_utc"))
        per_mode = by_day.setdefault(source_day, {})
        previous = per_mode.get(mode)
        if previous is None:
            per_mode[mode] = _ProjectionRef(
                metadata_path=metadata_path.resolve(), created_at_utc=created_at
            )
            continue
        if (created_at or datetime.min) >= (previous.created_at_utc or datetime.min):
            per_mode[mode] = _ProjectionRef(
                metadata_path=metadata_path.resolve(), created_at_utc=created_at
            )
    return by_day


TRef = TypeVar("TRef")


def _lookup_ref_on_or_before(
    lookup: Mapping[date, TRef],
    *,
    target_day: date,
    allow_on_or_before: bool,
) -> TRef | None:
    if not lookup:
        return None
    if not allow_on_or_before:
        return lookup.get(target_day)
    days = sorted(lookup)
    position = bisect_right(days, target_day) - 1
    if position < 0:
        return None
    return lookup.get(days[position])


def _discover_upside_manifests(
    edge_parent_dir: Path | None,
) -> tuple[Path | None, Path | None]:
    if edge_parent_dir is None:
        return None, None
    lens_root = edge_parent_dir
    aggregate = edge_parent_dir / "aggregate"
    if aggregate.exists():
        lens_root = aggregate
    upside_dir = lens_root / "upside_opportunity_scan"
    opportunity_manifest = upside_dir / "upside_opportunity_scan_manifest.json"
    move_manifest = upside_dir / "upside_move_potential_scan_manifest.json"
    return (
        opportunity_manifest if opportunity_manifest.exists() else None,
        move_manifest if move_manifest.exists() else None,
    )


def build_artifact_calendar(config: BacktestConfig) -> list[ArtifactDay]:
    all_fields_refs = discover_all_fields_days(
        all_fields_root=config.paths.all_fields_root,
        start_day_label=config.calendar.start_day_label,
        end_day_label=config.calendar.end_day_label,
        max_days=config.calendar.max_days,
    )
    prediction_refs = discover_prediction_runs(config.paths.prediction_root)
    edge_refs = discover_edge_parent_runs(config.paths.edge_runs_root)
    timing_refs = discover_market_timing_runs(config.paths.market_timing_root)
    projection_refs = discover_financial_projection_runs(config.paths.financial_projection_root)

    calendar_days: list[ArtifactDay] = []
    for as_of in sorted(all_fields_refs):
        all_fields_ref = all_fields_refs[as_of]
        prediction_ref = _lookup_ref_on_or_before(
            prediction_refs,
            target_day=as_of,
            allow_on_or_before=config.calendar.align_prediction_on_or_before,
        )
        edge_parent = _lookup_ref_on_or_before(
            edge_refs,
            target_day=as_of,
            allow_on_or_before=config.calendar.align_edge_on_or_before,
        )
        timing_manifest = _lookup_ref_on_or_before(
            timing_refs,
            target_day=as_of,
            allow_on_or_before=config.calendar.align_timing_on_or_before,
        )
        proj_refs = projection_refs.get(as_of, {})
        growth_meta = proj_refs.get("growth")
        price_meta = proj_refs.get("price")
        upside_manifest, upside_move_manifest = _discover_upside_manifests(edge_parent)
        calendar_days.append(
            ArtifactDay(
                as_of_date=as_of,
                day_label=_day_label(as_of),
                all_fields_database=all_fields_ref.database,
                all_fields_run_id=all_fields_ref.run_id,
                prediction_database=prediction_ref.database if prediction_ref else None,
                prediction_run_id=prediction_ref.run_id if prediction_ref else None,
                edge_parent_dir=edge_parent,
                upside_opportunity_manifest=upside_manifest,
                upside_move_manifest=upside_move_manifest,
                market_timing_manifest=timing_manifest,
                financial_projection_growth_metadata=(
                    growth_meta.metadata_path if growth_meta else None
                ),
                financial_projection_price_metadata=(
                    price_meta.metadata_path if price_meta else None
                ),
            )
        )
    return calendar_days


def build_calendar_coverage_rows(calendar_days: Iterable[ArtifactDay]) -> list[dict[str, Any]]:
    rows = []
    for day in calendar_days:
        rows.append(
            {
                "as_of_date": day.as_of_date.isoformat(),
                "day_label": day.day_label,
                "has_all_fields": int(day.all_fields_database is not None),
                "has_prediction": int(
                    day.prediction_database is not None and bool(day.prediction_run_id)
                ),
                "has_edge_parent": int(day.edge_parent_dir is not None),
                "has_upside_scan": int(day.upside_opportunity_manifest is not None),
                "has_timing_run": int(day.market_timing_manifest is not None),
                "has_projection_growth": int(
                    day.financial_projection_growth_metadata is not None
                ),
                "has_projection_price": int(
                    day.financial_projection_price_metadata is not None
                ),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Forward labels / outcomes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelBuildResult:
    rows: list[OutcomeRow]
    metadata: dict[str, Any]


def _load_day_closes(day: ArtifactDay) -> dict[str, float]:
    if day.all_fields_database is None or not day.all_fields_database.exists():
        return {}
    duckdb = _import_duckdb()
    conn = duckdb.connect(day.all_fields_database.as_posix(), read_only=True)
    try:
        columns = {
            str(row[0])
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='main' AND table_name='all_fields_rows'"
            ).fetchall()
        }
        if "close" not in columns:
            return {}
        if day.all_fields_run_id:
            rows = conn.execute(
                """
                SELECT symbol, TRY_CAST(close AS DOUBLE) AS close_price
                FROM all_fields_rows
                WHERE run_id = ?
                """,
                [day.all_fields_run_id],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT symbol, TRY_CAST(close AS DOUBLE) AS close_price
                FROM all_fields_rows
                """
            ).fetchall()
    finally:
        conn.close()
    closes: dict[str, float] = {}
    for symbol, close_price in rows:
        key = str(symbol or "").strip().upper()
        if not key or close_price in (None, ""):
            continue
        try:
            closes[key] = float(close_price)
        except (TypeError, ValueError):
            continue
    return closes


def _build_multi_horizon_outcomes(
    calendar_days: Sequence[ArtifactDay], horizons: Sequence[int]
) -> list[OutcomeRow]:
    valid_horizons = sorted({max(1, int(value)) for value in horizons})
    if not valid_horizons:
        return []

    closes_by_symbol: dict[str, list[tuple[date, str, float]]] = defaultdict(list)
    for day in sorted(calendar_days, key=lambda item: item.as_of_date):
        closes = _load_day_closes(day)
        for symbol, close_value in closes.items():
            closes_by_symbol[symbol].append((day.as_of_date, day.day_label, close_value))

    outcomes: list[OutcomeRow] = []
    for symbol, points in closes_by_symbol.items():
        points.sort(key=lambda item: item[0])
        for index, (entry_date, entry_label, entry_close) in enumerate(points):
            for horizon in valid_horizons:
                exit_index = index + horizon
                if exit_index >= len(points):
                    continue
                _, exit_label, exit_close = points[exit_index]
                if entry_close == 0:
                    continue
                fwd_return = ((exit_close / entry_close) - 1.0) * 100.0
                outcomes.append(
                    OutcomeRow(
                        as_of_date=entry_date,
                        symbol=symbol,
                        horizon_days=horizon,
                        forward_return_pct=round(float(fwd_return), 6),
                        entry_close=float(entry_close),
                        exit_close=float(exit_close),
                        entry_day_label=entry_label,
                        exit_day_label=exit_label,
                    )
                )
    return outcomes


def _wrap_close_forward_reference(
    calendar_days: Sequence[ArtifactDay],
    *,
    horizons: Sequence[int],
    output_dir: Path,
) -> dict[str, Any]:
    # Optional trace output from the existing all-fields analyzer utility.
    try:
        from data_analysis_scripts.trading_view_all_fields_metric_pattern_analyzer import (
            compute_cross_run_close_returns,
        )
    except Exception as exc:  # pragma: no cover
        return {"status": "skipped", "reason": f"import_error: {exc}"}

    input_paths = [
        day.all_fields_database
        for day in sorted(calendar_days, key=lambda item: item.as_of_date)
        if day.all_fields_database is not None and day.all_fields_database.exists()
    ]
    if not input_paths:
        return {"status": "skipped", "reason": "no_all_fields_databases"}

    max_forward_days = max(max(1, int(value)) for value in horizons)
    try:
        result = compute_cross_run_close_returns(
            input_paths=input_paths,
            output_dir=output_dir,
            max_forward_days=max_forward_days,
            include_end_scan_forward_return=True,
        )
        return {"status": "ok", **result}
    except Exception as exc:  # pragma: no cover
        return {"status": "error", "reason": str(exc)}


def build_outcome_rows(
    calendar_days: Sequence[ArtifactDay],
    *,
    horizons: Sequence[int],
    output_dir: str | Path,
) -> LabelBuildResult:
    resolved_output = Path(output_dir)
    resolved_output.mkdir(parents=True, exist_ok=True)
    reference_dir = resolved_output / "close_forward_reference"
    reference_dir.mkdir(parents=True, exist_ok=True)
    reference = _wrap_close_forward_reference(
        calendar_days, horizons=horizons, output_dir=reference_dir
    )
    rows = _build_multi_horizon_outcomes(calendar_days, horizons)
    metadata = {
        "row_count": len(rows),
        "horizons": sorted({int(value) for value in horizons}),
        "close_forward_reference": reference,
    }
    return LabelBuildResult(rows=rows, metadata=metadata)

