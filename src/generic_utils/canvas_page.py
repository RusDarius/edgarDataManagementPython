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
         "tone_field": "Course", "tone_map": {"ADD": "good", "EXIT": "bad"}, "decimals": 2}
      ]}],
      "ledger": {"auto": true, "standard": ["rr", "left"], "entries": [{"group": "Page", "rows": [["Δbo", "..."]]}]}
    }
"""

from __future__ import annotations

import html
import json
import re
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
        return rows_from_duckdb(_resolve(source["duckdb"], base_dir), source["sql"])
    if source.get("pack"):
        from generic_utils.focus import pack_path_rows

        pack = json.loads(_resolve(source["pack"], base_dir).read_text(encoding="utf-8"))
        return pack_path_rows(pack, source.get("key") or "names")
    raise ValueError(f"Unknown table source: {sorted(source)}")


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


def _left_carrier(row: Mapping[str, Any]) -> str:
    from generic_utils.derive import leftover_carrier

    return leftover_carrier(row.get("street_left"), row.get("left"))


# Columns a table can add with "derive": [...]. Each reuses a generic_utils formula.
DERIVED_COLUMNS = {"left_carrier": _left_carrier}


def table_rows(block: Mapping[str, Any], base_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Raw rows of a sourced table after where / sort / limit / derive: (rows, where clauses, n loaded)."""
    from generic_utils.predicates import filter_rows

    loaded = _source_rows(block["source"], base_dir)
    kept, clauses = filter_rows(loaded, block.get("where"))
    ordered = _sort_rows([dict(r) for r in kept], block.get("sort"))
    ids = block.get("ids")
    if ids:
        # A judgment-chosen name list (`ids_from`): keep those rows, in the judgment's order.
        id_field = block.get("id_field") or "symbol"
        by_id = {str(r.get(id_field)): r for r in ordered}
        ordered = [by_id[str(i)] for i in ids if str(i) in by_id]
    if block.get("limit"):
        ordered = ordered[: int(block["limit"])]
    for name in block.get("derive") or []:
        if name not in DERIVED_COLUMNS:
            raise ValueError(f"Unknown derive column {name!r}; known: {sorted(DERIVED_COLUMNS)}")
        for row in ordered:
            row[name] = DERIVED_COLUMNS[name](row)
    return ordered, clauses, len(loaded)


def resolve_table(block: Mapping[str, Any], base_dir: Path) -> dict[str, Any]:
    """Return {title, headers, rows, tones, spec} with every cell as a string."""
    if "source" not in block:
        headers = [str(h) for h in block.get("headers") or []]
        rows = [[_fmt(c, None) for c in row] for row in block.get("rows") or []]
        tones = list(block.get("tones") or [])
        return {"title": block.get("title"), "headers": headers, "rows": rows, "tones": tones,
                "note": block.get("note"), "spec": {"literal": True, "n": len(rows)}}

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
    rows = [[_fmt(row.get(f), decimals) for f in fields] for row in ordered]
    tones = [tone_map.get(str(row.get(tone_field))) if tone_field else None for row in ordered]
    spec = {"source": dict(block["source"]), "n_loaded": n_loaded, "where": clauses,
            "sort": block.get("sort"), "limit": limit, "n": len(rows)}
    return {"title": block.get("title"), "headers": headers, "rows": rows, "tones": tones,
            "note": block.get("note"), "spec": spec}


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
    for tab in spec.get("tabs") or []:
        label = str(tab.get("label") or "Tab")
        if _norm(label) == "ledger":
            raise ValueError("Do not add a Ledger tab. Put entries under spec.ledger; the renderer adds the one Ledger tab.")
        blocks: list[dict[str, Any]] = []
        for block in tab.get("blocks") or []:
            kind = block.get("type")
            if kind == "table":
                bare_extra_keys.update(str(k) for k in (block.get("extra") or {}) if ":" not in str(k))
                try:
                    table = resolve_table(block, base_dir)
                except FileNotFoundError as exc:
                    # A source this pack did not produce: say so on the page instead of failing the render.
                    # An `optional` table (a scan that only exists on some days) is noted without a warning.
                    if not block.get("optional"):
                        warnings.append(f"table {block.get('title')!r}: {exc}")
                    blocks.append({"type": "text", "tone": "secondary", "text": f"{block.get('title') or 'Table'}: source not produced on this pack ({exc})."})
                    continue
                tables_seen.append(table)
                blocks.append({"type": "table", **table})
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

    def explain(token: str) -> bool:
        norm = _norm(token)
        entry = page_index.get(norm) or std_index.get(norm)
        if entry is None:
            stem = re.sub(r"_(nav|usd|pct|px)$", "", norm)
            entry = page_index.get(stem) or std_index.get(stem)
        if entry is None and re.search(r"_(vs_ind|vs_avg|ind_med|ind_avg)$", norm):
            entry = std_index.get("field_vs_ind")
        if entry is None:
            return False
        chosen.setdefault(f"{entry['group']}|{entry['key']}", entry)
        return True

    for key in ledger_spec.get("standard") or []:
        if not explain(key):
            warnings.append(f"ledger.standard key {key!r} is not in {DEFAULT_LEDGER_PATH.name}")
    for entry in page_entries:
        chosen.setdefault(f"{entry['group']}|{entry['key']}", entry)

    if ledger_spec.get("auto", True):
        for table in tables_seen:
            for header in table["headers"]:
                if _norm(header) not in plain and not explain(header):
                    warnings.append(f"header {header!r} ({table.get('title') or 'table'}) has no Ledger row")
            # Letter-coded fields (Mesh): a cell is explained by the field row plus one row per letter.
            for i, h in enumerate(table["headers"]):
                allowed = letter_coded.get(_norm(h))
                if not allowed:
                    continue
                for cell in {row[i] for row in table["rows"] if i < len(row)}:
                    for ch in cell.strip("-"):
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
        "warnings": list(dict.fromkeys(warnings)),
        "tables": [t["spec"] | {"title": t.get("title")} for t in tables_seen],
    }


