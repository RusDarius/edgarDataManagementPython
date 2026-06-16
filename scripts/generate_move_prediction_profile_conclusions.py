#!/usr/bin/env python3
"""Generate per-profile conclusions for a move-prediction DuckDB run.

Cross-references profile leaders with whole-period indicator conclusions
(predictor_stock_rankings from scan_period_close_forward_tracking_25may_12jun2026).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_RUN_ID = "move_prediction_20260615_1659_utc_6cad86f1"
DEFAULT_MP_DB = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=25"
    / "move_prediction_2026_W25.duckdb"
)
DEFAULT_RUN_DIR = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/prediction_analysis/duckdb_runs/iso_year=2026/week=25/runs"
    / DEFAULT_RUN_ID
)
DEFAULT_RANK_DIR = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/runs"
    / "scan_period_close_forward_tracking_25may_12jun2026_dd898100/predictor_stock_rankings"
)
DEFAULT_DAILY_DB = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/trading_view_all_fields_data/12_06_2026"
    / "tradingview_all_fields_12_06_2026.duckdb"
)
DEFAULT_DAILY_RUN_ID = "tradingview_all_fields_20260612_2008_utc_028e0f86"
ADVISED_CONCLUSIONS = (
    PROJECT_ROOT
    / "logs/tradingview_analysis/trading_view_all_fields_data/pattern_analysis/advised_conclusions"
    / "close_forward_25may_12jun2026_dd898100_advised_conclusions.md"
)
PROFILE_CONFIG_DIR = PROJECT_ROOT / "config/move_prediction_profiles/profiles"

# Fallback descriptions for profiles not stored as JSON config files.
PROFILE_DESCRIPTION_FALLBACK: dict[str, str] = {
    "quality_value_compounder": (
        "Durable quality identification profile. Surfaces companies with persistent "
        "profitability, capital efficiency, and margin stability. Rewards operational "
        "excellence; short-term momentum is muted so quality ranking is not polluted "
        "by recent price moves."
    ),
    "asymmetric_value": (
        "Overlooked stock profile where fundamentals or industry-relative economics "
        "are better than price implies. Quality and valuation are co-primary, with "
        "low-attention and industry revenue-value gap signals. Safety filters value traps."
    ),
    "deep_value_momentum": (
        "Deep value with early price recovery confirmation. Rewards cheap valuation "
        "when tactical momentum shows repair — unlike pure value, requires tape confirmation."
    ),
    "fragility_short": (
        "Hedging and short opportunity profile identifying structurally fragile names. "
        "INVERTED scoring: negative event pressure, deteriorating momentum, and weak "
        "safety are rewarded. Answers 'what is most likely to break?'"
    ),
}


def _load_profile_descriptions() -> dict[str, str]:
    descriptions = dict(PROFILE_DESCRIPTION_FALLBACK)
    if PROFILE_CONFIG_DIR.exists():
        for path in PROFILE_CONFIG_DIR.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                profile = payload.get("profile", {})
                name = profile.get("name") or path.stem
                desc = profile.get("description")
                if desc:
                    descriptions[name] = desc
            except (json.JSONDecodeError, OSError):
                continue
    return descriptions

INVERTED_PROFILES = frozenset({"fragility_short", "mean_reversion_exhaustion_v1"})

PROFILE_STYLE: dict[str, tuple[str, str, str]] = {
    "breakout_long_v1": (
        "Continuation / breakout long",
        "Playbook A — vol expansion + tape confirmation",
        "1–4 weeks",
    ),
    "early_momentum_inflection_v1": (
        "Early momentum inflection",
        "Playbook A entry timing; avoid monthly oversold traps",
        "days–2 weeks",
    ),
    "forward_edge_active_v1": (
        "Active manager forward edge",
        "Blend Playbook A + B; catalyst-aware",
        "1–3 weeks",
    ),
    "quality_value_compounder": (
        "Quality value compounder",
        "Playbook B drift; avoid mega-cap headwind",
        "months+",
    ),
    "durable_value_compounder_v1": (
        "Durable compounder hold",
        "Playbook B; quality drift, low turnover",
        "quarters",
    ),
    "sector_relative_outperformer_v1": (
        "Sector-relative outperformer",
        "Sector rotation + relative strength",
        "2–8 weeks",
    ),
    "asymmetric_value": (
        "Asymmetric value / catalyst",
        "Value floor + event; filter trim list",
        "weeks–months",
    ),
    "value_recovery_v1": (
        "Value recovery / turnaround",
        "Contrarian recovery; NOT monthly oversold alone",
        "months",
    ),
    "deep_value_momentum": (
        "Deep value + momentum",
        "Cheap + tape repair; Playbook A overlay",
        "weeks",
    ),
    "fragility_short": (
        "Fragility / short watchlist",
        "Inverted lens — structural weakness candidates",
        "days–weeks (short bias)",
    ),
    "income_compounder_v1": (
        "Income compounder",
        "Defensive yield + quality; low beta tilt",
        "months+",
    ),
    "pre_earnings_drift_v1": (
        "Pre-earnings drift",
        "Event drift; vol confirmation pre-print",
        "days–2 weeks",
    ),
    "mean_reversion_exhaustion_v1": (
        "Mean-reversion exhaustion (trim)",
        "Inverted — extended names to trim / avoid adding",
        "days–weeks",
    ),
    "defensive_fortress_v1": (
        "Defensive fortress",
        "Safety-first; hedge book / ballast",
        "months+",
    ),
    "sector_rotation_momentum_v1": (
        "Sector rotation momentum",
        "Playbook A sector tape; cyclical/industrial cluster",
        "2–6 weeks",
    ),
    "quality_growth_at_reasonable_price_v1": (
        "GARP",
        "Growth at reasonable price; Playbook B hybrid",
        "months",
    ),
    "swing_reversal_v1": (
        "Swing reversal (oversold repair)",
        "Repair confirmation required — raw monthly oversold is a 3w headwind",
        "days–3 weeks",
    ),
}

INDICATOR_CONTEXT = """
### Whole-period indicator regime (26 May – 12 Jun 2026, period-total lens)

