"""Industry-pack reports from a move-prediction DuckDB run (§10.4 active management).

Splits a global ``run_full_analysis_suite_duckdb`` result into per-industry folders
with peer-local top-N rankings for theme hunting ("find the next Micron").
"""

from __future__ import annotations

import collections
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from data_analysis_scripts._shared_analysis_utils import (
    build_report_title,
    coerce_numeric,
    format_market_cap,
    reset_log_file,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    DEFAULT_HORIZON_WEIGHTS,
    HORIZON_TITLES,
    TOP_SECTION_ROWS,
    _build_consensus_scores,
    _format_multiple,
    _format_percent,
    _format_score,
    _get_company_name,
    _get_symbol_name,
    _sanitize_industry_folder_name,
)
from generic_utils.log_to_files_util import log_to_file

INDUSTRY_PACK_TOP_N = TOP_SECTION_ROWS
DEFAULT_SCORE_HORIZON = "weeks"
DEFAULT_PERF_RANK_FIELD = "Perf.1M"
PERF_RANK_FIELDS = ("Perf.W", "Perf.1M", "Perf.YTD")
SEARCH_NAME_MAX_LEN = 15
BARE_SYMBOL_MAX_LEN = 10
FULL_SYMBOL_MAX_LEN = 18

# Short-term liquidity gate: cash + short-term investments / short-term debt.
STSAFE_PASS_RATIO = 1.0
STSAFE_WATCH_RATIO = 0.75


def _bare_symbol_from_row(row: Mapping[str, Any]) -> str:
    symbol = str(row.get("symbol") or "").strip()
    if ":" in symbol:
        return symbol.split(":", 1)[-1][:BARE_SYMBOL_MAX_LEN]
    return _get_symbol_name(row)[:BARE_SYMBOL_MAX_LEN]


def _full_qualified_symbol(row: Mapping[str, Any]) -> str:
    symbol = str(row.get("symbol") or "").strip()
    if symbol:
        return symbol[:FULL_SYMBOL_MAX_LEN]
    return _get_symbol_name(row)[:FULL_SYMBOL_MAX_LEN]


def _search_name(row: Mapping[str, Any], *, max_len: int = SEARCH_NAME_MAX_LEN) -> str:
    """Compact company label for search when the local ticker is not on your main exchange."""
    name = _get_company_name(row)
    cleaned = " ".join(str(name).split())
    if not cleaned or cleaned == "N/A":
        return _bare_symbol_from_row(row)[:max_len]
    return cleaned[:max_len]


def _short_term_cash_coverage(row: Mapping[str, Any]) -> float | None:
    cash_fy = coerce_numeric(row.get("cash_n_short_term_invest_fy"))
    debt_fy = coerce_numeric(row.get("short_term_debt_fy"))
    if cash_fy is not None and debt_fy not in (None, 0):
        return cash_fy / debt_fy
    cash_fq = coerce_numeric(row.get("cash_n_short_term_invest_fq"))
    debt_fq = coerce_numeric(row.get("short_term_debt_fq"))
    if cash_fq is not None and debt_fq not in (None, 0):
        return cash_fq / debt_fq
    return None


def _stsafe_label(ratio: float | None) -> str:
    if ratio is None:
        return "N/A"
    if ratio >= STSAFE_PASS_RATIO:
        return "PASS"
    if ratio >= STSAFE_WATCH_RATIO:
        return "WATCH"
    return "FAIL"


def _resolve_regime_by_symbol(
    duckdb_run_result: Mapping[str, Any],
) -> dict[str, Any]:
    overlay = duckdb_run_result.get("_regime_context_overlay")
    if overlay is None:
        return {}
    by_symbol: dict[str, Any] = {}
    for symbol, record in getattr(overlay, "by_symbol", {}).items():
        by_symbol[str(symbol)] = record
        bare = str(symbol).split(":", 1)[-1]
        by_symbol.setdefault(bare, record)
    return by_symbol


