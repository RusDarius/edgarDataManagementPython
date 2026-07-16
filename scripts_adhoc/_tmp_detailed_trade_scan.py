"""Cross-run detailed trade opportunity scan for edge + move_prediction runs."""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pandas as pd

EDGE_BASE = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
    r"\edge_research_tools\runs\edge_latest_500m_full_parent_20260714_1546_utc_8a0ffb51\aggregate"
)
TV_DB = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
    r"\trading_view_all_fields_data\14_07_2026\tradingview_all_fields_14_07_2026.duckdb"
)
REGIME_LOG = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\logs\tradingview_analysis"
    r"\prediction_analysis\duckdb_runs\iso_year=2026\week=29\runs"
    r"\move_prediction_20260714_1520_utc_308b1ec2\move_prediction__regime_context_focus.log"
)
OUT_JSON = Path(
    r"D:\FinanceProjects\edgarDataManagementPython\scripts_adhoc\_tmp_detailed_trade_scan.json"
)

PROFILES = [
    "breakout_long_v1",
    "sustained_momentum_safety_v1",
    "early_momentum_inflection_v1",
    "forward_edge_active_v2",
    "quality_continuation_v1",
    "quality_value_compounder",
    "durable_value_compounder_v1",
    "asymmetric_value",
    "value_recovery_v3",
    "defensive_fortress_v2",
    "fragility_short",
    "mean_reversion_exhaustion_v1",
]


