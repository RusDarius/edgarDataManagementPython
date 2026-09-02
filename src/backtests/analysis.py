from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from statistics import mean
from typing import Any, Mapping, Sequence

from backtests.config import BacktestConfig
from backtests.contracts import OutcomeRow, SignalRow


# ---------------------------------------------------------------------------
# Overlay construction
# ---------------------------------------------------------------------------


def _build_signal_index(
    signal_rows: Sequence[SignalRow],
) -> dict[tuple[str, str], dict[str, SignalRow]]:
    indexed: dict[tuple[str, str], dict[str, SignalRow]] = defaultdict(dict)
    for row in signal_rows:
        key = (row.as_of_date.isoformat(), row.symbol)
        indexed[key][f"{row.family}:{row.signal_name}"] = row
    return indexed


def _choose_pick_score(
    row_map: Mapping[str, SignalRow], priority_signals: Sequence[str]
) -> tuple[float | None, str | None]:
    for signal_name in priority_signals:
        for key, row in row_map.items():
            family, candidate_signal = key.split(":", 1)
            if family not in {"move_prediction", "upside_opportunity"}:
                continue
            if candidate_signal != signal_name:
                continue
            if row.score is None:
                continue
            return float(row.score), signal_name
    return None, None


def _resolve_timing_context(
    row_map: Mapping[str, SignalRow], *, prefer_artifact: bool
) -> tuple[str | None, float | None]:
    order = (
        ("market_timing:timing_action_artifact", "market_timing:timing_action_reconstructed")
        if prefer_artifact
        else ("market_timing:timing_action_reconstructed", "market_timing:timing_action_artifact")
    )
    for key in order:
        row = row_map.get(key)
        if row is None:
            continue
        return row.action, row.weight
    return None, None


def _projection_gate(
    row_map: Mapping[str, SignalRow], config: BacktestConfig
) -> tuple[bool, dict[str, float | None]]:
    upside = None
    growth_gap = None
    for key, row in row_map.items():
        _, signal_name = key.split(":", 1)
        if signal_name in {"primary_upside_pct", "projection_input_price_target_upside_pct"}:
            upside = row.score if row.score is not None else upside
        if signal_name in {"own_minus_peer_revenue_cagr_pp", "projection_input_total_revenue_cagr_5y"}:
            growth_gap = row.score if row.score is not None else growth_gap
    upside_pass = upside is not None and upside >= config.overlay.projection_upside_min_pct
    growth_pass = growth_gap is not None and growth_gap >= config.overlay.projection_growth_gap_min_pp
    return upside_pass or growth_pass, {"upside": upside, "growth_gap": growth_gap}


def _rank_variant_rows(rows: Sequence[SignalRow]) -> list[SignalRow]:
    by_variant_day: dict[tuple[str, str], list[SignalRow]] = defaultdict(list)
    for row in rows:
        key = (row.as_of_date.isoformat(), row.signal_name)
        by_variant_day[key].append(row)
    ranked: list[SignalRow] = []
    for group in by_variant_day.values():
        sorted_rows = sorted(group, key=lambda item: item.score or float("-inf"), reverse=True)
        for rank, row in enumerate(sorted_rows, start=1):
            ranked.append(replace(row, rank=rank))
    return ranked


def build_overlay_signal_rows(
    signal_rows: Sequence[SignalRow], *, config: BacktestConfig
) -> list[SignalRow]:
    signal_index = _build_signal_index(signal_rows)
    variants = set(config.overlay.variants)
    output: list[SignalRow] = []

    for (_, symbol), row_map in signal_index.items():
        pick_score, pick_signal = _choose_pick_score(row_map, config.overlay.base_pick_signals)
        if pick_score is None:
            continue
        action, timing_weight = _resolve_timing_context(
            row_map, prefer_artifact=config.families.market_timing.prefer_artifact
        )
        if timing_weight is None:
            timing_weight = 0.0
        projection_pass, projection_values = _projection_gate(row_map, config)
        base_metadata = {
            "pick_signal": pick_signal,
            "timing_action": action,
            "timing_weight": timing_weight,
            "projection_pass": projection_pass,
            **projection_values,
        }
        as_of_date = row_map[next(iter(row_map))].as_of_date

        if "picks_only" in variants:
            output.append(
                SignalRow(
                    as_of_date=as_of_date,
                    symbol=symbol,
                    family="overlay",
                    signal_name="picks_only",
                    score=pick_score,
                    weight=1.0,
                    action=action,
                    metadata_json=json.dumps(base_metadata, sort_keys=True),
                )
            )
        if "timing_filter" in variants and action in set(config.overlay.enter_actions):
            output.append(
                SignalRow(
                    as_of_date=as_of_date,
                    symbol=symbol,
                    family="overlay",
                    signal_name="timing_filter",
                    score=pick_score,
                    weight=1.0,
                    action=action,
                    metadata_json=json.dumps(base_metadata, sort_keys=True),
                )
            )
        if "timing_weight" in variants and timing_weight > 0:
            output.append(
                SignalRow(
                    as_of_date=as_of_date,
                    symbol=symbol,
                    family="overlay",
                    signal_name="timing_weight",
                    score=pick_score * timing_weight,
                    weight=timing_weight,
                    action=action,
                    metadata_json=json.dumps(base_metadata, sort_keys=True),
                )
            )
        if "projection_filter" in variants and projection_pass:
            output.append(
                SignalRow(
                    as_of_date=as_of_date,
                    symbol=symbol,
                    family="overlay",
                    signal_name="projection_filter",
                    score=pick_score,
                    weight=1.0,
                    action=action,
                    metadata_json=json.dumps(base_metadata, sort_keys=True),
                )
            )
        if (
            "picks_timing_projection" in variants
            and projection_pass
            and action in set(config.overlay.enter_actions)
            and timing_weight > 0
        ):
            output.append(
                SignalRow(
                    as_of_date=as_of_date,
                    symbol=symbol,
                    family="overlay",
                    signal_name="picks_timing_projection",
                    score=pick_score * timing_weight,
                    weight=timing_weight,
                    action=action,
                    metadata_json=json.dumps(base_metadata, sort_keys=True),
                )
            )
    return _rank_variant_rows(output)