def _lookup_regime_record(
    row: Mapping[str, Any],
    regime_by_symbol: Mapping[str, Any],
) -> Any | None:
    if not regime_by_symbol:
        return None
    symbol = str(row.get("symbol") or "").strip()
    bare = _bare_symbol_from_row(row)
    return (
        regime_by_symbol.get(symbol)
        or regime_by_symbol.get(bare)
        or regime_by_symbol.get(_get_symbol_name(row))
    )


def _safety_gate_summary(
    row: Mapping[str, Any],
    consensus_row: Mapping[str, Any],
    regime_record: Any | None,
) -> str:
    flags: list[str] = []
    st_ratio = _short_term_cash_coverage(row)
    st_label = _stsafe_label(st_ratio)
    if st_label == "FAIL":
        flags.append("StSafe")
    safety = coerce_numeric((consensus_row.get("components") or {}).get("safety"))
    if safety is not None and safety < 0:
        flags.append("Saf-")
    warn_count = int(getattr(regime_record, "warning_flag_count", 0) or 0)
    if warn_count >= 2:
        flags.append(f"Warn{warn_count}")
    risk_tier = str(
        ((consensus_row.get("horizons") or {}).get(DEFAULT_SCORE_HORIZON) or {}).get(
            "risk_tier", ""
        )
    )
    if risk_tier == "high-risk":
        flags.append("HiRisk")
    return ",".join(flags) if flags else "ok"


def _resolve_profile_suite_from_run_result(
    duckdb_run_result: Mapping[str, Any],
    profile_names: Sequence[str] | None,
) -> dict[str, Any]:
    from data_analysis_scripts.trading_view_move_prediction_profile_config import (
        resolve_profile_suite,
    )

    suite_path = duckdb_run_result.get("_duckdb_profile_suite_path")
    resolved_names = list(
        profile_names
        or duckdb_run_result.get("_duckdb_profile_names")
        or []
    )
    return resolve_profile_suite(suite_path, resolved_names or None)


def _resolve_consensus_rows(
    duckdb_run_result: Mapping[str, Any],
    consensus_rows: Sequence[Mapping[str, Any]] | None,
    profile_names: Sequence[str] | None,
) -> list[dict[str, Any]]:
    if consensus_rows is not None:
        return [dict(row) for row in consensus_rows]

    stored_rows = duckdb_run_result.get("_consensus_rows")
    if stored_rows:
        return [dict(row) for row in stored_rows]

    profile_suite = _resolve_profile_suite_from_run_result(
        duckdb_run_result, profile_names
    )
    scan_rows = [
        dict(row)
        for row in _collect_scan_rows_from_consensus(duckdb_run_result)
    ]
    if not scan_rows:
        raise ValueError(
            "consensus_rows not found on duckdb_run_result and no scan rows "
            "available to rebuild consensus scores"
        )

    return _build_consensus_scores(
        scan_rows,
        profile_names=list(profile_suite["profile_names"]),
        profile_registry=profile_suite["registry"],
        consensus_profile_weights=profile_suite["consensus_profile_weights"],
        inverted_consensus_profiles=profile_suite["inverted_consensus_profiles"],
        long_consensus_profiles=profile_suite["long_consensus_profiles"],
    )


