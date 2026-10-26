"""Write a run folder the way edge research does: DuckDB tables + overview.log.

No eligibility cutoffs. Callers name tables and source ids.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.scan_sources import rows_from_csv, rows_to_csv

_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(name: str) -> str:
    if not _TABLE_NAME.match(name):
        raise ValueError(f"Invalid table name {name!r}")
    return name


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def git_stamp(cwd: Path | None = None) -> dict[str, Any]:
    """Best-effort git identity. Missing git is not a failure."""
    import subprocess

    root = Path(cwd) if cwd else Path(__file__).resolve().parents[2]
    out: dict[str, Any] = {"git_commit": None, "git_branch": None, "git_dirty": None}
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        out["git_commit"] = commit or None
        out["git_branch"] = branch or None
        out["git_dirty"] = bool(dirty)
    except (OSError, subprocess.CalledProcessError):
        pass
    return out


def write_overview_log(
    path: str | Path,
    *,
    tool: str,
    created_at: str | None = None,
    schema_version: str | None = None,
    sources: Mapping[str, Any] | None = None,
    tables: Mapping[str, int] | None = None,
    notes: Sequence[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Path:
    """Short run header: version, timestamp, source ids, table counts."""
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = git_stamp()
    created = created_at or datetime.now(tz=timezone.utc).isoformat()
    lines = [
        f"tool={tool}",
        f"created_at_utc={created}",
        f"schema_version={schema_version or ''}",
        f"git_commit={stamp.get('git_commit') or ''}",
        f"git_branch={stamp.get('git_branch') or ''}",
        f"git_dirty={stamp.get('git_dirty')}",
    ]
    for key, value in (sources or {}).items():
        if value is None or value == "":
            continue
        lines.append(f"source.{key}={value}")
    for name, count in (tables or {}).items():
        lines.append(f"table.{name}={count}")
    for key, value in (extra or {}).items():
        lines.append(f"{key}={value}")
    for note in notes or ():
        lines.append(f"note={note}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


# Meanings for columns that show up on operator dumps. Unknown columns are
# still listed; they are not given a guessed definition.
FIELD_MEANINGS: dict[str, str] = {
    "symbol": "Exchange-prefixed id, for example NASDAQ:MU.",
    "adx": "Average directional index. Trend strength, not a buy score.",
    "vs200": "Close versus the 200-day average, %.",
    "street_left": "Street leftover only, %. Pack leftover can be higher when edge upside is larger.",
    "left_carrier": "Who carries pack leftover: edge_only, both, street, or none. A label from derive.leftover_carrier. Not a cutoff.",
    "ticker": "Symbol stem without the exchange.",
    "company": "Company name.",
    "ind": "Industry.",
    "sector": "Sector.",
    "close": "Tape close used for leftover. Not a stale holdings mark.",
    "mcap": "Market cap, USD.",
    "rev_yoy": "Trailing revenue growth, year over year, %. Not blended with the CAGR.",
    "rev_cagr": "Trailing revenue CAGR, %. Not blended with year-over-year growth.",
    "rev_growth": "Quoted revenue growth, %. Prefers the projection year-1 step, then rev_yoy, then rev_cagr. The source column names which one.",
    "rev_growth_source": "fp_rev_y1, rev_yoy, or rev_cagr. Which number rev_growth used.",
    "earn_yoy": "Trailing net-income growth, year over year, %.",
    "earn_cagr": "Trailing net-income CAGR, %.",
    "ebitda_yoy": "Trailing EBITDA growth, year over year, %.",
    "earn_growth": "Quoted earnings growth, %. Prefers the projection net-income step, then earn_yoy, then ebitda_yoy, then earn_cagr.",
    "earn_growth_source": "fp_ni_y1, earn_yoy, ebitda_yoy, or earn_cagr. Which number earn_growth used.",
    "lanes": "revenue, earnings, or revenue+earnings. Which growth threshold the name cleared. Not a score.",
    "overlooked": "true when a growth-lane name is a forward-multiple DISCOUNT and rng is under the spec max. A label, not a rank.",
    "earn_outrun": "true when quoted earnings growth beats quoted revenue growth by at least 10 points. A results label, not a sort key.",
    "pe_hist_pct": "First-to-last percent change in trailing PE on the shortlist all-fields span. Negative means the multiple got cheaper on this name's own history.",
    "hist_pe": "CHEAPER, RICHER, FLAT, or THIN_HIST from pe_hist_pct. CHEAPER is pe_hist_pct <= -5. Not the analysis-style path class.",
    "rank": "Order inside that lane after the industry cap. 1 is the highest quoted growth. Not a 0-100.",
    "day": "Session price return, %.",
    "w": "Week price return, %.",
    "d5": "5-day price return, %.",
    "m1": "1-month price return, %.",
    "m3": "3-month price return, %.",
    "relvol": "Volume versus the about-10-day average.",
    "rsi": "Relative strength index, 0-100.",
    "rng": "Position in the 52-week range, 0-100. About 85+ is paid.",
    "vs50": "Close versus the 50-day average, %. The size of an ABOVE or BELOW label. Not a score.",
    "vs200": "Close versus the 200-day average, %. The size of tech_sma200. Not a score.",
    "opm": "Operating margin, %. Current business quality. Not a score.",
    "pe": "Trailing price / earnings.",
    "pe_fwd": "Quoted forward PE. Projection next-year PE (fp_pe_y1) when that path or CAGR exists, otherwise TradingView. Do not fill it with EV/Rev.",
    "pe_fwd_tv": "TradingView price_earnings_forward_fy. Empty on recent all-fields scans. Stays empty.",
    "pe_fwd_source": "fp_path, fp_cagr, tv, or tv_nongaap. Which number pe_fwd used.",
    "pe_fwd_nongaap": "TradingView non-GAAP next-FY PE. Used only when the projection and pe_fwd_tv are empty.",
    "evebitda_fwd": "Forward EV/EBITDA, year 1. Today's EV / projected EBITDA. Same number as fp_evebitda_y1.",
    "fp_horizon_year": "Year used by the fp_*_y5 columns. At most 5.",
    "fp_pe_y1": "Forward PE for next year. Today's market cap / projected net income. path = year grid. cagr = trailing compounded.",
    "fp_pe_y5": "Forward PE at fp_horizon_year. Today's market cap / that year's net income.",
    "fp_pe_method": "path or cagr for fp_pe_y1.",
    "fp_pe_y5_method": "path or cagr for fp_pe_y5.",
    "fp_evebitda_y1": "Forward EV/EBITDA for next year. Today's enterprise value / projected EBITDA.",
    "fp_evebitda_y5": "Forward EV/EBITDA at fp_horizon_year.",
    "fp_evebitda_method": "path or cagr for fp_evebitda_y1.",
    "fp_ev": "Enterprise value used in fp_evebitda_y1 and fp_evebitda_y5. Today's EV, not the model terminal EV.",
    "fp_net_debt_y1": "Net debt next year. Today's net debt scaled by the price-model path.",
    "fp_net_debt_y5": "Net debt at fp_horizon_year.",
    "fp_debt_method": "path when the price year grid supplied the debt path.",
    "fp_fcf_y1": "Free cash flow next year. Trailing FCF compounded by its CAGR, clipped to -80% to +150% per year.",
    "fp_fcf_y5": "Free cash flow at fp_horizon_year.",
    "fp_fcf_method": "cagr. There is no FCF year grid.",
    "fp_ni_y1": "Projected net income next year, dollars.",
    "fp_ni_y5": "Projected net income at fp_horizon_year, dollars.",
    "fp_ebitda_y1": "Projected EBITDA next year, dollars.",
    "fp_ebitda_y5": "Projected EBITDA at fp_horizon_year, dollars.",
    "ni": "Trailing net income, dollars.",
    "ebitda": "Trailing EBITDA, dollars. Not the EV/EBITDA multiple.",
    "fcf": "Trailing free cash flow, dollars.",
    "net_debt": "Trailing net debt, dollars. Negative is net cash.",
    "total_debt": "Trailing total debt, dollars.",
    "cash": "Trailing cash, dollars.",
    "ev": "Trailing enterprise value, dollars.",
    "peg": "PE versus earnings growth.",
    "evebitda": "Enterprise value / trailing EBITDA.",
    "pb": "Price / book.",
    "evfcf": "Enterprise value / free cash flow.",
    "evrev": "Enterprise value / trailing revenue. Not the peer quote unless val_field is evrev.",
    "dte": "Calendar days until the next earnings date.",
    "earn_last": "Last earnings release date. Epoch or calendar date from all-fields.",
    "earn_time": "TradingView earnings_release_time. 1 is after the close. -1 is before the open. 0 is unspecified.",
    "pre_chg": "Premarket change, percent, on that all-fields row. On a file written before the next cash open this can still be the prior morning.",
    "pre_gap": "Premarket gap, percent, on that all-fields row.",
    "pre_close": "Premarket close on that all-fields row.",
    "post_chg": "After-hours change versus the cash close, percent.",
    "post_close": "After-hours last price.",
    "afternoon_close": "Close on the prior all-fields scan. On a print day this can be an intraday mark, not the cash close.",
    "afternoon_day": "Session return, percent, on the prior all-fields scan.",
    "session_close": "Close on the later all-fields scan. After the cash close this is the official print.",
    "session_day": "Session return, percent, on the later all-fields scan.",
    "vs_afternoon": "Later close versus the prior scan close, percent. The size of the move after the earlier print.",
    "when_label": "BMO, AMC, or UNSPEC from earn_time. Not a score.",
    "pre_repeat": "True when pre_chg on the later file matches the prior file. The later file did not refresh premarket.",
    "left": "Unused upside %, max(street PT, edge forward upside). Not mix.",
    "rr": "leftover / max(52-week range used, 15). High means unused upside versus range already paid.",
    "alloc_sleeve": "discount_momentum or premium_strength. Empty means the row is not on the payoff focus.",
    "discount_momentum": "alloc_sleeve. Quoted multiple at least 5% cheaper than the industry median, and weeks bo at least the recipe momentum floor.",
    "premium_strength": "alloc_sleeve. Quoted multiple at least 5% richer than the industry median, and weeks bo at least the recipe strength floor.",
    "up_from_entry_pct": "Percent from entry_high up to exit_target. The upside width of the band.",
    "down_from_entry_pct": "Percent from entry_high down to exit_stop. Negative. The downside width of the band.",
    "band_payoff": "Upside distance from entry_high to exit_target, divided by the distance down to exit_stop. Sort key for the ten-name focus. Not rr and not a 0-100.",
    "stake_per_1k_usd": "Dollars that earn 1000 if exit_target prints. 10% upside is 10000. 50% upside is 2000.",
    "profit_on_10k_usd": "Dollar profit on a 10000 check if the upside percent prints. 10% upside is 1000.",
    "profit_on_5k_usd": "Dollar profit on a 5000 check if the upside percent prints. 50% upside is 2500.",
    "focus_order": "Reading order inside the payoff focus. Not a Daily rank and not a 0-100.",
    "cycle_read": "peak_margin, earn_leads, rev_carries, or thin. A label for whether earnings are carrying a cycle, not a rank.",
    "peak_margin": "cycle_read. Net margin is at or above the recipe peak floor, default 40 percent.",
    "earn_leads": "cycle_read. Earnings growth beats revenue growth by the recipe gap. Revenue is not the carrier.",
    "rev_carries": "cycle_read. Revenue growth and net margin clear their floors, and peak_margin and earn_leads are off.",
    "pb_read": "DISCOUNT, PREMIUM, or INLINE on pb_vs_ind. DISCOUNT is at or below -5. The percent is pb_vs_ind.",
    "seat_pct": "Where the close sits in the window from close_min to close_max. 0 is the low. 100 is the high.",
    "dd_from_high_pct": "Close versus the window high, percent. Negative when the close is under that high.",
    "up_from_low_pct": "Close versus the window low, percent.",
    "resilient": "True when cycle_read is rev_carries, pb_read is DISCOUNT, seat_pct is at or under the recipe seat max, and base_effect is false. A label, not a 0-100.",
    "base_effect": "True when revenue or earnings growth is at or above 300 percent. A tiny starting base, not a growth rate to underwrite.",
    "bo": "Weeks breakout_long_v1. Strength, not a blend with leftover.",
    "cont": "Weeks quality_continuation_v1.",
    "fwd": "Weeks forward_edge_active_v2.",
    "early": "Weeks early_momentum_inflection_v1.",
    "recov": "Weeks value_recovery_v3.",
    "peer": "DISCOUNT, PREMIUM, INLINE, THIN, or NA on val_field versus the US $2B industry median. DISCOUNT is val_vs_ind at or below -5. PREMIUM is at or above 5. INLINE is between. The percent is val_vs_ind, not this label.",
    "val": "The multiple setup chose to quote.",
    "val_field": "Which multiple val is (pe, evebitda, evrev, ...).",
    "val_label": "Short label for val_field.",
    "val_vs_ind": "Percent versus the industry median on val_field. Negative means cheaper. Separate from val_vs_avg. This is how wide a DISCOUNT or PREMIUM is. The peer label uses this column, not the average.",
    "val_ind_med": "Industry median of val_field. The level val_vs_ind is measured against. Separate from val_ind_avg.",
    "val_vs_avg": "Percent versus the industry average on val_field. Negative means cheaper. Separate from val_vs_ind. Does not change the peer label.",
    "val_ind_avg": "Industry average of val_field. The level val_vs_avg is measured against. Separate from val_ind_med.",
    "eff_vs_ind": "Percent versus the industry median of the efficiency field. Separate from eff_vs_avg.",
    "eff_ind_med": "Industry median of the efficiency field.",
    "eff_vs_avg": "Percent versus the industry average of the efficiency field. Separate from eff_vs_ind.",
    "eff_ind_avg": "Industry average of the efficiency field.",
    "tech_sma": "ABOVE or BELOW the 50-day average. ABOVE is vs50 >= 0. The percent gap is vs50.",
    "entry_low": "Bottom of the buy band from generic_utils.entry_book. Not a score.",
    "entry_high": "Top of the buy band. The buy is entry_low to entry_high. Do not buy a reclaim at the close.",
    "trim_px": "First trim. The 50-day when trim_state is trim_at_sma. Empty when through_average. Not the kill.",
    "trim_state": "trim_at_sma, through_average, at_average, or unknown.",
    "kill_px": "Same price as exit_stop. 1.5 ATR under the band. A print there ends this entry.",
    "mark_vs_band": "inside, above_band, below_band, or unknown versus today's close. Not a course.",
    "sma_role": "reclaim, stop_below, or at_average. How the 50-day sits versus the band.",
    "tech_sma200": "ABOVE or BELOW the 200-day average. The percent gap is vs200.",
    "tech_rng": "UNUSED (rng under 40), MID, or PAID (rng at least 85). The position is rng, not this label.",
    "fwd_street_px": "Street analyst target in dollars.",
    "fwd_street_pct": "Street leftover, %.",
    "fwd_pack_px": "Pack leftover in dollars: close times (1 + leftover).",
    "fwd_pack_pct": "Pack leftover, %. Same idea as left.",
    "fwd_pe": "Copy of quoted pe_fwd. Projection next-year PE when fp_pe_y1 exists.",
    "fwd_pe_source": "Same as pe_fwd_source.",
    "fwd_evebitda": "Copy of evebitda_fwd.",
    "fwd_peg": "Copy of peg.",
    "fp_terminal_px": "Model terminal price. Separate from street and pack.",
    "fp_primary_upside_pct": "Model upside, %. A reading of several hundred percent is a data gap.",
    "fp_rev_cagr_own": "Model own-lane revenue CAGR, %.",
    "fp_joined": "Which forward pieces were joined: street_tv, price, growth.",
    "street_px": "Street target in dollars.",
    "target_px": "Pack leftover in dollars.",
    "mix": "manager_action_signal. Not operator conviction.",
    "mtp": "ENTER_SMALL, ENTER_PROBE, WATCH, or AVOID_CHASE. Climate, not a buy list.",
    "down_class": "On punished tape: BOUNCE, CONTINUE_DOWN, or MIXED.",
    "wt_nav": "Position as % of NAV (equity mark + cash). Also the wipe if price goes to zero.",
    "wipe_nav_pct": "Same ceiling as wt_nav: loss if the position goes to zero, as % of NAV.",
    "m10_nav": "A further 10% drop from today's mark, as % of NAV.",
    "vs_cost": "Unrealized return versus cost, %.",
    "bo_first": "Breakout score at the first week in the span.",
    "bo_last": "Breakout score at the last week in the span.",
    "bo_chg": "bo_last minus bo_first. Not a 0-100.",
    "n": "Number of observations in a span.",
    "as_of": "Date label of that history row.",
    "upside_10": "1-10 trading-upside heuristic from named up_* components. Not leftover, not mix, not a probability. Thin coverage cannot print above 6.",
    "downside_10": "1-10 trading-downside heuristic from named dn_* components. Not a 0-100. Thin coverage cannot print above 6.",
    "up_minus_dn": "upside_10 minus downside_10. A gap, not a score.",
    "up_asymmetric_payoff": "0-1 upside part: leftover, rr, unused range, OPENING/CHEAPER. Not a 0-100.",
    "up_structure_conviction": "0-1 upside part: live bo/cont/fwd, recov, mix. Mix avoid cuts this. Not leftover.",
    "up_valuation_growth": "0-1 upside part: peer DISCOUNT, val_vs_ind, fp_rev_cagr_own, cheaper PE span.",
    "up_timing_catalyst": "0-1 upside part: MTP climate, dte 8-21, punished BOUNCE. ENTER_SMALL is not a buy list.",
    "up_move_fuel": "0-1 upside part: expected-move size, coiled leftover, relvol. ATRP is size, not quality.",
    "dn_fragility_trend": "0-1 downside part: frag, vs50 down, DYING, CONTINUE_DOWN, drawdown.",
    "dn_valuation_ceiling": "0-1 downside part: PREMIUM, paid rng, leftover gone.",
    "dn_exhaustion_reversal": "0-1 downside part: RSI stretch, exh, giveback from the window high.",
    "dn_edge_risk": "0-1 downside part: lean AVOID/downside, mix avoid.",
    "dn_vol_amplification": "0-1 downside part: high velocity with negative tilt. ATRP has negative IC as a rank.",
    "up_cover_frac": "Share of upside recipe weights that had a value. Under 0.4 is thin_up.",
    "dn_cover_frac": "Share of downside recipe weights that had a value. Under 0.4 is thin_dn.",
    "thin_up": "True when up_cover_frac is under 0.4. upside_10 cannot print above 6.",
    "thin_dn": "True when dn_cover_frac is under 0.4. downside_10 cannot print above 6.",
    "path_class": "OPENING, DYING, CHEAPER, FLAT, or THIN_HIST. OPENING/DYING/CHEAPER match analysis-style. DYING wins. Enough history that is none of those is FLAT. First-iteration CHEAPER uses pe_pct.",
    "vol_class": "coiled, active_swing, high_velocity, extreme, or thin. Realized close_step_abs_mean_pct, else ATRP.",
    "exp_move_20d": "Expected-move envelope, percent, over 20 trading days: ATRP times sqrt(20). Size, not leftover quality.",
    "exp_move_primary_d": "Expected-move envelope at the recipe primary horizon (default 20d).",
    "close_step_abs_mean_pct": "Mean absolute day-to-day close change, percent, on the history span.",
    "close_step_stdev_pct": "Stdev of day-to-day close changes, percent.",
    "close_step_up_rate": "Share of close steps that were up. 0-1, not a 0-100.",
    "close_max_drawdown_pct": "Worst peak-to-trough close drawdown on the span, percent. Negative.",
    "close_giveback_from_max_pct": "Last close versus the span high, percent. Negative when off the high.",
    "close_rebound_from_min_pct": "Last close versus the span low, percent.",
    "close_seat_pct": "Where the last close sits between span low and high. 0 is the low. 100 is the high.",
    "ic_cited": "Spearman IC quoted from the first-iteration research backtest. Not a leftover gate.",
    "ic_live": "Spearman IC from the newest family_metrics.csv when that family is on disk.",
}

SELECTION_FIELDS: tuple[str, ...] = (
    "symbol",
    "ind",
    "opm",
    "pe",
    "pe_peer",
    "pe_vs_ind",
    "pe_fwd",
    "pe_fwd_peer",
    "pe_fwd_vs_ind",
    "evebitda",
    "evebitda_peer",
    "evebitda_vs_ind",
    "evebitda_fwd",
    "evebitda_fwd_peer",
    "evebitda_fwd_vs_ind",
    "pe_fwd_source",
    "fp_pe_y1",
    "fp_pe_y5",
    "fp_evebitda_y1",
    "fp_evebitda_y5",
    "fp_net_debt_y1",
    "fp_fcf_y1",
    "peg",
    "dte",
    "left",
    "rr",
    "bo",
    "cont",
    "fwd",
    "rsi",
    "rng",
    "vs50",
    "vs200",
    "tech_sma",
    "tech_sma200",
    "tech_rng",
    "entry_low",
    "entry_high",
    "trim_px",
    "trim_state",
    "kill_px",
    "mark_vs_band",
    "sma_role",
    "peer",
    "val_vs_ind",
    "val_ind_med",
    "val_vs_avg",
    "val_ind_avg",
    "eff",
    "eff_vs_ind",
    "eff_ind_med",
    "eff_vs_avg",
    "eff_ind_avg",
    "val",
    "val_field",
    "fwd_street_px",
    "fwd_pack_px",
    "fp_terminal_px",
    "fp_primary_upside_pct",
    "fp_rev_cagr_own",
)


def _qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _columns_of(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: list[str] = []
    found: set[str] = set()
    for row in rows:
        for key in row.keys():
            text = str(key)
            if text not in found:
                found.add(text)
                seen.append(text)
    preferred = [name for name in SELECTION_FIELDS if name in found]
    rest = [name for name in seen if name not in set(preferred)]
    return preferred + rest


def _field_line(name: str) -> str:
    meaning = FIELD_MEANINGS.get(name)
    if meaning is None and name.startswith("m") and "_" in name:
        head, kind = name.split("_", 1)
        if head[1:].isdigit() and kind in {"nav", "usd", "px"}:
            unit = {"nav": "% of NAV", "usd": "dollars", "px": "price"}[kind]
            meaning = f"A further {head[1:]}% drop from today's mark, in {unit}."
    if meaning is None and name.startswith("c") and "_" in name:
        head, kind = name.split("_", 1)
        if head[1:].isdigit() and kind in {"nav", "usd", "px"}:
            unit = {"nav": "% of NAV", "usd": "dollars", "px": "price"}[kind]
            meaning = f"Price {head[1:]}% below cost, in {unit}."
    if meaning is None and name.startswith("trim") and name[4:6].isdigit():
        pct = name[4:6]
        if name.endswith("_cash"):
            meaning = f"Cash raised by selling {pct}% of the position."
        elif "remain" in name:
            meaning = f"Wipe left after selling {pct}% of the position, as % of NAV."
    if meaning is None and name in {"sma50_nav", "atr_nav"}:
        meaning = (
            "NAV change if price prints the 50-day average."
            if name == "sma50_nav"
            else "NAV change if price prints close minus 1.5 times ATR."
        )
    if meaning is None and name.endswith("_peer"):
        stem = name[: -len("_peer")]
        if stem in {"pe_fwd", "evebitda_fwd"}:
            meaning = (
                f"DISCOUNT, PREMIUM, INLINE, THIN, or NA on the forward derivative {stem} "
                f"versus the industry median. The percent is {stem}_vs_ind. "
                "The forward number can be off. Separate from the trailing TV bin."
            )
        else:
            meaning = (
                f"DISCOUNT, PREMIUM, INLINE, THIN, or NA on the TradingView trailing field {stem}, "
                f"taken as published. The percent is {stem}_vs_ind. "
                "Separate from the forward derivative bin."
            )
    if meaning is None and name.endswith("_vs_ind"):
        stem = name[: -len("_vs_ind")]
        meaning = (
            f"Percent versus the US $2B industry median of {stem}. "
            f"Separate from {stem}_vs_avg. Not blended into a score."
        )
    if meaning is None and name.endswith("_vs_avg"):
        stem = name[: -len("_vs_avg")]
        meaning = (
            f"Percent versus the US $2B industry average of {stem}. "
            f"Separate from {stem}_vs_ind. Not blended into a score."
        )
    if meaning is None and name.endswith("_ind_med"):
        meaning = f"Industry median of {name[: -len('_ind_med')]}."
    if meaning is None and name.endswith("_ind_avg"):
        meaning = f"Industry average of {name[: -len('_ind_avg')]}."
    if meaning is None and "_step_" in name:
        meaning = (
            "Day-to-day step statistic from series_span(include_steps=True). "
            "Not a 0-100 and not leftover."
        )
    if meaning is None and name.startswith("exp_move_") and name.endswith("d"):
        meaning = (
            "Expected-move envelope, percent, over that many trading days: "
            "ATRP times sqrt(horizon). Size, not quality."
        )
    if meaning:
        return f"  {name} — {meaning}"
    return f"  {name} — On this table. Not blended into a score."


def write_query_guide(
    path: str | Path,
    *,
    tool: str,
    duckdb_path: str | Path,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    csv_paths: Mapping[str, str] | None = None,
    notes: Sequence[str] | None = None,
) -> Path:
    """Plain-text field list and copy-paste queries for one DuckDB run folder."""
    guide = Path(path)
    guide.parent.mkdir(parents=True, exist_ok=True)
    db = Path(duckdb_path).as_posix()
    csv_paths = csv_paths or {}
    lines = [
        "DuckDB output — query guide",
        f"tool={tool}",
        f"database={db}",
        "",
        "This folder is the deliverable. A canvas is a separate page and is not required to use these rows.",
        "Columns stay separate. Do not average margin, earnings, forward prices, strength, and technicals into one score.",
        "pe_fwd is the quoted forward PE: fp_pe_y1 from financial_projection (path, or CAGR when the year cell is missing).",
        "pe_fwd_tv is the TradingView forward PE and stays empty when that scan column is empty.",
        "Do not fill pe_fwd with EV/Rev. fp_pe_y5, fp_evebitda_y1, fp_evebitda_y5, fp_net_debt_y1, and fp_fcf_y1 stay separate.",
        "fp_*_y5 uses fp_horizon_year (at most 5). Street price, pack price, and model terminal stay three numbers.",
        "In SQL the leftover column must be quoted as \"left\".",
        "A label is a bin. The percent beside it is the coverage, so a 1% gap and a 40% gap are not the same word.",
        "  tech_sma ABOVE/BELOW — read vs50 (percent versus the 50-day average). tech_sma200 uses vs200.",
        "  entry_low to entry_high is the buy band. kill_px ends that entry. tech_sma and vs50 stay on the row.",
        "  tech_rng UNUSED/MID/PAID — read rng (0-100 in the 52-week range).",
        "  peer DISCOUNT/PREMIUM/INLINE uses val_vs_ind only (percent versus the industry median on val_field).",
        "  Trailing TV bins are separate columns: pe_peer, evebitda_peer, pb_peer, evfcf_peer. Each uses that field's own _vs_ind. The multiple is the TradingView field as published.",
        "  Forward bins are pe_fwd_peer and evebitda_fwd_peer. Each uses that field's own _vs_ind. Those multiples are projection derivatives and can be off.",
        "  Same cutoffs on every bin: DISCOUNT <= -5, PREMIUM >= 5, INLINE between. Do not treat the forward bin as the trailing bin.",
        "  Negative val_vs_ind is cheaper. DISCOUNT is <= -5. PREMIUM is >= 5. INLINE is between. THIN and NA have no percent.",
        "  val_vs_avg is the same gap versus the industry average. It is a different column. It does not change the peer label.",
        "  val_ind_med is the median level. val_ind_avg is the average level.",
        "  Every other relative field follows the same four names: FIELD_vs_ind, FIELD_ind_med, FIELD_vs_avg, FIELD_ind_avg.",
        "  vs50 and vs200 are percent versus that name's own moving average, not versus peers.",
        "",
    ]
    for note in notes or ():
        lines.append(f"note: {note}")
    if notes:
        lines.append("")
    for name, rows in tables.items():
        cols = _columns_of(rows)
        csv_path = csv_paths.get(name, "")
        lines.append("=" * 72)
        lines.append(f"TABLE {name}    rows={len(rows)}")
        lines.append(f"database={db}")
        if csv_path:
            lines.append(f"csv={csv_path}")
        lines.append("")
        lines.append("Fields")
        if not cols:
            lines.append("  (no columns — the table was empty)")
        for col in cols:
            lines.append(_field_line(col))
        lines.append("")
        lines.append("Queries")
        lines.append("")
        lines.append("1. Column list")
        lines.append(f'   python src/generic_utils/tv_scan_cli.py inventory --db "{db}"')
        lines.append(
            f"   SQL: SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_name = '{name}' ORDER BY ordinal_position;"
        )
        lines.append("")
        present_selection = [col for col in SELECTION_FIELDS if col in set(cols)]
        if present_selection:
            field_list = ",".join(present_selection)
            lines.append("2. Selection columns (margin, earnings, forward, strength, technicals, payoff)")
            lines.append(
                "   python src/generic_utils/tv_scan_cli.py named "
                f'--db "{db}" --table {name} --fields {field_list} --out {name}_selection.csv'
            )
            sql_cols = ", ".join(_qident(col) for col in present_selection)
            lines.append(f"   SQL: SELECT {sql_cols} FROM {name};")
            lines.append("")
        step = 3
        if "rr" in cols:
            lines.append(f"{step}. Rank by payoff (rr), highest first. Not a 0-100.")
            if csv_path:
                tie = " --tie-breaker bo" if "bo" in cols else ""
                lines.append(
                    "   python src/generic_utils/tv_scan_cli.py focus "
                    f'--csv "{csv_path}" --field rr{tie} --top 25 --out {name}_by_rr.csv'
                )
            show = [col for col in ("symbol", "ind", "left", "rr", "opm", "pe", "bo") if col in cols]
            show_sql = ", ".join(_qident(col) for col in show) if show else "*"
            lines.append(
                f"   SQL: SELECT {show_sql} FROM {name} ORDER BY {_qident('rr')} DESC NULLS LAST LIMIT 25;"
            )
            lines.append("")
            step += 1
        if "bo" in cols:
            lines.append(f"{step}. Rank by weeks strength (bo), highest first. Separate from rr.")
            if csv_path:
                tie = " --tie-breaker rr" if "rr" in cols else ""
                lines.append(
                    "   python src/generic_utils/tv_scan_cli.py focus "
                    f'--csv "{csv_path}" --field bo{tie} --top 25 --out {name}_by_bo.csv'
                )
            show = [col for col in ("symbol", "ind", "bo", "cont", "fwd", "left", "rr") if col in cols]
            show_sql = ", ".join(_qident(col) for col in show) if show else "*"
            lines.append(
                f"   SQL: SELECT {show_sql} FROM {name} ORDER BY {_qident('bo')} DESC NULLS LAST LIMIT 25;"
            )
            lines.append("")
            step += 1
        if "ind" in cols:
            metrics = [col for col in ("left", "opm", "rsi", "d5", "bo") if col in cols]
            if metrics:
                lines.append(f"{step}. Industry means. Peer set is not this group unless you say so.")
                if csv_path:
                    lines.append(
                        "   python src/generic_utils/tv_scan_cli.py group "
                        f'--csv "{csv_path}" --by ind --metrics {",".join(metrics)} --min-n 6'
                    )
                avg = ", ".join(f"AVG({_qident(col)}) AS {_qident(col)}" for col in metrics)
                lines.append(
                    f"   SQL: SELECT {_qident('ind')}, COUNT(*) AS n, {avg} FROM {name} "
                    f"GROUP BY 1 HAVING COUNT(*) >= 6 ORDER BY n DESC;"
                )
                lines.append("")
                step += 1
        if "down_class" in cols:
            lines.append(f"{step}. Punished-tape classes.")
            if csv_path:
                lines.append(
                    "   python src/generic_utils/tv_scan_cli.py counts "
                    f'--csv "{csv_path}" --field down_class'
                )
            lines.append(
                f"   SQL: SELECT {_qident('down_class')}, COUNT(*) FROM {name} GROUP BY 1;"
            )
            lines.append("")
            step += 1
        if "wt_nav" in cols:
            id_col = "ticker" if "ticker" in cols else "symbol" if "symbol" in cols else None
            show = [col for col in (id_col, "wt_nav", "vs_cost", "m10_nav") if col and col in cols]
            lines.append(f"{step}. Book size. wt_nav is the wipe if the name goes to zero.")
            show_sql = ", ".join(_qident(col) for col in show)
            lines.append(
                f"   SQL: SELECT {show_sql} FROM {name} ORDER BY {_qident('wt_nav')} DESC;"
            )
            lines.append("")
        numeric = [col for col in ("opm", "pe", "pe_fwd", "left", "rr", "bo", "rsi", "rng") if col in cols]
        if numeric and csv_path:
            lines.append("Summary of the numeric selection fields that are on this table")
            lines.append(
                "   python src/generic_utils/tv_scan_cli.py summary "
                f'--csv "{csv_path}" --fields {",".join(numeric)}'
            )
            lines.append("")
    guide.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return guide


def rows_to_duckdb(
    path: str | Path,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Path:
    """Replace `path` with a DuckDB file, one table per mapping key.

    Same pattern as edge_research_tools (CSV round-trip -> CREATE TABLE AS
    read_csv). Empty tables get a placeholder VARCHAR column.
    """
    import duckdb

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = duckdb.connect(db_path.as_posix())
    try:
        for name, rows in tables.items():
            ident = _quote_ident(name)
            materialised = [dict(row) for row in rows]
            if not materialised:
                conn.execute(f"CREATE TABLE {ident} (placeholder VARCHAR)")
                continue
            csv_path = db_path.with_name(f".{name}.csv")
            rows_to_csv(csv_path, materialised)
            conn.execute(
                f"CREATE TABLE {ident} AS SELECT * FROM read_csv("
                f"{_quote_sql_literal(csv_path.as_posix())}, "
                f"header=true, auto_detect=true)"
            )
            csv_path.unlink(missing_ok=True)
    finally:
        conn.close()
    return db_path


def write_run_export(
    out_dir: str | Path,
    *,
    tool: str,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    sources: Mapping[str, Any] | None = None,
    schema_version: str | None = None,
    notes: Sequence[str] | None = None,
    extra: Mapping[str, Any] | None = None,
    duckdb_name: str = "scan.duckdb",
    log_name: str = "overview.log",
    write_csv: bool = True,
) -> dict[str, Any]:
    """Write CSV copies (optional), DuckDB, and overview.log into `out_dir`."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    counts = {name: len(rows) for name, rows in tables.items()}
    csv_paths: dict[str, str] = {}
    if write_csv:
        for name, rows in tables.items():
            csv_paths[name] = rows_to_csv(
                directory / f"{name}.csv", [dict(r) for r in rows]
            ).as_posix()
    db_path = rows_to_duckdb(directory / duckdb_name, tables)
    created = datetime.now(tz=timezone.utc).isoformat()
    log_path = write_overview_log(
        directory / log_name,
        tool=tool,
        created_at=created,
        schema_version=schema_version,
        sources=sources,
        tables=counts,
        notes=notes,
        extra=extra,
    )
    guide_path = write_query_guide(
        directory / "queries.txt",
        tool=tool,
        duckdb_path=db_path,
        tables=tables,
        csv_paths=csv_paths,
        notes=notes,
    )
    return {
        "out_dir": directory.as_posix(),
        "duckdb": db_path.as_posix(),
        "overview_log": log_path.as_posix(),
        "query_guide": guide_path.as_posix(),
        "csv": csv_paths,
        "tables": counts,
        "created_at_utc": created,
    }


def load_json_recipe(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Recipe {path} must be a JSON object")
    return payload


def parse_table_specs(items: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """Parse `name=csv_path` specs into table mapping."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Expected name=path, got {item!r}")
        name, csv_path = item.split("=", 1)
        tables[name.strip()] = rows_from_csv(csv_path.strip())
    return tables