# ---------------------------------------------------------------- render


def _inline(text: Any) -> str:
    escaped = html.escape(str(text or ""))
    escaped = _INLINE_BOLD.sub(r"<strong>\1</strong>", escaped)
    return _INLINE_CODE.sub(r"<code>\1</code>", escaped)


def _tone(value: Any) -> str:
    return value if value in TONES else "neutral"


_NUMERIC_CELL = re.compile(r"^[−\-+$]?[\d,]*\.?\d+%?$")


def _render_table(block: Mapping[str, Any], idx: str) -> str:
    out = ['<section class="tbl">']
    if block.get("title"):
        out.append(f'<h3>{_inline(block["title"])} <span class="count">{len(block["rows"])} rows</span></h3>')
    if len(block["rows"]) > 10:
        out.append(f'<input class="filter" type="search" placeholder="Filter rows" data-table="{idx}">')
    out.append(f'<div class="scroll"><table id="{idx}"><thead><tr>')
    out.extend(f'<th data-col="{i}">{html.escape(h)}</th>' for i, h in enumerate(block["headers"]))
    out.append("</tr></thead><tbody>")
    tones = block.get("tones") or []
    for r, row in enumerate(block["rows"]):
        tone = _tone(tones[r] if r < len(tones) else None)
        cells = "".join(
            f'<td class="num">{html.escape(c)}</td>' if _NUMERIC_CELL.match(c) else f"<td>{_inline(c)}</td>"
            for c in row
        )
        out.append(f'<tr class="t-{tone}">{cells}</tr>')
    out.append("</tbody></table></div>")
    if block.get("note"):
        out.append(f'<p class="secondary">{_inline(block["note"])}</p>')
    out.append("</section>")
    return "".join(out)


