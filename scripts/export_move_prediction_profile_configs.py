"""Export versioned profile JSON files from built-in ScoringProfile presets.

Run after editing in-Python presets to materialize *_v1 JSON configs::

    PYTHONPATH=src python scripts/export_move_prediction_profile_configs.py
"""

from __future__ import annotations

from pathlib import Path

from data_analysis_scripts.trading_view_move_prediction_analysis import (
    PRESET_SCORING_PROFILES,
)
from data_analysis_scripts.trading_view_move_prediction_profile_config import (
    write_profile_config_file,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "config" / "move_prediction_profiles" / "profiles"

EXPORTS: dict[str, tuple[str, str, str]] = {
    "breakout_long_v1": (
        "breakout_long",
        "1.0.0",
        "Tier-1 confirmation signals and false-breakout guards.",
    ),
    "early_momentum_inflection_v1": (
        "early_momentum_inflection",
        "1.0.0",
        "Hull MA, BBPower, Donchian/PSAR handoff rules.",
    ),
    "forward_edge_active_v1": (
        "forward_edge_active",
        "1.0.0",
        "Pre-earnings drift weighting and recommend TF spread.",
    ),
    "durable_value_compounder_v1": (
        "durable_value_compounder",
        "1.0.0",
        "EBITDA/employee and stronger valuation evidence penalties.",
    ),
    "sector_relative_outperformer_v1": (
        "sector_relative_outperformer",
        "1.0.0",
        "CMF accumulation and EBITDA/employee efficiency.",
    ),
    "value_recovery_v1": (
        "value_recovery",
        "1.0.0",
        "BBPower divergence as reversal trigger.",
    ),
    "income_compounder_v1": (
        "income_compounder",
        "1.0.0",
        "Dividend-growth compounder lens.",
    ),
    "pre_earnings_drift_v1": (
        "pre_earnings_drift",
        "1.0.0",
        "Coiled tape into earnings window.",
    ),
    "mean_reversion_exhaustion_v1": (
        "mean_reversion_exhaustion",
        "1.0.0",
        "Overextension risk overlay (consensus-inverted).",
    ),
    "defensive_fortress_v1": (
        "defensive_fortress",
        "1.0.0",
        "Low-beta defensive core lens.",
    ),
    "sector_rotation_momentum_v1": (
        "sector_rotation_momentum",
        "1.0.0",
        "Industry-relative tactical momentum.",
    ),
    "quality_growth_at_reasonable_price_v1": (
        "quality_growth_at_reasonable_price",
        "1.0.0",
        "GARP bridge profile.",
    ),
}


def main() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    for profile_id, (source_name, version, changelog) in EXPORTS.items():
        source_profile = PRESET_SCORING_PROFILES.get(source_name)
        if source_profile is None:
            existing = PROFILES_DIR / f"{profile_id}.json"
            if existing.exists():
                print(f"skip {profile_id}: no Python preset '{source_name}', JSON exists")
                continue
            raise KeyError(
                f"Cannot export {profile_id}: missing preset '{source_name}' "
                f"and no existing JSON at {existing}"
            )
        versioned_profile = type(source_profile)(
            name=profile_id,
            description=source_profile.description,
            horizon_weights=source_profile.horizon_weights,
            component_signal_weights=source_profile.component_signal_weights,
            component_directional_bias=source_profile.component_directional_bias,
            missing_component_scores_by_horizon=source_profile.missing_component_scores_by_horizon,
            performance_tracking_periods=source_profile.performance_tracking_periods,
            intro_metric_notes=source_profile.intro_metric_notes,
            confidence_multiplier=source_profile.confidence_multiplier,
            confidence_offset=source_profile.confidence_offset,
        )
        output_path = write_profile_config_file(
            versioned_profile,
            output_path=PROFILES_DIR / f"{profile_id}.json",
            base_profile_id=source_name,
            version=version,
            effective_from="2026-06-09",
            changelog=changelog,
        )
        print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
