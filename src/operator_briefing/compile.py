"""Assemble one agent-ready briefing pack from locked scans."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .continuity import (
    continuity_action,
    load_prior_actions,
    map_mix_to_naive_book_action,
    prior_action_for,
)
from .discovery import BRIEFING_RUNS, LockedSources, lock_sources
from .extract import (
    extract_all_fields,
    extract_conviction,
    extract_profile_weeks,
    extract_progression,
    extract_regime,
    industry_breadth,
    load_csv_by_symbol,
    universe_stats,
)
from .sleeves import (
    EARNINGS_INDUSTRY_CAP,
    MTP_AVOID_ACTIONS,
    MTP_BOUNCE_ACTIONS,
    RADAR_INDUSTRY_CAP,
    SHORT_INDUSTRY_CAP,
    continuation_paid,
    continuation_unpaid,
    dedupe_by_industry,
    forming_eligible,
    is_us_listed,
    leftover_pct,
    profile_live,
    reward_risk,
    short_limited_upside,
    unpaid_eligible,
    unpaid_sort_key,
)
from .stance import (
    classify_event_play,
    rank_suggested_courses,
    suggest_stance,
    summarize_operator_course,
)

DEFAULT_OUTPUT_ROOT = BRIEFING_RUNS

GOTCHAS = [
    "Do not flatten mix / conviction / leftover / MTP into one 0-100 rank.",
    "manager_action_signal is the mix column, not manager_action.",
    "conviction rank_overall is rank_overall; leftover is max(street PT, edge forward_valuation_upside_pct).",
    "ENTER_SMALL / ENTER_PROBE is timing climate, not a buy list.",
    "Catalyst policy PASS on every row is not a filter — classify prints from dte + leftover + tape.",
    "avoid_value_trap on a live leftover Book line is HOLD_NO_ADD, not EXIT, unless a thesis-kill fires.",
    "Auto leftover-first lists dump biotech/ADR junk. Use unpaid_eligible / forming / continuation_paid / short_limited_upside.",
    "DuckDB paths with '=' must be quoted. Prefer this pack over ad-hoc SQL.",
    "suggested_conviction is support for a named course, not mix and not a probability. Keep conflicts visible.",
    "rr is leftover / max(52w range, 15). High = unused upside vs already-paid range. Not a 0-100 score.",
    "Do not write _tmp_*.py under logs/. Use inspect / lookup / compare / stance.",
]


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _f(value)
    if number is None:
        return None
    return round(number, digits)


def _compact_name(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "symbol",
        "company",
        "exchange",
        "ind",
        "close",
        "mcap",
        "day",
        "d5",
        "m1",
        "rsi",
        "rng",
        "vs50",
        "left",
        "street_left",
        "edge_left",
        "bo",
        "cont",
        "fwd",
        "exh",
        "frag",
        "opp",
        "dte",
        "mtp",
        "mix",
        "conv",
        "regime",
        "pe",
        "opm",
        "rr",
    )
    return {k: row.get(k) for k in keys if row.get(k) is not None}


def _merge_names(
    af_rows: list[dict[str, Any]],
    profiles: dict[str, dict[str, float | None]],
    conviction: dict[str, dict[str, Any]],
    regime: dict[str, dict[str, Any]],
    edge: dict[str, dict[str, Any]],
    mtp: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    names: dict[str, dict[str, Any]] = {}
    for row in af_rows:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        rec = dict(row)
        rec.pop("_af_aliases", None)
        prof = profiles.get(symbol) or {}
        rec["bo"] = _round(prof.get("breakout_long_v1"), 2)
        rec["cont"] = _round(prof.get("quality_continuation_v1"), 2)
        rec["fwd"] = _round(prof.get("forward_edge_active_v2"), 2)
        rec["early"] = _round(prof.get("early_momentum_inflection_v1"), 2)
        rec["sms"] = _round(prof.get("sustained_momentum_safety_v1"), 2)
        rec["frag"] = _round(prof.get("fragility_short"), 2)
        rec["exh"] = _round(prof.get("mean_reversion_exhaustion_v1"), 2)
        conv = conviction.get(symbol) or {}
        rec["mix"] = conv.get("manager_action_signal")
        rec["conv"] = _round(conv.get("conviction_score"), 3)
        rec["weeks_ras"] = _round(conv.get("weeks_ras"), 3)
        rec["months_ras"] = _round(conv.get("months_ras"), 3)
        rec["entry"] = conv.get("entry_readiness")
        rec["excl"] = conv.get("exclusion_reason")
        reg = regime.get(symbol) or {}
        rec["regime"] = _round(reg.get("regime_fit_score"), 1)
        rec["tier"] = reg.get("active_mgmt_tier")
        edge_row = edge.get(symbol) or {}
        rec["edge_left"] = _round(edge_row.get("forward_valuation_upside_pct"), 1)
        rec["opp"] = _f(edge_row.get("upside_opportunity_rank"))
        rec["lean"] = edge_row.get("directional_lean")
        rec["left"] = _round(
            leftover_pct(rec.get("close"), rec.get("pt"), rec.get("edge_left")), 1
        )
        rec["rr"] = reward_risk(rec)
        rec["mtp"] = (mtp.get(symbol) or {}).get("action")
        rec["mtp_setup"] = (mtp.get(symbol) or {}).get("primary_setup")
        for key in (
            "close",
            "mcap",
            "day",
            "d5",
            "m1",
            "rsi",
            "rng",
            "vs50",
            "pe",
            "opm",
            "dte",
        ):
            rec[key] = _round(
                rec.get(key), 1 if key in {"rsi", "rng", "dte", "left"} else 2
            )
        names[symbol] = rec
    return names


def _book_rows(
    holdings_csv: dict[str, dict[str, Any]],
    names: dict[str, dict[str, Any]],
    priors: dict[str, Any],
) -> list[dict[str, Any]]:
    book: list[dict[str, Any]] = []
    for matched, row in holdings_csv.items():
        symbol = str(row.get("matched_symbol") or matched)
        ticker = str(row.get("config_ticker") or symbol.split(":")[-1])
        name = names.get(symbol) or names.get(matched) or {}
        vs_cost = _f(row.get("unrealized_return_pct"))
        close = _f(row.get("close")) or _f(name.get("close"))
        vs50 = _f(name.get("vs50"))
        lost_sma50 = vs50 is not None and vs50 < 0
        naive = map_mix_to_naive_book_action(
            str(name.get("mix") or row.get("consensus_weeks_manager_action") or ""),
            mtp=name.get("mtp"),
            leftover=_f(name.get("left")),
            rsi=_f(name.get("rsi")),
            rng=_f(name.get("rng")),
        )
        prior = prior_action_for(priors, ticker) or prior_action_for(priors, symbol)
        cont = continuity_action(
            prior=prior,
            naive=naive,
            close=close,
            vs_cost_pct=vs_cost,
            weeks_ras=_f(name.get("weeks_ras")) or _f(row.get("consensus_weeks_ras")),
            months_ras=_f(name.get("months_ras"))
            or _f(row.get("consensus_months_ras")),
            lost_sma50=lost_sma50,
            leftover=_f(name.get("left")),
            rsi=_f(name.get("rsi")),
            rng=_f(name.get("rng")),
            dte=_f(name.get("dte")),
        )
        book.append(
            {
                "symbol": symbol,
                "ticker": ticker,
                "wt": _round(row.get("portfolio_weight_pct"), 2),
                "vs_cost": _round(vs_cost, 1),
                "value_usd": _round(row.get("current_value_usd"), 0),
                "close": _round(close, 2),
                "mix": name.get("mix") or row.get("consensus_weeks_manager_action"),
                "mtp": name.get("mtp"),
                "left": name.get("left"),
                "bo": name.get("bo"),
                "dte": name.get("dte"),
                "rsi": name.get("rsi"),
                "continuity": cont,
                "event_alert": name.get("dte") is not None and name["dte"] <= 2,
            }
        )
    book.sort(key=lambda r: -(r.get("wt") or 0))
    return book


def _sleeve_lists(
    names: dict[str, dict[str, Any]], book_symbols: set[str]
) -> dict[str, list[dict[str, Any]]]:
    unpaid = []
    forming = []
    paid = []
    cont_unpaid = []
    shorts = []
    for rec in names.values():
        compact = _compact_name(rec)
        if unpaid_eligible(rec, book_symbols=book_symbols):
            unpaid.append(compact)
        if forming_eligible(rec):
            forming.append(compact)
        if continuation_paid(rec):
            paid.append(compact)
        if continuation_unpaid(rec):
            cont_unpaid.append(compact)
        if short_limited_upside(rec) and rec.get("symbol") not in book_symbols:
            shorts.append(compact)
    unpaid.sort(key=unpaid_sort_key)
    forming.sort(key=lambda r: (_f(r.get("rng")) or 99, -(_f(r.get("left")) or 0)))
    paid.sort(key=lambda r: (-(_f(r.get("bo")) or 0), -(_f(r.get("rsi")) or 0)))
    cont_unpaid.sort(key=unpaid_sort_key)
    shorts.sort(
        key=lambda r: (
            0 if (_f(r.get("day")) or 0) < 3 else 1,
            -(_f(r.get("frag")) or 0),
            _f(r.get("d5")) or 0,
            abs(_f(r.get("left")) or 0),
        )
    )
    seen: set[str] = set()
    radar_pool: list[dict[str, Any]] = []
    for row in unpaid + cont_unpaid:
        symbol = str(row.get("symbol") or "")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        radar_pool.append(row)
    radar_curated, radar_overflow = dedupe_by_industry(
        radar_pool, cap=RADAR_INDUSTRY_CAP, book_symbols=book_symbols
    )
    shorts_curated, shorts_overflow = dedupe_by_industry(
        shorts, cap=SHORT_INDUSTRY_CAP, book_symbols=book_symbols
    )
    return {
        "unpaid": unpaid[:80],
        "continuation_unpaid": cont_unpaid[:40],
        "forming": forming[:25],
        "continuation_paid": paid[:20],
        "shorts_limited_upside": shorts[:20],
        "radar_upside_100": radar_pool[:100],
        "radar_curated_25": radar_curated[:25],
        "radar_industry_overflow": radar_overflow,
        "short_book_15": shorts[:15],
        "short_book_15_curated": shorts_curated[:15],
        "short_book_15_overflow": shorts_overflow,
    }


def _mtp_climate(mtp_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(r.get("action") or "") for r in mtp_rows.values())
    bounce = [
        {"symbol": s, "action": r.get("action"), "setup": r.get("primary_setup")}
        for s, r in mtp_rows.items()
        if str(r.get("action") or "") in MTP_BOUNCE_ACTIONS
    ]
    avoid = [
        {"symbol": s, "action": r.get("action")}
        for s, r in mtp_rows.items()
        if str(r.get("action") or "") in MTP_AVOID_ACTIONS
    ]
    return {
        "n": len(mtp_rows),
        "counts": dict(counts),
        "avoid": avoid,
        "enter_bounce_n": len(bounce),
        "note": "ENTER_SMALL/PROBE is climate, not a buy list. Do not open crashed ADR names from this list.",
    }


def _event_quality(row: Mapping[str, Any], book_symbols: set[str]) -> bool:
    symbol = str(row.get("symbol") or "")
    if symbol in book_symbols:
        return True
    leftover = _f(row.get("left"))
    if leftover is not None and leftover > 90:
        return False
    if leftover is not None and leftover < -25:
        return False
    return bool(profile_live(row) or short_limited_upside(row))


def _earnings_lanes(
    names: dict[str, dict[str, Any]],
    book_symbols: set[str],
) -> dict[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for rec in names.values():
        dte = rec.get("dte")
        if dte is None or dte < -1 or dte > 60:
            continue
        if not is_us_listed(rec):
            continue
        if (_f(rec.get("mcap")) or 0) < 2_000_000_000:
            continue
        compact = _compact_name(rec)
        event = classify_event_play(rec, in_book=str(rec.get("symbol")) in book_symbols)
        compact["play"] = event.get("play")
        compact["window"] = event.get("window")
        compact["rr"] = rec.get("rr")
        rows.append(compact)
    rows.sort(
        key=lambda r: (
            r.get("dte") if r.get("dte") is not None else 99,
            -(_f(r.get("left")) or 0),
        )
    )

    def _bucket(lo: int, hi: int, *, quality: bool = False) -> list[dict[str, Any]]:
        out = [r for r in rows if r.get("dte") is not None and lo <= r["dte"] <= hi]
        if quality:
            out = [r for r in out if _event_quality(r, book_symbols)]
        return out

    upside_plays = {
        "BUY_PRE",
        "BUILD_TO_SELL",
        "BUY_THE_RUMOUR",
        "HOLD_THROUGH",
    }
    down_plays = {
        "SHORT_PRE",
        "WATCH_SHORT",
        "SELL_THE_NEWS",
        "WATCH_SELL_NEWS",
        "DERISK_INTO_PRINT",
    }
    quality_rows = [r for r in rows if _event_quality(r, book_symbols)]
    upside = [r for r in quality_rows if r.get("play") in upside_plays][:40]
    downside = [r for r in quality_rows if r.get("play") in down_plays][:30]
    upside_curated, upside_overflow = dedupe_by_industry(
        upside, cap=EARNINGS_INDUSTRY_CAP, book_symbols=book_symbols
    )
    downside_curated, downside_overflow = dedupe_by_industry(
        downside, cap=EARNINGS_INDUSTRY_CAP, book_symbols=book_symbols
    )
    return {
        "all_0_60d": rows[:120],
        "print_week_0_7d": _bucket(0, 7, quality=True)[:40],
        "near_8_21d": _bucket(8, 21, quality=True)[:40],
        "rumour_22_60d": _bucket(22, 60, quality=True)[:40],
        "upside": upside,
        "downside": downside,
        "upside_curated": upside_curated[:12],
        "upside_overflow": upside_overflow,
        "downside_curated": downside_curated[:12],
        "downside_overflow": downside_overflow,
    }


def _attach_sleeve_tags(
    rows: list[dict[str, Any]] | None,
    stances: Mapping[str, Mapping[str, Any]],
) -> None:
    """Join stance.py's per-symbol sleeve tags onto compact rows in place.

    Lets a canvas/agent cite cross-sleeve confirmation without re-joining
    `stances` against every sleeve/earnings bucket by hand.
    """
    for row in rows or []:
        symbol = str(row.get("symbol") or "")
        tags = list((stances.get(symbol) or {}).get("sleeves") or [])
        row["sleeve_tags"] = tags
        row["sleeve_count"] = len(tags)


def compile_briefing_pack(
    *,
    output_root: Path | None = None,
    prediction_run_id: str | None = None,
    all_fields_run_id: str | None = None,
) -> dict[str, Any]:
    sources = lock_sources(
        prediction_run_id=prediction_run_id,
        all_fields_run_id=all_fields_run_id,
    )
    edge = load_csv_by_symbol(sources.edge_opportunity_csv)
    mtp = load_csv_by_symbol(sources.mtp_policy_csv)
    holdings = load_csv_by_symbol(
        sources.holdings_summary_csv, symbol_key="matched_symbol"
    )
    if not holdings:
        holdings = load_csv_by_symbol(
            sources.holdings_summary_csv, symbol_key="config_ticker"
        )
    if sources.holdings_run_dir is not None:
        etf_csv = (
            sources.holdings_run_dir
            / "scans"
            / "etf_active_book_v1"
            / "holdings__summary.csv"
        )
        for symbol, row in load_csv_by_symbol(
            etf_csv, symbol_key="matched_symbol"
        ).items():
            holdings.setdefault(symbol, row)
    priors = load_prior_actions(sources.priors_path)
    extra_symbols = list(holdings.keys()) + list(mtp.keys())
    af_rows = extract_all_fields(sources, extra_symbols=extra_symbols)
    profiles = extract_profile_weeks(sources)
    conviction = extract_conviction(sources)
    regime = extract_regime(sources)

    names = _merge_names(af_rows, profiles, conviction, regime, edge, mtp)
    book = _book_rows(holdings, names, priors)
    book_symbols = {str(r["symbol"]) for r in book}
    sleeves = _sleeve_lists(names, book_symbols)
    earnings_lanes = _earnings_lanes(names, book_symbols)

    watch = set(book_symbols)
    for bucket_name in ("radar_upside_100", "short_book_15", "continuation_paid"):
        for row in sleeves.get(bucket_name) or []:
            symbol = str(row.get("symbol") or "")
            if symbol:
                watch.add(symbol)
    for row in (earnings_lanes.get("all_0_60d") or [])[:50]:
        symbol = str(row.get("symbol") or "")
        if symbol:
            watch.add(symbol)

    try:
        progression = extract_progression(sources, sorted(watch))
    except Exception as exc:  # noqa: BLE001
        progression = {"_error": str(exc)}
    book_by_symbol = {str(r["symbol"]): r for r in book}
    stances: dict[str, dict[str, Any]] = {}
    for symbol in sorted(watch):
        rec = names.get(symbol)
        if not rec:
            continue
        book_row = book_by_symbol.get(symbol)
        prog = progression.get(symbol) if isinstance(progression, dict) else None
        delta = (
            (prog or {}).get("delta_vs_prior_run") if isinstance(prog, dict) else None
        )
        stances[symbol] = suggest_stance(
            rec,
            in_book=symbol in book_symbols,
            continuity=(book_row or {}).get("continuity") if book_row else None,
            delta=delta if isinstance(delta, dict) else None,
        )
    suggested_courses = rank_suggested_courses(stances)
    for bucket in sleeves.values():
        if isinstance(bucket, list):
            _attach_sleeve_tags(bucket, stances)
    for bucket in earnings_lanes.values():
        if isinstance(bucket, list):
            _attach_sleeve_tags(bucket, stances)
    us2b = [r for r in af_rows if (_f(r.get("mcap")) or 0) >= 2_000_000_000]
    created = datetime.now(tz=timezone.utc)
    run_id = (
        f"briefing_pack_{created.strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"
    )
    regime = {
        "us_2b": universe_stats(af_rows, min_mcap=2_000_000_000),
        "us_500m": universe_stats(af_rows, min_mcap=500_000_000),
        "industries_5d": industry_breadth(af_rows, min_mcap=2_000_000_000)[:12],
        "industries_5d_laggards": list(
            reversed(industry_breadth(af_rows, min_mcap=2_000_000_000)[-8:])
        ),
    }
    mtp_climate = _mtp_climate(mtp)
    pack = {
        "schema_version": "operator_briefing_pack_v1",
        "run_id": run_id,
        "created_at_utc": created.isoformat(),
        "sources": sources.as_dict(),
        "gotchas": GOTCHAS,
        "regime": regime,
        "mtp": mtp_climate,
        "book": book,
        "sleeves": sleeves,
        "earnings_0_21d": (earnings_lanes.get("print_week_0_7d") or [])
        + (earnings_lanes.get("near_8_21d") or []),
        "earnings_lanes": earnings_lanes,
        "progression": progression,
        "names": {s: _compact_name(names[s]) for s in sorted(watch) if s in names},
        "stances": stances,
        "suggested_courses": suggested_courses,
        "primary_course": summarize_operator_course(
            stances,
            regime=regime,
            mtp=mtp_climate,
        ),
        "counts": {
            "af_rows": len(af_rows),
            "us_2b": len(us2b),
            "watch": len(watch),
            "book": len(book),
        },
    }
    root = Path(output_root or DEFAULT_OUTPUT_ROOT)
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    json_path = run_dir / "briefing_pack.json"
    md_path = run_dir / "briefing_pack.md"
    json_path.write_text(json.dumps(pack, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(pack), encoding="utf-8")
    pack["output"] = {
        "json": json_path.as_posix(),
        "md": md_path.as_posix(),
        "run_dir": run_dir.as_posix(),
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {"run_id": run_id, "json": json_path.name, "md": md_path.name}, indent=2
        ),
        encoding="utf-8",
    )
    return pack


def render_markdown(pack: dict[str, Any]) -> str:
    src = pack.get("sources") or {}
    regime = pack.get("regime") or {}
    us = regime.get("us_2b") or {}
    lines = [
        f"# Operator briefing pack `{pack.get('run_id')}`",
        "",
        f"Created {pack.get('created_at_utc')}",
        "",
        "## Source lock",
        f"- pred `{src.get('prediction_run_id')}` @ {src.get('prediction_created_at_utc')}",
        f"- all-fields `{src.get('all_fields_run_id')}` day `{src.get('all_fields_day_label')}`",
        f"- MTP `{src.get('mtp_dir')}`",
        f"- holdings `{src.get('holdings_run_dir')}`",
        f"- priors `{src.get('priors_path')}`",
        "",
        "## Regime (US mcap >= $2B)",
        f"- n={us.get('n')} day={us.get('day')} pct_up={us.get('pct_up')} 5D={us.get('d5')} 1M={us.get('m1')} RSI={us.get('rsi')}",
        "",
        "### 5D industry leaders",
    ]
    for row in (regime.get("industries_5d") or [])[:8]:
        lines.append(
            f"- {row.get('ind')}: 5D {row.get('d5')} / 1M {row.get('m1')} / RSI {row.get('rsi')} / {row.get('pct_up')}% up (n={row.get('n')})"
        )
    lines += ["", "### 5D laggards"]
    for row in regime.get("industries_5d_laggards") or []:
        lines.append(
            f"- {row.get('ind')}: 5D {row.get('d5')} / 1M {row.get('m1')} / RSI {row.get('rsi')}"
        )
    mtp = pack.get("mtp") or {}
    lines += [
        "",
        "## MTP climate",
        f"- n={mtp.get('n')} counts={mtp.get('counts')} avoid={mtp.get('avoid')}",
        f"- {mtp.get('note')}",
        "",
        "## Book continuity",
    ]
    for row in pack.get("book") or []:
        cont = row.get("continuity") or {}
        lines.append(
            f"- {row.get('ticker')} wt {row.get('wt')} vs-cost {row.get('vs_cost')}% "
            f"-> {cont.get('action')} (prior {cont.get('prior_action')}, naive {cont.get('naive_action')}"
            f"{', POLAR BLOCKED' if cont.get('polar_blocked') else ''})"
        )
    radar_curated = (pack.get("sleeves") or {}).get("radar_curated_25") or []
    radar_overflow = (pack.get("sleeves") or {}).get("radar_industry_overflow") or []
    lines += [
        "",
        f"## Top 100 radar \u2014 curated headline (industry-capped, {len(radar_overflow)} names held back to industry cap; see radar_upside_100 for the full appendix)",
        "",
    ]
    lines.append("| symbol | left | rr | bo | rsi | rng | opp | mix | sleeves |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---|---:|")
    for row in radar_curated[:25]:
        lines.append(
            f"| {row.get('symbol')} | {row.get('left')} | {row.get('rr')} | {row.get('bo')} | "
            f"{row.get('rsi')} | {row.get('rng')} | {row.get('opp')} | {row.get('mix')} | {row.get('sleeve_count')} |"
        )
    short_curated = (pack.get("sleeves") or {}).get("short_book_15_curated") or []
    short_overflow = (pack.get("sleeves") or {}).get("short_book_15_overflow") or []
    lines += [
        "",
        f"## Short book (15, limited leftover, curated; {len(short_overflow)} names held back to industry cap)",
        "",
    ]
    for row in short_curated:
        lines.append(
            f"- {row.get('symbol')} left {row.get('left')} 5D {row.get('d5')} day {row.get('day')} rsi {row.get('rsi')} mtp {row.get('mtp')}"
        )
    lines += ["", "## Forming", ""]
    for row in ((pack.get("sleeves") or {}).get("forming") or [])[:12]:
        lines.append(
            f"- {row.get('symbol')} rng {row.get('rng')} left {row.get('left')} rsi {row.get('rsi')} bo {row.get('bo')}"
        )
    lines += ["", "## Paid continuation (do not chase)", ""]
    for row in ((pack.get("sleeves") or {}).get("continuation_paid") or [])[:12]:
        lines.append(
            f"- {row.get('symbol')} RSI {row.get('rsi')} rng {row.get('rng')} left {row.get('left')} bo {row.get('bo')}"
        )
    lines += ["", "## Shorts with limited leftover", ""]
    for row in ((pack.get("sleeves") or {}).get("shorts_limited_upside") or [])[:12]:
        lines.append(
            f"- {row.get('symbol')} left {row.get('left')} 5D {row.get('d5')} day {row.get('day')} mtp {row.get('mtp')}"
        )
    lanes = pack.get("earnings_lanes") or {}
    lines += ["", "## Earnings lanes (US $2B, quality filter)", ""]
    for label, key in (
        ("0-7d print week", "print_week_0_7d"),
        ("8-21d this/next week", "near_8_21d"),
        ("22-60d late Sep / next month rumour", "rumour_22_60d"),
        (
            "upside plays, curated (BUY_PRE / BUILD / RUMOUR / HOLD_THROUGH)",
            "upside_curated",
        ),
        (
            "downside plays, curated (SHORT_PRE / WATCH_SHORT / SELL_NEWS / DERISK)",
            "downside_curated",
        ),
    ):
        lines.append(f"### {label}")
        for row in (lanes.get(key) or [])[:12]:
            lines.append(
                f"- dte {row.get('dte')} {row.get('play')} {row.get('symbol')} "
                f"left {row.get('left')} rr {row.get('rr')} rsi {row.get('rsi')} bo {row.get('bo')}"
            )
    primary = pack.get("primary_course") or {}
    lines += [
        "",
        "## Suggested course (support, not a probability)",
        f"- bias: {primary.get('bias')}",
        f"- {primary.get('note')}",
        "",
        "### Do",
    ]
    for item in primary.get("do") or []:
        lines.append(f"- {item}")
    lines += ["", "### Do not"]
    for item in primary.get("do_not") or []:
        lines.append(f"- {item}")
    courses = pack.get("suggested_courses") or {}
    lines += [
        "",
        "### Ranked NEW (pack watch, leftover+profile, not a 0-100 composite)",
    ]
    for row in (courses.get("new") or [])[:8]:
        lines.append(
            f"- {row.get('rank_in_bucket')} {row.get('symbol')} {row.get('stance')} "
            f"support {row.get('suggested_conviction')} — {row.get('course')}"
        )
    lines += ["", "### Ranked ADD"]
    for row in (courses.get("add") or [])[:8]:
        lines.append(
            f"- {row.get('rank_in_bucket')} {row.get('symbol')} support {row.get('suggested_conviction')} — {row.get('course')}"
        )
    lines += ["", "### Ranked TRIM/EXIT/DERISK"]
    for row in (courses.get("trim_exit") or [])[:8]:
        lines.append(
            f"- {row.get('rank_in_bucket')} {row.get('symbol')} {row.get('stance')} support {row.get('suggested_conviction')}"
        )
    lines += ["", "### Ranked SHORT_WAIT"]
    for row in (courses.get("short_wait") or [])[:6]:
        lines.append(
            f"- {row.get('rank_in_bucket')} {row.get('symbol')} support {row.get('suggested_conviction')} — {row.get('course')}"
        )
    lines += ["", "## Gotchas"]
    for item in pack.get("gotchas") or []:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)
