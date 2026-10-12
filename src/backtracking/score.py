from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from db.trading_view_move_prediction_duckdb import query_move_prediction_duckdb
from generic_utils.ranking import to_float
from generic_utils.scan_sources import rows_from_csv, rows_from_duckdb, rows_to_csv

from .contracts import DecisionWeek
from .reporting import load_run_manifest, record_score_run, resolve_run_dir
from .timeline import day_label, latest_all_fields_on_or_before


def _load_decision_weeks(run_dir: Path) -> list[DecisionWeek]:
    db_path = run_dir / "backtracking.duckdb"
    rows = rows_from_duckdb(
        db_path,
        "SELECT * FROM decision_weeks ORDER BY decision_date",
    )
    out: list[DecisionWeek] = []
    for row in rows:
        decision_text = str(row.get("decision_date") or "").strip()
        decision_date = date.fromisoformat(decision_text)
        out.append(
            DecisionWeek(
                decision_date=decision_date,
                decision_day_label=str(row.get("decision_day_label") or day_label(decision_date)),
                iso_year=int(row.get("iso_year") or decision_date.isocalendar().year),
                iso_week=int(row.get("iso_week") or decision_date.isocalendar().week),
                prediction_database=Path(str(row.get("prediction_database"))),
                prediction_run_id=str(row.get("prediction_run_id") or ""),
                all_fields_database=(
                    Path(str(row.get("all_fields_database")))
                    if row.get("all_fields_database") not in (None, "")
                    else None
                ),
                all_fields_run_id=(
                    str(row.get("all_fields_run_id"))
                    if row.get("all_fields_run_id") not in (None, "")
                    else None
                ),
                all_fields_day_label=(
                    str(row.get("all_fields_day_label"))
                    if row.get("all_fields_day_label") not in (None, "")
                    else None
                ),
                all_fields_lag_days=(
                    int(row.get("all_fields_lag_days"))
                    if row.get("all_fields_lag_days") not in (None, "")
                    else None
                ),
                edge_parent_dir=(
                    Path(str(row.get("edge_parent_dir")))
                    if row.get("edge_parent_dir") not in (None, "")
                    else None
                ),
                edge_unified_database=(
                    Path(str(row.get("edge_unified_database")))
                    if row.get("edge_unified_database") not in (None, "")
                    else None
                ),
            )
        )
    return out


