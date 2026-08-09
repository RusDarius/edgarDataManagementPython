"""Regime context overlay for daily move-prediction scans.

Scores symbols against stable predictors from scan-period close-forward tracking,
using move-prediction scan fields first and backfilling from the latest all-fields
DuckDB export when exact columns are missing.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import duckdb

from data_analysis_scripts.trading_view_move_prediction_pattern_discovery import (
    discover_latest_all_fields_db,
)
from generic_utils.log_to_files_util import log_to_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Match move-prediction profile logs: EXCHANGE:TICKER when exchange is known.
SYMBOL_LOG_WIDTH = 18

BULLISH_PREDICTORS_DEFAULT = (
    "ATRP|1W",
    "ATRP",
    "ADRP|15",
    "ADRP|1W",
    "ADX-DI|1M",
    "ADX-DI_50|1M",
    "relative_volume",
)

BEARISH_WARNING_PREDICTORS_DEFAULT = (
    "RSI21[1]|1M",
    "Stoch.K_14_1_3|1M",
    "W.R|1M",
    "Recommend.MA|1M",
    "oper_income_ttm",
    "ebitda_ttm",
)


@dataclass(frozen=True)
class RegimeContextConfig:
    config_id: str
    regime_label: str
    scan_period_run_root: Path
    all_fields_root: Path
    all_fields_db_path: Path | None
    performance_target: str
    stale_all_fields_days: int
    promoted_predictors: tuple[str, ...]
    demoted_predictors: tuple[str, ...]
    warning_predictors: tuple[str, ...]
    bullish_predictors: tuple[str, ...]
    playbook_a_min_relative_volume: float
    top_n_focus: int
    scan_column_aliases: dict[str, str]
    scan_proxies_last_resort: dict[str, str]
    source_path: Path


@dataclass
class PredictorValue:
    predictor_field: str
    value: float | None
    source: str


@dataclass
class RegimeContextRecord:
    symbol: str
    regime_fit_score: float
    signals_matched: int
    warning_flag_count: int
    active_mgmt_tier: str
    atrp_1w: float | None
    relative_volume: float | None
    promoted_field_ranks_json: str
    fields_source_summary: str
    playbook_a_fit: bool
    predictor_values: dict[str, PredictorValue] = field(default_factory=dict)


@dataclass
class RegimeOverlayResult:
    config: RegimeContextConfig
    by_symbol: dict[str, RegimeContextRecord]
    weights: list[tuple[str, float, float]]
    all_fields_db_path: Path | None
    all_fields_day_label: str | None
    all_fields_stale_warning: str | None
    aggregate_db_path: Path


def load_regime_context_config(path: str | Path) -> RegimeContextConfig:
    config_path = Path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    field_resolution = payload.get("field_resolution") or {}
    scan_period_root = payload.get("scan_period_run_root") or ""
    all_fields_root = payload.get("all_fields_root") or ""
    all_fields_override = payload.get("all_fields_db_path")
    return RegimeContextConfig(
        config_id=str(payload.get("config_id") or "regime_context"),
        regime_label=str(payload.get("regime_label") or ""),
        scan_period_run_root=(
            Path(scan_period_root)
            if Path(scan_period_root).is_absolute()
            else PROJECT_ROOT / scan_period_root
        ),
        all_fields_root=(
            Path(all_fields_root)
            if Path(all_fields_root).is_absolute()
            else PROJECT_ROOT / all_fields_root
        ),
        all_fields_db_path=(
            Path(all_fields_override)
            if all_fields_override
            else None
        ),
        performance_target=str(
            payload.get("performance_target") or "period_return_pct"
        ),
        stale_all_fields_days=int(payload.get("stale_all_fields_days") or 8),
        promoted_predictors=tuple(payload.get("promoted_predictors") or ()),
        demoted_predictors=tuple(payload.get("demoted_predictors") or ()),
        warning_predictors=tuple(
            payload.get("warning_predictors") or BEARISH_WARNING_PREDICTORS_DEFAULT
        ),
        bullish_predictors=tuple(
            payload.get("bullish_predictors") or BULLISH_PREDICTORS_DEFAULT
        ),
        playbook_a_min_relative_volume=float(
            payload.get("playbook_a_min_relative_volume") or 1.5
        ),
        top_n_focus=int(payload.get("top_n_focus") or 30),
        scan_column_aliases=dict(field_resolution.get("scan_column_aliases") or {}),
        scan_proxies_last_resort=dict(
            field_resolution.get("scan_proxies_last_resort") or {}
        ),
        source_path=config_path,
    )


def resolve_all_fields_database(
    config: RegimeContextConfig,
) -> tuple[Path | None, str | None, str | None]:
    """Return (db_path, day_label, stale_warning)."""
    db_path = config.all_fields_db_path
    if db_path is None:
        db_path = discover_latest_all_fields_db(config.all_fields_root)
    if db_path is None or not db_path.exists():
        return None, None, (
            "No all-fields DuckDB export found — using scan fields and proxies only. "
            "Run export_all_tradingview_fields_duckdb() for exact historical predictors."
        )

    day_label = db_path.parent.name
    stale_warning: str | None = None
    try:
        day_parts = day_label.split("_")
        if len(day_parts) == 3:
            day, month, year = (int(day_parts[0]), int(day_parts[1]), int(day_parts[2]))
            export_date = datetime(year, month, day, tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - export_date).days
            if age_days > config.stale_all_fields_days:
                stale_warning = (
                    f"All-fields export is {age_days} days old ({day_label}) — "
                    f"threshold is {config.stale_all_fields_days} days. "
                    "Consider re-running export_all_tradingview_fields_duckdb()."
                )
    except (TypeError, ValueError):
        pass

    return db_path, day_label, stale_warning


def fetch_predictor_weights(
    aggregate_db: Path,
    predictor_fields: Sequence[str],
    performance_target: str,
) -> list[tuple[str, float, float]]:
    aggregate_path = aggregate_db / "aggregates" / "all_fields_pattern_aggregate.duckdb"
    if not aggregate_path.exists():
        raise FileNotFoundError(
            f"Aggregate stability database not found at {aggregate_path}"
        )

    con = duckdb.connect(str(aggregate_path), read_only=True)
    try:
        placeholders = ", ".join("?" for _ in predictor_fields)
        rows = con.execute(
            f"""
            SELECT predictor_field,
                   rank_stability_score,
                   ABS(rank_stability_score) AS weight
            FROM cross_run_field_stability
            WHERE performance_field = ?
              AND predictor_field IN ({placeholders})
            ORDER BY ABS(rank_stability_score) DESC
            """,
            [performance_target, *list(predictor_fields)],
        ).fetchall()
    finally:
        con.close()

    return [
        (str(field_name), float(rank_stability_score), float(weight))
        for field_name, rank_stability_score, weight in rows
    ]


def _coerce_finite_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _symbol_lookup_keys(symbol: str) -> list[str]:
    keys = [symbol]
    if ":" in symbol:
        keys.append(symbol.split(":", 1)[1])
    return keys


def _row_get(row: Mapping[str, Any], column: str) -> Any:
    if column in row:
        return row.get(column)
    return None


def _list_all_fields_columns(db_path: Path) -> set[str]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        columns = con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'all_fields_rows'
            """
        ).fetchall()
        return {str(row[0]) for row in columns}
    finally:
        con.close()


