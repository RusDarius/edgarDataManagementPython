from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "default_v1.json"
EXPECTED_SCHEMA_VERSION = "backtracking_v1"
VALID_RAW_PRESETS = frozenset({"core", "focused"})

DEFAULT_CORE_RAW_FIELDS = (
    "close",
    "market_cap_basic",
    "industry",
    "RSI",
    "ATRP",
    "enterprise_value_to_revenue_ttm",
    "enterprise_value_ebitda_ttm",
    "price_earnings_ttm",
    "price_earnings_forward_fy",
    "price_earnings_growth_ttm",
    "operating_margin_ttm",
    "gross_margin_ttm",
    "Perf.5D",
    "Perf.1M",
    "price_target_average",
)


def _resolve_path(value: str | Path | None, *, base: Path) -> Path:
    if value is None:
        return base
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()


def _as_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _as_raw_fields(value: Any) -> tuple[str, ...]:
    if value is None:
        return tuple(DEFAULT_CORE_RAW_FIELDS)
    if not isinstance(value, list):
        raise ValueError("raw_fields.core_fields must be a list")
    rows = []
    seen: set[str] = set()
    for item in value:
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(key)
    if not rows:
        return tuple(DEFAULT_CORE_RAW_FIELDS)
    return tuple(rows)


@dataclass(frozen=True)
class PathsConfig:
    project_root: Path
    all_fields_root: Path
    prediction_root: Path
    edge_runs_root: Path
    output_root: Path
    focused_catalog_csv: Path


@dataclass(frozen=True)
class WindowConfig:
    start_day_label: str | None
    end_day_label: str | None


@dataclass(frozen=True)
class RawFieldsConfig:
    preset: str
    core_fields: tuple[str, ...]


@dataclass(frozen=True)
class OutputConfig:
    root_dir: Path
    write_csv: bool


@dataclass(frozen=True)
class BacktrackingConfig:
    schema_version: str
    config_id: str
    description: str
    source_path: Path
    paths: PathsConfig
    window: WindowConfig
    raw_fields: RawFieldsConfig
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
    default_output_root = (
        PROJECT_ROOT / "logs" / "tradingview_analysis" / "backtracking_analysis" / "runs"
    )
    default_focused_catalog = (
        PROJECT_ROOT / "savedData" / "trading_view_stock_fields_focused.csv"
    )
    return PathsConfig(
        project_root=PROJECT_ROOT,
        all_fields_root=_resolve_path(payload.get("all_fields_root"), base=default_all_fields_root),
        prediction_root=_resolve_path(payload.get("prediction_root"), base=default_prediction_root),
        edge_runs_root=_resolve_path(payload.get("edge_runs_root"), base=default_edge_runs_root),
        output_root=_resolve_path(payload.get("output_root"), base=default_output_root),
        focused_catalog_csv=_resolve_path(
            payload.get("focused_catalog_csv"),
            base=default_focused_catalog,
        ),
    )


def _parse_window(payload: Mapping[str, Any]) -> WindowConfig:
    def _clean(value: Any) -> str | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        return text or None

    return WindowConfig(
        start_day_label=_clean(payload.get("start_day_label")),
        end_day_label=_clean(payload.get("end_day_label")),
    )


def _parse_raw_fields(payload: Mapping[str, Any]) -> RawFieldsConfig:
    preset = str(payload.get("preset") or "core").strip().lower()
    if preset not in VALID_RAW_PRESETS:
        raise ValueError(
            f"raw_fields.preset must be one of {sorted(VALID_RAW_PRESETS)}, got {preset!r}"
        )
    return RawFieldsConfig(
        preset=preset,
        core_fields=_as_raw_fields(payload.get("core_fields")),
    )


def _parse_output(payload: Mapping[str, Any], *, paths: PathsConfig) -> OutputConfig:
    return OutputConfig(
        root_dir=_resolve_path(payload.get("root_dir"), base=paths.output_root),
        write_csv=_as_bool(payload.get("write_csv", True), field_name="output.write_csv"),
    )


def load_backtracking_config(config_path: str | Path | None = None) -> BacktrackingConfig:
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    resolved_path = path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version != EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version {schema_version!r}; expected {EXPECTED_SCHEMA_VERSION!r}"
        )
    paths = _parse_paths(payload.get("paths") or {})
    window = _parse_window(payload.get("window") or {})
    raw_fields = _parse_raw_fields(payload.get("raw_fields") or {})
    output = _parse_output(payload.get("output") or {}, paths=paths)
    return BacktrackingConfig(
        schema_version=schema_version,
        config_id=str(payload.get("config_id") or resolved_path.stem),
        description=str(payload.get("description") or "").strip(),
        source_path=resolved_path,
        paths=paths,
        window=window,
        raw_fields=raw_fields,
        output=output,
        raw_payload=payload,
    )
