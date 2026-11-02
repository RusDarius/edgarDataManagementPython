"""Operator page renderer: JSON page spec -> one self-contained HTML file.

Replaces the Cursor-only `.canvas.tsx` pages. The agent writes judgment
(course, reason, grade, callouts) into a small JSON spec. Data rows come
from files that already exist (CSV, DuckDB SQL, briefing-pack sleeve), so
the agent does not retype hundreds of cells.

The Ledger contract (canvas-ledger skill) is enforced here:
- exactly one Ledger tab, always last, built from `config/generic_utils/canvas_ledger.json`
  plus the page's own entries
- `ledger.auto` pulls standing meanings for every header and coded cell value
- headers and coded values that nothing explains come back as warnings

No ranking, no eligibility cutoff, no 0–100. `where` / `sort` / `limit`
on a table source are caller choices echoed in the payload.

Spec shape (all keys optional unless noted)::

    {
      "name": "daily-focus-20261022",            # required; file stem
      "title": "Daily focus 22 Oct 2026",
      "subtitle": "...", "sources": ["pack briefing_pack_..."],
      "tabs": [{"label": "Plan", "blocks": [
        {"type": "stats", "items": [{"value": "$147k", "label": "NAV", "tone": "info"}]},
        {"type": "callout", "tone": "warning", "title": "Bias", "text": "..."},
        {"type": "h2", "text": "Book"}, {"type": "text", "text": "...", "tone": "secondary"},
        {"type": "list", "items": ["..."]},
        {"type": "table", "title": "Book", "headers": [...], "rows": [[...]], "tones": [null, "good"]},
        {"type": "table", "title": "Build-50",
         "source": {"csv": "PATH"} | {"duckdb": "PATH", "sql": "SELECT ..."} | {"pack": "PACK.json", "key": "sleeves.radar_curated_50"},
         "where": "left>=25", "sort": "-rr", "limit": 50, "derive": ["left_carrier"],
         "columns": ["symbol", ["ind", "Industry"], "left", "rr"],
         "id_field": "symbol", "extra": {"NASDAQ:MU": {"Course": "ADD"}}, "extra_columns": ["Course"],
         "tone_field": "Course", "tone_map": {"ADD": "good", "EXIT": "bad"}, "decimals": 2,   # or {"book_usd": 0, "*": 1}
         "column_groups": [["Technical", ["close", "rsi", "vs50"]], ["Value", ["pe_fwd", "val_vs_ind"]]]}
      ]}],
      "column_groups": [...],                     # page default for tables without their own
      "ledger": {"auto": true, "standard": ["rr", "left"], "entries": [{"group": "Page", "rows": [["Δbo", "..."]]}]}
    }

``column_groups`` is layout only: a header row over contiguous columns of one group (matched on the
source field or the header). It does not reorder columns; the table's SELECT / ``columns`` does.

``screens`` on a table are one-click presets in its toolbar: ``[{"label": "Leans up", "filters": {"rx_up": ">=2",
"rx_dn": "=0"}, "sort": "-rx_up", "explain": "what the screen shows and how to act on it"}]`` (filters use the column
filter syntax, keyed by header). A page ``guide`` (``{"intro": "...", "sections": [{"title", "text" | "items" |
"headers" + "rows"}]}``) adds a Guide tab before the Ledger: the sections, every screen on the page with its
filters, and the standard table how-to. A screen only filters and sorts; it never ranks or scores.
A cell written ``▲+1.2`` / ``▼-0.8`` (a measured direction and its size) is shaded green / red and sorts as its number.

A ``cards`` block renders one card per source row (same ``source`` / ``where`` / ``sort`` / ``limit`` / ``ids`` as a table):
``title_field``, ``subtitle_fields``, ``headline_field``, ``metrics`` (fields shown as label / value pairs, Ledger-checked
like headers), ``sections`` (``[[heading, field], ...]`` paragraphs), ``list_sections`` (``[[heading, field]]``, items split
on `` | ``), the judgment's ``extra`` (Course / Grade / Reason) and ``notes`` (``{id: [lines]}``), ``collapsed`` (sections
folded). A filter box hides cards by text. The texts come from the source; the page never writes them.

A ``chart`` block draws source rows as lines (inline SVG, no script): ``x`` (the field on the x axis, rows in source order
unless ``sort``), ``series`` (``[[field, label], ...]``, up to six), ``y_label``, ``note``, the same ``source`` / ``where`` /
``sort`` / ``limit`` as a table. Hovering a point shows every series at that x. Its fields go through the Ledger like headers.
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from generic_utils.symbol_identity import bare_tickers_in

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER_PATH = REPO_ROOT / "config" / "generic_utils" / "canvas_ledger.json"

TONES = frozenset({"good", "bad", "warning", "info", "neutral"})
_CODED_VALUE = re.compile(r"^[A-Z][A-Z0-9_]{1,}$")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_INLINE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_INLINE_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+|[\w.-]+\.html(?:#[\w-]+)?)\)")  # web, or a sibling page in the run folder
_URL = re.compile(r"https?://\S+")


def _norm(text: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(text).strip().lower())


# ---------------------------------------------------------------- ledger


def load_standard_ledger(path: str | Path | None = None) -> dict[str, Any]:
    ledger_path = Path(path) if path else DEFAULT_LEDGER_PATH
    return json.loads(ledger_path.read_text(encoding="utf-8"))


def _alias_index(entries: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """alias -> entry. A single-alias entry wins over a combined one (`pe_fwd` vs `pe / pe_fwd / peg`)."""
    index: dict[str, Mapping[str, Any]] = {}
    for entry in sorted(entries, key=lambda e: len(e.get("aliases") or [])):
        for alias in entry.get("aliases") or [entry["key"]]:
            index.setdefault(_norm(alias), entry)
        index.setdefault(_norm(entry["key"]), entry)
    return index


# ---------------------------------------------------------------- sources


def _resolve(path_text: str, base_dir: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute() or path.exists():
        return path
    for root in (base_dir, REPO_ROOT):
        candidate = root / path
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Page source not found: {path_text}")


def _source_rows(source: Mapping[str, Any], base_dir: Path) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv, rows_from_duckdb

    if source.get("csv"):
        return rows_from_csv(_resolve(source["csv"], base_dir))
    if source.get("duckdb"):
        if not source.get("sql"):
            raise ValueError("duckdb source needs sql")
        path = _resolve(source["duckdb"], base_dir)
        if not source.get("drop_missing_columns"):
            return rows_from_duckdb(path, source["sql"])
        return _rows_dropping_missing(path, str(source["sql"]))
    if source.get("pack"):
        from generic_utils.focus import pack_path_rows

        pack = json.loads(_resolve(source["pack"], base_dir).read_text(encoding="utf-8"))
        return pack_path_rows(pack, source.get("key") or "names")
    raise ValueError(f"Unknown table source: {sorted(source)}")


_MISSING_COLUMN = re.compile(r'Referenced column "([^"]+)" not found|Table "\w+" does not have a column named "([^"]+)"')


def _rows_dropping_missing(path: Path, sql: str, *, tries: int = 20) -> list[dict[str, Any]]:
    """Run ``sql``; a bare SELECT-list column the table lacks (an older pack) is dropped and the query re-run.

    Opt-in per source (``drop_missing_columns``): lets a template add a column without breaking older packs.
    Only plain ``, name`` / ``, "name"`` items are dropped; any other error is raised as is.
    """
    from generic_utils.scan_sources import rows_from_duckdb

    for _ in range(tries):
        try:
            return rows_from_duckdb(path, sql)
        except Exception as exc:  # noqa: BLE001 - duckdb.BinderException; matched on its message
            hit = _MISSING_COLUMN.search(str(exc))
            if not hit:
                raise
            name = re.escape(hit.group(1) or hit.group(2))
            trimmed = re.sub(rf',\s*(?:\w+\.)?"?{name}"?(?=\s*(,|FROM\b))', "", sql, count=1, flags=re.IGNORECASE)
            if trimmed == sql:
                raise
            sql = trimmed
    return rows_from_duckdb(path, sql)


def _sort_rows(rows: list[dict[str, Any]], sort: str | None) -> list[dict[str, Any]]:
    if not sort:
        return rows
    from generic_utils.ranking import to_float

    for part in reversed([p.strip() for p in sort.split(",") if p.strip()]):
        reverse = part.startswith("-")
        field = part.lstrip("+-")
        present = [r for r in rows if r.get(field) not in (None, "")]
        missing = [r for r in rows if r.get(field) in (None, "")]
        numeric = all(to_float(r.get(field)) is not None for r in present)
        key = (lambda r: to_float(r.get(field))) if numeric else (lambda r: str(r.get(field)))
        rows = sorted(present, key=key, reverse=reverse) + missing
    return rows


_DECIMAL_TEXT = re.compile(r"^-?\d+\.\d+(?:[eE]-?\d+)?$")


def _fmt(value: Any, decimals: int | None) -> str:
    """Floats (and CSV decimal text) to `decimals` places, default 2. Negative decimals keep the raw text."""
    if value is None:
        return "—"
    if isinstance(value, (bool, int)):
        return str(value)
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return "—"
    if decimals is not None and decimals < 0:
        return text
    if isinstance(value, float) or _DECIMAL_TEXT.match(text):
        return f"{float(text):.{2 if decimals is None else decimals}f}"
    return text


def _decimals_for(decimals: Any, field: str) -> int | None:
    """A table's `decimals`: one int for every column, or {field: n, "*": default}."""
    if isinstance(decimals, Mapping):
        return decimals.get(field, decimals.get("*"))
    return decimals


def _left_carrier(row: Mapping[str, Any]) -> str:
    from generic_utils.derive import leftover_carrier

    return leftover_carrier(row.get("street_left"), row.get("left"))


def _post_usd(row: Mapping[str, Any]) -> float | None:
    from generic_utils.derive import move_usd

    return move_usd(row.get("book_usd"), row.get("post_chg"))


def _post_value_usd(row: Mapping[str, Any]) -> float | None:
    from generic_utils.derive import move_usd
    from generic_utils.ranking import to_float

    value, move = to_float(row.get("book_usd")), move_usd(row.get("book_usd"), row.get("post_chg"))
    return None if value is None else round(value + (move or 0.0))


# Columns a table can add with "derive": [...]. Each reuses a generic_utils formula.
DERIVED_COLUMNS = {"left_carrier": _left_carrier, "post_usd": _post_usd, "post_value_usd": _post_value_usd}


