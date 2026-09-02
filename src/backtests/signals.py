from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtests.config import BacktestConfig
from backtests.contracts import AdapterOutput, ArtifactDay, SignalRow
from edge_research_tools.market_timing_policy import evaluate_timing_policy_row
from edge_research_tools.upside_opportunity_scanner import compute_momentum_component


# ---------------------------------------------------------------------------
# Shared adapter helpers
# ---------------------------------------------------------------------------


def _import_duckdb():
    import duckdb

    return duckdb


def _read_csv_rows(path: str | Path | None) -> list[dict[str, str]]:
    if not path:
        return []
    csv_path = Path(path)
    if not csv_path.exists() or csv_path.is_dir():
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    return int(number) if number is not None else None


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_signal_name(name: str) -> str:
    output = []
    for char in str(name):
        if char.isalnum():
            output.append(char.lower())
        else:
            output.append("_")
    collapsed = "".join(output)
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    return collapsed.strip("_")


def _table_exists(connection: Any, table_name: str) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = ?",
        [table_name],
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _with_ranked_signals(rows: Sequence[SignalRow]) -> list[SignalRow]:
    grouped: dict[tuple[str, str, str], list[SignalRow]] = defaultdict(list)
    for row in rows:
        key = (row.as_of_date.isoformat(), row.family, row.signal_name)
        grouped[key].append(row)
    ranked: list[SignalRow] = []
    for group_rows in grouped.values():
        sorted_rows = sorted(
            group_rows,
            key=lambda item: item.score if item.score is not None else float("-inf"),
            reverse=True,
        )
        rank = 1
        for row in sorted_rows:
            if row.score is None:
                ranked.append(row)
                continue
            ranked.append(replace(row, rank=rank))
            rank += 1
    return ranked


def _safe_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Move prediction adapter
# ---------------------------------------------------------------------------


class MovePredictionAdapter:
    family = "move_prediction"

    def load(
        self,
        calendar_days: Sequence[ArtifactDay],
        *,
        config: BacktestConfig,
    ) -> AdapterOutput:
        if not config.families.move_prediction.enabled:
            return AdapterOutput(family=self.family, signals=[], coverage={"enabled": False})

        consensus_horizons = {
            str(value).strip().lower()
            for value in config.families.move_prediction.consensus_horizons
        }
        profile_names = {
            str(value).strip().lower() for value in config.families.move_prediction.profile_names
        }
        signals: list[SignalRow] = []
        scanned_days = 0
        duckdb = _import_duckdb()

        for day in calendar_days:
            if day.prediction_database is None or not day.prediction_database.exists():
                continue
            if not day.prediction_run_id:
                continue
            scanned_days += 1
            conn = duckdb.connect(day.prediction_database.as_posix(), read_only=True)
            try:
                run_id = day.prediction_run_id
                if _table_exists(conn, "consensus_horizon_scores"):
                    rows = conn.execute(
                        """
                        SELECT symbol, horizon_name, score, risk_adjusted_score
                        FROM consensus_horizon_scores
                        WHERE run_id = ?
                        """,
                        [run_id],
                    ).fetchall()
                    for symbol, horizon_name, score, ras in rows:
                        symbol_value = _normalize_symbol(symbol)
                        if not symbol_value:
                            continue
                        horizon = str(horizon_name or "").strip().lower()
                        if horizon not in consensus_horizons:
                            continue
                        score_value = _safe_float(score)
                        ras_value = _safe_float(ras)
                        if score_value is not None:
                            signals.append(
                                SignalRow(
                                    as_of_date=day.as_of_date,
                                    symbol=symbol_value,
                                    family=self.family,
                                    signal_name=f"consensus_{_normalize_signal_name(horizon)}_score",
                                    score=score_value,
                                    source_path=day.prediction_database.as_posix(),
                                    run_id=run_id,
                                    horizon_hint=horizon,
                                )
                            )
                        if ras_value is not None:
                            signals.append(
                                SignalRow(
                                    as_of_date=day.as_of_date,
                                    symbol=symbol_value,
                                    family=self.family,
                                    signal_name=f"consensus_{_normalize_signal_name(horizon)}_ras",
                                    score=ras_value,
                                    source_path=day.prediction_database.as_posix(),
                                    run_id=run_id,
                                    horizon_hint=horizon,
                                )
                            )

                if _table_exists(conn, "conviction_rankings"):
                    rows = conn.execute(
                        """
                        SELECT symbol, conviction_score, rank_overall,
                               manager_action_signal, entry_readiness
                        FROM conviction_rankings
                        WHERE run_id = ?
                        """,
                        [run_id],
                    ).fetchall()
                    for symbol, conviction_score, rank_overall, manager_action, entry_readiness in rows:
                        symbol_value = _normalize_symbol(symbol)
                        if not symbol_value:
                            continue
                        conviction_value = _safe_float(conviction_score)
                        if conviction_value is None:
                            continue
                        metadata = json.dumps(
                            {"rank_overall": rank_overall}, separators=(",", ":"), sort_keys=True
                        )
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol_value,
                                family=self.family,
                                signal_name="conviction_score",
                                score=conviction_value,
                                source_path=day.prediction_database.as_posix(),
                                run_id=run_id,
                                metadata_json=metadata,
                            )
                        )
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol_value,
                                family=self.family,
                                signal_name="manager_action_signal",
                                score=conviction_value,
                                action=str(manager_action or "").strip() or None,
                                source_path=day.prediction_database.as_posix(),
                                run_id=run_id,
                            )
                        )
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol_value,
                                family=self.family,
                                signal_name="entry_readiness",
                                score=conviction_value,
                                action=str(entry_readiness or "").strip() or None,
                                source_path=day.prediction_database.as_posix(),
                                run_id=run_id,
                            )
                        )

                if (
                    config.families.move_prediction.include_regime_context
                    and _table_exists(conn, "regime_context_scores")
                ):
                    rows = conn.execute(
                        """
                        SELECT symbol, regime_fit_score
                        FROM regime_context_scores
                        WHERE run_id = ?
                        """,
                        [run_id],
                    ).fetchall()
                    for symbol, regime_fit_score in rows:
                        score_value = _safe_float(regime_fit_score)
                        if score_value is None:
                            continue
                        symbol_value = _normalize_symbol(symbol)
                        if not symbol_value:
                            continue
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol_value,
                                family=self.family,
                                signal_name="regime_fit_score",
                                score=score_value,
                                source_path=day.prediction_database.as_posix(),
                                run_id=run_id,
                            )
                        )

                if _table_exists(conn, "profile_horizon_scores"):
                    rows = conn.execute(
                        """
                        SELECT symbol, profile_name, horizon_name, score
                        FROM profile_horizon_scores
                        WHERE run_id = ?
                        """,
                        [run_id],
                    ).fetchall()
                    for symbol, profile_name, horizon_name, score in rows:
                        symbol_value = _normalize_symbol(symbol)
                        if not symbol_value:
                            continue
                        profile = str(profile_name or "").strip()
                        horizon = str(horizon_name or "").strip().lower()
                        if not profile:
                            continue
                        if profile_names and profile.lower() not in profile_names:
                            continue
                        if consensus_horizons and horizon not in consensus_horizons:
                            continue
                        score_value = _safe_float(score)
                        if score_value is None:
                            continue
                        signal_name = (
                            f"profile_{_normalize_signal_name(profile)}_"
                            f"{_normalize_signal_name(horizon)}_score"
                        )
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol_value,
                                family=self.family,
                                signal_name=signal_name,
                                score=score_value,
                                source_path=day.prediction_database.as_posix(),
                                run_id=run_id,
                                horizon_hint=horizon,
                            )
                        )
            finally:
                conn.close()

        ranked = _with_ranked_signals(signals)
        return AdapterOutput(
            family=self.family,
            signals=ranked,
            coverage={"days_scanned": scanned_days, "signal_rows": len(ranked)},
        )


