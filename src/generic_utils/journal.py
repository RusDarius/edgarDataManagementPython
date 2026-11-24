"""Decision journal: what each session saw and decided, kept small, measured later.

One DuckDB (``config/generic_utils/journal.json`` ``path``) with:

- ``sessions``: one row per briefing pack recorded (day, scan time, kind, the market regime then, live sentiment
  patterns, alpha model states, the day's headline).
- ``snaps``: one row per (day, covered name): the pack's reads at that time (bands, kill, model decile, peer gap,
  letters, warn level, lists the name was on...). A later session the same day overwrites the row, so the table
  grows by about one row per covered name per day. Raw TradingView fields are not copied: the panel store holds them.
- ``calls``: the judgment's course per name, recorded only when the course, grade or capital-plan row changed
  (an as-of log: the call in force at any time is the newest row at or before it), with the price then.
- ``texts``: each reason text once (hash key, capped length, the "Earlier:" tail cut).
- ``notes``: dated notes per name or the market ('*'): OUTSIDE news, guidance, sentiment, lessons (deduplicated).
- ``outcomes`` (a temp table built by ``score``, never stored): each call's raw return, excess over the median panel-store name, signed
  excess (by the call's direction), path low / high and kill / band / target touches at 1 day to 6 months.

It does not choose courses, rank names, add reads into a composite or apply cutoffs: classes, buckets and studies
are recipe knobs read on the page. Small samples stay visible (n, names, days) instead of being filtered out.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "journal.json"
RUNS_ROOT = REPO_ROOT / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"
PRIORS_ROOT = REPO_ROOT / "logs" / "AI_ANALYSIS_UTILS" / "priors"
CANVAS_ROOT = REPO_ROOT / "logs" / "canvases"
PACK_RE = re.compile(r"briefing_pack_(\d{8})_(\d{4})_utc")


# ---------------------------------------------------------------- recipe / connection


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


def journal_path(recipe: Mapping[str, Any] | None = None, path: str | Path | None = None) -> Path:
    p = Path(path or (recipe or load_recipe()).get("path") or "logs/tradingview_analysis/journal/journal.duckdb")
    return p if p.is_absolute() else REPO_ROOT / p


def connect(path: str | Path | None = None, *, recipe: Mapping[str, Any] | None = None, read_only: bool = False) -> Any:
    import duckdb

    p = journal_path(recipe, path)
    if read_only and not p.exists():
        raise FileNotFoundError(f"journal {p} not built: run tv_scan_cli.py journal record")
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p), read_only=read_only)
    if not read_only:
        ensure_schema(con, recipe or load_recipe())
    return con


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def snap_columns(recipe: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    sf = recipe.get("snap_fields") or {}
    return [k for k in (sf.get("num") or {}) if not k.startswith("_")], [k for k in (sf.get("text") or {}) if not k.startswith("_")]


def ensure_schema(con: Any, recipe: Mapping[str, Any]) -> None:
    reg = [f"{_q(f)} REAL" for f in (recipe.get("regime") or {}).get("fields") or []]
    con.execute("CREATE TABLE IF NOT EXISTS sessions (session_id VARCHAR PRIMARY KEY, day DATE, ts TIMESTAMP, kind VARCHAR, "
                "pack VARCHAR, judgment VARCHAR, recorded_at TIMESTAMP, " + ", ".join(reg + [
                    "patterns VARCHAR", "alpha_states VARCHAR", "headline VARCHAR", "n_snaps INTEGER", "n_calls INTEGER", "n_notes INTEGER"]) + ")")
    num, txt = snap_columns(recipe)
    cols = [f"{_q(c)} REAL" for c in num] + [f"{_q(c)} VARCHAR" for c in txt]
    con.execute("CREATE TABLE IF NOT EXISTS snaps (day DATE, symbol VARCHAR, session_id VARCHAR, ts TIMESTAMP, lists VARCHAR, "
                "radar_rank REAL, in_book BOOLEAN, " + ", ".join(cols) + ", PRIMARY KEY (day, symbol))")
    have = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'snaps'").fetchall()}
    for c in num:
        if c not in have:
            con.execute(f"ALTER TABLE snaps ADD COLUMN {_q(c)} REAL")
    for c in txt:
        if c not in have:
            con.execute(f"ALTER TABLE snaps ADD COLUMN {_q(c)} VARCHAR")
    con.execute("CREATE TABLE IF NOT EXISTS calls (session_id VARCHAR, day DATE, ts TIMESTAMP, symbol VARCHAR, course VARCHAR, "
                "cls VARCHAR, sign TINYINT, grade VARCHAR, horizon VARCHAR, px REAL, plan_px REAL, plan_usd REAL, plan_frac REAL, "
                "band_lo REAL, band_hi REAL, kill REAL, target REAL, text_id VARCHAR, source VARCHAR, PRIMARY KEY (session_id, symbol))")
    con.execute("CREATE TABLE IF NOT EXISTS texts (text_id VARCHAR PRIMARY KEY, text VARCHAR)")
    con.execute("CREATE TABLE IF NOT EXISTS notes (note_id VARCHAR PRIMARY KEY, day DATE, ts TIMESTAMP, session_id VARCHAR, "
                "symbol VARCHAR, kind VARCHAR, tone TINYINT, text VARCHAR, source VARCHAR)")


# ---------------------------------------------------------------- small helpers


def _f(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _hash(*parts: Any) -> str:
    return hashlib.md5("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def clean_text(text: Any, chars: int, cut_at: Sequence[str] = ()) -> str:
    t = " ".join(str(text or "").split())
    for cut in cut_at:
        if cut and cut in t:
            t = t.split(cut, 1)[0]
    return t[:chars].rstrip()


def course_class(course: Any, recipe: Mapping[str, Any]) -> tuple[str, int]:
    cc = recipe.get("course_classes") or {}
    text = str(course or "").strip().upper()
    for pattern, cls, sign in cc.get("rules") or []:
        if re.search(pattern, text):
            return str(cls), int(sign)
    d = cc.get("default") or ["other", 0]
    return str(d[0]), int(d[1])


def pack_session(pack: str | Path) -> dict[str, Any]:
    """session_id (pack folder name), day and scan time from briefing_pack.json, else from the folder name."""
    p = Path(pack)
    p = p if p.is_absolute() else REPO_ROOT / p
    at: datetime | None = None
    meta = p / "briefing_pack.json"
    if meta.exists():
        try:
            created = json.loads(meta.read_text(encoding="utf-8")).get("created_at_utc")
            if created:
                at = datetime.fromisoformat(str(created).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            at = None
    m = PACK_RE.search(p.name)
    if at is None and m:
        at = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")
    if at is None:
        raise ValueError(f"cannot date pack {p}")
    return {"session_id": p.name, "day": at.date(), "at": at, "pack": p}


def _tag(tag: str) -> str:
    t = str(tag).strip()
    if t.startswith("radar50#"):
        return "radar50"
    if re.fullmatch(r"[A-Z][A-Z_]*", t):  # a suggested_courses stance, not one of our calls
        return "pack " + t
    return t.replace(";", ",")


# ---------------------------------------------------------------- reading a pack


def _duck_rows(db: Path, sql: str) -> list[dict[str, Any]]:
    import duckdb

    if not db.exists():
        return []
    con = duckdb.connect(str(db), read_only=True)
    try:
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        con.close()


def regime_row(pack: Path, day: date, recipe: Mapping[str, Any]) -> dict[str, Any]:
    fields = list((recipe.get("regime") or {}).get("fields") or [])
    out: dict[str, Any] = {f: None for f in fields}
    db = pack / "sentiment" / "market_sentiment.duckdb"
    rows = _duck_rows(db, f"SELECT * FROM daily WHERE as_of <= DATE '{day.isoformat()}' ORDER BY as_of DESC LIMIT 1")
    if rows:
        out.update({f: _f(rows[0].get(f)) for f in fields})
    live = _duck_rows(db, "SELECT pattern FROM patterns WHERE live")
    out["patterns"] = ", ".join(str(r["pattern"]) for r in live) or None
    st = _duck_rows(pack / "alpha_model" / "alpha_model.duckdb", "SELECT horizon, state FROM state")
    out["alpha_states"] = ", ".join(f"{r['horizon']} {r['state']}" for r in st) or None
    return out


def _source_rows(pack: Path, recipe: Mapping[str, Any], extra: Iterable[str]) -> tuple[list[dict[str, Any]], str]:
    """Covered rows: board names (condition + extra ids), else pack names + book, else the pack JSON."""
    extra = sorted({s for s in extra if s})
    ids = ", ".join("'" + s.replace("'", "''") + "'" for s in extra) or "''"
    board = pack / "board" / "board.duckdb"
    if board.exists():
        where = (recipe.get("coverage") or {}).get("board_where") or "TRUE"
        rows = _duck_rows(board, f"SELECT * FROM names WHERE ({where}) OR symbol IN ({ids})")
        if rows:
            return rows, "board"
    db = pack / "briefing_pack.duckdb"
    if db.exists():
        rows = _duck_rows(db, "SELECT * FROM names")
        seen = {r.get("symbol") for r in rows}
        book = _duck_rows(db, "SELECT * FROM book")
        for r in book:
            r["in_book"] = True
        rows += [r for r in book if r.get("symbol") not in seen]
        if rows:
            return rows, "pack_names"
    meta = pack / "briefing_pack.json"
    if meta.exists():
        try:
            pj = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            return [], "none"
        names = pj.get("names") or {}
        rows = [dict(v, symbol=k) for k, v in names.items() if isinstance(v, dict)] if isinstance(names, dict) else list(names)
        seen = {r.get("symbol") for r in rows}
        for r in pj.get("book") or []:
            if isinstance(r, dict) and r.get("symbol") not in seen:
                rows.append(dict(r, in_book=True))
        return rows, "pack_json"
    return [], "none"


def snap_rows(pack: Path, recipe: Mapping[str, Any], extra: Iterable[str] = ()) -> tuple[list[dict[str, Any]], str]:
    from generic_utils.catalyst_outlook import pack_list_tags

    rows, source = _source_rows(pack, recipe, extra)
    try:
        tags = pack_list_tags(pack)
    except Exception:
        tags = {}
    num, txt = snap_columns(recipe)
    sf = recipe.get("snap_fields") or {}
    out = []
    for r in rows:
        sym = r.get("symbol")
        if not sym:
            continue
        rec: dict[str, Any] = {"symbol": str(sym)}
        for col in num:
            rec[col] = next((_f(r.get(s)) for s in sf["num"][col] if _f(r.get(s)) is not None), None)
        for col in txt:
            rec[col] = next((str(r.get(s)) for s in sf["text"][col] if r.get(s) not in (None, "")), None)
        t = list(tags.get(str(sym)) or [])
        rank = next((int(x.split("#", 1)[1]) for x in t if x.startswith("radar50#") and x.split("#", 1)[1].isdigit()), None)
        lists = [_tag(x) for x in t]
        if rec.get("earn_week") in ("W0", "W1"):
            lists.append(f"earn {rec['earn_week']}")
        in_book = bool(r.get("in_book")) or "book" in lists
        rec.update(radar_rank=rank, in_book=in_book, lists=";".join(dict.fromkeys(lists)) or None)
        out.append(rec)
    return out, source


def plan_rows(judgment: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """symbol -> its first capital-plan row (base plan first)."""
    plans = judgment.get("capital_plan") or {}
    order = (["capital_plan"] if "capital_plan" in plans else []) + [k for k in plans if k != "capital_plan"]
    out: dict[str, dict[str, Any]] = {}
    for key in order:
        for r in plans.get(key) or []:
            sym = r.get("symbol")
            if sym and sym not in out:
                out[sym] = {"plan_px": _f(r.get("px")), "plan_usd": _f(r.get("usd")), "plan_frac": _f(r.get("frac"))}
    return out


def harvest_notes(judgment: Mapping[str, Any], session: Mapping[str, Any], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Notes the judgment already carries: the print brief's OUTSIDE lines per name and the update banner."""
    chars = int((recipe.get("text") or {}).get("note_chars") or 400)
    out = []
    pb = ((judgment.get("pages") or {}).get("print_brief") or {}).get("notes") or {}
    for sym, lines in pb.items():
        for line in lines if isinstance(lines, list) else [lines]:
            out.append({"symbol": sym, "kind": "outside", "text": clean_text(line, chars), "source": "judgment print_brief"})
    ban = ((judgment.get("update") or {}).get("banner") or {})
    if ban.get("title") or ban.get("text"):
        out.append({"symbol": "*", "kind": "session", "text": clean_text(f"{ban.get('title', '')}: {ban.get('text', '')}", chars),
                    "source": "judgment update banner"})
    return out