def _load_close_map(all_fields_database: Path, run_id: str | None) -> dict[str, float]:
    tables = {
        str(row.get("table_name") or "")
        for row in query_move_prediction_duckdb(
            all_fields_database,
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'",
        )
    }
    if "all_fields_rows" not in tables:
        return {}
    columns = {
        str(row.get("column_name") or "")
        for row in query_move_prediction_duckdb(
            all_fields_database,
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='main' AND table_name='all_fields_rows'
            """,
        )
    }
    if "close" not in columns:
        return {}
    where_sql = "WHERE run_id = ?" if run_id else ""
    params: list[Any] = [run_id] if run_id else []
    rows = query_move_prediction_duckdb(
        all_fields_database,
        f"SELECT symbol, TRY_CAST(close AS DOUBLE) AS close_price FROM all_fields_rows {where_sql}",
        params,
    )
    closes: dict[str, float] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        close_price = to_float(row.get("close_price"))
        if not symbol or close_price is None:
            continue
        closes[symbol] = close_price
    return closes


def _render_markdown(
    *,
    run_id: str,
    selector: str,
    picks_file: Path,
    coverage_rows: Sequence[Mapping[str, Any]],
    book_rows: Sequence[Mapping[str, Any]],
    name_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Weekly Backtracking Investing Run",
        "",
        f"- run_id: `{run_id}`",
        f"- selector: `{selector}`",
        f"- picks: `{picks_file.name}`",
        f"- decision weeks: `{len(coverage_rows)}`",
        f"- scored names: `{sum(1 for row in name_rows if int(row.get('scored') or 0) == 1)}`",
        "",
        "## Coverage",
        "",
    ]
    for row in coverage_rows:
        lines.append(
            "- "
            + f"{row.get('decision_date')} "
            + f"(edge={int(row.get('has_edge') or 0)}, "
            + f"all_fields_lag_days={row.get('all_fields_lag_days')})"
        )
    lines.extend(["", "## Weekly Book Returns", ""])
    if not book_rows:
        lines.append("- no scored weeks")
    else:
        for row in book_rows:
            lines.append(
                "- "
                + f"{row.get('decision_date')} -> {row.get('exit_date')}: "
                + f"{row.get('book_return_pct')}% "
                + f"(scored={row.get('scored_names')}, unscored={row.get('unscored_names')})"
            )
    lines.extend(["", "## Name Path", ""])
    if not name_rows:
        lines.append("- no picks")
    else:
        for row in name_rows:
            if int(row.get("scored") or 0) == 1:
                lines.append(
                    "- "
                    + f"{row.get('decision_date')} {row.get('symbol')}: "
                    + f"entry={row.get('entry_close')} exit={row.get('exit_close')} "
                    + f"return={row.get('return_pct')}%"
                )
            else:
                lines.append(
                    "- "
                    + f"{row.get('decision_date')} {row.get('symbol')}: "
                    + f"unscored ({row.get('unscored_reason')})"
                )
    lines.append("")
    return "\n".join(lines)


def _resolve_exit_map(
    *,
    decision_weeks: Sequence[DecisionWeek],
    all_fields_root: Path,
    end_day_label: str | None,
) -> dict[str, dict[str, Any]]:
    ordered = sorted(decision_weeks, key=lambda item: item.decision_date)
    final_snapshot = latest_all_fields_on_or_before(
        all_fields_root=all_fields_root,
        end_day_label=end_day_label,
    )
    final_ref: dict[str, Any] | None = None
    if final_snapshot is not None:
        final_day, final_payload = final_snapshot
        final_ref = {
            "exit_date": final_day.isoformat(),
            "all_fields_database": final_payload.database,
            "all_fields_run_id": final_payload.run_id,
            "all_fields_day_label": day_label(final_day),
        }

    result: dict[str, dict[str, Any]] = {}
    for idx, week in enumerate(ordered):
        key = week.decision_date.isoformat()
        if idx + 1 < len(ordered):
            nxt = ordered[idx + 1]
            result[key] = {
                "exit_date": nxt.decision_date.isoformat(),
                "all_fields_database": nxt.all_fields_database,
                "all_fields_run_id": nxt.all_fields_run_id,
                "all_fields_day_label": nxt.all_fields_day_label,
            }
        else:
            result[key] = final_ref or {
                "exit_date": None,
                "all_fields_database": None,
                "all_fields_run_id": None,
                "all_fields_day_label": None,
            }
    return result


def _normalize_week_weights(rows: Sequence[dict[str, Any]]) -> list[float]:
    if not rows:
        return []
    parsed = [to_float(row.get("weight")) for row in rows]
    all_provided = all(value is not None for value in parsed)
    if all_provided and sum(value or 0.0 for value in parsed) > 0:
        weights = [float(value or 0.0) for value in parsed]
    else:
        weights = [1.0 for _ in rows]
    total = sum(weights)
    if total <= 0:
        return [1.0 / len(rows) for _ in rows]
    return [value / total for value in weights]


def score_run(
    *,
    run_dir: str | Path,
    picks_path: str | Path,
) -> dict[str, Any]:
    run_path = resolve_run_dir(run_dir)
    manifest = load_run_manifest(run_path)
    picks_file = Path(picks_path)
    if not picks_file.is_absolute():
        picks_file = run_path / picks_file
    if not picks_file.exists():
        raise FileNotFoundError(f"Picks file not found: {picks_file}")

    picks_rows = rows_from_csv(picks_file)
    if not picks_rows:
        raise ValueError(f"Picks file is empty: {picks_file}")

    decision_weeks = _load_decision_weeks(run_path)
    week_by_date = {week.decision_date.isoformat(): week for week in decision_weeks}
    end_day_label = (
        (manifest.get("window") or {}).get("end_day_label")
        or manifest.get("end_day_label")
    )
    all_fields_root = Path((manifest.get("paths") or {}).get("all_fields_root") or "")
    exit_map = _resolve_exit_map(
        decision_weeks=decision_weeks,
        all_fields_root=all_fields_root,
        end_day_label=end_day_label,
    )

    close_cache: dict[tuple[str, str], dict[str, float]] = {}

    def _closes(db_path: Path | None, run_id: str | None) -> dict[str, float]:
        if db_path is None:
            return {}
        key = (db_path.as_posix(), str(run_id or ""))
        cached = close_cache.get(key)
        if cached is not None:
            return cached
        loaded = _load_close_map(db_path, run_id)
        close_cache[key] = loaded
        return loaded

    name_returns: list[dict[str, Any]] = []
    for pick in picks_rows:
        decision_date = str(pick.get("decision_date") or "").strip()
        symbol = str(pick.get("symbol") or "").strip().upper()
        selector = str(pick.get("selector") or "").strip() or picks_file.stem.replace("picks_", "")
        base = {
            "decision_date": decision_date,
            "symbol": symbol,
            "selector": selector,
            "weight": pick.get("weight"),
            "source_layer": pick.get("source_layer"),
            "cited_fields": pick.get("cited_fields"),
            "note": pick.get("note"),
        }
        week = week_by_date.get(decision_date)
        if week is None:
            name_returns.append(
                {
                    **base,
                    "entry_close": None,
                    "exit_close": None,
                    "exit_date": None,
                    "return_pct": None,
                    "scored": 0,
                    "unscored_reason": "decision_date_not_in_run",
                }
            )
            continue

        exit_ref = exit_map.get(decision_date) or {}
        entry_db = week.all_fields_database
        exit_db = exit_ref.get("all_fields_database")
        entry_run_id = week.all_fields_run_id
        exit_run_id = exit_ref.get("all_fields_run_id")
        entry_day_label = week.all_fields_day_label
        exit_day_label = exit_ref.get("all_fields_day_label")
        entry_close = _closes(entry_db, entry_run_id).get(symbol)
        exit_close = _closes(exit_db, exit_run_id).get(symbol) if isinstance(exit_db, Path) else None

        unscored_reason = None
        if entry_close is None:
            unscored_reason = "missing_entry_close"
        elif exit_close is None:
            unscored_reason = "missing_exit_close"
        elif entry_close == 0:
            unscored_reason = "entry_close_zero"
        elif entry_day_label and exit_day_label and entry_day_label == exit_day_label:
            unscored_reason = "entry_and_exit_same_snapshot"

        if unscored_reason is not None:
            name_returns.append(
                {
                    **base,
                    "entry_close": entry_close,
                    "exit_close": exit_close,
                    "exit_date": exit_ref.get("exit_date"),
                    "return_pct": None,
                    "scored": 0,
                    "unscored_reason": unscored_reason,
                }
            )
            continue

        return_pct = round(((float(exit_close) / float(entry_close)) - 1.0) * 100.0, 6)
        name_returns.append(
            {
                **base,
                "entry_close": round(float(entry_close), 8),
                "exit_close": round(float(exit_close), 8),
                "exit_date": exit_ref.get("exit_date"),
                "return_pct": return_pct,
                "scored": 1,
                "unscored_reason": "",
            }
        )

    by_week: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in name_returns:
        by_week[str(row.get("decision_date") or "")].append(row)

    book_rows: list[dict[str, Any]] = []
    for decision_date in sorted(by_week):
        week_rows = by_week[decision_date]
        scored = [row for row in week_rows if int(row.get("scored") or 0) == 1]
        unscored = [row for row in week_rows if int(row.get("scored") or 0) == 0]
        if not scored:
            book_rows.append(
                {
                    "decision_date": decision_date,
                    "exit_date": None,
                    "selector": (week_rows[0].get("selector") if week_rows else None),
                    "scored_names": 0,
                    "unscored_names": len(unscored),
                    "book_return_pct": None,
                }
            )
            continue
        weights = _normalize_week_weights(scored)
        book_return = 0.0
        for row, weight in zip(scored, weights, strict=True):
            row["weight_used"] = round(weight, 8)
            book_return += weight * float(row.get("return_pct") or 0.0)
        exit_date = next(
            (str(row.get("exit_date") or "") for row in scored if row.get("exit_date")),
            "",
        )
        book_rows.append(
            {
                "decision_date": decision_date,
                "exit_date": exit_date or None,
                "selector": scored[0].get("selector"),
                "scored_names": len(scored),
                "unscored_names": len(unscored),
                "book_return_pct": round(book_return, 6),
            }
        )

    name_returns_path = rows_to_csv(run_path / "name_returns.csv", name_returns)
    book_weeks_path = rows_to_csv(run_path / "book_weeks.csv", book_rows)

    coverage_rows = [
        {
            "decision_date": week.decision_date.isoformat(),
            "has_edge": int(week.edge_unified_database is not None),
            "all_fields_lag_days": week.all_fields_lag_days,
        }
        for week in decision_weeks
    ]
    selector_name = str(picks_rows[0].get("selector") or picks_file.stem.replace("picks_", ""))
    markdown = _render_markdown(
        run_id=str(manifest.get("run_id") or run_path.name),
        selector=selector_name,
        picks_file=picks_file,
        coverage_rows=coverage_rows,
        book_rows=book_rows,
        name_rows=name_returns,
    )
    investing_report_path = run_path / "investing_run.md"
    investing_report_path.write_text(markdown, encoding="utf-8")

    record_score_run(
        run_dir=run_path,
        selector=selector_name,
        picks_path=picks_file,
        book_rows=book_rows,
        name_rows=name_returns,
    )
    return {
        "run_dir": run_path.as_posix(),
        "selector": selector_name,
        "picks_path": picks_file.as_posix(),
        "name_returns_path": name_returns_path.as_posix(),
        "book_weeks_path": book_weeks_path.as_posix(),
        "investing_report_path": investing_report_path.as_posix(),
        "scored_names": sum(1 for row in name_returns if int(row.get("scored") or 0) == 1),
        "unscored_names": sum(1 for row in name_returns if int(row.get("scored") or 0) == 0),
        "book_weeks": len(book_rows),
    }
