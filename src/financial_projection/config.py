"""Load and validate financial projection scenario configs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .fields import REQUIRED_SCENARIO_KEYS, SCENARIO_PARAM_KEYS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_CONFIG_PATH = (
    PROJECT_ROOT / "config" / "financial_projection" / "scenarios_v1.json"
)
DEFAULT_ALL_FIELDS_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "trading_view_all_fields_data"
)
DEFAULT_PREDICTION_ROOT = (
    PROJECT_ROOT
    / "logs"
    / "tradingview_analysis"
    / "prediction_analysis"
    / "duckdb_runs"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "logs" / "tradingview_analysis" / "financial_projection"
)
DEFAULT_MIN_MARKET_CAP_USD = 500_000_000.0

EXPECTED_SCHEMA_VERSION = "financial_projection_scenarios_v1"


@dataclass(frozen=True)
class ScenarioParams:
    revenue_growth_override: float | None
    revenue_growth_scale: float
    growth_fade_per_year: float
    terminal_multiple_scale_vs_peer: float
    own_multiple_blend: float
    net_debt_growth_per_year: float
    sgr_cap_multiplier: float


@dataclass(frozen=True)
class ValuationLensRules:
    earnings_preferred_industries: tuple[str, ...]
    earnings_preferred_sectors: tuple[str, ...]
    ev_rev_unsuitable_sectors: tuple[str, ...]


@dataclass(frozen=True)
class FinancialProjectionConfig:
    schema_version: str
    config_id: str
    description: str
    horizon_years: int
    min_market_cap_usd: float
    min_peer_group_size: int
    peer_trim_fraction: float
    relative_scale_clip_low: float
    relative_scale_clip_high: float
    peer_mcap_band_low: float
    peer_mcap_band_high: float
    peer_mcap_refine_min_industry_n: int
    max_starting_growth_fraction: float
    forecast_growth_max_fraction: float
    max_ev_to_revenue: float
    min_revenue_usd: float
    rich_growth_fy_rev_pct: float
    valuation_conflict_pp: float
    outlier_terminal_upside_pct: float
    outlier_max_ev_rev: float
    scenario_width_wide_pp: float
    rev_eps_divergence_hot_pp: float
    min_peer_n_trust: int
    valuation_lens_rules: ValuationLensRules
    scenarios: dict[str, ScenarioParams]
    source_path: Path


def _as_optional_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number or null") from exc


def _as_float(value: Any, *, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc


def _as_int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _parse_scenario_params(
    scenario_name: str, payload: Mapping[str, Any]
) -> ScenarioParams:
    missing = [key for key in SCENARIO_PARAM_KEYS if key not in payload]
    if missing:
        raise ValueError(
            f"Scenario '{scenario_name}' missing keys: {', '.join(missing)}"
        )
    unknown = sorted(set(payload) - set(SCENARIO_PARAM_KEYS))
    if unknown:
        raise ValueError(
            f"Scenario '{scenario_name}' has unknown keys: {', '.join(unknown)}"
        )

    own_blend = _as_float(
        payload["own_multiple_blend"], field_name=f"{scenario_name}.own_multiple_blend"
    )
    if not 0.0 <= own_blend <= 1.0:
        raise ValueError(
            f"{scenario_name}.own_multiple_blend must be between 0 and 1 inclusive"
        )

    fade = _as_float(
        payload["growth_fade_per_year"],
        field_name=f"{scenario_name}.growth_fade_per_year",
    )
    if fade <= 0.0:
        raise ValueError(f"{scenario_name}.growth_fade_per_year must be > 0")

    return ScenarioParams(
        revenue_growth_override=_as_optional_float(
            payload["revenue_growth_override"],
            field_name=f"{scenario_name}.revenue_growth_override",
        ),
        revenue_growth_scale=_as_float(
            payload["revenue_growth_scale"],
            field_name=f"{scenario_name}.revenue_growth_scale",
        ),
        growth_fade_per_year=fade,
        terminal_multiple_scale_vs_peer=_as_float(
            payload["terminal_multiple_scale_vs_peer"],
            field_name=f"{scenario_name}.terminal_multiple_scale_vs_peer",
        ),
        own_multiple_blend=own_blend,
        net_debt_growth_per_year=_as_float(
            payload["net_debt_growth_per_year"],
            field_name=f"{scenario_name}.net_debt_growth_per_year",
        ),
        sgr_cap_multiplier=_as_float(
            payload["sgr_cap_multiplier"],
            field_name=f"{scenario_name}.sgr_cap_multiplier",
        ),
    )


def load_projection_config(
    config_path: str | Path | None = None,
) -> FinancialProjectionConfig:
    path = Path(config_path) if config_path is not None else DEFAULT_SCENARIO_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Scenario config not found: {path.as_posix()}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Scenario config root must be a JSON object")

    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version != EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version '{schema_version}'. "
            f"Expected '{EXPECTED_SCHEMA_VERSION}'."
        )

    scenarios_raw = payload.get("scenarios")
    if not isinstance(scenarios_raw, dict):
        raise ValueError("'scenarios' must be an object")

    missing_scenarios = [
        name for name in REQUIRED_SCENARIO_KEYS if name not in scenarios_raw
    ]
    if missing_scenarios:
        raise ValueError(
            "Missing required scenarios: " + ", ".join(missing_scenarios)
        )
    unknown_scenarios = sorted(set(scenarios_raw) - set(REQUIRED_SCENARIO_KEYS))
    if unknown_scenarios:
        raise ValueError(
            "Unknown scenario keys (expected only bear/base/bull): "
            + ", ".join(unknown_scenarios)
        )

    scenarios = {
        name: _parse_scenario_params(name, scenarios_raw[name])
        for name in REQUIRED_SCENARIO_KEYS
    }

    horizon_years = _as_int(payload.get("horizon_years"), field_name="horizon_years")
    if horizon_years < 1:
        raise ValueError("horizon_years must be >= 1")

    peer_trim = _as_float(
        payload.get("peer_trim_fraction", 0.05), field_name="peer_trim_fraction"
    )
    if not 0.0 <= peer_trim < 0.5:
        raise ValueError("peer_trim_fraction must be in [0, 0.5)")

    lens_raw = payload.get("valuation_lens_rules") or {}
    if lens_raw is None:
        lens_raw = {}
    if not isinstance(lens_raw, dict):
        raise ValueError("valuation_lens_rules must be an object when provided")

    def _as_str_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError(f"{field_name} must be a list of strings")
        return tuple(str(item).strip() for item in value if str(item).strip())

    earnings_industries = _as_str_tuple(
        lens_raw.get("earnings_preferred_industries"),
        field_name="valuation_lens_rules.earnings_preferred_industries",
    )
    earnings_sectors = _as_str_tuple(
        lens_raw.get("earnings_preferred_sectors"),
        field_name="valuation_lens_rules.earnings_preferred_sectors",
    )
    unsuitable_sectors = _as_str_tuple(
        lens_raw.get("ev_rev_unsuitable_sectors"),
        field_name="valuation_lens_rules.ev_rev_unsuitable_sectors",
    )
    return FinancialProjectionConfig(
        schema_version=schema_version,
        config_id=str(payload.get("config_id") or path.stem),
        description=str(payload.get("description") or ""),
        horizon_years=horizon_years,
        min_market_cap_usd=_as_float(
            payload.get("min_market_cap_usd", 0.0), field_name="min_market_cap_usd"
        ),
        min_peer_group_size=_as_int(
            payload.get("min_peer_group_size", 8), field_name="min_peer_group_size"
        ),
        peer_trim_fraction=peer_trim,
        relative_scale_clip_low=_as_float(
            payload.get("relative_scale_clip_low", 0.25),
            field_name="relative_scale_clip_low",
        ),
        relative_scale_clip_high=_as_float(
            payload.get("relative_scale_clip_high", 3.0),
            field_name="relative_scale_clip_high",
        ),
        peer_mcap_band_low=_as_float(
            payload.get("peer_mcap_band_low", 0.25),
            field_name="peer_mcap_band_low",
        ),
        peer_mcap_band_high=_as_float(
            payload.get("peer_mcap_band_high", 4.0),
            field_name="peer_mcap_band_high",
        ),
        peer_mcap_refine_min_industry_n=_as_int(
            payload.get("peer_mcap_refine_min_industry_n", 40),
            field_name="peer_mcap_refine_min_industry_n",
        ),
        max_starting_growth_fraction=_as_float(
            payload.get("max_starting_growth_fraction", 1.0),
            field_name="max_starting_growth_fraction",
        ),
        forecast_growth_max_fraction=_as_float(
            payload.get("forecast_growth_max_fraction", 1.5),
            field_name="forecast_growth_max_fraction",
        ),
        max_ev_to_revenue=_as_float(
            payload.get("max_ev_to_revenue", 80.0),
            field_name="max_ev_to_revenue",
        ),
        min_revenue_usd=_as_float(
            payload.get("min_revenue_usd", 25_000_000.0),
            field_name="min_revenue_usd",
        ),
        rich_growth_fy_rev_pct=_as_float(
            payload.get("rich_growth_fy_rev_pct", 15.0),
            field_name="rich_growth_fy_rev_pct",
        ),
        valuation_conflict_pp=_as_float(
            payload.get("valuation_conflict_pp", 40.0),
            field_name="valuation_conflict_pp",
        ),
        outlier_terminal_upside_pct=_as_float(
            payload.get("outlier_terminal_upside_pct", 400.0),
            field_name="outlier_terminal_upside_pct",
        ),
        outlier_max_ev_rev=_as_float(
            payload.get("outlier_max_ev_rev", 1.5),
            field_name="outlier_max_ev_rev",
        ),
        scenario_width_wide_pp=_as_float(
            payload.get("scenario_width_wide_pp", 100.0),
            field_name="scenario_width_wide_pp",
        ),
        rev_eps_divergence_hot_pp=_as_float(
            payload.get("rev_eps_divergence_hot_pp", 25.0),
            field_name="rev_eps_divergence_hot_pp",
        ),
        min_peer_n_trust=_as_int(
            payload.get("min_peer_n_trust", 15),
            field_name="min_peer_n_trust",
        ),
        valuation_lens_rules=ValuationLensRules(
            earnings_preferred_industries=earnings_industries,
            earnings_preferred_sectors=earnings_sectors,
            ev_rev_unsuitable_sectors=unsuitable_sectors,
        ),
        scenarios=scenarios,
        source_path=path.resolve(),
    )