def _collect_scan_rows_from_consensus(
    duckdb_run_result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    consensus_rows = duckdb_run_result.get("_consensus_rows")
    if consensus_rows:
        return [row["row"] for row in consensus_rows]
    return []


def _industry_key(row: Mapping[str, Any], group_field: str) -> str:
    value = row.get(group_field)
    key = str(value).strip() if value else ""
    return key or "Unknown"


def _group_consensus_rows(
    consensus_rows: Sequence[Mapping[str, Any]],
    group_field: str,
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for consensus_row in consensus_rows:
        row = consensus_row.get("row") or {}
        groups[_industry_key(row, group_field)].append(dict(consensus_row))
    return dict(groups)


def _peer_rescore_group(
    group_rows: Sequence[Mapping[str, Any]],
    profile_suite: Mapping[str, Any],
) -> list[dict[str, Any]]:
    scan_rows = [deepcopy(row["row"]) for row in group_rows]
    return _build_consensus_scores(
        scan_rows,
        profile_names=list(profile_suite["profile_names"]),
        profile_registry=profile_suite["registry"],
        consensus_profile_weights=profile_suite["consensus_profile_weights"],
        inverted_consensus_profiles=profile_suite["inverted_consensus_profiles"],
        long_consensus_profiles=profile_suite["long_consensus_profiles"],
    )


def _horizon_score(
    consensus_row: Mapping[str, Any], horizon_name: str
) -> float | None:
    horizon = consensus_row.get("horizons", {}).get(horizon_name, {})
    return coerce_numeric(horizon.get("score"))


def _perf_value(row: Mapping[str, Any], perf_field: str) -> float | None:
    return coerce_numeric(row.get(perf_field))


def _top_profile_signals(
    consensus_row: Mapping[str, Any],
    horizon_name: str,
    *,
    limit: int = 3,
) -> str:
    profile_scores = consensus_row.get("profile_scores") or {}
    scored_profiles: list[tuple[str, float]] = []
    for profile_name, horizons in profile_scores.items():
        score = coerce_numeric(horizons.get(horizon_name, {}).get("score"))
        if score is None:
            continue
        scored_profiles.append((profile_name, score))
    scored_profiles.sort(key=lambda item: item[1], reverse=True)
    if not scored_profiles:
        return "N/A"
    return ", ".join(
        f"{name}={score:+.2f}" for name, score in scored_profiles[:limit]
    )


def _log_ranked_table_header(
    log_file: Path,
    *,
    title: str,
    score_horizon: str,
    ranking_basis: str,
) -> None:
    horizon_title = HORIZON_TITLES.get(score_horizon, score_horizon.title())
    log_to_file(log_file, title)
    log_to_file(log_file, "=" * 160)
    log_to_file(
        log_file,
        f"Ranking basis: {ranking_basis} | score horizon: {horizon_title}",
    )
    log_to_file(
        log_file,
        "Sym = local ticker | Name = searchable company label (15 chars) | "
        "FQSym = full exchange:symbol for lookup. StSafe = cash+ST investments / "
        "short-term debt (PASS >=1.0x). Saf = consensus safety component. "
        "Warn = regime trap-field flags (0-5). Gates = quick composite.",
    )
    log_to_file(log_file, "")
    log_to_file(
        log_file,
        f"{'Rank':<5} {'Sym':<10} {'Name':<15} {'FQSym':<18} {'Score':>7} {'RAdj':>7} "
        f"{'StSafe':>7} {'Saf':>6} {'Warn':>5} {'Gates':<10} {'Risk':<12} "
        f"{'Action':<16} {'Perf.1M':>8} {'Perf.W':>8} {'MCap':>12}",
    )
    log_to_file(log_file, "-" * 160)


def _log_ranked_row(
    log_file: Path,
    rank: int,
    consensus_row: Mapping[str, Any],
    *,
    score_horizon: str,
    regime_by_symbol: Mapping[str, Any],
) -> None:
    row = consensus_row.get("row") or {}
    horizon_data = consensus_row.get("horizons", {}).get(score_horizon, {})
    regime_record = _lookup_regime_record(row, regime_by_symbol)
    st_ratio = _short_term_cash_coverage(row)
    safety = coerce_numeric((consensus_row.get("components") or {}).get("safety"))
    warn_count = int(getattr(regime_record, "warning_flag_count", 0) or 0)
    log_to_file(
        log_file,
        f"{rank:<5} {_bare_symbol_from_row(row):<10} "
        f"{_search_name(row):<15} {_full_qualified_symbol(row):<18} "
        f"{_format_score(coerce_numeric(horizon_data.get('score'))):>7} "
        f"{_format_score(coerce_numeric(horizon_data.get('risk_adjusted_score'))):>7} "
        f"{_format_multiple(st_ratio):>7} "
        f"{_format_score(safety):>6} "
        f"{warn_count:>5} "
        f"{_safety_gate_summary(row, consensus_row, regime_record):<10} "
        f"{str(horizon_data.get('risk_tier', 'N/A'))[:12]:<12} "
        f"{str(consensus_row.get('manager_action_signal', 'N/A'))[:16]:<16} "
        f"{_format_percent(_perf_value(row, 'Perf.1M')):>8} "
        f"{_format_percent(_perf_value(row, 'Perf.W')):>8} "
        f"{format_market_cap(coerce_numeric(consensus_row.get('market_cap'))):>12}",
    )


def _write_group_ranked_by_score_log(
    log_file: Path,
    ranked_rows: Sequence[Mapping[str, Any]],
    *,
    group_label: str,
    score_horizon: str,
    top_n: int,
    peer_rescored: bool,
    regime_by_symbol: Mapping[str, Any],
) -> None:
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title(f"Industry pack — {group_label} — ranked by score"),
    )
    basis = (
        "peer-local consensus (MAD normalized within industry)"
        if peer_rescored
        else "global consensus scores (industry-local rank only)"
    )
    _log_ranked_table_header(
        log_file,
        title=(
            f"TOP {min(top_n, len(ranked_rows))} BY CONSENSUS SCORE — "
            f"{group_label.upper()}"
        ),
        score_horizon=score_horizon,
        ranking_basis=basis,
    )
    for rank, consensus_row in enumerate(ranked_rows[:top_n], 1):
        _log_ranked_row(
            log_file,
            rank,
            consensus_row,
            score_horizon=score_horizon,
            regime_by_symbol=regime_by_symbol,
        )
    _log_top_profile_detail_block(
        log_file, ranked_rows[:top_n], score_horizon=score_horizon
    )
    log_to_file(log_file, "")


def _log_top_profile_detail_block(
    log_file: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    score_horizon: str,
) -> None:
    if not rows:
        return
    log_to_file(log_file, "Profile detail (top rows)")
    log_to_file(log_file, "-" * 160)
    for consensus_row in rows[:10]:
        row = consensus_row.get("row") or {}
        log_to_file(
            log_file,
            f"  {_search_name(row):<15} {_full_qualified_symbol(row):<18} "
            f"profiles: {_top_profile_signals(consensus_row, score_horizon)} | "
            f"IndMCap%={_format_percent(coerce_numeric(row.get('_industry_market_cap_share')))} "
            f"IndRev%={_format_percent(coerce_numeric(row.get('_industry_revenue_share')))}",
        )


def _write_group_ranked_by_perf_log(
    log_file: Path,
    group_rows: Sequence[Mapping[str, Any]],
    *,
    group_label: str,
    perf_field: str,
    score_horizon: str,
    top_n: int,
    regime_by_symbol: Mapping[str, Any],
) -> None:
    perf_label = {
        "Perf.W": "1-week trailing return",
        "Perf.1M": "1-month trailing return",
        "Perf.YTD": "year-to-date trailing return",
    }.get(perf_field, perf_field)

    ranked = sorted(
        group_rows,
        key=lambda row: _perf_value(row.get("row") or {}, perf_field) or float("-inf"),
        reverse=True,
    )

    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title(
            f"Industry pack — {group_label} — ranked by {perf_field}"
        ),
    )
    _log_ranked_table_header(
        log_file,
        title=(
            f"TOP {min(top_n, len(ranked))} BY {perf_field} — "
            f"{group_label.upper()}"
        ),
        score_horizon=score_horizon,
        ranking_basis=perf_label,
    )
    for rank, consensus_row in enumerate(ranked[:top_n], 1):
        _log_ranked_row(
            log_file,
            rank,
            consensus_row,
            score_horizon=score_horizon,
            regime_by_symbol=regime_by_symbol,
        )
    _log_top_profile_detail_block(
        log_file, ranked[:top_n], score_horizon=score_horizon
    )
    log_to_file(log_file, "")


