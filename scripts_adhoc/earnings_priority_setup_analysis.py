"""Ad-hoc: cross-profile earnings-priority setup analysis for a move-prediction scan."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(r"d:\FinanceProjects\edgarDataManagementPython")
SCAN_RUN = "move_prediction_20260710_0929_utc_aa8e469f"
EARNINGS_DIR = (
    ROOT
    / "logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=28/runs"
    / SCAN_RUN
    / "earnings_priority"
)
CONVICTION_CSV = (
    ROOT
    / "logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=28/runs"
    / SCAN_RUN
    / "move_prediction__conviction_rankings.csv"
)
ALL_FIELDS_DB = (
    ROOT
    / "logs/tradingview_analysis/trading_view_all_fields_data/10_07_2026/tradingview_all_fields_10_07_2026.duckdb"
)
BACKWARDS_DB = (
    ROOT
    / "logs/tradingview_analysis/prediction_analysis/duckdb_runs/backwards_prediction_analysis/runs"
    / "backwards_prediction_analysis_20260712_2003_utc_ec9aadd2/backwards_prediction_analysis.duckdb"
)
BACKWARDS_ID = "backwards_prediction_analysis_20260712_2003_utc_ec9aadd2"
REF_DATE = datetime(2026, 7, 12, tzinfo=timezone.utc)

PROFILE_MEANINGS = {
    "breakout_long_v1": "Breakout / expansion tape — price coiling or breaking with momentum; pre-earnings catalyst can extend range break.",
    "early_momentum_inflection_v1": "Early turn — momentum inflecting from base; earnings as confirmation of reversal.",
    "quality_continuation_v1": "Trend continuation in quality names — earnings risk is drift-through, not mean-revert.",
    "sustained_momentum_safety_v1": "Momentum with safety rails — strong tape but filtered for fragility; earnings as trend test.",
    "forward_edge_active_v2": "Active forward edge — event/attention skew; earnings window amplifies positioning.",
    "asymmetric_value": "Asymmetric payoff skew — mispriced upside into catalyst; earnings as valuation re-rating event.",
    "quality_value_compounder": "Quality compounder — steady fundamentals; earnings usually confirm compounding, less gap risk.",
    "durable_value_compounder_v1": "Durable compounder — long-horizon quality; pre-earnings move often muted unless surprise.",
    "value_recovery_v3": "Recovery / rehab — beaten-down names repairing; earnings can unlock rerating or trap.",
    "defensive_fortress_v2": "Defensive fortress — low beta safety; earnings less about gap, more about guidance stability.",
    "mean_reversion_exhaustion_v1": "Exhaustion / fade — stretched moves due to cool; pre-earnings = crowded long risk.",
    "fragility_short": "Fragility / short bias — weak structure; positive earnings surprise is contrarian risk.",
    "consensus": "Cross-profile consensus — multi-lens agreement on direction into earnings.",
}

ROW_RE = re.compile(
    r"^\s*(?P<days>-?\d+\.\d+)\s+(?P<earn>\d{4}-\d{2}-\d{2})\s+(?P<ticker>\S+)\s+(?P<company>.+?)\s{2,}"
    r"(?P<sector>\S+(?:\s+\S+)*?)\s+(?P<mcap>\d+\.\d+[BMK]?)\s+"
    r"(?P<day_score>[+-]?\d+\.\d+)\s+(?P<day_dir>\S+(?:\s+\S+)?)\s+(?P<day_conf>\d+)\s+(?P<day_cov>\d+%)\s+"
    r"(?P<week_score>[+-]?\d+\.\d+)\s+(?P<week_dir>\S+(?:\s+\S+)?)\s+(?P<week_conf>\d+)\s+(?P<week_cov>\d+%)"
)


@dataclass
class EarningsRow:
    profile: str
    days_until: float
    earnings_date: str
    ticker: str
    company: str
    sector: str
    mcap: str
    week_score: float
    week_dir: str
    week_conf: int
    bucket: str = ""


def parse_earnings_logs() -> list[EarningsRow]:
    rows: list[EarningsRow] = []
    for path in sorted(EARNINGS_DIR.glob("tradingview_earnings_priority__*__profile_*.log")):
        if "_flavour_" in path.name:
            continue
        m = re.search(r"profile_(.+)\.log$", path.name)
        if not m:
            continue
        profile = m.group(1)
        text = path.read_text(encoding="utf-8", errors="replace")
        bucket = ""
        for line in text.splitlines():
            if line.startswith("BUCKET —"):
                bucket = line.replace("BUCKET —", "").strip()
                continue
            if not line.strip() or line.startswith("=") or line.startswith("-") or line.startswith("DATE GROUP"):
                continue
            if "Days Earnings" in line or "Methodology" in line or "Universe rows" in line:
                continue
            match = ROW_RE.match(line)
            if not match:
                continue
            d = match.groupdict()
            rows.append(
                EarningsRow(
                    profile=profile,
                    days_until=float(d["days"]),
                    earnings_date=d["earn"],
                    ticker=d["ticker"],
                    company=d["company"].strip(),
                    sector=d["sector"].strip(),
                    mcap=d["mcap"],
                    week_score=float(d["week_score"]),
                    week_dir=d["week_dir"].strip(),
                    week_conf=int(d["week_conf"]),
                    bucket=bucket,
                )
            )
    return rows


def load_conviction() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with CONVICTION_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["symbol"]] = row
    return out


def num(val) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def main() -> None:
    earnings_rows = parse_earnings_logs()
    conviction = load_conviction()

    # Focus: earnings within 14 days from ref date (Jul 12); scan ref was Jul 10
    near = [r for r in earnings_rows if 0 <= r.days_until <= 14]
    by_symbol: dict[str, list[EarningsRow]] = defaultdict(list)
    for r in near:
        by_symbol[r.ticker].append(r)

    symbols = sorted(by_symbol)
    sym_sql = ",".join(f"'{s}'" for s in symbols)

    af = duckdb.connect(str(ALL_FIELDS_DB), read_only=True)
    setup_cols = """
        symbol, name, sector, industry,
        TRY_CAST(earnings_release_next_calendar_date AS DATE) AS earn_cal,
        earnings_release_next_time AS earn_time,
        TRY_CAST(close AS DOUBLE) AS close,
        TRY_CAST("change|1W" AS DOUBLE) AS chg_1w,
        TRY_CAST("change|1M" AS DOUBLE) AS chg_1m,
        TRY_CAST("Perf.3M" AS DOUBLE) AS perf_3m,
        TRY_CAST(price_52_week_high AS DOUBLE) AS hi_52w,
        TRY_CAST(price_52_week_low AS DOUBLE) AS lo_52w,
        TRY_CAST(RSI AS DOUBLE) AS rsi,
        TRY_CAST("RSI|1W" AS DOUBLE) AS rsi_1w,
        TRY_CAST(ATRP AS DOUBLE) AS atrp,
        TRY_CAST("ATRP|1W" AS DOUBLE) AS atrp_1w,
        TRY_CAST(relative_volume_10d_calc AS DOUBLE) AS relvol_10d,
        TRY_CAST("relative_volume_intraday|5" AS DOUBLE) AS relvol_intra,
        TRY_CAST("Volatility.D" AS DOUBLE) AS vol_d,
        TRY_CAST("Volatility.W" AS DOUBLE) AS vol_w,
        TRY_CAST(gap AS DOUBLE) AS gap,
        TRY_CAST(premarket_change AS DOUBLE) AS premarket,
        TRY_CAST(postmarket_change AS DOUBLE) AS postmarket,
        TRY_CAST("Recommend.All" AS DOUBLE) AS rec_all,
        TRY_CAST("Recommend.All|1W" AS DOUBLE) AS rec_1w,
        TRY_CAST(earnings_per_share_forecast_next_fq AS DOUBLE) AS eps_fc,
        TRY_CAST(earnings_per_share_diluted_ttm AS DOUBLE) AS eps_ttm,
        TRY_CAST(total_revenue_yoy_growth_ttm AS DOUBLE) AS rev_yoy,
        TRY_CAST(price_earnings_ttm AS DOUBLE) AS pe_ttm,
        TRY_CAST(market_cap_basic AS DOUBLE) AS mcap_num,
        TRY_CAST(average_volume_10d_calc AS DOUBLE) AS avg_vol_10d,
        TRY_CAST(volume AS DOUBLE) AS volume,
        TRY_CAST("BB.upper" AS DOUBLE) AS bb_upper,
        TRY_CAST("BB.lower" AS DOUBLE) AS bb_lower,
        TRY_CAST(ChaikinMoneyFlow AS DOUBLE) AS cmf,
        TRY_CAST("ChaikinMoneyFlow|1W" AS DOUBLE) AS cmf_1w
    """
    setup_rows = af.execute(
        f"SELECT {setup_cols} FROM all_fields_rows WHERE symbol IN ({sym_sql})"
    ).fetchdf()
    setup = {r["symbol"]: r.to_dict() for _, r in setup_rows.iterrows()}

    bw = duckdb.connect(str(BACKWARDS_DB), read_only=True)
    # Recent anchors: ~1-2 weeks back for component drift into earnings window
    comp_rows = bw.execute(
        f"""
        WITH ranked AS (
          SELECT *,
            ROW_NUMBER() OVER (
              PARTITION BY symbol, profile_name
              ORDER BY CASE anchor_name
                WHEN 'dense_2026_07_03' THEN 1
                WHEN 'dense_2026_07_01' THEN 2
                WHEN 'dense_2026_06_29' THEN 3
                WHEN 'dense_2026_06_26' THEN 4
                WHEN 'dense_2026_06_24' THEN 5
                ELSE 99 END
            ) AS rn
          FROM backwards_profile_component_deltas
          WHERE backwards_analysis_id = '{BACKWARDS_ID}'
            AND symbol IN ({sym_sql})
            AND anchor_name IN (
              'dense_2026_07_03','dense_2026_07_01','dense_2026_06_29',
              'dense_2026_06_26','dense_2026_06_24'
            )
        )
        SELECT symbol, profile_name,
          event_delta, attention_delta, momentum_delta, trend_delta,
          quality_delta, valuation_delta,
          current_event, current_attention, current_momentum, current_trend,
          anchor_name
        FROM ranked WHERE rn = 1
        """
    ).fetchdf()
    comp_by_sym_prof: dict[tuple[str, str], dict] = {}
    for _, r in comp_rows.iterrows():
        comp_by_sym_prof[(r["symbol"], r["profile_name"])] = r.to_dict()

    # Profile family map for backwards join
    prof_family = {
        "breakout_long_v1": "breakout_long",
        "early_momentum_inflection_v1": "early_momentum_inflection",
        "quality_continuation_v1": "quality_continuation",
        "sustained_momentum_safety_v1": "sustained_momentum_safety",
        "forward_edge_active_v2": "forward_edge_active",
        "asymmetric_value": "asymmetric_value",
        "quality_value_compounder": "quality_value_compounder",
        "durable_value_compounder_v1": "durable_value_compounder",
        "value_recovery_v3": "value_recovery",
        "defensive_fortress_v2": "defensive_fortress",
        "mean_reversion_exhaustion_v1": "mean_reversion_exhaustion",
        "fragility_short": "fragility_short",
        "consensus": "consensus",
    }

    def pct_from_high(s: dict) -> float | None:
        c, h = num(s.get("close")), num(s.get("hi_52w"))
        if c and h and h > 0:
            return (c / h - 1) * 100
        return None

    def bb_pos(s: dict) -> str | None:
        c, u, l = num(s.get("close")), num(s.get("bb_upper")), num(s.get("bb_lower"))
        if None in (c, u, l) or u == l:
            return None
        pos = (c - l) / (u - l)
        if pos >= 0.85:
            return "upper_band"
        if pos <= 0.15:
            return "lower_band"
        if pos >= 0.55:
            return "upper_half"
        if pos <= 0.45:
            return "lower_half"
        return "mid"

    # Build per-symbol synthesis
    synth = []
    for sym, prof_rows in by_symbol.items():
        s = setup.get(sym, {})
        conv = conviction.get(sym, {})
        earn_date = prof_rows[0].earnings_date
        days = prof_rows[0].days_until
        # top profiles by week score for this symbol
        top_profs = sorted(prof_rows, key=lambda x: x.week_score, reverse=True)[:5]
        bullish_profs = [p for p in prof_rows if p.week_score >= 0.5 and "Up" in p.week_dir]
        bearish_profs = [p for p in prof_rows if p.week_score <= -0.3 or "Down" in p.week_dir]
        profile_votes = defaultdict(list)
        for p in prof_rows:
            profile_votes[p.profile].append(p.week_score)
        prof_avg = {k: sum(v) / len(v) for k, v in profile_votes.items()}

        # component narrative from backwards (breakout + forward_edge + early_momentum)
        comp_notes = []
        for pname in ["breakout_long", "forward_edge_active", "early_momentum_inflection", "fragility_short"]:
            c = comp_by_sym_prof.get((sym, pname))
            if not c:
                continue
            bits = []
            for k, label in [
                ("event_delta", "event"),
                ("attention_delta", "attention"),
                ("momentum_delta", "momentum"),
                ("trend_delta", "trend"),
            ]:
                v = num(c.get(k))
                if v is not None and abs(v) >= 0.05:
                    bits.append(f"{label}{v:+.2f}")
            if bits:
                comp_notes.append(f"{pname}({c.get('anchor_name','?')}): {', '.join(bits)}")

        hi_pct = pct_from_high(s)
        relvol = num(s.get("relvol_10d")) or num(s.get("relvol_intra"))
        synth.append(
            {
                "symbol": sym,
                "company": prof_rows[0].company,
                "earn_date": earn_date,
                "days": days,
                "bucket": prof_rows[0].bucket,
                "sector": s.get("sector") or prof_rows[0].sector,
                "industry": s.get("industry"),
                "mcap": s.get("mcap_num"),
                "earn_time": s.get("earn_time"),
                "close": num(s.get("close")),
                "chg_1w": num(s.get("chg_1w")),
                "chg_1m": num(s.get("chg_1m")),
                "perf_3m": num(s.get("perf_3m")),
                "hi_52w_pct": hi_pct,
                "rsi": num(s.get("rsi")),
                "rsi_1w": num(s.get("rsi_1w")),
                "atrp_1w": num(s.get("atrp_1w")),
                "relvol": relvol,
                "vol_w": num(s.get("vol_w")),
                "gap": num(s.get("gap")),
                "premarket": num(s.get("premarket")),
                "postmarket": num(s.get("postmarket")),
                "rec_all": num(s.get("rec_all")),
                "rec_1w": num(s.get("rec_1w")),
                "eps_fc": num(s.get("eps_fc")),
                "eps_ttm": num(s.get("eps_ttm")),
                "rev_yoy": num(s.get("rev_yoy")),
                "pe": num(s.get("pe_ttm")),
                "bb_pos": bb_pos(s),
                "cmf_1w": num(s.get("cmf_1w")),
                "bullish_profile_count": int(conv.get("bullish_profile_count") or 0),
                "tape_pass": conv.get("tape_pass"),
                "entry_readiness": conv.get("entry_readiness"),
                "breakout_tier": conv.get("breakout_conviction_tier"),
                "manager_action": conv.get("manager_action_signal"),
                "conviction": num(conv.get("conviction_score")),
                "top_profiles": top_profs,
                "bullish_profs": bullish_profs,
                "bearish_profs": bearish_profs,
                "prof_avg": prof_avg,
                "comp_notes": comp_notes,
                "profile_count": len(prof_rows),
            }
        )

    synth.sort(key=lambda x: (x["days"], -(x["conviction"] or 0)))

    # OUTPUT
    print("=" * 100)
    print(f"EARNINGS PRIORITY SETUP ANALYSIS | scan={SCAN_RUN} | ref={REF_DATE.date()} | all_fields=10_07_2026")
    print(f"Symbols with earnings <=14d across profiles: {len(synth)}")
    print("=" * 100)

    # Group by earnings date
    by_date: dict[str, list] = defaultdict(list)
    for item in synth:
        by_date[item["earn_date"]].append(item)

    for earn_date in sorted(by_date):
        items = by_date[earn_date]
        print(f"\n{'#'*100}")
        print(f"EARNINGS DATE: {earn_date} ({len(items)} names in cross-profile near-term set)")
        print(f"{'#'*100}")

        # subgroup: multi-lens agreement longs
        strong = [i for i in items if len(i["bullish_profs"]) >= 4 and (i["tape_pass"] == "True" or i["bullish_profile_count"] >= 8)]
        watch_fade = [i for i in items if len(i["bearish_profs"]) >= 3 or (i.get("prof_avg", {}).get("fragility_short", 0) <= -0.4)]
        event_build = [i for i in items if i["comp_notes"] and any("event+" in n for n in i["comp_notes"])]
        vol_setup = [i for i in items if (i["relvol"] or 0) >= 1.3 or (i["atrp_1w"] or 0) >= 8]
        coiled = [i for i in items if i["hi_52w_pct"] is not None and i["hi_52w_pct"] > -8 and (i["chg_1w"] or 0) < 3 and (i["rsi"] or 50) < 65]

        def fmt_item(i: dict, detail: str = "full") -> str:
            tops = ", ".join(f"{p.profile.split('_')[0]}:{p.week_score:+.2f}/{p.week_dir}" for p in i["top_profiles"][:4])
            tape = []
            if i["relvol"]:
                tape.append(f"RelVol10d={i['relvol']:.2f}")
            if i["atrp_1w"]:
                tape.append(f"ATRP1W={i['atrp_1w']:.1f}%")
            if i["hi_52w_pct"] is not None:
                tape.append(f"vs52wH={i['hi_52w_pct']:+.1f}%")
            if i["chg_1w"] is not None:
                tape.append(f"1W={i['chg_1w']:+.1f}%")
            if i["chg_1m"] is not None:
                tape.append(f"1M={i['chg_1m']:+.1f}%")
            if i["rsi"] is not None:
                tape.append(f"RSI={i['rsi']:.0f}")
            if i["bb_pos"]:
                tape.append(f"BB={i['bb_pos']}")
            if i["earn_time"]:
                tape.append(f"time={i['earn_time']}")
            if i["gap"]:
                tape.append(f"gap={i['gap']:+.1f}%")
            fund = []
            if i["rev_yoy"] is not None:
                fund.append(f"RevYoY={i['rev_yoy']:+.1f}%")
            if i["pe"]:
                fund.append(f"PE={i['pe']:.1f}")
            if i["rec_all"] is not None:
                fund.append(f"Rec={i['rec_all']:.2f}")
            conv = []
            if i["conviction"]:
                conv.append(f"conv={i['conviction']:.2f}")
            if i["bullish_profile_count"]:
                conv.append(f"bull_profiles={i['bullish_profile_count']}/12")
            if i["breakout_tier"]:
                conv.append(f"tier={i['breakout_tier']}")
            if i["entry_readiness"]:
                conv.append(f"entry={i['entry_readiness']}")
            if i["manager_action"]:
                conv.append(f"action={i['manager_action']}")
            comp = " | ".join(i["comp_notes"][:2]) if i["comp_notes"] else ""
            mcap_s = f"{i['mcap']/1e9:.2f}B" if i.get("mcap") else "?"
            line = (
                f"  {i['symbol']} — {i['company']} | {i['sector']}/{i.get('industry','')} | MCap {mcap_s} | "
                f"earn in {i['days']:.1f}d\n"
                f"    TAPE: {'; '.join(tape)}\n"
                f"    PROFILES(top): {tops}\n"
                f"    CONVICTION: {'; '.join(conv)}\n"
            )
            if fund:
                line += f"    FUND/ANALYST: {'; '.join(fund)}\n"
            if comp:
                line += f"    BACKWARDS COMPONENTS (1-2w): {comp}\n"
            return line

        if strong:
            print("\n  [MULTI-LENS BULLISH + TAPE OK] — profiles agree up into print; look for continuation/coiling setups")
            for i in sorted(strong, key=lambda x: -(x["conviction"] or 0))[:25]:
                print(fmt_item(i))

        if event_build:
            print("\n  [EVENT/ATTENTION BUILDING] — backwards component deltas show rising event skew pre-earnings")
            shown = set()
            for i in sorted(event_build, key=lambda x: -len(x["comp_notes"]))[:20]:
                if i["symbol"] in shown:
                    continue
                shown.add(i["symbol"])
                print(fmt_item(i))

        if vol_setup:
            print("\n  [VOLATILITY / VOLUME EXPANSION] — elevated ATR or relative volume; gap-risk both ways")
            for i in sorted(vol_setup, key=lambda x: -(x["relvol"] or 0))[:20]:
                print(fmt_item(i))

        if coiled:
            print("\n  [COILED NEAR HIGHS] — within ~8% of 52w high, not extended weekly; classic pre-earnings drift/break")
            for i in sorted(coiled, key=lambda x: x["hi_52w_pct"] or -999, reverse=True)[:20]:
                print(fmt_item(i))

        if watch_fade:
            print("\n  [FRAGILITY / EXHAUSTION FLAGS] — short-bias or down profiles into earnings; fade or hedge")
            for i in sorted(watch_fade, key=lambda x: len(x["bearish_profs"]), reverse=True)[:20]:
                print(fmt_item(i))

        # Remaining names grouped by dominant profile lens
        covered = set(i["symbol"] for i in strong + event_build + vol_setup + coiled + watch_fade)
        remaining = [i for i in items if i["symbol"] not in covered]
        if remaining:
            print("\n  [OTHER NAMES BY DOMINANT PROFILE LENS]")
            by_dom: dict[str, list] = defaultdict(list)
            for i in remaining:
                dom = i["top_profiles"][0].profile if i["top_profiles"] else "unknown"
                by_dom[dom].append(i)
            for prof in sorted(by_dom, key=lambda p: -len(by_dom[p])):
                meaning = PROFILE_MEANINGS.get(prof, "")
                print(f"\n    Profile lens: {prof}")
                if meaning:
                    print(f"    -> {meaning}")
                for i in sorted(by_dom[prof], key=lambda x: -(x["top_profiles"][0].week_score if x["top_profiles"] else 0))[:12]:
                    print(fmt_item(i))

    # Cross-date profile translation summary
    print(f"\n\n{'='*100}")
    print("PROFILE LENS TRANSLATION (what each profile is saying about near-term earnings cohort)")
    print("=" * 100)
    for prof in sorted(PROFILE_MEANINGS):
        prof_rows = [r for r in near if r.profile == prof and 0 <= r.days_until <= 14]
        if not prof_rows:
            continue
        top = sorted(prof_rows, key=lambda x: x.week_score, reverse=True)[:8]
        up = sum(1 for r in prof_rows if r.week_score >= 0.5)
        down = sum(1 for r in prof_rows if r.week_score <= -0.3)
        print(f"\n{prof}:")
        print(f"  Meaning: {PROFILE_MEANINGS[prof]}")
        print(f"  Near-term cohort: {len(prof_rows)} names | week_up(>=0.5): {up} | week_down(<=-0.3): {down}")
        print("  Top week-score into earnings:")
        for r in top:
            print(f"    {r.earnings_date} {r.ticker} {r.company[:40]:40s} week={r.week_score:+.2f} {r.week_dir}")


if __name__ == "__main__":
    main()
