"""Load and validate focus pool screening configs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .scoring import DIRECTION_BAND, SUPPORTED_DIRECTIONS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "default_wide_pool.json"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "focus_pool_screening" / "runs"
)

EXPECTED_SCHEMA_VERSION = "focus_pool_screening_v1"

SOURCE_ALL_FIELDS = "all_fields"
SOURCE_PREDICTION_RAW = "prediction_raw"
SUPPORTED_FIELD_SOURCES = (SOURCE_ALL_FIELDS, SOURCE_PREDICTION_RAW)

IDENTITY_KEYS = (
    "symbol",
    "company",
    "exchange",
    "country",
    "sector",
    "industry",
    "market",
    "type",
    "close",
    "market_cap_basic",
    "earnings_release_date",
    "earnings_release_next_date",
)

RESERVED_SCORE_KEYS = (
    "focus_score",
    "focus_rank",
    "value_score",
    "fundamental_score",
    "technical_score",
    "momentum_score",
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    column: str | None
    derived: str | None
    sources: tuple[str, ...]
    direction: str
    weight: float
    valid_range: tuple[float | None, float | None] | None
    clip: tuple[float | None, float | None] | None
    band: tuple[float, float] | None
    decay_span: float | None
    min_fill: float


@dataclass(frozen=True)
class FamilySpec:
    name: str
    weight: float
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class MovePredictionSourceSpec:
    enabled: bool
    database_path: Path | None
    run_id: str | None
    consensus_horizon: str
    profile_columns: tuple[str, ...]
    include_conviction: bool


@dataclass(frozen=True)
class AllFieldsSourceSpec:
    enabled: bool
    database_path: Path | None
    day_label: str | None
    run_id: str | None


@dataclass(frozen=True)
class EdgeResearchSourceSpec:
    enabled: bool
    parent_run_dir: Path | None


@dataclass(frozen=True)
class SourcesSpec:
    move_prediction: MovePredictionSourceSpec
    all_fields: AllFieldsSourceSpec
    edge_research: EdgeResearchSourceSpec


@dataclass(frozen=True)
class UniverseSpec:
    min_market_cap_usd: float
    max_market_cap_usd: float | None
    markets: tuple[str, ...] | None
    min_close: float
    min_avg_dollar_traded_10d: float
    exclude_symbols: tuple[str, ...]


@dataclass(frozen=True)
class LaneOrderKey:
    key: str
    direction: str  # "desc" | "asc"


@dataclass(frozen=True)
class LaneSpec:
    lane_id: str
    enabled: bool
    description: str
    conditions: Mapping[str, Any]
    order_by: tuple[LaneOrderKey, ...]
    top_n: int


@dataclass(frozen=True)
class OverlayPredictionSpec:
    weight: float
    score_min: float
    score_max: float


@dataclass(frozen=True)
class OverlayEdgeSpec:
    weight: float


@dataclass(frozen=True)
class OverlaysSpec:
    prediction: OverlayPredictionSpec
    edge: OverlayEdgeSpec


@dataclass(frozen=True)
class OutputSpec:
    root_dir: Path
    write_csv: bool
    top_n_focus_section: int
    min_composite_coverage: float


@dataclass(frozen=True)
class FocusPoolConfig:
    schema_version: str
    config_id: str
    description: str
    sources: SourcesSpec
    universe: UniverseSpec
    families: tuple[FamilySpec, ...]
    overlays: OverlaysSpec
    lanes: tuple[LaneSpec, ...]
    output: OutputSpec
    min_field_fill: float
    source_path: Path
    raw_payload: Mapping[str, Any] = field(repr=False)


def _as_float(value: Any, *, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc


def _as_optional_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    return _as_float(value, field_name=field_name)


def _as_int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _as_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _as_str_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(str(item).strip() for item in value if str(item).strip())


def _as_bound_pair(
    value: Any, *, field_name: str, allow_open: bool = True
) -> tuple[float | None, float | None] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field_name} must be [low, high] or null")
    low = _as_optional_float(value[0], field_name=f"{field_name}[0]")
    high = _as_optional_float(value[1], field_name=f"{field_name}[1]")
    if not allow_open and (low is None or high is None):
        raise ValueError(f"{field_name} requires numeric low and high")
    if low is not None and high is not None and low > high:
        raise ValueError(f"{field_name} low must be <= high")
    return (low, high)


def _parse_field(family_name: str, payload: Mapping[str, Any], default_min_fill: float) -> FieldSpec:
    prefix = f"families.{family_name}"
    key = str(payload.get("key") or "").strip()
    if not key:
        raise ValueError(f"{prefix}: every field needs a non-empty 'key'")
    if not key.replace("_", "").isalnum() or not key.islower():
        raise ValueError(f"{prefix}.{key}: key must be snake_case [a-z0-9_]")
    if key in IDENTITY_KEYS or key in RESERVED_SCORE_KEYS:
        raise ValueError(f"{prefix}.{key}: key collides with a reserved output column")

    column = payload.get("column")
    derived = payload.get("derived")
    if (column is None) == (derived is None):
        raise ValueError(
            f"{prefix}.{key}: exactly one of 'column' (TradingView field name) "
            "or 'derived' (engine derived metric) must be set"
        )

    sources_raw = payload.get("sources") or [SOURCE_ALL_FIELDS, SOURCE_PREDICTION_RAW]
    sources = tuple(str(item).strip() for item in sources_raw)
    unknown_sources = sorted(set(sources) - set(SUPPORTED_FIELD_SOURCES))
    if unknown_sources:
        raise ValueError(f"{prefix}.{key}: unknown sources {unknown_sources}")

    direction = str(payload.get("direction") or "").strip()
    if direction not in SUPPORTED_DIRECTIONS:
        raise ValueError(
            f"{prefix}.{key}: direction must be one of {SUPPORTED_DIRECTIONS}"
        )
    band = _as_bound_pair(payload.get("band"), field_name=f"{prefix}.{key}.band", allow_open=False)
    if direction == DIRECTION_BAND and band is None:
        raise ValueError(f"{prefix}.{key}: direction 'band' requires band=[low, high]")

    weight = _as_float(payload.get("weight", 1.0), field_name=f"{prefix}.{key}.weight")
    if weight <= 0:
        raise ValueError(f"{prefix}.{key}: weight must be > 0")

    min_fill = _as_float(
        payload.get("min_fill", default_min_fill), field_name=f"{prefix}.{key}.min_fill"
    )
    if not 0.0 <= min_fill <= 1.0:
        raise ValueError(f"{prefix}.{key}: min_fill must be in [0, 1]")

    return FieldSpec(
        key=key,
        column=str(column) if column is not None else None,
        derived=str(derived) if derived is not None else None,
        sources=sources,
        direction=direction,
        weight=weight,
        valid_range=_as_bound_pair(
            payload.get("valid_range"), field_name=f"{prefix}.{key}.valid_range"
        ),
        clip=_as_bound_pair(payload.get("clip"), field_name=f"{prefix}.{key}.clip"),
        band=(band[0], band[1]) if band is not None else None,
        decay_span=_as_optional_float(
            payload.get("decay_span"), field_name=f"{prefix}.{key}.decay_span"
        ),
        min_fill=min_fill,
    )


def _parse_sources(payload: Mapping[str, Any]) -> SourcesSpec:
    def _section(name: str) -> Mapping[str, Any]:
        section = payload.get(name) or {}
        if not isinstance(section, Mapping):
            raise ValueError(f"sources.{name} must be an object")
        return section

    mp = _section("move_prediction")
    af = _section("all_fields")
    edge = _section("edge_research")

    mp_db = mp.get("database_path")
    edge_dir = edge.get("parent_run_dir")
    af_db = af.get("database_path")

    return SourcesSpec(
        move_prediction=MovePredictionSourceSpec(
            enabled=bool(mp.get("enabled", True)),
            database_path=Path(mp_db) if mp_db else None,
            run_id=str(mp["run_id"]) if mp.get("run_id") else None,
            consensus_horizon=str(mp.get("consensus_horizon") or "weeks").strip(),
            profile_columns=_as_str_tuple(
                mp.get("profile_columns"), field_name="sources.move_prediction.profile_columns"
            ),
            include_conviction=bool(mp.get("include_conviction", True)),
        ),
        all_fields=AllFieldsSourceSpec(
            enabled=bool(af.get("enabled", True)),
            database_path=Path(af_db) if af_db else None,
            day_label=str(af["day_label"]) if af.get("day_label") else None,
            run_id=str(af["run_id"]) if af.get("run_id") else None,
        ),
        edge_research=EdgeResearchSourceSpec(
            enabled=bool(edge.get("enabled", True)),
            parent_run_dir=Path(edge_dir) if edge_dir else None,
        ),
    )


def _parse_universe(payload: Mapping[str, Any]) -> UniverseSpec:
    raw = payload.get("universe") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("'universe' must be an object")
    markets = raw.get("markets")
    return UniverseSpec(
        min_market_cap_usd=_as_float(
            raw.get("min_market_cap_usd", 500_000_000), field_name="universe.min_market_cap_usd"
        ),
        max_market_cap_usd=_as_optional_float(
            raw.get("max_market_cap_usd"), field_name="universe.max_market_cap_usd"
        ),
        markets=(
            tuple(str(m).strip().lower() for m in markets if str(m).strip())
            if isinstance(markets, list)
            else None
        ),
        min_close=_as_float(raw.get("min_close", 0.0), field_name="universe.min_close"),
        min_avg_dollar_traded_10d=_as_float(
            raw.get("min_avg_dollar_traded_10d", 0.0),
            field_name="universe.min_avg_dollar_traded_10d",
        ),
        exclude_symbols=_as_str_tuple(
            raw.get("exclude_symbols"), field_name="universe.exclude_symbols"
        ),
    )


def _parse_lanes(payload: Mapping[str, Any]) -> tuple[LaneSpec, ...]:
    raw = payload.get("lanes") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("'lanes' must be an object")
    lanes: list[LaneSpec] = []
    for lane_id, lane_raw in raw.items():
        lane_id_clean = str(lane_id).strip()
        if not lane_id_clean:
            raise ValueError("lane ids must be non-empty")
        if not isinstance(lane_raw, Mapping):
            raise ValueError(f"lanes.{lane_id_clean} must be an object")
        conditions = lane_raw.get("conditions")
        if not isinstance(conditions, Mapping):
            raise ValueError(f"lanes.{lane_id_clean}.conditions must be an object")
        order_raw = lane_raw.get("order_by") or [{"key": "focus_score", "dir": "desc"}]
        if not isinstance(order_raw, list):
            raise ValueError(f"lanes.{lane_id_clean}.order_by must be a list")
        order_by: list[LaneOrderKey] = []
        for item in order_raw:
            if not isinstance(item, Mapping) or not item.get("key"):
                raise ValueError(
                    f"lanes.{lane_id_clean}.order_by entries need {{'key', 'dir'}}"
                )
            direction = str(item.get("dir") or "desc").strip().lower()
            if direction not in ("asc", "desc"):
                raise ValueError(
                    f"lanes.{lane_id_clean}.order_by dir must be asc|desc"
                )
            order_by.append(LaneOrderKey(key=str(item["key"]).strip(), direction=direction))
        top_n = _as_int(lane_raw.get("top_n", 40), field_name=f"lanes.{lane_id_clean}.top_n")
        if top_n < 1:
            raise ValueError(f"lanes.{lane_id_clean}.top_n must be >= 1")
        lanes.append(
            LaneSpec(
                lane_id=lane_id_clean,
                enabled=bool(lane_raw.get("enabled", True)),
                description=str(lane_raw.get("description") or ""),
                conditions=conditions,
                order_by=tuple(order_by),
                top_n=top_n,
            )
        )
    return tuple(lanes)


def _parse_overlays(payload: Mapping[str, Any]) -> OverlaysSpec:
    raw = payload.get("overlays") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("'overlays' must be an object")
    pred_raw = raw.get("prediction") or {}
    edge_raw = raw.get("edge") or {}
    score_min = _as_float(pred_raw.get("score_min", -2.0), field_name="overlays.prediction.score_min")
    score_max = _as_float(pred_raw.get("score_max", 2.0), field_name="overlays.prediction.score_max")
    if score_min >= score_max:
        raise ValueError("overlays.prediction.score_min must be < score_max")
    return OverlaysSpec(
        prediction=OverlayPredictionSpec(
            weight=_as_float(pred_raw.get("weight", 0.0), field_name="overlays.prediction.weight"),
            score_min=score_min,
            score_max=score_max,
        ),
        edge=OverlayEdgeSpec(
            weight=_as_float(edge_raw.get("weight", 0.0), field_name="overlays.edge.weight"),
        ),
    )


def load_focus_pool_config(config_path: str | Path | None = None) -> FocusPoolConfig:
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Focus pool config not found: {path.as_posix()}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Focus pool config root must be a JSON object")

    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version != EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version '{schema_version}'. "
            f"Expected '{EXPECTED_SCHEMA_VERSION}'."
        )

    min_field_fill = _as_float(
        payload.get("min_field_fill", 0.05), field_name="min_field_fill"
    )

    families_raw = payload.get("families")
    if not isinstance(families_raw, Mapping) or not families_raw:
        raise ValueError("'families' must be a non-empty object")
    families: list[FamilySpec] = []
    for family_name, family_raw in families_raw.items():
        name = str(family_name).strip()
        if not isinstance(family_raw, Mapping):
            raise ValueError(f"families.{name} must be an object")
        fields_raw = family_raw.get("fields")
        if not isinstance(fields_raw, list) or not fields_raw:
            raise ValueError(f"families.{name}.fields must be a non-empty list")
        weight = _as_float(family_raw.get("weight", 0.0), field_name=f"families.{name}.weight")
        if weight < 0:
            raise ValueError(f"families.{name}.weight must be >= 0")
        field_specs = tuple(
            _parse_field(name, field_payload, min_field_fill) for field_payload in fields_raw
        )
        seen_keys = [spec.key for spec in field_specs]
        if len(set(seen_keys)) != len(seen_keys):
            raise ValueError(f"families.{name}: duplicate field keys {sorted(seen_keys)}")
        families.append(FamilySpec(name=name, weight=weight, fields=field_specs))

    all_keys = [spec.key for fam in families for spec in fam.fields]
    if len(set(all_keys)) != len(all_keys):
        raise ValueError("field keys must be unique across families")

    output_raw = payload.get("output") or {}
    if not isinstance(output_raw, Mapping):
        raise ValueError("'output' must be an object")
    root_dir = output_raw.get("root_dir")
    min_composite_coverage = _as_float(
        output_raw.get("min_composite_coverage", 0.5),
        field_name="output.min_composite_coverage",
    )
    if not 0.0 <= min_composite_coverage <= 1.0:
        raise ValueError("output.min_composite_coverage must be in [0, 1]")

    return FocusPoolConfig(
        schema_version=schema_version,
        config_id=str(payload.get("config_id") or path.stem),
        description=str(payload.get("description") or ""),
        sources=_parse_sources(payload.get("sources") or {}),
        universe=_parse_universe(payload),
        families=tuple(families),
        overlays=_parse_overlays(payload),
        lanes=_parse_lanes(payload),
        output=OutputSpec(
            root_dir=Path(root_dir) if root_dir else DEFAULT_OUTPUT_ROOT,
            write_csv=bool(output_raw.get("write_csv", True)),
            top_n_focus_section=_as_int(
                output_raw.get("top_n_focus_section", 40),
                field_name="output.top_n_focus_section",
            ),
            min_composite_coverage=min_composite_coverage,
        ),
        min_field_fill=min_field_fill,
        source_path=path.resolve(),
        raw_payload=payload,
    )