# ---------------------------------------------------------------------------
# All-fields focused adapter
# ---------------------------------------------------------------------------


def _qi(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class AllFieldsAdapter:
    family = "all_fields"

    def load(
        self,
        calendar_days: Sequence[ArtifactDay],
        *,
        config: BacktestConfig,
    ) -> AdapterOutput:
        if not config.families.all_fields.enabled:
            return AdapterOutput(family=self.family, signals=[], coverage={"enabled": False})

        wanted_fields = tuple(config.families.all_fields.fields)
        if not wanted_fields:
            return AdapterOutput(
                family=self.family, signals=[], coverage={"enabled": True, "fields": 0}
            )

        duckdb = _import_duckdb()
        signals: list[SignalRow] = []
        day_count = 0
        field_non_null_counts: dict[str, int] = defaultdict(int)
        field_total_counts: dict[str, int] = defaultdict(int)

        for day in calendar_days:
            if day.all_fields_database is None or not day.all_fields_database.exists():
                continue
            if not day.all_fields_run_id:
                continue
            day_count += 1
            conn = duckdb.connect(day.all_fields_database.as_posix(), read_only=True)
            try:
                if not _table_exists(conn, "all_fields_rows"):
                    continue
                columns = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='main' AND table_name='all_fields_rows'"
                    ).fetchall()
                }
                selected_fields = [field for field in wanted_fields if field in columns]
                if not selected_fields:
                    continue
                select_parts = [
                    f"TRY_CAST({_qi(field)} AS DOUBLE) AS {_qi(_normalize_signal_name(field))}"
                    for field in selected_fields
                ]
                query = (
                    "SELECT symbol, "
                    + ", ".join(select_parts)
                    + " FROM all_fields_rows WHERE run_id = ?"
                )
                cursor = conn.execute(query, [day.all_fields_run_id])
                column_names = [str(value[0]) for value in cursor.description]
                rows = cursor.fetchall()
            finally:
                conn.close()

            signal_columns = column_names[1:]
            raw_to_signal = dict(zip(selected_fields, signal_columns, strict=True))
            index_by_column = {name: idx for idx, name in enumerate(column_names)}
            for row in rows:
                symbol = _normalize_symbol(row[0])
                if not symbol:
                    continue
                for raw_field, signal_column in raw_to_signal.items():
                    field_total_counts[raw_field] += 1
                    value = _safe_float(row[index_by_column[signal_column]])
                    if value is None:
                        continue
                    field_non_null_counts[raw_field] += 1
                    signals.append(
                        SignalRow(
                            as_of_date=day.as_of_date,
                            symbol=symbol,
                            family=self.family,
                            signal_name=f"all_fields_{_normalize_signal_name(raw_field)}",
                            score=value,
                            source_path=day.all_fields_database.as_posix(),
                            run_id=day.all_fields_run_id,
                        )
                    )

        fill_rows: list[dict[str, Any]] = []
        for field in wanted_fields:
            total = field_total_counts.get(field, 0)
            non_null = field_non_null_counts.get(field, 0)
            fill_rows.append(
                {
                    "family": self.family,
                    "field_name": field,
                    "row_count": total,
                    "non_null_count": non_null,
                    "fill_rate": (non_null / total) if total else 0.0,
                }
            )

        return AdapterOutput(
            family=self.family,
            signals=_with_ranked_signals(signals),
            diagnostics={"all_fields_fill_rates": fill_rows},
            coverage={
                "days_scanned": day_count,
                "signal_rows": len(signals),
                "configured_fields": len(wanted_fields),
            },
        )


