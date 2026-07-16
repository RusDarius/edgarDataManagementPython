"""Full-market industry rotation: all industries, lanes, and setup names."""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pandas as pd

RUNS_ROOT = Path(
    "d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    "/edge_research_tools/runs"
)
AF_DB = Path(
    "d:/FinanceProjects/edgarDataManagementPython/logs/tradingview_analysis"
    "/trading_view_all_fields_data/13_07_2026/tradingview_all_fields_13_07_2026.duckdb"
)
LATEST_RUN = RUNS_ROOT / "edge_latest_500m_full_parent_20260713_1503_utc_ce6f36f2"
OUT_JSON = Path(__file__).with_name("_tmp_full_market_rotation_analysis.json")


def run_date(name: str) -> str:
    m = re.search(r"(\d{8})_", name)
    return m.group(1) if m else name


def bare(sym: str) -> str:
    return sym.split(":")[-1] if isinstance(sym, str) and ":" in sym else str(sym)


def setup_score(row: pd.Series, n: int) -> float:
    return float(
        0.30 * (1 - row.get("unified_edge_highlight_rank", n) / max(n, 1))
        + 0.20 * row.get("forward_valuation_upside_pct", 0) / 100
        + 0.15 * row.get("safety_companion_score", 0)
        + 0.15 * (1 - row.get("tradeable_safety_rank", n) / max(n, 1))
        + 0.10 * row.get("lane_context_score", 0)
        + 0.10 * row.get("composite_score", 0)
    )


def classify_quadrant(med_1w: float, med5_delta: float) -> str:
    price_up = med_1w > 0
    edge_up = med5_delta > 0.05
    if price_up and edge_up:
        return "Momentum + edge"
    if not price_up and edge_up:
        return "Early rotation (price lag)"
    if price_up and not edge_up:
        return "Late / fading edge"
    return "Avoid / unwind"