def _latest_all_fields_run_id(db_path: Path) -> str | None:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            """
            SELECT run_id
            FROM run_metadata
            ORDER BY created_at_utc DESC
            LIMIT 1
            """
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        con.close()


def _load_all_fields_lookup(
    db_path: Path,
    columns: Sequence[str],
) -> dict[str, dict[str, Any]]:
    available_columns = _list_all_fields_columns(db_path)
    requested = [column for column in columns if column in available_columns]
    if not requested:
        return {}

    run_id = _latest_all_fields_run_id(db_path)
    if not run_id:
        return {}

    quoted = ", ".join(
        '"' + column.replace('"', '""') + '"' for column in ["symbol", *requested]
    )
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        relation = con.execute(
            f"""
            SELECT {quoted}
            FROM all_fields_rows
            WHERE run_id = ?
            """,
            [run_id],
        )
        column_names = [col[0] for col in relation.description]
        lookup: dict[str, dict[str, Any]] = {}
        for row in relation.fetchall():
            record = dict(zip(column_names, row))
            symbol = str(record.get("symbol") or "")
            if not symbol:
                continue
            for key in _symbol_lookup_keys(symbol):
                lookup[key] = record
        return lookup
    finally:
        con.close()


def _resolve_predictor_value(
    *,
    predictor: str,
    scan_row: Mapping[str, Any],
    all_fields_row: Mapping[str, Any] | None,
    config: RegimeContextConfig,
) -> PredictorValue:
    if predictor in scan_row:
        value = _coerce_finite_float(scan_row.get(predictor))
        if value is not None:
            return PredictorValue(predictor, value, "scan")

    alias = config.scan_column_aliases.get(predictor)
    if alias and alias in scan_row:
        value = _coerce_finite_float(scan_row.get(alias))
        if value is not None:
            return PredictorValue(predictor, value, "scan_alias")

    if all_fields_row is not None and predictor in all_fields_row:
        value = _coerce_finite_float(all_fields_row.get(predictor))
        if value is not None:
            return PredictorValue(predictor, value, "all_fields")

    proxy = config.scan_proxies_last_resort.get(predictor)
    if proxy and proxy in scan_row:
        value = _coerce_finite_float(scan_row.get(proxy))
        if value is not None:
            return PredictorValue(predictor, value, "scan_proxy")

    if proxy and all_fields_row is not None and proxy in all_fields_row:
        value = _coerce_finite_float(all_fields_row.get(proxy))
        if value is not None:
            return PredictorValue(predictor, value, "scan_proxy")

    return PredictorValue(predictor, None, "missing")


