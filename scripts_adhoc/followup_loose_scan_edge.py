"""Ad-hoc follow-up: scan-edge with relaxed volatility/liquidity thresholds.

Goal: test whether Finance (banks/insurance) and Health Care setups show real
forward-return edge once the ADRP / relative-volume / volatility-core gates
that the default variants require are loosened substantially. Uses two custom
loose variants alongside the three defaults for direct before/after contrast.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edge_research_tools.setup_engine import (
    VolatilityLiquiditySetupVariant,
    DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS,
)
from edge_research_tools.persistence_scanner import run_edge_summary

SNAPSHOT_DB = (
    "logs/tradingview_analysis/edge_research_tools/runs/"
    "edge_latest_500m_full_parent_20260710_1645_utc_e4d46cec/_staging/foundations/"
    "edge_feature_snapshot_20260710_1645_utc_3e41819a/symbol_day_feature_snapshot.duckdb"
)

PREFERRED_MARKETS = [
    "america", "canada", "mexico", "austria", "belgium", "cyprus", "czech",
    "denmark", "estonia", "finland", "france", "germany", "greece", "hungary",
    "iceland", "ireland", "italy", "latvia", "lithuania", "luxembourg",
    "netherlands", "norway", "poland", "portugal", "romania", "slovakia",
    "spain", "sweden", "switzerland", "uk",
]

OUTPUT_ROOT = "logs/tradingview_analysis/edge_research_tools/_adhoc_followup_20260712"

LOOSE_VARIANTS = (
    VolatilityLiquiditySetupVariant(
        name="loose_liquidity_momentum_v1",
        description="Relaxed vol/liq gate emphasizing momentum continuation; allows low-volatility grinders.",
        min_adrp_pct=0.40,
        min_relative_volume_pct=0.45,
        min_value_traded_pct=0.40,
        min_liquidity_core_pct=0.45,
        min_momentum_context_pct=0.55,
        min_volatility_core_pct=0.30,
    ),
    VolatilityLiquiditySetupVariant(
        name="loose_quiet_grinder_v1",
        description="Very permissive vol/liq gate; requires only decent momentum, near-zero volatility bar.",
        min_adrp_pct=0.30,
        min_relative_volume_pct=0.35,
        min_value_traded_pct=0.35,
        min_liquidity_core_pct=0.40,
        min_momentum_context_pct=0.60,
        min_volatility_core_pct=0.20,
    ),
)

ALL_VARIANTS = tuple(DEFAULT_VOLATILITY_LIQUIDITY_SETUP_VARIANTS) + LOOSE_VARIANTS

TARGET_INDUSTRIES = {
    "Insurance Brokers/Services",
    "Property/Casualty Insurance",
    "Multi-Line Insurance",
    "Life/Health Insurance",
    "Major Banks",
    "Regional Banks",
    "Savings Institutions",
    "Investment Managers",
    "Biotechnology",
    "Pharmaceuticals: Major",
    "Airlines",
}


def main() -> None:
    print("=== scan-edge --group-by sector (loose + default variants, min_occurrence=3) ===")
    sector_result = run_edge_summary(
        snapshot_database_path=SNAPSHOT_DB,
        group_by="sector",
        min_occurrence_count=3,
        markets=PREFERRED_MARKETS,
        output_root=OUTPUT_ROOT,
        duckdb_threads=16,
        variants=ALL_VARIANTS,
    )
    print(sector_result)

    print("\n=== scan-edge --group-by industry (loose + default variants, min_occurrence=3) ===")
    industry_result = run_edge_summary(
        snapshot_database_path=SNAPSHOT_DB,
        group_by="industry",
        min_occurrence_count=3,
        markets=PREFERRED_MARKETS,
        output_root=OUTPUT_ROOT,
        duckdb_threads=16,
        variants=ALL_VARIANTS,
    )
    print(industry_result)

    import duckdb

    con = duckdb.connect()

    print("\n=== Sector summary: Finance & Health Technology / Health Care rows ===")
    sector_csv = str(sector_result["summary_csv"])
    rows = con.execute(
        f"""
        SELECT *
        FROM read_csv_auto('{sector_csv}')
        WHERE sector IN ('Finance', 'Health Technology', 'Health Care', 'Consumer Services')
        ORDER BY setup_name, sector
        """
    ).fetchall()
    cols = [d[0] for d in con.description]
    print(",".join(cols))
    for r in rows:
        print(r)

    print("\n=== Industry summary: target bank/insurance/healthcare/airline industries ===")
    industry_csv = str(industry_result["summary_csv"])
    industries_sql = ",".join(f"'{v}'" for v in TARGET_INDUSTRIES)
    rows2 = con.execute(
        f"""
        SELECT *
        FROM read_csv_auto('{industry_csv}')
        WHERE industry IN ({industries_sql})
        ORDER BY setup_name, industry
        """
    ).fetchall()
    cols2 = [d[0] for d in con.description]
    print(",".join(cols2))
    for r in rows2:
        print(r)


if __name__ == "__main__":
    main()