def table_rows(block: Mapping[str, Any], base_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Raw rows of a sourced table after where / sort / limit / derive: (rows, where clauses, n loaded)."""
    from generic_utils.predicates import filter_rows

    loaded = _source_rows(block["source"], base_dir)
    kept, clauses = filter_rows(loaded, block.get("where"))
    ordered = _sort_rows([dict(r) for r in kept], block.get("sort"))
    ids = block.get("ids")
    if ids:
        # A judgment-chosen name list (`ids_from`): keep those rows, in the judgment's order
        # (every row of an id, in source order, for a table with several rows per name).
        id_field = block.get("id_field") or "symbol"
        by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in ordered:
            by_id[str(r.get(id_field))].append(r)
        ordered = [r for i in dict.fromkeys(str(i) for i in ids) for r in by_id.get(i, [])]
    if block.get("limit"):
        ordered = ordered[: int(block["limit"])]
    for name in block.get("derive") or []:
        if name not in DERIVED_COLUMNS:
            raise ValueError(f"Unknown derive column {name!r}; known: {sorted(DERIVED_COLUMNS)}")
        for row in ordered:
            row[name] = DERIVED_COLUMNS[name](row)
    return ordered, clauses, len(loaded)


def column_group_labels(fields: Sequence[str], headers: Sequence[str], groups: Any) -> list[str]:
    """Group label per column ('' when ungrouped) from ``[[label, [field or header, ...]], ...]``."""
    if not groups:
        return []
    owner: dict[str, str] = {}
    for item in groups:
        label, names = (item.get("label"), item.get("columns")) if isinstance(item, Mapping) else (item[0], item[1])
        for name in names or []:
            owner.setdefault(_norm(name), str(label))
    labels = [owner.get(_norm(f)) or owner.get(_norm(h)) or "" for f, h in zip(fields, headers)]
    return labels if any(labels) else []


def resolve_table(block: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
    """Return {title, headers, rows, tones, groups, spec} with every cell as a string."""
    if "source" not in block:
        headers = [str(h) for h in block.get("headers") or []]
        rows = [[_fmt(c, None) for c in row] for row in block.get("rows") or []]
        tones = list(block.get("tones") or [])
        return {"title": block.get("title"), "headers": headers, "rows": rows, "tones": tones,
                "groups": column_group_labels(headers, headers, block.get("column_groups")),
                "note": block.get("note"), "screens": list(block.get("screens") or []), "wide": bool(block.get("wide")),
                "spec": {"literal": True, "n": len(rows)}}

    ordered, clauses, n_loaded = table_rows(block, base_dir)
    limit = block.get("limit")

    id_field = block.get("id_field") or "symbol"
    extra = block.get("extra") or {}
    extra_columns = list(block.get("extra_columns") or [])
    for row in ordered:
        row.update(extra.get(str(row.get(id_field)), {}))

    columns = block.get("columns") or (list(ordered[0].keys()) if ordered else [])
    fields: list[str] = []
    headers: list[str] = []
    rename = block.get("rename") or {}
    for col in list(columns) + [c for c in extra_columns if c not in columns]:
        field, header = (col[0], col[1]) if isinstance(col, (list, tuple)) else (col, rename.get(col, col))
        fields.append(field)
        headers.append(str(header))

    decimals = block.get("decimals")
    tone_field = block.get("tone_field")
    tone_map = block.get("tone_map") or {}
    rows = [[_fmt(row.get(f), _decimals_for(decimals, f)) for f in fields] for row in ordered]
    tones = [tone_map.get(str(row.get(tone_field))) if tone_field else None for row in ordered]
    spec = {"source": dict(block["source"]), "n_loaded": n_loaded, "where": clauses,
            "sort": block.get("sort"), "limit": limit, "n": len(rows)}
    return {"title": block.get("title"), "headers": headers, "rows": rows, "tones": tones,
            "groups": column_group_labels(fields, headers, block.get("column_groups")),
            "note": block.get("note"), "screens": list(block.get("screens") or []), "wide": bool(block.get("wide")), "spec": spec}


def resolve_cards(block: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
    """One card per source row: {title, cards, ledger_table}; ``ledger_table`` puts the metric fields through the Ledger checks."""
    ordered, clauses, n_loaded = table_rows(block, base_dir)
    id_field = block.get("id_field") or "symbol"
    extra, notes = block.get("extra") or {}, block.get("notes") or {}
    metrics = list(block.get("metrics") or [])
    decimals = block.get("decimals")
    sep = str(block.get("list_sep") or " | ")
    tone_map = block.get("tone_map") or {}
    cards, ledger_rows = [], []
    for row in ordered:
        key = str(row.get(id_field))
        judged = dict(extra.get(key) or {})
        cards.append({
            "id": key, "title": _fmt(row.get(block.get("title_field") or id_field), -1),
            "subtitle": " · ".join(_fmt(row.get(f), _decimals_for(decimals, f)) for f in block.get("subtitle_fields") or []
                                   if row.get(f) not in (None, "")),
            "headline": row.get(block.get("headline_field")) if block.get("headline_field") else None,
            "metrics": [[f, _fmt(row.get(f), _decimals_for(decimals, f))] for f in metrics if row.get(f) not in (None, "")],
            "sections": [[str(h), str(row[f])] for h, f in block.get("sections") or [] if row.get(f) not in (None, "")],
            "lists": [[str(h), [x.strip() for x in str(row[f]).split(sep) if x.strip()]] for h, f in block.get("list_sections") or []
                      if row.get(f) not in (None, "")],
            "course": judged, "tone": tone_map.get(str(judged.get("Course"))) if judged else None,
            "notes": [str(n) for n in (notes.get(key) or [])],
        })
        ledger_rows.append([key] + [_fmt(row.get(f), _decimals_for(decimals, f)) for f in metrics] + ([str(judged.get("Course") or "")] if extra else []))
    headers = [id_field] + metrics + (["Course"] if extra else [])
    spec = {"source": dict(block["source"]), "n_loaded": n_loaded, "where": clauses, "sort": block.get("sort"),
            "limit": block.get("limit"), "n": len(cards), "cards": True}
    return {"title": block.get("title"), "cards": cards, "collapsed": bool(block.get("collapsed")), "note": block.get("note"),
            "ledger_table": {"title": block.get("title"), "headers": headers, "rows": ledger_rows, "tones": [], "spec": spec}}


def _num_or_none(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def resolve_chart(block: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
    """Line chart points: {title, xs, series [{field, label, ys}], y_label, note, ledger_table}."""
    ordered, clauses, n_loaded = table_rows(block, base_dir)
    x = str(block.get("x") or "as_of")
    pairs = [(s[0], s[1]) if isinstance(s, (list, tuple)) else (s, s) for s in block.get("series") or []][:6]
    xs = [_fmt(r.get(x), -1) for r in ordered]
    series = [{"field": f, "label": str(label), "ys": [_num_or_none(r.get(f)) for r in ordered]} for f, label in pairs]
    spec = {"source": dict(block["source"]), "n_loaded": n_loaded, "where": clauses, "sort": block.get("sort"),
            "limit": block.get("limit"), "n": len(xs), "chart": True}
    return {"title": block.get("title"), "xs": xs, "series": series, "y_label": block.get("y_label"), "note": block.get("note"),
            "ledger_table": {"title": block.get("title"), "headers": [x] + [f for f, _ in pairs], "rows": [], "tones": [], "spec": spec}}


# ---------------------------------------------------------------- build


def build_page(spec: Mapping[str, Any], *, base_dir: Path, ledger_path: str | Path | None = None) -> dict[str, Any]:
    """Resolve tables and the one Ledger. Returns the render model plus warnings."""
    if not spec.get("name"):
        raise ValueError("Page spec needs a name (file stem, e.g. daily-focus-20261022)")
    warnings: list[str] = []
    bare_extra_keys: set[str] = set()
    standard = load_standard_ledger(ledger_path)
    std_entries = standard.get("entries") or []
    std_index = _alias_index(std_entries)
    plain = {_norm(h) for h in standard.get("plain_headers") or []}
    free_text = {_norm(h) for h in standard.get("free_text_headers") or []}
    letter_coded = {_norm(h): str(v) for h, v in (standard.get("letter_coded_headers") or {}).items()}

    tabs: list[dict[str, Any]] = []
    tables_seen: list[dict[str, Any]] = []
    screens: list[list[str]] = []
    default_groups = spec.get("column_groups")
    for tab in spec.get("tabs") or []:
        label = str(tab.get("label") or "Tab")
        if _norm(label) in {"ledger", "guide"}:
            raise ValueError(f"Do not add a {label} tab. Put entries under spec.ledger / spec.guide; the renderer adds those tabs.")
        blocks: list[dict[str, Any]] = []
        for block in tab.get("blocks") or []:
            kind = block.get("type")
            if kind == "table":
                bare_extra_keys.update(str(k) for k in (block.get("extra") or {}) if ":" not in str(k))
                if default_groups and "column_groups" not in block and "source" in block:
                    block = {**block, "column_groups": default_groups}
                try:
                    table = resolve_table(block, base_dir)
                except FileNotFoundError as exc:
                    # A source this pack did not produce: say so on the page instead of failing the render.
                    # An `optional` table (a scan that only exists on some days) is noted without a warning.
                    if not block.get("optional"):
                        warnings.append(f"table {block.get('title')!r}: {exc}")
                    blocks.append({"type": "text", "tone": "secondary", "text": f"{block.get('title') or 'Table'}: source not produced on this pack ({exc})."})
                    continue
                except Exception as exc:  # noqa: BLE001 - an optional table this run did not write (e.g. an empty track) is noted, not fatal
                    if not block.get("optional"):
                        raise
                    first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                    blocks.append({"type": "text", "tone": "secondary", "text": f"{block.get('title') or 'Table'}: not produced on this run ({first_line})."})
                    continue
                for screen in table.get("screens") or []:
                    missing = [h for h in (screen.get("filters") or {}) if h not in table["headers"]]
                    missing += [k.strip().lstrip("+-") for k in str(screen.get("sort") or "").split(",")
                                if k.strip() and k.strip().lstrip("+-") not in table["headers"]]
                    if missing and table["rows"]:  # an empty table (nothing printed yet) drops its optional columns; no screen applies
                        warnings.append(f"screen {screen.get('label')!r} ({table.get('title') or 'table'}) names columns the table does not show: {', '.join(missing)}")
                    screens.append([label, str(table.get("title") or ""), str(screen.get("label") or ""),
                                    "  ".join(f"{h} {x}" for h, x in (screen.get("filters") or {}).items())
                                    + (f"  sort {screen['sort']}" if screen.get("sort") else ""),
                                    str(screen.get("explain") or "")])
                tables_seen.append(table)
                blocks.append({"type": "table", **table})
            elif kind == "cards":
                bare_extra_keys.update(str(k) for k in (block.get("extra") or {}) if ":" not in str(k))
                try:
                    cards = resolve_cards(block, base_dir)
                except FileNotFoundError as exc:
                    if not block.get("optional"):
                        warnings.append(f"cards {block.get('title')!r}: {exc}")
                    blocks.append({"type": "text", "tone": "secondary", "text": f"{block.get('title') or 'Cards'}: source not produced on this pack ({exc})."})
                    continue
                tables_seen.append(cards.pop("ledger_table"))
                blocks.append({"type": "cards", **cards})
            elif kind == "chart":
                try:
                    chart = resolve_chart(block, base_dir)
                except FileNotFoundError as exc:
                    if not block.get("optional"):
                        warnings.append(f"chart {block.get('title')!r}: {exc}")
                    blocks.append({"type": "text", "tone": "secondary", "text": f"{block.get('title') or 'Chart'}: source not produced on this pack ({exc})."})
                    continue
                tables_seen.append(chart.pop("ledger_table"))
                blocks.append({"type": "chart", **chart})
            elif kind in {"h2", "h3", "text", "callout", "stats", "list"}:
                blocks.append(dict(block))
            else:
                raise ValueError(f"Unknown block type {kind!r} on tab {label!r}")
        tabs.append({"label": label, "blocks": blocks})

    ledger_spec = spec.get("ledger") or {}
    page_entries: list[dict[str, Any]] = []
    for group in ledger_spec.get("entries") or []:
        for key, meaning in group.get("rows") or []:
            page_entries.append({"key": key, "aliases": [a.strip() for a in str(key).split(" / ")],
                                 "group": group.get("group") or "Page", "meaning": meaning})
    page_index = _alias_index(page_entries)

    chosen: dict[str, Mapping[str, Any]] = {}

    tips: dict[str, str] = {}

    def explain(token: str) -> Mapping[str, Any] | None:
        norm = _norm(token)
        entry = page_index.get(norm) or std_index.get(norm)
        if entry is None:
            stem = re.sub(r"_(nav|usd|pct|px)$", "", norm)
            entry = page_index.get(stem) or std_index.get(stem)
        if entry is None and re.search(r"_(vs_ind|vs_avg|ind_med|ind_avg)$", norm):
            entry = std_index.get("field_vs_ind")
        if entry is None:
            return None
        chosen.setdefault(f"{entry['group']}|{entry['key']}", entry)
        return entry

    for key in ledger_spec.get("standard") or []:
        if not explain(key):
            warnings.append(f"ledger.standard key {key!r} is not in {DEFAULT_LEDGER_PATH.name}")
    for entry in page_entries:
        chosen.setdefault(f"{entry['group']}|{entry['key']}", entry)

    if ledger_spec.get("auto", True):
        for table in tables_seen:
            for header in table["headers"]:
                if _norm(header) in plain:
                    continue
                entry = explain(header)
                if entry is None:
                    warnings.append(f"header {header!r} ({table.get('title') or 'table'}) has no Ledger row")
                else:
                    tips.setdefault(str(header), str(entry["meaning"]))
            # Letter-coded fields (Mesh): a cell is explained by the field row plus one row per letter.
            for i, h in enumerate(table["headers"]):
                allowed = letter_coded.get(_norm(h))
                if not allowed:
                    continue
                for cell in {row[i] for row in table["rows"] if i < len(row)}:
                    for ch in cell.strip("-").replace("—", ""):  # "—" is the empty-cell placeholder, not a letter
                        if ch not in allowed or not explain(ch):
                            warnings.append(f"letter {ch!r} in {h!r} ({table.get('title') or 'table'}) has no Ledger row")
            checked = [i for i, h in enumerate(table["headers"]) if _norm(h) not in free_text and _norm(h) not in letter_coded]
            coded = {row[i] for row in table["rows"] for i in checked if i < len(row) and _CODED_VALUE.match(row[i])}
            for value in sorted(coded):
                if not explain(value):
                    warnings.append(f"coded value {value!r} ({table.get('title') or 'table'}) has no Ledger row")

    # Id contract (symbol_identity): a name is EXCHANGE:TICKER on every page, never a bare ticker.
    warnings.extend(spec.get("id_warnings") or [])
    for table in tables_seen:
        title = table.get("title") or "table"
        heads = [_norm(h) for h in table["headers"]]
        if "symbol" not in heads and "ticker" in heads:
            warnings.append(f"table {title!r} identifies names by bare `ticker`; show `symbol` (EXCHANGE:TICKER)")
        if "symbol" in heads:
            i = heads.index("symbol")
            bare = sorted({t for row in table["rows"] if i < len(row) for t in bare_tickers_in(row[i])})
            if bare:
                warnings.append(f"bare ticker ids in {title!r} symbol column: {', '.join(bare[:8])} (write EXCHANGE:TICKER)")
    for key in sorted(bare_extra_keys):
        warnings.append(f"judgment key {key!r} is a bare ticker; courses are keyed EXCHANGE:TICKER")

    if not chosen:
        warnings.append("Ledger is empty. Every operator page needs one Ledger tab.")
    groups: dict[str, list[list[str]]] = {}
    for entry in chosen.values():
        groups.setdefault(str(entry["group"]), []).append([str(entry["key"]), str(entry["meaning"])])
    ledger = [{"group": g, "rows": sorted(rows, key=lambda r: r[0].lower())} for g, rows in groups.items()]

    return {
        "name": spec["name"],
        "title": spec.get("title") or spec["name"],
        "subtitle": spec.get("subtitle"),
        "sources": list(spec.get("sources") or []),
        "tabs": tabs,
        "ledger": ledger,
        "tips": tips,
        "guide": ({"intro": (spec.get("guide") or {}).get("intro"), "sections": list((spec.get("guide") or {}).get("sections") or []),
                   "screens": screens} if (spec.get("guide") or screens) else None),
        "warnings": list(dict.fromkeys(warnings)),
        "tables": [t["spec"] | {"title": t.get("title")} for t in tables_seen],
    }


# ---------------------------------------------------------------- render


def _inline(text: Any) -> str:
    escaped = html.escape(str(text or ""))
    escaped = _INLINE_BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = _INLINE_LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', escaped)
    return _INLINE_CODE.sub(r"<code>\1</code>", escaped)


def _tone(value: Any) -> str:
    return value if value in TONES else "neutral"


_NUMERIC_CELL = re.compile(r"^[−\-+$]?[\d,]*\.?\d+%?$")
# A measured direction written before a number (print_night pointer cells): shaded by direction, sorted by the number.
_ARROWS = {"▲": "up", "▼": "dn"}
# A text cell longer than this wraps (with a width cap, two lines shown, click to open) instead of stretching its column.
_WRAP_CHARS = 36


def _header_html(text: str) -> str:
    """Header text that may break after `_` (long field names stop setting the column width)."""
    return html.escape(text).replace("_", "_<wbr>")


def _group_row(groups: Sequence[str]) -> tuple[str, set[int]]:
    """The group header row (one cell per run of equal labels) and the column indexes that start a group."""
    cells, starts, i, shade = [], set(), 0, 0
    while i < len(groups):
        j = i
        while j + 1 < len(groups) and groups[j + 1] == groups[i]:
            j += 1
        label = groups[i]
        if i > 0:
            starts.add(i)
        cls = f' class="g{shade % 4}"' if label else ""
        cells.append(f'<th colspan="{j - i + 1}"{cls}>{html.escape(label)}</th>')
        shade += 1 if label else 0
        i = j + 1
    return f'<tr class="grp">{"".join(cells)}</tr>', starts


def _cell_html(cell: str, start: bool, clamp: bool = True) -> str:
    gs = " gs" if start else ""
    if cell[:1] in _ARROWS and _NUMERIC_CELL.match(cell[1:]):
        return f'<td class="num {_ARROWS[cell[:1]]}{gs}">{html.escape(cell)}</td>'
    if _NUMERIC_CELL.match(cell):
        return f'<td class="num{gs}">{html.escape(cell)}</td>'
    if len(cell) > _WRAP_CHARS:
        body = f'<div class="clamp" title="{html.escape(cell)}">{_inline(cell)}</div>' if clamp else _inline(cell)
        return f'<td class="txt{gs}">{body}</td>'
    return f'<td class="{gs.strip()}">{_inline(cell)}</td>' if gs else f"<td>{_inline(cell)}</td>"


def _render_table(block: Mapping[str, Any], idx: str, tips: Mapping[str, str] | None = None) -> str:
    """One grid. The toolbar (search, column filters, columns menu, heat, stats, CSV, reset), sorting and
    column drag are added by the page script; `data-group` lets it redraw the group row after a move."""
    screens = [{k: s.get(k) for k in ("label", "filters", "sort", "explain")} for s in block.get("screens") or []]
    data = f' data-screens="{html.escape(json.dumps(screens, ensure_ascii=False))}"' if screens else ""
    out = [f'<section class="tbl{" wide" if block.get("wide") else ""}"{data}>']
    if block.get("title"):
        out.append(f'<h3>{_inline(block["title"])} <span class="count">{len(block["rows"])} rows</span></h3>')
    groups = list(block.get("groups") or [])
    if len(groups) != len(block["headers"]):
        groups = []
    starts: set[int] = set()
    out.append(f'<div class="scroll"><table id="{idx}" class="grid"><thead>')
    if groups:
        row_html, starts = _group_row(groups)
        out.append(row_html)
    out.append('<tr class="hdr">')
    for i, h in enumerate(block["headers"]):
        attrs = f' data-col="{i}"'
        if groups:
            attrs += f' data-group="{html.escape(groups[i])}"'
        tip = (tips or {}).get(str(h))
        if tip:
            attrs += f' title="{html.escape(tip)}"'
        if i in starts:
            attrs += ' class="gs"'
        out.append(f"<th{attrs}>{_header_html(h)}</th>")
    out.append("</tr></thead><tbody>")
    tones = block.get("tones") or []
    for r, row in enumerate(block["rows"]):
        tone = _tone(tones[r] if r < len(tones) else None)
        cells = "".join(_cell_html(c, i in starts, clamp=not block.get("wide")) for i, c in enumerate(row))
        out.append(f'<tr class="t-{tone}">{cells}</tr>')
    out.append("</tbody></table></div>")
    if block.get("note"):
        out.append(f'<p class="secondary">{_inline(block["note"])}</p>')
    out.append("</section>")
    return "".join(out)


def _render_cards(block: Mapping[str, Any], tips: Mapping[str, str] | None = None) -> str:
    cards = block.get("cards") or []
    out = ['<section class="cards">']
    out.append(f'<h3>{_inline(block.get("title") or "")} <span class="count">{len(cards)} cards</span></h3>')
    if len(cards) > 3:
        out.append('<input type="search" class="filter cardq" placeholder="Filter cards: words, a|b, !not">')
    if 3 < len(cards) <= 60:
        out.append('<div class="chips">' + "".join(f'<a href="#card-{html.escape(re.sub(r"[^A-Za-z0-9]+", "-", c["id"]))}">{html.escape(c["id"])}</a>'
                                                   for c in cards) + "</div>")
    out.append('<div class="cardgrid">')
    for c in cards:
        anchor = re.sub(r"[^A-Za-z0-9]+", "-", c["id"])
        course = c.get("course") or {}
        badge = " ".join(str(course[k]) for k in ("Course", "Grade", "Horizon") if course.get(k))
        out.append(f'<article class="card t-{_tone(c.get("tone"))}" id="card-{html.escape(anchor)}"><header>'
                   f'<span class="sym">{html.escape(c["title"])}</span><span class="sub">{_inline(c.get("subtitle"))}</span>'
                   + (f'<span class="badge t-{_tone(c.get("tone"))}">{html.escape(badge)}</span>' if badge else "") + "</header>")
        if c.get("headline"):
            out.append(f'<p class="head">{_inline(c["headline"])}</p>')
        if c.get("metrics"):
            out.append('<div class="kv">' + "".join(
                f'<span title="{html.escape((tips or {}).get(f, ""))}"><b>{html.escape(f)}</b> {html.escape(v)}</span>' for f, v in c["metrics"]) + "</div>")
        body = []
        if course.get("Reason"):
            body.append(f'<h4>Judgment</h4><p>{_inline(course["Reason"])}</p>')
        if c.get("notes"):
            body.append("<h4>Notes (OUTSIDE)</h4><ul>" + "".join(f"<li>{_inline(n)}</li>" for n in c["notes"]) + "</ul>")
        for head, text in c.get("sections") or []:
            body.append(f"<h4>{_inline(head)}</h4><p>{_inline(text)}</p>")
        for head, items in c.get("lists") or []:
            body.append(f"<h4>{_inline(head)}</h4><ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
        if body:
            inner = "".join(body)
            out.append(f'<details{"" if block.get("collapsed") else " open"}><summary>Read</summary>{inner}</details>')
        out.append("</article>")
    out.append("</div>")
    if block.get("note"):
        out.append(f'<p class="secondary">{_inline(block["note"])}</p>')
    out.append("</section>")
    return "".join(out)


def _nice_step(span: float) -> float:
    raw = span / 5 if span > 0 else 1.0
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


def _render_chart(block: Mapping[str, Any]) -> str:
    """Inline SVG lines, a zero line, y grid, every 6th-ish x label, legend; a hover band per x lists every series."""
    xs, series = block.get("xs") or [], block.get("series") or []
    vals = [v for s in series for v in s["ys"] if v is not None]
    out = ['<section class="chart">']
    if block.get("title"):
        out.append(f"<h3>{_inline(block['title'])}</h3>")
    if not xs or not vals:
        out.append('<p class="secondary">No points on this pack.</p></section>')
        return "".join(out)
    w, h, ml, mr, mt, mb = 760, 260, 48, 12, (24 if block.get("y_label") else 10), 34
    lo, hi = min(vals + [0.0]), max(vals + [0.0])
    step = _nice_step(hi - lo)
    lo, hi = step * (lo // step), step * -(-hi // step)
    span = (hi - lo) or 1.0
    px = lambda i: ml + (w - ml - mr) * (i / max(len(xs) - 1, 1))  # noqa: E731
    py = lambda v: mt + (h - mt - mb) * (hi - v) / span  # noqa: E731
    svg = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(str(block.get("title") or "chart"))}">']
    tick = lo
    while tick <= hi + 1e-9:
        y = py(tick)
        cls = "zero" if abs(tick) < 1e-9 else "grid"
        svg.append(f'<line class="{cls}" x1="{ml}" x2="{w - mr}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class="ax" x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end">{tick:g}</text>')
        tick += step
    every = max(1, len(xs) // 7)
    for i in range(0, len(xs), every):
        anchor = "end" if px(i) > w - mr - 40 else ("start" if px(i) < ml + 40 else "middle")
        svg.append(f'<text class="ax" x="{px(i):.1f}" y="{h - mb + 16}" text-anchor="{anchor}">{html.escape(xs[i])}</text>')
    for k, s in enumerate(series):
        d, pen = [], "M"
        for i, v in enumerate(s["ys"]):
            if v is None:
                pen = "M"
                continue
            d.append(f"{pen}{px(i):.1f},{py(v):.1f}")
            pen = "L"
        svg.append(f'<path class="ln c{k}" d="{" ".join(d)}"/>')
    band = (w - ml - mr) / max(len(xs), 1)
    for i, xv in enumerate(xs):
        tip = xv + "".join(f"\n{s['label']}: {s['ys'][i]:.2f}" for s in series if s["ys"][i] is not None)
        svg.append(f'<rect class="hov" x="{px(i) - band / 2:.1f}" y="{mt}" width="{band:.1f}" height="{h - mt - mb}"><title>{html.escape(tip)}</title></rect>')
    if block.get("y_label"):
        svg.append(f'<text class="ax" x="{ml}" y="4" dominant-baseline="hanging">{html.escape(str(block["y_label"]))}</text>')
    svg.append("</svg>")
    out.append("".join(svg))
    out.append('<div class="legend">' + "".join(f'<span><i class="c{k}"></i>{html.escape(s["label"])}</span>' for k, s in enumerate(series)) + "</div>")
    if block.get("note"):
        out.append(f'<p class="secondary">{_inline(block["note"])}</p>')
    out.append("</section>")
    return "".join(out)


def _render_block(block: Mapping[str, Any], idx: str, tips: Mapping[str, str] | None = None) -> str:
    kind = block["type"]
    if kind == "table":
        return _render_table(block, idx, tips)
    if kind == "cards":
        return _render_cards(block, tips)
    if kind == "chart":
        return _render_chart(block)
    if kind in {"h2", "h3"}:
        return f"<{kind}>{_inline(block.get('text'))}</{kind}>"
    if kind == "text":
        cls = ' class="secondary"' if block.get("tone") == "secondary" else ""
        return f"<p{cls}>{_inline(block.get('text'))}</p>"
    if kind == "list":
        return "<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in block.get("items") or []) + "</ul>"
    if kind == "callout":
        title = f"<strong>{_inline(block['title'])}</strong>" if block.get("title") else ""
        return f'<div class="callout t-{_tone(block.get("tone"))}">{title}<p>{_inline(block.get("text"))}</p></div>'
    if kind == "stats":
        items = "".join(
            f'<div class="stat t-{_tone(i.get("tone"))}"><div class="v">{html.escape(str(i.get("value", "")))}</div>'
            f'<div class="l">{_inline(i.get("label"))}</div></div>'
            for i in block.get("items") or []
        )
        return f'<div class="stats">{items}</div>'
    return ""


_CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--accent:#2f5bd3;
--good:#1f7a4d;--bad:#b3261e;--warning:#a15c00;--info:#2f5bd3;--good-bg:#e8f4ee;--bad-bg:#fbeceb;--warning-bg:#fdf3e2;--info-bg:#eaf0fc;--zebra:#fafaf8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151514;--panel:#1e1e1c;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--accent:#8fb0ff;
--good:#6fcf9b;--bad:#ff8a80;--warning:#f3b45b;--info:#8fb0ff;--good-bg:#1b2b22;--bad-bg:#2f1d1b;--warning-bg:#2e2615;--info-bg:#1b2236;--zebra:#232321}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:12px 16px 6px}main{padding:6px 16px 28px}h1{font-size:20px;margin:0 0 2px}h2{font-size:16px;margin:16px 0 6px}h3{font-size:13.5px;margin:14px 0 4px}
main p,main ul,.callout{max-width:170ch}main ul{padding-left:20px;margin:6px 0}main li{margin:2px 0}
.secondary,.count,.sources{color:var(--muted)}.count{font-weight:400;font-size:12px}.sources{font-size:12px;margin-top:4px}
nav{position:sticky;top:0;z-index:30;display:flex;flex-wrap:wrap;gap:4px;padding:6px 16px;background:var(--bg);border-bottom:1px solid var(--line)}
nav button{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:999px;padding:3px 10px;cursor:pointer;font:inherit;font-size:13px}
nav button.on{background:var(--accent);border-color:var(--accent);color:var(--panel)}section.tab{display:none}section.tab.on{display:block}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:6px;background:var(--panel);max-height:78vh;max-width:100%;width:fit-content}
table{border-collapse:separate;border-spacing:0;font-size:12px}th,td{padding:3px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{position:sticky;top:0;z-index:2;background:var(--panel);user-select:none;white-space:normal;vertical-align:bottom;line-height:1.15;min-width:4ch}
tr.hdr th{cursor:grab}tr.hdr th:hover{background:var(--zebra)}
th[data-sort="asc"]::after{content:" \\25B2" attr(data-sortn);color:var(--accent);font-size:10px}
th[data-sort="desc"]::after{content:" \\25BC" attr(data-sortn);color:var(--accent);font-size:10px}
thead tr.grp th{height:21px;font-size:11px;text-align:center;font-weight:600;color:var(--muted);cursor:default;border-bottom:none}
thead tr.grp~tr th{top:21px}thead tr.grp th.g0{background:var(--info-bg)}thead tr.grp th.g1{background:var(--good-bg)}
thead tr.grp th.g2{background:var(--warning-bg)}thead tr.grp th.g3{background:var(--zebra)}
th.gs,td.gs{border-left:2px solid var(--line)}
td.txt{white-space:normal;min-width:24ch;max-width:60ch}.tbl.wide td.txt{max-width:150ch}
.clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;cursor:pointer}
.clamp.open,body.expand .clamp{display:block;-webkit-line-clamp:unset}nav button.tool{margin-left:auto}
.stk{position:sticky;left:0}td.stk{z-index:1;background:var(--panel)}thead th.stk{z-index:3}
tbody tr.z{background:var(--zebra)}tbody tr.z td.stk{background:var(--zebra)}
td.num{text-align:right;font-variant-numeric:tabular-nums}tbody tr:hover td,tbody tr:hover td.stk{background:var(--info-bg)}
tr.t-good td.stk{box-shadow:inset 3px 0 var(--good)}tr.t-bad td.stk{box-shadow:inset 3px 0 var(--bad)}
tr.t-warning td.stk{box-shadow:inset 3px 0 var(--warning)}tr.t-info td.stk{box-shadow:inset 3px 0 var(--info)}
tr.flt th{padding:2px 3px;cursor:default}tr.flt input{width:100%;min-width:6ch;font:inherit;font-size:11px;padding:1px 4px;border:1px solid var(--line);border-radius:3px;background:var(--bg);color:var(--fg)}
tfoot td{position:sticky;bottom:0;background:var(--info-bg);font-weight:600;border-top:1px solid var(--line);white-space:nowrap}tfoot td.stk{z-index:2;background:var(--info-bg)}
.bar{display:flex;flex-wrap:wrap;align-items:center;gap:4px;margin:0 0 5px}
.bar button,.bar summary{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:4px;padding:1px 8px;font:inherit;font-size:12px;cursor:pointer;list-style:none}
.bar button.on{background:var(--accent);border-color:var(--accent);color:var(--panel)}.bar details{position:relative}.bar summary::-webkit-details-marker{display:none}
.bar .menu{position:absolute;z-index:40;top:calc(100% + 2px);left:0;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 10px;max-height:60vh;overflow:auto;
columns:3 18ch;column-gap:14px;font-size:12px;box-shadow:0 6px 18px rgba(0,0,0,.18);width:max-content;max-width:min(92vw,780px)}
.bar .menu label{display:block;white-space:nowrap;break-inside:avoid;cursor:pointer}.bar .menu .g{color:var(--muted)}.bar .menu .mtop{column-span:all;margin-bottom:4px;display:flex;gap:4px}
.bar .hint{color:var(--muted);font-size:11px;cursor:help;border:1px solid var(--line);border-radius:50%;width:18px;height:18px;display:inline-flex;align-items:center;justify-content:center}
th.drag{opacity:.4}body.dragging,body.dragging *{cursor:grabbing!important;user-select:none}
.dropmark{position:fixed;width:3px;background:var(--accent);z-index:50;pointer-events:none;border-radius:2px}
:root{--c0:#2f5bd3;--c1:#b3261e;--c2:#a15c00;--c3:#1f7a4d;--c4:#7a3fb0;--c5:#5f6b73}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--c0:#8fb0ff;--c1:#ff8a80;--c2:#f3b45b;--c3:#6fcf9b;--c4:#c9a2ff;--c5:#b0bac2}}
.chart{margin:10px 0}.chart svg{display:block;width:100%;max-width:960px;height:auto;background:var(--panel);border:1px solid var(--line);border-radius:6px}
.chart .grid{stroke:var(--line)}.chart .zero{stroke:var(--muted)}.chart .ax{fill:var(--muted);font-size:11px}.chart .ln{fill:none;stroke-width:1.8}
.chart .hov{fill:transparent}.chart .hov:hover{fill:var(--fg);fill-opacity:.06}
.chart .c0{stroke:var(--c0)}.chart .c1{stroke:var(--c1)}.chart .c2{stroke:var(--c2)}.chart .c3{stroke:var(--c3)}.chart .c4{stroke:var(--c4)}.chart .c5{stroke:var(--c5)}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted);margin:4px 0}.legend i{display:inline-block;width:14px;height:3px;margin-right:5px;vertical-align:middle}
.legend i.c0{background:var(--c0)}.legend i.c1{background:var(--c1)}.legend i.c2{background:var(--c2)}.legend i.c3{background:var(--c3)}.legend i.c4{background:var(--c4)}.legend i.c5{background:var(--c5)}
.callout{border-left:4px solid var(--line);background:var(--panel);padding:8px 12px;border-radius:4px;margin:10px 0}.callout p{margin:4px 0 0}
.callout.t-good{border-color:var(--good);background:var(--good-bg)}.callout.t-bad{border-color:var(--bad);background:var(--bad-bg)}
.callout.t-warning{border-color:var(--warning);background:var(--warning-bg)}.callout.t-info{border-color:var(--info);background:var(--info-bg)}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:10px 0}.stat{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 10px}
.stat .v{font-size:18px;font-weight:600}.stat .l{font-size:12px;color:var(--muted)}.stat.t-good .v{color:var(--good)}.stat.t-bad .v{color:var(--bad)}.stat.t-warning .v{color:var(--warning)}.stat.t-info .v{color:var(--info)}
.filter{margin:0;padding:2px 8px;border:1px solid var(--line);border-radius:4px;background:var(--panel);color:var(--fg);width:min(280px,100%);font:inherit;font-size:12px}
code{font-size:12px;background:var(--zebra);padding:0 3px;border-radius:3px}.warn{color:var(--warning);font-size:12px}
td.up{color:var(--good);background:var(--good-bg);font-weight:600}td.dn{color:var(--bad);background:var(--bad-bg);font-weight:600}
.bar .scrl{color:var(--muted);font-size:11px;margin-left:6px}.bar button.scr{border-style:dashed}.bar button.scr.on{border-style:solid}
.snote{color:var(--muted);font-size:12px;margin:0 0 5px;max-width:170ch}.snote:empty{display:none}
.cardgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,560px),1fr));gap:10px;margin:6px 0}
.card{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:6px;padding:8px 12px;min-width:0}
.card.t-good{border-left-color:var(--good)}.card.t-bad{border-left-color:var(--bad)}.card.t-warning{border-left-color:var(--warning)}.card.t-info{border-left-color:var(--info)}
.card header{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px}.card .sym{font-weight:700;font-size:15px}.card .sub{color:var(--muted);font-size:12px}
.badge{margin-left:auto;font-size:11px;font-weight:600;border:1px solid var(--line);border-radius:999px;padding:0 8px}
.badge.t-good{color:var(--good);border-color:var(--good)}.badge.t-bad{color:var(--bad);border-color:var(--bad)}.badge.t-warning{color:var(--warning);border-color:var(--warning)}.badge.t-info{color:var(--info);border-color:var(--info)}
.card .head{font-weight:600;margin:5px 0;font-size:13.5px}.kv{display:flex;flex-wrap:wrap;gap:3px 12px;font-size:12px;margin:4px 0 6px;font-variant-numeric:tabular-nums}
.kv span{white-space:nowrap;cursor:help}.kv b{color:var(--muted);font-weight:500}.card summary{cursor:pointer;color:var(--accent);font-size:12px}
.card h4{font-size:12px;margin:8px 0 1px;color:var(--muted)}.card p,.card li{font-size:13px;margin:1px 0}.card ul{padding-left:18px;margin:2px 0}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 6px}.chips a{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:0 8px;text-decoration:none;color:var(--fg);background:var(--panel)}
section.cards input.cardq{margin:0 0 4px}
"""

# Page script. Every table.grid: click a header to sort (numbers high-low first, text and dates A-Z / oldest first;
# again to flip, a third time to clear; shift-click adds a key), drag a header left or right to move the column
# (remembered per page and table in localStorage), a toolbar with row search, a filter box per column
# (>5 <=-2 1..10 =exact !not a|b, `=` empty, `!` not empty), a columns menu, Heat (shading of the visible rows),
# Stats (a footer row: median per numeric column, top value per text column), CSV of the visible grid and Reset.
# Alt+click a cell filters its column to that value. Nothing here ranks or scores: it only reorders and hides.
_JS = r"""
const tabs=[...document.querySelectorAll('section.tab')],btns=[...document.querySelectorAll('nav button[data-tab]')];
const PAGE=document.body.dataset.page||document.title,$$=(el,s)=>[...el.querySelectorAll(s)];
const store={get(k){try{return JSON.parse(localStorage.getItem(k)||'null');}catch(e){return null;}},
 set(k,v){try{if(v===null)localStorage.removeItem(k);else localStorage.setItem(k,JSON.stringify(v));}catch(e){}}};
const HELP='Header: click to sort (numbers high to low first, text A-Z, dates oldest first); click again to flip, a third time to clear. '+
 'Shift+click adds a second sort key. Drag a header left or right to move the column (remembered for this page). '+
 'Alt+click a cell: filter its column to that value. Filter syntax: >5  <=-2  1..10  2026-11-01..2026-11-07  =exact  !not  a|b (either)  '+
 '= (empty)  ! (not empty); space-separated terms must all match; "quoted text" keeps spaces. '+
 'Screens (dashed buttons): a preset of column filters and a sort; click again to clear. Green / red cells marked ▲ / ▼ are measured directions, not Heat.';
const ISO=/^\d{4}-\d{2}-\d{2}/,UNIT=/^(%|x|pp|bp|[kKMBT]|d|w)?([\s–\-\/(].*)?$/;
function val(s){s=(s||'').trim();if(!s)return null;if(ISO.test(s))return Date.parse(s.slice(0,10));
 const t=s.replace(/−/g,'-').replace(/[$,+▲▼]/g,''),m=t.match(/^-?\d*\.?\d+/);if(!m)return null;
 return UNIT.test(t.slice(m[0].length).trim())?parseFloat(m[0]):null;}
const txt=c=>c?c.textContent.trim():'';
const hdr=t=>t.tHead.querySelector('tr.hdr'),lines=t=>$$(t,'tr.hdr,tr.flt,tbody tr,tfoot tr');
function kind(t,th){if(th._kind)return th._kind;const i=th.cellIndex;let n=0,d=0,x=0;
 for(const r of t.tBodies[0].rows){const s=txt(r.cells[i]);if(!s)continue;if(ISO.test(s))d++;else if(val(s)!==null)n++;else x++;if(n+d+x>=120)break;}
 return th._kind=d&&d>=n+x?'date':(n&&n>=3*x?'num':'text');}
function stripe(t){let k=0;for(const r of t.tBodies[0].rows){if(!r.hidden)r.classList.toggle('z',k++%2===1);}}
function sortRows(t){const st=t._st,tb=t.tBodies[0],keys=st.sorts.map(o=>[o.th.cellIndex,o.asc]),rk=(s,v)=>v!==null?0:s?1:2;
 const rows=[...tb.rows];rows.sort((a,b)=>{for(const [i,asc] of keys){const xs=txt(a.cells[i]),ys=txt(b.cells[i]),vx=val(xs),vy=val(ys),rx=rk(xs,vx),ry=rk(ys,vy);
  if(rx!==ry)return rx-ry;const c=rx===0?vx-vy:rx===1?xs.localeCompare(ys,undefined,{numeric:true,sensitivity:'base'}):0;if(c)return asc?c:-c;}
  return a._i-b._i;});
 rows.forEach(r=>tb.appendChild(r));
 $$(t,'tr.hdr th').forEach(th=>{const n=st.sorts.findIndex(o=>o.th===th);th.dataset.sort=n<0?'':st.sorts[n].asc?'asc':'desc';
  th.dataset.sortn=n<0||st.sorts.length<2?'':String(n+1);});stripe(t);}
function onSort(t,th,e){const st=t._st,first=kind(t,th)!=='num',n=st.sorts.findIndex(o=>o.th===th);
 if(e.shiftKey){if(n<0)st.sorts.push({th,asc:first});else if(st.sorts[n].asc===first)st.sorts[n].asc=!first;else st.sorts.splice(n,1);}
 else if(n<0||st.sorts.length>1)st.sorts=[{th,asc:first}];else if(st.sorts[0].asc===first)st.sorts[0].asc=!first;else st.sorts=[];
 sortRows(t);}
const terms=x=>(x.toLowerCase().match(/(?:[^\s"]+|"[^"]*")+/g)||[]).map(s=>s.replace(/"/g,''));
function one(a,low,v){let m;if(a==='=')return !low;if(a==='!'||a==='!=')return !!low;if(/^(>=|<=|>|<)$/.test(a))return true;
 if((m=a.match(/^(>=|<=|>|<)(.+)$/))){const x=val(m[2]);if(x===null||v===null)return false;return m[1]==='>'?v>x:m[1]==='<'?v<x:m[1]==='>='?v>=x:v<=x;}
 if((m=a.match(/^(\d{4}-\d{2}-\d{2}|-?\d*\.?\d+)\.\.(\d{4}-\d{2}-\d{2}|-?\d*\.?\d+)$/)))return v!==null&&v>=val(m[1])&&v<=val(m[2]);
 if(a[0]==='=')return low===a.slice(1);if(a[0]==='!')return !low.includes(a.slice(1));return low.includes(a);}
const pass=(ts,s,isNum)=>{const low=s.toLowerCase(),v=isNum?val(s):null;return ts.every(tok=>tok.split('|').some(a=>one(a,low,v)));};
function apply(t){const st=t._st,q=terms(st.q),fs=[];st.f.forEach((x,th)=>{const ts=terms(x);if(ts.length)fs.push([th,ts]);});
 const tb=t.tBodies[0];let n=0;
 for(const r of tb.rows){const ok=(!q.length||pass(q,r.textContent,false))&&fs.every(([th,ts])=>pass(ts,txt(r.cells[th.cellIndex]),true));r.hidden=!ok;if(ok)n++;}
 const total=tb.rows.length;if(t._count)t._count.textContent=n===total?total+' rows':n+' of '+total+' rows';
 const fb=t._bar&&t._bar.querySelector('[data-a=flt]');if(fb)fb.textContent='Column filters'+(fs.length?' ('+fs.length+')':'');
 stripe(t);if(t._st.sum)summary(t);if(t._st.heat)heat(t);}
function regroup(t){const h=hdr(t),grp=t.tHead.querySelector('tr.grp'),vis=[...h.cells].filter(c=>!c.hidden),g=c=>c.dataset.group||'';
 const starts=new Set(),first=vis.length?vis[0].cellIndex:-1;if(grp)vis.forEach((c,k)=>{if(k&&g(c)!==g(vis[k-1]))starts.add(c.cellIndex);});
 for(const r of lines(t))[...r.cells].forEach((c,i)=>{c.classList.toggle('gs',starts.has(i));c.classList.toggle('stk',i===first);});
 if(!grp)return;grp.textContent='';let k=0;
 while(k<vis.length){const label=g(vis[k]);let j=k;while(j+1<vis.length&&g(vis[j+1])===label)j++;const th=document.createElement('th');th.colSpan=j-k+1;
  if(label){th.className='g'+(t._shade.get(label)%4);th.textContent=label;}if(k)th.classList.add('gs');grp.appendChild(th);k=j+1;}}
function stick(t){let top=0;for(const r of t.tHead.rows){if(r.hidden)continue;for(const c of r.cells)c.style.top=top+'px';top+=r.offsetHeight;}}
function moveCol(t,f,to){if(to===f||to===f+1)return false;for(const r of lines(t)){const c=r.cells[f];if(c)r.insertBefore(c,r.cells[to]||null);}return true;}
function setHidden(t,th,on){const i=th.cellIndex;for(const r of lines(t)){const c=r.cells[i];if(c)c.hidden=on;}}
function arrange(t,ths){ths.forEach((th,pos)=>{const f=th.cellIndex;if(f>pos)moveCol(t,f,pos);});}
const lkey=t=>'canvas-layout|'+PAGE+'|'+(t._title||t.id);
function save(t){const hs=[...hdr(t).cells];store.set(lkey(t),{order:hs.map(c=>c._key),hidden:hs.filter(c=>c.hidden).map(c=>c._key)});}
function restore(t){const s=store.get(lkey(t));if(!s||!Array.isArray(s.order))return;const left=[...hdr(t).cells],want=[];
 s.order.forEach(k=>{const i=left.findIndex(c=>c._key===k);if(i>=0)want.push(left.splice(i,1)[0]);});arrange(t,want.concat(left));
 const hid=new Set(s.hidden||[]);[...hdr(t).cells].forEach(c=>{if(hid.has(c._key))setHidden(t,c,true);});}
function after(t){regroup(t);stick(t);if(t._st.sum)summary(t);save(t);}
const qt=(s,p)=>{const i=(s.length-1)*p,lo=Math.floor(i),hi=Math.ceil(i);return s[lo]+(s[hi]-s[lo])*(i-lo);};
const fmt=v=>Math.abs(v)>=1000?Math.round(v).toLocaleString('en-US'):Math.abs(v)>=100?v.toFixed(1):v.toFixed(2);
function summary(t){let f=t.tFoot;if(!t._st.sum){if(f)f.hidden=true;return;}
 if(!f){f=t.createTFoot();const r=f.insertRow();[...hdr(t).cells].forEach(th=>{r.insertCell().hidden=th.hidden;});regroup(t);}
 f.hidden=false;const rows=[...t.tBodies[0].rows].filter(r=>!r.hidden),cells=f.rows[0].cells,hs=[...hdr(t).cells];
 hs.forEach((th,i)=>{const c=cells[i],k=kind(t,th),vals=rows.map(r=>txt(r.cells[i])).filter(Boolean);c.classList.toggle('num',k==='num');c.title='';c.textContent='';
  if(k==='num'){const s=vals.map(val).filter(v=>v!==null).sort((a,b)=>a-b);if(!s.length)return;const mean=s.reduce((a,b)=>a+b,0)/s.length;
   c.textContent=fmt(qt(s,.5));c.title=th._key+': n '+s.length+' · min '+fmt(s[0])+' · p25 '+fmt(qt(s,.25))+' · median '+fmt(qt(s,.5))+
    ' · p75 '+fmt(qt(s,.75))+' · max '+fmt(s[s.length-1])+' · mean '+fmt(mean);}
  else if(vals.length){const cnt=new Map();vals.forEach(v=>cnt.set(v,(cnt.get(v)||0)+1));const top=[...cnt].sort((a,b)=>b[1]-a[1]);
   c.textContent=cnt.size<=12?top[0][0].slice(0,18)+' '+Math.round(100*top[0][1]/vals.length)+'%':cnt.size+' distinct';
   c.title=th._key+' ('+vals.length+' filled, '+cnt.size+' distinct)\n'+top.slice(0,10).map(([v,n])=>v.slice(0,40)+': '+n).join('\n');}});
 const i0=hs.findIndex(h=>!h.hidden);if(i0>=0&&kind(t,hs[i0])!=='num'){cells[i0].textContent='median · '+rows.length+' rows';}}
function heat(t){const tb=t.tBodies[0],rows=[...tb.rows];for(const r of rows)for(const c of r.cells)c.style.background='';if(!t._st.heat)return;
 const vis=rows.filter(r=>!r.hidden);[...hdr(t).cells].forEach((th,i)=>{if(th.hidden||th.classList.contains('stk')||kind(t,th)!=='num')return;
  const vs=vis.map(r=>val(txt(r.cells[i]))),nums=vs.filter(v=>v!==null);if(nums.length<3)return;
  if(nums.some(v=>v<0)&&nums.some(v=>v>0)){const m=qt(nums.map(Math.abs).sort((a,b)=>a-b),.95)||1;
   vis.forEach((r,k)=>{const v=vs[k];if(!v)return;r.cells[i].style.background='color-mix(in srgb,var('+(v<0?'--bad':'--good')+') '+Math.round(Math.min(1,Math.abs(v)/m)*42)+'%,transparent)';});}
  else{const s=[...nums].sort((a,b)=>a-b),d=s.length-1||1;vis.forEach((r,k)=>{const v=vs[k];if(v===null)return;let lo=0,hi=s.length;
   while(lo<hi){const md=(lo+hi)>>1;if(s[md]<v)lo=md+1;else hi=md;}r.cells[i].style.background='color-mix(in srgb,var(--info) '+Math.round(lo/d*34)+'%,transparent)';});}});}
function csv(t){const hs=[...hdr(t).cells].filter(c=>!c.hidden),ix=hs.map(c=>c.cellIndex),esc=s=>/[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;
 const out=[hs.map(c=>esc(c._key)).join(',')];for(const r of t.tBodies[0].rows)if(!r.hidden)out.push(ix.map(i=>esc(txt(r.cells[i]))).join(','));
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['﻿'+out.join('\r\n')],{type:'text/csv'}));
 a.download=(PAGE+'-'+(t._title||t.id)).replace(/[^\w.-]+/g,'_').slice(0,120)+'.csv';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),2000);}
function fltRow(t){let r=t.tHead.querySelector('tr.flt');if(r)return r;r=document.createElement('tr');r.className='flt';r.hidden=true;
 [...hdr(t).cells].forEach(th=>{const c=document.createElement('th'),i=document.createElement('input'),k=kind(t,th);c.hidden=th.hidden;i.type='search';
  i.placeholder=k==='num'?'>0  a..b':k==='date'?'>2026-11-01':'text  a|b';i.title=HELP;i.oninput=()=>{t._st.f.set(th,i.value);apply(t);};th._in=i;c.appendChild(i);r.appendChild(c);});
 t.tHead.appendChild(r);regroup(t);return r;}
function showFlt(t,on){const r=fltRow(t);r.hidden=!on;const b=t._bar.querySelector('[data-a=flt]');b.classList.toggle('on',on);stick(t);}
function menu(t,m){m.textContent='';const top=document.createElement('div');top.className='mtop';
 const all=document.createElement('button');all.textContent='Show all';all.onclick=e=>{e.preventDefault();[...hdr(t).cells].forEach(c=>setHidden(t,c,false));after(t);menu(t,m);};
 top.appendChild(all);m.appendChild(top);
 [...hdr(t).cells].forEach(th=>{const l=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.checked=!th.hidden;
  c.onchange=()=>{if(!c.checked&&[...hdr(t).cells].filter(x=>!x.hidden).length<=1){c.checked=true;return;}setHidden(t,th,!c.checked);after(t);};
  l.append(c,' '+th._key);if(th.dataset.group){const g=document.createElement('span');g.className='g';g.textContent=' · '+th.dataset.group;l.appendChild(g);}m.appendChild(l);});}
function reset(t){const st=t._st;t._orig.forEach(th=>setHidden(t,th,false));arrange(t,t._orig);st.sorts=[];st.q='';st.f.clear();st.heat=false;st.sum=false;
 if(t._bar){t._bar.querySelector('input.filter').value='';$$(t._bar,'button.on').forEach(x=>x.classList.remove('on'));}
 const fr=t.tHead.querySelector('tr.flt');if(fr){fr.hidden=true;$$(fr,'input').forEach(i=>i.value='');}
 if(t._note)t._note.textContent='';
 sortRows(t);heat(t);summary(t);apply(t);regroup(t);stick(t);store.set(lkey(t),null);}
function bar(t){const sec=t.closest('section.tbl'),b=document.createElement('div');b.className='bar';
 b.innerHTML='<input type="search" class="filter" placeholder="Search rows: words, a|b, !not">'+
  '<button data-a="flt" title="A filter box under every header (>5, 1..10, =exact, !not, a|b)">Column filters</button>'+
  '<details class="cols"><summary title="Show or hide columns">Columns</summary><div class="menu"></div></details>'+
  '<button data-a="heat" title="Shade numeric columns over the visible rows: green / red around 0 when a column has both signs, blue by rank otherwise">Heat</button>'+
  '<button data-a="sum" title="Footer row over the visible rows: median per numeric column (hover: n, min, quartiles, max, mean), top value or distinct count per text column">Stats</button>'+
  '<button data-a="csv" title="Download the visible rows and columns, in the order shown, as CSV">CSV</button>'+
  '<button data-a="reset" title="Original column order, every column, no sort, no filters">Reset</button><span class="hint">?</span>';
 b.querySelector('.hint').title=HELP;sec.insertBefore(b,sec.querySelector('.scroll'));t._bar=b;
 if(!t._count){const c=document.createElement('span');c.className='count';b.appendChild(c);t._count=c;}
 const q=b.querySelector('input.filter');q.oninput=()=>{t._st.q=q.value;apply(t);};
 b.querySelector('[data-a=flt]').onclick=()=>{const r=t.tHead.querySelector('tr.flt');showFlt(t,!r||r.hidden);};
 b.querySelector('[data-a=heat]').onclick=e=>{t._st.heat=!t._st.heat;e.target.classList.toggle('on',t._st.heat);heat(t);};
 b.querySelector('[data-a=sum]').onclick=e=>{t._st.sum=!t._st.sum;e.target.classList.toggle('on',t._st.sum);summary(t);};
 b.querySelector('[data-a=csv]').onclick=()=>csv(t);b.querySelector('[data-a=reset]').onclick=()=>reset(t);
 const det=b.querySelector('details');det.addEventListener('toggle',()=>{if(det.open)menu(t,det.querySelector('.menu'));});
 let scr=[];try{scr=JSON.parse(sec.dataset.screens||'[]');}catch(e){}
 if(scr.length){const l=document.createElement('span');l.className='scrl';l.textContent='Screens:';b.appendChild(l);
  const note=document.createElement('p');note.className='snote';sec.insertBefore(note,sec.querySelector('.scroll'));t._note=note;
  scr.forEach(s=>{const x=document.createElement('button');x.className='scr';x.textContent=s.label;
   x.title=(s.explain||'')+'\nFilters: '+Object.entries(s.filters||{}).map(([h,v])=>h+' '+v).join('  ')+(s.sort?'\nSort: '+s.sort:'');
   x.onclick=()=>screen(t,s,x);b.appendChild(x);});}}
function screen(t,s,x){const on=!x.classList.contains('on'),st=t._st,fr=fltRow(t);$$(t._bar,'button.scr').forEach(y=>y.classList.remove('on'));
 st.f.clear();$$(fr,'input').forEach(i=>{i.value='';});
 if(on){const hs=[...hdr(t).cells];Object.entries(s.filters||{}).forEach(([h,v])=>{const th=hs.find(c=>c._key===h);if(th){th._in.value=v;st.f.set(th,v);}});
  st.sorts=String(s.sort||'').split(',').map(k=>k.trim()).filter(Boolean).map(k=>({th:hs.find(c=>c._key===k.replace(/^[-+]/,'')),asc:!k.startsWith('-')})).filter(o=>o.th);
  if(st.sorts.length)sortRows(t);x.classList.add('on');showFlt(t,true);}
 if(t._note)t._note.textContent=on?s.label+': '+(s.explain||''):'';apply(t);}
let drag=null,dragged=false;const mark=document.createElement('div');mark.className='dropmark';mark.hidden=true;document.body.appendChild(mark);
function dropAt(t,x){const vis=[...hdr(t).cells].filter(c=>!c.hidden);for(const c of vis){const b=c.getBoundingClientRect();if(x<b.left+b.width/2)return[c.cellIndex,b.left];}
 const l=vis[vis.length-1];return[l.cellIndex+1,l.getBoundingClientRect().right];}
function grid(t){const h=t.tHead&&hdr(t);if(!h)return;const sec=t.closest('section.tbl'),h3=sec&&sec.querySelector(':scope>h3');
 t._st={sorts:[],q:'',f:new Map(),heat:false,sum:false};t._orig=[...h.cells];t._shade=new Map();t._count=h3&&h3.querySelector('.count');
 t._title=h3?h3.textContent.replace(t._count?t._count.textContent:'','').trim():'';
 t._orig.forEach(th=>{th._key=th.textContent.trim();const g=th.dataset.group;if(g&&!t._shade.has(g))t._shade.set(g,t._shade.size);
  th.addEventListener('click',e=>{if(!dragged)onSort(t,th,e);});});
 h.addEventListener('pointerdown',e=>{const th=e.target.closest('th');if(!th||e.button!==0)return;e.preventDefault();drag={t,th,x:e.clientX,on:false};});
 [...t.tBodies[0].rows].forEach((r,i)=>{r._i=i;});if(sec&&(t.tBodies[0].rows.length>5||h.cells.length>6))bar(t);
 t.tBodies[0].addEventListener('click',e=>{if(!e.altKey||!t._bar)return;const td=e.target.closest('td');if(!td)return;e.preventDefault();e.stopPropagation();
  const th=hdr(t).cells[td.cellIndex],v=txt(td),x=/\s/.test(v)?'="'+v+'"':'='+v;showFlt(t,true);th._in.value=th._in.value===x?'':x;t._st.f.set(th,th._in.value);apply(t);},true);
 restore(t);regroup(t);stripe(t);}
document.addEventListener('pointermove',e=>{if(!drag)return;if(!drag.on){if(Math.abs(e.clientX-drag.x)<6)return;drag.on=true;drag.th.classList.add('drag');
 document.body.classList.add('dragging');mark.hidden=false;}
 const sc=drag.t.closest('.scroll'),b=sc.getBoundingClientRect();if(e.clientX<b.left+28)sc.scrollLeft-=18;else if(e.clientX>b.right-28)sc.scrollLeft+=18;
 const [to,x]=dropAt(drag.t,e.clientX);drag.to=to;mark.style.left=(x-1)+'px';mark.style.top=b.top+'px';mark.style.height=Math.max(0,Math.min(b.bottom,innerHeight)-b.top)+'px';});
function endDrag(){const d=drag;drag=null;if(!d||!d.on)return;d.th.classList.remove('drag');document.body.classList.remove('dragging');mark.hidden=true;
 dragged=true;setTimeout(()=>{dragged=false;},0);if(d.to!==undefined&&moveCol(d.t,d.th.cellIndex,d.to))after(d.t);}
document.addEventListener('pointerup',endDrag);document.addEventListener('pointercancel',endDrag);
document.addEventListener('click',e=>{$$(document,'details.cols[open]').forEach(d=>{if(!d.contains(e.target))d.open=false;});});
function show(id){tabs.forEach(t=>t.classList.toggle('on',t.id===id));btns.forEach(b=>b.classList.toggle('on',b.dataset.tab===id));history.replaceState(null,'','#'+id);
 const tab=document.getElementById(id);if(tab)$$(tab,'table.grid').forEach(stick);}
btns.forEach(b=>b.onclick=()=>show(b.dataset.tab));
document.querySelectorAll('.clamp').forEach(d=>d.onclick=e=>{if(!e.altKey)d.classList.toggle('open');});
const tool=document.querySelector('nav button.tool');if(tool)tool.onclick=()=>{const on=document.body.classList.toggle('expand');tool.textContent=on?'Clamp text':'Expand text';};
$$(document,'table.grid').forEach(grid);
$$(document,'input.cardq').forEach(q=>{const sec=q.closest('section.cards'),cnt=sec.querySelector('.count');
 q.oninput=()=>{const ts=terms(q.value);let n=0;$$(sec,'article.card').forEach(a=>{const ok=!ts.length||pass(ts,a.textContent,false);a.hidden=!ok;if(ok)n++;});
  cnt.textContent=n+' cards';};});
let rz=0;addEventListener('resize',()=>{clearTimeout(rz);rz=setTimeout(()=>{const on=document.querySelector('section.tab.on');if(on)$$(on,'table.grid').forEach(stick);},150);});
show((location.hash||'').slice(1)&&document.getElementById(location.hash.slice(1))?location.hash.slice(1):tabs[0].id);
"""


# The standard how-to on every Guide tab: what the toolbar does and how the shading reads.
TABLE_HOWTO = [
    ["Sort", "Click a header: numbers high to low first, text A-Z, dates oldest first; again to flip, a third time to clear. Shift+click adds a second key."],
    ["Move / hide columns", "Drag a header left or right (remembered for this page and table). Columns menu: tick columns on or off; Show all; Reset restores the original layout."],
    ["Search rows", "The box left of the buttons: every word must appear somewhere in the row; a|b = either; !x = not."],
    ["Column filters", "A box under every header: >5  <=-2  1..10  2026-11-01..2026-11-07  =exact  !not  a|b  = (empty)  ! (not empty). Alt+click a cell filters its column to that value."],
    ["Screens", "Dashed buttons above a table: a named preset of column filters and a sort, explained in the line under the toolbar and in the Screens table on this tab. Click again to clear. A screen narrows the rows; it does not rank or score them."],
    ["Heat", "Shades numeric columns over the visible rows: green / red around 0 when a column has both signs (stronger = larger), blue by rank otherwise. It shows size, not whether high is good: the Ledger gives each column's direction."],
    ["▲ / ▼ cells", "Green / red cells written ▲+1.2 / ▼-0.8 are measured directions (the cell's evidence cleared its rule); a bare number in the same column was measured without a clear direction. They sort and filter by the number (>0, <0)."],
    ["Stats", "A footer row over the visible rows: the median of each numeric column (hover for n, quartiles, min, max, mean), the top value of each text column."],
    ["CSV", "Downloads the visible rows and columns in the order shown."],
    ["Tooltips", "Hover a header for its Ledger meaning; hover a screen button for its filters."],
]


def _guide_blocks(guide: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if guide.get("intro"):
        blocks.append({"type": "text", "text": guide["intro"]})
    for section in guide.get("sections") or []:
        if section.get("title"):
            blocks.append({"type": "h2", "text": section["title"]})
        if section.get("text"):
            blocks.append({"type": "text", "text": section["text"]})
        if section.get("items"):
            blocks.append({"type": "list", "items": list(section["items"])})
        if section.get("rows"):
            headers = [str(h) for h in section.get("headers") or ["Step", "What to do"]]
            blocks.append({"type": "table", "headers": headers, "rows": [[str(c) for c in r] for r in section["rows"]], "tones": [], "wide": True})
    if guide.get("screens"):
        blocks.append({"type": "h2", "text": "Screens on this page"})
        blocks.append({"type": "table", "headers": ["Tab", "Table", "Screen", "Filters and sort", "Shows / how to use it"],
                       "rows": guide["screens"], "tones": [], "wide": True})
    blocks.append({"type": "h2", "text": "Using the tables"})
    blocks.append({"type": "table", "headers": ["Tool", "How it works"], "rows": TABLE_HOWTO, "tones": [], "wide": True})
    return blocks


def render_html(page: Mapping[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    guide = [{"label": "Guide", "blocks": _guide_blocks(page["guide"])}] if page.get("guide") else []
    tabs = list(page["tabs"]) + guide + [{"label": "Ledger", "blocks": [
        {"type": "table", "title": group["group"], "headers": ["Field or value", "Meaning"], "rows": group["rows"], "tones": [],
         "wide": True}
        for group in page["ledger"]
    ]}]
    nav, body = [], []
    for t, tab in enumerate(tabs):
        tab_id = _norm(tab["label"]) or f"tab{t}"
        nav.append(f'<button data-tab="{tab_id}">{html.escape(tab["label"])}</button>')
        inner = "".join(_render_block(b, f"t{t}b{i}", page.get("tips")) for i, b in enumerate(tab["blocks"]))
        body.append(f'<section class="tab" id="{tab_id}">{inner}</section>')
    sources = " · ".join(_inline(s) for s in page.get("sources") or [])
    warn = (f'<p class="warn">{len(page["warnings"])} ledger warnings at render time.</p>' if page.get("warnings") else "")
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(page['title'])}</title><style>{_CSS}</style></head>"
        f'<body data-page="{html.escape(re.sub(r"-[0-9]{8}$", "", str(page["name"])))}">'
        f"<header><h1>{_inline(page['title'])}</h1>"
        + (f'<div class="secondary">{_inline(page["subtitle"])}</div>' if page.get("subtitle") else "")
        + (f'<div class="sources">Sources: {sources}</div>' if sources else "")
        + f'<div class="sources">Rendered {stamp} · {html.escape(page["name"])}</div>{warn}'
        f"</header><nav>{''.join(nav)}<button class=\"tool\" title=\"Show every long text cell in full\">Expand text</button></nav>"
        f"<main>{''.join(body)}</main><script>{_JS}</script></body></html>"
    )


def write_index(pages_dir: Path) -> Path:
    pages = sorted((p for p in pages_dir.glob("*.html") if p.name != "index.html"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    rows = [[f"{p.stem}", datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")] for p in pages]
    links = "".join(f'<tr><td><a href="{html.escape(p.name)}">{html.escape(n)}</a></td><td>{d}</td></tr>'
                    for p, (n, d) in zip(pages, rows))
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Operator pages</title><style>{_CSS}</style></head>'
           f'<body><header><h1>Operator pages</h1><div class="secondary">{len(pages)} pages, newest first</div></header>'
           f'<main><div class="scroll"><table><thead><tr><th>Page</th><th>Written</th></tr></thead><tbody>{links}</tbody></table></div></main></body></html>')
    out = pages_dir / "index.html"
    out.write_text(doc, encoding="utf-8")
    return out


# ---------------------------------------------------------------- templates

DEFAULT_TEMPLATES_DIR = REPO_ROOT / "config" / "generic_utils" / "pages"
_DROP = object()


def _lookup(root: Any, dotted: str) -> Any:
    current = root
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def load_template(name_or_path: str | Path) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.suffix:
        path = DEFAULT_TEMPLATES_DIR / f"{name_or_path}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _fill_block(block: Mapping[str, Any], judgment: Mapping[str, Any] | None, tone_map: Mapping[str, str]) -> Any:
    out = dict(block)
    manual = judgment is None
    if out.pop("judgment_only", False) and manual:
        return _DROP
    source_key = out.pop("from", None)
    if source_key:
        value = None if manual else _lookup(judgment, source_key)
        if value is None:
            return _DROP
        kind = out.get("type")
        if kind == "callout" and isinstance(value, Mapping):
            out.update({k: value[k] for k in ("title", "text", "tone") if k in value})
        elif kind in {"callout", "text", "h2", "h3"}:
            out["text"] = str(value)
        elif kind in {"list", "stats"}:
            out["items"] = list(value)
        elif kind == "table" and isinstance(value, Mapping):
            out.update({k: value[k] for k in ("headers", "rows", "tones", "note", "title") if k in value})
        else:
            raise ValueError(f"Cannot fill a {kind!r} block from {source_key!r}")
    ids_key = out.pop("ids_from", None)
    if ids_key and not manual:
        # The judgment names the rows (e.g. Best 25); without that list (and on the data-only page) the sort / limit stand.
        out["ids"] = list(_lookup(judgment, ids_key) or [])
        if out["ids"]:
            out.pop("limit", None)
    notes_key = out.pop("notes_from", None)
    if notes_key:
        out["notes"] = {} if manual else dict(_lookup(judgment, notes_key) or {})
    extra_key = out.pop("extra_from", None)
    if extra_key:
        if manual:
            out.pop("extra_columns", None)
            out.pop("tone_field", None)
        else:
            out["extra"] = _lookup(judgment, extra_key) or {}
            out.setdefault("tone_field", "Course")
            out.setdefault("tone_map", dict(tone_map))
    return out


def pack_sources(pack: str) -> list[str]:
    """Source lock of a pack as page `sources` lines (run ids, never copied from an old page)."""
    path = Path(pack) / "briefing_pack.json"
    path = path if path.is_absolute() else REPO_ROOT / path
    if not path.exists():
        return [f"pack `{Path(pack).name}`"]
    src = json.loads(path.read_text(encoding="utf-8")).get("sources") or {}
    lines = [f"pack `{Path(pack).name}`"]
    for label, key in (("pred", "prediction_run_id"), ("all-fields", "all_fields_run_id"), ("edge", "edge_parent_dir"),
                       ("MTP", "mtp_dir"), ("holdings", "holdings_run_dir"), ("priors", "priors_path")):
        if src.get(key):
            lines.append(f"{label} `{Path(str(src[key])).name}`")
    return lines


def _block_texts(block: Mapping[str, Any]) -> list[str]:
    texts = [str(block.get(k) or "") for k in ("title", "text")]
    texts += [str(i.get("label") or i.get("value") or "") if isinstance(i, Mapping) else str(i) for i in block.get("items") or []]
    if not block.get("source"):
        texts += [str(cell) for row in block.get("rows") or [] for cell in row]
    texts += [str(n) for lines in (block.get("notes") or {}).values() for n in lines]
    return texts


def bare_course_mentions(tabs: Sequence[Mapping[str, Any]], judgment: Mapping[str, Any]) -> list[str]:
    """Warnings for page text that names a judged stock by its bare ticker (the judgment knows its exchange)."""
    from generic_utils.symbol_identity import qualify_text, ticker_map

    by_ticker = ticker_map((judgment.get("courses") or {}).keys())
    found: dict[str, set[str]] = {}
    for tab in tabs:
        for block in tab.get("blocks") or []:
            for text in _block_texts(block):
                # a URL path is not page text (quote pages carry bare tickers)
                for token in qualify_text(_URL.sub("", text), by_ticker)[1]:
                    found.setdefault(by_ticker[token], set()).add(str(tab.get("label")))
    return [f"bare ticker {full.split(':', 1)[1]!r} in page text ({', '.join(sorted(tabs_))}); write {full}"
            for full, tabs_ in sorted(found.items())]


def render_template(
    template: Mapping[str, Any],
    *,
    pack: str,
    day: str,
    judgment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Template + pack folder + judgment -> page spec. judgment=None is the manual (data-only) page.

    Hooks: `from` fills a block from a dotted judgment key (block dropped when missing),
    `ids_from` keeps only the rows a judgment list names, in that order (Best 25),
    `judgment_only` drops a block on the manual page, `extra_from` names the
    symbol -> {Course, Grade, Horizon, Reason} map a table joins. {PACK}, {PLAYBOOK},
    and {DAY} are replaced everywhere.
    """
    from generic_utils.task_dag import substitute

    mapping = {"PACK": pack, "PLAYBOOK": f"{pack}/playbook", "DAY": day}
    filled = substitute(json.loads(json.dumps(template)), mapping)
    tone_map = filled.pop("tone_map", {})
    spec: dict[str, Any] = {k: v for k, v in filled.items()
                            if k not in {"tabs", "page_key", "sources_extra", "requires", "column_groups_from"}}
    groups_from = filled.get("column_groups_from")
    if groups_from and "column_groups" not in spec:
        # One owner for the column order: a recipe's `column_groups` labels the page's tables.
        path = Path(groups_from) if Path(groups_from).is_absolute() else REPO_ROOT / groups_from
        spec["column_groups"] = json.loads(path.read_text(encoding="utf-8")).get("column_groups")
    # Sources always come from the pack being rendered; templates only add static lines.
    spec["sources"] = pack_sources(pack) + list(filled.get("sources_extra") or [])
    page_key = filled.get("page_key") or ""
    if judgment is None:
        spec["name"] = f"{filled['name']}-manual"
        spec["subtitle"] = "Data-only page (no agent judgment). " + str(filled.get("subtitle") or "")
    else:
        for field in ("title", "subtitle"):
            value = _lookup(judgment, f"pages.{page_key}.{field}") if page_key else None
            if value:
                spec[field] = value
    tabs = []
    for tab in filled.get("tabs") or []:
        # A tab whose `requires` path is missing (an on-demand scan not run on this pack) is left out.
        needed = tab.get("requires")
        if needed and not all(Path(p).exists() or (REPO_ROOT / p).exists() for p in ([needed] if isinstance(needed, str) else needed)):
            continue
        blocks = [b for b in (_fill_block(b, judgment, tone_map) for b in tab.get("blocks") or []) if b is not _DROP]
        if blocks:
            tabs.append({"label": tab["label"], "blocks": blocks})
    spec["tabs"] = tabs
    if judgment is not None:
        spec["id_warnings"] = bare_course_mentions(tabs, judgment)
    ledger = dict(filled.get("ledger") or {"auto": True})
    extra_entries = _lookup(judgment, f"pages.{page_key}.ledger_entries") if (judgment and page_key) else None
    if extra_entries:
        ledger["entries"] = list(ledger.get("entries") or []) + [{"group": "Page", "rows": extra_entries}]
    spec["ledger"] = ledger
    return spec


def write_template_page(
    template: str | Path,
    *,
    pack: str,
    day: str,
    judgment_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render a template to <out_dir>/<name>.json and .html.

    Default out dir: the open run ($CANVAS_RUN_DIR), else logs/canvases/<day>/ (canvas_runs).
    A run folder also gets judgment.json: the judgment these pages were rendered from.
    A template's `requires` path (placeholders allowed) that is missing on this pack skips
    the page: the result has html=None and `skipped` says which suite folder to run.
    """
    from generic_utils.canvas_runs import default_out_dir, is_run_dir, snapshot
    from generic_utils.task_dag import substitute

    template_spec = load_template(template)
    needed = template_spec.get("requires")
    for item in ([needed] if isinstance(needed, str) else list(needed or [])):
        path = Path(substitute(str(item), {"PACK": pack, "PLAYBOOK": f"{pack}/playbook", "DAY": day}))
        if not (path.exists() or (REPO_ROOT / path).exists()):
            label = substitute(str(template_spec.get("name") or template), {"DAY": day})
            return {"html": None, "skipped": f"{label}: {path.as_posix()} is missing on this pack", "warnings": []}
    judgment = json.loads(Path(judgment_path).read_text(encoding="utf-8")) if judgment_path else None
    spec = render_template(template_spec, pack=pack, day=day, judgment=judgment)
    directory = Path(out_dir) if out_dir else default_out_dir(day)
    directory.mkdir(parents=True, exist_ok=True)
    if judgment_path and is_run_dir(directory):
        snapshot(judgment_path, directory, "judgment.json")
    spec_path = directory / f"{spec['name']}.json"
    spec_path.write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    return write_page(spec_path, ledger_path=ledger_path)


def write_page(
    spec_path: str | Path,
    *,
    out_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    index: bool = True,
) -> dict[str, Any]:
    spec_file = Path(spec_path)
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    page = build_page(spec, base_dir=spec_file.parent, ledger_path=ledger_path)
    out = Path(out_path) if out_path else spec_file.with_name(f"{page['name']}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(page), encoding="utf-8")
    payload = {
        "html": str(out),
        "tabs": [t["label"] for t in page["tabs"]] + (["Guide"] if page.get("guide") else []) + ["Ledger"],
        "tables": page["tables"],
        "ledger_rows": sum(len(g["rows"]) for g in page["ledger"]),
        "warnings": page["warnings"],
    }
    if index:
        from generic_utils.canvas_runs import canvas_root, write_index as write_run_index

        root = canvas_root().resolve()
        inside = out.parent.resolve() == root or root in out.parent.resolve().parents
        payload["index"] = str(write_run_index(root) if inside else write_index(out.parent))
    return payload