def build_symbol_feature_frame(
    scan_rows: Sequence[Mapping[str, Any]],
    *,
    all_fields_db: Path | None,
    config: RegimeContextConfig,
) -> dict[str, dict[str, PredictorValue]]:
    predictors = sorted(
        set(config.bullish_predictors)
        | set(config.warning_predictors)
        | set(config.promoted_predictors)
        | set(config.demoted_predictors)
    )
    all_fields_lookup: dict[str, dict[str, Any]] = {}
    if all_fields_db is not None:
        all_fields_lookup = _load_all_fields_lookup(all_fields_db, predictors)

    frame: dict[str, dict[str, PredictorValue]] = {}
    for scan_row in scan_rows:
        symbol = str(scan_row.get("symbol") or "")
        if not symbol:
            continue
        all_fields_row = None
        for key in _symbol_lookup_keys(symbol):
            if key in all_fields_lookup:
                all_fields_row = all_fields_lookup[key]
                break
        frame[symbol] = {
            predictor: _resolve_predictor_value(
                predictor=predictor,
                scan_row=scan_row,
                all_fields_row=all_fields_row,
                config=config,
            )
            for predictor in predictors
        }
    return frame


def _percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    if len(values) == 1:
        only_symbol = next(iter(values))
        return {only_symbol: 0.5}
    sorted_items = sorted(values.items(), key=lambda item: item[1])
    ranks: dict[str, float] = {}
    count = len(sorted_items)
    for index, (symbol, _) in enumerate(sorted_items):
        ranks[symbol] = index / (count - 1)
    return ranks


def _ntile_5(values: dict[str, float]) -> dict[str, int]:
    if not values:
        return {}
    sorted_items = sorted(values.items(), key=lambda item: item[1])
    count = len(sorted_items)
    quintiles: dict[str, int] = {}
    for index, (symbol, _) in enumerate(sorted_items):
        quintiles[symbol] = min(5, int((index + 1) * 5 / count))
    return quintiles


def _fields_source_summary(predictor_values: dict[str, PredictorValue]) -> str:
    counts: dict[str, int] = {}
    for predictor_value in predictor_values.values():
        counts[predictor_value.source] = counts.get(predictor_value.source, 0) + 1
    return ",".join(f"{source}:{count}" for source, count in sorted(counts.items()))


def _warning_flag_count(
    symbol: str,
    predictor_values: dict[str, PredictorValue],
    quintiles_by_predictor: dict[str, dict[str, int]],
) -> int:
    flags = 0
    stoch_q = quintiles_by_predictor.get("Stoch.K_14_1_3|1M", {}).get(symbol)
    if stoch_q == 1:
        flags += 1
    wr_q = quintiles_by_predictor.get("W.R|1M", {}).get(symbol)
    if wr_q == 1:
        flags += 1
    rec_q = quintiles_by_predictor.get("Recommend.MA|1M", {}).get(symbol)
    if rec_q == 5:
        flags += 1
    oper_q = quintiles_by_predictor.get("oper_income_ttm", {}).get(symbol)
    if oper_q == 5:
        flags += 1
    ebitda_q = quintiles_by_predictor.get("ebitda_ttm", {}).get(symbol)
    if ebitda_q == 5:
        flags += 1
    return flags