def main() -> None:
    runs = sorted(p for p in RUNS_ROOT.iterdir() if p.is_dir() and "500m_full_parent" in p.name)
    industry_ts: list[pd.DataFrame] = []

    for run_path in runs:
        d = run_date(run_path.name)
        scan = pd.read_csv(run_path / "aggregate/scan_edge/edge_summary_industry.csv")
        g = scan.groupby("industry", as_index=False).agg(
            total_occ=("occurrence_count", "sum"),
            symbols=("distinct_symbol_count", "sum"),
            med5=("median_fwd_5d", "median"),
            win5=("win_rate_5d", "mean"),
        )
        g["run_date"] = d
        industry_ts.append(g)

    ind_all = pd.concat(industry_ts, ignore_index=True)
    dates = sorted(ind_all["run_date"].unique())
    earliest, latest = dates[0], dates[-1]

    edge_pivot = ind_all.pivot_table(index="industry", columns="run_date", values="med5", aggfunc="median")
    edge_pivot["med5_earliest"] = edge_pivot[earliest]
    edge_pivot["med5_latest"] = edge_pivot[latest]
    edge_pivot["med5_delta"] = edge_pivot["med5_latest"] - edge_pivot["med5_earliest"]
    occ_latest = ind_all[ind_all.run_date == latest].set_index("industry")["total_occ"]
    win_latest = ind_all[ind_all.run_date == latest].set_index("industry")["win5"]
    edge_pivot["occ_latest"] = occ_latest
    edge_pivot["win5_latest"] = win_latest
    edge_df = edge_pivot.reset_index()

    # all-fields price by industry + sector
    con = duckdb.connect(str(AF_DB), read_only=True)
    meta = con.execute(
        "SELECT run_date_utc, iso_week, scan_data_count FROM run_metadata"
    ).fetchdf()
    af = con.execute(
        """
        SELECT
          industry,
          sector,
          COUNT(*) AS n,
          MEDIAN(TRY_CAST("change|1W" AS DOUBLE)) AS med_chg_1w,
          MEDIAN(TRY_CAST("change|1M" AS DOUBLE)) AS med_chg_1m,
          MEDIAN(TRY_CAST("Perf.3M" AS DOUBLE)) AS med_chg_3m,
          AVG(CASE WHEN TRY_CAST("change|1W" AS DOUBLE) > 0 THEN 1.0 ELSE 0.0 END) AS pct_up_1w,
          AVG(CASE WHEN TRY_CAST("change|1M" AS DOUBLE) > 0 THEN 1.0 ELSE 0.0 END) AS pct_up_1m
        FROM all_fields_rows
        WHERE TRY_CAST(market_cap_basic AS DOUBLE) >= 500000000
          AND industry IS NOT NULL AND industry <> ''
        GROUP BY 1, 2
        HAVING COUNT(*) >= 3
        """
    ).fetchdf()

    # rotation (all industries)
    rot = pd.read_csv(LATEST_RUN / "aggregate/blindspot_lane/edge_blindspot_industry_rotation.csv")
    rot_cols = [
        "industry", "sector", "industry_symbol_count", "industry_median_perf_1m",
        "industry_breadth_positive_1m", "industry_breadth_delta",
        "industry_relative_strength_percentile", "industry_rotation_delta",
        "industry_rotation_score", "industry_rotation_rank", "top_quiet_movers",
        "blindspot_hist_median_fwd_pct", "blindspot_hist_win_rate",
    ]
    rot = rot[[c for c in rot_cols if c in rot.columns]]

    # lane leaders (all industries)
    lanes = pd.read_csv(LATEST_RUN / "aggregate/highlights/edge_lane_leaders.csv")
    lane_cols = [
        "group_value", "occurrence_count", "distinct_symbol_count",
        "median_fwd_5d", "win_rate_5d", "median_fwd_10d", "win_rate_10d",
        "median_fwd_20d", "win_rate_20d", "median_fwd_5d_ci_low", "median_fwd_5d_ci_high",
    ]
    lanes = lanes[lanes["group_by"] == "industry"][lane_cols].rename(columns={"group_value": "industry"})
    lanes = lanes.sort_values("median_fwd_5d", ascending=False)

    # unified shortlist by industry
    uni = pd.read_csv(LATEST_RUN / "aggregate/edge_unified_highlights/edge_unified_highlights.csv")
    uni_ind = uni.groupby("industry", as_index=False).agg(
        shortlist_n=("symbol", "count"),
        med_unified_rank=("unified_edge_highlight_rank", "median"),
        med_fwd_upside=("forward_valuation_upside_pct", "median"),
        med_safety=("safety_companion_score", "median"),
        med_tradeable_rank=("tradeable_safety_rank", "median"),
        top_symbol=("symbol", lambda s: s.iloc[0]),
    )
    # fix top_symbol - get best rank per industry
    best_per_ind = (
        uni.sort_values("unified_edge_highlight_rank")
        .groupby("industry", as_index=False)
        .first()[["industry", "symbol", "bare_ticker", "unified_edge_highlight_rank"]]
        .rename(columns={"symbol": "top_unified_symbol", "bare_ticker": "top_unified_ticker"})
    )

    # master merge
    master = af.merge(edge_df, on="industry", how="outer")
    master = master.merge(rot, on="industry", how="left", suffixes=("", "_rot"))
    if "sector_rot" in master.columns:
        master["sector"] = master["sector"].fillna(master["sector_rot"])
        master.drop(columns=["sector_rot"], inplace=True)
    master = master.merge(lanes.add_prefix("lane_"), left_on="industry", right_on="lane_industry", how="left")
    master = master.merge(best_per_ind, on="industry", how="left")
    master = master.merge(
        uni_ind[["industry", "shortlist_n", "med_fwd_upside", "med_safety"]],
        on="industry",
        how="left",
    )

    for c in ["med_chg_1w", "med_chg_1m", "med_chg_3m", "med5_delta"]:
        if c in master.columns:
            master[c] = pd.to_numeric(master[c], errors="coerce")

    master["quadrant"] = master.apply(
        lambda r: classify_quadrant(
            float(r["med_chg_1w"]) if pd.notna(r.get("med_chg_1w")) else 0,
            float(r["med5_delta"]) if pd.notna(r.get("med5_delta")) else 0,
        ),
        axis=1,
    )

    # composite opportunity score for ranking industries
    master["move_score"] = (
        master["med_chg_1w"].rank(pct=True, na_option="bottom") * 0.15
        + master["med_chg_1m"].rank(pct=True, na_option="bottom") * 0.20
        + master["med5_delta"].rank(pct=True, na_option="bottom") * 0.20
        + master["industry_rotation_score"].rank(pct=True, na_option="bottom") * 0.25
        + master["lane_median_fwd_5d"].rank(pct=True, na_option="bottom") * 0.20
    )

    # sector rollup
    sector_df = (
        master.groupby("sector", as_index=False)
        .agg(
            industries=("industry", "count"),
            names=("n", "sum"),
            med_1w=("med_chg_1w", "median"),
            med_1m=("med_chg_1m", "median"),
            med5_delta=("med5_delta", "median"),
            med_rotation=("industry_rotation_score", "median"),
            shortlist_total=("shortlist_n", "sum"),
        )
        .sort_values("med_1m", ascending=False)
    )

    # per-industry setup names (top 4 unified + top 2 blindspot per industry with shortlist)
    blind = pd.read_csv(LATEST_RUN / "aggregate/blindspot_lane/edge_blindspot_symbol_top40.csv")
    screen = pd.read_csv(
        LATEST_RUN / "aggregate/screen/edge_screen_ranked.csv",
        usecols=["bare_ticker", "symbol", "industry", "composite_score", "screen_rank"],
    )

    n_uni = len(uni)
    uni["setup_score"] = uni.apply(lambda r: setup_score(r, n_uni), axis=1)

    industry_setups: list[dict] = []
    industries_with_signal = master[
        (master["shortlist_n"].fillna(0) > 0)
        | (master["industry_rotation_score"].fillna(0) > 0.5)
        | (master["lane_median_fwd_5d"].fillna(-99) > 2)
    ]["industry"].dropna().unique()

    for ind in sorted(industries_with_signal):
        row = master[master.industry == ind].iloc[0] if (master.industry == ind).any() else None
        if row is None:
            continue
        u_sub = uni[uni.industry == ind].sort_values("setup_score", ascending=False).head(4)
        b_sub = blind[blind.industry == ind].sort_values("quiet_mover_score", ascending=False).head(2)
        s_sub = screen[screen.industry == ind].sort_values("composite_score", ascending=False).head(2)

        names: list[dict] = []
        seen: set[str] = set()
        for _, r in u_sub.iterrows():
            t = bare(r["symbol"])
            if t in seen:
                continue
            seen.add(t)
            names.append({
                "ticker": t,
                "symbol": r["symbol"],
                "source": "unified",
                "unified_rank": int(r["unified_edge_highlight_rank"]),
                "fwd_upside_pct": round(float(r["forward_valuation_upside_pct"]), 1),
                "safety": round(float(r["safety_companion_score"]), 3),
                "tradeable_rank": int(r["tradeable_safety_rank"]),
                "hist_bucket": str(r.get("historical_validation_bucket", "")),
                "lane": str(r.get("lane_group_value", ind)),
                "setup_score": round(float(r["setup_score"]), 3),
            })
        for _, r in b_sub.iterrows():
            t = bare(r["symbol"])
            if t in seen:
                continue
            seen.add(t)
            names.append({
                "ticker": t,
                "symbol": r["symbol"],
                "source": "blindspot",
                "quiet_score": round(float(r["quiet_mover_score"]), 3),
                "perf_1m": round(float(r["perf_1m"]), 1),
                "in_shortlist": bool(r.get("in_shortlist_flag", False)),
            })
        for _, r in s_sub.iterrows():
            t = bare(r["symbol"])
            if t in seen:
                continue
            seen.add(t)
            names.append({
                "ticker": t,
                "symbol": r["symbol"],
                "source": "screen",
                "screen_rank": int(r["screen_rank"]),
                "composite": round(float(r["composite_score"]), 3),
            })

        if not names:
            continue

        industry_setups.append({
            "industry": ind,
            "sector": str(row.get("sector", "")),
            "quadrant": str(row.get("quadrant", "")),
            "med_1w": round(float(row["med_chg_1w"]), 2) if pd.notna(row.get("med_chg_1w")) else None,
            "med_1m": round(float(row["med_chg_1m"]), 2) if pd.notna(row.get("med_chg_1m")) else None,
            "med5_delta": round(float(row["med5_delta"]), 2) if pd.notna(row.get("med5_delta")) else None,
            "rotation_score": round(float(row["industry_rotation_score"]), 3) if pd.notna(row.get("industry_rotation_score")) else None,
            "rotation_rank": int(row["industry_rotation_rank"]) if pd.notna(row.get("industry_rotation_rank")) else None,
            "lane_med5": round(float(row["lane_median_fwd_5d"]), 2) if pd.notna(row.get("lane_median_fwd_5d")) else None,
            "shortlist_n": int(row["shortlist_n"]) if pd.notna(row.get("shortlist_n")) else 0,
            "move_score": round(float(row["move_score"]), 3) if pd.notna(row.get("move_score")) else None,
            "setups": names[:6],
        })

    industry_setups.sort(key=lambda x: x.get("move_score") or 0, reverse=True)

    def slim(df: pd.DataFrame, cols: list[str], n: int = 30) -> list[dict]:
        use = [c for c in cols if c in df.columns]
        out = df[use].head(n).round(3).replace({float("nan"): None}).to_dict(orient="records")
        return out

    master_out_cols = [
        "industry", "sector", "n", "med_chg_1w", "med_chg_1m", "med_chg_3m", "pct_up_1w",
        "med5_latest", "med5_delta", "win5_latest", "occ_latest",
        "industry_rotation_score", "industry_rotation_rank", "industry_median_perf_1m",
        "lane_median_fwd_5d", "lane_win_rate_5d", "shortlist_n", "med_fwd_upside",
        "quadrant", "move_score", "top_unified_ticker",
    ]

    payload = {
        "meta": {
            "latest_run": latest,
            "earliest_run": earliest,
            "run_count": len(runs),
            "all_fields_date": str(meta["run_date_utc"].iloc[0]),
            "industry_count": int(master["industry"].nunique()),
            "min_mcap_usd": 500_000_000,
        },
        "sectors": sector_df.round(3).replace({float("nan"): None}).to_dict(orient="records"),
        "price_leaders_1w": slim(af.sort_values("med_chg_1w", ascending=False),
            ["industry", "sector", "n", "med_chg_1w", "med_chg_1m", "pct_up_1w"], 25),
        "price_laggards_1w": slim(af.sort_values("med_chg_1w", ascending=True),
            ["industry", "sector", "n", "med_chg_1w", "med_chg_1m", "pct_up_1w"], 25),
        "price_leaders_1m": slim(af.sort_values("med_chg_1m", ascending=False),
            ["industry", "sector", "n", "med_chg_1m", "med_chg_1w", "pct_up_1m"], 25),
        "edge_improvers": slim(edge_df.sort_values("med5_delta", ascending=False),
            ["industry", "med5_delta", "med5_latest", "occ_latest", "win5_latest"], 25),
        "edge_decliners": slim(edge_df.sort_values("med5_delta", ascending=True),
            ["industry", "med5_delta", "med5_latest", "occ_latest", "win5_latest"], 25),
        "rotation_all": slim(rot.sort_values("industry_rotation_score", ascending=False),
            ["industry", "sector", "industry_rotation_rank", "industry_rotation_score",
             "industry_median_perf_1m", "industry_breadth_delta", "top_quiet_movers"], 40),
        "lane_leaders_all": slim(lanes,
            ["industry", "occurrence_count", "median_fwd_5d", "win_rate_5d",
             "median_fwd_10d", "median_fwd_20d"], 35),
        "quadrant_counts": master.groupby("quadrant").size().to_dict(),
        "quadrant_early_rotation": slim(
            master[master.quadrant == "Early rotation (price lag)"].sort_values("move_score", ascending=False),
            master_out_cols, 20),
        "quadrant_momentum": slim(
            master[master.quadrant == "Momentum + edge"].sort_values("move_score", ascending=False),
            master_out_cols, 20),
        "quadrant_avoid": slim(
            master[master.quadrant == "Avoid / unwind"].sort_values("med_chg_1w", ascending=True),
            master_out_cols, 20),
        "top_opportunity_industries": slim(
            master.sort_values("move_score", ascending=False), master_out_cols, 30),
        "industry_setups": industry_setups[:80],
    }

    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Wrote {OUT_JSON} ({len(industry_setups)} industries with setups)")
    print(f"Industries in master: {master['industry'].nunique()}")
    print("\nQuadrants:", payload["quadrant_counts"])
    print("\nTop 10 opportunity industries:")
    for r in payload["top_opportunity_industries"][:10]:
        print(f"  {r['industry']}: move={r.get('move_score')} quad={r.get('quadrant')} 1w={r.get('med_chg_1w')}")


if __name__ == "__main__":
    main()
