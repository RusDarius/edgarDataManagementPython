"""Industry price-mover scan with better-scored peer alternatives.

Ranks industries by trailing price action, takes the top movers, and surfaces
within-industry names that score higher but have lagged on tape — the "bang for
buck" / catch-up set relative to extended price leaders.

Designed to run immediately after ``run_full_analysis_suite_duckdb`` using the
same consensus rows (optionally peer-rescored within each industry).
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from data_analysis_scripts._shared_analysis_utils import (
    build_report_title,
    coerce_numeric,
    reset_log_file,
)
from data_analysis_scripts.trading_view_move_prediction_analysis import (
    HORIZON_TITLES,
    _format_percent,
    _format_score,
    _sanitize_industry_folder_name,
)
from data_analysis_scripts.trading_view_move_prediction_industry_packs import (
    DEFAULT_PERF_RANK_FIELD,
    DEFAULT_SCORE_HORIZON,
    _build_group_overview_meta,
    _collect_perf_pack_stats,
    _full_qualified_symbol,
    _group_consensus_rows,
    _horizon_score,
    _log_ranked_row,
    _log_ranked_table_header,
    _lookup_regime_record,
    _peer_rescore_group,
    _perf_value,
    _resolve_consensus_rows,
    _resolve_profile_suite_from_run_result,
    _resolve_regime_by_symbol,
    _safety_gate_summary,
    _search_name,
    _top_profile_signals,
)
from generic_utils.log_to_files_util import log_to_file

INDUSTRY_MOVER_SCAN_LOG_SUBDIR = "industry_price_mover_relative_scan"
DEFAULT_TOP_INDUSTRIES = 10
DEFAULT_PRICE_MOVERS_PER_INDUSTRY = 5
DEFAULT_CATCH_UP_PER_INDUSTRY = 10
DEFAULT_ALTERNATIVES_PER_MOVER = 3
MIN_GROUP_SIZE = 3


@dataclass(frozen=True)
class IndustryTapeSummary:
    industry: str
    row_count: int
    avg_perf: float | None
    median_perf: float | None
    avg_weeks_score: float | None
    median_weeks_score: float | None
    catch_up_count: int
    extended_count: int
    best_perf_symbol: str
    best_perf_company: str
    best_perf_value: float | None
    best_score_symbol: str
    best_score_company: str
    best_weeks_score: float | None


@dataclass(frozen=True)
class RelativeOpportunityRow:
    industry: str
    symbol: str
    company: str
    weeks_score: float
    risk_adjusted_score: float | None
    perf_value: float
    perf_field: str
    bang_for_buck: float
    score_vs_median: float
    perf_vs_median: float
    manager_action_signal: str
    risk_tier: str
    gates: str
    role: str
    ref_mover_symbol: str | None = None
    ref_mover_perf: float | None = None
    score_gap_vs_mover: float | None = None
    perf_lag_vs_mover: float | None = None


def _perf_field_label(perf_field: str) -> str:
    return {
        "Perf.W": "1-week trailing return",
        "Perf.1M": "1-month trailing return",
        "Perf.5D": "5-day trailing return",
        "Perf.YTD": "year-to-date trailing return",
        "change": "daily change",
    }.get(perf_field, perf_field)


def _industry_tape_summaries(
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    score_horizon: str,
    perf_field: str,
    regime_by_symbol: Mapping[str, Any],
) -> list[IndustryTapeSummary]:
    summaries: list[IndustryTapeSummary] = []
    for industry, group_rows in grouped.items():
        if len(group_rows) < MIN_GROUP_SIZE:
            continue
        row_dicts = [row.get("row") or {} for row in group_rows]
        perf_stats = _collect_perf_pack_stats(row_dicts, perf_field)
        meta = _build_group_overview_meta(
            group_rows,
            score_horizon=score_horizon,
            score_sorted=sorted(
                group_rows,
                key=lambda row: _horizon_score(row, score_horizon) or float("-inf"),
                reverse=True,
            ),
            regime_by_symbol=regime_by_symbol,
        )
        best_perf_row = meta.get("best_perf_row") or {}
        best_score_row = meta.get("best_score_row") or {}
        best_perf_scan = best_perf_row.get("row") or {}
        best_score_scan = best_score_row.get("row") or {}
        summaries.append(
            IndustryTapeSummary(
                industry=industry,
                row_count=len(group_rows),
                avg_perf=perf_stats.get("avg"),
                median_perf=perf_stats.get("median"),
                avg_weeks_score=meta.get("avg_weeks_score"),
                median_weeks_score=meta.get("median_weeks_score"),
                catch_up_count=int(meta.get("catch_up_count") or 0),
                extended_count=int(meta.get("extended_count") or 0),
                best_perf_symbol=_full_qualified_symbol(best_perf_scan),
                best_perf_company=_search_name(best_perf_scan),
                best_perf_value=_perf_value(best_perf_scan, perf_field),
                best_score_symbol=_full_qualified_symbol(best_score_scan),
                best_score_company=_search_name(best_score_scan),
                best_weeks_score=meta.get("best_weeks_score"),
            )
        )
    return summaries


def _select_top_industries_by_price(
    summaries: Sequence[IndustryTapeSummary],
    *,
    top_industries: int,
) -> list[IndustryTapeSummary]:
    ranked = sorted(
        summaries,
        key=lambda item: item.avg_perf if item.avg_perf is not None else float("-inf"),
        reverse=True,
    )
    return ranked[:top_industries]


def _consensus_row_metrics(
    consensus_row: Mapping[str, Any],
    *,
    score_horizon: str,
    perf_field: str,
) -> tuple[float | None, float | None, float | None, str, str]:
    row = consensus_row.get("row") or {}
    horizon = consensus_row.get("horizons", {}).get(score_horizon, {})
    score = _horizon_score(consensus_row, score_horizon)
    ras = coerce_numeric(horizon.get("risk_adjusted_score"))
    perf = _perf_value(row, perf_field)
    action = str(consensus_row.get("manager_action_signal") or "N/A")
    risk_tier = str(horizon.get("risk_tier") or "N/A")
    return score, ras, perf, action, risk_tier


def _bang_for_buck_index(
    score: float,
    perf: float,
    *,
    median_score: float,
    median_perf: float,
) -> float:
    """Higher = stronger score with more performance still on the table."""
    return (score - median_score) + (median_perf - perf)


def _build_catch_up_rows(
    industry: str,
    group_rows: Sequence[Mapping[str, Any]],
    *,
    score_horizon: str,
    perf_field: str,
    median_score: float,
    median_perf: float,
    regime_by_symbol: Mapping[str, Any],
    limit: int,
) -> list[RelativeOpportunityRow]:
    candidates: list[RelativeOpportunityRow] = []
    for consensus_row in group_rows:
        row = consensus_row.get("row") or {}
        score, ras, perf, action, risk_tier = _consensus_row_metrics(
            consensus_row,
            score_horizon=score_horizon,
            perf_field=perf_field,
        )
        if score is None or perf is None:
            continue
        if score < median_score or perf >= median_perf:
            continue
        regime_record = _lookup_regime_record(row, regime_by_symbol)
        candidates.append(
            RelativeOpportunityRow(
                industry=industry,
                symbol=_full_qualified_symbol(row),
                company=_search_name(row),
                weeks_score=score,
                risk_adjusted_score=ras,
                perf_value=perf,
                perf_field=perf_field,
                bang_for_buck=_bang_for_buck_index(
                    score, perf, median_score=median_score, median_perf=median_perf
                ),
                score_vs_median=score - median_score,
                perf_vs_median=perf - median_perf,
                manager_action_signal=action,
                risk_tier=risk_tier,
                gates=_safety_gate_summary(row, consensus_row, regime_record),
                role="catch_up",
            )
        )
    candidates.sort(key=lambda item: item.bang_for_buck, reverse=True)
    return candidates[:limit]


def _build_alternative_rows_for_mover(
    industry: str,
    mover_row: Mapping[str, Any],
    group_rows: Sequence[Mapping[str, Any]],
    *,
    score_horizon: str,
    perf_field: str,
    regime_by_symbol: Mapping[str, Any],
    limit: int,
) -> list[RelativeOpportunityRow]:
    mover_scan = mover_row.get("row") or {}
    mover_symbol = _full_qualified_symbol(mover_scan)
    mover_score, _, mover_perf, _, _ = _consensus_row_metrics(
        mover_row,
        score_horizon=score_horizon,
        perf_field=perf_field,
    )
    if mover_score is None or mover_perf is None:
        return []

    alternatives: list[RelativeOpportunityRow] = []
    for consensus_row in group_rows:
        row = consensus_row.get("row") or {}
        symbol = _full_qualified_symbol(row)
        if symbol == mover_symbol:
            continue
        score, ras, perf, action, risk_tier = _consensus_row_metrics(
            consensus_row,
            score_horizon=score_horizon,
            perf_field=perf_field,
        )
        if score is None or perf is None:
            continue
        if score <= mover_score or perf >= mover_perf:
            continue
        score_gap = score - mover_score
        perf_lag = mover_perf - perf
        regime_record = _lookup_regime_record(row, regime_by_symbol)
        alternatives.append(
            RelativeOpportunityRow(
                industry=industry,
                symbol=symbol,
                company=_search_name(row),
                weeks_score=score,
                risk_adjusted_score=ras,
                perf_value=perf,
                perf_field=perf_field,
                bang_for_buck=score_gap + perf_lag,
                score_vs_median=score_gap,
                perf_vs_median=-perf_lag,
                manager_action_signal=action,
                risk_tier=risk_tier,
                gates=_safety_gate_summary(row, consensus_row, regime_record),
                role="better_vs_mover",
                ref_mover_symbol=mover_symbol,
                ref_mover_perf=mover_perf,
                score_gap_vs_mover=score_gap,
                perf_lag_vs_mover=perf_lag,
            )
        )
    alternatives.sort(key=lambda item: item.bang_for_buck, reverse=True)
    return alternatives[:limit]


def _top_price_movers(
    group_rows: Sequence[Mapping[str, Any]],
    *,
    perf_field: str,
    limit: int,
) -> list[Mapping[str, Any]]:
    ranked = sorted(
        group_rows,
        key=lambda row: _perf_value(row.get("row") or {}, perf_field) or float("-inf"),
        reverse=True,
    )
    return ranked[:limit]


def _write_master_overview_log(
    log_file: Path,
    *,
    run_id: str | None,
    perf_field: str,
    score_horizon: str,
    top_industries: Sequence[IndustryTapeSummary],
) -> None:
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title("Industry price-mover relative scan — top industries"),
    )
    log_to_file(log_file, "=" * 160)
    if run_id:
        log_to_file(log_file, f"run_id: {run_id}")
    log_to_file(
        log_file,
        f"Industry ranking lens: {_perf_field_label(perf_field)} ({perf_field}) | "
        f"score horizon: {HORIZON_TITLES.get(score_horizon, score_horizon)}",
    )
    log_to_file(
        log_file,
        "CatchUp = above-median score, below-median perf within industry. "
        "Extended = below-median score, above-median perf (chasing). "
        "Per-industry detail logs list price leaders and better-scored alternatives.",
    )
    log_to_file(log_file, "")
    log_to_file(log_file, f"TOP {len(top_industries)} INDUSTRIES BY AVERAGE {perf_field}")
    log_to_file(log_file, "-" * 160)
    log_to_file(
        log_file,
        f"{'Industry':<34} {'Names':>6} {'AvgPerf':>9} {'MedPerf':>9} "
        f"{'AvgScore':>9} {'CatchUp':>8} {'Extend':>8} "
        f"{'TopMover':<15} {'TopPerf':>8} {'TopScorer':<15} {'BestScr':>8}",
    )
    log_to_file(log_file, "-" * 160)
    for summary in top_industries:
        log_to_file(
            log_file,
            f"{summary.industry[:33]:<34} {summary.row_count:>6} "
            f"{_format_percent(summary.avg_perf):>9} "
            f"{_format_percent(summary.median_perf):>9} "
            f"{_format_score(summary.avg_weeks_score):>9} "
            f"{summary.catch_up_count:>8} "
            f"{summary.extended_count:>8} "
            f"{summary.best_perf_company[:15]:<15} "
            f"{_format_percent(summary.best_perf_value):>8} "
            f"{summary.best_score_company[:15]:<15} "
            f"{_format_score(summary.best_weeks_score):>8}",
        )
    log_to_file(log_file, "")


def _write_industry_detail_log(
    log_file: Path,
    *,
    industry: str,
    run_id: str | None,
    perf_field: str,
    score_horizon: str,
    group_rows: Sequence[Mapping[str, Any]],
    score_sorted: Sequence[Mapping[str, Any]],
    peer_rescored: bool,
    regime_by_symbol: Mapping[str, Any],
    price_movers: Sequence[Mapping[str, Any]],
    catch_up_rows: Sequence[RelativeOpportunityRow],
    alternative_map: Mapping[str, Sequence[RelativeOpportunityRow]],
    median_score: float,
    median_perf: float,
) -> None:
    reset_log_file(log_file)
    log_to_file(
        log_file,
        build_report_title(f"Industry relative scan — {industry}"),
    )
    log_to_file(log_file, "=" * 160)
    if run_id:
        log_to_file(log_file, f"run_id: {run_id}")
    log_to_file(
        log_file,
        f"industry={industry} | names={len(group_rows)} | peer_rescore="
        f"{'yes' if peer_rescored else 'no'} | med_score={median_score:+.3f} | "
        f"med_{perf_field}={median_perf:+.2f}%",
    )
    log_to_file(log_file, "")

    log_to_file(
        log_file,
        f"SECTION 1 — TOP PRICE MOVERS ({perf_field})",
    )
    log_to_file(log_file, "-" * 160)
    _log_ranked_table_header(
        log_file,
        title=f"Price leaders — {industry}",
        score_horizon=score_horizon,
        ranking_basis=_perf_field_label(perf_field),
    )
    for rank, consensus_row in enumerate(price_movers, 1):
        _log_ranked_row(
            log_file,
            rank,
            consensus_row,
            score_horizon=score_horizon,
            regime_by_symbol=regime_by_symbol,
        )
    log_to_file(log_file, "")

    log_to_file(log_file, "SECTION 2 — BETTER-SCORED ALTERNATIVES VS EACH PRICE LEADER")
    log_to_file(log_file, "-" * 160)
    for mover_row in price_movers:
        mover_scan = mover_row.get("row") or {}
        mover_symbol = _full_qualified_symbol(mover_scan)
        mover_score = _horizon_score(mover_row, score_horizon)
        mover_perf = _perf_value(mover_scan, perf_field)
        log_to_file(
            log_file,
            f"Mover: {_search_name(mover_scan):<15} {mover_symbol:<18} "
            f"perf={_format_percent(mover_perf)} score={_format_score(mover_score)}",
        )
        alternatives = alternative_map.get(mover_symbol, [])
        if not alternatives:
            log_to_file(
                log_file,
                "  (no same-industry names with higher score AND lower perf)",
            )
            continue
        log_to_file(
            log_file,
            f"  {'AltSym':<18} {'Name':<15} {'Score':>7} {'RAdj':>7} "
            f"{perf_field:>8} {'ScrGap':>7} {'PerfLag':>8} {'B4B':>7} "
            f"{'Action':<16} {'Gates':<8}",
        )
        for alt in alternatives:
            log_to_file(
                log_file,
                f"  {alt.symbol:<18} {alt.company:<15} "
                f"{alt.weeks_score:>+7.3f} "
                f"{_format_score(alt.risk_adjusted_score):>7} "
                f"{_format_percent(alt.perf_value):>8} "
                f"{alt.score_gap_vs_mover or 0:>+7.3f} "
                f"{_format_percent(alt.perf_lag_vs_mover):>8} "
                f"{alt.bang_for_buck:>+7.3f} "
                f"{alt.manager_action_signal[:16]:<16} {alt.gates:<8}",
            )
        log_to_file(log_file, "")

    log_to_file(log_file, "SECTION 3 — CATCH-UP / BANG-FOR-BUCK (high score, lagging perf)")
    log_to_file(log_file, "-" * 160)
    if not catch_up_rows:
        log_to_file(log_file, "No catch-up names above median score with below-median perf.")
    else:
        log_to_file(
            log_file,
            f"{'Rank':<5} {'FQSym':<18} {'Name':<15} {'Score':>7} {'RAdj':>7} "
            f"{perf_field:>8} {'ScrMed':>7} {'PfMed':>8} {'B4B':>7} "
            f"{'Action':<16} {'Gates':<8} {'Profiles':<40}",
        )
        log_to_file(log_file, "-" * 160)
        for rank, item in enumerate(catch_up_rows, 1):
            consensus_row = next(
                (
                    row
                    for row in score_sorted
                    if _full_qualified_symbol(row.get("row") or {}) == item.symbol
                ),
                None,
            )
            profiles = (
                _top_profile_signals(consensus_row, score_horizon)
                if consensus_row
                else "N/A"
            )
            log_to_file(
                log_file,
                f"{rank:<5} {item.symbol:<18} {item.company:<15} "
                f"{item.weeks_score:>+7.3f} {_format_score(item.risk_adjusted_score):>7} "
                f"{_format_percent(item.perf_value):>8} "
                f"{item.score_vs_median:>+7.3f} {_format_percent(item.perf_vs_median):>8} "
                f"{item.bang_for_buck:>+7.3f} "
                f"{item.manager_action_signal[:16]:<16} {item.gates:<8} {profiles[:40]:<40}",
            )
    log_to_file(log_file, "")

    log_to_file(log_file, "SECTION 4 — FULL PEER SCORE RANK (context)")
    log_to_file(log_file, "-" * 160)
    _log_ranked_table_header(
        log_file,
        title=f"All peers by score — {industry}",
        score_horizon=score_horizon,
        ranking_basis=(
            "peer-local consensus" if peer_rescored else "global consensus, industry rank"
        ),
    )
    for rank, consensus_row in enumerate(score_sorted[:15], 1):
        _log_ranked_row(
            log_file,
            rank,
            consensus_row,
            score_horizon=score_horizon,
            regime_by_symbol=regime_by_symbol,
        )
    log_to_file(log_file, "")


def _write_opportunities_csv(
    csv_path: Path,
    rows: Sequence[RelativeOpportunityRow],
) -> None:
    fieldnames = list(RelativeOpportunityRow.__annotations__.keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def run_industry_price_mover_relative_scan(
    duckdb_run_result: Mapping[str, Any],
    *,
    consensus_rows: Sequence[Mapping[str, Any]] | None = None,
    profile_names: Sequence[str] | None = None,
    group_field: str = "industry",
    perf_field: str = DEFAULT_PERF_RANK_FIELD,
    score_horizon: str = DEFAULT_SCORE_HORIZON,
    top_industries: int = DEFAULT_TOP_INDUSTRIES,
    price_movers_per_industry: int = DEFAULT_PRICE_MOVERS_PER_INDUSTRY,
    catch_up_per_industry: int = DEFAULT_CATCH_UP_PER_INDUSTRY,
    alternatives_per_mover: int = DEFAULT_ALTERNATIVES_PER_MOVER,
    peer_rescore_within_group: bool = True,
    min_group_size_for_peer_rescore: int = 5,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Scan top price-moving industries and surface better-scored relative peers.

    Parameters
    ----------
    duckdb_run_result:
        Return value from :func:`run_full_analysis_suite_duckdb`.
    perf_field:
        Industry ranking field — ``Perf.1M`` (default), ``Perf.W``, ``Perf.5D``,
        or ``change``.
    top_industries:
        How many industries to expand (default 10).
    price_movers_per_industry:
        Top N tape leaders per industry for alternative matching.
    catch_up_per_industry:
        Top N bang-for-buck names per industry.
    alternatives_per_mover:
        Better-scored peers returned for each price leader.

    Returns
    -------
    dict with overview log path, per-industry logs, CSV export, and summaries.
    """
    run_output_dir = Path(duckdb_run_result["_duckdb_run_output_dir"])
    scan_root = (
        Path(output_dir)
        if output_dir is not None
        else run_output_dir / INDUSTRY_MOVER_SCAN_LOG_SUBDIR
    )
    scan_root.mkdir(parents=True, exist_ok=True)

    resolved_consensus = _resolve_consensus_rows(
        duckdb_run_result, consensus_rows, profile_names
    )
    profile_suite = _resolve_profile_suite_from_run_result(
        duckdb_run_result, profile_names
    )
    grouped = _group_consensus_rows(resolved_consensus, group_field)
    regime_by_symbol = _resolve_regime_by_symbol(duckdb_run_result)
    run_id = duckdb_run_result.get("_duckdb_run_id")

    summaries = _industry_tape_summaries(
        grouped,
        score_horizon=score_horizon,
        perf_field=perf_field,
        regime_by_symbol=regime_by_symbol,
    )
    selected_industries = _select_top_industries_by_price(
        summaries, top_industries=top_industries
    )

    overview_log = scan_root / "industry_price_mover_relative_scan__overview.log"
    _write_master_overview_log(
        overview_log,
        run_id=str(run_id) if run_id else None,
        perf_field=perf_field,
        score_horizon=score_horizon,
        top_industries=selected_industries,
    )

    all_opportunity_rows: list[RelativeOpportunityRow] = []
    industry_outputs: dict[str, dict[str, Any]] = {}

    for summary in selected_industries:
        group_rows = grouped[summary.industry]
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

        row_dicts = [row.get("row") or {} for row in group_rows]
        perf_stats = _collect_perf_pack_stats(row_dicts, perf_field)
        median_perf = perf_stats.get("median")
        median_score = summary.median_weeks_score
        if median_perf is None or median_score is None:
            continue

        price_movers = _top_price_movers(
            group_rows,
            perf_field=perf_field,
            limit=price_movers_per_industry,
        )
        catch_up_rows = _build_catch_up_rows(
            summary.industry,
            score_source_rows,
            score_horizon=score_horizon,
            perf_field=perf_field,
            median_score=median_score,
            median_perf=median_perf,
            regime_by_symbol=regime_by_symbol,
            limit=catch_up_per_industry,
        )
        all_opportunity_rows.extend(catch_up_rows)

        alternative_map: dict[str, list[RelativeOpportunityRow]] = {}
        for mover_row in price_movers:
            mover_scan = mover_row.get("row") or {}
            mover_symbol = _full_qualified_symbol(mover_scan)
            alts = _build_alternative_rows_for_mover(
                summary.industry,
                mover_row,
                score_source_rows,
                score_horizon=score_horizon,
                perf_field=perf_field,
                regime_by_symbol=regime_by_symbol,
                limit=alternatives_per_mover,
            )
            alternative_map[mover_symbol] = alts
            all_opportunity_rows.extend(alts)

        folder_name = _sanitize_industry_folder_name(summary.industry)
        industry_dir = scan_root / folder_name
        industry_dir.mkdir(parents=True, exist_ok=True)
        detail_log = industry_dir / "industry_relative_scan.log"
        _write_industry_detail_log(
            detail_log,
            industry=summary.industry,
            run_id=str(run_id) if run_id else None,
            perf_field=perf_field,
            score_horizon=score_horizon,
            group_rows=group_rows,
            score_sorted=score_sorted,
            peer_rescored=peer_rescored,
            regime_by_symbol=regime_by_symbol,
            price_movers=price_movers,
            catch_up_rows=catch_up_rows,
            alternative_map=alternative_map,
            median_score=median_score,
            median_perf=median_perf,
        )
        industry_outputs[summary.industry] = {
            "industry_dir": industry_dir,
            "detail_log": detail_log,
            "summary": asdict(summary),
            "catch_up_count": len(catch_up_rows),
            "price_mover_count": len(price_movers),
            "alternative_count": sum(len(v) for v in alternative_map.values()),
        }

    opportunities_csv = scan_root / "industry_relative_opportunities.csv"
    _write_opportunities_csv(opportunities_csv, all_opportunity_rows)

    manifest = {
        "run_id": run_id,
        "perf_field": perf_field,
        "score_horizon": score_horizon,
        "top_industries": top_industries,
        "selected_industry_count": len(selected_industries),
        "selected_industries": [item.industry for item in selected_industries],
        "opportunity_row_count": len(all_opportunity_rows),
    }
    manifest_path = scan_root / "industry_price_mover_relative_scan__manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {
        "scan_root": scan_root,
        "overview_log": overview_log,
        "opportunities_csv": opportunities_csv,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "industry_summaries": [asdict(item) for item in selected_industries],
        "industries": industry_outputs,
    }