def _render_block(block: Mapping[str, Any], idx: str) -> str:
    kind = block["type"]
    if kind == "table":
        return _render_table(block, idx)
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
header,main{max-width:1600px;margin:0 auto;padding:16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:20px 0 8px}h3{font-size:14px;margin:16px 0 6px}
.secondary,.count,.sources{color:var(--muted)}.count{font-weight:400;font-size:12px}.sources{font-size:12px;margin-top:6px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}nav button{border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:999px;padding:5px 12px;cursor:pointer;font:inherit}
nav button.on{background:var(--accent);border-color:var(--accent);color:var(--panel)}section.tab{display:none}section.tab.on{display:block}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--panel);max-height:75vh}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{padding:4px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
td:last-child{white-space:normal;min-width:220px}th{position:sticky;top:0;background:var(--panel);cursor:pointer;user-select:none}
tbody tr:nth-child(even){background:var(--zebra)}td.num{text-align:right;font-variant-numeric:tabular-nums}
tr.t-good td:first-child{box-shadow:inset 3px 0 var(--good)}tr.t-bad td:first-child{box-shadow:inset 3px 0 var(--bad)}
tr.t-warning td:first-child{box-shadow:inset 3px 0 var(--warning)}tr.t-info td:first-child{box-shadow:inset 3px 0 var(--info)}
.callout{border-left:4px solid var(--line);background:var(--panel);padding:8px 12px;border-radius:4px;margin:10px 0}.callout p{margin:4px 0 0}
.callout.t-good{border-color:var(--good);background:var(--good-bg)}.callout.t-bad{border-color:var(--bad);background:var(--bad-bg)}
.callout.t-warning{border-color:var(--warning);background:var(--warning-bg)}.callout.t-info{border-color:var(--info);background:var(--info-bg)}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:10px 0}.stat{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 10px}
.stat .v{font-size:18px;font-weight:600}.stat .l{font-size:12px;color:var(--muted)}.stat.t-good .v{color:var(--good)}.stat.t-bad .v{color:var(--bad)}.stat.t-warning .v{color:var(--warning)}.stat.t-info .v{color:var(--info)}
.filter{margin:0 0 6px;padding:4px 8px;border:1px solid var(--line);border-radius:4px;background:var(--panel);color:var(--fg);width:min(320px,100%)}
code{font-size:12px;background:var(--zebra);padding:0 3px;border-radius:3px}.warn{color:var(--warning);font-size:12px}
"""

_JS = """
const tabs=[...document.querySelectorAll('section.tab')],btns=[...document.querySelectorAll('nav button')];
function show(id){tabs.forEach(t=>t.classList.toggle('on',t.id===id));btns.forEach(b=>b.classList.toggle('on',b.dataset.tab===id));history.replaceState(null,'','#'+id);}
btns.forEach(b=>b.onclick=()=>show(b.dataset.tab));show((location.hash||'').slice(1)&&document.getElementById(location.hash.slice(1))?location.hash.slice(1):tabs[0].id);
document.querySelectorAll('input.filter').forEach(inp=>inp.oninput=()=>{const q=inp.value.toLowerCase();
document.getElementById(inp.dataset.table).querySelectorAll('tbody tr').forEach(tr=>tr.style.display=tr.textContent.toLowerCase().includes(q)?'':'none');});
const num=s=>{const v=parseFloat(s.replace(/[$,%+]/g,'').replace('−','-'));return isNaN(v)?null:v;};
document.querySelectorAll('th').forEach(th=>th.onclick=()=>{const tb=th.closest('table').tBodies[0],i=+th.dataset.col,asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
const rows=[...tb.rows];rows.sort((a,b)=>{const x=a.cells[i].textContent,y=b.cells[i].textContent,nx=num(x),ny=num(y);
if(nx!==null&&ny!==null)return asc?nx-ny:ny-nx;if(nx===null&&ny!==null)return 1;if(ny===null&&nx!==null)return -1;return asc?x.localeCompare(y):y.localeCompare(x);});rows.forEach(r=>tb.appendChild(r));});
"""


def render_html(page: Mapping[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tabs = list(page["tabs"]) + [{"label": "Ledger", "blocks": [
        {"type": "table", "title": group["group"], "headers": ["Field or value", "Meaning"], "rows": group["rows"], "tones": []}
        for group in page["ledger"]
    ]}]
    nav, body = [], []
    for t, tab in enumerate(tabs):
        tab_id = _norm(tab["label"]) or f"tab{t}"
        nav.append(f'<button data-tab="{tab_id}">{html.escape(tab["label"])}</button>')
        inner = "".join(_render_block(b, f"t{t}b{i}") for i, b in enumerate(tab["blocks"]))
        body.append(f'<section class="tab" id="{tab_id}">{inner}</section>')
    sources = " · ".join(_inline(s) for s in page.get("sources") or [])
    warn = (f'<p class="warn">{len(page["warnings"])} ledger warnings at render time.</p>' if page.get("warnings") else "")
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(page['title'])}</title><style>{_CSS}</style></head><body>"
        f"<header><h1>{_inline(page['title'])}</h1>"
        + (f'<div class="secondary">{_inline(page["subtitle"])}</div>' if page.get("subtitle") else "")
        + (f'<div class="sources">Sources: {sources}</div>' if sources else "")
        + f'<div class="sources">Rendered {stamp} · {html.escape(page["name"])}</div>{warn}'
        f"<nav>{''.join(nav)}</nav></header><main>{''.join(body)}</main><script>{_JS}</script></body></html>"
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
    return texts


def bare_course_mentions(tabs: Sequence[Mapping[str, Any]], judgment: Mapping[str, Any]) -> list[str]:
    """Warnings for page text that names a judged stock by its bare ticker (the judgment knows its exchange)."""
    from generic_utils.symbol_identity import qualify_text, ticker_map

    by_ticker = ticker_map((judgment.get("courses") or {}).keys())
    found: dict[str, set[str]] = {}
    for tab in tabs:
        for block in tab.get("blocks") or []:
            for text in _block_texts(block):
                for token in qualify_text(text, by_ticker)[1]:
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
    spec: dict[str, Any] = {k: v for k, v in filled.items() if k not in {"tabs", "page_key", "sources_extra", "requires"}}
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
        "tabs": [t["label"] for t in page["tabs"]] + ["Ledger"],
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