def load_name_map() -> dict[str, str]:
    con = duckdb.connect(str(TV_DB), read_only=True)
    tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    name_map: dict[str, str] = {}
    for table in tables:
        cols = [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
        sym_col = next((c for c in cols if c.lower() in {"symbol", "ticker"}), None)
        desc_col = next(
            (
                c
                for c in cols
                if c.lower() in {"description", "company_name", "name", "long_name"}
            ),
            None,
        )
        if not sym_col or not desc_col:
            continue
        rows = con.execute(
            f'SELECT DISTINCT "{sym_col}", "{desc_col}" FROM "{table}" '
            f'WHERE "{desc_col}" IS NOT NULL AND TRIM("{desc_col}") != \'\''
        ).fetchdf()
        for _, row in rows.iterrows():
            sym = str(row[sym_col]).strip()
            desc = str(row[desc_col]).strip()
            if sym and desc and desc.upper() != sym.upper():
                name_map[sym] = desc
    con.close()
    return name_map


def full_name(symbol: str, short: str, name_map: dict[str, str]) -> str:
    bare = symbol.split(":", 1)[-1] if ":" in symbol else symbol
    for key in (symbol, bare, f"NASDAQ:{bare}", f"NYSE:{bare}"):
        if key in name_map:
            return name_map[key]
    if short and short.upper() != bare and len(short) > len(bare):
        return short
    return short or bare


def parse_regime_log(path: Path) -> dict[str, dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, dict] = {}

    # conviction leaders
    m = re.search(
        r"CONVICTION LEADERS TOP 30\n-+\n(.*?)(?:\n\nCROSS-PROFILE|\Z)",
        text,
        re.S,
    )
    conv_re = re.compile(
        r"^\s*(\d+)\s+(\S+:\S+)\s+([\d.]+)\s+(\d+)(long|short|mid)"
    )
    if m:
        for line in m.group(1).splitlines():
            cm = conv_re.match(line)
            if not cm:
                continue
            sym = cm.group(2)
            out.setdefault(sym, {})["conviction_rank"] = int(cm.group(1))
            out[sym]["conviction"] = float(cm.group(3))
            out[sym]["conviction_sleeve"] = f"{cm.group(4)}{cm.group(5)}"

    # cross-profile aligned
    m = re.search(
        r"CROSS-PROFILE ALIGNED TOP 30\n-+\n(.*?)(?:\n\nPLAYBOOK A|\Z)",
        text,
        re.S,
    )
    cross_re = re.compile(
        r"^\s*(\d+)\s+(\S+:\S+)\s+([\d.]+)\s+([\d.]+)(watch|composite|playbook_a)"
    )
    if m:
        for line in m.group(1).splitlines():
            cm = cross_re.match(line)
            if not cm:
                continue
            sym = cm.group(2)
            out.setdefault(sym, {})["cross_profile_rank"] = int(cm.group(1))
            out[sym]["regime_fit"] = float(cm.group(3))
            out[sym]["weeks_ras"] = float(cm.group(4))
            out[sym]["regime_tier"] = cm.group(5)

    # playbook A
    playbook = set()
    m = re.search(r"PLAYBOOK A TACTICAL.*?\n-+\n(.*?)(?:\n\nWARNING|\Z)", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            parts = line.split()
            if len(parts) >= 1 and ":" in parts[0]:
                playbook.add(parts[0])
    for sym in playbook:
        out.setdefault(sym, {})["playbook_a"] = True

    # per-profile ranks (top 10 each)
    for profile in PROFILES:
        pat = rf"PROFILE: {re.escape(profile)}.*?\n#.*?Ticker.*?\n(.*?)(?:\n\nPROFILE:|\nCONVICTION|\Z)"
        m = re.search(pat, text, re.S)
        if not m:
            continue
        prof_re = re.compile(
            r"^\s*(\d+)\s+(\S+:\S+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(watch|composite|playbook_a)"
        )
        for line in m.group(1).splitlines():
            pm = prof_re.match(line)
            if not pm:
                continue
            rank = int(pm.group(1))
            if rank > 10:
                continue
            sym = pm.group(2)
            out.setdefault(sym, {}).setdefault("profile_ranks", {})[profile] = rank
            out[sym].setdefault("profile_scores", {})[profile] = float(pm.group(3))

    return out


def main() -> None:
    name_map = load_name_map()
    regime = parse_regime_log(REGIME_LOG)

    unified = pd.read_csv(EDGE_BASE / "edge_unified_highlights" / "edge_unified_highlights.csv")
    trade = pd.read_csv(EDGE_BASE / "edge_trade_plan" / "edge_trade_plan_ranked.csv")
    upside = pd.read_csv(EDGE_BASE / "upside_opportunity_scan" / "upside_opportunity_candidates.csv")
    hist = pd.read_csv(
        EDGE_BASE / "historical_edge_progression" / "edge_unified_with_history.csv"
    )
    persist = pd.read_csv(EDGE_BASE / "scan_persistence" / "edge_persistence_ranked.csv")

    merged = trade.merge(
        upside[
            [
                "symbol",
                "upside_opportunity_rank",
                "upside_opportunity_score",
                "opportunity_tier",
                "directional_lean",
                "opp_momentum_component",
                "opp_historical_validation_component",
                "opp_valuation_component",
                "opp_safety_quality_component",
                "opp_binary_risk_score",
            ]
        ],
        on="symbol",
        how="left",
        suffixes=("", "_up"),
    )
    merged = merged.merge(
        hist[
            [
                "symbol",
                "hist_anchor_state",
                "hist_days_in_anchor",
                "hist_anchor_first_date",
                "hist_anchor_return_since_entry_pct",
            ]
        ]
        if "hist_anchor_state" in hist.columns
        else hist[["symbol"]],
        on="symbol",
        how="left",
    )
    persist_cols = [
        "symbol",
        "persistence_rank",
        "any_setup_rate",
        "median_fwd_5d_in_setup",
        "win_rate_5d_in_setup",
        "target_rate_5d_in_setup",
    ]
    merged = merged.merge(
        persist[[c for c in persist_cols if c in persist.columns]],
        on="symbol",
        how="left",
    )

    def enrich_row(row: pd.Series) -> dict:
        sym = row["symbol"]
        reg = regime.get(sym, {})
        profiles = reg.get("profile_ranks", {})
        return {
            "symbol": sym,
            "full_name": full_name(sym, str(row.get("company_name", "")), name_map),
            "exchange": sym.split(":")[0] if ":" in sym else "",
            "sector": row.get("sector"),
            "industry": row.get("industry"),
            "entry_state": row.get("entry_state"),
            "trade_plan_rank": row.get("trade_plan_rank"),
            "trade_plan_score": row.get("trade_plan_score"),
            "matched_variants": row.get("matched_entry_variants"),
            "safety_bucket": row.get("safety_bucket"),
            "unified_rank": row.get("unified_edge_highlight_rank"),
            "upside_pred_rank": row.get("upside_prediction_rank"),
            "forward_upside_pct": row.get("forward_valuation_upside_pct"),
            "latest_close": row.get("latest_close"),
            "perf_5d": row.get("perf_5d"),
            "perf_1m": row.get("perf_1m"),
            "perf_3m": row.get("perf_3m"),
            "atrp": row.get("atrp"),
            "extension_atr": row.get("extension_atr_units"),
            "relvol": row.get("relative_volume_10d_calc"),
            "recommend": row.get("recommend_all"),
            "hist_bucket": row.get("historical_validation_bucket"),
            "hist_occurrences": row.get("hist_occurrence_count_in_setup"),
            "median_fwd_5d": row.get("median_fwd_5d_in_setup"),
            "win_rate_5d": row.get("win_rate_5d_in_setup"),
            "persistence_rank": row.get("persistence_rank"),
            "any_setup_rate": row.get("any_setup_rate"),
            "opportunity_tier": row.get("opportunity_tier"),
            "directional_lean": row.get("directional_lean"),
            "upside_opp_score": row.get("upside_opportunity_score"),
            "opp_momentum": row.get("opp_momentum_component"),
            "opp_hist": row.get("opp_historical_validation_component"),
            "opp_valuation": row.get("opp_valuation_component"),
            "opp_safety": row.get("opp_safety_quality_component"),
            "opp_binary_risk": row.get("opp_binary_risk_score"),
            "checks": {
                "evidence_depth": bool(row.get("check_evidence_depth")),
                "historical_support": bool(row.get("check_historical_support")),
                "lane_lb_positive": bool(row.get("check_lane_lower_bound_positive")),
                "safety_not_spec": bool(row.get("check_safety_not_speculative")),
                "valuation_nonneg": bool(row.get("check_valuation_not_negative")),
            },
            "regime": reg,
            "profile_count_top10": len(profiles),
            "profiles_top10": profiles,
        }

    armed = merged[merged["entry_state"] == "ARMED"].sort_values("trade_plan_rank")
    watch = merged[merged["entry_state"] == "WATCH"].sort_values("trade_plan_score", ascending=False)

    # sleeve buckets from move prediction conviction sleeve
    sleeve_buckets: dict[str, list] = {"long": [], "short": [], "mid": [], "other": []}
    for sym, reg in regime.items():
        sleeve = str(reg.get("conviction_sleeve", ""))
        if sleeve.endswith("long"):
            sleeve_buckets["long"].append(sym)
        elif sleeve.endswith("short"):
            sleeve_buckets["short"].append(sym)
        elif sleeve.endswith("mid"):
            sleeve_buckets["mid"].append(sym)

    # priority names: armed + top watch with cross-profile or moderate upside
    priority_syms = set(armed["symbol"].tolist())
    for _, r in watch.head(25).iterrows():
        priority_syms.add(r["symbol"])
    for sym in list(regime.keys())[:30]:
        if regime[sym].get("cross_profile_rank", 99) <= 15:
            priority_syms.add(sym)
    for _, r in upside[upside["opportunity_tier"] == "MODERATE_UPSIDE"].head(15).iterrows():
        priority_syms.add(r["symbol"])

    positions = [enrich_row(merged[merged["symbol"] == s].iloc[0]) for s in priority_syms if s in set(merged["symbol"])]

    # sort: armed first, then by composite signal
    def sort_key(p: dict) -> tuple:
        state_order = {"ARMED": 0, "WATCH": 1, "ENTER_STARTER": -1, "REJECT": 9}
        return (
            state_order.get(p["entry_state"], 5),
            p.get("trade_plan_rank") or 999,
            -(p.get("upside_opp_score") or 0),
        )

    positions.sort(key=sort_key)

    payload = {
        "scan_day": "2026-07-14",
        "regime_label": "Risk-on / high-beta rally",
        "armed_count": len(armed),
        "watch_count": len(watch),
        "moderate_upside_count": int((upside["opportunity_tier"] == "MODERATE_UPSIDE").sum()),
        "armed": [enrich_row(r) for _, r in armed.iterrows()],
        "top_watch": [enrich_row(r) for _, r in watch.head(20).iterrows()],
        "positions": positions,
        "sleeve_leaders": {
            k: [
                {
                    "symbol": s,
                    "full_name": full_name(s, s.split(":")[-1], name_map),
                    **{kk: vv for kk, vv in regime.get(s, {}).items() if kk != "profile_ranks"},
                }
                for s in v[:15]
            ]
            for k, v in sleeve_buckets.items()
            if v
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {OUT_JSON} with {len(positions)} positions, {len(name_map)} names mapped")
    print("ARMED:", [p["symbol"] for p in payload["armed"]])
    print("TOP WATCH:", [p["symbol"] for p in payload["top_watch"][:8]])


if __name__ == "__main__":
    main()
