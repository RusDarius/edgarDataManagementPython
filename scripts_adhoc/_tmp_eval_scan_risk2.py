import csv
from pathlib import Path

AGG = Path(
    r"logs/tradingview_analysis/edge_research_tools/runs/"
    r"edge_latest_500m_full_parent_20260713_1746_utc_6d8e320a/aggregate"
)


def read(p: Path) -> list[dict[str, str]]:
    with p.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


uni = {r["symbol"]: r for r in read(AGG / "edge_unified_highlights" / "edge_unified_highlights.csv")}
uo = {r["symbol"]: r for r in read(AGG / "upside_opportunity_scan" / "upside_opportunity_candidates.csv")}
tp = {r["symbol"]: r for r in read(AGG / "edge_trade_plan" / "edge_trade_plan_ranked.csv")}

watch = [r for r in tp.values() if r.get("entry_state") == "WATCH"]
scored = []
for row in watch:
    symbol = row["symbol"]
    unified = uni.get(symbol, {})
    safety = float(unified.get("safety_companion_score") or row.get("safety_companion_score") or 0)
    blend = float(
        unified.get("tradeable_safety_blend_score") or row.get("tradeable_safety_blend_score") or 0
    )
    if safety >= 0.65 and blend >= 0.55:
        opp = uo.get(symbol, {})
        scored.append(
            (
                float(row.get("trade_plan_score", 0)),
                symbol,
                safety,
                blend,
                opp.get("directional_lean"),
                opp.get("opportunity_tier"),
                unified.get("safety_bucket"),
            )
        )
scored.sort(reverse=True)
print("WATCH + safety>=0.65 + blend>=0.55:")
for item in scored[:12]:
    print(" ", item[1], "plan", round(item[0], 3), "safety", round(item[2], 2), "blend", round(item[3], 2), "lean", item[4], "tier", item[5], "bucket", item[6])

names = [
    "NASDAQ:ALAB", "NASDAQ:MU", "NASDAQ:PLTR", "NYSE:RDDT", "NASDAQ:ARM", "NASDAQ:RMBS",
    "NYSE:CDE", "OMXCOP:ZEAL", "NYSE:DV", "LSE:JET2", "NYSE:EPAM", "NASDAQ:CART",
    "NASDAQ:MANH", "NYSE:DOCS", "NASDAQ:DUOL", "BET:OPUS", "NASDAQ:PAYO",
]
print("\nKEY NAMES CROSS-LENS:")
for symbol in names:
    unified = uni.get(symbol, {})
    opp = uo.get(symbol, {})
    plan = tp.get(symbol, {})
    print(
        f"{symbol:16} blend={unified.get('tradeable_safety_blend_score', '-'):>6} "
        f"safety={unified.get('safety_companion_score', '-'):>5} "
        f"bucket={str(unified.get('safety_bucket', '-')):8} "
        f"uo={opp.get('upside_opportunity_score', '-'):>6} "
        f"lean={str(opp.get('directional_lean', '-')):18} "
        f"tier={str(opp.get('opportunity_tier', '-')):25} "
        f"plan={plan.get('entry_state', '-')}"
    )
