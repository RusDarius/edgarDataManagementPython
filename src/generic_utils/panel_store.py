"""Point-in-time panel store: the all-fields day files as one DuckDB, appended file by file.

Tables (knobs in config/generic_utils/panel_store.json):

- ``panel``: one row per (as_of, symbol) from each day file's newest run inside the ingest floor, raw
  TradingView fields under short names (a field a file lacks is NULL).
- ``pred``: the move-prediction scores (consensus per horizon, risk-adjusted, profile weeks, conviction) of the
  newest prediction run created at or before that day file's scan, on the exact EXCHANGE:TICKER id.
- ``ingest_log``: the file, run and scan time behind each as_of, and the prediction run joined to it.
- ``series``: ``split_flag`` and ``split_seq`` (a running split count). A window or label across a split is empty.
- ``prints``: one row per detected print (a new ``earnings_release_date``) with its reaction file, move, surprises
  and the closes around it, through earnings_week.print_reactions.
- ``labels``: ``fwd_<h>`` from each as_of's close to the first later file at least ``days`` later (within the
  tolerance), and ``lab_<h>`` the file it used.
- ``features``: the recipe's feature expressions per (as_of, symbol), point-in-time: that file, earlier files,
  the prediction run before the scan, and prints whose reaction file is on or before the as_of.

Re-running ingests only day files (or newer runs of a day) not yet in the store, then rebuilds the derived
tables. No ranking and no cutoff beyond the ingest floor: the study floor belongs to the reader.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.series import qident

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "panel_store.json"


def load_store_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def _abs(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def default_store_path(recipe: Mapping[str, Any] | None = None) -> Path:
    return _abs(str((recipe or load_store_recipe()).get("path")))


def _num(value: Any) -> str:
    """A recipe number as a SQL literal (knobs are formatted in, never user text)."""
    return repr(float(value))


# --- schema ------------------------------------------------------------------------------------------------


def pred_columns(recipe: Mapping[str, Any]) -> list[str]:
    spec = dict(recipe.get("prediction") or {})
    cols = list((spec.get("consensus") or {}).values()) + list((spec.get("risk_adjusted") or {}).values())
    cols += list((spec.get("profiles_weeks") or {}).values())
    if spec.get("conviction"):
        cols.append(str(spec["conviction"]))
    return cols


def ensure_schema(con: Any, recipe: Mapping[str, Any]) -> None:
    """Create the base tables, and add any column a newer recipe names (a rebuild then fills it)."""
    text = list((recipe.get("text_fields") or {}).keys())
    numeric = list((recipe.get("fields") or {}).keys())
    con.execute("CREATE TABLE IF NOT EXISTS panel (as_of DATE, run_id VARCHAR, symbol VARCHAR)")
    con.execute("CREATE TABLE IF NOT EXISTS pred (as_of DATE, pred_run VARCHAR, symbol VARCHAR)")
    con.execute("CREATE TABLE IF NOT EXISTS ingest_log (as_of DATE, path VARCHAR, run_id VARCHAR, scan_at TIMESTAMP, "
                "n BIGINT, pred_run VARCHAR, pred_n BIGINT, ingested_at TIMESTAMP)")
    for table, wanted in (("panel", [(c, "VARCHAR") for c in text] + [(c, "DOUBLE") for c in numeric]),
                          ("pred", [(c, "DOUBLE") for c in pred_columns(recipe)])):
        have = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]).fetchall()}
        for col, kind in wanted:
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {qident(col)} {kind}")


# --- ingest ------------------------------------------------------------------------------------------------


def _naive_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    return None


def _newest_run(con: Any, alias: str) -> tuple[str | None, datetime | None]:
    try:
        row = con.execute(f"SELECT run_id, created_at_utc FROM {alias}.run_metadata "
                          "ORDER BY created_at_utc DESC NULLS LAST, run_id DESC LIMIT 1").fetchone()
    except Exception:  # noqa: BLE001 - a day file without run_metadata is not ingested
        return None, None
    return (str(row[0]), _naive_utc(row[1])) if row else (None, None)


def numeric_pick(columns: str | Sequence[str], present: set[str]) -> str:
    """SQL for one panel field: a column name, or a list read in order (the first non-null of the columns the day file
    has). A list lets a field survive a scan that fetched a sibling column only (the focused intraday export carries
    price_book_fq, not price_book_ratio)."""
    names = [columns] if isinstance(columns, str) else list(columns)
    parts = [f"TRY_CAST({qident(c)} AS DOUBLE)" for c in names if c in present]
    if not parts:
        return "CAST(NULL AS DOUBLE)"
    return parts[0] if len(parts) == 1 else f"COALESCE({', '.join(parts)})"


def ingest_day_file(con: Any, source: Mapping[str, Any], recipe: Mapping[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Load one day file's newest run inside the ingest floor. Skips a day already stored from the same run."""
    path = Path(source["path"])
    as_of = source.get("as_of_date") or date.fromisoformat(str(source["as_of"]))
    con.execute(f"ATTACH '{path.as_posix()}' AS src (READ_ONLY)")
    try:
        run_id, scan_at = _newest_run(con, "src")
        if run_id is None:
            return {"as_of": as_of.isoformat(), "status": "no_run"}
        logged = con.execute("SELECT run_id FROM ingest_log WHERE as_of = ?", [as_of]).fetchone()
        if logged and logged[0] == run_id and not force:
            return {"as_of": as_of.isoformat(), "status": "kept", "run_id": run_id}
        present = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_catalog = 'src' AND table_name = 'all_fields_rows'").fetchall()}
        select = []
        for short, col in (recipe.get("text_fields") or {}).items():
            select.append(f"CAST({qident(col)} AS VARCHAR) AS {qident(short)}" if col in present else f"CAST(NULL AS VARCHAR) AS {qident(short)}")
        for short, col in (recipe.get("fields") or {}).items():
            select.append(f"{numeric_pick(col, present)} AS {qident(short)}")
        floor = dict(recipe.get("floor") or {})
        where = ["run_id = ?", "TRY_CAST(market_cap_basic AS DOUBLE) >= ?"]
        params: list[Any] = [as_of, run_id, run_id, float(floor.get("min_market_cap_usd") or 0)]
        for key, col in (("markets", "market"), ("types", "type"), ("exchanges", "exchange")):
            values = list(floor.get(key) or [])
            if values and col in present:
                where.append(f"{col} IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        con.execute("DELETE FROM panel WHERE as_of = ?", [as_of])
        con.execute(
            f"INSERT INTO panel BY NAME SELECT CAST(? AS DATE) AS as_of, ? AS run_id, symbol, {', '.join(select)} "
            f"FROM src.all_fields_rows WHERE {' AND '.join(where)} "
            "QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY TRY_CAST(market_cap_basic AS DOUBLE) DESC NULLS LAST) = 1",
            params)
        n = con.execute("SELECT count(*) FROM panel WHERE as_of = ?", [as_of]).fetchone()[0]
        con.execute("DELETE FROM ingest_log WHERE as_of = ?", [as_of])
        con.execute("INSERT INTO ingest_log VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
                    [as_of, path.as_posix(), run_id, scan_at, n, datetime.now(timezone.utc).replace(tzinfo=None)])
        return {"as_of": as_of.isoformat(), "status": "ingested", "run_id": run_id, "n": n}
    finally:
        con.execute("DETACH src")


def _prediction_dbs(root: Path, as_of: date) -> list[Path]:
    out: list[Path] = []
    for day in (as_of, as_of - timedelta(days=7)):
        year, week, _ = day.isocalendar()
        out.extend(sorted((root / f"iso_year={year}" / f"week={week}").glob("move_prediction_*.duckdb")))
    return out


def ingest_prediction(con: Any, as_of: date, scan_at: datetime | None, recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Join the newest move-prediction run created at or before the scan onto that as_of's panel ids."""
    spec = dict(recipe.get("prediction") or {})
    root = _abs(str(spec.get("root") or ""))
    if scan_at is None or not root.exists():
        return {"as_of": as_of.isoformat(), "status": "no_prediction_root"}
    best: tuple[datetime, str, Path] | None = None
    for db in _prediction_dbs(root, as_of):
        con.execute(f"ATTACH '{db.as_posix()}' AS pdb (READ_ONLY)")
        try:
            row = con.execute("SELECT run_id, created_at_utc FROM pdb.run_metadata WHERE created_at_utc <= ? "
                              "ORDER BY created_at_utc DESC LIMIT 1", [scan_at]).fetchone()
        except Exception:  # noqa: BLE001 - a week DB without run_metadata has no usable run
            row = None
        finally:
            con.execute("DETACH pdb")
        if row and row[1] is not None:
            created = _naive_utc(row[1])
            if best is None or created > best[0]:
                best = (created, str(row[0]), db)
    if best is None or (scan_at - best[0]).total_seconds() > float(spec.get("max_age_days", 4)) * 86400:
        return {"as_of": as_of.isoformat(), "status": "no_run_before_scan"}
    _, run_id, db = best
    con.execute(f"ATTACH '{db.as_posix()}' AS pdb (READ_ONLY)")
    try:
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_catalog = 'pdb'").fetchall()}
        parts = ["WITH ids AS (SELECT symbol FROM panel WHERE as_of = ?)"]
        params: list[Any] = [as_of]
        joins, cols = [], []
        if "consensus_horizon_scores" in tables:
            picks = [f"max(score) FILTER (WHERE horizon_name = '{h}') AS {qident(dest)}"
                     for h, dest in (spec.get("consensus") or {}).items()]
            picks += [f"max(risk_adjusted_score) FILTER (WHERE horizon_name = '{h}') AS {qident(dest)}"
                      for h, dest in (spec.get("risk_adjusted") or {}).items()]
            parts.append(f", c AS (SELECT symbol, {', '.join(picks)} FROM pdb.consensus_horizon_scores WHERE run_id = ? GROUP BY symbol)")
            params.append(run_id)
            joins.append("LEFT JOIN c USING (symbol)")
            cols += list((spec.get("consensus") or {}).values()) + list((spec.get("risk_adjusted") or {}).values())
        profiles = dict(spec.get("profiles_weeks") or {})
        if profiles and "profile_horizon_scores" in tables:
            picks = [f"max(score) FILTER (WHERE profile_name = '{p}') AS {qident(dest)}" for p, dest in profiles.items()]
            parts.append(f", p AS (SELECT symbol, {', '.join(picks)} FROM pdb.profile_horizon_scores "
                         "WHERE run_id = ? AND horizon_name = 'weeks' GROUP BY symbol)")
            params.append(run_id)
            joins.append("LEFT JOIN p USING (symbol)")
            cols += list(profiles.values())
        if spec.get("conviction") and "conviction_rankings" in tables:
            parts.append(f", v AS (SELECT symbol, max(conviction_score) AS {qident(spec['conviction'])} "
                         "FROM pdb.conviction_rankings WHERE run_id = ? GROUP BY symbol)")
            params.append(run_id)
            joins.append("LEFT JOIN v USING (symbol)")
            cols.append(str(spec["conviction"]))
        if not cols:
            return {"as_of": as_of.isoformat(), "status": "no_score_tables", "pred_run": run_id}
        select = ", ".join(qident(c) for c in cols)
        con.execute("DELETE FROM pred WHERE as_of = ?", [as_of])
        con.execute(f"INSERT INTO pred BY NAME {' '.join(parts)} SELECT CAST(? AS DATE) AS as_of, ? AS pred_run, ids.symbol, {select} "
                    f"FROM ids {' '.join(joins)}", params + [as_of, run_id])
        n = con.execute(f"SELECT count(*) FROM pred WHERE as_of = ? AND {qident(cols[0])} IS NOT NULL", [as_of]).fetchone()[0]
        con.execute("UPDATE ingest_log SET pred_run = ?, pred_n = ? WHERE as_of = ?", [run_id, n, as_of])
        return {"as_of": as_of.isoformat(), "status": "joined", "pred_run": run_id, "n": n}
    finally:
        con.execute("DETACH pdb")


# --- derived tables ----------------------------------------------------------------------------------------


def build_series(con: Any, recipe: Mapping[str, Any]) -> None:
    guard = dict(recipe.get("split_guard") or {})
    gap, jump, day_max = int(guard.get("max_gap_days", 5)), _num(guard.get("jump_ratio", 1.8)), _num(guard.get("day_change_max_pct", 25.0))
    con.execute(f"""
        CREATE OR REPLACE TABLE series AS
        WITH s AS (
            SELECT symbol, as_of, close, change, lag(close) OVER w AS prev_close, lag(as_of) OVER w AS prev_as_of
            FROM panel WINDOW w AS (PARTITION BY symbol ORDER BY as_of)),
        f AS (
            SELECT symbol, as_of,
                   CASE WHEN prev_close > 0 AND close > 0 AND as_of - prev_as_of <= {gap}
                             AND (close / prev_close >= {jump} OR close / prev_close <= 1.0 / {jump})
                             AND abs(COALESCE(change, 0)) < {day_max} THEN 1 ELSE 0 END AS split_flag
            FROM s)
        SELECT symbol, as_of, split_flag,
               sum(split_flag) OVER (PARTITION BY symbol ORDER BY as_of ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS split_seq
        FROM f""")


def build_labels(con: Any, recipe: Mapping[str, Any]) -> None:
    tol = int(recipe.get("label_tolerance_days", 4))
    cols, joins = [], []
    for k, h in enumerate(recipe.get("horizons") or []):
        name, days, alias = str(h["name"]), int(h["days"]), f"h{k}"
        ok = f"{alias}.split_seq = a.split_seq AND {alias}.as_of - a.as_of <= {days + tol}"
        cols.append(f"CASE WHEN {ok} THEN round(({alias}.close / a.close - 1) * 100, 4) END AS {qident('fwd_' + name)}")
        cols.append(f"CASE WHEN {ok} THEN {alias}.as_of END AS {qident('lab_' + name)}")
        joins.append(f"ASOF LEFT JOIN b {alias} ON a.symbol = {alias}.symbol AND a.as_of + {days} <= {alias}.as_of")
    con.execute(f"""
        CREATE OR REPLACE TABLE labels AS
        WITH b AS (SELECT p.symbol, p.as_of, p.close, s.split_seq FROM panel p JOIN series s USING (symbol, as_of) WHERE p.close > 0)
        SELECT a.symbol, a.as_of, {', '.join(cols)} FROM b a {' '.join(joins)}""")


PRINT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("symbol", "VARCHAR"), ("ind", "VARCHAR"), ("print_date", "DATE"), ("slot", "VARCHAR"), ("react_on", "DATE"),
    ("pre_file", "DATE"), ("react_file", "DATE"), ("react", "DOUBLE"), ("react_px", "DOUBLE"), ("react_relvol", "DOUBLE"),
    ("runup", "DOUBLE"), ("drift", "DOUBLE"), ("eps_surprise", "DOUBLE"), ("rev_surprise", "DOUBLE"),
    ("pre_close", "DOUBLE"), ("react_close", "DOUBLE"), ("mcap_pre", "DOUBLE"), ("split_seq", "DOUBLE"),
    ("prev_react", "DOUBLE"), ("note", "VARCHAR"),
)


def build_prints(con: Any, recipe: Mapping[str, Any]) -> int:
    """Detect prints per name from ``earn_last`` and measure each through earnings_week.print_reactions."""
    from generic_utils.earnings_week import load_recipe as load_earnings_recipe
    from generic_utils.earnings_week import past_prints, print_reactions

    ew = load_earnings_recipe()
    gap = int((ew.get("reaction") or {}).get("min_days_between_prints", 20))
    cur = con.execute("""
        SELECT p.symbol, p.as_of, p.close, p.change, p.relvol, p.earn_last, p.earn_last_time, p.last_surprise,
               p.rev_surprise_tv, p.rev_last_q, p.rev_est_last_q, p.mcap, p.ind, s.split_seq
        FROM panel p JOIN series s USING (symbol, as_of) ORDER BY p.symbol, p.as_of""")
    grouped: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for row in cur.fetchall():
        grouped[row[0]].append(row)
    out: list[list[Any]] = []
    for symbol, rows in grouped.items():
        points = [{"as_of": r[1].isoformat(), "close": r[2], "change": r[3], "relative_volume_10d_calc": r[4],
                   "earnings_release_date": r[5], "earnings_release_time": r[6], "eps_surprise_percent_fq": r[7],
                   "revenue_surprise_percent_fq": r[8], "total_revenue_fq": r[9], "revenue_forecast_fq": r[10]}
                  for r in rows]
        by_day = {r[1].isoformat(): r for r in rows}
        prev = None
        for rec in print_reactions(points, past_prints(points, rows[-1][1] + timedelta(days=1), min_days_apart=gap), ew):
            pre = by_day.get(rec.get("pre_file") or "")
            hit = by_day.get(rec.get("react_file") or "")
            out.append([symbol, (hit or rows[-1])[12], rec["print_date"], rec["slot"], rec["react_on"], rec.get("pre_file"),
                        rec.get("react_file"), rec.get("react"), rec.get("react_px"), rec.get("react_relvol"), rec.get("runup"),
                        rec.get("drift"), rec.get("eps_surprise"), rec.get("rev_surprise"), rec.get("pre_close"),
                        rec.get("react_close"), pre[11] if pre else None, hit[13] if hit else None, prev, rec.get("note")])
            if rec.get("react") is not None:
                prev = rec["react"]
    con.execute("CREATE OR REPLACE TABLE prints (" + ", ".join(f"{c} {t}" for c, t in PRINT_COLUMNS) + ")")
    if out:
        con.executemany(f"INSERT INTO prints VALUES ({', '.join('?' for _ in PRINT_COLUMNS)})", out)
    return len(out)


def feature_names(recipe: Mapping[str, Any]) -> list[str]:
    names = list((recipe.get("features") or {}).keys())
    rel = dict(recipe.get("relative") or {})
    return names + [f"{f}_ri" for f in rel.get("fields") or [] if f in names]


def feature_family(recipe: Mapping[str, Any]) -> dict[str, str]:
    feats = dict(recipe.get("features") or {})
    out = {name: str(spec.get("family") or "") for name, spec in feats.items()}
    for f in (recipe.get("relative") or {}).get("fields") or []:
        if f in feats:
            out[f"{f}_ri"] = f"{out[f]}_vs_ind"
    return out


def feature_labels(recipe: Mapping[str, Any]) -> dict[str, str]:
    feats = dict(recipe.get("features") or {})
    out = {name: str(spec.get("label") or name) for name, spec in feats.items()}
    for f in (recipe.get("relative") or {}).get("fields") or []:
        if f in feats:
            out[f"{f}_ri"] = f"{out[f]} minus the industry median"
    return out


def build_features(con: Any, recipe: Mapping[str, Any]) -> None:
    feats = dict(recipe.get("features") or {})
    lag_tol = str(int(recipe.get("lag_tolerance_days", 10)))
    win = int(recipe.get("window_files", 20))
    exprs = [f"({str(spec['sql']).replace('{lag_tol}', lag_tol)}) AS {qident(name)}" for name, spec in feats.items()]
    rel = dict(recipe.get("relative") or {})
    by, min_n = str(rel.get("by") or "ind"), int(rel.get("min_n", 5))
    rel_exprs = []
    for f in rel.get("fields") or []:
        if f in feats:
            part = f"PARTITION BY as_of, {qident(by)}"
            rel_exprs.append(f"CASE WHEN count({qident(f)}) OVER ({part}) >= {min_n} "
                             f"THEN {qident(f)} - median({qident(f)}) OVER ({part}) END AS {qident(f + '_ri')}")
    con.execute(f"""
        CREATE OR REPLACE TABLE features AS
        WITH b AS (
            SELECT p.*, s.split_seq,
                   CASE WHEN count(p.change) OVER w >= {max(2, win // 2)} THEN stddev_samp(p.change) OVER w END AS rvol_20,
                   CASE WHEN count(p.change) OVER w >= {max(2, win // 2)} THEN max(p.change) OVER w END AS max_20
            FROM panel p JOIN series s USING (symbol, as_of)
            WINDOW w AS (PARTITION BY p.symbol ORDER BY p.as_of ROWS BETWEEN {win - 1} PRECEDING AND CURRENT ROW)),
        l AS (SELECT symbol, as_of, split_seq, close, eps_nfy, rev_nfy, pt_1y, pt_avg FROM b),
        pr AS (SELECT symbol, react_file, react, react_close, split_seq FROM prints WHERE react_file IS NOT NULL AND react IS NOT NULL),
        x AS (
            SELECT b.as_of, b.symbol, b.ind, b.sector, b.exchange, b.mcap, b.close, b.split_seq, {', '.join(exprs)}
            FROM b
            ASOF LEFT JOIN l m1 ON b.symbol = m1.symbol AND b.as_of - 30 >= m1.as_of
            ASOF LEFT JOIN l m3 ON b.symbol = m3.symbol AND b.as_of - 91 >= m3.as_of
            ASOF LEFT JOIN pr ON b.symbol = pr.symbol AND b.as_of >= pr.react_file
            LEFT JOIN pred pd ON pd.symbol = b.symbol AND pd.as_of = b.as_of)
        SELECT x.*{(', ' + ', '.join(rel_exprs)) if rel_exprs else ''} FROM x""")


def rebuild_derived(con: Any, recipe: Mapping[str, Any]) -> dict[str, Any]:
    build_series(con, recipe)
    n_prints = build_prints(con, recipe)
    build_labels(con, recipe)
    build_features(con, recipe)
    return {"prints": n_prints}


# --- driver ------------------------------------------------------------------------------------------------


def store_summary(con: Any, recipe: Mapping[str, Any]) -> dict[str, Any]:
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    out: dict[str, Any] = {}
    row = con.execute("SELECT count(*), count(DISTINCT as_of), count(DISTINCT symbol), min(as_of), max(as_of) FROM panel").fetchone()
    out.update(rows=row[0], days=row[1], symbols=row[2], first=row[3].isoformat() if row[3] else None,
               last=row[4].isoformat() if row[4] else None)
    out["pred_days"] = con.execute("SELECT count(*) FROM ingest_log WHERE pred_run IS NOT NULL").fetchone()[0]
    if "prints" in tables:
        out["prints"] = con.execute("SELECT count(*) FROM prints WHERE react IS NOT NULL").fetchone()[0]
    if "labels" in tables:
        out["labels"] = {str(h["name"]): con.execute(
            f"SELECT count({qident('fwd_' + str(h['name']))}) FROM labels").fetchone()[0] for h in recipe.get("horizons") or []}
    if "features" in tables:
        out["features"] = len(feature_names(recipe))
    return out


def update_store(recipe: Mapping[str, Any] | None = None, *, store: str | Path | None = None, root: str | Path | None = None,
                 rebuild: bool = False, derived: bool = True, predictions: bool = True,
                 refresh_derived: bool = False) -> dict[str, Any]:
    """Ingest new day files (and their prediction runs), then rebuild series, prints, labels and features.

    The derived tables rebuild when something was ingested, or always with ``refresh_derived`` (after a change
    to how prints or labels are measured, without re-reading every day file as ``rebuild`` does)."""
    import duckdb

    from generic_utils.series import list_dated_files

    recipe = dict(recipe or load_store_recipe())
    path = Path(store) if store else default_store_path(recipe)
    path.parent.mkdir(parents=True, exist_ok=True)
    sources = list_dated_files(_abs(root or recipe.get("all_fields_root")), "**/tradingview_all_fields_*.duckdb",
                               newest=int(recipe.get("history_newest") or 400))
    con = duckdb.connect(str(path))
    try:
        ensure_schema(con, recipe)
        if rebuild:
            for table in ("panel", "pred", "ingest_log"):
                con.execute(f"DELETE FROM {table}")
        results = [ingest_day_file(con, s, recipe, force=rebuild) for s in sources if s.get("as_of_date")]
        pred_results = []
        if predictions:
            for as_of, scan_at in con.execute("SELECT as_of, scan_at FROM ingest_log WHERE pred_run IS NULL ORDER BY as_of").fetchall():
                pred_results.append(ingest_prediction(con, as_of, _naive_utc(scan_at), recipe))
        changed = [r for r in results if r["status"] == "ingested"] + [r for r in pred_results if r["status"] == "joined"]
        tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        built: dict[str, Any] = {}
        if derived and (changed or rebuild or refresh_derived or not {"series", "prints", "labels", "features"} <= tables):
            built = rebuild_derived(con, recipe)
        summary = store_summary(con, recipe)
    finally:
        con.close()
    return {
        "store": path.as_posix(),
        "ingested": [r["as_of"] for r in results if r["status"] == "ingested"],
        "kept": sum(1 for r in results if r["status"] == "kept"),
        "skipped": [r for r in results if r["status"] not in {"ingested", "kept"}],
        "pred_joined": sum(1 for r in pred_results if r["status"] == "joined"),
        "pred_missing": [r["as_of"] for r in pred_results if r["status"] != "joined"],
        "derived": bool(built) or None,
        "summary": summary,
    }


def connect_read(store: str | Path | None = None) -> Any:
    """Read-only connection to the store (raises FileNotFoundError when it was never built)."""
    import duckdb

    path = Path(store) if store else default_store_path()
    if not path.exists():
        raise FileNotFoundError(f"panel store {path} not built: run tv_scan_cli.py panel-store")
    return duckdb.connect(str(path), read_only=True)


def newest_day(con: Any) -> date | None:
    row = con.execute("SELECT max(as_of) FROM panel").fetchone()
    return row[0] if row else None


def horizon_specs(recipe: Mapping[str, Any], names: Sequence[str] | None = None) -> list[dict[str, Any]]:
    specs = [dict(h) for h in recipe.get("horizons") or []]
    if names:
        wanted = list(names)
        specs = [h for h in specs if h["name"] in wanted]
    return specs
