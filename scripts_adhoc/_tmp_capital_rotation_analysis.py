"""Adhoc: capital rotation analysis across edge runs + all-fields snapshot."""
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
OUT_JSON = Path(__file__).with_name("_tmp_capital_rotation_analysis.json")

THEMES = {
    "Semiconductors": [r"semiconductor", r"chip", r"wafer", r"foundry", r"fab"],
    "Software": [r"software", r"saas", r"cloud", r"application", r"internet content", r"data processing"],
    "Healthcare/Nursing": [
        r"biotech", r"pharma", r"health", r"medical", r"hospital", r"nursing", r"drug", r"life science",
    ],
    "Insurance/Financial": [r"insurance", r"reinsurance", r"financial"],
}


def theme_match(industry: str) -> list[str]:
    if not isinstance(industry, str):
        return []
    s = industry.lower()
    return [t for t, pats in THEMES.items() if any(re.search(p, s) for p in pats)]


def run_date(name: str) -> str:
    m = re.search(r"(\d{8})_", name)
    return m.group(1) if m else name


def theme_rollup(df: pd.DataFrame, date: str) -> pd.DataFrame:
    sub = df[df.run_date == date].copy()
    rows = []
    for theme, pats in THEMES.items():
        mask = sub["industry"].str.contains("|".join(pats), case=False, na=False, regex=True)
        part = sub[mask]
        if part.empty:
            continue
        rows.append(
            {
                "theme": theme,
                "run_date": date,
                "industries": int(part["industry"].nunique()),
                "total_occ": float(part["total_occ"].sum()),
                "med5": float(part["med5"].median()),
                "win5": float(part["win5"].mean()),
                "symbols": float(part["symbols"].sum()),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    runs = sorted(p for p in RUNS_ROOT.iterdir() if p.is_dir() and "500m_full_parent" in p.name)
    industry_ts: list[pd.DataFrame] = []
    lane_ts: list[pd.DataFrame] = []
    rotation_ts: list[pd.DataFrame] = []
    unified_ind_ts: list[pd.DataFrame] = []

    for run_path in runs:
        d = run_date(run_path.name)
        agg = run_path / "aggregate"
        scan = pd.read_csv(agg / "scan_edge/edge_summary_industry.csv")
        g = scan.groupby("industry", as_index=False).agg(
            setups=("setup_name", "nunique"),
            total_occ=("occurrence_count", "sum"),
            symbols=("distinct_symbol_count", "sum"),
            med5=("median_fwd_5d", "median"),
            win5=("win_rate_5d", "mean"),
            med3=("median_fwd_3d", "median"),
            win3=("win_rate_3d", "mean"),
        )
        g["run_date"] = d
        industry_ts.append(g)

        lane = agg / "highlights/edge_lane_leaders.csv"
        if lane.exists():
            ld = pd.read_csv(lane)
            ld["run_date"] = d
            lane_ts.append(ld)

        rot = agg / "blindspot_lane/edge_blindspot_industry_rotation.csv"
        if rot.exists():
            rd = pd.read_csv(rot)
            rd["run_date"] = d
            rotation_ts.append(rd)

        uni = agg / "edge_unified_highlights/edge_unified_highlights.csv"
        if uni.exists():
            u = pd.read_csv(uni)
            ug = u.groupby("industry", as_index=False).agg(
                n=("symbol", "count"),
                med_unified_rank=("unified_edge_highlight_rank", "median"),
                med_upside_rank=("upside_prediction_rank", "median"),
                med_tradeable=("tradeable_safety_rank", "median"),
                med_fwd_upside=("forward_valuation_upside_pct", "median"),
                med_safety=("safety_companion_score", "median"),
                med_mover=("big_mover_score", "median"),
            )
            ug["run_date"] = d
            unified_ind_ts.append(ug)

    ind_all = pd.concat(industry_ts, ignore_index=True)
    lane_all = pd.concat(lane_ts, ignore_index=True) if lane_ts else pd.DataFrame()
    rot_all = pd.concat(rotation_ts, ignore_index=True) if rotation_ts else pd.DataFrame()
    uni_all = pd.concat(unified_ind_ts, ignore_index=True) if unified_ind_ts else pd.DataFrame()

    mid_dates = sorted(ind_all["run_date"].unique())
    latest_d = mid_dates[-1]
    earliest_d = mid_dates[0]

    theme_all = pd.concat([theme_rollup(ind_all, d) for d in mid_dates], ignore_index=True)
    latest_theme = theme_all[theme_all.run_date == latest_d].set_index("theme")
    early_theme = theme_all[theme_all.run_date == earliest_d].set_index("theme")
    theme_delta = latest_theme.join(early_theme, lsuffix="_latest", rsuffix="_early", how="outer")
    theme_delta["med5_delta"] = theme_delta["med5_latest"] - theme_delta["med5_early"]
    theme_delta["occ_delta_pct"] = (
        theme_delta["total_occ_latest"] / theme_delta["total_occ_early"] - 1
    ) * 100

    pivot = ind_all.pivot_table(index="industry", columns="run_date", values="med5", aggfunc="median")
    cols = sorted(pivot.columns)
    top_up = top_dn = pd.DataFrame()
    if len(cols) >= 2:
        pivot["delta_med5"] = pivot[cols[-1]] - pivot[cols[0]]
        pivot["latest_med5"] = pivot[cols[-1]]
        occ_latest = ind_all[ind_all.run_date == cols[-1]].set_index("industry")["total_occ"]
        pivot["occ_latest"] = occ_latest
        top_up = pivot.sort_values("delta_med5", ascending=False).head(15)
        top_dn = pivot.sort_values("delta_med5", ascending=True).head(15)

    # per-date theme edge med5 time series for chart
    theme_ts_chart = theme_all.pivot(index="run_date", columns="theme", values="med5").reset_index()
    theme_occ_chart = theme_all.pivot(index="run_date", columns="theme", values="total_occ").reset_index()

    # unified theme time series
    unified_theme_ts = []
    for d in mid_dates:
        sub = uni_all[uni_all.run_date == d]
        row: dict = {"run_date": d}
        for theme, pats in THEMES.items():
            mask = sub["industry"].str.contains("|".join(pats), case=False, na=False, regex=True)
            part = sub[mask]
            row[f"{theme}_names"] = int(part["n"].sum()) if len(part) else 0
            row[f"{theme}_med_fwd"] = float(part["med_fwd_upside"].median()) if len(part) else None
            row[f"{theme}_med_safety"] = float(part["med_safety"].median()) if len(part) else None
        unified_theme_ts.append(row)

    # all fields
    con = duckdb.connect(str(AF_DB), read_only=True)
    meta = con.execute(
        "SELECT run_date_utc, iso_week, min_market_cap_usd, scan_data_count FROM run_metadata"
    ).fetchdf()
    cols = [r[0] for r in con.execute("DESCRIBE all_fields_rows").fetchall()]
    composite_expr = "MEDIAN(composite_score)" if "composite_score" in cols else "NULL"
    af_ind = con.execute(
        f"""
        SELECT
          industry,
          COUNT(*) AS n,
          MEDIAN(TRY_CAST("change|1W" AS DOUBLE)) AS med_chg_1w,
          MEDIAN(TRY_CAST("change|1M" AS DOUBLE)) AS med_chg_1m,
          MEDIAN(TRY_CAST("change|5" AS DOUBLE)) AS med_chg_5d,
          AVG(CASE WHEN TRY_CAST("change|1W" AS DOUBLE) > 0 THEN 1.0 ELSE 0.0 END) AS pct_up_1w,
          MEDIAN(TRY_CAST(market_cap_basic AS DOUBLE)) AS med_mcap,
          {composite_expr} AS med_composite
        FROM all_fields_rows
        WHERE TRY_CAST(market_cap_basic AS DOUBLE) >= 500000000
          AND industry IS NOT NULL AND industry <> ''
        GROUP BY 1
        HAVING COUNT(*) >= 3
        ORDER BY med_chg_1w DESC
        """
    ).fetchdf()
    af_ind["theme"] = af_ind["industry"].apply(
        lambda x: theme_match(x)[0] if theme_match(x) else "Other"
    )
    theme_af = (
        af_ind.groupby("theme", as_index=False)
        .agg(
            industries=("industry", "count"),
            names=("n", "sum"),
            med_1w=("med_chg_1w", "median"),
            med_1m=("med_chg_1m", "median"),
            med_5d=("med_chg_5d", "median"),
            pct_up=("pct_up_1w", "mean"),
        )
        .sort_values("med_1w", ascending=False)
    )

    uni_path = runs[-1] / "aggregate/edge_unified_highlights/edge_unified_highlights.csv"
    uni = pd.read_csv(uni_path)
    pick_cols = [
        "theme",
        "symbol",
        "company_name",
        "industry",
        "unified_edge_highlight_rank",
        "tradeable_safety_rank",
        "forward_valuation_upside_pct",
        "safety_companion_score",
        "rotation_score",
    ]
    if "historical_validation_bucket" in uni.columns:
        pick_cols.insert(-1, "historical_validation_bucket")
    picks: list[pd.DataFrame] = []
    for theme, pats in THEMES.items():
        mask = uni["industry"].str.contains("|".join(pats), case=False, na=False, regex=True)
        sub = uni[mask].copy()
        if sub.empty:
            continue
        sub["theme"] = theme
        n = len(uni)
        sub["rotation_score"] = (
            0.35 * (1 - sub["unified_edge_highlight_rank"].fillna(n).rank(pct=True))
            + 0.25 * sub["forward_valuation_upside_pct"].fillna(0).rank(pct=True)
            + 0.20 * sub["safety_companion_score"].fillna(0).rank(pct=True)
            + 0.20 * (1 - sub["tradeable_safety_rank"].fillna(n).rank(pct=True))
        )
        picks.append(sub.sort_values("rotation_score", ascending=False).head(8))

    rotation_latest = pd.DataFrame()
    if not rot_all.empty:
        sortc = (
            "industry_rotation_score"
            if "industry_rotation_score" in rot_all.columns
            else rot_all.columns[1]
        )
        rotation_latest = rot_all[rot_all.run_date == latest_d].sort_values(sortc, ascending=False).head(20)

    lane_latest = lane_all[lane_all.run_date == latest_d] if not lane_all.empty else pd.DataFrame()

    payload = {
        "meta": {
            "earliest_run": earliest_d,
            "latest_run": latest_d,
            "run_count": len(runs),
            "all_fields_date": str(meta["run_date_utc"].iloc[0]) if len(meta) else "2026-07-13",
            "all_fields_week": int(meta["iso_week"].iloc[0]) if len(meta) else 28,
            "min_mcap_usd": 500_000_000,
        },
        "theme_delta": theme_delta.reset_index().round(4).to_dict(orient="records"),
        "theme_timeseries": theme_all.round(4).to_dict(orient="records"),
        "theme_ts_chart": theme_ts_chart.round(4).to_dict(orient="records"),
        "theme_occ_chart": theme_occ_chart.round(4).to_dict(orient="records"),
        "unified_theme_ts": unified_theme_ts,
        "industry_gainers": top_up.reset_index().round(4).to_dict(orient="records"),
        "industry_losers": top_dn.reset_index().round(4).to_dict(orient="records"),
        "all_fields_theme": theme_af.round(4).to_dict(orient="records"),
        "all_fields_top": af_ind.head(15).round(4).to_dict(orient="records"),
        "all_fields_bottom": af_ind.tail(15).sort_values("med_chg_1w").round(4).to_dict(orient="records"),
        "rotation_latest": rotation_latest.round(4).to_dict(orient="records"),
        "lane_latest": lane_latest.round(4).to_dict(orient="records") if not lane_latest.empty else [],
        "theme_picks": (
            pd.concat(picks)[pick_cols]
            .round(4)
            .to_dict(orient="records")
        ) if picks else [],
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT_JSON}")
    print("\n=== THEME DELTA ===")
    print(theme_delta.sort_values("med5_delta", ascending=False).round(4).to_string())
    print("\n=== ALL FIELDS THEME ===")
    print(theme_af.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
