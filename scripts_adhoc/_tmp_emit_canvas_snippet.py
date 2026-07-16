"""Emit row arrays for canvas from full market JSON."""
import json
from pathlib import Path

d = json.loads(Path(__file__).with_name("_tmp_full_market_rotation_analysis.json").read_text())


def fmt_pct(v, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:+.2f}%" if sign else f"{v:.2f}%"


def fmt_num(v, decimals=2, suffix=""):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{decimals}f}{suffix}"


def safe_str(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return str(v)


def rows_sector(items):
    return [
        [
            r["sector"],
            str(r["industries"]),
            str(int(r.get("names", 0))),
            fmt_pct(r.get("med_1w")),
            fmt_pct(r.get("med_1m")),
            f"{r.get('med_rotation', 0):.2f}",
            str(int(r.get("shortlist_total") or 0)),
        ]
        for r in items[:18]
    ]


def rows_price(items, one_week=True):
    return [
        [
            r.get("industry", ""),
            r.get("sector", "") or "—",
            str(int(r.get("n", 0))),
            fmt_pct(r.get("med_chg_1w") if one_week else r.get("med_chg_1m")),
            fmt_pct(r.get("med_chg_1m") if one_week else r.get("med_chg_1w")),
            f"{int(r.get('pct_up_1w' if one_week else 'pct_up_1m', 0) * 100)}%",
        ]
        for r in items
    ]


def rows_edge(items):
    return [
        [
            r["industry"],
            f"{r['med5_delta']:+.2f}",
            f"{r['med5_latest']:.2f}",
            str(int(r.get("occ_latest", 0))),
            f"{r.get('win5_latest', 0) * 100:.0f}%",
        ]
        for r in items
    ]


def rows_rot(items):
    return [
        [
            str(r.get("industry_rotation_rank", "—")),
            r["industry"],
            r.get("sector", "") or "—",
            f"{r.get('industry_rotation_score', 0):.3f}",
            fmt_pct(r.get("industry_median_perf_1m")),
            (r.get("top_quiet_movers") or "")[:55],
        ]
        for r in items[:28]
    ]


def rows_lane(items):
    return [
        [
            r["industry"],
            str(int(r.get("occurrence_count", 0))),
            f"{r.get('median_fwd_5d', 0):.2f}%",
            f"{r.get('win_rate_5d', 0) * 100:.0f}%",
            f"{r.get('median_fwd_20d', 0):.1f}%",
        ]
        for r in items[:28]
    ]


def rows_opp(items):
    return [
        [
            r["industry"],
            safe_str(r.get("sector")) if r.get("sector") else "—",
            safe_str(r.get("quadrant")),
            fmt_pct(r.get("med_chg_1w")),
            fmt_pct(r.get("med_chg_1m")),
            fmt_num(r.get("med5_delta"), 2),
            fmt_num(r.get("industry_rotation_score"), 2),
            fmt_num(r.get("shortlist_n"), 0),
            safe_str(r.get("top_unified_ticker")),
        ]
        for r in items[:28]
    ]


def rows_quadrant(items):
    return [
        [
            r["industry"],
            safe_str(r.get("sector")) if r.get("sector") else "—",
            fmt_pct(r.get("med_chg_1w")),
            fmt_num(r.get("med5_delta"), 2),
            fmt_num(r.get("industry_rotation_score"), 2),
            fmt_num(r.get("lane_median_fwd_5d"), 2, "%") if r.get("lane_median_fwd_5d") is not None and r.get("lane_median_fwd_5d") == r.get("lane_median_fwd_5d") else "—",
            fmt_num(r.get("shortlist_n"), 0),
            safe_str(r.get("top_unified_ticker")),
        ]
        for r in items
    ]


def setup_detail(ind):
    lines = []
    for s in ind.get("setups", [])[:5]:
        if s["source"] == "unified":
            lines.append(
                f"{s['ticker']}: unified #{s['unified_rank']}, fwd {s['fwd_upside_pct']:+.0f}%, "
                f"safety {s['safety']:.2f}, {s['hist_bucket']}"
            )
        elif s["source"] == "blindspot":
            lines.append(
                f"{s['ticker']}: blindspot quiet {s['quiet_score']:.2f}, 1M {s['perf_1m']:+.1f}%"
            )
        else:
            lines.append(f"{s['ticker']}: screen #{s['screen_rank']}, composite {s['composite']:.2f}")
    return " · ".join(lines)[:120]


def setup_blocks(items, n=35):
    return [
        [
            ind["industry"],
            ind.get("quadrant", ""),
            fmt_pct(ind.get("med_1w")),
            f"{ind.get('rotation_score', 0):.2f}" if ind.get("rotation_score") else "—",
            setup_detail(ind),
        ]
        for ind in items[:n]
    ]


def rows_avoid(items):
    return rows_quadrant(items[:18])


# sector chart top 12 by 1M
sectors = d["sectors"][:12]
sectorChart = {
    "categories": [r["sector"] for r in sectors],
    "series": [
        {
            "name": "Median 1-month price change (%)",
            "data": [round(r["med_1m"], 2) for r in sectors],
        },
        {
            "name": "Median blindspot rotation score",
            "data": [round(r["med_rotation"] * 10, 2) for r in sectors],
            "tone": "info",
        },
    ],
}

out = {
    "quadrant_counts": d["quadrant_counts"],
    "sector_rows": rows_sector(d["sectors"]),
    "price_leaders_1w": rows_price(d["price_leaders_1w"]),
    "price_laggards_1w": rows_price(d["price_laggards_1w"]),
    "price_leaders_1m": rows_price(d["price_leaders_1m"], one_week=False),
    "edge_improvers": rows_edge(d["edge_improvers"]),
    "edge_decliners": rows_edge(d["edge_decliners"]),
    "rotation_rows": rows_rot(d["rotation_all"]),
    "lane_rows": rows_lane(d["lane_leaders_all"]),
    "opportunity_rows": rows_opp(d["top_opportunity_industries"]),
    "early_rotation_rows": rows_quadrant(d["quadrant_early_rotation"]),
    "momentum_rows": rows_quadrant(d["quadrant_momentum"]),
    "avoid_rows": rows_avoid(d["quadrant_avoid"]),
    "setup_rows": setup_blocks(d["industry_setups"], 40),
    "sector_chart": sectorChart,
}

Path(__file__).with_name("_tmp_canvas_rows.json").write_text(json.dumps(out, indent=2))
print("wrote canvas rows")