def _write_group_summary_log(
    log_file: Path,
    group_rows: Sequence[Mapping[str, Any]],
    *,
    group_label: str,
    group_field: str,
    score_horizon: str,
    top_n: int,
    peer_rescored: bool,
    run_id: str | None,
    regime_by_symbol: Mapping[str, Any],
) -> None:
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title(f"Industry pack summary — {group_label}"),
    )
    log_to_file(log_file, "=" * 160)
    if run_id:
        log_to_file(log_file, f"run_id: {run_id}")
    log_to_file(
        log_file,
        f"group_field={group_field} | names_in_group={len(group_rows)} | "
        f"peer_rescore={'yes' if peer_rescored else 'no'}",
    )
    log_to_file(
        log_file,
        "Purpose: theme-local peer review for tactical concentration — compare "
        "names within the same industry before sizing a theme trade.",
    )
    log_to_file(log_file, "")

    row_dicts = [row.get("row") or {} for row in group_rows]
    sector = next(
        (str(row.get("sector") or "").strip() for row in row_dicts if row.get("sector")),
        "N/A",
    )
    mcaps = [
        m
        for row in row_dicts
        if (m := coerce_numeric(row.get("market_cap_basic"))) is not None
    ]
    total_mcap = sum(mcaps) if mcaps else 0.0
    log_to_file(log_file, f"Sector: {sector}")
    log_to_file(log_file, f"Aggregate market cap: {format_market_cap(total_mcap)}")
    if mcaps:
        log_to_file(
            log_file,
            f"Median market cap: {format_market_cap(sorted(mcaps)[len(mcaps) // 2])}",
        )
    log_to_file(log_file, "")

    log_to_file(log_file, "Suggested safety gates for highlighted names")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        "  StSafe (cash+ST invest / ST debt): PASS >= 1.0x | WATCH 0.75-1.0x | FAIL < 0.75x",
    )
    log_to_file(
        log_file,
        "  Saf (consensus safety component): prefer >= 0 for tactical adds; negative = balance-sheet stress signal.",
    )
    log_to_file(
        log_file,
        "  RAdj: soft penalty only — can still rank high if momentum dominates. Compare RAdj vs raw Score.",
    )
    log_to_file(
        log_file,
        "  Warn (regime flags): counts trap-style field extremes (Stoch, Williams %R, oper_income, ebitda, Recommend.MA). "
        "Treat Warn >= 2 as demote/watchlist, not a hard veto alone.",
    )
    log_to_file(
        log_file,
        "  Gates column: composite quick filter — ok means none of StSafe/Saf-/Warn2+/high-risk fired.",
    )
    log_to_file(log_file, "")

    gate_failures = 0
    stsafe_missing = 0
    for consensus_row in group_rows:
        row = consensus_row.get("row") or {}
        regime_record = _lookup_regime_record(row, regime_by_symbol)
        if _safety_gate_summary(row, consensus_row, regime_record) != "ok":
            gate_failures += 1
        if _short_term_cash_coverage(row) is None:
            stsafe_missing += 1
    log_to_file(
        log_file,
        f"Group safety snapshot: {gate_failures}/{len(group_rows)} names fail composite gates | "
        f"{stsafe_missing} missing StSafe inputs",
    )
    log_to_file(log_file, "")

    horizon_names = list(DEFAULT_HORIZON_WEIGHTS.keys())
    log_to_file(log_file, "Consensus score breadth (global scores in this industry)")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        f"{'Horizon':<18} {'AvgScore':>10} {'Bullish%':>10} {'Bearish%':>10} {'Scored':>8}",
    )
    log_to_file(log_file, "-" * 160)
    for horizon_name in horizon_names:
        scores = [
            s
            for row in group_rows
            if (s := _horizon_score(row, horizon_name)) is not None
        ]
        if not scores:
            continue
        avg_score = sum(scores) / len(scores)
        bullish_pct = sum(1 for score in scores if score > 0) / len(scores) * 100
        bearish_pct = sum(1 for score in scores if score < 0) / len(scores) * 100
        horizon_title = HORIZON_TITLES.get(horizon_name, horizon_name.title())
        log_to_file(
            log_file,
            f"{horizon_title:<18} {avg_score:>+10.3f} {bullish_pct:>9.1f}% "
            f"{bearish_pct:>9.1f}% {len(scores):>8}",
        )
    log_to_file(log_file, "")

    for perf_field in PERF_RANK_FIELDS:
        perf_values = [
            p
            for row in row_dicts
            if (p := _perf_value(row, perf_field)) is not None
        ]
        if not perf_values:
            continue
        avg_perf = sum(perf_values) / len(perf_values)
        log_to_file(
            log_file,
            f"Avg {perf_field} (scan-time tape): {_format_percent(avg_perf)} "
            f"across {len(perf_values)} names",
        )
    log_to_file(log_file, "")

    score_sorted = sorted(
        group_rows,
        key=lambda row: _horizon_score(row, score_horizon) or float("-inf"),
        reverse=True,
    )
    perf_sorted = sorted(
        group_rows,
        key=lambda row: _perf_value(row.get("row") or {}, DEFAULT_PERF_RANK_FIELD)
        or float("-inf"),
        reverse=True,
    )

    log_to_file(log_file, f"Quick reference — top {min(5, top_n)} by score ({score_horizon})")
    log_to_file(log_file, "-" * 160)
    for rank, consensus_row in enumerate(score_sorted[: min(5, top_n)], 1):
        row = consensus_row.get("row") or {}
        score = _horizon_score(consensus_row, score_horizon)
        regime_record = _lookup_regime_record(row, regime_by_symbol)
        log_to_file(
            log_file,
            f"  {rank}. {_search_name(row):<15} {_full_qualified_symbol(row):<18} "
            f"score={_format_score(score)} "
            f"RAdj={_format_score(coerce_numeric(((consensus_row.get('horizons') or {}).get(score_horizon) or {}).get('risk_adjusted_score')))} "
            f"StSafe={_format_multiple(_short_term_cash_coverage(row))} "
            f"gates={_safety_gate_summary(row, consensus_row, regime_record)}",
        )
    log_to_file(log_file, "")

    log_to_file(
        log_file,
        f"Quick reference — top {min(5, top_n)} by {DEFAULT_PERF_RANK_FIELD} (scan tape)",
    )
    log_to_file(log_file, "-" * 160)
    for rank, consensus_row in enumerate(perf_sorted[: min(5, top_n)], 1):
        row = consensus_row.get("row") or {}
        perf = _perf_value(row, DEFAULT_PERF_RANK_FIELD)
        score = _horizon_score(consensus_row, score_horizon)
        log_to_file(
            log_file,
            f"  {rank}. {_search_name(row):<15} {_full_qualified_symbol(row):<18} "
            f"{DEFAULT_PERF_RANK_FIELD}={_format_percent(perf)} "
            f"score={_format_score(score)}",
        )
    log_to_file(log_file, "")


