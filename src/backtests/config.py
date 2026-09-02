from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "default_v1.json"
EXPECTED_SCHEMA_VERSION = "backtests_v1"


def _resolve_path(value: str | Path | None, *, base: Path) -> Path:
    if value is None:
        return base
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()


def _as_int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _as_float(value: Any, *, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a float") from exc


def _as_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _as_str_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return tuple(str(item).strip() for item in value if str(item).strip())


@dataclass(frozen=True)
class PathsConfig:
    project_root: Path
    all_fields_root: Path
    prediction_root: Path
    edge_runs_root: Path
    market_timing_root: Path
    financial_projection_root: Path
    output_root: Path


@dataclass(frozen=True)
class CalendarConfig:
    start_day_label: str | None
    end_day_label: str | None
    max_days: int | None
    align_prediction_on_or_before: bool
    align_edge_on_or_before: bool
    align_timing_on_or_before: bool


@dataclass(frozen=True)
class MovePredictionFamilyConfig:
    enabled: bool
    consensus_horizons: tuple[str, ...]
    profile_names: tuple[str, ...]
    include_regime_context: bool


@dataclass(frozen=True)
class AllFieldsFamilyConfig:
    enabled: bool
    fields: tuple[str, ...]


@dataclass(frozen=True)
class UpsideFamilyConfig:
    enabled: bool
    allow_momentum_reconstruct: bool


@dataclass(frozen=True)
class MarketTimingFamilyConfig:
    enabled: bool
    checkpoint: str
    reconstruct_top_n: int
    prefer_artifact: bool


@dataclass(frozen=True)
class FinancialProjectionFamilyConfig:
    enabled: bool
    projection_upside_threshold_pct: float
    projection_growth_gap_threshold_pp: float


@dataclass(frozen=True)
class FamiliesConfig:
    move_prediction: MovePredictionFamilyConfig
    all_fields: AllFieldsFamilyConfig
    upside_opportunity: UpsideFamilyConfig
    market_timing: MarketTimingFamilyConfig
    financial_projection: FinancialProjectionFamilyConfig


@dataclass(frozen=True)
class EvaluationConfig:
    horizons: tuple[int, ...]
    top_ns: tuple[int, ...]
    quintile_count: int
    min_pairs_for_metric: int


@dataclass(frozen=True)
class OverlayConfig:
    variants: tuple[str, ...]
    enter_actions: tuple[str, ...]
    base_pick_signals: tuple[str, ...]
    timing_weight_mode: str
    projection_upside_min_pct: float
    projection_growth_gap_min_pp: float


@dataclass(frozen=True)
class OutputConfig:
    root_dir: Path
    write_csv: bool
    max_report_signals: int
    persist_raw_row_tables: bool
    duckdb_memory_limit: str
    duckdb_materialize_max_bytes: int


@dataclass(frozen=True)
class BacktestConfig:
    schema_version: str
    config_id: str
    description: str
    source_path: Path
    paths: PathsConfig
    calendar: CalendarConfig
    families: FamiliesConfig
    evaluation: EvaluationConfig
    overlay: OverlayConfig
    output: OutputConfig
    raw_payload: Mapping[str, Any] = field(repr=False)


def _parse_paths(payload: Mapping[str, Any]) -> PathsConfig:
    default_all_fields_root = (
        PROJECT_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
    )
    default_prediction_root = (
        PROJECT_ROOT / "logs" / "tradingview_analysis" / "prediction_analysis" / "duckdb_runs"
    )
    default_edge_runs_root = (
        PROJECT_ROOT / "logs" / "tradingview_analysis" / "edge_research_tools" / "runs"
    )
    default_market_timing_root = (
        PROJECT_ROOT / "logs" / "tradingview_analysis" / "market_timing_policy" / "runs"
    )
    default_financial_projection_root = (
        PROJECT_ROOT / "logs" / "tradingview_analysis" / "financial_projection"
    )
    default_output_root = (
        PROJECT_ROOT / "logs" / "tradingview_analysis" / "backtests" / "runs"
    )
    return PathsConfig(
        project_root=PROJECT_ROOT,
        all_fields_root=_resolve_path(payload.get("all_fields_root"), base=default_all_fields_root),
        prediction_root=_resolve_path(payload.get("prediction_root"), base=default_prediction_root),
        edge_runs_root=_resolve_path(payload.get("edge_runs_root"), base=default_edge_runs_root),
        market_timing_root=_resolve_path(
            payload.get("market_timing_root"), base=default_market_timing_root
        ),
        financial_projection_root=_resolve_path(
            payload.get("financial_projection_root"), base=default_financial_projection_root
        ),
        output_root=_resolve_path(payload.get("output_root"), base=default_output_root),
    )


def _parse_calendar(payload: Mapping[str, Any]) -> CalendarConfig:
    max_days_raw = payload.get("max_days")
    max_days = _as_int(max_days_raw, field_name="calendar.max_days") if max_days_raw is not None else None
    return CalendarConfig(
        start_day_label=(
            str(payload.get("start_day_label")).strip()
            if payload.get("start_day_label") not in (None, "")
            else None
        ),
        end_day_label=(
            str(payload.get("end_day_label")).strip()
            if payload.get("end_day_label") not in (None, "")
            else None
        ),
        max_days=max_days,
        align_prediction_on_or_before=_as_bool(
            payload.get("align_prediction_on_or_before", True),
            field_name="calendar.align_prediction_on_or_before",
        ),
        align_edge_on_or_before=_as_bool(
            payload.get("align_edge_on_or_before", True),
            field_name="calendar.align_edge_on_or_before",
        ),
        align_timing_on_or_before=_as_bool(
            payload.get("align_timing_on_or_before", True),
            field_name="calendar.align_timing_on_or_before",
        ),
    )


def _parse_families(payload: Mapping[str, Any]) -> FamiliesConfig:
    move_payload = payload.get("move_prediction") or {}
    all_fields_payload = payload.get("all_fields") or {}
    upside_payload = payload.get("upside_opportunity") or {}
    timing_payload = payload.get("market_timing") or {}
    projection_payload = payload.get("financial_projection") or {}
    return FamiliesConfig(
        move_prediction=MovePredictionFamilyConfig(
            enabled=_as_bool(move_payload.get("enabled", True), field_name="families.move_prediction.enabled"),
            consensus_horizons=_as_str_tuple(
                move_payload.get("consensus_horizons", ["days", "weeks"]),
                field_name="families.move_prediction.consensus_horizons",
            ),
            profile_names=_as_str_tuple(
                move_payload.get("profile_names", ["breakout_long_v1", "forward_edge_active_v2"]),
                field_name="families.move_prediction.profile_names",
            ),
            include_regime_context=_as_bool(
                move_payload.get("include_regime_context", True),
                field_name="families.move_prediction.include_regime_context",
            ),
        ),
        all_fields=AllFieldsFamilyConfig(
            enabled=_as_bool(all_fields_payload.get("enabled", True), field_name="families.all_fields.enabled"),
            fields=_as_str_tuple(
                all_fields_payload.get(
                    "fields",
                    [
                        "ATRP",
                        "ADRP",
                        "Perf.5D",
                        "Perf.1M",
                        "Perf.3M",
                        "RSI",
                        "Recommend.All",
                        "change",
                        "close",
                        "price_target",
                        "total_revenue_yoy_growth_ttm",
                        "total_revenue_cagr_5y",
                    ],
                ),
                field_name="families.all_fields.fields",
            ),
        ),
        upside_opportunity=UpsideFamilyConfig(
            enabled=_as_bool(
                upside_payload.get("enabled", True),
                field_name="families.upside_opportunity.enabled",
            ),
            allow_momentum_reconstruct=_as_bool(
                upside_payload.get("allow_momentum_reconstruct", True),
                field_name="families.upside_opportunity.allow_momentum_reconstruct",
            ),
        ),
        market_timing=MarketTimingFamilyConfig(
            enabled=_as_bool(
                timing_payload.get("enabled", True), field_name="families.market_timing.enabled"
            ),
            checkpoint=str(timing_payload.get("checkpoint", "latest")).strip() or "latest",
            reconstruct_top_n=_as_int(
                timing_payload.get("reconstruct_top_n", 100),
                field_name="families.market_timing.reconstruct_top_n",
            ),
            prefer_artifact=_as_bool(
                timing_payload.get("prefer_artifact", True),
                field_name="families.market_timing.prefer_artifact",
            ),
        ),
        financial_projection=FinancialProjectionFamilyConfig(
            enabled=_as_bool(
                projection_payload.get("enabled", True),
                field_name="families.financial_projection.enabled",
            ),
            projection_upside_threshold_pct=_as_float(
                projection_payload.get("projection_upside_threshold_pct", 15.0),
                field_name="families.financial_projection.projection_upside_threshold_pct",
            ),
            projection_growth_gap_threshold_pp=_as_float(
                projection_payload.get("projection_growth_gap_threshold_pp", 0.0),
                field_name="families.financial_projection.projection_growth_gap_threshold_pp",
            ),
        ),
    )


def _parse_evaluation(payload: Mapping[str, Any]) -> EvaluationConfig:
    horizons = tuple(
        sorted(
            {
                _as_int(value, field_name="evaluation.horizons[]")
                for value in payload.get("horizons", [1, 5, 10, 20, 60])
            }
        )
    )
    top_ns = tuple(
        sorted(
            {
                _as_int(value, field_name="evaluation.top_ns[]")
                for value in payload.get("top_ns", [20, 50])
            }
        )
    )
    return EvaluationConfig(
        horizons=horizons,
        top_ns=top_ns,
        quintile_count=max(
            2,
            _as_int(payload.get("quintile_count", 5), field_name="evaluation.quintile_count"),
        ),
        min_pairs_for_metric=max(
            2,
            _as_int(
                payload.get("min_pairs_for_metric", 8),
                field_name="evaluation.min_pairs_for_metric",
            ),
        ),
    )


def _parse_overlay(payload: Mapping[str, Any]) -> OverlayConfig:
    variants = _as_str_tuple(
        payload.get(
            "variants",
            [
                "picks_only",
                "timing_filter",
                "timing_weight",
                "projection_filter",
                "picks_timing_projection",
            ],
        ),
        field_name="overlay.variants",
    )
    if not variants:
        variants = ("picks_only",)
    return OverlayConfig(
        variants=variants,
        enter_actions=_as_str_tuple(
            payload.get("enter_actions", ["ENTER_SMALL", "ENTER_PROBE"]),
            field_name="overlay.enter_actions",
        ),
        base_pick_signals=_as_str_tuple(
            payload.get(
                "base_pick_signals",
                ["conviction_score", "upside_opportunity_score", "move_upside_score"],
            ),
            field_name="overlay.base_pick_signals",
        ),
        timing_weight_mode=str(payload.get("timing_weight_mode", "risk_units")).strip()
        or "risk_units",
        projection_upside_min_pct=_as_float(
            payload.get("projection_upside_min_pct", 15.0),
            field_name="overlay.projection_upside_min_pct",
        ),
        projection_growth_gap_min_pp=_as_float(
            payload.get("projection_growth_gap_min_pp", 0.0),
            field_name="overlay.projection_growth_gap_min_pp",
        ),
    )


def _parse_output(payload: Mapping[str, Any], *, paths: PathsConfig) -> OutputConfig:
    return OutputConfig(
        root_dir=_resolve_path(payload.get("root_dir"), base=paths.output_root),
        write_csv=_as_bool(payload.get("write_csv", True), field_name="output.write_csv"),
        max_report_signals=max(
            1,
            _as_int(payload.get("max_report_signals", 20), field_name="output.max_report_signals"),
        ),
        persist_raw_row_tables=_as_bool(
            payload.get("persist_raw_row_tables", True),
            field_name="output.persist_raw_row_tables",
        ),
        duckdb_memory_limit=str(payload.get("duckdb_memory_limit") or "2GB").strip() or "2GB",
        duckdb_materialize_max_bytes=max(
            0,
            _as_int(
                payload.get("duckdb_materialize_max_bytes", 64 * 1024 * 1024),
                field_name="output.duckdb_materialize_max_bytes",
            ),
        ),
    )


def load_backtest_config(config_path: str | Path | None = None) -> BacktestConfig:
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    resolved_path = path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version != EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version {schema_version!r}; expected {EXPECTED_SCHEMA_VERSION!r}"
        )

    paths = _parse_paths(payload.get("paths") or {})
    calendar = _parse_calendar(payload.get("calendar") or {})
    families = _parse_families(payload.get("families") or {})
    evaluation = _parse_evaluation(payload.get("evaluation") or {})
    overlay = _parse_overlay(payload.get("overlay") or {})
    output = _parse_output(payload.get("output") or {}, paths=paths)
    return BacktestConfig(
        schema_version=schema_version,
        config_id=str(payload.get("config_id") or resolved_path.stem),
        description=str(payload.get("description") or "").strip(),
        source_path=resolved_path,
        paths=paths,
        calendar=calendar,
        families=families,
        evaluation=evaluation,
        overlay=overlay,
        output=output,
        raw_payload=payload,
    )
