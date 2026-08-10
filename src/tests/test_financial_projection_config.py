from pathlib import Path

import pytest

from financial_projection.config import (
    EXPECTED_SCHEMA_VERSION,
    load_projection_config,
)


def test_load_default_scenario_config() -> None:
    config = load_projection_config()
    assert config.schema_version == EXPECTED_SCHEMA_VERSION
    assert config.horizon_years == 5
    assert set(config.scenarios) == {"bear", "base", "bull"}
    assert config.scenarios["bear"].revenue_growth_scale == 0.55
    assert config.scenarios["base"].revenue_growth_scale == 1.0
    assert config.scenarios["bull"].revenue_growth_scale == 1.25
    assert config.peer_mcap_refine_min_industry_n == 0
    assert "Finance" in config.valuation_lens_rules.ev_rev_unsuitable_sectors
    assert "Managed Health Care" in config.valuation_lens_rules.earnings_preferred_industries
    assert config.rich_growth_fy_rev_pct == 15.0
    assert config.outlier_terminal_upside_pct == 400.0


def test_unknown_scenario_key_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / "bad_scenarios.json"
    path.write_text(
        """
        {
          "schema_version": "financial_projection_scenarios_v1",
          "horizon_years": 5,
          "min_market_cap_usd": 1,
          "min_peer_group_size": 8,
          "peer_trim_fraction": 0.05,
          "scenarios": {
            "bear": {
              "revenue_growth_override": null,
              "revenue_growth_scale": 0.5,
              "growth_fade_per_year": 0.9,
              "terminal_multiple_scale_vs_peer": 0.8,
              "own_multiple_blend": 0.5,
              "net_debt_growth_per_year": 0.0,
              "sgr_cap_multiplier": 1.0
            },
            "base": {
              "revenue_growth_override": null,
              "revenue_growth_scale": 1.0,
              "growth_fade_per_year": 0.9,
              "terminal_multiple_scale_vs_peer": 1.0,
              "own_multiple_blend": 0.5,
              "net_debt_growth_per_year": 0.0,
              "sgr_cap_multiplier": 1.0
            },
            "bull": {
              "revenue_growth_override": null,
              "revenue_growth_scale": 1.2,
              "growth_fade_per_year": 0.95,
              "terminal_multiple_scale_vs_peer": 1.1,
              "own_multiple_blend": 0.5,
              "net_debt_growth_per_year": 0.0,
              "sgr_cap_multiplier": 1.0
            },
            "moon": {
              "revenue_growth_override": null,
              "revenue_growth_scale": 2.0,
              "growth_fade_per_year": 1.0,
              "terminal_multiple_scale_vs_peer": 2.0,
              "own_multiple_blend": 0.5,
              "net_debt_growth_per_year": 0.0,
              "sgr_cap_multiplier": 1.0
            }
          }
        }
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unknown scenario keys"):
        load_projection_config(path)


def test_missing_required_scenario_fails_fast(tmp_path: Path) -> None:
    path = tmp_path / "missing_bull.json"
    path.write_text(
        """
        {
          "schema_version": "financial_projection_scenarios_v1",
          "horizon_years": 5,
          "scenarios": {
            "bear": {
              "revenue_growth_override": null,
              "revenue_growth_scale": 0.5,
              "growth_fade_per_year": 0.9,
              "terminal_multiple_scale_vs_peer": 0.8,
              "own_multiple_blend": 0.5,
              "net_debt_growth_per_year": 0.0,
              "sgr_cap_multiplier": 1.0
            },
            "base": {
              "revenue_growth_override": null,
              "revenue_growth_scale": 1.0,
              "growth_fade_per_year": 0.9,
              "terminal_multiple_scale_vs_peer": 1.0,
              "own_multiple_blend": 0.5,
              "net_debt_growth_per_year": 0.0,
              "sgr_cap_multiplier": 1.0
            }
          }
        }
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Missing required scenarios"):
        load_projection_config(path)