| Theme | Fields | Spread | Consistency | Long-only use |
|-------|--------|--------|-------------|---------------|
| Vol expansion | ATRP, ATRP\\|1W, ADRP | +0.5–0.7 pp | ~69% | **Primary tilt** (Playbook A) |
| ADX −DI pressure absorbed | ADX-DI\\|1M | +0.4–0.56 pp | 62–69% | Confirmation filter |
| Monthly oversold | Stoch.K\\|1M, RSI21\\|1M, W.R\\|1M | −0.69–0.77 pp | ~69% | **Avoid** as entry (Playbook C) |
| Bullish TV MA consensus | Recommend.MA\\|1M | −0.58 pp | 77% | Crowded / trim risk |
| Mega-cap size | oper_income_ttm, ebitda_ttm | −0.41–0.50 pp | ~77% | Prefer smaller within ≥$1B band |

**Tier 1 drift examples (Playbook B):** CORT, CBRL, ARCB, MEC, EPC, ELVN (event day).  
**Tier 3 trim examples:** FLY, OCS, LUNR, WOLF, OLMA, OMER, ASTS, RKLB.
"""


def _load_csv_map(path: Path, key: str = "symbol") -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {row[key]: row for row in csv.DictReader(handle)}


def _first_existing(path_candidates: list[Path]) -> Path:
    for candidate in path_candidates:
        if candidate.exists():
            return candidate
    return path_candidates[0]


def _fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _classify_symbol(
    sym: str,
    *,
    trim: dict[str, dict[str, str]],
    warn: dict[str, dict[str, str]],
    pb_a: dict[str, dict[str, str]],
    pb_b: dict[str, dict[str, str]],
    active: dict[str, dict[str, str]],
    composite: dict[str, dict[str, str]],
) -> tuple[list[str], list[str]]:
    flags: list[str] = []
    playbooks: list[str] = []
    if sym in trim:
        flags.append(
            f"TRIM LIST: avg period return {trim[sym].get('avg_period_ret_pct', '?')}% "
            f"over {trim[sym].get('n_days', '?')} symbols"
        )
    if sym in warn:
        flags.append(f"WARNING OVERLAY hit (avoid rank {warn[sym].get('rank', '?')})")
    if sym in pb_a:
        playbooks.append("Playbook A — vol continuation fit")
    if sym in pb_b:
        playbooks.append(
            f"Playbook B — quality drift (hist avg period return {pb_b[sym].get('avg_period_ret_pct', '?')}%)"
        )
    if sym in active:
        playbooks.append(f"Active mgmt tier: {active[sym].get('active_mgmt_tier', '?')}")
    if sym in composite:
        playbooks.append(f"Composite bullish fit rank #{composite[sym].get('rank', '?')}")
    return flags, playbooks


def _indicator_flags(live: tuple) -> list[str]:
    atrp_1w, relvol, adx_di5, stoch_1m, wr_1m, rec_ma_1m, oper_inc = live
    notes: list[str] = []
    if atrp_1w is not None and atrp_1w > 12:
        notes.append(f"elevated ATRP|1W ({_fmt_num(atrp_1w, 1)})")
    if relvol is not None and relvol > 1.5:
        notes.append(f"rel volume spike ({_fmt_num(relvol, 2)})")
    if adx_di5 is not None and adx_di5 > 30:
        notes.append(f"ADX-DI|5 elevated ({_fmt_num(adx_di5, 1)}) — pressure-absorption context")
    if stoch_1m is not None and stoch_1m < 20:
        notes.append(f"monthly Stoch oversold ({_fmt_num(stoch_1m, 1)}) — 3w study headwind for fresh longs")
    if wr_1m is not None and wr_1m < -80:
        notes.append(f"monthly W.R deeply oversold ({_fmt_num(wr_1m, 1)}) — Playbook C filter")
    if rec_ma_1m is not None and rec_ma_1m > 0.5:
        notes.append(f"bullish Recommend.MA|1M ({_fmt_num(rec_ma_1m, 2)}) — crowded consensus risk")
    if oper_inc is not None and oper_inc > 5e9:
        notes.append("large oper_income_ttm — mega-cap headwind in 3w study")
    return notes


def _profile_conclusion(
    profile: str,
    stats: dict[str, int],
    sector_counts: dict[str, int],
    top_symbols: list[str],
    inverted: bool,
) -> str:
    style, playbook, horizon = PROFILE_STYLE.get(profile, ("", "", ""))
    lines: list[str] = []

    if inverted:
        lines.append(
            f"This is an **inverted** profile in consensus — high scores flag names to **watch for weakness, "
            f"trim, or short bias**, not to add long exposure blindly."
        )

    pb_a = stats["pb_a_hits_top30"]
    pb_b = stats["pb_b_hits_top30"]
    trim_h = stats["trim_hits_top30"]
    warn_h = stats["warn_hits_top30"]

    if profile in {
        "breakout_long_v1",
        "sector_rotation_momentum_v1",
        "early_momentum_inflection_v1",
    }:
        lines.append(
            f"**Tape alignment:** {pb_a}/30 top names match **Playbook A** (ATRP/volume continuation). "
            f"The semi/storage complex (SNDK, WDC, MU, AMAT, LRCX) dominates — consistent with **vol expansion + "
            f"sector rotation** themes from the 3-week study."
        )
        if trim_h:
            lines.append(
                f"**Caution:** {trim_h} top-30 names also appear on the **persistent negative drift trim list** "
                f"(e.g. high-ATRP losers) — verify symbol-level history before sizing."
            )
    elif profile in {"quality_value_compounder", "durable_value_compounder_v1", "income_compounder_v1"}:
        lines.append(
            f"**Drift alignment:** {pb_b}/30 leaders overlap **Playbook B** quality-drift names. "
            f"Favor names with **win rate ≥ 60%** in the 3w window; de-prioritize mega-cap fundamental size flags."
        )
        lines.append(
            "Recurring leaders **ZEAL, AUPH, INCY, WDO** skew **healthcare / precious metals / defensive quality** — "
            "lower beta ballast rather than vol-breakout chase."
        )
    elif profile == "swing_reversal_v1":
        lines.append(
            "**Critical tension with whole-period indicators:** Monthly oversold oscillators were **reliably negative** "
            "for period-total returns (−0.7 pp). This profile's top picks (SNDK, SEZL, AMAT) are **momentum leaders**, "
            "not classical oversold reversals — treat as **repair-after-depression** only when ATRP + volume confirm, "
            "not as blind oversold buys."
        )
    elif profile == "mean_reversion_exhaustion_v1":
        lines.append(
            f"Top names (SNDK, MU, CRDO) are **extended momentum leaders** — exactly the cohort Playbook A "
            f"would add but Playbook C would flag if monthly oscillators are crowded bullish. "
            f"Use this profile to **trim/add hedge**, not to initiate fresh longs."
        )
    elif profile == "fragility_short":
        lines.append(
            "Despite short bias, several top-scored names are **current breakout leaders** (SNDK, MU, WDC). "
            "For long-only books, read this as **fragility watchlist** — high beta, event-heavy semi cycle; "
            "pair with trim list and warning overlay before holding through earnings."
        )
    elif profile == "value_recovery_v1":
        lines.append(
            f"Leaders skew **precious metals / deep value repair** (AFM, NEM, AU, WDO). "
            f"Aligns with **commodity/value recovery** thesis; cross-check Playbook C — avoid if only monthly oversold "
            f"without operating repair."
        )
    elif profile == "pre_earnings_drift_v1":
        lines.append(
            "Earnings-proximate drift names cluster in **semi / fintech / industrials**. "
            "Combine with **elevated ATRP** filter from 3w study; names without volume confirmation are event gambles."
        )
    elif profile == "defensive_fortress_v1":
        lines.append(
            "Defensive screen favors **biotech quality (INCY), SaaS ballast (ZM), gold miners (FRES)**. "
            "Low overlap with Playbook A — appropriate **portfolio hedge / lower-turnover sleeve**."
        )
    else:
        lines.append(
            f"Playbook overlap in top 30: **A={pb_a}**, **B={pb_b}**, **trim hits={trim_h}**, **warning hits={warn_h}**."
        )

    if warn_h >= 3:
        lines.append(
            f"**{warn_h}** top-30 names hit the **Playbook C warning overlay** — apply extra scrutiny before entry."
        )

    top_sectors = ", ".join(f"{s} ({n})" for s, n in list(sector_counts.items())[:4])
    if top_sectors:
        lines.append(f"**Sector concentration (top 30):** {top_sectors}.")

    if style:
        lines.append(f"**Investment style:** {style} | **Horizon:** {horizon} | **Indicator playbook:** {playbook}.")

    return "\n\n".join(lines)


def generate(
    *,
    run_id: str,
    mp_db: Path,
    run_dir: Path,
    rank_dir: Path,
    daily_db: Path,
    daily_run_id: str,
) -> list[Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    out_dir = run_dir / "profile_conclusions"
    out_dir.mkdir(parents=True, exist_ok=True)

    indicator_perf_dir = rank_dir / "indicator_perf"
    top_stable_indicator_file = _first_existing(
        [
            indicator_perf_dir / "00_top_stable_period_return_pct_predictors.csv",
            indicator_perf_dir / "00_top_stable_close_forward_predictors.csv",
        ]
    )

    composite = _load_csv_map(rank_dir / "01_composite_bullish_fit_top100.csv")
    active = _load_csv_map(rank_dir / "07_active_management_candidates_top100.csv")
    trim = _load_csv_map(rank_dir / "04_trim_negative_drift_top100.csv")
    warn = _load_csv_map(rank_dir / "05_warning_overlay_avoid_top100.csv")
    pb_a = _load_csv_map(rank_dir / "02_playbook_a_volatility_continuation_top100.csv")
    pb_b = _load_csv_map(rank_dir / "03_playbook_b_quality_drift_top100.csv")

    con = duckdb.connect(str(mp_db), read_only=True)
    con.execute(f"ATTACH '{daily_db.as_posix()}' AS day (READ_ONLY)")

    profiles = [
        row[0]
        for row in con.execute(
            """
            SELECT DISTINCT profile_name
            FROM profile_prediction_rows
            WHERE run_id = ?
            ORDER BY profile_name
            """,
            [run_id],
        ).fetchall()
    ]

    descs: dict[str, str] = _load_profile_descriptions()
    for pname, cfg in con.execute(
        "SELECT profile_name, profile_config_json FROM profile_config_snapshots WHERE run_id = ?",
        [run_id],
    ).fetchall():
        try:
            parsed = json.loads(cfg)
            if parsed.get("description"):
                descs[pname] = parsed["description"]
        except json.JSONDecodeError:
            pass

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    index_lines = [
        "# Per-profile conclusions — move prediction run",
        "",
        f"**Run ID:** `{run_id}`  ",
        f"**Generated:** {generated_at}  ",
        f"**Universe:** all industries, market cap ≥ $1B  ",
        f"**Cross-reference:** `close_forward_25may_12jun2026_dd898100` (whole-period close return study)  ",
        f"**Rankings source:** `{rank_dir.relative_to(PROJECT_ROOT).as_posix()}`  ",
        "",
        "This pack provides **separate outlooks per investment profile** — no composite weighting. "
        "Each file maps profile leaders to the whole-period indicator playbooks (A/B/C).",
        "",
        INDICATOR_CONTEXT,
        "",
        "## Profile index",
        "",
        "| Profile | Style | Top leader | PB-A hits /30 | PB-B hits /30 | Trim hits /30 | File |",
        "|---------|-------|------------|---------------|---------------|---------------|------|",
    ]

    per_profile_paths: list[Path] = []
    all_stats: dict[str, dict] = {}

    for profile in profiles:
        top_rows = con.execute(
            """
            SELECT symbol, Company, sector, industry, market, close, market_cap_basic,
                   weeks_score, weeks_direction, weeks_risk_adjusted_score, weeks_risk_tier,
                   days_score, days_risk_adjusted_score,
                   months_score, months_risk_adjusted_score,
                   manager_action_signal,
                   component_attention, component_momentum, component_trend,
                   component_quality, component_valuation, component_safety,
                   beta_adjusted_atrp, float_turnover, earnings_days_to_next
            FROM profile_prediction_rows
            WHERE run_id = ? AND profile_name = ?
            ORDER BY weeks_risk_adjusted_score DESC NULLS LAST
            LIMIT 30
            """,
            [run_id, profile],
        ).fetchall()

        sector_counts: dict[str, int] = defaultdict(int)
        stats = {
            "actionable_signals": 0,
            "trim_hits_top30": 0,
            "warn_hits_top30": 0,
            "pb_a_hits_top30": 0,
            "pb_b_hits_top30": 0,
            "active_hits_top30": 0,
        }
        enriched: list[tuple] = []

        for row in top_rows:
            sym = row[0]
            sector_counts[row[2] or "Unknown"] += 1
            flags, playbooks = _classify_symbol(
                sym,
                trim=trim,
                warn=warn,
                pb_a=pb_a,
                pb_b=pb_b,
                active=active,
                composite=composite,
            )
            if any("TRIM LIST" in flag for flag in flags):
                stats["trim_hits_top30"] += 1
            if any("WARNING OVERLAY" in flag for flag in flags):
                stats["warn_hits_top30"] += 1
            if any("Playbook A" in pb for pb in playbooks):
                stats["pb_a_hits_top30"] += 1
            if any("Playbook B" in pb for pb in playbooks):
                stats["pb_b_hits_top30"] += 1
            if any("Active mgmt" in pb for pb in playbooks):
                stats["active_hits_top30"] += 1
            signal = str(row[15] or "")
            if "add" in signal.lower() or "accumulate" in signal.lower():
                stats["actionable_signals"] += 1
            enriched.append((row, flags, playbooks))

        syms = [item[0][0] for item in enriched[:20]]
        live_map: dict[str, tuple] = {}
        if syms:
            placeholders = ", ".join("?" for _ in syms)
            for live_row in con.execute(
                f"""
                SELECT symbol,
                       TRY_CAST("ATRP|1W" AS DOUBLE),
                       TRY_CAST(relative_volume AS DOUBLE),
                       TRY_CAST("ADX-DI|5" AS DOUBLE),
                       TRY_CAST("Stoch.K_14_1_3|1M" AS DOUBLE),
                       TRY_CAST("W.R|1M" AS DOUBLE),
                       TRY_CAST("Recommend.MA|1M" AS DOUBLE),
                       TRY_CAST(oper_income_ttm AS DOUBLE)
                FROM day.all_fields_rows
                WHERE run_id = ? AND symbol IN ({placeholders})
                """,
                [daily_run_id, *syms],
            ).fetchall():
                live_map[live_row[0]] = live_row[1:]

        style, playbook, horizon = PROFILE_STYLE.get(profile, ("", "", ""))
        inverted = profile in INVERTED_PROFILES
        conclusion = _profile_conclusion(
            profile,
            stats,
            dict(sorted(sector_counts.items(), key=lambda item: -item[1])),
            [item[0][0] for item in enriched[:5]],
            inverted,
        )

        path = out_dir / f"{profile}_conclusions.md"
        lines = [
            f"# {profile} — conclusions",
            "",
            f"**Run:** `{run_id}` | **Generated:** {generated_at}",
            "",
            "## Profile lens",
            "",
            descs.get(profile) or "_No description snapshot in run._",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Investment style | {style or '—'} |",
            f"| Typical horizon | {horizon or '—'} |",
            f"| Indicator playbook mapping | {playbook or '—'} |",
            f"| Consensus treatment | {'**Inverted** (short/trim bias)' if inverted else 'Long-bias'} |",
            "",
            "## Conclusion",
            "",
            conclusion,
            "",
            "## Cross-reference statistics (top 30)",
            "",
            f"| Metric | Count |",
            f"|--------|------:|",
            f"| Actionable manager signals (add/accumulate) | {stats['actionable_signals']} |",
            f"| Playbook A (vol continuation) overlap | {stats['pb_a_hits_top30']} |",
            f"| Playbook B (quality drift) overlap | {stats['pb_b_hits_top30']} |",
            f"| Active management candidates overlap | {stats['active_hits_top30']} |",
            f"| Persistent negative drift (trim list) | {stats['trim_hits_top30']} |",
            f"| Playbook C warning overlay | {stats['warn_hits_top30']} |",
            "",
            "## Top 20 leaders with indicator correlation",
            "",
            "| Rank | Symbol | Company | Sector | Wks RAS | Signal | 3w flags / playbooks | Live indicators (12 Jun) |",
            "|-----:|--------|---------|--------|--------:|--------|----------------------|--------------------------|",
        ]

        for rank, (row, flags, playbooks) in enumerate(enriched[:20], start=1):
            sym = row[0]
            live = live_map.get(sym)
            ind_notes = _indicator_flags(live) if live else []
            cross = flags + playbooks
            cross_txt = "; ".join(cross) if cross else "—"
            ind_txt = "; ".join(ind_notes) if ind_notes else "—"
            mcap_b = (row[6] or 0) / 1e9
            lines.append(
                f"| {rank} | {sym} | {row[1] or ''} | {row[2] or ''} | "
                f"{_fmt_num(row[9])} | {row[15] or '—'} | {cross_txt} | {ind_txt} |"
            )

        lines.extend(
            [
                "",
                "## Sector concentration (top 30)",
                "",
            ]
        )
        for sector, count in sorted(sector_counts.items(), key=lambda item: -item[1])[:10]:
            lines.append(f"- **{sector}:** {count}")

        lines.extend(
            [
                "",
                "## Actionable subsets",
                "",
            ]
        )

        # Tier: high conviction (no trim, no warning, has playbook)
        high_conv = []
        watch = []
        reject = []
        for row, flags, playbooks in enriched:
            sym = row[0]
            is_trim = any("TRIM LIST" in f for f in flags)
            is_warn = any("WARNING OVERLAY" in f for f in flags)
            has_pb = bool(playbooks)
            if is_trim or is_warn:
                reject.append((row, flags, playbooks))
            elif has_pb and not inverted:
                high_conv.append((row, flags, playbooks))
            else:
                watch.append((row, flags, playbooks))

        if inverted:
            lines.append("### Elevated fragility / trim candidates (profile top 15)")
            for row, flags, playbooks in enriched[:15]:
                lines.append(f"- **{row[0]}** — {row[1]} | signal: {row[15]}")
        else:
            lines.append("### Start / add candidates (Playbook-aligned, no trim/warning)")
            if high_conv:
                for row, flags, playbooks in high_conv[:12]:
                    pb_txt = ", ".join(playbooks)
                    lines.append(
                        f"- **{row[0]}** ({row[2]}) — weeks RAS {_fmt_num(row[9])}, "
                        f"{row[15]} | {pb_txt}"
                    )
            else:
                lines.append("_None cleanly pass all filters in top 30 — use watchlist._")

            lines.append("")
            lines.append("### Watch / confirm (missing playbook or mixed signals)")
            for row, flags, playbooks in watch[:8]:
                lines.append(f"- **{row[0]}** — weeks RAS {_fmt_num(row[9])}, signal {row[15]}")

            lines.append("")
            lines.append("### Reject / trim overlay (3w negative drift or warning flags)")
            if reject:
                for row, flags, playbooks in reject[:10]:
                    flag_txt = "; ".join(flags)
                    lines.append(f"- **{row[0]}** — {flag_txt}")
            else:
                lines.append("_No trim-list or warning-overlay hits in top 30._")

        lines.extend(
            [
                "",
                "## References",
                "",
                f"- Advised conclusions: `{ADVISED_CONCLUSIONS.relative_to(PROJECT_ROOT).as_posix()}`",
                f"- Predictor rankings: `{rank_dir.relative_to(PROJECT_ROOT).as_posix()}`",
                f"- Profile log: `../tradingview_move_prediction__all_industries__min_1000000000__max_none__profile_{profile}.log`",
                "",
                "_Interpretive layer on pipeline outputs; not investment advice._",
            ]
        )

        path.write_text("\n".join(lines), encoding="utf-8")
        per_profile_paths.append(path)
        all_stats[profile] = stats

        leader = enriched[0][0][0] if enriched else "—"
        index_lines.append(
            f"| {profile} | {style} | {leader} | {stats['pb_a_hits_top30']} | "
            f"{stats['pb_b_hits_top30']} | {stats['trim_hits_top30']} | "
            f"[{profile}_conclusions.md](profile_conclusions/{profile}_conclusions.md) |"
        )

    # Theme summary across momentum profiles
    theme_lines = [
        "",
        "## Cross-profile themes (not a composite score)",
        "",
        "### Momentum / continuation cluster",
        "Profiles **breakout_long_v1**, **sector_rotation_momentum_v1**, **early_momentum_inflection_v1**, "
        "**forward_edge_active_v1**, **pre_earnings_drift_v1** share a **semi + storage + equipment** tape "
        "(SNDK, WDC, MU, AMAT, LRCX, SEZL). This aligns with **Playbook A** (ATRP/volume) from the 3-week study. "
        "**Risk:** several are also top picks on **mean_reversion_exhaustion_v1** — extended tape; scale in on "
        "pullbacks, enforce stops.",
        "",
        "### Quality / compounder cluster",
        "**quality_value_compounder**, **durable_value_compounder_v1**, **income_compounder_v1**, "
        "**defensive_fortress_v1**, **quality_growth_at_reasonable_price_v1** favor **AUPH, INCY, ZEAL, WDO, FRES** — "
        "healthcare, gold, defensive. Better **Playbook B** overlap; lower vol, longer hold.",
        "",
        "### Value / recovery cluster",
        "**asymmetric_value**, **value_recovery_v1**, **deep_value_momentum** tilt **precious metals, small-cap value, "
        "turnaround** (AFM, NEM, AU, HCI, BION). Use **Playbook C** to reject pure monthly-oversold entries.",
        "",
        "### Inverted / risk management",
        "**fragility_short** and **mean_reversion_exhaustion_v1** highlight the **same semi momentum leaders** — "
        "for long-only books, treat as **cooling/trim signals** when 3w vol playbook is crowded.",
        "",
        "### swing_reversal_v1 (new profile)",
        "Top names overlap momentum leaders more than classical oversold reversals. Per 3w study, **monthly oversold "
        "alone is a headwind** — require **repair confirmation** (volume, ATRP, short-term trend emergence) before "
        "treating as swing-long entries.",
        "",
        "## Predictor stock rankings quick links",
        "",
        "| File | Use |",
        "|------|-----|",
        "| `01_composite_bullish_fit_top100.csv` | Cross-profile vol/indicator fit |",
        "| `02_playbook_a_volatility_continuation_top100.csv` | Momentum profile validation |",
        "| `03_playbook_b_quality_drift_top100.csv` | Compounder profile validation |",
        "| `04_trim_negative_drift_top100.csv` | Universal avoid/trim |",
        "| `05_warning_overlay_avoid_top100.csv` | Playbook C filter |",
        "| `07_active_management_candidates_top100.csv` | Active sleeve overlay |",
        "",
    ]
    index_lines.extend(theme_lines)

    index_path = run_dir / "per_profile_conclusions_index.md"
    index_path.write_text("\n".join(index_lines), encoding="utf-8")

    rankings_ref = run_dir / "predictor_stock_rankings_reference.md"
    rankings_ref.write_text(
        "\n".join(
            [
                "# Predictor stock rankings — cross-reference for this run",
                "",
                f"Rankings generated from **whole-period close return study** "
                f"`scan_period_close_forward_tracking_25may_12jun2026_dd898100`.",
                "",
                f"**Source directory:** `{rank_dir}`",
                "",
                "## Key files used in profile conclusions",
                "",
                "| File | Purpose |",
                "|------|---------|",
                "| `01_composite_bullish_fit_top100.csv` | ATRP/volume/indicator composite fit (12 Jun positioning) |",
                "| `02_playbook_a_volatility_continuation_top100.csv` | Vol expansion continuation screen |",
                "| `03_playbook_b_quality_drift_top100.csv` | Multi-day positive drift names |",
                "| `04_trim_negative_drift_top100.csv` | Persistent losers — universal trim |",
                "| `05_warning_overlay_avoid_top100.csv` | Playbook C — oversold/crowded/mega-cap flags |",
                "| `06_playbook_a_realized_11_06_2026_top100.csv` | Realized period winners on last scored day anchor |",
                "| `07_active_management_candidates_top100.csv` | Active sleeve overlay |",
                f"| `indicator_perf/{top_stable_indicator_file.name}` | Which indicators mattered in period-total analysis |",
                "",
                "## Re-generate rankings",
                "",
                "```bash",
                "python scripts/run_close_forward_predictor_stock_rankings.py --top-n 100",
                "```",
                "",
                "## Re-generate profile conclusions",
                "",
                "```bash",
                f"python scripts/generate_move_prediction_profile_conclusions.py --run-id {run_id}",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return [index_path, rankings_ref, *per_profile_paths]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--mp-db", type=Path, default=DEFAULT_MP_DB)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--rank-dir", type=Path, default=DEFAULT_RANK_DIR)
    parser.add_argument("--daily-db", type=Path, default=DEFAULT_DAILY_DB)
    parser.add_argument("--daily-run-id", default=DEFAULT_DAILY_RUN_ID)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = generate(
        run_id=args.run_id,
        mp_db=args.mp_db,
        run_dir=args.run_dir,
        rank_dir=args.rank_dir,
        daily_db=args.daily_db,
        daily_run_id=args.daily_run_id,
    )
    print(f"Wrote {len(paths)} files to {args.run_dir}")
    for path in paths[:3]:
        print(f"  {path}")
    if len(paths) > 3:
        print(f"  ... and {len(paths) - 3} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