def _write_overview_log(
    log_file: Path,
    *,
    run_id: str | None,
    group_field: str,
    industry_outputs: Mapping[str, Mapping[str, Any]],
    score_horizon: str,
) -> None:
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title("Industry packs overview — theme-local peer sets"),
    )
    log_to_file(log_file, "=" * 160)
    if run_id:
        log_to_file(log_file, f"run_id: {run_id}")
    log_to_file(
        log_file,
        f"Industries exported: {len(industry_outputs)} | group_field={group_field} | "
        f"top_n={INDUSTRY_PACK_TOP_N}",
    )
    log_to_file(
        log_file,
        "Each industry folder contains: summary, ranked_by_score, ranked_by_perf_1m. "
        "Open the industry that matches your active theme before comparing global ranks.",
    )
    log_to_file(log_file, "")

    ranked_industries = []
    for group_label, meta in industry_outputs.items():
        avg_score = meta.get("avg_weeks_score")
        if avg_score is None:
            continue
        ranked_industries.append((group_label, meta))

    ranked_industries.sort(
        key=lambda item: item[1].get("avg_weeks_score") or float("-inf"),
        reverse=True,
    )

    log_to_file(log_file, "Industries ranked by average weeks consensus score")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        f"{'Industry':<40} {'Names':>6} {'AvgScore':>10} {'BestScore':>12} "
        f"{'BestName':<15} {'FQSym':<18} {'TopPerf':<15} {'Perf1M':>8}",
    )
    log_to_file(log_file, "-" * 160)
    for group_label, meta in ranked_industries:
        best_row = meta.get("best_score_row") or {}
        best_scan_row = best_row.get("row") or {}
        perf_row = meta.get("best_perf_row") or {}
        perf_scan_row = perf_row.get("row") or {}
        log_to_file(
            log_file,
            f"{group_label[:39]:<40} {meta.get('row_count', 0):>6} "
            f"{meta.get('avg_weeks_score', 0):>+10.3f} "
            f"{meta.get('best_weeks_score', 0):>+12.3f} "
            f"{_search_name(best_scan_row):<15} "
            f"{_full_qualified_symbol(best_scan_row):<18} "
            f"{_search_name(perf_scan_row):<15} "
            f"{_format_percent(meta.get('best_perf_1m')):>8}",
        )
    log_to_file(log_file, "")