# ---------------------------------------------------------------------------
# Upside / move-potential adapter
# ---------------------------------------------------------------------------


def _percentile(values: Sequence[float | None]) -> list[float]:
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    output = [0.5] * len(values)
    if not indexed:
        return output
    if len(indexed) == 1:
        output[indexed[0][0]] = 0.5
        return output
    indexed.sort(key=lambda item: item[1])
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = avg_rank / (len(indexed) - 1)
        for pos in range(i, j + 1):
            output[indexed[pos][0]] = float(pct)
        i = j + 1
    return output


def _load_all_fields_for_reconstruct(day: ArtifactDay) -> list[dict[str, Any]]:
    if day.all_fields_database is None or not day.all_fields_database.exists():
        return []
    if not day.all_fields_run_id:
        return []
    duckdb = _import_duckdb()
    conn = duckdb.connect(day.all_fields_database.as_posix(), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT symbol,
                   TRY_CAST("ADRP" AS DOUBLE) AS adrp,
                   TRY_CAST(relative_volume_10d_calc AS DOUBLE) AS relvol,
                   TRY_CAST("Recommend.All" AS DOUBLE) AS recommend_all_raw,
                   TRY_CAST("Perf.1M" AS DOUBLE) AS perf_1m
            FROM all_fields_rows
            WHERE run_id = ?
            """,
            [day.all_fields_run_id],
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    output = []
    for symbol, adrp, relvol, recommend_all_raw, perf_1m in rows:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            continue
        output.append(
            {
                "symbol": normalized,
                "adrp": _safe_float(adrp),
                "relvol": _safe_float(relvol),
                "recommend_all_raw": _safe_float(recommend_all_raw),
                "perf_1m": _safe_float(perf_1m),
            }
        )
    return output


class UpsideOpportunityAdapter:
    family = "upside_opportunity"

    def load(
        self,
        calendar_days: Sequence[ArtifactDay],
        *,
        config: BacktestConfig,
    ) -> AdapterOutput:
        if not config.families.upside_opportunity.enabled:
            return AdapterOutput(family=self.family, signals=[], coverage={"enabled": False})

        signals: list[SignalRow] = []
        artifact_days = 0
        reconstructed_days = 0
        coverage_rows: list[dict[str, Any]] = []

        for day in calendar_days:
            day_has_artifact = False
            if day.upside_opportunity_manifest is not None:
                manifest = _safe_json(day.upside_opportunity_manifest)
                candidates_path = Path(
                    manifest.get("candidates_csv")
                    or (day.upside_opportunity_manifest.parent / "upside_opportunity_candidates.csv")
                )
                candidates = _read_csv_rows(candidates_path)
                if candidates:
                    day_has_artifact = True
                    artifact_days += 1
                    for row in candidates:
                        symbol = _normalize_symbol(row.get("symbol"))
                        if not symbol:
                            continue
                        score_columns = {
                            "upside_opportunity_score": row.get("upside_opportunity_score"),
                            "opp_momentum_component": row.get("opp_momentum_component"),
                            "opp_historical_validation_component": row.get(
                                "opp_historical_validation_component"
                            ),
                            "opp_valuation_component": row.get("opp_valuation_component"),
                            "opp_safety_quality_component": row.get(
                                "opp_safety_quality_component"
                            ),
                            "opp_binary_risk_score": row.get("opp_binary_risk_score"),
                            "opp_downside_risk_score": row.get("opp_downside_risk_score"),
                        }
                        for signal_name, raw_value in score_columns.items():
                            score = _safe_float(raw_value)
                            if score is None:
                                continue
                            signals.append(
                                SignalRow(
                                    as_of_date=day.as_of_date,
                                    symbol=symbol,
                                    family=self.family,
                                    signal_name=signal_name,
                                    score=score,
                                    action=str(row.get("directional_lean") or "").strip() or None,
                                    source_path=candidates_path.as_posix(),
                                    run_id=day.edge_parent_dir.name if day.edge_parent_dir else None,
                                )
                            )

            if day.upside_move_manifest is not None:
                manifest = _safe_json(day.upside_move_manifest)
                candidates_path = Path(
                    manifest.get("candidates_csv")
                    or (day.upside_move_manifest.parent / "upside_move_potential_candidates.csv")
                )
                move_rows = _read_csv_rows(candidates_path)
                if move_rows:
                    for row in move_rows:
                        symbol = _normalize_symbol(row.get("symbol"))
                        if not symbol:
                            continue
                        score_columns = {
                            "move_upside_score": row.get("move_upside_score"),
                            "move_magnitude_score": row.get("move_magnitude_score"),
                            "move_upside_tilt_score": row.get("move_upside_tilt_score"),
                            "move_catalyst_score": row.get("move_catalyst_score"),
                            "expected_move_proxy_pct": row.get("expected_move_proxy_pct"),
                        }
                        for signal_name, raw_value in score_columns.items():
                            score = _safe_float(raw_value)
                            if score is None:
                                continue
                            signals.append(
                                SignalRow(
                                    as_of_date=day.as_of_date,
                                    symbol=symbol,
                                    family=self.family,
                                    signal_name=signal_name,
                                    score=score,
                                    action=str(row.get("move_tier") or "").strip() or None,
                                    source_path=candidates_path.as_posix(),
                                    run_id=day.edge_parent_dir.name if day.edge_parent_dir else None,
                                )
                            )

            if (
                not day_has_artifact
                and config.families.upside_opportunity.allow_momentum_reconstruct
            ):
                reconstruct_rows = _load_all_fields_for_reconstruct(day)
                if reconstruct_rows:
                    reconstructed_days += 1
                    adrp_percentiles = _percentile([row.get("adrp") for row in reconstruct_rows])
                    relvol_percentiles = _percentile([row.get("relvol") for row in reconstruct_rows])
                    perf_1m_percentiles = _percentile([row.get("perf_1m") for row in reconstruct_rows])
                    for index, base_row in enumerate(reconstruct_rows):
                        payload = {
                            "adrp_pct_today": adrp_percentiles[index],
                            "relvol_pct_today": relvol_percentiles[index],
                            "mom_core_pct_today": perf_1m_percentiles[index],
                            "recommend_all_raw": base_row.get("recommend_all_raw"),
                            "perf_1m": base_row.get("perf_1m"),
                        }
                        score = compute_momentum_component(payload)
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=base_row["symbol"],
                                family=self.family,
                                signal_name="opp_momentum_component_reconstructed",
                                score=float(score),
                                source_path=(
                                    day.all_fields_database.as_posix()
                                    if day.all_fields_database
                                    else None
                                ),
                                run_id=day.all_fields_run_id,
                                metadata_json=json.dumps({"mode": "reconstructed"}),
                            )
                        )

            coverage_rows.append(
                {
                    "as_of_date": day.as_of_date.isoformat(),
                    "used_artifact": int(day_has_artifact),
                    "used_reconstruct": int(
                        (not day_has_artifact)
                        and config.families.upside_opportunity.allow_momentum_reconstruct
                    ),
                }
            )

        ranked = _with_ranked_signals(signals)
        return AdapterOutput(
            family=self.family,
            signals=ranked,
            diagnostics={"upside_coverage": coverage_rows},
            coverage={
                "artifact_days": artifact_days,
                "reconstructed_days": reconstructed_days,
                "signal_rows": len(ranked),
            },
        )


# ---------------------------------------------------------------------------
# Market timing adapter (artifact + reconstruct + calibration)
# ---------------------------------------------------------------------------


def _percentile_100(values: Sequence[float | None]) -> list[float]:
    indexed = [(idx, value) for idx, value in enumerate(values) if value is not None]
    out = [50.0] * len(values)
    if not indexed:
        return out
    if len(indexed) == 1:
        out[indexed[0][0]] = 50.0
        return out
    indexed.sort(key=lambda item: item[1])
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = (avg_rank / (len(indexed) - 1)) * 100.0
        for pos in range(i, j + 1):
            out[indexed[pos][0]] = pct
        i = j + 1
    return out


def _table_columns(connection: Any, table_name: str) -> set[str]:
    rows = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='main' AND table_name = ?",
        [table_name],
    ).fetchall()
    return {str(row[0]) for row in rows}


def _load_conviction_top_n(day: ArtifactDay, top_n: int) -> list[dict[str, Any]]:
    if day.prediction_database is None or not day.prediction_database.exists():
        return []
    if not day.prediction_run_id:
        return []
    duckdb = _import_duckdb()
    conn = duckdb.connect(day.prediction_database.as_posix(), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT symbol, conviction_score, rank_overall
            FROM conviction_rankings
            WHERE run_id = ?
            ORDER BY rank_overall ASC NULLS LAST, conviction_score DESC NULLS LAST
            """,
            [day.prediction_run_id],
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    resolved: list[dict[str, Any]] = []
    for symbol, conviction_score, rank_overall in rows:
        normalized_symbol = _normalize_symbol(symbol)
        if not normalized_symbol:
            continue
        rank_value = _safe_int(rank_overall)
        if rank_value is not None and rank_value > top_n:
            continue
        resolved.append(
            {
                "symbol": normalized_symbol,
                "conviction_score": _safe_float(conviction_score),
                "rank_overall": rank_value,
            }
        )
        if len(resolved) >= top_n:
            break
    return resolved


def _load_all_fields_snapshot(day: ArtifactDay) -> list[dict[str, Any]]:
    if day.all_fields_database is None or not day.all_fields_database.exists():
        return []
    if not day.all_fields_run_id:
        return []
    duckdb = _import_duckdb()
    conn = duckdb.connect(day.all_fields_database.as_posix(), read_only=True)
    try:
        columns = _table_columns(conn, "all_fields_rows")
        if not columns:
            return []

        def col(name: str, cast: str = "DOUBLE", default: str = "NULL") -> str:
            if name not in columns:
                return f"CAST({default} AS {cast})"
            return f"TRY_CAST(\"{name}\" AS {cast})"

        high_expr_candidates = []
        for candidate in ("High.52W", "high_52_week", "52 Week High"):
            if candidate in columns:
                high_expr_candidates.append(f"TRY_CAST(\"{candidate}\" AS DOUBLE)")
        high_expr = (
            f"COALESCE({', '.join(high_expr_candidates)})"
            if high_expr_candidates
            else "CAST(NULL AS DOUBLE)"
        )

        query = f"""
            SELECT
                symbol,
                CAST(industry AS VARCHAR) AS industry,
                {col("market_cap_basic")} AS market_cap_basic,
                {col("change")} AS current_day_pct,
                {col("Perf.5D")} AS perf_5d,
                {col("Perf.1M")} AS perf_1m,
                {col("ATRP")} AS atrp,
                {col("ADRP")} AS adrp_raw,
                {col("close")} AS close,
                {high_expr} AS high_52w
            FROM all_fields_rows
            WHERE run_id = ?
        """
        rows = conn.execute(query, [day.all_fields_run_id]).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    output: list[dict[str, Any]] = []
    for (
        symbol,
        industry,
        market_cap_basic,
        current_day_pct,
        perf_5d,
        perf_1m,
        atrp,
        adrp_raw,
        close,
        high_52w,
    ) in rows:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            continue
        output.append(
            {
                "symbol": normalized,
                "industry": str(industry or "").strip(),
                "market_cap_basic": _safe_float(market_cap_basic),
                "current_day_pct": _safe_float(current_day_pct),
                "perf_5d": _safe_float(perf_5d),
                "perf_1m": _safe_float(perf_1m),
                "atrp": _safe_float(atrp),
                "adrp_raw": _safe_float(adrp_raw),
                "close": _safe_float(close),
                "high_52w": _safe_float(high_52w),
            }
        )
    return output


def _build_industry_breadth(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    members: dict[str, int] = defaultdict(int)
    up_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        industry = str(row.get("industry") or "").strip()
        if not industry:
            continue
        market_cap = _safe_float(row.get("market_cap_basic"))
        if market_cap is None or market_cap < 500_000_000:
            continue
        members[industry] += 1
        if (_safe_float(row.get("current_day_pct")) or 0.0) >= 3.0:
            up_counts[industry] += 1
    breadth: dict[str, float] = {}
    for industry, total in members.items():
        if total <= 0:
            continue
        breadth[industry] = (100.0 * up_counts.get(industry, 0)) / total
    return breadth


class MarketTimingAdapter:
    family = "market_timing"

    def load(
        self,
        calendar_days: Sequence[ArtifactDay],
        *,
        config: BacktestConfig,
    ) -> AdapterOutput:
        if not config.families.market_timing.enabled:
            return AdapterOutput(family=self.family, signals=[], coverage={"enabled": False})

        ordered_days = sorted(calendar_days, key=lambda item: item.as_of_date)
        signals: list[SignalRow] = []
        calibration_rows: list[dict[str, Any]] = []
        artifact_days = 0
        reconstructed_days = 0

        prior_symbol_change: dict[str, float] = {}
        prior_industry_breadth: dict[str, float] = {}
        rolling_negative_days: dict[str, int] = defaultdict(int)

        for day in ordered_days:
            artifact_policy_by_symbol: dict[str, dict[str, Any]] = {}
            if day.market_timing_manifest is not None and day.market_timing_manifest.exists():
                manifest = _safe_json(day.market_timing_manifest)
                outputs = manifest.get("outputs") or {}
                policy_csv = outputs.get("policy_csv") or (
                    day.market_timing_manifest.parent / "action_policy_recommendations.csv"
                )
                timing_csv = outputs.get("timing_csv") or (
                    day.market_timing_manifest.parent / "timing_checkpoint_scores.csv"
                )
                policy_rows = _read_csv_rows(policy_csv)
                timing_rows = _read_csv_rows(timing_csv)
                if policy_rows:
                    artifact_days += 1
                for row in policy_rows:
                    symbol = _normalize_symbol(row.get("symbol"))
                    if not symbol:
                        continue
                    artifact_policy_by_symbol[symbol] = row
                    score_value = _safe_float(row.get("timing_score"))
                    signals.append(
                        SignalRow(
                            as_of_date=day.as_of_date,
                            symbol=symbol,
                            family=self.family,
                            signal_name="timing_action_artifact",
                            score=score_value,
                            action=str(row.get("action") or "").strip() or None,
                            weight=_safe_float(row.get("normalized_risk_units")),
                            source_path=str(policy_csv),
                            run_id=str(manifest.get("run_id") or ""),
                        )
                    )
                for row in timing_rows:
                    symbol = _normalize_symbol(row.get("symbol"))
                    score_value = _safe_float(row.get("timing_score"))
                    if not symbol or score_value is None:
                        continue
                    signals.append(
                        SignalRow(
                            as_of_date=day.as_of_date,
                            symbol=symbol,
                            family=self.family,
                            signal_name="timing_score_artifact",
                            score=score_value,
                            source_path=str(timing_csv),
                            run_id=str(manifest.get("run_id") or ""),
                        )
                    )

            conviction_rows = _load_conviction_top_n(
                day, config.families.market_timing.reconstruct_top_n
            )
            all_fields_rows = _load_all_fields_snapshot(day)
            breadth = _build_industry_breadth(all_fields_rows) if all_fields_rows else {}
            if conviction_rows and all_fields_rows:
                reconstructed_days += 1
                row_by_symbol = {str(row["symbol"]): row for row in all_fields_rows}
                adrp_percentiles = _percentile_100(
                    [row_by_symbol.get(item["symbol"], {}).get("adrp_raw") for item in conviction_rows]
                )
                reconstructed_policy: dict[str, dict[str, Any]] = {}
                for index, conviction in enumerate(conviction_rows):
                    symbol = conviction["symbol"]
                    current = row_by_symbol.get(symbol)
                    if current is None:
                        continue
                    close = _safe_float(current.get("close"))
                    high_52w = _safe_float(current.get("high_52w"))
                    drawdown = None
                    if close is not None and high_52w not in (None, 0):
                        drawdown = ((close / high_52w) - 1.0) * 100.0
                    industry = str(current.get("industry") or "").strip()
                    prior_pct = prior_symbol_change.get(symbol)
                    consecutive_down = rolling_negative_days.get(symbol, 0)
                    row_for_policy = {
                        "symbol": symbol,
                        "candidate_source": "screen",
                        "upside_prediction_rank": conviction.get("rank_overall"),
                        "screen_rank": conviction.get("rank_overall"),
                        "in_upside_watchlist_flag": 0,
                        "in_screen_coverage_flag": 1,
                        "adrp_percentile": adrp_percentiles[index],
                        "atrp": current.get("atrp"),
                        "drawdown_from_high_pct": drawdown,
                        "consecutive_down_days": consecutive_down,
                        "prior_day_pct": prior_pct,
                        "current_day_pct": current.get("current_day_pct"),
                        "gap_pct": current.get("current_day_pct"),
                        "industry_breadth_up_3pct": breadth.get(industry),
                        "industry_breadth_expanding_flag": (
                            breadth.get(industry, 0.0) > prior_industry_breadth.get(industry, 0.0)
                        ),
                        "perf_5d": current.get("perf_5d"),
                        "perf_1m": current.get("perf_1m"),
                        "conviction_score": conviction.get("conviction_score"),
                    }
                    timing_row, policy_row = evaluate_timing_policy_row(
                        row_for_policy,
                        checkpoint=config.families.market_timing.checkpoint,
                    )
                    reconstructed_policy[symbol] = policy_row
                    signals.append(
                        SignalRow(
                            as_of_date=day.as_of_date,
                            symbol=symbol,
                            family=self.family,
                            signal_name="timing_action_reconstructed",
                            score=_safe_float(policy_row.get("timing_score")),
                            action=str(policy_row.get("action") or "").strip() or None,
                            weight=_safe_float(policy_row.get("normalized_risk_units")),
                            source_path=(
                                day.all_fields_database.as_posix()
                                if day.all_fields_database is not None
                                else None
                            ),
                            run_id=day.all_fields_run_id,
                        )
                    )
                    signals.append(
                        SignalRow(
                            as_of_date=day.as_of_date,
                            symbol=symbol,
                            family=self.family,
                            signal_name="timing_score_reconstructed",
                            score=_safe_float(timing_row.get("timing_score")),
                            source_path=(
                                day.all_fields_database.as_posix()
                                if day.all_fields_database is not None
                                else None
                            ),
                            run_id=day.all_fields_run_id,
                        )
                    )

                # Calibration: artifact-vs-reconstruct action match on shared symbols.
                shared_symbols = sorted(
                    set(artifact_policy_by_symbol).intersection(reconstructed_policy)
                )
                if shared_symbols:
                    matches = 0
                    for symbol in shared_symbols:
                        artifact_action = str(
                            artifact_policy_by_symbol[symbol].get("action") or ""
                        ).strip()
                        reconstructed_action = str(
                            reconstructed_policy[symbol].get("action") or ""
                        ).strip()
                        if artifact_action == reconstructed_action:
                            matches += 1
                    calibration_rows.append(
                        {
                            "as_of_date": day.as_of_date.isoformat(),
                            "symbol_count": len(shared_symbols),
                            "action_match_count": matches,
                            "action_agreement_pct": (100.0 * matches / len(shared_symbols)),
                        }
                    )

            # Carry simple prior-day context across calendar sequence.
            if all_fields_rows:
                current_change_map = {
                    str(row["symbol"]): _safe_float(row.get("current_day_pct"))
                    for row in all_fields_rows
                }
                for symbol, pct in current_change_map.items():
                    if pct is not None and pct < 0:
                        rolling_negative_days[symbol] = rolling_negative_days.get(symbol, 0) + 1
                    else:
                        rolling_negative_days[symbol] = 0
                prior_symbol_change = {
                    symbol: pct for symbol, pct in current_change_map.items() if pct is not None
                }
                prior_industry_breadth = breadth

        ranked = _with_ranked_signals(signals)
        return AdapterOutput(
            family=self.family,
            signals=ranked,
            diagnostics={"timing_calibration": calibration_rows},
            coverage={
                "artifact_days": artifact_days,
                "reconstructed_days": reconstructed_days,
                "signal_rows": len(ranked),
            },
        )


# ---------------------------------------------------------------------------
# Financial projection adapter
# ---------------------------------------------------------------------------


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "t"}


def _load_table_rows(database_path: Path, table_name: str) -> list[dict[str, Any]]:
    if not database_path or not database_path.exists() or not database_path.is_file():
        return []
    duckdb = _import_duckdb()
    conn = duckdb.connect(database_path.as_posix(), read_only=True)
    try:
        cursor = conn.execute(f"SELECT * FROM {table_name}")
        columns = [str(item[0]) for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _fallback_projection_inputs(day: ArtifactDay) -> list[SignalRow]:
    if day.all_fields_database is None or not day.all_fields_database.exists():
        return []
    if not day.all_fields_run_id:
        return []
    duckdb = _import_duckdb()
    conn = duckdb.connect(day.all_fields_database.as_posix(), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT symbol,
                   TRY_CAST(close AS DOUBLE) AS close_price,
                   TRY_CAST(price_target AS DOUBLE) AS price_target,
                   TRY_CAST(total_revenue_yoy_growth_ttm AS DOUBLE) AS revenue_yoy_growth,
                   TRY_CAST(total_revenue_cagr_5y AS DOUBLE) AS revenue_cagr_5y
            FROM all_fields_rows
            WHERE run_id = ?
            """,
            [day.all_fields_run_id],
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    output: list[SignalRow] = []
    for symbol, close_price, price_target, yoy_growth, revenue_cagr in rows:
        ticker = _normalize_symbol(symbol)
        if not ticker:
            continue
        close_value = _safe_float(close_price)
        target_value = _safe_float(price_target)
        upside = None
        if close_value not in (None, 0) and target_value is not None:
            upside = ((target_value / close_value) - 1.0) * 100.0
        if upside is not None:
            output.append(
                SignalRow(
                    as_of_date=day.as_of_date,
                    symbol=ticker,
                    family="all_fields",
                    signal_name="projection_input_price_target_upside_pct",
                    score=upside,
                    source_path=day.all_fields_database.as_posix(),
                    run_id=day.all_fields_run_id,
                )
            )
        yoy_value = _safe_float(yoy_growth)
        if yoy_value is not None:
            output.append(
                SignalRow(
                    as_of_date=day.as_of_date,
                    symbol=ticker,
                    family="all_fields",
                    signal_name="projection_input_total_revenue_yoy_growth_ttm",
                    score=yoy_value,
                    source_path=day.all_fields_database.as_posix(),
                    run_id=day.all_fields_run_id,
                )
            )
        cagr_value = _safe_float(revenue_cagr)
        if cagr_value is not None:
            output.append(
                SignalRow(
                    as_of_date=day.as_of_date,
                    symbol=ticker,
                    family="all_fields",
                    signal_name="projection_input_total_revenue_cagr_5y",
                    score=cagr_value,
                    source_path=day.all_fields_database.as_posix(),
                    run_id=day.all_fields_run_id,
                )
            )
    return output


class FinancialProjectionAdapter:
    family = "financial_projection"

    def load(
        self,
        calendar_days: Sequence[ArtifactDay],
        *,
        config: BacktestConfig,
    ) -> AdapterOutput:
        if not config.families.financial_projection.enabled:
            return AdapterOutput(family=self.family, signals=[], coverage={"enabled": False})

        signals: list[SignalRow] = []
        growth_days = 0
        price_days = 0
        fallback_days = 0

        for day in calendar_days:
            day_has_projection = False

            if day.financial_projection_growth_metadata is not None:
                metadata = _safe_json(day.financial_projection_growth_metadata)
                database_raw = str(metadata.get("database_path") or "").strip()
                if not database_raw:
                    database_raw = day.financial_projection_growth_metadata.parent.name + ".duckdb"
                database_path = (
                    Path(database_raw)
                    if Path(database_raw).is_absolute()
                    else (day.financial_projection_growth_metadata.parent / database_raw)
                )
                rows = _load_table_rows(database_path, "growth_summary")
                if rows:
                    growth_days += 1
                    day_has_projection = True
                own_rows: dict[str, dict[str, Any]] = {}
                peer_rows: dict[str, dict[str, Any]] = {}
                for row in rows:
                    if str(row.get("scenario") or "").lower() != "base":
                        continue
                    if not _is_truthy(row.get("valid")):
                        continue
                    symbol = _normalize_symbol(row.get("symbol"))
                    if not symbol:
                        continue
                    lane = str(row.get("growth_lane") or "").lower()
                    if lane == "own":
                        own_rows[symbol] = row
                    elif lane == "peer":
                        peer_rows[symbol] = row
                for symbol, row in own_rows.items():
                    cagr = _safe_float(row.get("revenue_cagr_implied_pct"))
                    y1_growth = _safe_float(row.get("y1_revenue_growth_pct"))
                    peer = peer_rows.get(symbol)
                    peer_cagr = _safe_float(peer.get("revenue_cagr_implied_pct")) if peer else None
                    if cagr is not None:
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol,
                                family=self.family,
                                signal_name="revenue_cagr_implied_pct",
                                score=cagr,
                                source_path=database_path.as_posix(),
                                run_id=str(metadata.get("run_id") or ""),
                            )
                        )
                    if y1_growth is not None:
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol,
                                family=self.family,
                                signal_name="y1_revenue_growth_pct",
                                score=y1_growth,
                                source_path=database_path.as_posix(),
                                run_id=str(metadata.get("run_id") or ""),
                            )
                        )
                    if cagr is not None and peer_cagr is not None:
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol,
                                family=self.family,
                                signal_name="own_minus_peer_revenue_cagr_pp",
                                score=cagr - peer_cagr,
                                source_path=database_path.as_posix(),
                                run_id=str(metadata.get("run_id") or ""),
                            )
                        )

            if day.financial_projection_price_metadata is not None:
                metadata = _safe_json(day.financial_projection_price_metadata)
                database_raw = str(metadata.get("database_path") or "").strip()
                if not database_raw:
                    database_raw = day.financial_projection_price_metadata.parent.name + ".duckdb"
                database_path = (
                    Path(database_raw)
                    if Path(database_raw).is_absolute()
                    else (day.financial_projection_price_metadata.parent / database_raw)
                )
                rows = _load_table_rows(database_path, "projection_summary")
                if rows:
                    price_days += 1
                    day_has_projection = True
                for row in rows:
                    if str(row.get("scenario") or "").lower() != "base":
                        continue
                    if not _is_truthy(row.get("valid")):
                        continue
                    symbol = _normalize_symbol(row.get("symbol"))
                    if not symbol:
                        continue
                    upside = _safe_float(row.get("primary_upside_pct"))
                    if upside is not None:
                        signals.append(
                            SignalRow(
                                as_of_date=day.as_of_date,
                                symbol=symbol,
                                family=self.family,
                                signal_name="primary_upside_pct",
                                score=upside,
                                source_path=database_path.as_posix(),
                                run_id=str(metadata.get("run_id") or ""),
                            )
                        )
                    rank_eligible = _is_truthy(row.get("rank_eligible"))
                    signals.append(
                        SignalRow(
                            as_of_date=day.as_of_date,
                            symbol=symbol,
                            family=self.family,
                            signal_name="rank_eligible",
                            score=1.0 if rank_eligible else 0.0,
                            action="eligible" if rank_eligible else "ineligible",
                            source_path=database_path.as_posix(),
                            run_id=str(metadata.get("run_id") or ""),
                        )
                    )

            if not day_has_projection:
                fallback_rows = _fallback_projection_inputs(day)
                if fallback_rows:
                    fallback_days += 1
                    signals.extend(fallback_rows)

        ranked = _with_ranked_signals(signals)
        return AdapterOutput(
            family=self.family,
            signals=ranked,
            coverage={
                "growth_days": growth_days,
                "price_days": price_days,
                "fallback_days": fallback_days,
                "signal_rows": len(ranked),
            },
        )