def _playbook_a_fit(
    symbol: str,
    predictor_values: dict[str, PredictorValue],
    atrp_1w_values: dict[str, float],
    min_relative_volume: float,
) -> bool:
    atrp_1w = predictor_values.get("ATRP|1W")
    relvol = predictor_values.get("relative_volume")
    atrp_value = atrp_1w.value if atrp_1w else None
    relvol_value = relvol.value if relvol else None
    if atrp_value is None or relvol_value is None:
        return False
    if relvol_value <= min_relative_volume:
        return False
    universe_atrp = list(atrp_1w_values.values())
    if not universe_atrp:
        return False
    median_atrp = sorted(universe_atrp)[len(universe_atrp) // 2]
    return atrp_value > median_atrp


def score_regime_context(
    feature_frame: dict[str, dict[str, PredictorValue]],
    weights: Sequence[tuple[str, float, float]],
    config: RegimeContextConfig,
) -> dict[str, RegimeContextRecord]:
    if not feature_frame:
        return {}

    weight_by_predictor = {field: (rss, weight) for field, rss, weight in weights}
    min_signals = max(3, len(weights) // 2)

    quintiles_by_predictor: dict[str, dict[str, int]] = {}
    for predictor in config.warning_predictors:
        values = {
            symbol: predictor_value.value
            for symbol, predictor_map in feature_frame.items()
            if (predictor_value := predictor_map.get(predictor))
            and predictor_value.value is not None
        }
        if values:
            quintiles_by_predictor[predictor] = _ntile_5(values)

    aligned_scores: dict[str, dict[str, float]] = {
        symbol: {} for symbol in feature_frame
    }
    for predictor_field, (rss, weight) in weight_by_predictor.items():
        values = {
            symbol: predictor_map[predictor_field].value
            for symbol, predictor_map in feature_frame.items()
            if predictor_field in predictor_map
            and predictor_map[predictor_field].value is not None
        }
        if not values:
            continue
        pct_ranks = _percentile_ranks(values)
        for symbol, pct_rank in pct_ranks.items():
            aligned = pct_rank if rss >= 0 else 1.0 - pct_rank
            aligned_scores[symbol][predictor_field] = aligned * weight

    atrp_1w_values = {
        symbol: predictor_map["ATRP|1W"].value
        for symbol, predictor_map in feature_frame.items()
        if "ATRP|1W" in predictor_map
        and predictor_map["ATRP|1W"].value is not None
    }

    records: dict[str, RegimeContextRecord] = {}
    for symbol, predictor_values in feature_frame.items():
        matched = aligned_scores.get(symbol) or {}
        signals_matched = len(matched)
        total_weight = sum(weight_by_predictor[field][1] for field in matched)
        raw_score = (
            sum(matched.values()) / total_weight if total_weight > 0 else 0.0
        )
        regime_fit_score = round(100.0 * raw_score, 2) if signals_matched else 0.0

        warning_count = _warning_flag_count(
            symbol, predictor_values, quintiles_by_predictor
        )
        atrp_record = predictor_values.get("ATRP|1W")
        relvol_record = predictor_values.get("relative_volume")
        playbook_a = _playbook_a_fit(
            symbol,
            predictor_values,
            atrp_1w_values,
            config.playbook_a_min_relative_volume,
        )

        if warning_count >= 2:
            tier = "warning_overlay"
        elif signals_matched < min_signals:
            tier = "insufficient_signals"
        elif playbook_a:
            tier = "playbook_a_vol_continuation"
        elif regime_fit_score >= 60.0:
            tier = "playbook_b_watch"
        else:
            tier = "composite_only"

        field_ranks: dict[str, dict[str, Any]] = {}
        for predictor_field, (rss, _) in weight_by_predictor.items():
            predictor_value = predictor_values.get(predictor_field)
            if predictor_value is None or predictor_value.value is None:
                continue
            values = {
                sym: predictor_map[predictor_field].value
                for sym, predictor_map in feature_frame.items()
                if predictor_field in predictor_map
                and predictor_map[predictor_field].value is not None
            }
            pct = _percentile_ranks(values).get(symbol)
            if pct is None:
                continue
            field_ranks[predictor_field] = {
                "percentile": round(pct, 4),
                "source": predictor_value.source,
                "aligned": round(pct if rss >= 0 else 1.0 - pct, 4),
            }

        records[symbol] = RegimeContextRecord(
            symbol=symbol,
            regime_fit_score=regime_fit_score,
            signals_matched=signals_matched,
            warning_flag_count=warning_count,
            active_mgmt_tier=tier,
            atrp_1w=atrp_record.value if atrp_record else None,
            relative_volume=relvol_record.value if relvol_record else None,
            promoted_field_ranks_json=json.dumps(field_ranks, sort_keys=True),
            fields_source_summary=_fields_source_summary(predictor_values),
            playbook_a_fit=playbook_a,
            predictor_values=predictor_values,
        )
    return records


def compute_regime_context_overlay(
    scan_rows: Sequence[Mapping[str, Any]],
    *,
    config_path: str | Path,
) -> RegimeOverlayResult:
    config = load_regime_context_config(config_path)
    all_predictors = sorted(
        set(config.bullish_predictors) | set(config.warning_predictors)
    )
    weights = fetch_predictor_weights(
        config.scan_period_run_root,
        all_predictors,
        config.performance_target,
    )
    if not weights:
        raise RuntimeError(
            f"No predictor weights found in aggregate DB under {config.scan_period_run_root}"
        )

    all_fields_db, day_label, stale_warning = resolve_all_fields_database(config)
    feature_frame = build_symbol_feature_frame(
        scan_rows,
        all_fields_db=all_fields_db,
        config=config,
    )
    by_symbol = score_regime_context(feature_frame, weights, config)
    return RegimeOverlayResult(
        config=config,
        by_symbol=by_symbol,
        weights=list(weights),
        all_fields_db_path=all_fields_db,
        all_fields_day_label=day_label,
        all_fields_stale_warning=stale_warning,
        aggregate_db_path=config.scan_period_run_root,
    )


def build_regime_context_duckdb_records(
    run_id: str,
    overlay: RegimeOverlayResult,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row_number, (symbol, record) in enumerate(
        sorted(overlay.by_symbol.items()), start=1
    ):
        records.append(
            {
                "run_id": run_id,
                "row_number": row_number,
                "symbol": symbol,
                "regime_fit_score": record.regime_fit_score,
                "active_mgmt_tier": record.active_mgmt_tier,
                "warning_flag_count": record.warning_flag_count,
                "atrp_1w": record.atrp_1w,
                "relative_volume": record.relative_volume,
                "signals_matched": record.signals_matched,
                "promoted_field_ranks_json": record.promoted_field_ranks_json,
                "fields_source_summary": record.fields_source_summary,
                "all_fields_day_label": overlay.all_fields_day_label,
                "config_id": overlay.config.config_id,
            }
        )
    return records


def _format_regime_tier_short(tier: str) -> str:
    mapping = {
        "playbook_a_vol_continuation": "playbook_a",
        "playbook_b_watch": "watch",
        "playbook_b_quality_drift": "quality",
        "warning_overlay": "warn",
        "composite_only": "composite",
        "insufficient_signals": "low_sig",
    }
    return mapping.get(tier, tier[:12])


def _bare_symbol(symbol: str) -> str:
    return symbol.split(":", 1)[-1]


def _display_symbol_label(row_or_symbol: Mapping[str, Any] | str) -> str:
    """Return EXCHANGE:TICKER for log output when exchange is known."""
    if isinstance(row_or_symbol, Mapping):
        from data_analysis_scripts.trading_view_move_prediction_analysis import (
            _get_exchange_ticker_symbol,
        )

        return _get_exchange_ticker_symbol(dict(row_or_symbol))

    symbol = str(row_or_symbol or "").strip()
    if not symbol:
        return "N/A"
    if ":" in symbol:
        exchange, ticker = symbol.split(":", 1)
        if exchange and ticker:
            return f"{exchange.upper()}:{ticker.upper()}"
    return symbol.upper()


def _log_symbol_list_line(path: Path, symbols: Sequence[str]) -> None:
    from data_analysis_scripts.trading_view_move_prediction_analysis import (
        _format_exchange_ticker_list,
    )

    log_to_file(path, f"symbols: {_format_exchange_ticker_list(symbols)}")


def _prediction_row_symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol") or row.get("ticker") or "")


def build_conviction_lookup(
    conviction_records: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    lookup: dict[str, Mapping[str, Any]] = {}
    if not conviction_records:
        return lookup
    for record in conviction_records:
        symbol = str(record.get("symbol") or "")
        if not symbol:
            continue
        lookup[symbol] = record
        bare = _bare_symbol(symbol)
        lookup.setdefault(bare, record)
    return lookup


def _conviction_lookup_for_symbol(
    symbol: str,
    conviction_lookup: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    if not symbol or not conviction_lookup:
        return None
    record = conviction_lookup.get(symbol)
    if record is not None:
        return record
    bare = _bare_symbol(symbol)
    return conviction_lookup.get(bare)


def _is_regime_aligned(
    record: RegimeContextRecord,
    weeks_ras: float,
    *,
    min_relative_volume: float,
) -> bool:
    relvol = record.relative_volume or 0.0
    return record.active_mgmt_tier == "playbook_a_vol_continuation" or (
        record.active_mgmt_tier in {"playbook_b_watch", "composite_only"}
        and relvol >= min_relative_volume
        and weeks_ras >= 0.25
    )


def _write_per_profile_regime_conviction_section(
    path: Path,
    *,
    profile_name: str,
    prediction_rows: Sequence[Mapping[str, Any]],
    overlay: RegimeOverlayResult,
    conviction_lookup: Mapping[str, Mapping[str, Any]],
    top_n: int,
) -> None:
    config = overlay.config
    regime_by_symbol = overlay.by_symbol
    ranked: list[tuple[float, Mapping[str, Any], RegimeContextRecord]] = []
    for prediction in prediction_rows:
        horizons = prediction.get("horizons") or {}
        weeks = horizons.get("weeks") or {}
        weeks_ras = weeks.get("risk_adjusted_score")
        if weeks_ras is None:
            continue
        row = prediction.get("row") or {}
        record = get_regime_record_for_row(row, regime_by_symbol)
        if record is None:
            continue
        if record.active_mgmt_tier in {"warning_overlay", "insufficient_signals"}:
            continue
        combined = float(weeks_ras) * (record.regime_fit_score / 100.0)
        ranked.append((combined, prediction, record))
    ranked.sort(key=lambda item: item[0], reverse=True)

    log_to_file(path, f"PROFILE: {profile_name} — REGIME + CONVICTION TOP {top_n}")
    log_to_file(path, "-" * 160)
    has_conviction = bool(conviction_lookup)
    log_to_file(
        path,
        f"Playbook A gate: ATRP|1W elevated + RelVol >= "
        f"{config.playbook_a_min_relative_volume:.1f}. "
        f"Aligned=YES when tier=playbook_a or "
        f"(watch/composite + RelVol gate + WeeksRAS >= 0.25).",
    )
    header = (
        f"{'#':<4}{'Ticker':<{SYMBOL_LOG_WIDTH}}{'Score':>8}{'RAdj':>8}{'RegFit':>8}{'Tier':<12}"
        f"{'ATRP':>8}{'RelVol':>8}"
    )
    if has_conviction:
        header += (
            f"{'Conv':>8}{'CnvRk':>6}{'Sleeve':<10}{'Warn':>5}{'Aligned':<8}"
        )
    else:
        header += f"{'Warn':>5}{'Aligned':<8}"
    log_to_file(path, header)
    section_symbols: list[str] = []
    for index, (combined, prediction, record) in enumerate(ranked[:top_n], start=1):
        row = prediction.get("row") or {}
        symbol = _prediction_row_symbol(row)
        ticker = _display_symbol_label(row) if row else "N/A"
        if ticker and ticker != "N/A":
            section_symbols.append(ticker)
        weeks = (prediction.get("horizons") or {}).get("weeks") or {}
        weeks_ras = float(weeks.get("risk_adjusted_score") or 0.0)
        weeks_score = weeks.get("score")
        aligned_flag = _is_regime_aligned(
            record,
            weeks_ras,
            min_relative_volume=config.playbook_a_min_relative_volume,
        )
        row_text = (
            f"{index:<4}{ticker:<{SYMBOL_LOG_WIDTH}}"
            f"{float(weeks_score or 0.0):>8.2f}"
            f"{weeks_ras:>8.2f}"
            f"{record.regime_fit_score:>8.1f}"
            f"{_format_regime_tier_short(record.active_mgmt_tier):<12}"
            f"{(record.atrp_1w or 0.0):>8.1f}"
            f"{(record.relative_volume or 0.0):>8.2f}"
        )
        if has_conviction:
            conv_record = _conviction_lookup_for_symbol(symbol, conviction_lookup)
            conv_score = conv_record.get("conviction_score") if conv_record else None
            conv_rank = conv_record.get("rank_overall") if conv_record else None
            sleeve = str((conv_record or {}).get("sleeve") or "")[:10]
            conv_text = f"{float(conv_score):>8.3f}" if conv_score is not None else "     n/a"
            rank_text = (
                f"{int(conv_rank):>6}" if conv_rank is not None else "   n/a"
            )
            row_text += (
                f"{conv_text}"
                f"{rank_text}"
                f"{sleeve:<10}"
                f"{record.warning_flag_count:>5}"
                f"{'YES' if aligned_flag else 'no':<8}"
            )
        else:
            row_text += (
                f"{record.warning_flag_count:>5}"
                f"{'YES' if aligned_flag else 'no':<8}"
            )
        log_to_file(path, row_text)
    if not ranked:
        log_to_file(path, "  (no regime-scored rows for this profile)")
    _log_symbol_list_line(path, section_symbols)
    log_to_file(path, "")


def write_regime_context_focus_log(
    path: Path,
    *,
    run_id: str,
    overlay: RegimeOverlayResult,
    consensus_rows: Sequence[Mapping[str, Any]],
    profile_names: Sequence[str],
    profile_log_paths: Mapping[str, Path],
    profile_predictions_by_name: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    conviction_records: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    config = overlay.config
    top_n = config.top_n_focus

    log_to_file(path, "REGIME CONTEXT DAILY FOCUS")
    log_to_file(path, "=" * 160)
    log_to_file(
        path,
        "Symbol labels use EXCHANGE:TICKER for uniqueness across listings "
        "(bare ticker only when exchange is unknown).",
    )
    log_to_file(
        path,
        "Regime confirmation layer — not a live trading signal. "
        "Mar–Jun 2026 batch was a rising tape; use to confirm tactical entries.",
    )
    log_to_file(path, "")
    log_to_file(path, f"run_id: {run_id} | config: {config.config_id}")
    log_to_file(path, f"regime_label: {config.regime_label}")
    log_to_file(
        path,
        f"scan_period_run: {overlay.aggregate_db_path.name} | "
        f"performance_target: {config.performance_target}",
    )
    log_to_file(
        path,
        f"all_fields_export: {overlay.all_fields_day_label or 'not available'} | "
        f"db: {overlay.all_fields_db_path or 'n/a'}",
    )
    if overlay.all_fields_stale_warning:
        log_to_file(path, f"WARNING: {overlay.all_fields_stale_warning}")
    log_to_file(path, "")
    log_to_file(path, "Promoted predictors (stable positive):")
    for predictor in config.promoted_predictors:
        log_to_file(path, f"  + {predictor}")
    log_to_file(path, "Demoted / trap filters (stable negative):")
    for predictor in config.demoted_predictors:
        log_to_file(path, f"  - {predictor}")
    log_to_file(path, "")
    log_to_file(path, "Top stability weights (from aggregate):")
    for predictor_field, rss, _ in overlay.weights[:8]:
        log_to_file(path, f"  {predictor_field:<24} rank_stability={rss:+.3f}")
    log_to_file(path, "")

    conviction_lookup = build_conviction_lookup(conviction_records)
    predictions_by_profile = profile_predictions_by_name or {}

    if predictions_by_profile:
        log_to_file(path, "PER-PROFILE REGIME + CONVICTION FOCUS")
        log_to_file(path, "=" * 160)
        if conviction_lookup:
            log_to_file(
                path,
                "Ranked by weeks risk-adjusted score x regime fit per profile. "
                "Conviction columns from cross-profile conviction mode.",
            )
        else:
            log_to_file(
                path,
                "Ranked by weeks risk-adjusted score x regime fit per profile. "
                "Conviction columns pending (run conviction pass to populate).",
            )
        log_to_file(path, "")
        for profile_name in profile_names:
            prediction_rows = predictions_by_profile.get(profile_name)
            if not prediction_rows:
                log_path = profile_log_paths.get(profile_name)
                if log_path:
                    log_to_file(
                        path,
                        f"PROFILE: {profile_name} — (no prediction rows; see {log_path.name})",
                    )
                else:
                    log_to_file(
                        path,
                        f"PROFILE: {profile_name} — (no prediction rows)",
                    )
                log_to_file(path, "")
                continue
            _write_per_profile_regime_conviction_section(
                path,
                profile_name=profile_name,
                prediction_rows=prediction_rows,
                overlay=overlay,
                conviction_lookup=conviction_lookup,
                top_n=top_n,
            )

    if conviction_lookup:
        conviction_leaders = sorted(
            (
                record
                for record in conviction_records or []
                if record.get("rank_overall") is not None
            ),
            key=lambda record: int(record.get("rank_overall") or 0),
        )[:top_n]
        log_to_file(path, f"CONVICTION LEADERS TOP {top_n}")
        log_to_file(path, "-" * 160)
        log_to_file(
            path,
            f"{'#':<4}{'Ticker':<{SYMBOL_LOG_WIDTH}}{'Conv':>8}{'CnvRk':>6}{'Sleeve':<10}"
            f"{'RegFit':>8}{'Tier':<12}{'ATRP':>8}{'RelVol':>8}"
            f"{'WeeksRAS':>10}{'Warn':>5}{'Aligned':<8}",
        )
        conviction_symbols: list[str] = []
        for record in conviction_leaders:
            symbol = str(record.get("symbol") or "")
            ticker = _display_symbol_label(symbol) if symbol else "N/A"
            if ticker and ticker != "N/A":
                conviction_symbols.append(ticker)
            regime_record = get_regime_record_for_row(
                {"symbol": symbol},
                overlay.by_symbol,
            )
            reg_fit = (
                float(regime_record.regime_fit_score)
                if regime_record is not None
                else float(record.get("regime_fit_score") or 0.0)
            )
            tier = (
                regime_record.active_mgmt_tier
                if regime_record is not None
                else str(record.get("regime_tier") or "")
            )
            warn_count = (
                regime_record.warning_flag_count
                if regime_record is not None
                else int(record.get("regime_warning_count") or 0)
            )
            weeks_ras = float(record.get("weeks_ras") or 0.0)
            aligned_flag = (
                _is_regime_aligned(
                    regime_record,
                    weeks_ras,
                    min_relative_volume=config.playbook_a_min_relative_volume,
                )
                if regime_record is not None
                else False
            )
            atrp_value = (
                regime_record.atrp_1w
                if regime_record is not None
                else None
            )
            relvol_value = (
                regime_record.relative_volume
                if regime_record is not None
                else None
            )
            log_to_file(
                path,
                f"{int(record.get('rank_overall') or 0):<4}{ticker:<{SYMBOL_LOG_WIDTH}}"
                f"{float(record.get('conviction_score') or 0.0):>8.3f}"
                f"{int(record.get('rank_overall') or 0):>6}"
                f"{str(record.get('sleeve') or '')[:10]:<10}"
                f"{reg_fit:>8.1f}"
                f"{_format_regime_tier_short(tier):<12}"
                f"{(atrp_value or 0.0):>8.1f}"
                f"{(relvol_value or 0.0):>8.2f}"
                f"{weeks_ras:>10.2f}"
                f"{warn_count:>5}"
                f"{'YES' if aligned_flag else 'no':<8}",
            )
        _log_symbol_list_line(path, conviction_symbols)
        log_to_file(path, "")

    consensus_by_symbol = {
        str(row.get("ticker") or row.get("symbol") or ""): row
        for row in consensus_rows
    }
    for symbol, record in overlay.by_symbol.items():
        bare = symbol.split(":", 1)[-1]
        if bare not in consensus_by_symbol and symbol in consensus_by_symbol:
            continue
        if symbol not in consensus_by_symbol and bare in consensus_by_symbol:
            consensus_by_symbol[symbol] = consensus_by_symbol[bare]

    def _aligned_sort_key(item: tuple[str, RegimeContextRecord]) -> float:
        symbol, record = item
        if record.active_mgmt_tier in {"warning_overlay", "insufficient_signals"}:
            return -1.0
        consensus_row = consensus_by_symbol.get(symbol)
        if consensus_row is None:
            bare = symbol.split(":", 1)[-1]
            consensus_row = consensus_by_symbol.get(bare)
        weeks_ras = 0.0
        if consensus_row:
            horizons = consensus_row.get("horizons") or {}
            weeks = horizons.get("weeks") or {}
            weeks_ras = float(weeks.get("risk_adjusted_score") or 0.0)
        return weeks_ras * (record.regime_fit_score / 100.0)

    ranked_overlay = sorted(
        overlay.by_symbol.items(),
        key=_aligned_sort_key,
        reverse=True,
    )
    aligned_rows = [
        (symbol, record)
        for symbol, record in ranked_overlay
        if _aligned_sort_key((symbol, record)) > 0
    ][:top_n]

    log_to_file(path, f"CROSS-PROFILE ALIGNED TOP {top_n}")
    log_to_file(path, "-" * 160)
    log_to_file(
        path,
        f"{'#':<4}{'Ticker':<{SYMBOL_LOG_WIDTH}}{'RegFit':>8}{'WeeksRAS':>10}{'Tier':<14}"
        f"{'ATRP1W':>8}{'RelVol':>8}{'Warn':>5}{'Aligned':<8}",
    )
    aligned_symbols: list[str] = []
    for index, (symbol, record) in enumerate(aligned_rows, start=1):
        consensus_row = consensus_by_symbol.get(symbol)
        if consensus_row is None:
            bare = symbol.split(":", 1)[-1]
            consensus_row = consensus_by_symbol.get(bare)
        weeks_ras = 0.0
        if consensus_row:
            horizons = consensus_row.get("horizons") or {}
            weeks = horizons.get("weeks") or {}
            weeks_ras = float(weeks.get("risk_adjusted_score") or 0.0)
        aligned_flag = _is_regime_aligned(
            record,
            weeks_ras,
            min_relative_volume=config.playbook_a_min_relative_volume,
        )
        ticker = _display_symbol_label(symbol)
        aligned_symbols.append(ticker)
        log_to_file(
            path,
            f"{index:<4}{ticker:<{SYMBOL_LOG_WIDTH}}"
            f"{record.regime_fit_score:>8.1f}"
            f"{weeks_ras:>10.2f}"
            f"{_format_regime_tier_short(record.active_mgmt_tier):<14}"
            f"{(record.atrp_1w or 0.0):>8.1f}"
            f"{(record.relative_volume or 0.0):>8.2f}"
            f"{record.warning_flag_count:>5}"
            f"{'YES' if aligned_flag else 'no':<8}",
        )
    _log_symbol_list_line(path, aligned_symbols)
    log_to_file(path, "")

    playbook_a_rows = [
        (symbol, record)
        for symbol, record in overlay.by_symbol.items()
        if record.active_mgmt_tier == "playbook_a_vol_continuation"
    ]
    playbook_a_rows.sort(key=lambda item: item[1].regime_fit_score, reverse=True)
    log_to_file(path, f"PLAYBOOK A TACTICAL ({len(playbook_a_rows)} names)")
    log_to_file(path, "-" * 160)
    playbook_a_symbols: list[str] = []
    if not playbook_a_rows:
        log_to_file(path, "  (none — strict ATRP|1W + relvol filter)")
    else:
        for symbol, record in playbook_a_rows[:top_n]:
            ticker = _display_symbol_label(symbol)
            playbook_a_symbols.append(ticker)
            log_to_file(
                path,
                f"  {ticker:<{SYMBOL_LOG_WIDTH}} "
                f"RegFit={record.regime_fit_score:.1f} "
                f"ATRP|1W={record.atrp_1w or 0:.1f} "
                f"relvol={record.relative_volume or 0:.2f}",
            )
    _log_symbol_list_line(path, playbook_a_symbols)
    log_to_file(path, "")

    consensus_sorted = sorted(
        consensus_rows,
        key=lambda row: abs(
            float(
                ((row.get("horizons") or {}).get("weeks") or {}).get(
                    "risk_adjusted_score"
                )
                or 0.0
            )
        ),
        reverse=True,
    )[:50]
    warning_hits: list[tuple[str, RegimeContextRecord, Mapping[str, Any]]] = []
    for consensus_row in consensus_sorted:
        ticker = str(consensus_row.get("ticker") or consensus_row.get("symbol") or "")
        regime_record = overlay.by_symbol.get(ticker)
        if regime_record is None:
            for key in _symbol_lookup_keys(ticker):
                regime_record = overlay.by_symbol.get(key)
                if regime_record:
                    break
        if regime_record and regime_record.warning_flag_count >= 2:
            warning_hits.append((ticker, regime_record, consensus_row))

    log_to_file(path, "WARNING OVERLAY ON CONSENSUS LEADERS")
    log_to_file(path, "-" * 160)
    warning_symbols: list[str] = []
    if not warning_hits:
        log_to_file(path, "  (none in consensus top 50)")
    else:
        for ticker, record, _ in warning_hits[:top_n]:
            label = _display_symbol_label(ticker)
            warning_symbols.append(label)
            log_to_file(
                path,
                f"  {label:<{SYMBOL_LOG_WIDTH}} "
                f"warn_flags={record.warning_flag_count} "
                f"RegFit={record.regime_fit_score:.1f}",
            )
    _log_symbol_list_line(path, warning_symbols)
    log_to_file(path, "")

    if profile_log_paths:
        log_to_file(path, "PROFILE LOG REFERENCES")
        log_to_file(path, "-" * 160)
        for profile_name in profile_names:
            log_path = profile_log_paths.get(profile_name)
            if log_path:
                log_to_file(
                    path,
                    f"  {profile_name}: full horizon tables in {log_path.name}",
                )
            else:
                log_to_file(path, f"  {profile_name}: (log not found)")


def get_regime_record_for_row(
    row: Mapping[str, Any],
    regime_by_symbol: Mapping[str, RegimeContextRecord],
) -> RegimeContextRecord | None:
    symbol = str(row.get("symbol") or row.get("ticker") or "")
    if not symbol:
        return None
    if symbol in regime_by_symbol:
        return regime_by_symbol[symbol]
    for key in _symbol_lookup_keys(symbol):
        if key in regime_by_symbol:
            return regime_by_symbol[key]
    bare = _bare_symbol(symbol)
    for key, record in regime_by_symbol.items():
        if _bare_symbol(key) == bare:
            return record
    return None