def _headline(judgment: Mapping[str, Any] | None) -> str | None:
    if not judgment:
        return None
    read = (((judgment.get("pages") or {}).get("brief") or {}).get("blocks") or {}).get("read") or {}
    return clean_text(read.get("title"), 200) or None


# ---------------------------------------------------------------- recording


def add_notes(con: Any, notes: Iterable[Mapping[str, Any]], *, day: date, at: datetime, session_id: str | None,
              recipe: Mapping[str, Any]) -> int:
    chars = int((recipe.get("text") or {}).get("note_chars") or 400)
    n = 0
    for nt in notes:
        text = clean_text(nt.get("text"), chars)
        if not text:
            continue
        sym = str(nt.get("symbol") or "*")
        kind = str(nt.get("kind") or "note")
        nid = _hash(sym, kind, text)
        if con.execute("SELECT 1 FROM notes WHERE note_id = ?", [nid]).fetchone():
            continue
        tone = nt.get("tone")
        con.execute("INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [nid, day, at, session_id, sym, kind, int(tone) if tone not in (None, "") else None, text, nt.get("source")])
        n += 1
    return n


def _insert_many(con: Any, head: str, rows: Sequence[Sequence[Any]], *, chunk: int = 250) -> None:
    """INSERT in chunks of multi-row VALUES (one statement per chunk; executemany runs one statement per row)."""
    if not rows:
        return
    width = len(rows[0])
    one = "(" + ", ".join("?" for _ in range(width)) + ")"
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        con.execute(f"{head} VALUES " + ", ".join(one for _ in part), [v for r in part for v in r])


def _norm_key(key: Sequence[Any]) -> tuple:
    """Call identity with numbers rounded (REAL storage) so an unchanged plan is not a new call."""
    return tuple(round(v, 3) if isinstance(v, float) else (round(float(v), 3) if isinstance(v, int) and not isinstance(v, bool) else v)
                 for v in key)


def _bare_session(con: Any, ses: Mapping[str, Any], kind: str, judgment: str | None, n_calls: int) -> None:
    con.execute("DELETE FROM sessions WHERE session_id = ?", [ses["session_id"]])
    con.execute("INSERT INTO sessions (session_id, day, ts, kind, judgment, recorded_at, n_snaps, n_calls, n_notes) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0)", [ses["session_id"], ses["day"], ses["at"], kind, judgment,
                                                      datetime.now(timezone.utc).replace(tzinfo=None), n_calls])


def _last_calls(con: Any, at: datetime, session_id: str) -> dict[str, tuple]:
    rows = con.execute("""
        SELECT symbol, course, grade, plan_px, plan_usd, plan_frac FROM (
            SELECT *, row_number() OVER (PARTITION BY symbol ORDER BY ts DESC) AS rk FROM calls
            WHERE ts <= ? AND session_id <> ?) WHERE rk = 1""", [at, session_id]).fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


_CATALOG: Any = None


def _qualify(sym: str, extra: Any = None) -> str:
    """EXCHANGE:TICKER for a bare key (old priors files) from the panel store's ids, then the journal's own snapshot ids
    (non-US Book lines); a ticker listed twice stays bare."""
    global _CATALOG
    if ":" in sym:
        return sym
    if _CATALOG is None:
        from generic_utils.symbol_identity import SymbolCatalog

        ids: list[str] = []
        try:
            from generic_utils.panel_store import connect_read

            sc = connect_read()
            try:
                ids = [r[0] for r in sc.execute("SELECT DISTINCT symbol FROM series").fetchall()]
            finally:
                sc.close()
        except Exception:
            ids = []
        _CATALOG = SymbolCatalog.build(ids)
    from generic_utils.symbol_identity import qualify

    return qualify(sym, _CATALOG, extra) if extra is not None else qualify(sym, _CATALOG)


def record_calls(con: Any, session: Mapping[str, Any], courses: Mapping[str, Any], plans: Mapping[str, Mapping[str, Any]],
                 snaps: Mapping[str, Mapping[str, Any]], recipe: Mapping[str, Any], *, source: str) -> int:
    tx = recipe.get("text") or {}
    chars, cut = int(tx.get("reason_chars") or 600), list(tx.get("cut_at") or [])
    sid, at = session["session_id"], session["at"]
    con.execute("DELETE FROM calls WHERE session_id = ?", [sid])
    last = _last_calls(con, at, sid)
    batch: list[list[Any]] = []
    texts: dict[str, str] = {}
    extra = None
    if any(":" not in str(s) for s in courses):
        from generic_utils.symbol_identity import SymbolCatalog

        extra = SymbolCatalog.build([r[0] for r in con.execute("SELECT DISTINCT symbol FROM snaps WHERE contains(symbol, ':')").fetchall()])
    for sym, c in courses.items():
        if not isinstance(c, Mapping):
            continue
        sym = _qualify(str(sym), extra)
        course = str(c.get("Course") or c.get("action") or "").strip()
        if not course:
            continue
        grade = str(c.get("Grade") or c.get("grade") or "") or None
        plan = plans.get(sym) or {}
        key = (course, grade, plan.get("plan_px"), plan.get("plan_usd"), plan.get("plan_frac"))
        prev = last.get(sym)
        if prev is not None and _norm_key(prev) == _norm_key(key):
            continue
        cls, sign = course_class(course, recipe)
        text = clean_text(c.get("Reason") or c.get("note"), chars, cut)
        tid = _hash(text) if text else None
        if tid:
            texts[tid] = text
        sn = snaps.get(sym) or {}
        batch.append([sid, session["day"], at, sym, course, cls, sign, grade, c.get("Horizon") or c.get("horizon"),
                      sn.get("close"), plan.get("plan_px"), plan.get("plan_usd"), plan.get("plan_frac"),
                      sn.get("band_lo"), sn.get("band_hi"), sn.get("kill"), sn.get("target"), tid, source])
    if texts:
        known = {r[0] for r in con.execute("SELECT text_id FROM texts WHERE list_contains(?, text_id)", [list(texts)]).fetchall()}
        new = [[k, v] for k, v in texts.items() if k not in known]
        if new:
            _insert_many(con, "INSERT INTO texts", new)
    _insert_many(con, "INSERT INTO calls", batch)
    return len(batch)


def record_session(con: Any, pack: str | Path, *, judgment: Mapping[str, Any] | None = None, judgment_path: str | None = None,
                   kind: str = "", recipe: Mapping[str, Any] | None = None, notes: Iterable[Mapping[str, Any]] = (),
                   snaps: bool = True) -> dict[str, Any]:
    """Record one pack (regime + snaps) and, when given, the judgment's calls and notes under that pack's session."""
    recipe = recipe or load_recipe()
    ses = pack_session(pack)
    p: Path = ses["pack"]
    courses = (judgment or {}).get("courses") or {}
    extra = list(courses) if courses else []
    rows, source = snap_rows(p, recipe, extra) if snaps else ([], "skipped")
    by_sym = {r["symbol"]: r for r in rows}
    num, txt = snap_columns(recipe)
    cols = ["day", "symbol", "session_id", "ts", "lists", "radar_rank", "in_book"] + num + txt
    _insert_many(con, f"INSERT OR REPLACE INTO snaps ({', '.join(_q(c) for c in cols)})",
                 [[ses["day"], r["symbol"], ses["session_id"], ses["at"], r.get("lists"), r.get("radar_rank"), r.get("in_book")]
                  + [r.get(c) for c in num] + [r.get(c) for c in txt] for r in rows])
    n_calls = record_calls(con, ses, courses, plan_rows(judgment or {}), by_sym, recipe, source="judgment") if courses else 0
    n_notes = add_notes(con, list(harvest_notes(judgment or {}, ses, recipe)) + list(notes), day=ses["day"], at=ses["at"],
                        session_id=ses["session_id"], recipe=recipe)
    reg = regime_row(p, ses["day"], recipe)
    fields = list((recipe.get("regime") or {}).get("fields") or [])
    old = con.execute("SELECT kind, judgment, headline, n_calls, n_notes FROM sessions WHERE session_id = ?", [ses["session_id"]]).fetchone()
    con.execute("DELETE FROM sessions WHERE session_id = ?", [ses["session_id"]])
    scols = ["session_id", "day", "ts", "kind", "pack", "judgment", "recorded_at"] + fields + [
        "patterns", "alpha_states", "headline", "n_snaps", "n_calls", "n_notes"]
    vals = [ses["session_id"], ses["day"], ses["at"], kind or (old[0] if old else None), p.relative_to(REPO_ROOT).as_posix()
            if p.is_relative_to(REPO_ROOT) else p.as_posix(), judgment_path or (old[1] if old else None),
            datetime.now(timezone.utc).replace(tzinfo=None)] + [reg.get(f) for f in fields] + [
        reg.get("patterns"), reg.get("alpha_states"), _headline(judgment) or (old[2] if old else None), len(rows),
        n_calls if courses else (old[3] if old else 0), n_notes + (old[4] if old and not courses else 0)]
    con.execute(f"INSERT INTO sessions ({', '.join(_q(c) for c in scols)}) VALUES ({', '.join('?' for _ in scols)})", vals)
    return {"session_id": ses["session_id"], "day": ses["day"].isoformat(), "snaps": len(rows), "snap_source": source,
            "calls": n_calls, "notes": n_notes}


def requalify(con: Any) -> int:
    """Re-key bare call / note ids (old priors files) once the journal's snapshots know the EXCHANGE:TICKER id."""
    from generic_utils.symbol_identity import SymbolCatalog, qualify

    cat = SymbolCatalog.build([r[0] for r in con.execute("SELECT DISTINCT symbol FROM snaps WHERE contains(symbol, ':')").fetchall()])
    n = 0
    for table in ("calls", "notes"):
        for (sym,) in con.execute(f"SELECT DISTINCT symbol FROM {table} WHERE NOT contains(symbol, ':') AND symbol <> '*'").fetchall():
            q = qualify(sym, cat)
            if ":" in q:
                con.execute(f"UPDATE {table} SET symbol = ? WHERE symbol = ?", [q, sym])
                n += 1
    return n


def record_priors(con: Any, priors_file: str | Path, recipe: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """A priors file (actions per name, from before day judgments existed) as calls under its pack's session."""
    recipe = recipe or load_recipe()
    path = Path(priors_file)
    data = json.loads(path.read_text(encoding="utf-8"))
    actions = data.get("actions") or {}
    if not actions:
        return None
    pack = RUNS_ROOT / str(data.get("pack") or "")
    if data.get("pack") and (pack / "briefing_pack.json").exists():
        return record_session(con, pack, judgment={"courses": actions}, judgment_path=path.name, kind="priors", recipe=recipe)
    day = datetime.strptime(path.name[:8], "%Y%m%d").date()
    ses = {"session_id": f"priors_{path.name[:8]}", "day": day, "at": datetime.combine(day, datetime.min.time()) + timedelta(hours=21)}
    n = record_calls(con, ses, actions, {}, {}, recipe, source="priors")
    _bare_session(con, ses, "priors", path.name, n)
    return {"session_id": ses["session_id"], "day": day.isoformat(), "snaps": 0, "calls": n, "notes": 0}


def _judgment_sources(since: date | None) -> list[tuple[datetime, str, Path, str]]:
    """(time, kind, file, pack) for every run-folder and day judgment whose pack folder still exists or is named."""
    out = []
    for f in sorted(CANVAS_ROOT.glob("2*/**/judgment.json")):
        if f.name != "judgment.json" or "prev" in f.name:
            continue
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pack = str(j.get("reviewed_pack") or j.get("pack") or "")
        if not pack:
            continue
        try:
            ses = pack_session(pack)
        except Exception:
            continue
        if since and ses["day"] < since:
            continue
        kind = f.parent.name.split("_", 1)[1] if "_" in f.parent.name else "day"
        out.append((ses["at"], kind, f, pack))
    return out


def backfill(con: Any, recipe: Mapping[str, Any] | None = None, *, since: date | None = None, packs: bool = True) -> dict[str, Any]:
    """Every priors file, run / day judgment and (optionally) data-only pack, in time order."""
    recipe = recipe or load_recipe()
    items: list[tuple[datetime, int, str, Any]] = []
    judged = _judgment_sources(since)
    judged_days = {at.date() for at, *_ in judged} | {datetime.strptime(d.name, "%Y%m%d").date()
                                                      for d in CANVAS_ROOT.glob("2*") if d.is_dir() and d.name.isdigit()}
    for pf in sorted(PRIORS_ROOT.glob("*_operator_actions.json")):
        d = datetime.strptime(pf.name[:8], "%Y%m%d")
        if (not since or d.date() >= since) and d.date() not in judged_days:  # a judgment day keeps its fuller judgment calls
            items.append((d + timedelta(hours=21), 1, "priors", pf))
    for at, kind, f, pack in judged:
        items.append((at, 2, kind, (f, pack)))
    if packs:
        for p in sorted(RUNS_ROOT.glob("briefing_pack_*")):
            try:
                ses = pack_session(p)
            except Exception:
                continue
            if not since or ses["day"] >= since:
                items.append((ses["at"], 0, "data", p))
    items.sort(key=lambda t: (t[0], t[1]))
    done: list[dict[str, Any]] = []
    for at, _, kind, obj in items:
        try:
            if kind == "priors":
                r = record_priors(con, obj, recipe)
            elif kind == "data":
                r = record_session(con, obj, kind="data", recipe=recipe)
            else:
                f, pack = obj
                j = json.loads(Path(f).read_text(encoding="utf-8"))
                p = Path(pack)
                p = p if p.is_absolute() else REPO_ROOT / p
                snaps_ok = p.exists()
                if not snaps_ok:
                    ses = pack_session(pack)
                    ses["session_id"] = Path(pack).name
                    n = record_calls(con, ses, j.get("courses") or {}, plan_rows(j), {}, recipe, source="judgment")
                    _bare_session(con, ses, kind, Path(f).relative_to(REPO_ROOT).as_posix(), n)
                    r = {"session_id": ses["session_id"], "calls": n, "snaps": 0}
                else:
                    r = record_session(con, p, judgment=j, judgment_path=Path(f).relative_to(REPO_ROOT).as_posix(), kind=kind,
                                       recipe=recipe)
            if r:
                done.append(r)
        except Exception as exc:  # one broken folder never stops the backfill
            done.append({"error": f"{kind} {obj}: {exc}"})
    con.execute("DELETE FROM texts WHERE text_id NOT IN (SELECT text_id FROM calls WHERE text_id IS NOT NULL)")
    requalify(con)
    return {"recorded": len([d for d in done if "error" not in d]), "errors": [d["error"] for d in done if "error" in d][:20],
            "calls": sum(d.get("calls") or 0 for d in done), "snaps": sum(d.get("snaps") or 0 for d in done)}


# ---------------------------------------------------------------- scoring


def _attach_store(con: Any, store: str | Path | None) -> bool:
    from generic_utils.panel_store import default_store_path

    path = Path(store) if store else default_store_path()
    if not path.exists():
        return False
    if "st" not in {r[0] for r in con.execute("SELECT database_name FROM duckdb_databases()").fetchall()}:
        con.execute(f"ATTACH '{path.as_posix()}' AS st (READ_ONLY)")
    return True


def _horizons(recipe: Mapping[str, Any]) -> list[tuple[str, int]]:
    return [(str(h["name"]), int(h["days"])) for h in recipe.get("horizons") or []]


def _measure(con: Any, entries: str, recipe: Mapping[str, Any], out: str, *, store_labels: set[str]) -> None:
    """Temp table ``out``: per entry (eid, symbol, day, px, sign, kill, band_hi, target) the outcome at each horizon and now."""
    tol = int(recipe.get("label_tolerance_days") or 4)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _px AS
        SELECT p.symbol, p.as_of, p.close FROM st.panel p WHERE p.close > 0 AND p.symbol IN (SELECT DISTINCT symbol FROM {entries})
        UNION ALL
        SELECT s.symbol, s.day, s.close FROM snaps s WHERE s.close > 0 AND s.symbol IN (SELECT DISTINCT symbol FROM {entries})
          AND s.symbol NOT IN (SELECT DISTINCT symbol FROM st.panel WHERE symbol IN (SELECT DISTINCT symbol FROM {entries}))""")
    hz = _horizons(recipe)
    med = [f"median(fwd_{n}) AS m_{n}" for n, _ in hz if f"fwd_{n}" in store_labels]
    con.execute(f"CREATE OR REPLACE TEMP TABLE _mk AS SELECT as_of, {', '.join(med) or 'NULL AS m_none'} FROM st.labels GROUP BY as_of")
    con.execute("""CREATE OR REPLACE TEMP TABLE _last AS
        SELECT symbol, arg_max(close, as_of) AS close, max(as_of) AS as_of FROM _px GROUP BY symbol""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _e AS
        SELECT e.*, COALESCE(e.px, x.close) AS px0, m.as_of AS mday FROM {entries} e
        ASOF LEFT JOIN _px x ON e.symbol = x.symbol AND e.day >= x.as_of
        ASOF LEFT JOIN _mk m ON e.day >= m.as_of""")
    con.execute("""CREATE OR REPLACE TEMP TABLE _now AS
        WITH d AS (SELECT DISTINCT mday FROM _e WHERE mday IS NOT NULL), n AS (SELECT max(as_of) AS d FROM st.panel)
        SELECT d.mday, median((b.close / a.close - 1) * 100) AS m_now
        FROM d JOIN st.panel a ON a.as_of = d.mday JOIN st.panel b ON b.symbol = a.symbol AND b.as_of = (SELECT d FROM n)
        WHERE a.close > 0 AND b.close > 0 GROUP BY d.mday""")
    sel = ["e.*", "round((l.close / e.px0 - 1) * 100, 3) AS ret_now", "l.as_of - e.day AS days_now",
           "round((l.close / e.px0 - 1) * 100 - nw.m_now, 3) AS fx_now"]
    joins = ["LEFT JOIN _last l ON l.symbol = e.symbol", "LEFT JOIN _now nw ON nw.mday = e.mday"]
    for k, (n, days) in enumerate(hz):
        a = f"x{k}"
        con.execute(f"""CREATE OR REPLACE TEMP TABLE _{a} AS
            SELECT e.eid, x.close AS xc, x.as_of AS xd,
                   (SELECT min(p.close) FROM _px p WHERE p.symbol = e.symbol AND p.as_of > e.day AND p.as_of <= e.day + {days}) AS lo,
                   (SELECT max(p.close) FROM _px p WHERE p.symbol = e.symbol AND p.as_of > e.day AND p.as_of <= e.day + {days}) AS hi
            FROM _e e ASOF LEFT JOIN _px x ON e.symbol = x.symbol AND x.as_of >= e.day + {days}""")
        ok = f"{a}.xd IS NOT NULL AND {a}.xd <= e.day + {days + tol}"
        m = f"mk.m_{n}" if f"fwd_{n}" in store_labels else "NULL"
        sel += [f"CASE WHEN {ok} THEN round(({a}.xc / e.px0 - 1) * 100, 3) END AS ret_{n}",
                f"CASE WHEN {ok} THEN round(({a}.xc / e.px0 - 1) * 100 - {m}, 3) END AS fx_{n}",
                f"CASE WHEN {ok} THEN round(e.sign * (({a}.xc / e.px0 - 1) * 100 - {m}), 3) END AS sfx_{n}",
                f"round(({a}.lo / e.px0 - 1) * 100, 3) AS mae_{n}", f"round(({a}.hi / e.px0 - 1) * 100, 3) AS mfe_{n}",
                f"CASE WHEN e.kill IS NULL OR NOT ({ok}) THEN NULL WHEN {a}.lo < e.kill THEN 1 ELSE 0 END AS kill_{n}",
                f"CASE WHEN e.band_hi IS NULL OR NOT ({ok}) THEN NULL WHEN {a}.lo <= e.band_hi THEN 1 ELSE 0 END AS band_{n}",
                f"CASE WHEN e.target IS NULL OR NOT ({ok}) THEN NULL WHEN {a}.hi >= e.target THEN 1 ELSE 0 END AS tgt_{n}"]
        joins.append(f"LEFT JOIN _{a} {a} ON {a}.eid = e.eid")
    con.execute(f"CREATE OR REPLACE TEMP TABLE {out} AS SELECT {', '.join(sel)} FROM _e e "
                f"LEFT JOIN _mk mk ON mk.as_of = e.mday {' '.join(joins)}")


def score(con: Any, recipe: Mapping[str, Any] | None = None, *, store: str | Path | None = None) -> dict[str, Any]:
    """Build the temp table ``outcomes`` (one row per call) on the panel store; nothing is written to the journal file."""
    recipe = recipe or load_recipe()
    if not _attach_store(con, store):
        return {"scored": 0, "note": "panel store missing"}
    labels = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_catalog = 'st' "
                                        "AND table_name = 'labels'").fetchall()}
    con.execute("""CREATE OR REPLACE TEMP TABLE _calls_e AS
        SELECT session_id || '|' || symbol AS eid, symbol, day, px, sign, kill, band_hi, target FROM calls""")
    _measure(con, "_calls_e", recipe, "_calls_o", store_labels=labels)
    con.execute("""CREATE OR REPLACE TEMP TABLE outcomes AS
        SELECT o.* EXCLUDE (symbol, day, px, sign, kill, band_hi, target, px0), c.* EXCLUDE (px), o.px0 AS px_entry
        FROM _calls_o o JOIN calls c ON c.session_id || '|' || c.symbol = o.eid""")
    n = con.execute("SELECT count(*), count(ret_now) FROM outcomes").fetchone()
    return {"scored": n[0], "priced": n[1]}


# ---------------------------------------------------------------- review


def _bucket_sql(spec: Mapping[str, Any], col: str) -> str:
    edges, labels = list(spec.get("edges") or []), list(spec.get("labels") or [])
    parts = [f"WHEN {col} < {float(e)!r} THEN '{labels[i]}'" for i, e in enumerate(edges)]
    return f"CASE WHEN {col} IS NULL THEN NULL {' '.join(parts)} ELSE '{labels[len(edges)]}' END"


def _obs(con: Any, recipe: Mapping[str, Any], labels: set[str]) -> None:
    """Temp ``obs``: one row per snap with the call in force then, the session regime and its outcomes."""
    buckets = {k: v for k, v in (recipe.get("buckets") or {}).items() if not k.startswith("_")}
    reg_fields = set((recipe.get("regime") or {}).get("fields") or [])
    con.execute("""CREATE OR REPLACE TEMP TABLE _snap_e AS
        SELECT s.day::VARCHAR || '|' || s.symbol AS eid, s.symbol, s.day, s.close AS px,
               COALESCE(c.sign, 0) AS sign, s.kill, s.band_hi, s.target
        FROM snaps s ASOF LEFT JOIN calls c ON c.symbol = s.symbol AND s.ts >= c.ts""")
    _measure(con, "_snap_e", recipe, "_snap_o", store_labels=labels)
    bsel = []
    for name, spec in buckets.items():
        f = spec["field"]
        src = f"ss.{_q(f)}" if f in reg_fields else f"s.{_q(f)}"
        bsel.append(f"{_bucket_sql(spec, src)} AS {_q(name)}")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE obs AS
        SELECT s.*, COALESCE(c.cls, 'no call') AS cls, c.course, {', '.join(bsel) or 'NULL AS none'},
               o.* EXCLUDE (eid, symbol, day, px, sign, kill, band_hi, target, px0, mday)
        FROM snaps s
        ASOF LEFT JOIN calls c ON c.symbol = s.symbol AND s.ts >= c.ts
        LEFT JOIN sessions ss ON ss.session_id = s.session_id
        LEFT JOIN _snap_o o ON o.eid = s.day::VARCHAR || '|' || s.symbol""")
    con.execute("""CREATE OR REPLACE TEMP TABLE obs_l AS
        SELECT o.*, trim(t.list) AS list FROM obs o, unnest(string_split(COALESCE(o.lists, 'no list'), ';')) AS t(list)""")


def _agg_cols(hz: Sequence[str], signed: bool) -> list[str]:
    cols = []
    for h in hz:
        cols += [f"count(fx_{h}) AS n_{h}", f"round(avg(ret_{h}), 2) AS ret_{h}_mean", f"round(avg(fx_{h}), 2) AS fx_{h}_mean",
                 f"round(median(fx_{h}), 2) AS fx_{h}_med", f"round(avg(CASE WHEN fx_{h} > 0 THEN 1.0 WHEN fx_{h} IS NOT NULL THEN 0.0 END), 3) AS fx_{h}_hit"]
        if signed:
            cols += [f"round(avg(sfx_{h}) FILTER (WHERE sign <> 0), 2) AS sfx_{h}_mean",
                     f"round(avg(kill_{h}), 3) AS kill_{h}_share"]
    return cols


def review(con: Any, recipe: Mapping[str, Any] | None = None, *, ids: Sequence[str] = (), store: str | Path | None = None,
           day: date | None = None) -> dict[str, Any]:
    recipe = recipe or load_recipe()
    sc = score(con, recipe, store=store)
    if not sc.get("scored") and sc.get("note"):
        raise FileNotFoundError(sc["note"])
    labels = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_catalog = 'st' "
                                        "AND table_name = 'labels'").fetchall()}
    _obs(con, recipe, labels)
    hz = [n for n, _ in _horizons(recipe)]
    rv = recipe.get("review") or {}

    def rows(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cur = con.execute(sql, list(params))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    agg_c = ", ".join(_agg_cols(hz, True))
    scorecard = rows(f"""SELECT cls, count(*) AS n, count(DISTINCT symbol) AS n_names, min(day) AS first_day, max(day) AS last_day,
                         round(avg(fx_now), 2) AS fx_now_mean, round(avg(sign * fx_now) FILTER (WHERE sign <> 0), 2) AS sfx_now_mean, {agg_c}
                         FROM outcomes GROUP BY ROLLUP (cls) ORDER BY cls NULLS FIRST""")
    for r in scorecard:
        r["cls"] = r["cls"] or "all calls"
    by_grade = rows(f"SELECT cls, COALESCE(grade, '-') AS grade, count(*) AS n, {agg_c} FROM outcomes GROUP BY cls, grade ORDER BY cls, grade")
    agg_o = ", ".join(_agg_cols(hz, False))
    studies = []
    for keys in recipe.get("studies") or []:
        tbl = "obs_l" if "list" in keys else "obs"
        kc = ", ".join(_q(k) for k in keys)
        for r in rows(f"""SELECT {kc}, count(*) AS n, count(DISTINCT symbol) AS n_names, count(DISTINCT day) AS n_days, {agg_o}
                          FROM {tbl} WHERE {' AND '.join(f'{_q(k)} IS NOT NULL' for k in keys)} GROUP BY {kc} ORDER BY {kc}"""):
            studies.append({"study": " x ".join(keys), "cell": " | ".join(str(r.pop(k)) for k in keys), **r})
    newest = day or con.execute("SELECT max(day) FROM snaps").fetchone()[0]
    min_obs = int(rv.get("min_cell_obs") or 5)
    sim_sel, sim_join = [], []
    for i, keys in enumerate(recipe.get("similar_studies") or []):
        tbl = "obs_l" if "list" in keys else "obs"
        kc = ", ".join(_q(k) for k in keys)
        con.execute(f"""CREATE OR REPLACE TEMP TABLE _sim{i} AS SELECT {kc}, count(fx_7d) AS n7, round(avg(fx_7d), 2) AS fx7,
                        count(fx_28d) AS n28, round(avg(fx_28d), 2) AS fx28,
                        round(avg(CASE WHEN fx_28d > 0 THEN 1.0 WHEN fx_28d IS NOT NULL THEN 0.0 END), 3) AS hit28
                        FROM {tbl} WHERE day < DATE '{newest.isoformat()}' GROUP BY {kc}""")
        label = "_".join(keys)
        if "list" in keys:  # a name on several lists: its best-measured list cell
            con.execute(f"""CREATE OR REPLACE TEMP TABLE _simn{i} AS SELECT * FROM (
                SELECT o.symbol, {', '.join('o.' + _q(k) for k in keys)}, s.n7, s.fx7, s.n28, s.fx28, s.hit28,
                       row_number() OVER (PARTITION BY o.symbol ORDER BY s.n28 DESC NULLS LAST) AS rk
                FROM obs_l o JOIN _sim{i} s USING ({kc}) WHERE o.day = DATE '{newest.isoformat()}') WHERE rk = 1""")
            src = f"_simn{i}"
        else:
            con.execute(f"""CREATE OR REPLACE TEMP TABLE _simn{i} AS SELECT o.symbol, {', '.join('o.' + _q(k) for k in keys)},
                s.n7, s.fx7, s.n28, s.fx28, s.hit28 FROM obs o JOIN _sim{i} s USING ({kc}) WHERE o.day = DATE '{newest.isoformat()}'""")
            src = f"_simn{i}"
        cell = " || ' | ' || ".join(f"CAST(z{i}.{_q(k)} AS VARCHAR)" for k in keys)
        sim_sel += [f"{cell} AS {_q('cell_' + label)}", f"CASE WHEN z{i}.n7 >= {min_obs} THEN z{i}.fx7 END AS {_q('fx_7d_' + label)}",
                    f"CASE WHEN z{i}.n28 >= {min_obs} THEN z{i}.fx28 END AS {_q('fx_28d_' + label)}",
                    f"CASE WHEN z{i}.n28 >= {min_obs} THEN z{i}.hit28 END AS {_q('hit_28d_' + label)}", f"z{i}.n28 AS {_q('n_28d_' + label)}"]
        sim_join.append(f"LEFT JOIN {src} z{i} ON z{i}.symbol = o.symbol")
    similar = rows(f"""SELECT o.symbol, o.in_book, o.cls, o.course, o.lists, round(o.close::DOUBLE, 4) AS close, o.band_state, o.dec_28d, o.warn_level, o.det,
                       {', '.join(sim_sel) or 'NULL AS none'} FROM obs o {' '.join(sim_join)}
                       WHERE o.day = DATE '{newest.isoformat()}' ORDER BY o.in_book DESC, o.cls, o.symbol""")
    since = newest - timedelta(days=int(rv.get("timeline_days") or 21))
    focus = set(ids) | {r[0] for r in con.execute("SELECT symbol FROM snaps WHERE day = ? AND in_book", [newest]).fetchall()}
    fl = ", ".join("'" + s.replace("'", "''") + "'" for s in focus) or "''"
    timeline = rows(f"""SELECT o.symbol, o.day, o.course, o.cls, o.grade, o.horizon, round(o.px_entry::DOUBLE, 4) AS px_entry, round(o.plan_px::DOUBLE, 4) AS plan_px, o.plan_usd, round(o.kill::DOUBLE, 4) AS kill,
                        o.ret_now, o.fx_now, o.days_now, o.fx_7d, o.fx_28d, o.fx_91d, o.kill_28d, t.text AS reason
                        FROM outcomes o LEFT JOIN texts t USING (text_id)
                        WHERE o.symbol IN ({fl}) OR o.day >= DATE '{since.isoformat()}'
                        ORDER BY (o.symbol IN ({fl})) DESC, o.symbol, o.ts""")
    latest = rows("""SELECT symbol, day, course, cls, grade, round(px_entry::DOUBLE, 4) AS px_entry, ret_now, fx_now, days_now, sign * fx_now AS sfx_now, fx_7d, fx_28d, fx_91d
                     FROM (SELECT *, row_number() OVER (PARTITION BY symbol ORDER BY ts DESC) AS rk FROM outcomes) WHERE rk = 1
                     ORDER BY cls, fx_now DESC NULLS LAST""")
    notes = rows("SELECT day, symbol, kind, tone, text, source FROM notes WHERE day >= ? ORDER BY day DESC, symbol",
                 [newest - timedelta(days=int(rv.get("notes_days") or 30))])
    sessions = rows("SELECT session_id, day, ts, kind, ret_cap, adv_pct, upvol_pct, patterns, alpha_states, headline, n_snaps, n_calls, n_notes "
                    "FROM sessions ORDER BY ts DESC")
    spec = {"journal": journal_path(recipe).as_posix(), "newest_day": newest.isoformat() if newest else None, "scored": sc,
            "horizons": hz, "storage": stats(con, recipe)}
    return {"spec": spec, "scorecard": scorecard, "by_grade": by_grade, "studies": studies, "similar_now": similar,
            "timeline": timeline, "latest": latest, "notes": notes, "sessions": sessions, "storage": spec["storage"]["tables"]}


def write_review(payload: Mapping[str, Any], out_dir: str | Path) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export

    tables = {k: payload[k] for k in ("scorecard", "by_grade", "studies", "similar_now", "timeline", "latest", "notes", "sessions", "storage")}
    notes = [
        "scorecard: per call class (from the course text, recipe course_classes) every recorded call, its raw return, excess over the "
        "median panel-store name over the same window (fx), signed excess (sfx = sign x fx; sign +1 held / bought, -1 sold / passed, 0 "
        "watch), the kill touched share; _now = from the call to the newest close.",
        "studies: every covered snap (one per name per day) with the call in force then; overlapping windows on the same name repeat, so "
        "read n_names and n_days beside n. Buckets are recipe edges (readings, not cutoffs).",
        "similar_now: the newest day's covered names with the record of their cell in earlier days (fx_<h>_<study> empty under min_cell_obs).",
        "Entry price = the scan price at the session (intraday when the scan ran in session); the market median runs from that day's close.",
        "No ranking, no composite, no course chosen.",
    ]
    return write_run_export(out_dir, tool="generic_utils.journal", tables=tables, sources={"journal": payload["spec"]["journal"]},
                            notes=notes, extra={"spec": payload["spec"]}, duckdb_name="journal_review.duckdb")


# ---------------------------------------------------------------- history / storage


def history_lines(con: Any, ids: Sequence[str], *, text_chars: int = 160) -> list[str]:
    out = []
    has_out = "outcomes" in {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    for sym in ids:
        out.append(f"== {sym}")
        if has_out:
            sql = """SELECT o.day, o.course, o.grade, round(o.px_entry::DOUBLE, 4), o.ret_now, o.fx_now, o.fx_28d, t.text FROM outcomes o
                     LEFT JOIN texts t USING (text_id) WHERE o.symbol = ? ORDER BY o.ts"""
        else:
            sql = """SELECT c.day, c.course, c.grade, round(c.px::DOUBLE, 4), NULL, NULL, NULL, t.text FROM calls c LEFT JOIN texts t USING (text_id)
                     WHERE c.symbol = ? ORDER BY c.ts"""
        for d, course, grade, px, rn, fn, f28, text in con.execute(sql, [sym]).fetchall():
            out.append(f"{d} {course} [{grade or '-'}] px={px} now={rn}% fx_now={fn} fx_28d={f28} | {clean_text(text, text_chars)}")
        for d, kind, text, src in con.execute("SELECT day, kind, text, source FROM notes WHERE symbol = ? ORDER BY day", [sym]).fetchall():
            out.append(f"{d} note[{kind}] {clean_text(text, text_chars)} ({src or '-'})")
        snap = con.execute("SELECT day, round(close::DOUBLE, 4), round(band_lo::DOUBLE, 4), round(band_hi::DOUBLE, 4), round(kill::DOUBLE, 4), "
                           "dec_28d, lists FROM snaps WHERE symbol = ? ORDER BY day DESC LIMIT 1",
                           [sym]).fetchone()
        if snap:
            out.append(f"latest snap {snap[0]} close={snap[1]} band={snap[2]}-{snap[3]} kill={snap[4]} dec_28d={snap[5]} lists={snap[6]}")
    return out


def stats(con: Any, recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    tables = []
    for (t,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_catalog = current_database() "
                            "AND table_type = 'BASE TABLE' ORDER BY 1").fetchall():
        tables.append({"table": t, "rows": con.execute(f"SELECT count(*) FROM {_q(t)}").fetchone()[0]})
    cur = con.execute("SELECT * FROM pragma_database_size() WHERE database_name = current_database()")
    names = [d[0] for d in cur.description]
    size = cur.fetchone() or ()
    db = dict(zip(names, size))
    p = journal_path(recipe)
    return {"tables": tables, "file_bytes": p.stat().st_size if p.exists() else None, "used_blocks": db.get("used_blocks"),
            "block_size": db.get("block_size"), "days": con.execute("SELECT count(DISTINCT day) FROM snaps").fetchone()[0]}


def compact(path: str | Path | None = None, recipe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Rewrite the file (DuckDB does not shrink in place after deletes / replaces)."""
    import duckdb

    src = journal_path(recipe, path)
    before = src.stat().st_size
    tmp = src.with_suffix(".compact.duckdb")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(src))
    try:
        con.execute("DROP TABLE IF EXISTS outcomes")
        con.execute("DELETE FROM texts WHERE text_id NOT IN (SELECT text_id FROM calls WHERE text_id IS NOT NULL)")
        con.execute(f"ATTACH '{tmp.as_posix()}' AS nj")
        con.execute("COPY FROM DATABASE " + con.execute("SELECT current_database()").fetchone()[0] + " TO nj")
        con.execute("DETACH nj")
    finally:
        con.close()
    src.unlink()
    tmp.rename(src)
    return {"path": src.as_posix(), "bytes_before": before, "bytes_after": src.stat().st_size}


def journal_local_main() -> None:
    """No-arg default: record the newest pack with today's judgment, then print the storage line."""
    from generic_utils.canvas_runs import judgment_path

    packs = sorted(RUNS_ROOT.glob("briefing_pack_*"))
    if not packs:
        print("no pack")
        return
    ses = pack_session(packs[-1])
    jp = judgment_path(ses["day"].strftime("%Y%m%d"))
    judgment = json.loads(jp.read_text(encoding="utf-8")) if jp.exists() else None
    con = connect()
    try:
        print(record_session(con, packs[-1], judgment=judgment, judgment_path=str(jp) if judgment else None, kind="manual"))
        print(stats(con))
    finally:
        con.close()


if __name__ == "__main__":
    journal_local_main()