# ---------------------------------------------------------------------------
# Signal-vs-outcome metrics
# ---------------------------------------------------------------------------


def _average_rank(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    output = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for pos in range(i, j + 1):
            output[indexed[pos][0]] = avg_rank
        i = j + 1
    return output


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = mean(xs)
    mean_y = mean(ys)
    num = 0.0
    den_x = 0.0
    den_y = 0.0
    for x, y in zip(xs, ys, strict=True):
        dx = x - mean_x
        dy = y - mean_y
        num += dx * dy
        den_x += dx * dx
        den_y += dy * dy
    if den_x <= 0 or den_y <= 0:
        return None
    return num / ((den_x**0.5) * (den_y**0.5))


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    return _pearson(_average_rank(xs), _average_rank(ys))


def _assign_quantile(scores: Sequence[float], quantile_count: int) -> list[int]:
    ranked_positions = sorted(range(len(scores)), key=lambda idx: scores[idx])
    output = [1] * len(scores)
    total = len(scores)
    if total == 0:
        return output
    for rank_index, source_index in enumerate(ranked_positions):
        quantile = int((rank_index * quantile_count) / total) + 1
        if quantile > quantile_count:
            quantile = quantile_count
        output[source_index] = quantile
    return output


@dataclass(frozen=True)
class _Pair:
    as_of_date: str
    symbol: str
    score: float
    forward_return_pct: float


def evaluate_signal_rows(
    signal_rows: Sequence[SignalRow],
    outcome_rows: Sequence[OutcomeRow],
    *,
    horizons: Sequence[int],
    top_ns: Sequence[int],
    quintile_count: int,
    min_pairs_for_metric: int,
) -> dict[str, list[dict[str, Any]]]:
    outcome_index = {
        (row.as_of_date.isoformat(), row.symbol, int(row.horizon_days)): row.forward_return_pct
        for row in outcome_rows
    }

    grouped_signals: dict[tuple[str, str], list[SignalRow]] = defaultdict(list)
    for row in signal_rows:
        if row.score is None:
            continue
        grouped_signals[(row.family, row.signal_name)].append(row)

    metric_rows: list[dict[str, Any]] = []
    quintile_rows: list[dict[str, Any]] = []
    topn_rows: list[dict[str, Any]] = []

    for (family, signal_name), rows in grouped_signals.items():
        for horizon in sorted({max(1, int(item)) for item in horizons}):
            pairs: list[_Pair] = []
            for row in rows:
                key = (row.as_of_date.isoformat(), row.symbol, horizon)
                outcome = outcome_index.get(key)
                if outcome is None:
                    continue
                pairs.append(
                    _Pair(
                        as_of_date=row.as_of_date.isoformat(),
                        symbol=row.symbol,
                        score=float(row.score),
                        forward_return_pct=float(outcome),
                    )
                )
            if not pairs:
                continue

            scores = [pair.score for pair in pairs]
            returns = [pair.forward_return_pct for pair in pairs]
            spearman_ic = _spearman(scores, returns) if len(pairs) >= min_pairs_for_metric else None
            hit_rate = (
                sum(1 for value in returns if value > 0) / len(returns) if returns else None
            )
            winners = [value for value in returns if value > 0]
            losers = [value for value in returns if value <= 0]
            avg_winner = mean(winners) if winners else None
            avg_loser = mean(losers) if losers else None

            quantiles = _assign_quantile(scores, quantile_count=max(2, quintile_count))
            bucket_returns: dict[int, list[float]] = defaultdict(list)
            for pair, quantile in zip(pairs, quantiles, strict=True):
                bucket_returns[quantile].append(pair.forward_return_pct)
            q_spread: float | None = None
            if bucket_returns.get(1) and bucket_returns.get(quintile_count):
                q_spread = mean(bucket_returns[quintile_count]) - mean(bucket_returns[1])

            for quantile in sorted(bucket_returns):
                values = bucket_returns[quantile]
                quintile_rows.append(
                    {
                        "family": family,
                        "signal_name": signal_name,
                        "horizon_days": horizon,
                        "quintile": quantile,
                        "pair_count": len(values),
                        "avg_forward_return_pct": mean(values),
                    }
                )

            top_summary: dict[int, tuple[float | None, float | None, float | None]] = {}
            by_day: dict[str, list[_Pair]] = defaultdict(list)
            for pair in pairs:
                by_day[pair.as_of_date].append(pair)
            for top_n in sorted({max(1, int(value)) for value in top_ns}):
                day_top: list[float] = []
                day_universe: list[float] = []
                for day_pairs in by_day.values():
                    ordered = sorted(day_pairs, key=lambda item: item.score, reverse=True)
                    selected = ordered[: min(top_n, len(ordered))]
                    if not selected:
                        continue
                    day_top.append(mean([item.forward_return_pct for item in selected]))
                    day_universe.append(mean([item.forward_return_pct for item in ordered]))
                top_avg = mean(day_top) if day_top else None
                universe_avg = mean(day_universe) if day_universe else None
                lift = None
                if top_avg is not None and universe_avg is not None:
                    lift = top_avg - universe_avg
                top_summary[top_n] = (top_avg, universe_avg, lift)
                topn_rows.append(
                    {
                        "family": family,
                        "signal_name": signal_name,
                        "horizon_days": horizon,
                        "top_n": top_n,
                        "day_count": len(day_top),
                        "avg_top_return_pct": top_avg,
                        "avg_universe_return_pct": universe_avg,
                        "lift_pct": lift,
                    }
                )

            metric_row: dict[str, Any] = {
                "family": family,
                "signal_name": signal_name,
                "horizon_days": horizon,
                "pair_count": len(pairs),
                "day_count": len(by_day),
                "spearman_ic": spearman_ic,
                "quintile_spread_pct": q_spread,
                "hit_rate": hit_rate,
                "avg_winner_pct": avg_winner,
                "avg_loser_pct": avg_loser,
            }
            for top_n, (top_avg, universe_avg, lift) in top_summary.items():
                metric_row[f"top_{top_n}_avg_return_pct"] = top_avg
                metric_row[f"top_{top_n}_universe_avg_return_pct"] = universe_avg
                metric_row[f"top_{top_n}_lift_pct"] = lift
            metric_rows.append(metric_row)

    metric_rows.sort(
        key=lambda row: (
            row.get("family", ""),
            row.get("signal_name", ""),
            int(row.get("horizon_days", 0)),
        )
    )
    quintile_rows.sort(
        key=lambda row: (
            row.get("family", ""),
            row.get("signal_name", ""),
            int(row.get("horizon_days", 0)),
            int(row.get("quintile", 0)),
        )
    )
    topn_rows.sort(
        key=lambda row: (
            row.get("family", ""),
            row.get("signal_name", ""),
            int(row.get("horizon_days", 0)),
            int(row.get("top_n", 0)),
        )
    )
    return {
        "family_metrics": metric_rows,
        "field_patterns": quintile_rows,
        "top_n_metrics": topn_rows,
    }


# ---------------------------------------------------------------------------
# Overlay ablation summaries
# ---------------------------------------------------------------------------


def summarize_overlay_lift(
    metric_rows: Sequence[dict[str, Any]],
    *,
    top_ns: Sequence[int],
) -> list[dict[str, Any]]:
    overlay_rows = [row for row in metric_rows if str(row.get("family") or "") == "overlay"]
    by_horizon_signal: dict[tuple[int, str], dict[str, Any]] = {}
    for row in overlay_rows:
        by_horizon_signal[(int(row.get("horizon_days") or 0), str(row.get("signal_name") or ""))] = row

    output: list[dict[str, Any]] = []
    horizons = sorted({int(row.get("horizon_days") or 0) for row in overlay_rows})
    for horizon in horizons:
        baseline = by_horizon_signal.get((horizon, "picks_only"))
        baseline_ic = baseline.get("spearman_ic") if baseline else None
        for row in overlay_rows:
            if int(row.get("horizon_days") or 0) != horizon:
                continue
            signal_name = str(row.get("signal_name") or "")
            lift_row: dict[str, Any] = {
                "horizon_days": horizon,
                "variant": signal_name,
                "pair_count": row.get("pair_count"),
                "spearman_ic": row.get("spearman_ic"),
                "spearman_ic_lift_vs_picks_only": (
                    None
                    if baseline_ic is None or row.get("spearman_ic") is None
                    else float(row["spearman_ic"]) - float(baseline_ic)
                ),
                "quintile_spread_pct": row.get("quintile_spread_pct"),
            }
            for top_n in top_ns:
                key = f"top_{int(top_n)}_avg_return_pct"
                baseline_value = baseline.get(key) if baseline else None
                current_value = row.get(key)
                lift_row[key] = current_value
                lift_row[f"{key}_lift_vs_picks_only"] = (
                    None
                    if baseline_value is None or current_value is None
                    else float(current_value) - float(baseline_value)
                )
            output.append(lift_row)
    output.sort(key=lambda item: (int(item["horizon_days"]), str(item["variant"])))
    return output

