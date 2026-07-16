"""Ad-hoc follow-up: wide screen + persistence scan filtered to the 20 shortlisted tickers.

Reuses the same snapshot database and market scope as the
edge_latest_500m_full_parent_20260710_1645_utc_e4d46cec run, but with
min_composite / min_setup_days relaxed to 0 and top_n set very high so every
symbol (including calm, low-volatility names) appears in the ranked output
regardless of setup-quality threshold.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edge_research_tools.screener import run_edge_screen
from edge_research_tools.persistence_scanner import run_persistence_scan

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

TICKERS = [
    "TRYG", "AON", "HG", "WTM", "ACGL", "HCI", "MCY", "UVE", "RNR", "CASH",
    "TLV", "UMBF", "NBIX", "BMRN", "EXEL", "REGN", "INCY", "CPA", "IAG", "EZJ",
]


def main() -> None:
    print("=== Running wide screen (min_composite=0.0, top_n=6000) ===")
    screen_result = run_edge_screen(
        snapshot_database_path=SNAPSHOT_DB,
        top_n=6000,
        min_composite=0.0,
        markets=PREFERRED_MARKETS,
        output_root=OUTPUT_ROOT,
        duckdb_threads=16,
    )
    print(screen_result)

    print("\n=== Running wide persistence scan (min_setup_days=0, top_n=6000) ===")
    persistence_result = run_persistence_scan(
        snapshot_database_path=SNAPSHOT_DB,
        min_setup_days=0,
        top_n=6000,
        markets=PREFERRED_MARKETS,
        output_root=OUTPUT_ROOT,
        duckdb_threads=16,
    )
    print(persistence_result)

    import duckdb

    con = duckdb.connect()
    ticker_list_sql = ",".join(f"'{t}'" for t in TICKERS)

    print("\n=== Screen results for shortlisted tickers ===")
    screen_csv = str(screen_result["ranked_csv"])
    print(f"screen csv: {screen_csv}")
    variant_cols = con.execute(
        f"SELECT * FROM read_csv_auto('{screen_csv}') LIMIT 0"
    ).description
    in_cols = [c[0] for c in variant_cols if c[0].startswith("in_")]
    in_cols_sql = ", ".join(in_cols)
    rows = con.execute(
        f"""
        SELECT bare_ticker, symbol, sector, industry, composite_score, screen_rank, {in_cols_sql}
        FROM read_csv_auto('{screen_csv}')
        WHERE bare_ticker IN ({ticker_list_sql})
        ORDER BY bare_ticker
        """
    ).fetchall()
    cols = [d[0] for d in con.description]
    print(",".join(cols))
    for r in rows:
        print(r)
    found_screen = {r[0] for r in rows}
    print(f"\nFound in screen: {sorted(found_screen)}")
    print(f"Missing from screen entirely: {sorted(set(TICKERS) - found_screen)}")

    print("\n=== Persistence results for shortlisted tickers ===")
    persistence_csv = str(persistence_result["persistence_csv"])
    print(f"persistence csv: {persistence_csv}")
    rows2 = con.execute(
        f"""
        SELECT bare_ticker, symbol, sector, industry, any_setup_days, any_setup_rate,
               persistence_rank, avg_adrp_pct, avg_relvol_pct, avg_vol_core_pct, avg_liq_core_pct
        FROM read_csv_auto('{persistence_csv}')
        WHERE bare_ticker IN ({ticker_list_sql})
        ORDER BY bare_ticker
        """
    ).fetchall()
    cols2 = [d[0] for d in con.description]
    print(",".join(cols2))
    for r in rows2:
        print(r)
    found_persist = {r[0] for r in rows2}
    print(f"\nFound in persistence: {sorted(found_persist)}")
    print(f"Missing from persistence entirely: {sorted(set(TICKERS) - found_persist)}")


if __name__ == "__main__":
    main()