def write_industry_packs_from_duckdb_run(
    duckdb_run_result: Mapping[str, Any],
    *,
    consensus_rows: Sequence[Mapping[str, Any]] | None = None,
    profile_names: Sequence[str] | None = None,
    group_field: str = "industry",
    top_n: int = INDUSTRY_PACK_TOP_N,
    score_horizon: str = DEFAULT_SCORE_HORIZON,
    peer_rescore_within_group: bool = True,
    min_group_size_for_peer_rescore: int = 5,
    industries_filter: Sequence[str] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build per-industry human-readable packs from a DuckDB suite run.

  Parameters
  ----------
  duckdb_run_result:
      Return value from :func:`run_full_analysis_suite_duckdb`.
  consensus_rows:
      Optional override when ``_consensus_rows`` is not on the result dict.
  group_field:
      ``industry`` (default, finer peer sets) or ``sector``.
  top_n:
      Names per ranking table (default 30).
  peer_rescore_within_group:
      When the group has enough names, recompute consensus with MAD profiles
      scoped to that group for cleaner peer-relative ordering.
  industries_filter:
      Optional allow-list of group labels to export.
  output_dir:
      Defaults to ``<run_output_dir>/industry_packs``.

  Returns
  -------
  dict with ``industry_packs_dir``, ``overview_log``, and per-group file paths.
    """
    run_output_dir = Path(duckdb_run_result["_duckdb_run_output_dir"])
    if output_dir is None:
        packs_root = run_output_dir / "industry_packs"
    else:
        packs_root = Path(output_dir)
    packs_root.mkdir(parents=True, exist_ok=True)

    resolved_consensus = _resolve_consensus_rows(
        duckdb_run_result, consensus_rows, profile_names
    )
    profile_suite = _resolve_profile_suite_from_run_result(
        duckdb_run_result, profile_names
    )
    grouped = _group_consensus_rows(resolved_consensus, group_field)

    if industries_filter:
        allowed = {str(value).strip() for value in industries_filter}
        grouped = {
            key: rows for key, rows in grouped.items() if key in allowed
        }

    run_id = duckdb_run_result.get("_duckdb_run_id")
    regime_by_symbol = _resolve_regime_by_symbol(duckdb_run_result)
    industry_outputs: dict[str, dict[str, Any]] = {}

    for group_label in sorted(grouped, key=lambda key: (-len(grouped[key]), key)):
        group_rows = grouped[group_label]
        if not group_rows:
            continue

        folder_name = _sanitize_industry_folder_name(group_label)
        group_dir = packs_root / folder_name
        group_dir.mkdir(parents=True, exist_ok=True)

        peer_rescored = (
            peer_rescore_within_group
            and len(group_rows) >= min_group_size_for_peer_rescore
        )
        score_source_rows = (
            _peer_rescore_group(group_rows, profile_suite) if peer_rescored else group_rows
        )
        score_sorted = sorted(
            score_source_rows,
            key=lambda row: _horizon_score(row, score_horizon) or float("-inf"),
            reverse=True,
        )

        summary_log = group_dir / "industry_pack_summary.log"
        score_log = group_dir / "industry_pack__ranked_by_score.log"
        perf_log = group_dir / "industry_pack__ranked_by_perf_1m.log"

        _write_group_summary_log(
            summary_log,
            group_rows,
            group_label=group_label,
            group_field=group_field,
            score_horizon=score_horizon,
            top_n=top_n,
            peer_rescored=peer_rescored,
            run_id=str(run_id) if run_id else None,
            regime_by_symbol=regime_by_symbol,
        )
        _write_group_ranked_by_score_log(
            score_log,
            score_sorted,
            group_label=group_label,
            score_horizon=score_horizon,
            top_n=top_n,
            peer_rescored=peer_rescored,
            regime_by_symbol=regime_by_symbol,
        )
        _write_group_ranked_by_perf_log(
            perf_log,
            group_rows,
            group_label=group_label,
            perf_field=DEFAULT_PERF_RANK_FIELD,
            score_horizon=score_horizon,
            top_n=top_n,
            regime_by_symbol=regime_by_symbol,
        )

        weeks_scores = [
            s
            for row in group_rows
            if (s := _horizon_score(row, score_horizon)) is not None
        ]
        best_score_row = score_sorted[0] if score_sorted else None
        perf_sorted = sorted(
            group_rows,
            key=lambda row: _perf_value(row.get("row") or {}, DEFAULT_PERF_RANK_FIELD)
            or float("-inf"),
            reverse=True,
        )
        best_perf_row = perf_sorted[0] if perf_sorted else None

        industry_outputs[group_label] = {
            "folder": group_dir,
            "summary_log": summary_log,
            "ranked_by_score_log": score_log,
            "ranked_by_perf_1m_log": perf_log,
            "row_count": len(group_rows),
            "peer_rescored": peer_rescored,
            "avg_weeks_score": (
                sum(weeks_scores) / len(weeks_scores) if weeks_scores else None
            ),
            "best_weeks_score": (
                _horizon_score(best_score_row, score_horizon)
                if best_score_row
                else None
            ),
            "best_score_ticker": (
                best_score_row.get("ticker") if best_score_row else None
            ),
            "best_score_row": best_score_row,
            "best_perf_row": best_perf_row,
            "best_perf_ticker": best_perf_row.get("ticker") if best_perf_row else None,
            "best_perf_1m": (
                _perf_value(best_perf_row.get("row") or {}, DEFAULT_PERF_RANK_FIELD)
                if best_perf_row
                else None
            ),
        }

    overview_log = packs_root / "_industry_packs_overview.log"
    _write_overview_log(
        overview_log,
        run_id=str(run_id) if run_id else None,
        group_field=group_field,
        industry_outputs=industry_outputs,
        score_horizon=score_horizon,
    )

    return {
        "industry_packs_dir": packs_root,
        "overview_log": overview_log,
        "industries": industry_outputs,
        "run_output_dir": run_output_dir,
    }


__all__ = [
    "INDUSTRY_PACK_TOP_N",
    "write_industry_packs_from_duckdb_run",
]
