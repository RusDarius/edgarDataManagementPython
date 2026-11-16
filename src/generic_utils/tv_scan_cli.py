"""Generic TradingView dataset interrogation CLI.

Replaces ad-hoc agent SQL / `_tmp_*.py`. No eligibility cutoffs.
Subcommands: recipes, inventory, summary, group, histogram, counts,
overlap, fetch, named, history, span (--include-steps for day-to-day),
weight-group, join, derive, focus, pack-focus, movers, setup, forward,
risk, capture, capture-replay, session, briefs, entry-book, timing-levels,
payoff-focus, book-cycle, book-sessions, export, mesh, capital-plan, page (operator HTML page).
Rank-only (no --where): rank_cli.py.
All-suite manual runner: src/run_operator_suites.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str)


def _print_payload(payload: dict[str, Any], *, include_rows: bool) -> None:
    printed = dict(payload)
    if not include_rows and "rows" in printed:
        n = len(printed.get("rows") or [])
        printed["rows"] = []
        spec = dict(printed.get("spec") or {})
        spec.setdefault("n", n)
        printed["spec"] = spec
    print(_dumps(printed))


def _qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _load_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_csv, rows_from_duckdb

    if args.csv and getattr(args, "db", None):
        raise SystemExit("Pass either --csv or --db, not both.")
    if args.csv:
        return rows_from_csv(args.csv)
    if getattr(args, "db", None):
        sql = getattr(args, "sql", None)
        if not sql:
            raise SystemExit("--db requires --sql (or use the fetch command).")
        return rows_from_duckdb(args.db, sql)
    raise SystemExit("Pass --csv or --db.")


def _load_pack_or_source_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    pack_path = getattr(args, "pack", None)
    if pack_path:
        pack = json.loads(Path(pack_path).read_text(encoding="utf-8"))
        sleeve = getattr(args, "sleeve", "names") or "names"
        if sleeve == "us2b":
            from generic_utils.focus import pack_us2b_rows

            rows = pack_us2b_rows(pack, pack_path=pack_path)
            if rows:
                return rows
            sleeve = "names"
        if sleeve == "book":
            return list(pack.get("book") or [])
        names = pack.get("names") or {}
        return list(names.values()) if isinstance(names, dict) else list(names)
    return _load_rows(args)


def _peer_rows_for_setup(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    recipe: dict[str, Any],
) -> list[dict[str, Any]] | None:
    from generic_utils.ranking import to_float
    from generic_utils.scan_sources import rows_from_csv

    if getattr(args, "peer_csv", None):
        return rows_from_csv(args.peer_csv)
    floor = getattr(args, "peer_mcap", None)
    if floor is None:
        floor = recipe.get("peer_mcap_min")
    if floor:
        return [r for r in rows if (to_float(r.get("mcap")) or 0) >= float(floor)]
    return None


def _apply_cover(
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
    *,
    clamp: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from generic_utils.ranking import cover_rows

    cover = getattr(args, "cover", None)
    rank_field = getattr(args, "rank_field", None) or "left"
    spec = {"cover": cover, "rank_field": rank_field, "source_rows": len(rows)}
    if cover is None:
        return rows, spec
    size = int(cover)
    if clamp:
        from generic_utils.forward_value import clamp_cover

        size = clamp_cover(size)
    spec["cover"] = size
    return list(cover_rows(rows, size, field=rank_field)), spec


def _cmd_recipes(_args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.recipes import list_recipes

    payload = {"recipes": list_recipes()}
    print(_dumps(payload))
    return payload


def _cmd_inventory(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.scan_sources import rows_from_duckdb

    tables = rows_from_duckdb(args.db, "SHOW TABLES")
    names = []
    for row in tables:
        name = row.get("name") or row.get("table_name")
        if name:
            names.append(str(name))
    columns: dict[str, Any] = {}
    for table in names:
        quoted = _qident(table)
        info = rows_from_duckdb(args.db, f"PRAGMA table_info({quoted})")
        col_names = [str(r.get("name")) for r in info if r.get("name")]
        if len(col_names) > 40:
            columns[table] = {
                "count": len(col_names),
                "preview": col_names[:24],
                "note": "Wide table; preview only. Never SELECT *.",
            }
        else:
            columns[table] = {"count": len(col_names), "names": col_names}
    payload = {"db": str(args.db), "tables": names, "columns": columns}
    print(_dumps(payload))
    return payload


def _cmd_summary(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import numeric_summary

    rows = _load_rows(args)
    fields = [item.strip() for item in args.fields.split(",") if item.strip()]
    payload = {
        "spec": {"fields": fields, "source_rows": len(rows)},
        "summary": numeric_summary(rows, fields),
    }
    print(_dumps(payload))
    return payload


def _cmd_group(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import group_stats

    rows = _load_rows(args)
    metrics = [item.strip() for item in args.metrics.split(",") if item.strip()]
    grouped = group_stats(
        rows,
        group_field=args.by,
        metrics=metrics,
        min_n=args.min_n,
        pct_positive_field=args.pct_positive,
        digits=args.digits,
        center=args.center or "mean",
    )
    payload = {
        "spec": {
            "group_field": args.by,
            "metrics": metrics,
            "min_n": args.min_n,
            "pct_positive_field": args.pct_positive,
            "source_rows": len(rows),
            "groups": len(grouped),
        },
        "rows": grouped,
    }
    print(_dumps(payload))
    return payload


def _cmd_histogram(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import histogram

    rows = _load_rows(args)
    bins = [float(item.strip()) for item in args.bins.split(",") if item.strip()]
    payload = {
        "spec": {"field": args.field, "bins": bins, "source_rows": len(rows)},
        "buckets": histogram(rows, field=args.field, bins=bins),
    }
    print(_dumps(payload))
    return payload


def _cmd_counts(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import value_counts

    rows = _load_rows(args)
    payload = {
        "spec": {"field": args.field, "top": args.top, "source_rows": len(rows)},
        "rows": value_counts(rows, args.field, top=args.top),
    }
    print(_dumps(payload))
    return payload


def _cmd_overlap(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import ids_of, overlap
    from generic_utils.scan_sources import rows_from_csv

    rows_a = rows_from_csv(args.csv_a)
    rows_b = rows_from_csv(args.csv_b)
    payload = {
        "spec": {
            "csv_a": args.csv_a,
            "csv_b": args.csv_b,
            "id_field": args.id_field,
        },
        "overlap": overlap(
            ids_of(rows_a, args.id_field),
            ids_of(rows_b, args.id_field),
            list_limit=args.list_limit,
        ),
    }
    print(_dumps(payload))
    return payload


def _cmd_fetch(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.recipes import render_recipe
    from generic_utils.scan_sources import rows_from_duckdb

    values: dict[str, Any] = {}
    if args.run_id:
        values["run_id"] = args.run_id
    if args.limit is not None:
        values["limit"] = args.limit
    sql, params = render_recipe(args.recipe, **values)
    rows = rows_from_duckdb(args.db, sql, params)
    payload = {
        "spec": {"recipe": args.recipe, "params": values, "n": len(rows)},
        "rows": rows[: args.max_rows],
    }
    if args.out:
        from generic_utils.scan_sources import rows_to_csv

        path = rows_to_csv(args.out, rows)
        payload["out"] = path.as_posix()
    _print_payload(payload, include_rows=not args.out)
    return payload


def _cmd_focus(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.focus import build_focus, write_focus

    rows = _load_rows(args)
    exempt = set(args.exempt.split(",")) if args.exempt else None
    payload = build_focus(
        rows,
        where=args.where,
        field=args.field,
        n=args.top,
        reverse=not args.asc,
        tie_breaker=args.tie_breaker,
        group_field=args.group_field,
        cap=args.cap,
        exempt=exempt,
        id_field=args.id_field,
    )
    if args.out:
        path = write_focus(payload, args.out)
        payload["out"] = path.as_posix()
    _print_payload(payload, include_rows=not args.out)
    return payload


def _cmd_named(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.series import alias_fields, parse_csv_list, parse_equals, parse_rename, select_named_rows
    from generic_utils.scan_sources import rows_from_csv, rows_to_csv

    columns = parse_csv_list(args.fields)
    if args.csv:
        fetched = select_named_rows(rows_from_csv(args.csv), columns=columns, id_field=args.id_field,
                                    ids=parse_csv_list(args.ids), filter_field=args.filter_field,
                                    filter_values=parse_csv_list(args.filter_values), equals=parse_equals(args.equals),
                                    source=args.csv)
    elif not (args.db and args.table):
        raise SystemExit("named needs --csv, or --db and --table")
    else:
        fetched = _fetch_named_db(args, columns)
    rows = alias_fields(fetched["rows"], parse_rename(args.rename))
    payload: dict[str, Any] = {"spec": fetched["spec"], "rows": rows}
    if args.out:
        path = rows_to_csv(args.out, rows)
        payload["out"] = path.as_posix()
    if args.line:
        print(_row_lines(rows, columns, id_field=args.id_field))
        missing = (fetched["spec"] or {}).get("missing_ids")
        if missing:
            print(f"missing: {', '.join(map(str, missing))}")
    elif args.quiet:
        print(_one_line(payload))
    else:
        _print_payload(payload, include_rows=not args.out or args.include_rows)
    return payload


def _fmt_cell(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1e6 else f"{value:.3e}"
    text = str(value)
    try:
        number = float(text)
    except ValueError:
        return text
    return f"{number:.4g}" if abs(number) < 1e6 else f"{number:.3e}"


def _row_lines(rows: list[dict[str, Any]], columns: list[str], *, id_field: str | None = None) -> str:
    """One compact line per row: the id, then field=value for every non-empty field (about 200 bytes a row)."""
    key = id_field or "symbol"
    out = []
    for row in rows:
        parts = [str(row.get(key) or "")]
        parts += [f"{c}={_fmt_cell(row[c])}" for c in columns if c != key and row.get(c) not in (None, "")]
        out.append(" ".join(parts))
    return "\n".join(out)


def _one_line(payload: dict[str, Any]) -> str:
    """n, missing ids / columns and the out path: what a caller needs after writing a CSV."""
    spec = payload.get("spec") or {}
    bits = [f"n={spec.get('n', len(payload.get('rows') or []))}"]
    for key in ("missing_ids", "missing_columns"):
        if spec.get(key):
            bits.append(f"{key}={','.join(map(str, spec[key]))}")
    if payload.get("out"):
        bits.append(f"out={payload['out']}")
    return " ".join(bits)


def _fetch_named_db(args: argparse.Namespace, columns: list[str]) -> dict[str, Any]:
    from generic_utils.series import fetch_named, parse_csv_list, parse_equals

    return fetch_named(
        args.db,
        table=args.table,
        columns=columns,
        run_id=args.run_id,
        id_field=args.id_field,
        ids=parse_csv_list(args.ids),
        filter_field=args.filter_field,
        filter_values=parse_csv_list(args.filter_values),
        equals=parse_equals(args.equals),
    )


def _cmd_history(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.scan_sources import rows_to_csv
    from generic_utils.series import (
        alias_fields,
        field_history,
        group_history,
        list_dated_files,
        parse_csv_list,
        parse_equals,
        parse_rename,
        series_span,
    )

    sources = list_dated_files(args.root, args.glob, newest=args.newest)
    columns = parse_csv_list(args.fields)
    if args.id_field and args.id_field not in columns:
        columns = [args.id_field, *columns]
    if args.filter_field and args.filter_field not in columns:
        columns = [args.filter_field, *columns]
    history = field_history(
        sources,
        table=args.table,
        columns=columns,
        id_field=args.id_field,
        ids=parse_csv_list(args.ids),
        filter_field=args.filter_field,
        filter_values=parse_csv_list(args.filter_values),
        equals=parse_equals(args.equals),
        run_prefix=args.run_prefix,
        all_runs=args.all_runs,
        recipe=args.recipe,
    )
    rename = parse_rename(args.rename)
    rows = alias_fields(history["rows"], rename)
    grouped = None
    if args.group_field:
        raw_metrics = parse_csv_list(args.metrics) or parse_csv_list(args.fields)
        metrics = [rename.get(m, m) for m in raw_metrics]
        grouped = group_history(
            rows,
            group_field=args.group_field,
            metrics=metrics,
            min_n=args.min_n,
            pct_positive_field=rename.get(args.pct_positive, args.pct_positive)
            if args.pct_positive
            else None,
        )
    span_rows = None
    if args.span_id_field:
        raw_span = parse_csv_list(args.span_fields) or parse_csv_list(args.fields)
        span_fields = [rename.get(m, m) for m in raw_span]
        span_rows = series_span(rows, id_field=args.span_id_field, fields=span_fields)
    result_rows = grouped if grouped is not None else rows
    spec = dict(history["spec"])
    spec["emitted_n"] = len(result_rows)
    payload: dict[str, Any] = {
        "spec": spec,
        "snapshots": history["snapshots"],
        "rows": result_rows if args.include_rows else [],
    }
    if span_rows is not None:
        payload["span"] = span_rows
    if args.out:
        path = rows_to_csv(args.out, result_rows)
        payload["out"] = path.as_posix()
    if args.quiet:
        skipped = sum(1 for snap in history["snapshots"] if not snap.get("run_id"))
        print(f"n={spec['emitted_n']} files={spec.get('n_sources')} skipped_files={skipped}" + (f" out={payload['out']}" if payload.get("out") else ""))
    else:
        print(_dumps(payload))
    return payload


def _cmd_span(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.scan_sources import rows_from_csv, rows_to_csv
    from generic_utils.series import parse_csv_list, series_span

    rows = rows_from_csv(args.csv)
    fields = parse_csv_list(args.fields)
    span_rows = series_span(
        rows,
        id_field=args.id_field,
        fields=fields,
        as_of_field=args.as_of,
        normalize=args.normalize,
        include_steps=bool(getattr(args, "include_steps", False)),
    )
    payload: dict[str, Any] = {
        "spec": {
            "csv": args.csv,
            "id_field": args.id_field,
            "fields": fields,
            "n": len(span_rows),
        },
        "rows": span_rows,
    }
    if args.out:
        path = rows_to_csv(args.out, span_rows)
        payload["out"] = path.as_posix()
    print(_dumps(payload))
    return payload


def _cmd_weight_group(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.aggregations import weighted_group_stats
    from generic_utils.series import parse_csv_list

    rows = _load_rows(args)
    metrics = parse_csv_list(args.metrics)
    grouped = weighted_group_stats(
        rows,
        group_field=args.by,
        weight_field=args.weight,
        metrics=metrics,
        min_n=args.min_n,
        digits=args.digits,
    )
    payload = {
        "spec": {
            "group_field": args.by,
            "weight_field": args.weight,
            "metrics": metrics,
            "source_rows": len(rows),
            "groups": len(grouped),
        },
        "rows": grouped,
    }
    print(_dumps(payload))
    return payload


def _cmd_join(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.scan_sources import rows_from_csv, rows_to_csv
    from generic_utils.series import join_rows

    left = rows_from_csv(args.csv_a)
    right = rows_from_csv(args.csv_b)
    rows = join_rows(
        left,
        right,
        left_on=args.left_on,
        right_on=args.right_on,
        how=args.how,
        prefix=args.prefix or "",
    )
    payload: dict[str, Any] = {
        "spec": {
            "csv_a": args.csv_a,
            "csv_b": args.csv_b,
            "left_on": args.left_on,
            "right_on": args.right_on,
            "how": args.how,
            "n": len(rows),
        },
        "rows": rows if args.include_rows else [],
    }
    if args.out:
        path = rows_to_csv(args.out, rows)
        payload["out"] = path.as_posix()
    print(_dumps(payload))
    return payload


def _cmd_derive(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.derive import apply_derived, attach_vs_group
    from generic_utils.scan_sources import rows_from_csv, rows_to_csv
    from generic_utils.series import parse_csv_list

    rows = apply_derived(
        rows_from_csv(args.csv),
        close_field=args.close,
        pt_field=args.pt,
        edge_field=args.edge,
        lo52_field=args.lo52,
        hi52_field=args.hi52,
        sma50_field=args.sma50,
        leftover_max=args.leftover_max,
        refresh=args.refresh,
    )
    vs_fields = parse_csv_list(args.vs_fields) if args.vs_fields else []
    if args.vs_by:
        if not vs_fields:
            raise SystemExit("derive --vs-by requires --vs-fields")
        rows = attach_vs_group(
            rows,
            group_field=args.vs_by,
            fields=vs_fields,
            min_n=args.vs_min_n,
        )
    payload: dict[str, Any] = {
        "spec": {
            "csv": args.csv,
            "close": args.close,
            "pt": args.pt,
            "refresh": args.refresh,
            "vs_by": args.vs_by,
            "vs_fields": vs_fields,
            "n": len(rows),
        },
        "rows": rows if args.include_rows else [],
    }
    if args.out:
        path = rows_to_csv(args.out, rows)
        payload["out"] = path.as_posix()
    print(_dumps(payload))
    return payload


def _movers_cli_size_kwargs(
    args: argparse.Namespace, recipe: dict[str, Any]
) -> dict[str, Any]:
    """CLI sizes override recipe. Bare --top stays per-tail (both equal)."""
    from generic_utils.ranking import movers_size_kwargs_from_recipe

    if args.leaders is not None or args.laggards is not None:
        if args.leaders is None or args.laggards is None:
            raise SystemExit("movers --leaders and --laggards must be passed together")
        return {"n_leaders": args.leaders, "n_laggards": args.laggards}
    recipe_split = (
        recipe.get("cover") is not None
        or recipe.get("up_share") is not None
        or recipe.get("n_leaders") is not None
        or recipe.get("n_laggards") is not None
    )
    if (
        args.top is not None
        and args.cover is None
        and args.up_share is None
        and not recipe_split
    ):
        return {"n": args.top}
    cover = args.cover if args.cover is not None else recipe.get("cover")
    up_share = args.up_share if args.up_share is not None else recipe.get("up_share")
    if cover is None and args.top is not None:
        cover = args.top
    if cover is None and up_share is None:
        return movers_size_kwargs_from_recipe(recipe)
    if cover is None:
        cover = recipe.get("cover") or recipe.get("top") or 50
    if up_share is None:
        up_share = 0.75 if recipe.get("up_share") is None else recipe.get("up_share")
    return {"cover": int(cover), "up_share": float(up_share)}


def _cmd_movers(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.derive import enrich_mover_rows, keep_with_vs_group
    from generic_utils.predicates import filter_rows
    from generic_utils.ranking import (
        drop_duplicate_id_suffixes,
        flatten_movers,
        horizon_movers,
        resolve_movers_tail_n,
    )
    from generic_utils.run_export import load_json_recipe, write_run_export
    from generic_utils.scan_sources import rows_to_csv
    from generic_utils.series import parse_csv_list

    recipe: dict[str, Any] = {}
    if args.recipe:
        recipe = load_json_recipe(args.recipe)
    rows = _load_rows(args)
    fields = parse_csv_list(args.fields) or list(recipe.get("fields") or [])
    if not fields:
        raise SystemExit("movers requires --fields or --recipe with fields")
    keep = parse_csv_list(args.keep) if args.keep else list(recipe.get("keep") or [])
    keep = keep_with_vs_group(keep, recipe)
    where = args.where if args.where is not None else recipe.get("where")
    size_kwargs = _movers_cli_size_kwargs(args, recipe)
    n_up, n_down = resolve_movers_tail_n(**size_kwargs)
    group_field = args.group_field if args.group_field else recipe.get("group_field")
    cap = args.cap if args.cap is not None else recipe.get("cap")
    id_field = args.id_field or recipe.get("id_field") or "symbol"
    if group_field and cap is None:
        raise SystemExit("movers --group-field requires --cap")
    filtered, clauses = filter_rows(rows, where)
    if recipe.get("name") in {"movers_day", "movers_3m"}:
        from generic_utils.forward_fundamentals import (
            attach_forward_fundamentals,
            load_projection_context,
        )

        try:
            filtered = attach_forward_fundamentals(
                filtered, context=load_projection_context()
            )
        except Exception:
            pass
    drop_suffixes = recipe.get("drop_duplicate_suffixes", True)
    if getattr(args, "keep_duplicate_suffixes", False):
        drop_suffixes = False
    n_before_suffix = len(filtered)
    if drop_suffixes:
        filtered = drop_duplicate_id_suffixes(filtered, id_field=id_field)
    enriched = enrich_mover_rows(filtered, recipe)
    payload = horizon_movers(
        enriched,
        fields=fields,
        n_leaders=n_up,
        n_laggards=n_down,
        up_share=size_kwargs.get("up_share"),
        group_field=group_field,
        cap=cap,
        id_field=id_field,
        keep=keep,
    )
    try:
        from operator_briefing.sleeves import attach_punished_classes, stamp_movers_payload

        payload = stamp_movers_payload(payload)
        flat = attach_punished_classes(flatten_movers(payload))
    except ImportError:
        flat = flatten_movers(payload)
    spec = dict(payload["spec"])
    spec["recipe"] = recipe.get("name")
    spec["where"] = clauses
    spec["filtered_rows"] = len(filtered)
    spec["drop_duplicate_suffixes"] = drop_suffixes
    spec["dropped_duplicate_suffixes"] = max(0, n_before_suffix - len(filtered))
    spec["vs_group"] = recipe.get("vs_group")
    spec["emitted_n"] = len(flat)
    spec["tails"] = {
        field: {
            "leaders": len(block.get("leaders") or []),
            "laggards": len(block.get("laggards") or []),
            "overflow_leaders_n": block.get("overflow_leaders_n"),
            "overflow_laggards_n": block.get("overflow_laggards_n"),
        }
        for field, block in payload["horizons"].items()
    }
    out_payload: dict[str, Any] = {
        "spec": spec,
        "horizons": payload["horizons"] if args.include_rows else {},
        "rows": flat if args.include_rows else [],
    }
    if args.out:
        path = rows_to_csv(args.out, flat)
        out_payload["out"] = path.as_posix()
    if args.out_dir:
        exported = write_run_export(
            args.out_dir,
            tool="tv_scan_cli.movers",
            tables={"movers_tails": flat},
            sources={"recipe": recipe.get("name"), "csv": args.csv, "db": args.db},
            schema_version="movers_recipe_v1",
            extra={
                "fields": ",".join(fields),
                "cover": n_up + n_down,
                "up_share": size_kwargs.get("up_share")
                if size_kwargs.get("up_share") is not None
                else round(n_up / (n_up + n_down), 4) if (n_up + n_down) else None,
                "n_leaders": n_up,
                "n_laggards": n_down,
            },
            duckdb_name="movers.duckdb",
        )
        out_payload["export"] = exported
    if not getattr(args, "quiet", False):
        print(_dumps(out_payload))
    return out_payload


def _join_finproj_rows(
    args: argparse.Namespace, recipe: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    from generic_utils.forward_value import (
        discover_latest_projection_db,
        load_growth_join_rows,
        load_price_join_rows,
    )

    sources: dict[str, Any] = {}
    if getattr(args, "no_finproj", False):
        return [], [], sources
    price_db = getattr(args, "finproj_db", None)
    growth_db = getattr(args, "fingrowth_db", None)
    if not price_db:
        found = discover_latest_projection_db(prefix="finproj_")
        price_db = str(found) if found else None
    if not growth_db:
        found = discover_latest_projection_db(prefix="fingrowth_")
        growth_db = str(found) if found else None
    price_rows = load_price_join_rows(price_db, recipe)
    growth_rows = load_growth_join_rows(growth_db, recipe)
    sources["finproj_db"] = price_db
    sources["fingrowth_db"] = growth_db
    sources["finproj_n"] = len(price_rows)
    sources["fingrowth_n"] = len(growth_rows)
    return price_rows, growth_rows, sources


def _cmd_setup(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.forward_value import attach_forward_value, load_forward_recipe
    from generic_utils.run_export import write_run_export
    from generic_utils.scan_sources import rows_to_csv
    from generic_utils.setup import attach_setup, load_setup_recipe

    from generic_utils.forward_fundamentals import (
        attach_forward_fundamentals,
        load_projection_context,
    )

    recipe = load_setup_recipe(args.recipe)
    rows = _load_pack_or_source_rows(args)
    if getattr(args, "no_finproj", False):
        rows = attach_forward_fundamentals(rows)
    else:
        rows = attach_forward_fundamentals(
            rows,
            context=load_projection_context(
                growth_db=getattr(args, "fingrowth_db", None),
                price_db=getattr(args, "finproj_db", None),
            ),
        )
    peer = _peer_rows_for_setup(args, rows, recipe)
    out_rows = attach_setup(rows, recipe, peer_rows=peer)
    notes = [
        "generic_utils.setup — primary value / efficiency / tech + street/TV fwd_*; not a 0-100"
    ]
    sources: dict[str, Any] = {
        "recipe": recipe.get("name"),
        "csv": getattr(args, "csv", None),
        "pack": getattr(args, "pack", None),
    }
    if getattr(args, "forward", False):
        fwd_recipe = load_forward_recipe(getattr(args, "forward_recipe", None))
        price_rows, growth_rows, fp_sources = _join_finproj_rows(args, fwd_recipe)
        sources.update(fp_sources)
        out_rows = attach_forward_value(
            out_rows,
            fwd_recipe,
            price_rows=price_rows,
            growth_rows=growth_rows,
            price_run=fp_sources.get("finproj_db"),
            growth_run=fp_sources.get("fingrowth_db"),
        )
        notes.append("forward join: street/TV leftover + financial_projection base/own")
    out_rows, cover_spec = _apply_cover(out_rows, args, clamp=True)
    spec = {
        "recipe": str(args.recipe),
        "n": len(out_rows),
        "peer_n": len(peer) if peer is not None else len(rows),
        "note": "val_field is the multiple to quote. peer NA/THIN means omit the discount story.",
        **cover_spec,
    }
    payload: dict[str, Any] = {
        "spec": spec,
        "rows": out_rows if args.include_rows else [],
    }
    if args.out:
        payload["out"] = rows_to_csv(args.out, out_rows).as_posix()
    if args.out_dir:
        payload["export"] = write_run_export(
            args.out_dir,
            tool="tv_scan_cli.setup",
            tables={"setup": out_rows},
            sources=sources,
            notes=notes,
            extra={"cover": spec.get("cover")},
            duckdb_name="setup.duckdb",
        )
    if not getattr(args, "quiet", False):
        print(_dumps(payload))
    return payload


def _cmd_forward(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.forward_value import (
        build_forward_suite_rows,
        load_forward_recipe,
    )
    from generic_utils.run_export import write_run_export
    from generic_utils.scan_sources import rows_to_csv
    from generic_utils.setup import load_setup_recipe

    recipe = load_forward_recipe(args.recipe)
    setup_recipe = load_setup_recipe(args.setup_recipe)
    rows = _load_pack_or_source_rows(args)
    peer = _peer_rows_for_setup(args, rows, setup_recipe)
    price_rows, growth_rows, fp_sources = _join_finproj_rows(args, recipe)
    out_rows = build_forward_suite_rows(
        rows,
        recipe=recipe,
        setup_recipe=setup_recipe,
        peer_rows=peer,
        price_rows=price_rows,
        growth_rows=growth_rows,
        price_run=fp_sources.get("finproj_db"),
        growth_run=fp_sources.get("fingrowth_db"),
        with_setup=not getattr(args, "no_setup", False),
        load_projection=not getattr(args, "no_finproj", False),
    )
    out_rows, cover_spec = _apply_cover(out_rows, args, clamp=True)
    spec = {
        "recipe": str(args.recipe),
        "setup_recipe": str(args.setup_recipe),
        "n": len(out_rows),
        "peer_n": len(peer) if peer is not None else len(rows),
        "note": (
            "fwd_street_* = analyst PT leftover; fwd_pack_* = max(street, edge); "
            "fp_pe_y1 / fp_evebitda_y1 are the projection forwards. "
            "pe_fwd_tv stays empty when TradingView forward PE is empty. Do not fill with EV/Rev."
        ),
        **cover_spec,
        **{k: v for k, v in fp_sources.items() if k.endswith("_n")},
    }
    payload: dict[str, Any] = {
        "spec": spec,
        "rows": out_rows if args.include_rows else [],
    }
    if args.out:
        payload["out"] = rows_to_csv(args.out, out_rows).as_posix()
    if args.out_dir:
        payload["export"] = write_run_export(
            args.out_dir,
            tool="tv_scan_cli.forward",
            tables={"forward_value": out_rows},
            sources={
                "recipe": recipe.get("name"),
                "csv": getattr(args, "csv", None),
                "pack": getattr(args, "pack", None),
                **fp_sources,
            },
            notes=[
                "generic_utils.forward_value — street/TV leftover + finproj join; not a 0-100",
                "no industry cap; --cover is a size (max 1000)",
            ],
            extra={"cover": spec.get("cover")},
            duckdb_name="forward_value.duckdb",
        )
    if not getattr(args, "quiet", False):
        print(_dumps(payload))
    return payload


def _cmd_risk(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.focus import pack_path_rows
    from generic_utils.risk import (
        attach_book_risk,
        cash_from_holdings_config,
        cash_from_holdings_manifest,
        default_risk_out_dir,
        format_book_risk_md,
        latest_holdings_run,
        load_book_risk_recipe,
        load_holdings_summaries,
        merge_pack_book,
        rows_from_holdings_json,
        slim_position_row,
    )
    from generic_utils.run_export import write_run_export
    from generic_utils.scan_sources import rows_to_csv

    recipe = load_book_risk_recipe(args.recipe)
    holdings_cfg = args.holdings
    notes: list[str] = []
    run_dir = None
    if args.config_only:
        rows = rows_from_holdings_json(holdings_cfg)
        notes.append("config-only: FX invested_sum is native currency; prefer --run for USD marks")
    else:
        if args.summary:
            from generic_utils.scan_sources import rows_from_csv

            rows = rows_from_csv(args.summary)
        else:
            run_arg = args.run
            if run_arg and run_arg != "latest":
                run_dir = Path(run_arg)
            else:
                run_dir = latest_holdings_run()
            if run_dir is None:
                rows = rows_from_holdings_json(holdings_cfg)
                notes.append("no holdings_scoring run found — cost-only from current_holdings.json")
            else:
                rows = load_holdings_summaries(run_dir)
                if not rows:
                    rows = rows_from_holdings_json(holdings_cfg)
                    notes.append("holdings run had empty summaries — cost-only")
    if args.pack:
        pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
        try:
            pack_book = pack_path_rows(pack, "book")
        except (KeyError, TypeError):
            pack_book = []
        if pack_book:
            rows = merge_pack_book(rows, pack_book)
            notes.append(f"joined pack book n={len(pack_book)}")
    cash = args.cash
    if cash is None and run_dir is not None:
        cash = cash_from_holdings_manifest(run_dir)
    if cash is None:
        cash = cash_from_holdings_config(holdings_cfg)
    payload = attach_book_risk(rows, cash_usd=cash, recipe=recipe)
    spec = dict(payload["spec"])
    spec["holdings"] = str(holdings_cfg) if holdings_cfg else None
    spec["run_dir"] = run_dir.as_posix() if run_dir else None
    spec["pack"] = args.pack
    spec["notes"] = notes
    spec["config_only"] = bool(args.config_only)
    positions = [slim_position_row(r) for r in payload["positions"]]
    if not args.out_dir:
        args.out_dir = default_risk_out_dir().as_posix()
        notes.append("defaulted --out-dir")
        spec["notes"] = notes
    out_payload: dict[str, Any] = {
        "spec": spec,
        "capital": payload["capital"],
        "industry": payload["industry"],
        "positions": payload["positions"] if args.include_rows else [],
        "levels": payload["levels"] if args.include_rows else [],
    }
    if args.out:
        path = rows_to_csv(args.out, positions)
        out_payload["out"] = path.as_posix()
    if args.out_dir:
        exported = write_run_export(
            args.out_dir,
            tool="tv_scan_cli.risk",
            tables={
                "book_risk": positions,
                "risk_levels": payload["levels"],
                "industry_exposure": payload["industry"],
                "capital": [payload["capital"]],
            },
            sources={
                "recipe": spec.get("recipe"),
                "holdings": holdings_cfg,
                "run_dir": spec.get("run_dir"),
                "pack": args.pack,
            },
            schema_version="book_risk_v1",
            extra={"n": spec.get("n"), "nav_usd": spec.get("nav_usd")},
            notes=notes,
            duckdb_name="book_risk.duckdb",
        )
        md_path = Path(args.out_dir) / "book_risk.md"
        md_path.write_text(
            format_book_risk_md(
                {
                    "spec": spec,
                    "capital": payload["capital"],
                    "industry": payload["industry"],
                    "positions": positions,
                }
            ),
            encoding="utf-8",
        )
        exported["md"] = md_path.as_posix()
        out_payload["export"] = exported
    if not getattr(args, "quiet", False):
        print(_dumps(out_payload))
    return out_payload


def _cmd_capture(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.capture import (
        build_capture_dump,
        default_capture_out_dir,
        format_capture_md,
        load_capture_recipe,
    )
    from generic_utils.run_export import write_run_export
    from generic_utils.scan_sources import rows_to_csv

    recipe = load_capture_recipe(args.recipe)
    pack_path = args.pack
    if not pack_path:
        try:
            from operator_briefing.discovery import latest_briefing_pack_path

            found = latest_briefing_pack_path()
            pack_path = str(found) if found else None
        except Exception:
            pack_path = None

    payload = build_capture_dump(
        recipe=recipe,
        lookback_days=args.lookback_days,
        af_start=args.af_start,
        af_end=args.af_end,
        pack_path=pack_path,
        holdings_runs_root=args.holdings_runs_root,
        all_fields_root=args.all_fields_root,
        prediction_root=args.prediction_root,
    )
    spec = dict(payload.get("spec") or {})
    summary = dict(payload.get("summary") or {})
    notes = list(payload.get("notes") or [])
    if not args.out_dir:
        args.out_dir = default_capture_out_dir().as_posix()
        notes.append("defaulted --out-dir")
    out_payload: dict[str, Any] = {
        "spec": spec,
        "summary": summary,
        "jobs": payload.get("jobs") if args.include_rows else [],
    }
    if args.out:
        out_payload["out"] = rows_to_csv(
            args.out, [dict(r) for r in (payload.get("jobs") or [])]
        ).as_posix()
    if args.out_dir:
        exported = write_run_export(
            args.out_dir,
            tool="tv_scan_cli.capture",
            tables={
                "book_asof": payload.get("book_asof") or [],
                "book_names": payload.get("book_names") or [],
                "exits": payload.get("exits") or [],
                "fund_path": payload.get("fund_path") or [],
                "fund_span": payload.get("fund_span") or [],
                "score_path": payload.get("score_path") or [],
                "score_span": payload.get("score_span") or [],
                "jobs": payload.get("jobs") or [],
                "coverage_fill_rates": payload.get("coverage_fill_rates") or [],
                "ic_snapshot": payload.get("ic_snapshot") or [],
                "fund_snapshots": payload.get("fund_snapshots") or [],
                "score_snapshots": payload.get("score_snapshots") or [],
            },
            sources={
                "recipe": str(args.recipe),
                "pack": pack_path,
                "holdings_runs_root": args.holdings_runs_root,
                "all_fields_root": args.all_fields_root,
                "prediction_root": args.prediction_root,
            },
            schema_version="capture_v1",
            notes=notes,
            extra={
                "lookback_days": spec.get("lookback_days") or args.lookback_days,
                "window_start": summary.get("window_start"),
                "window_end": summary.get("window_end"),
                "n_symbols": summary.get("n_symbols"),
                "n_exits": summary.get("n_exits"),
            },
            duckdb_name="capture.duckdb",
        )
        md_path = Path(args.out_dir) / "capture.md"
        md_path.write_text(format_capture_md(payload), encoding="utf-8")
        exported["md"] = md_path.as_posix()
        out_payload["export"] = exported
    if not getattr(args, "quiet", False):
        print(_dumps(out_payload))
    return out_payload


def _cmd_capture_replay(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.capture import (
        build_capture_dump,
        build_capture_replay,
        format_capture_replay_md,
        latest_capture_dir,
        load_capture_dump,
        load_capture_recipe,
    )
    from generic_utils.run_export import write_run_export

    recipe = load_capture_recipe(args.recipe)
    capture_dir = Path(args.capture_dir) if args.capture_dir else None
    dump: dict[str, Any] | None = None
    notes: list[str] = []
    rebuild = bool(getattr(args, "rebuild", False) or args.af_start or args.af_end)
    if capture_dir and (capture_dir / "fund_path.csv").exists() and not rebuild:
        dump = load_capture_dump(capture_dir)
        notes.append(f"loaded dump {capture_dir.as_posix()}")
    elif not rebuild:
        found = latest_capture_dir()
        if found is not None:
            capture_dir = found
            dump = load_capture_dump(found)
            notes.append(f"loaded latest dump {found.as_posix()}")
    if dump is None:
        pack_path = args.pack
        if not pack_path:
            try:
                from operator_briefing.discovery import latest_briefing_pack_path

                found_pack = latest_briefing_pack_path()
                pack_path = str(found_pack) if found_pack else None
            except Exception:
                pack_path = None
        dump = build_capture_dump(
            recipe=recipe,
            lookback_days=args.lookback_days,
            af_start=args.af_start,
            af_end=args.af_end,
            pack_path=pack_path,
        )
        notes.append("built capture dump then replayed")
        if capture_dir is None:
            from generic_utils.capture import default_capture_out_dir

            capture_dir = default_capture_out_dir()
    horizons = None
    if args.horizons:
        horizons = [int(item.strip()) for item in str(args.horizons).split(",") if item.strip()]
    payload = build_capture_replay(dump, recipe=recipe, horizons=horizons)
    spec = dict(payload.get("spec") or {})
    summary = dict(payload.get("summary") or {})
    notes.extend(payload.get("notes") or [])
    if not args.out_dir:
        base = capture_dir or latest_capture_dir()
        args.out_dir = ((base / "replay") if base else Path("logs") / "capture_replay").as_posix()
        notes.append("defaulted --out-dir to dump/replay")
    out_payload: dict[str, Any] = {
        "spec": spec,
        "summary": summary,
        "replay_sleeves": payload.get("replay_sleeves") if args.include_rows else [],
        "replay_tune": payload.get("replay_tune") if args.include_rows else [],
    }
    if args.out_dir:
        exported = write_run_export(
            args.out_dir,
            tool="tv_scan_cli.capture_replay",
            tables={
                "replay_sleeves": payload.get("replay_sleeves") or [],
                "replay_tune": payload.get("replay_tune") or [],
                "replay_first_hit": payload.get("replay_first_hit") or [],
                "replay_paths": payload.get("replay_paths") or [],
                "replay_events": payload.get("replay_events") or [],
            },
            sources={
                "recipe": str(args.recipe),
                "capture_dir": spec.get("capture_dir") or (capture_dir.as_posix() if capture_dir else None),
                "pack": args.pack,
            },
            schema_version="capture_replay_v1",
            notes=notes,
            extra={
                "n_events": summary.get("n_events"),
                "n_symbols": summary.get("n_symbols"),
                "horizons": summary.get("horizons"),
                "window_start": summary.get("window_start"),
                "window_end": summary.get("window_end"),
            },
            duckdb_name="capture_replay.duckdb",
        )
        md_path = Path(args.out_dir) / "capture_replay.md"
        md_path.write_text(format_capture_replay_md(payload), encoding="utf-8")
        exported["md"] = md_path.as_posix()
        out_payload["export"] = exported
    if not getattr(args, "quiet", False):
        print(_dumps(out_payload))
    return out_payload


def _cmd_briefs(args: argparse.Namespace) -> dict[str, Any]:
    from datetime import datetime, timezone

    from generic_utils.brief_history import (
        build_brief_history,
        collect_marks,
        format_digest_md,
        load_bands,
        load_sessions,
    )
    from generic_utils.run_export import write_run_export

    project = Path(__file__).resolve().parents[2]
    priors = Path(args.priors) if args.priors else project / "logs" / "AI_ANALYSIS_UTILS" / "priors"
    runs = Path(args.runs) if args.runs else project / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"
    recipe = Path(args.recipe) if args.recipe else project / "config" / "generic_utils" / "brief_history.json"
    recipe_payload: dict[str, Any] = {}
    if recipe.exists():
        loaded = json.loads(recipe.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            recipe_payload = loaded
    max_names = int(args.max_names) if args.max_names is not None else int(recipe_payload.get("max_names") or 40)
    note_chars = int(args.note_chars) if args.note_chars is not None else int(recipe_payload.get("note_chars") or 0)
    sessions = load_sessions(priors)
    marks, latest_day, notes = collect_marks(sessions, runs, args.latest_pack)
    built = build_brief_history(
        sessions,
        marks,
        latest_day=latest_day,
        bands=load_bands(recipe),
        max_names=max_names,
        note_chars=note_chars,
    )
    if not args.out_dir:
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M_utc")
        args.out_dir = (runs / f"brief_history_{stamp}").as_posix()
        notes.append("defaulted --out-dir under operator_briefing/runs")
    notes.append("digest.md omits prior notes. Query events for one ticker.")
    exported = write_run_export(
        args.out_dir,
        tool="tv_scan_cli.briefs",
        tables={
            "sessions": built["sessions"],
            "names": built["names"],
            "events": built["events"],
            "adapt": [{"line": line} for line in built["adapt"]],
        },
        sources={
            "priors": priors.as_posix(),
            "runs": runs.as_posix(),
            "recipe": recipe.as_posix(),
            "latest_day": latest_day,
        },
        schema_version="brief_history_v1",
        notes=notes,
        extra={
            "n_names": built["spec"]["n_names"],
            "n_events": built["spec"]["n_events"],
            "buckets": built["buckets"],
        },
        duckdb_name="brief_history.duckdb",
    )
    md_path = Path(args.out_dir) / "digest.md"
    md_path.write_text(format_digest_md(built), encoding="utf-8")
    exported["digest"] = md_path.as_posix()
    printed = {
        "spec": built["spec"],
        "buckets": built["buckets"],
        "adapt": built["adapt"],
        "names": built["names"] if args.include_rows else built["shown"],
        "notes": notes,
        "export": exported,
    }
    if args.include_rows:
        printed["events"] = built["events"]
    if not args.quiet:
        print(_dumps(printed))
    return printed


def _cmd_session(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.run_export import write_run_export
    from generic_utils.session_tape import build_named_premarket, build_post_earn

    symbols = [part.strip() for part in str(args.ids or "").split(",") if part.strip()]
    if symbols:
        built = build_named_premarket(
            prior_db=args.prior_db,
            current_db=args.current_db,
            symbols=symbols,
            prior_run=args.prior_run,
            current_run=args.current_run,
        )
        table_name = "premarket"
    else:
        if not args.earn_on:
            raise SystemExit("session needs --earn-on, or --ids for a named premarket join")
        exchanges = None
        if args.exchanges:
            exchanges = [part.strip() for part in str(args.exchanges).split(",") if part.strip()]
        built = build_post_earn(
            prior_db=args.prior_db,
            current_db=args.current_db,
            earn_on=args.earn_on,
            prior_run=args.prior_run,
            current_run=args.current_run,
            exchanges=exchanges,
        )
        table_name = "post_earn"
    payload = {
        "spec": built["spec"],
        "rows": built["rows"] if args.include_rows else [],
        "n": built["spec"]["n"],
    }
    if args.out_dir:
        exported = write_run_export(
            args.out_dir,
            tool="tv_scan_cli.session",
            tables={table_name: built["rows"]},
            sources=built["spec"],
            notes=list(built["spec"].get("notes") or []),
            duckdb_name="session.duckdb",
        )
        payload["export"] = exported
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _cmd_premarket_book(args: argparse.Namespace) -> dict[str, Any]:
    from datetime import datetime, timezone
    from generic_utils.run_export import write_run_export
    from generic_utils.session_tape import build_premarket_book, load_premarket_recipe

    recipe = load_premarket_recipe(args.recipe)
    built = build_premarket_book(
        prior_db=args.prior_db,
        current_db=args.current_db,
        recipe=recipe,
    )
    out_dir = args.out_dir
    if not out_dir:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        out_dir = (
            Path(__file__).resolve().parents[2]
            / "logs"
            / "tradingview_analysis"
            / "operator_briefing"
            / "runs"
            / f"premarket_book_{stamp}_utc"
        )
    exported = write_run_export(
        out_dir,
        tool="tv_scan_cli.premarket_book",
        tables={"premarket": built["rows"], "tape_climate": built.get("climate") or []},
        sources=built["spec"],
        notes=list(built["spec"].get("notes") or []),
        duckdb_name="premarket_book.duckdb",
    )
    payload = {
        "spec": built["spec"],
        "n": built["spec"]["n"],
        "in_book_n": built["spec"].get("in_book_n"),
        "export": exported,
        "pre_repeat_n": sum(1 for row in built["rows"] if row.get("pre_repeat")),
    }
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _cmd_export(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.run_export import parse_table_specs, write_run_export

    tables = parse_table_specs(args.table)
    payload = write_run_export(
        args.out_dir,
        tool="tv_scan_cli.export",
        tables=tables,
        sources={"note": args.source_note} if args.source_note else None,
        schema_version="tv_scan_export_v1",
        notes=list(args.note or []),
        duckdb_name=args.duckdb_name,
    )
    print(_dumps(payload))
    return payload


def _cmd_pack_focus(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.focus import pack_path_rows
    from generic_utils.scan_sources import rows_to_csv

    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    rows = pack_path_rows(pack, args.sleeve)
    out_path = None
    if args.out:
        out_path = rows_to_csv(args.out, rows)
    payload = {
        "spec": {
            "pack": args.pack,
            "sleeve": args.sleeve,
            "n": len(rows),
            "pack_run_id": pack.get("run_id"),
        },
        "rows": rows if args.include_rows else [],
        "out": out_path.as_posix() if out_path else None,
    }
    print(_dumps(payload))
    return payload


def _add_source_flags(parser: argparse.ArgumentParser, *, sql_optional: bool = True) -> None:
    parser.add_argument("--csv", default=None)
    parser.add_argument("--db", default=None)
    if sql_optional:
        parser.add_argument("--sql", default=None)


def _table_rows(database: str, table: str) -> list[dict[str, Any]]:
    from generic_utils.scan_sources import rows_from_duckdb
    from generic_utils.series import qident

    if table == "all_fields_rows":
        raise SystemExit("payoff-focus does not read all_fields_rows. Pass a pack table or a CSV.")
    columns = rows_from_duckdb(
        database,
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        ORDER BY ordinal_position
        """,
        [table],
    )
    names = [str(row.get("column_name") or "") for row in columns if row.get("column_name")]
    if not names:
        raise SystemExit(f"No columns on table {table}")
    sql = "SELECT " + ", ".join(qident(name) for name in names) + " FROM " + qident(table)
    return rows_from_duckdb(database, sql)


def _cmd_payoff_focus(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.payoff_focus import load_payoff_recipe, write_payoff_focus
    from generic_utils.scan_sources import rows_from_csv

    if args.csv:
        rows = rows_from_csv(args.csv)
        source = args.csv
    elif args.db and args.table:
        rows = _table_rows(args.db, args.table)
        source = f"{args.db} {args.table}"
    else:
        raise SystemExit("payoff-focus needs --csv or both --db and --table")
    recipe = load_payoff_recipe(args.recipe)
    if args.top is not None:
        recipe["top"] = int(args.top)
    if args.cap is not None:
        recipe["cap"] = None if int(args.cap) < 0 else int(args.cap)
    exported = write_payoff_focus(
        rows,
        args.out_dir,
        recipe=recipe,
        source_note=f"source={source}",
    )
    payload = {
        "n": len(rows),
        "tables": exported.get("tables"),
        "export": {key: exported.get(key) for key in ("out_dir", "duckdb", "query_guide", "csv")},
    }
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _cmd_book_cycle(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.book_cycle import load_book_cycle_recipe, write_book_cycle
    from generic_utils.scan_sources import rows_from_csv

    recipe = load_book_cycle_recipe(args.recipe)
    exported = write_book_cycle(
        rows_from_csv(args.tape),
        args.out_dir,
        fund_rows=rows_from_csv(args.fund) if args.fund else None,
        span_rows=rows_from_csv(args.span) if args.span else None,
        peer_rows=rows_from_csv(args.peer) if args.peer else None,
        recipe=recipe,
        source_note=args.source_note,
    )
    payload = {"tables": exported.get("tables"), "export": {key: exported.get(key) for key in ("out_dir", "duckdb", "query_guide")}}
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _cmd_entry_book(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.entry_book import load_entry_recipe, marks_from_rows, write_entry_book
    from generic_utils.scan_sources import rows_from_csv

    rows = rows_from_csv(args.csv)
    recipe = load_entry_recipe(args.recipe) if args.recipe else load_entry_recipe()
    marks = None
    if args.mark_csv:
        marks = marks_from_rows(rows_from_csv(args.mark_csv), close_field=args.mark_field or "close")
    if not args.out_dir:
        raise SystemExit("entry-book requires --out-dir")
    exported = write_entry_book(
        rows,
        args.out_dir,
        recipe=recipe,
        marks=marks,
        source_note=args.source_note,
    )
    payload = {"n": len(rows), "export": exported}
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _cmd_intraday(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.intraday import drift_by_group, list_runs, run_drift, write_intraday
    from generic_utils.series import list_dated_files

    db = args.db
    if not db:
        files = list_dated_files(Path(__file__).resolve().parents[2] / "logs" / "tradingview_analysis" / "trading_view_all_fields_data",
                                 "**/tradingview_all_fields_*.duckdb", newest=1)
        if not files:
            raise SystemExit("intraday: no all-fields day file")
        db = str(files[-1]["path"])
    runs = list_runs(db)
    if len(runs) < 2 and not (args.run_a and args.run_b):
        raise SystemExit(f"intraday: {db} has {len(runs)} run(s); two are needed")
    run_a = args.run_a or runs[0]["run_id"]
    run_b = args.run_b or runs[-1]["run_id"]
    clauses, params = [], []
    if args.min_mcap:
        clauses.append("market_cap_basic >= ?")
        params.append(float(args.min_mcap))
    exchanges = [e for e in (args.exchanges or "").split(",") if e]
    if exchanges:
        clauses.append(f"split_part(symbol, ':', 1) IN ({', '.join('?' for _ in exchanges)})")
        params += exchanges
    if args.ids:
        from generic_utils.series import expand_ids

        ids = expand_ids([i for i in args.ids.split(",") if i])
        clauses.append(f"symbol IN ({', '.join('?' for _ in ids)})")
        params += ids
    rows = run_drift(db, run_a=run_a, run_b=run_b, where_sql=" AND ".join(clauses), params=params, flat_pct=args.flat_pct)
    groups = drift_by_group(rows, min_n=args.group_min_n)
    created = {r["run_id"]: r["created_at_utc"] for r in runs}
    exported = write_intraday(rows, groups, args.out_dir, sources={
        "db": str(db), "run_a": run_a, "run_a_utc": created.get(run_a), "run_b": run_b, "run_b_utc": created.get(run_b),
        "min_mcap": args.min_mcap, "exchanges": args.exchanges, "n": len(rows)})
    payload = {"n": len(rows), "groups": len(groups), "run_a": run_a, "run_b": run_b, "export": exported}
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _theme_or_ids(args: argparse.Namespace) -> list[str]:
    from generic_utils.name_levels import theme_ids
    from generic_utils.series import parse_csv_list

    ids = parse_csv_list(args.ids) if getattr(args, "ids", None) else []
    if getattr(args, "theme", None):
        ids += [i for i in theme_ids(parse_csv_list(args.theme)) if i not in ids]
    if not ids:
        raise SystemExit("needs --ids and/or --theme (config/generic_utils/themes.json)")
    return ids


def _cmd_name_levels(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.name_levels import run_name_levels

    pack = _resolve_pack_dir(args.pack)
    out_dir = args.out_dir or f"{pack}/analysis_scans/name_levels"
    payload = run_name_levels(pack, _theme_or_ids(args), out_dir, source_note=args.source_note or f"name-levels on {Path(pack).name}")
    if args.quiet:
        miss = f" missing={','.join(payload['missing_ids'])}" if payload["missing_ids"] else ""
        print(f"n={payload['n']}{miss} {payload['lens']} history_rows={payload['history_rows']} "
              f"files={payload['history_files']} skipped_files={payload['history_skipped_files']} out={payload['out_dir']}")
    else:
        print(_dumps(payload))
    return payload


def _cmd_theme_slice(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.name_levels import load_themes, theme_slice

    pack = _resolve_pack_dir(args.pack)
    theme = load_themes().get(args.theme) or {}
    industries = theme.get("industries") or []
    if not industries:
        raise SystemExit(f"theme {args.theme!r} has no industries in config/generic_utils/themes.json")
    out = args.out or f"{pack}/analysis_scans/theme_slice.csv"
    payload = theme_slice(pack, industries, field=args.field, top=args.top, cap=args.cap, out=out)
    rows = payload.get("rows") or []
    if args.line:
        cols = ["symbol", "ind", "lenses", "tape_state", args.field, "r_1m", "eps_rev_3m", "fwd_eps_g", "pe_nfy", "pe_nfy_vs_ind", "street_left", "dte"]
        print(_row_lines(rows, cols))
    else:
        print(f"n={len(rows)} field={args.field} cap={args.cap} out={payload.get('out')}")
    return payload


def _cmd_earnings_week(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.earnings_week import build_earnings_week, load_recipe, write_earnings_week
    from generic_utils.series import parse_csv_list

    pack = _resolve_pack_dir(args.pack)
    recipe = load_recipe(args.recipe)
    if args.weeks:
        recipe["weeks"] = args.weeks
    if args.min_mcap is not None:
        recipe["min_market_cap_usd"] = args.min_mcap
    if args.markets:
        recipe["markets"] = parse_csv_list(args.markets)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_earnings_week(database=args.db, pack=pack, ids=ids, recipe=recipe)
    exported = write_earnings_week(payload, args.out_dir or f"{pack}/earnings_week")
    spec = payload["spec"]
    if args.line:
        named = [r for r in payload["calendar"] if r.get("named")]
        cols = ["symbol", "earn_utc", "slot", "react_on", "printed", "day", "w", "runup", "pt_up", "eps_q_step", "last_surprise",
                "pe_nfy", "pe_nfy_vs_ind", "pe_nfy_vs_own", "eps_rev_3m", "react_hist", "lenses", "cons_w", "left", "rr"]
        print(_row_lines(named, cols))
    miss = f" missing={','.join(spec['missing_ids'])}" if spec["missing_ids"] else ""
    weeks = {w: sum(1 for r in payload["calendar"] if r.get("week") == w) for w in ("W0", "W1")}
    print(f"n={spec['n']} W0={weeks['W0']} W1={weeks['W1']} prints={spec['n_prints']}{miss} "
          f"window={spec['week_start']}..{spec['week_end']} scan={spec['run_id']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_week_plays(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.week_plays import build_week_plays, load_recipe, write_week_plays

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_week_plays(pack, recipe=load_recipe(args.recipe), ids=ids, database=args.db)
    exported = write_week_plays(payload, args.out_dir or f"{pack}/week_plays")
    spec = payload["spec"]
    if args.line:
        cols = ["symbol", "phase", "earn_dow", "slot", "close", "runup", "react_last", "eps_rev_3m", "dec_7d", "dec_28d",
                "b7d_q20", "b7d_q80", "entry_low", "entry_high", "kill_px", "mark_vs_band", "pt", "fcf_margin", "nd_ebitda",
                "vol_d", "setup_cell", "setup_mean", "setup_t", "mix_cell", "mix_mean", "big_cell", "big_mean"]
        wanted = set(ids)
        rows = [r for r in payload["plays"] if not wanted or r["symbol"] in wanted or r["symbol"].split(":")[-1] in wanted]
        print(_row_lines(rows, cols))
    print(f"plays={spec['n_plays']} book={spec['n_book']} entry_rows={spec['entry_rows']} alpha={spec['alpha']} "
          f"lab={spec['lab']} mkt_day={spec['mkt_day']} scan_day={spec['scan_day']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_deterioration(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.deterioration import build_deterioration, load_recipe, write_deterioration

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    recipe = load_recipe(args.recipe)
    if args.first_day:
        recipe.setdefault("study", {})["first_day"] = args.first_day
    payload = build_deterioration(pack, recipe=recipe, ids=ids, store=args.store)
    exported = write_deterioration(payload, args.out_dir or f"{pack}/deterioration")
    spec = payload["spec"]
    if args.line:
        cols = ["symbol", "book_usd", "book_vs_cost", "close", "hi_prox", "dd", "det", "vs200", "r_1m", "r_3m", "eps_rev_3m",
                "rev_rev_3m", "pt_rev_1m", "opm_chg_q", "rev_yoy_chg_q", "fcf_margin", "cnt_cell", "cnt_fx_28d_mean", "cnt_fx_28d_t",
                "cnt_p_dn_28d", "cnt_p_half_56d", "carry_28d_usd", "tail_28d_usd", "pred_28d", "dec_28d", "kill_px", "earn_date"]
        rows = [r for r in payload["names"] if r["in_book"] or r["named"]]
        print(_row_lines(rows, cols))
    miss = f" missing={','.join(spec['missing_ids'])}" if spec["missing_ids"] else ""
    print(f"names={spec['n_names']} cells={spec['n_cells']} day={spec['day']} mkt_regime={spec['mkt_regime']} "
          f"frame={spec['frame']['first']}..{spec['frame']['last']} rows={spec['frame']['rows']}{miss} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_recovery(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.recovery_ladder import build_recovery_ladder, load_recipe, write_recovery_ladder

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_recovery_ladder(pack, recipe=load_recipe(args.recipe), ids=ids)
    exported = write_recovery_ladder(payload, args.out_dir or f"{pack}/recovery_ladder")
    spec = payload["spec"]
    if args.line:
        print(_row_lines(payload["lines"], ["symbol", "value_usd", "pnl_usd", "pnl_pct", "close", "avg_px", "be_from_close_pct",
                                            "gain10_from_close_pct", "sma50", "half_px", "street_px", "bounce_low", "bounce_high",
                                            "bounce_kill", "det_seen", "let_fx_28d_mean", "let_p_half_56d", "solve_at_px",
                                            "add_be_at_sma50_usd", "add_be_at_half_px_usd", "add_be_at_street_px_usd"]))
    miss = f" missing={','.join(spec['missing_ids'])}" if spec["missing_ids"] else ""
    print(f"lines={spec['n_lines']} ladder={spec['n_ladder']} adds={spec['add_usd']} at={spec['add_levels']}{miss} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_recovery_clock(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.recovery_clock import build_recovery_clock, load_recipe, write_recovery_clock

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_recovery_clock(pack, recipe=load_recipe(args.recipe), ids=ids, store=args.store)
    exported = write_recovery_clock(payload, args.out_dir or f"{pack}/recovery_clock")
    spec = payload["spec"]
    if args.line:
        print(_row_lines(payload["lines"], ["symbol", "value_usd", "pnl_pct", "dd", "det", "run_12m", "primary", "need_cost_pct",
                                            "p_cost_91", "p_cost_182", "days_cost", "need_sma50_pct", "p_sma50_91", "days_sma50",
                                            "need_half_pct", "p_half_91", "p_half_182", "floor_91_med", "floor_91_q10", "floor_91_q10_px",
                                            "floor_91_q10_usd", "fwd_91_med", "fx_91_mean", "n_91"]))
    miss = f" no_state={','.join(spec['missing_state'])}" if spec["missing_state"] else ""
    print(f"starts={spec['starts']} cells={spec['n_cells']} lines={spec['n_lines']} frame={spec['frame']['first']}..{spec['frame']['last']}{miss} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_cycle(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.cycle_scenarios import build_cycle_scenarios, load_recipe, write_cycle_scenarios

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_cycle_scenarios(pack, recipe=load_recipe(args.recipe), ids=ids, value_csv=args.value_csv, shocks=args.shocks)
    exported = write_cycle_scenarios(payload, args.out_dir or f"{pack}/cycle_scenarios")
    spec = payload["spec"]
    if args.line:
        print(_row_lines(payload["names"], ["symbol", "book_usd", "industry", "close", "eps_ttm", "eps1", "boom_g_pct", "pe_y1",
                                            "pe_on_peak_k2", "margin_x_ind", "hurdle_pct", "drop_in_k0", "drop_in_k1", "drop_in_k2",
                                            "drop_in_k3", "drop_state_k0", "pe_need_k0_d50", "pe_need_k1_d50", "pe_need_k2_d50",
                                            "pe_need_k2_d25", "k_needed_d50", "up_k2_d50", "eps_rev_3m", "invalid_reason"]))
        for r in payload["stress"]:
            if r["role"] == "BOOK total":
                print(f"stress {r['scenario']}: usd={r['usd']} nav_pct={r['nav_pct']} book_pct={r['shock_pct']}")
    miss = f" missing={','.join(spec['missing_ids'])}" if spec["missing_ids"] else ""
    print(f"names={spec['n_names']} valid={spec['n_valid']} grid={spec['n_grid']} stress={len(payload['stress'])}{miss} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_peer_value(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.peer_value import build_peer_value, load_recipe, write_peer_value

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_peer_value(pack, recipe=load_recipe(args.recipe), ids=ids, store=args.store, with_evidence=not args.no_evidence)
    exported = write_peer_value(payload, args.out_dir or f"{pack}/peer_value")
    spec = payload["spec"]
    if args.line:
        shown = [r for r in payload["names"] if r.get("in_book") or r.get("named")]
        print(_row_lines(shown, ["symbol", "group", "book_usd", "lens", "mult", "ind_med", "prem_pct", "just_pct", "gap_pct", "px_fit",
                                 "pctl_value", "pctl_growth", "pctl_quality", "pctl_revision", "rev_nfy_g_vs_ind", "opm_vs_ind", "roe_vs_ind",
                                 "peg", "peg_vs_ind_pct", "priced_in_cagr_pct_vs_ind", "irr_exp_pct_vs_ind"]))
        for e in payload["evidence"]:
            print(f"evidence {e['horizon']} {e['signal']}: ic {e['ic_mean']} t {e['t_indep']} days {e['n_days']} hit {e['hit_days']}")
    miss = f" missing={','.join(spec['missing_ids'])}" if spec["missing_ids"] else ""
    print(f"day={spec['day']} peers={spec['n_peer_rows']} listed={spec['n_listed']} groups={spec['n_groups']} fitted={spec['n_fitted']}"
          f" evidence_days={spec['evidence'].get('days')}{miss} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_board(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.opportunity_board import build_board, load_recipe, write_board

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_board(pack, recipe=load_recipe(args.recipe), ids=ids)
    exported = write_board(payload, args.out_dir or f"{pack}/board")
    spec = payload["spec"]
    if args.line:
        shown = [r for r in payload["names"] if r.get("in_book") or r.get("named")]
        print(_row_lines(shown, ["symbol", "book_usd", "close", "day", "band_state", "bounce_low", "bounce_high", "bounce_kill", "to_band_high_pct",
                                 "kill_dist_pct", "earn_date", "bar_tape", "bar_rev", "c_bar_text", "dec_28d", "pred_28d", "peer_gap_pct",
                                 "fv_upside_pct", "beta", "rvol_20", "det", "cnt_fx_28d_mean"]))
    joins = " ".join(f"{k}={v['n']}" for k, v in spec["joins"].items())
    miss = f" missing={','.join(spec['missing_ids'])}" if spec["missing_ids"] else ""
    miss += f" bare_dropped={','.join(spec['bare_ids'])}" if spec.get("bare_ids") else ""
    print(f"names={spec['n_names']} in_band={spec['n_in_band']} reporters={spec['n_reporters']} joins: {joins}{miss} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_lane_book(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.lane_book import build_lane_book, load_recipe, write_lane_book

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_lane_book(pack, recipe=load_recipe(args.recipe), ids=ids, store=args.store, calendar=args.calendar)
    exported = write_lane_book(payload, args.out_dir or f"{pack}/lane_book")
    spec = payload["spec"]
    if args.line:
        lanes = [r for r in payload["lanes"] if r["split"] == "all"]
        print(_row_lines(lanes, ["side", "n_28", "n_days", "xs_7", "xs_28", "t_28", "hit_28", "p_up_28", "p_dn_28", "xs_28_h1", "xs_28_h2",
                                 "n_live"], id_field="lane"))
        shown = [r for r in payload["names"] if r.get("in_book") or r.get("named")]
        print(_row_lines(shown, ["book_usd", "lanes", "avoid", "best_lane", "best_xs_28", "best_p_up_28", "vq", "timing", "cell_xs_28",
                                 "entry_px", "bounce_kill", "month_up_pct", "risk_pct", "payoff", "earn_date"]))
    c = payload["capital"][0] if payload["capital"] else {}
    print(f"day={spec['day']} store={spec['first_day']}.. lanes={spec['n_lanes']} names={spec['n_names']} long={spec['n_long_names']} "
          f"avoid={spec['n_avoid_names']} events={spec['n_events']} cash_above_floor=${c.get('above_floor_usd')} "
          f"starters={c.get('starters_at_hint')} out={exported['out_dir']} highlights={exported['highlights']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_downside(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.downside_risk import build_downside_risk, load_recipe, write_downside_risk

    pack = _resolve_pack_dir(args.pack)
    payload = build_downside_risk(pack, recipe=load_recipe(args.recipe), store=args.store)
    exported = write_downside_risk(payload, args.out_dir or f"{pack}/downside")
    spec = payload["spec"]
    if args.line:
        print(_row_lines(payload["lines"], ["symbol", "value_usd", "wt_nav", "in_cluster", "beta_store", "down_beta_store", "beta_tv",
                                            "vol_1m_pct", "own_q05_28d_pct", "maxdd_store_pct", "to_sma200_pct", "to_lo52_pct",
                                            "to_kill_px_pct", "cnt_p_dn_56d", "mkt10_usd"]))
        print(_row_lines(payload["scenarios"], ["cap_move_pct", "book_usd_store", "nav_pct_store", "book_usd_down", "nav_pct_down",
                                                "cluster_usd_down"], id_field="cap_move_pct"))
        print(_row_lines([d for d in payload["dist"] if d["series"] in ("cap", "book")],
                         ["horizon_d", "series", "n_windows", "n_indep", "q05_pct", "q10_pct", "worst_pct", "worst_start", "q05_usd", "worst_usd"], id_field="series"))
        print(_row_lines(payload["episodes"], ["series", "peak", "trough", "depth_pct", "recovered_on", "book_usd", "book_nav_pct",
                                               "cluster_pct", "cap_pct"], id_field="episode"))
        print(_row_lines(payload["plans"], ["plan", "course", "delta_usd", "beta_used", "beta_src", "beta_delta_usd", "in_cluster"]))
        print(f"plans_after={spec['plans_after']} to_levels={spec['to_levels']}")
    print(f"store={spec['first_file']}..{spec['last_file']} files={spec['n_files']} nav=${spec['nav_usd']} beta_nav={spec['beta_nav_pct']}% "
          f"down_beta_nav={spec['down_beta_nav_pct']}% cluster={spec['cluster_nav_pct']}% book_vol_1m={spec['book_vol_1m_pct']}% "
          f"dd_now={spec['dd_now']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_event_risk(args: argparse.Namespace) -> dict[str, Any]:
    from datetime import date as _date

    from generic_utils.event_risk import build_event_risk, highlights_text, load_recipe, write_event_risk

    pack = _resolve_pack_dir(args.pack)
    ids = [s.strip() for s in (args.ids or "").split(",") if s.strip()]
    as_of = _date.fromisoformat(args.as_of) if args.as_of else None
    payload = build_event_risk(pack, recipe=load_recipe(args.recipe), store=args.store, ids=ids, as_of=as_of)
    exported = write_event_risk(payload, args.out_dir or f"{pack}/event_risk")
    spec = payload["spec"]
    if args.line:
        # Compact: the core warning cells per Book line / named id (detail per line lives in highlights.log and the tables).
        print(_row_lines(payload["book"], ["warn_level", "er_letters", "stress_5s_usd", "stress_5s_nav", "crowd_day_pct", "crowd_z",
                                           "ev_recent_z", "ev_next", "ev_binary"]))
    print(f"store={spec['first_file']}..{spec['last_file']} steps={spec['n_steps']} names={spec['n_names']} book={spec['n_book']} "
          f"levels={spec['book_levels']} book_stress=${spec['book_stress_5s_usd']} ahead={spec['n_upcoming']} recent={spec['n_recent']} "
          f"sell={spec['n_sell']} buy={spec['n_buy']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(highlights_text(payload), end="")
    return {"spec": spec, "export": exported}


def _cmd_dossier(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.name_dossier import build_dossier, highlights_text, judgment_skeleton, load_recipe, write_dossier

    ids = [s.strip() for s in (args.ids or "").split(",") if s.strip()]
    payload = build_dossier(ids, pack=args.pack, store=args.store, recipe=load_recipe(args.recipe), question=args.question,
                            theme=args.theme, label=args.label)
    exported = write_dossier(payload, args.out_dir)
    skeleton = Path(exported["out_dir"]) / "dossier.judgment.skeleton.json"
    skeleton.write_text(json.dumps(judgment_skeleton(exported["out_dir"]), indent=1, ensure_ascii=False), encoding="utf-8")
    if not args.quiet:
        print(highlights_text(payload), end="")
    print(f"ids={len(payload['spec']['ids'])} out={exported['out_dir']} dossier={exported['dossier']} skeleton={skeleton.as_posix()}")
    return {"spec": payload["spec"], "export": exported}


def _cmd_dossier_page(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.name_dossier import dossier_page

    out = dossier_page(args.dir, args.judgment, args.out_dir)
    warnings = out.get("warnings") or []
    print(f"html={out['html']} tabs={len(out['tabs'])} warnings={len(warnings)}")
    for w in warnings:
        print(f"  warning: {w}")
    if args.strict and warnings:
        raise SystemExit(f"{len(warnings)} page warning(s) under --strict")
    return out


def _cmd_symbol_check(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.symbol_resolver import holdings_ids, symbol_check

    ids = [s.strip() for s in (args.ids or "").split(",") if s.strip()] or holdings_ids(args.holdings)
    rows = symbol_check(ids, store=args.store, stale_days=args.stale_days)
    cols = ["current", "qualified", "alias_from", "mark_source", "close", "mark_age_days", "store_first", "store_last", "stale",
            "looks_old", "candidates", "reason"]
    if args.out:
        from generic_utils.scan_sources import rows_to_csv

        rows_to_csv(args.out, rows)
    if not args.quiet:
        print(_row_lines([r for r in rows if r["looks_old"] or r["alias_from"] or not args.flagged], cols))
    old = [r["symbol"] for r in rows if r["looks_old"]]
    print(f"ids={len(rows)} looks_old={len(old)}{' (' + ', '.join(old) + ')' if old else ''} "
          f"aliased={sum(1 for r in rows if r['alias_from'])} out={args.out or '-'}")
    return {"rows": rows}


def _cmd_sentiment(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.market_sentiment import build_market_sentiment, highlights_text, load_recipe, write_market_sentiment

    payload = build_market_sentiment(recipe=load_recipe(args.recipe), store=args.store)
    out = args.out_dir or (f"{_resolve_pack_dir(args.pack)}/sentiment" if args.pack else "logs/tradingview_analysis/market_sentiment")
    exported = write_market_sentiment(payload, out)
    spec = payload["spec"]
    if args.line:
        print(_row_lines(payload["readings"], ["family", "value", "chg_5", "pctl", "q", "read_5", "read_10", "read_21", "t_5", "t_21", "n_indep_21"],
                         id_field="indicator"))
        print(_row_lines(payload["patterns"], ["live", "days", "episodes", "last", "lift_5", "hit_5", "lift_21", "hit_21"], id_field="pattern"))
        print(_row_lines(payload["outside"], ["date", "value", "unit", "chg_prev", "pctl_own", "source"], id_field="kind"))
        print(highlights_text(payload), end="")
    print(f"store={spec['first_file']}..{spec['last_file']} files={spec['n_files']} scan={spec['scan_at']} frac={spec['scan_frac']} "
          f"live_patterns={','.join(spec['live_patterns']) or '-'} families={spec['families']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_sentiment_add(args: argparse.Namespace) -> dict[str, Any]:
    from datetime import date

    from generic_utils.market_sentiment import add_outside, load_recipe

    recipe = load_recipe(args.recipe)
    path = args.file or recipe.get("outside_file") or "config/generic_utils/sentiment_outside.json"
    row = add_outside(path, kind=args.kind, value=float(args.value), day=args.date or date.today().isoformat(), unit=args.unit or "",
                      source=args.source or "", link=args.link or "", read=args.read or "")
    print(f"added {row['kind']}={row['value']}{row['unit']} on {row['date']} ({row['source']}) -> {path}")
    return {"row": row, "file": str(path)}


def _cmd_pathway(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.pathway import build_pathway, load_recipe, write_pathway

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_pathway(pack, recipe=load_recipe(args.recipe), ids=ids, store=args.store)
    exported = write_pathway(payload, args.out_dir or f"{pack}/pathway")
    spec = payload["spec"]
    if args.line:
        print(_row_lines(payload["exposure"], ["factor", "book_pctl", "gap", "ic_28d", "verdict_28d", "ic_month", "ic_prev_month",
                                               "ic_tape", "ic_tape_prev", "tape_pos", "tilt_month", "tilt_tape", "coverage_pct"], id_field="factor"))
        print(_row_lines(payload["attribution"], ["horizon", "mkt", "book_rel", "book_ind_part", "book_stock_part", "rel_usd",
                                                  "ind_part_usd", "stock_part_usd", "coverage_pct"], id_field="horizon"))
    print(f"factors={len(spec['factors'])} book=${spec['book_usd']} in_store=${spec['store_usd']} candidates={spec['n_candidates']} "
          f"day={spec['day']} lab={spec['lab']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_outlook(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.catalyst_outlook import build_outlook, load_recipe, write_outlook

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_outlook(pack, recipe=load_recipe(args.recipe), ids=ids, database=args.db)
    exported = write_outlook(payload, args.out_dir or f"{pack}/outlook")
    spec = payload["spec"]
    if args.line:
        cols = ["symbol", "close", "day", "m1", "rsi", "rng", "vs50", "tape_state", "next_kind", "next_date", "earn_date", "earn_slot",
                "setup_cell", "entry_low", "entry_high", "kill_px", "bounce_low", "bounce_high", "bounce_kill", "last_surprise",
                "surprise_mix", "react_last", "pe_nfy", "pe_nfy_vs_ind", "fcf_margin", "pt_up", "eps_rev_3m", "dec_28d", "pred_28d",
                "lenses", "analysts", "pack_lists"]
        wanted = set(ids)
        rows = [r for r in payload["names"] if r["symbol"] in wanted or r["symbol"].split(":")[-1] in wanted]
        print(_row_lines(rows, cols))
    print(f"names={spec['n_names']} events={spec['n_events']} outside={spec['n_outside']} levels={spec['n_levels']} "
          f"{spec['scan_day']}..{spec['end']} catalyst_run={spec['catalyst_run']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_print_night(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.print_night import build_print_night, load_recipe, write_print_night

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_print_night(pack, recipe=load_recipe(args.recipe), ids=ids, react_on=args.react_on, sessions=args.sessions,
                                store=args.store)
    exported = write_print_night(payload, args.out_dir or f"{pack}/print_night")
    spec = payload["spec"]
    if args.line:
        cols = ["symbol", "phase", "slot", "mcap_b", "close", "runup", "react_abs_mean", "rx_up", "rx_dn", "h7_up", "h7_dn", "h28_up",
                "h28_dn", "h28_fam_up", "h28_up_list", "h28_dn_list", "ac_up", "ac_dn", "d28_up", "d28_dn", "setup_cell", "setup_rx_q10",
                "setup_rx_q90", "tail_starter_usd", "dec_7d", "dec_7d_fx", "bounce_low", "bounce_high", "bounce_kill", "book_usd"]
        rows = payload["names"]
        if ids:
            wanted = set(ids)
            rows = [r for r in rows if r["symbol"] in wanted or r["symbol"].split(":")[-1] in wanted]
        print(_row_lines(rows, cols))
    print(f"names={spec['n_names']} pre={spec['n_pre']} post={spec['n_post']} sessions={','.join(spec['sessions'])} "
          f"arrows={spec['n_arrows']}/{spec['n_cells']} prints={spec['n_events']} features={spec['feature_day']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_beat_bar(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.beat_bar import build_beat_bar, load_recipe, write_beat_bar

    pack = _resolve_pack_dir(args.pack)
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    weeks = [w.strip() for w in args.weeks.split(",") if w.strip()] if args.weeks else None
    payload = build_beat_bar(pack, recipe=load_recipe(args.recipe), ids=ids, weeks=weeks, store=args.store)
    exported = write_beat_bar(payload, args.out_dir or f"{pack}/beat_bar")
    spec = payload["spec"]
    if args.line:
        cols = ["symbol", "week", "earn_date", "slot", "mcap_b", "close", "cell", "cell_depth", "c_n_beat", "c_n_miss", "p_beat",
                "c_rx_beat", "c_rx_beat_hit", "c_rx_miss", "c_beat_sold", "c_bar_text", "c_h28_beat", "c_h28_miss", "p_up_rx", "ev_rx",
                "ev_h7", "ev_h28", "beat_rx_up_list", "beat_rx_dn_list", "own_surprises", "dec_28d", "entry_low", "entry_high", "kill_px",
                "book_usd"]
        rows = payload["names"]
        if ids:
            wanted = set(ids)
            rows = [r for r in rows if r["symbol"] in wanted or r["symbol"].split(":")[-1] in wanted]
        print(_row_lines(rows, cols))
    print(f"names={spec['n_names']} weeks={','.join(spec['weeks'])} cells={spec['n_cells']} prints={spec['n_events']} "
          f"pointer_arrows={spec['n_pointer_arrows']}/{spec['n_pointer_cells']} features={spec['feature_day']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_print_brief(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.print_brief import build_print_brief, load_recipe, write_print_brief

    pack = _resolve_pack_dir(args.pack)
    payload = build_print_brief(pack, recipe=load_recipe(args.recipe))
    exported = write_print_brief(payload, args.out_dir or f"{pack}/print_brief")
    spec = payload["spec"]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        for card in payload["cards"]:
            if card["symbol"] in wanted or card["symbol"].split(":")[-1] in wanted:
                print(f"{card['symbol']}: {card['headline']}")
                if args.line:
                    for field in ("setup_text", "value_text", "quarter_text", "peers_text", "pointers_text", "levels_text", "print_text"):
                        if card.get(field):
                            print(f"  {field}: {card[field]}")
    print(f"cards={spec['n_cards']} printed={spec['n_post']} industries={spec['n_industries']} sessions={','.join(spec['sessions'])} "
          f"out={exported['out_dir']}")
    if not args.quiet and not args.ids:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_play_review(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.play_review import build_play_review, prior_judgment_for, write_play_review

    pack = _resolve_pack_dir(args.pack)
    judgment = args.judgment or prior_judgment_for(pack, before=args.before)
    if not judgment:
        raise SystemExit("No prior judgment.json under logs/canvases: pass --judgment")
    ids = _theme_or_ids(args) if (args.ids or args.theme) else []
    payload = build_play_review(judgment, pack_dir=pack, prior_pack_dir=args.prior_pack, ids=ids)
    exported = write_play_review(payload, args.out_dir or f"{pack}/play_review")
    spec = payload["spec"]
    if args.line:
        cols = ["symbol", "course", "play_rank", "prior_close", "close", "ret_pct", "ret_fx", "bounce_low", "bounce_high",
                "bounce_kill", "open_state", "min_state", "close_state", "now_phase", "now_react_fx", "now_surprise_mix", "now_dec_28d"]
        rows = [r for r in payload["calls"] if r["play_rank"] is not None or ids]
        print(_row_lines(sorted(rows, key=lambda r: r["play_rank"] or 999), cols))
        print(_row_lines(payload["by_course"], ["course", "n", "ret_fx_mean", "ret_fx_median", "up_share"]))
    print(f"calls={spec['n_calls']} priced={spec['n_priced']} play_ids={spec['n_play_ids']} mkt_ret={spec['mkt_ret']} "
          f"{spec['prior_day']}->{spec['later_day']} judgment={spec['judgment']} out={exported['out_dir']}")
    if not args.quiet and not args.line:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _stamp_dir(kind: str) -> Path:
    from datetime import datetime, timezone
    import uuid

    root = Path(__file__).resolve().parents[2] / "logs" / "tradingview_analysis" / kind / "runs"
    return root / f"{kind}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}_utc_{uuid.uuid4().hex[:8]}"


def _store_line(summary: Mapping[str, Any]) -> str:
    labels = " ".join(f"{k}={v}" for k, v in (summary.get("labels") or {}).items())
    return (f"store days={summary.get('days')} rows={summary.get('rows')} symbols={summary.get('symbols')} "
            f"{summary.get('first')}..{summary.get('last')} pred_days={summary.get('pred_days')} prints={summary.get('prints')} labels: {labels}")


def _cmd_panel_store(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.panel_store import load_store_recipe, update_store

    out = update_store(load_store_recipe(args.recipe), store=args.store, root=args.root, rebuild=args.rebuild,
                       derived=not args.no_derived, predictions=not args.no_predictions, refresh_derived=args.derived)
    print(f"ingested={len(out['ingested'])} kept={out['kept']} pred_joined={out['pred_joined']} derived={out['derived']} "
          f"{_store_line(out['summary'])} out={out['store']}")
    if out["skipped"] or out["pred_missing"]:
        print(f"skipped={[s['as_of'] for s in out['skipped']]} pred_missing={out['pred_missing'][:12]}")
    if not args.quiet:
        print(_dumps(out))
    return out


def _cmd_anatomy(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.panel_store import load_store_recipe
    from generic_utils.series import parse_csv_list
    from generic_utils.winners_anatomy import build_anatomy, highlights_text, load_recipe, write_anatomy

    recipe = load_recipe(args.recipe)
    if args.horizons:
        recipe["horizons"] = parse_csv_list(args.horizons)
    if args.tail_pct is not None:
        recipe["tail_pct"] = args.tail_pct
    if args.test:
        recipe["tests"] = list(recipe.get("tests") or []) + list(args.test)
    if args.first_day:
        recipe["study"]["first_day"] = args.first_day
    if args.last_day:
        recipe["study"]["last_day"] = args.last_day
    pack = None if args.pack == "none" else _resolve_pack_dir(args.pack)
    payload = build_anatomy(store=args.store, recipe=recipe, store_recipe=load_store_recipe(args.store_recipe), pack=pack)
    out_dir = args.out_dir or (f"{pack}/anatomy" if pack else _stamp_dir("anatomy").as_posix())
    exported = write_anatomy(payload, out_dir, store=args.store)
    spec = payload["spec"]
    t = payload["tables"]
    print(f"days={spec['days']} {spec['first']}..{spec['last']} pair_tests={spec['pair_tests']} "
          f"stable_up={sum(1 for p in t['pair_lift'] if p['verdict_up'] in ('STRONG', 'STABLE'))} "
          f"stable_dn={sum(1 for p in t['pair_lift'] if p['verdict_dn'] in ('STRONG', 'STABLE'))} live={len(t['live'])} out={exported['out_dir']}")
    if args.line:
        print(highlights_text(payload))
    elif not args.quiet:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_signal_lab(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.panel_store import load_store_recipe, update_store
    from generic_utils.series import parse_csv_list
    from generic_utils.signal_lab import build_signal_lab, highlights_text, load_lab_recipe, write_signal_lab

    store_recipe = load_store_recipe(args.store_recipe)
    if not args.no_update:
        update_store(store_recipe, store=args.store)
    lab = load_lab_recipe(args.recipe)
    if args.horizons:
        lab["horizons"] = parse_csv_list(args.horizons)
    if args.features:
        lab["features"] = parse_csv_list(args.features)
    if args.min_mcap is not None:
        lab["study"]["min_market_cap_usd"] = args.min_mcap
    if args.first_day:
        lab["study"]["first_day"] = args.first_day
    if args.last_day:
        lab["study"]["last_day"] = args.last_day
    studies = parse_csv_list(args.studies) or ["factor", "prints", "scans"]
    payload = build_signal_lab(store=args.store, lab=lab, store_recipe=store_recipe, studies=studies)
    pack = None if args.pack == "none" else _resolve_pack_dir(args.pack)
    out_dir = args.out_dir or (f"{pack}/signal_lab" if pack else _stamp_dir("signal_lab").as_posix())
    exported = write_signal_lab(payload, out_dir, store=args.store)
    spec = payload["spec"]
    verdicts = {}
    for row in payload["tables"].get("factor_summary") or []:
        verdicts[row.get("verdict")] = verdicts.get(row.get("verdict"), 0) + 1
    print(f"days={spec['frame']['days']} {spec['frame']['first']}..{spec['frame']['last']} tests={spec['n_tests']} "
          f"chance_t2={spec['expected_false_at_t2']} verdicts={verdicts} prints={len(payload['tables'].get('print_events') or [])} "
          f"packs={len(payload['tables'].get('scan_packs') or [])} out={exported['out_dir']}")
    if args.line:
        print(highlights_text(payload))
    elif not args.quiet:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_alpha_model(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.alpha_model import build_alpha_model, highlights_text, load_model_recipe, log_live, write_alpha_model
    from generic_utils.panel_store import load_store_recipe, update_store
    from generic_utils.series import parse_csv_list

    store_recipe = load_store_recipe(args.store_recipe)
    if not args.no_update:
        update_store(store_recipe, store=args.store)
    recipe = load_model_recipe(args.recipe)
    if args.horizons:
        recipe["horizons"] = parse_csv_list(args.horizons)
    pack = None if args.pack == "none" else _resolve_pack_dir(args.pack)
    payload = build_alpha_model(store=args.store, recipe=recipe, store_recipe=store_recipe, pack=pack)
    logged = None if args.no_log else log_live(payload, recipe, store=args.store)
    out_dir = args.out_dir or (f"{pack}/alpha_model" if pack else _stamp_dir("alpha_model").as_posix())
    exported = write_alpha_model(payload, out_dir, store=args.store, live_log=logged)
    spec = payload["spec"]
    states = " ".join(f"{s['horizon']}={s['state']}(ic {s['ic_mean']}, recent {s['ic_recent']})" for s in payload["tables"]["state"])
    print(f"live={spec['live_day']} names={len(payload['tables']['live'])} {states} "
          f"logged_days={(logged or {}).get('logged_days')} out={exported['out_dir']}")
    if args.ids:
        from generic_utils.symbol_identity import ticker_map

        live = payload["tables"]["live"]
        by_ticker = ticker_map(r["symbol"] for r in live)
        wanted = [i if ":" in i else by_ticker.get(i.upper(), i) for i in parse_csv_list(args.ids)]
        rows = [r for w in wanted for r in live if r["symbol"] == w]
        cols = ["symbol", "close"] + [f"{k}_{h}" for h in spec["horizons"] for k in ("pred", "dec")] + \
               ["b7d_q20", "b7d_q80", "b28d_q20", "b28d_q50", "b28d_q80", "why_28d"]
        print(_row_lines(rows, cols))
    if args.line:
        print(highlights_text(payload))
    elif not args.quiet and not args.ids:
        print(_dumps({"spec": spec, "export": exported}))
    return {"spec": spec, "export": exported}


def _cmd_judgment_skeleton(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.canvas_page import load_template
    from generic_utils.canvas_runs import judgment_path
    from generic_utils.judgment_tools import build_skeleton, load_json
    from generic_utils.page_update import load_refresh_config
    from generic_utils.series import parse_csv_list

    out = Path(args.out) if args.out else judgment_path(args.day)
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists; pass --force to replace it (or --out elsewhere)")
    names = parse_csv_list(args.templates) or _template_set(load_refresh_config(), args.set)
    templates = [load_template(t) for t in names]
    prev = prev_day = None
    if args.carry_from:
        carry = Path(args.carry_from)
        prev_path = carry if carry.suffix == ".json" else judgment_path(args.carry_from)
        prev = load_json(prev_path)
        prev_day = str(prev.get("day") or (args.carry_from if carry.suffix != ".json" else ""))
    skeleton = build_skeleton(args.day, pack=_resolve_pack_dir(args.pack), templates=templates, prev=prev, prev_day=prev_day)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(skeleton, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out.as_posix()}: carried courses={len(skeleton['courses'])} scans={len(skeleton['scans'])}")
    for key, page in skeleton["pages"].items():
        for block, shape in page["_shapes"].items():
            print(f"  pages.{key}.{'blocks.' if not shape.startswith('ids:') else ''}{block}: {shape}")
    return skeleton


def _template_set(cfg: dict[str, Any], which: str | None) -> list[str]:
    """daily_refresh.json page set: 'core' = templates (brief + board + movers), 'all' = templates_all (every page)."""
    if (which or "core") == "all":
        return list(cfg.get("templates_all") or cfg.get("templates") or [])
    return list(cfg.get("templates") or [])


def _cmd_page_all(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.canvas_page import write_template_page
    from generic_utils.judgment_tools import load_json, unfilled
    from generic_utils.page_update import load_refresh_config
    from generic_utils.series import parse_csv_list

    pack = _resolve_pack_dir(args.pack)
    names = parse_csv_list(args.templates) or _template_set(load_refresh_config(), args.set)
    names += parse_csv_list(args.extra)
    results: list[dict[str, Any]] = []
    for name in names:
        payload = write_template_page(name, pack=pack, day=args.day, judgment_path=args.judgment, out_dir=args.out_dir,
                                      ledger_path=args.ledger)
        label = Path(name).stem
        if payload.get("skipped"):
            print(f"{label}: skipped ({payload['skipped']})")
        elif payload["warnings"]:
            shown = " | ".join(payload["warnings"][:args.show])
            print(f"{label}: {len(payload['warnings'])} warnings: {shown}")
        else:
            print(f"{label}: ok {payload['html']}")
        results.append({"template": name, **payload})
    if args.judgment:
        for key, blocks in unfilled(load_json(args.judgment)).items():
            print(f"unfilled pages.{key}: {', '.join(blocks)}")
    total = sum(len(r.get("warnings") or []) for r in results)
    if args.strict and total:
        raise SystemExit(f"{total} ledger warnings across {len(results)} pages (--strict)")
    return {"pages": results, "warnings": total}


def _cmd_deep_dive(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.canvas_page import write_template_page
    from generic_utils.deep_dive import build_template, qualify_groups
    from generic_utils.name_levels import load_themes, run_name_levels
    from generic_utils.scan_sources import rows_from_csv
    from generic_utils.series import parse_csv_list

    pack = _resolve_pack_dir(args.pack)
    themes = load_themes()
    groups: dict[str, list[str]] = {}
    for name in parse_csv_list(args.theme):
        groups[(themes.get(name) or {}).get("label") or name] = list((themes.get(name) or {}).get("ids") or [])
    if args.ids:
        groups[args.ids_label] = parse_csv_list(args.ids)
    if not groups:
        raise SystemExit("deep-dive needs --theme and/or --ids")
    levels = args.levels_dir or f"{args.out_dir}/name_levels"
    if not args.levels_dir:
        every = [i for ids in groups.values() for i in ids]
        run_name_levels(pack, list(dict.fromkeys(every)), levels, source_note=f"deep-dive on {Path(pack).name}")
    symbols = [r.get("symbol") for r in rows_from_csv(f"{levels}/names.csv")]
    template = build_template(Path(levels).as_posix(), qualify_groups(groups, symbols), name=args.name, title=args.title)
    template_path = Path(args.out_dir) / "deep_dive_template.json"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(json.dumps(template, indent=1), encoding="utf-8")
    payload = write_template_page(template_path, pack=pack, day=args.day, judgment_path=args.judgment, out_dir=args.out_dir)
    print(f"template={template_path.as_posix()} html={payload['html']} warnings={len(payload['warnings'])}")
    for warning in payload["warnings"][:10]:
        print(f"  {warning}")
    if args.strict and payload["warnings"]:
        raise SystemExit(f"{len(payload['warnings'])} ledger warnings (--strict)")
    return payload


def _cmd_safety_band(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.safety_band import build_safety_band, load_safety_recipe, write_safety_band
    from generic_utils.scan_sources import rows_from_csv

    rows = build_safety_band(
        rows_from_csv(args.csv),
        lens=rows_from_csv(args.lens_csv) if args.lens_csv else (),
        history=rows_from_csv(args.history_csv) if args.history_csv else (),
        recipe=load_safety_recipe(args.recipe),
    )
    exported = write_safety_band(rows, args.out_dir, source_note=args.source_note)
    payload = {"n": len(rows), "export": exported}
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _cmd_mesh(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.mesh import load_mesh_recipe, write_mesh
    from generic_utils.scan_sources import rows_from_csv

    if args.csv:
        rows = rows_from_csv(args.csv)
    elif args.db and args.table:
        rows = _table_rows(args.db, args.table)
    else:
        raise SystemExit("mesh needs --csv or --db with --table")
    spans: dict[str, dict[str, Any]] = {}
    if args.span_db:
        for row in _table_rows(args.span_db, args.span_table):
            spans[str(row.get("symbol"))] = row
    forming_ids: set[str] = set()
    if args.forming_db:
        forming_ids = {str(r.get("symbol")) for r in _table_rows(args.forming_db, args.forming_table)}
    recipe = load_mesh_recipe(args.recipe) if args.recipe else load_mesh_recipe()
    exported = write_mesh(rows, args.out_dir, spans=spans, forming_ids=forming_ids, recipe=recipe)
    payload = {"n": len(rows), "n_spans": len(spans), "n_forming": len(forming_ids), "export": exported}
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _cmd_capital_plan(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.capital_plan import load_capital_recipe, write_capital_plan
    from generic_utils.scan_sources import rows_from_csv

    if args.judgment:
        plans = json.loads(Path(args.judgment).read_text(encoding="utf-8")).get("capital_plan") or {}
        if args.key not in plans:
            raise SystemExit(f"judgment has no capital_plan.{args.key}; keys: {sorted(plans)}")
        plan = [dict(r) for r in plans[args.key]]
    elif args.plan:
        plan = rows_from_csv(args.plan)
    else:
        raise SystemExit("capital-plan needs --plan CSV or --judgment (rows from capital_plan.<key>)")
    if not args.out_dir:
        if not (args.judgment and args.pack):
            raise SystemExit("capital-plan needs --out-dir (defaults to PACK/<key> only with --judgment and --pack)")
        args.out_dir = str(Path(args.pack).parent / args.key)
    cash = args.cash
    book: list[dict[str, Any]] = []
    if args.pack:
        pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
        book = [dict(r) for r in pack.get("book") or []]
        if cash is None:
            cash = (pack.get("capital") or {}).get("cash_usd")
    if args.book_csv:
        book = rows_from_csv(args.book_csv)
    if cash is None:
        raise SystemExit("capital-plan needs --cash or a --pack with capital.cash_usd")
    recipe = load_capital_recipe(args.recipe) if args.recipe else load_capital_recipe()
    payload = write_capital_plan(
        plan, book, args.out_dir, cash=float(cash), recipe=recipe, source_note=args.source_note
    )
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _resolve_pack_dir(pack: str | None) -> str:
    """'latest' / None -> newest briefing_pack_* folder (repo-relative posix path)."""
    if pack and pack != "latest":
        return Path(pack).as_posix().rstrip("/")
    root = Path(__file__).resolve().parents[2]
    runs = root / "logs" / "tradingview_analysis" / "operator_briefing" / "runs"
    packs = sorted(runs.glob("briefing_pack_*"), key=lambda p: p.name)
    if not packs:
        raise SystemExit("No briefing_pack_* folder found")
    return packs[-1].relative_to(root).as_posix()


def _cmd_page(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.canvas_page import write_page, write_template_page

    if args.template:
        if args.manual and args.judgment:
            raise SystemExit("--manual renders the data-only page; drop --judgment")
        if not args.manual and not args.judgment:
            raise SystemExit("--template needs --judgment (agent page) or --manual (data-only page)")
        payload = write_template_page(
            args.template,
            pack=_resolve_pack_dir(args.pack),
            day=args.day,
            judgment_path=args.judgment,
            out_dir=args.out_dir,
            ledger_path=args.ledger,
        )
    else:
        if not args.spec:
            raise SystemExit("page needs --spec, or --template with --day")
        payload = write_page(
            args.spec,
            out_path=args.out,
            ledger_path=args.ledger,
            index=not args.no_index,
        )
    if not args.quiet:
        print(_dumps(payload))
    if payload.get("skipped"):
        raise SystemExit(f"page skipped: {payload['skipped']}")
    if args.strict and payload["warnings"]:
        raise SystemExit(f"{len(payload['warnings'])} ledger warnings (--strict)")
    return payload


def _cmd_page_run(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.canvas_runs import new_run_dir, write_index, write_run_record

    run = new_run_dir(args.day, args.mode)
    write_run_record(run, pack=_resolve_pack_dir(args.pack), origin=args.origin)
    write_index()
    root = Path(__file__).resolve().parents[2]
    payload = {"run_dir": run.resolve().relative_to(root).as_posix() if root in run.resolve().parents else run.as_posix()}
    print(_dumps(payload))
    return payload


def _cmd_priors(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.canvas_runs import judgment_path
    from generic_utils.priors import DEFAULT_SKIP, write_priors

    jpath = Path(args.judgment) if args.judgment else judgment_path(args.day)
    payload = write_priors(
        args.day,
        json.loads(jpath.read_text(encoding="utf-8")),
        pack_dir=_resolve_pack_dir(args.pack),
        priors_dir=args.priors_dir,
        source=args.source or "",
        notes=args.notes or "",
        merge=not args.replace,
        skip=[s for s in (args.skip.split(",") if args.skip is not None else DEFAULT_SKIP) if s],
    )
    print(_dumps(payload))
    return payload


def _cmd_timing_levels(args: argparse.Namespace) -> dict[str, Any]:
    from generic_utils.scan_sources import rows_from_csv
    from generic_utils.timing_levels import (
        load_screen_recipe,
        load_timing_recipe,
        write_timing_levels,
    )

    if args.csv:
        rows = rows_from_csv(args.csv)
        source = args.csv
    elif args.db and args.table:
        rows = _table_rows(args.db, args.table)
        source = f"{args.db} {args.table}"
    else:
        raise SystemExit("timing-levels needs --csv or --db and --table")
    if not args.out_dir:
        raise SystemExit("timing-levels requires --out-dir")
    recipe = load_timing_recipe(args.recipe) if args.recipe else load_timing_recipe()
    screen = None
    if args.screen:
        screen = load_screen_recipe(args.screen if args.screen != "low_downside" else None)
    exported = write_timing_levels(
        rows,
        args.out_dir,
        recipe=recipe,
        screen=screen,
        stop_atr_units=args.stop_atr,
        source_note=args.source_note or source,
    )
    payload = {"n": len(rows), "export": exported}
    if not args.quiet:
        print(_dumps(payload))
    return payload


def _cmd_book_sessions(args: argparse.Namespace) -> dict[str, Any]:
    from datetime import datetime, timezone

    from generic_utils.capture import build_holdings_sessions
    from generic_utils.run_export import write_run_export

    project = Path(__file__).resolve().parents[2]
    built = build_holdings_sessions(
        lookback_days=int(args.lookback_days or 180),
        start=args.start,
        end=args.end,
        holdings_runs_root=args.holdings_runs_root,
        briefs_db=args.briefs_db,
    )
    if not args.out_dir:
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M_utc")
        args.out_dir = (
            project
            / "logs"
            / "tradingview_analysis"
            / "operator_briefing"
            / "runs"
            / f"book_sessions_{stamp}"
        ).as_posix()
    notes = [
        "Session roll of holdings runs. Not the all-fields fund path. That stays capture.",
        "still_held is membership on the last as-of in the window.",
        "brief_label is the brief-history name label when --briefs-db is passed. It is not a rank.",
        "A starter to bear in mind is about 5000 USD. An add to a held line can be about 1000 USD. Advice, not a filter.",
    ]
    exported = write_run_export(
        args.out_dir,
        tool="tv_scan_cli.book-sessions",
        tables={
            "book_asof": built["book_asof"],
            "book_names": built["book_names"],
            "exits": built["exits"],
        },
        sources={
            "start": built["start"],
            "end": built["end"],
            "n_runs": built["n_runs"],
            "holdings_runs_root": built["holdings_runs_root"],
            "briefs_db": built["briefs_db"],
        },
        notes=notes,
        duckdb_name="book_sessions.duckdb",
    )
    payload = {
        "start": built["start"],
        "end": built["end"],
        "n_runs": built["n_runs"],
        "n_names": len(built["book_names"]),
        "n_exits": len(built["exits"]),
        "export": exported,
    }
    if not args.quiet:
        print(_dumps(payload))
    return payload


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        description="Generic TradingView dataset interrogation. No embedded eligibility cutoffs."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    recipes_p = sub.add_parser("recipes", help="List named SQL recipes.")
    recipes_p.set_defaults(func=_cmd_recipes)

    inv_p = sub.add_parser("inventory", help="SHOW TABLES + PRAGMA table_info.")
    inv_p.add_argument("--db", required=True)
    inv_p.set_defaults(func=_cmd_inventory)

    sum_p = sub.add_parser("summary", help="Numeric summary of named fields.")
    _add_source_flags(sum_p)
    sum_p.add_argument("--fields", required=True)
    sum_p.set_defaults(func=_cmd_summary)

    grp_p = sub.add_parser("group", help="Group means by a field.")
    _add_source_flags(grp_p)
    grp_p.add_argument("--by", required=True)
    grp_p.add_argument("--metrics", required=True)
    grp_p.add_argument("--min-n", type=int, default=1)
    grp_p.add_argument("--pct-positive", default=None)
    grp_p.add_argument("--digits", type=int, default=2)
    grp_p.add_argument("--center", default="mean", choices=("mean", "median"))
    grp_p.set_defaults(func=_cmd_group)

    hist_p = sub.add_parser("histogram", help="Histogram with caller bin edges.")
    _add_source_flags(hist_p)
    hist_p.add_argument("--field", required=True)
    hist_p.add_argument("--bins", required=True)
    hist_p.set_defaults(func=_cmd_histogram)

    cnt_p = sub.add_parser("counts", help="Value counts for a field.")
    _add_source_flags(cnt_p)
    cnt_p.add_argument("--field", required=True)
    cnt_p.add_argument("--top", type=int, default=40)
    cnt_p.set_defaults(func=_cmd_counts)

    ov_p = sub.add_parser("overlap", help="Set overlap of two CSVs.")
    ov_p.add_argument("--csv-a", required=True)
    ov_p.add_argument("--csv-b", required=True)
    ov_p.add_argument("--id-field", default="symbol")
    ov_p.add_argument("--list-limit", type=int, default=40)
    ov_p.set_defaults(func=_cmd_overlap)

    fetch_p = sub.add_parser("fetch", help="Run a named recipe against a DuckDB.")
    fetch_p.add_argument("--recipe", required=True)
    fetch_p.add_argument("--db", required=True)
    fetch_p.add_argument("--run-id", default=None)
    fetch_p.add_argument("--limit", type=int, default=None)
    fetch_p.add_argument("--max-rows", type=int, default=50)
    fetch_p.add_argument("--out", default=None)
    fetch_p.set_defaults(func=_cmd_fetch)

    focus_p = sub.add_parser("focus", help="Filter + rank + optional group cap.")
    _add_source_flags(focus_p)
    focus_p.add_argument("--where", default=None)
    focus_p.add_argument("--field", required=True)
    focus_p.add_argument("--top", type=int, default=100)
    focus_p.add_argument("--asc", action="store_true")
    focus_p.add_argument("--tie-breaker", default=None)
    focus_p.add_argument("--group-field", default=None)
    focus_p.add_argument("--cap", type=int, default=None)
    focus_p.add_argument("--exempt", default=None)
    focus_p.add_argument("--id-field", default="symbol")
    focus_p.add_argument("--out", default=None)
    focus_p.set_defaults(func=_cmd_focus)

    pack_p = sub.add_parser("pack-focus", help="Export a briefing-pack sleeve to CSV.")
    pack_p.add_argument("--pack", required=True)
    pack_p.add_argument("--sleeve", default="sleeves.radar_curated_50")
    pack_p.add_argument("--out", required=True)
    pack_p.add_argument("--include-rows", action="store_true")
    pack_p.set_defaults(func=_cmd_pack_focus)

    named_p = sub.add_parser("named", help="Named-column fetch from DuckDB or a CSV (ids may be bare tickers). Never SELECT *.")
    named_p.add_argument("--line", action="store_true", help="Print one compact line per row (id field=value ...) instead of JSON.")
    named_p.add_argument("--quiet", action="store_true", help="Print one summary line (n, missing ids, out path).")
    named_p.add_argument("--db", default=None)
    named_p.add_argument("--table", default=None)
    named_p.add_argument("--csv", default=None, help="CSV instead of --db/--table, e.g. PACK/movers/us2b_tape.csv (whole US $2B tape).")
    named_p.add_argument("--fields", required=True)
    named_p.add_argument("--run-id", default=None)
    named_p.add_argument("--id-field", default=None)
    named_p.add_argument("--ids", default=None)
    named_p.add_argument("--filter-field", default=None)
    named_p.add_argument("--filter-values", default=None)
    named_p.add_argument("--equals", default=None)
    named_p.add_argument("--rename", default=None)
    named_p.add_argument("--out", default=None)
    named_p.add_argument("--include-rows", action="store_true")
    named_p.set_defaults(func=_cmd_named)

    hist_src = sub.add_parser(
        "history",
        help="Oldest-to-latest named fields across dated DuckDB files.",
    )
    hist_src.add_argument("--root", required=True)
    hist_src.add_argument("--quiet", action="store_true", help="Print one summary line instead of the snapshot JSON.")
    hist_src.add_argument("--glob", required=True)
    hist_src.add_argument("--table", default="all_fields_rows")
    hist_src.add_argument("--fields", default="close")
    hist_src.add_argument("--id-field", default=None)
    hist_src.add_argument("--ids", default=None)
    hist_src.add_argument("--filter-field", default=None)
    hist_src.add_argument("--filter-values", default=None)
    hist_src.add_argument("--equals", default=None)
    hist_src.add_argument("--run-prefix", default=None)
    hist_src.add_argument("--all-runs", action="store_true")
    hist_src.add_argument("--newest", type=int, default=None)
    hist_src.add_argument("--recipe", default=None)
    hist_src.add_argument("--group-field", default=None)
    hist_src.add_argument("--metrics", default=None)
    hist_src.add_argument("--min-n", type=int, default=1)
    hist_src.add_argument("--pct-positive", default=None)
    hist_src.add_argument("--span-id-field", default=None)
    hist_src.add_argument("--span-fields", default=None)
    hist_src.add_argument("--rename", default=None)
    hist_src.add_argument("--out", default=None)
    hist_src.add_argument("--include-rows", action="store_true")
    hist_src.set_defaults(func=_cmd_history)

    span_p = sub.add_parser("span", help="First/last/delta per id from a history CSV.")
    span_p.add_argument("--csv", required=True)
    span_p.add_argument("--id-field", required=True)
    span_p.add_argument("--fields", required=True)
    span_p.add_argument("--as-of", default="as_of")
    span_p.add_argument("--normalize", action="store_true")
    span_p.add_argument(
        "--include-steps",
        action="store_true",
        help="Also attach day-to-day field_step_* / drawdown / seat columns.",
    )
    span_p.add_argument("--out", default=None)
    span_p.set_defaults(func=_cmd_span)

    wgt_p = sub.add_parser("weight-group", help="Weighted group sums/means.")
    _add_source_flags(wgt_p)
    wgt_p.add_argument("--by", required=True)
    wgt_p.add_argument("--weight", required=True)
    wgt_p.add_argument("--metrics", default="")
    wgt_p.add_argument("--min-n", type=int, default=1)
    wgt_p.add_argument("--digits", type=int, default=2)
    wgt_p.set_defaults(func=_cmd_weight_group)

    join_p = sub.add_parser("join", help="Join two CSVs on normalized ticker ids.")
    join_p.add_argument("--csv-a", required=True)
    join_p.add_argument("--csv-b", required=True)
    join_p.add_argument("--left-on", required=True)
    join_p.add_argument("--right-on", required=True)
    join_p.add_argument("--how", default="left", choices=("left", "inner"))
    join_p.add_argument("--prefix", default="")
    join_p.add_argument("--out", default=None)
    join_p.add_argument("--include-rows", action="store_true")
    join_p.set_defaults(func=_cmd_join)

    der_p = sub.add_parser("derive", help="Attach leftover / range / vs50 / rr / optional vs-group.")
    der_p.add_argument("--csv", required=True)
    der_p.add_argument("--close", default="close")
    der_p.add_argument("--pt", default="pt")
    der_p.add_argument("--edge", default="edge_left")
    der_p.add_argument("--lo52", default="lo52")
    der_p.add_argument("--hi52", default="hi52")
    der_p.add_argument("--sma50", default="sma50")
    der_p.add_argument("--leftover-max", type=float, default=None)
    der_p.add_argument("--refresh", action="store_true")
    der_p.add_argument("--vs-by", default=None, help="Group field for pct vs median (e.g. ind).")
    der_p.add_argument("--vs-fields", default=None, help="Comma fields (evrev,pe,left).")
    der_p.add_argument("--vs-min-n", type=int, default=6)
    der_p.add_argument("--out", default=None)
    der_p.add_argument("--include-rows", action="store_true")
    der_p.set_defaults(func=_cmd_derive)

    mv_p = sub.add_parser(
        "movers",
        help="Both-tail leaders/laggards per named return field. No cutoff.",
    )
    _add_source_flags(mv_p)
    mv_p.add_argument("--recipe", default=None, help="JSON recipe (movers_day.json).")
    mv_p.add_argument("--fields", default=None, help="Comma fields; recipe default if omitted.")
    mv_p.add_argument("--where", default=None, help="Optional caller filter. Empty = no filter.")
    mv_p.add_argument(
        "--top",
        type=int,
        default=None,
        help="Legacy per-tail size (both tails equal). Ignored when --cover / recipe cover is set.",
    )
    mv_p.add_argument(
        "--cover",
        type=int,
        default=None,
        help="Names per horizon (default 50). Split by --up-share.",
    )
    mv_p.add_argument(
        "--up-share",
        type=float,
        default=None,
        help="Leader share of cover (CLI default 0.75 at cover 50). Recipes pin n_leaders/n_laggards.",
    )
    mv_p.add_argument("--leaders", type=int, default=None, help="Override leader count.")
    mv_p.add_argument("--laggards", type=int, default=None, help="Override laggard count.")
    mv_p.add_argument("--group-field", default=None)
    mv_p.add_argument("--cap", type=int, default=None)
    mv_p.add_argument("--id-field", default=None)
    mv_p.add_argument("--keep", default=None)
    mv_p.add_argument(
        "--keep-duplicate-suffixes",
        action="store_true",
        help="Keep TICKER23 listing leaks. Default drops them when the stem ticker is in the universe.",
    )
    mv_p.add_argument("--out", default=None)
    mv_p.add_argument("--out-dir", default=None, help="Write CSV + movers.duckdb + overview.log")
    mv_p.add_argument("--include-rows", action="store_true")
    mv_p.add_argument("--quiet", action="store_true")
    mv_p.set_defaults(func=_cmd_movers)

    setup_p = sub.add_parser(
        "setup",
        help="Primary value / efficiency / peer + technical structure + street/TV fwd_*. Not a 0-100. Does not quote EV/Rev when a native earnings/cash/book multiple exists.",
    )
    _add_source_flags(setup_p)
    setup_p.add_argument("--pack", default=None, help="briefing_pack.json names (watch set; peer medians may be THIN).")
    setup_p.add_argument("--sleeve", default="names", choices=("names", "book", "us2b"))
    setup_p.add_argument("--peer-csv", default=None, help="US $2B (or other) rows for industry medians.")
    setup_p.add_argument("--peer-mcap", type=float, default=None)
    setup_p.add_argument("--recipe", default="config/generic_utils/value_tech.json")
    setup_p.add_argument("--forward", action="store_true", help="Also join financial_projection price/growth dumps.")
    setup_p.add_argument("--forward-recipe", default="config/generic_utils/forward_value.json")
    setup_p.add_argument("--finproj-db", default=None, help="finproj_*.duckdb (default: latest).")
    setup_p.add_argument("--fingrowth-db", default=None, help="fingrowth_*.duckdb (default: latest).")
    setup_p.add_argument("--no-finproj", action="store_true")
    setup_p.add_argument("--cover", type=int, default=None, help="Output size (max 1000). No industry cap.")
    setup_p.add_argument("--rank-field", default="left")
    setup_p.add_argument("--out", default=None)
    setup_p.add_argument("--out-dir", default=None)
    setup_p.add_argument("--include-rows", action="store_true")
    setup_p.add_argument("--quiet", action="store_true")
    setup_p.set_defaults(func=_cmd_setup)

    fwd_p = sub.add_parser(
        "forward",
        help="Street/TV leftover targets + financial_projection terminals/CAGRs. Not a 0-100. Cover max 1000, no industry cap.",
    )
    _add_source_flags(fwd_p)
    fwd_p.add_argument("--pack", default=None)
    fwd_p.add_argument("--sleeve", default="us2b", choices=("names", "book", "us2b"))
    fwd_p.add_argument("--peer-csv", default=None)
    fwd_p.add_argument("--peer-mcap", type=float, default=None)
    fwd_p.add_argument("--recipe", default="config/generic_utils/forward_value.json")
    fwd_p.add_argument("--setup-recipe", default="config/generic_utils/value_tech.json")
    fwd_p.add_argument("--no-setup", action="store_true", help="Skip val_field / peer / tech attach.")
    fwd_p.add_argument("--finproj-db", default=None)
    fwd_p.add_argument("--fingrowth-db", default=None)
    fwd_p.add_argument("--no-finproj", action="store_true", help="Street/TV columns only.")
    fwd_p.add_argument("--cover", type=int, default=1000, help="Output size (default 1000, max 1000). No industry cap.")
    fwd_p.add_argument("--rank-field", default="left")
    fwd_p.add_argument("--out", default=None)
    fwd_p.add_argument("--out-dir", default=None)
    fwd_p.add_argument("--include-rows", action="store_true")
    fwd_p.add_argument("--quiet", action="store_true")
    fwd_p.set_defaults(func=_cmd_forward)

    risk_p = sub.add_parser(
        "risk",
        help="Book NAV + loss ladders (position %% / $ / %% of NAV). No trim/EXIT cutoff.",
    )
    risk_p.add_argument(
        "--holdings",
        default="config/holdings_scoring/current_holdings.json",
        help="current_holdings.json (cash + cost). FX marks need --run.",
    )
    risk_p.add_argument(
        "--run",
        default="latest",
        help="holdings_scoring run directory, or 'latest'.",
    )
    risk_p.add_argument("--summary", default=None, help="holdings__summary.csv override.")
    risk_p.add_argument(
        "--config-only",
        action="store_true",
        help="Skip holdings scan; cost-only from --holdings (USD-naive on FX names).",
    )
    risk_p.add_argument("--cash", type=float, default=None, help="Override cash USD.")
    risk_p.add_argument(
        "--pack",
        default=None,
        help="briefing_pack.json to join leftover / vs50 / rsi / ATR.",
    )
    risk_p.add_argument(
        "--recipe",
        default="config/generic_utils/book_risk.json",
    )
    risk_p.add_argument("--out", default=None)
    risk_p.add_argument(
        "--out-dir",
        default=None,
        help="Scan folder: CSV + book_risk.md + book_risk.duckdb + overview.log. Default: logs/.../book_risk_YYYYMMDD_HHMM_utc/",
    )
    risk_p.add_argument("--include-rows", action="store_true")
    risk_p.add_argument("--quiet", action="store_true")
    risk_p.set_defaults(func=_cmd_risk)

    cap_p = sub.add_parser(
        "capture",
        help="Whole-book capture path: holdings membership + fundamentals + prediction progression.",
    )
    cap_p.add_argument("--pack", default=None, help="briefing_pack.json (optional join layer).")
    cap_p.add_argument("--recipe", default="config/generic_utils/capture.json")
    cap_p.add_argument("--lookback-days", type=int, default=None)
    cap_p.add_argument(
        "--af-start",
        default=None,
        help="Window start day label (DD_MM_YYYY or YYYY-MM-DD).",
    )
    cap_p.add_argument(
        "--af-end",
        default=None,
        help="Window end day label (DD_MM_YYYY or YYYY-MM-DD).",
    )
    cap_p.add_argument("--holdings-runs-root", default=None)
    cap_p.add_argument("--all-fields-root", default=None)
    cap_p.add_argument("--prediction-root", default=None)
    cap_p.add_argument("--out", default=None, help="Optional jobs CSV path.")
    cap_p.add_argument(
        "--out-dir",
        default=None,
        help="Run folder with capture.duckdb + CSV tables + capture.md + overview.log.",
    )
    cap_p.add_argument("--include-rows", action="store_true")
    cap_p.add_argument("--quiet", action="store_true")
    cap_p.set_defaults(func=_cmd_capture)

    capr_p = sub.add_parser(
        "capture-replay",
        help="Walk-forward replay of capture job tags vs later closes on an existing dump (or rebuild a window).",
    )
    capr_p.add_argument("--capture-dir", default=None, help="Existing capture dump folder (default: latest).")
    capr_p.add_argument("--pack", default=None)
    capr_p.add_argument("--recipe", default="config/generic_utils/capture.json")
    capr_p.add_argument("--lookback-days", type=int, default=None)
    capr_p.add_argument("--af-start", default=None)
    capr_p.add_argument("--af-end", default=None)
    capr_p.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild the capture dump before replaying (needed for a new window).",
    )
    capr_p.add_argument("--horizons", default=None, help="Comma steps along fund_path (default 5,10,21,60).")
    capr_p.add_argument("--out-dir", default=None, help="Default: CAPTURE_DIR/replay")
    capr_p.add_argument("--include-rows", action="store_true")
    capr_p.add_argument("--quiet", action="store_true")
    capr_p.set_defaults(func=_cmd_capture_replay)

    briefs_p = sub.add_parser(
        "briefs",
        help="Score prior operator briefs vs later pack closes. Digest omits notes.",
    )
    briefs_p.add_argument("--priors", default=None, help="Folder of *_operator_actions.json.")
    briefs_p.add_argument("--runs", default=None, help="operator_briefing/runs with briefing_pack_* folders.")
    briefs_p.add_argument("--latest-pack", default=None, help="Pack folder or briefing_pack.duckdb. Default: newest pack.")
    briefs_p.add_argument("--recipe", default=None, help="Band file. Default config/generic_utils/brief_history.json.")
    briefs_p.add_argument("--max-names", type=int, default=None, help="Names on the digest page. Default from the recipe.")
    briefs_p.add_argument("--note-chars", type=int, default=None, help="Chars of note kept on events. Default 0.")
    briefs_p.add_argument("--out-dir", default=None)
    briefs_p.add_argument("--include-rows", action="store_true")
    briefs_p.add_argument("--quiet", action="store_true")
    briefs_p.set_defaults(func=_cmd_briefs)

    entry_p = sub.add_parser(
        "entry-book",
        help="Buy band and thesis-kill prices from a catalog CSV. No ADD/WAIT cutoff.",
    )
    entry_p.add_argument("--csv", required=True, help="Catalog or tape CSV with close and vs50.")
    entry_p.add_argument("--recipe", default=None, help="Default config/generic_utils/entry_book.json.")
    entry_p.add_argument("--mark-csv", default=None, help="Later closes. Does not move the band.")
    entry_p.add_argument("--mark-field", default="close", help="Close column on --mark-csv.")
    entry_p.add_argument("--source-note", default=None)
    entry_p.add_argument("--out-dir", required=True)
    entry_p.add_argument("--quiet", action="store_true")
    entry_p.set_defaults(func=_cmd_entry_book)

    nl_p = sub.add_parser(
        "name-levels",
        help="One call: tape row -> entry book -> GMV lens -> day history -> safety band for named ids or a theme.",
    )
    nl_p.add_argument("--pack", default="latest")
    nl_p.add_argument("--ids", default=None, help="Comma ids (bare or EXCHANGE:TICKER).")
    nl_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    nl_p.add_argument("--out-dir", default=None, help="Default PACK/analysis_scans/name_levels.")
    nl_p.add_argument("--source-note", default=None)
    nl_p.add_argument("--quiet", action="store_true", help="Print one summary line.")
    nl_p.set_defaults(func=_cmd_name_levels)

    ts_p = sub.add_parser("theme-slice", help="GMV lens rows in a theme's industries, ordered by a lens floor with an industry cap.")
    ts_p.add_argument("--pack", default="latest")
    ts_p.add_argument("--theme", required=True, help="Theme with `industries` in config/generic_utils/themes.json.")
    ts_p.add_argument("--field", default="rerate_floor")
    ts_p.add_argument("--top", type=int, default=30)
    ts_p.add_argument("--cap", type=int, default=4)
    ts_p.add_argument("--out", default=None, help="Default PACK/analysis_scans/theme_slice.csv.")
    ts_p.add_argument("--line", action="store_true", help="Also print one compact line per row.")
    ts_p.set_defaults(func=_cmd_theme_slice)

    ew_p = sub.add_parser("earnings-week", help="Every print this week and next: calendar slot, tape into the print, estimates, value, past reactions, suite joins.")
    ew_p.add_argument("--pack", default="latest")
    ew_p.add_argument("--db", default=None, help="All-fields day file (default: the newest).")
    ew_p.add_argument("--ids", default=None, help="Comma ids always kept (bare tickers resolve inside the recipe floor).")
    ew_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    ew_p.add_argument("--weeks", type=int, default=None, help="Weeks from this Monday (recipe default 2).")
    ew_p.add_argument("--min-mcap", type=float, default=None, help="USD floor (recipe default 1e9).")
    ew_p.add_argument("--markets", default=None, help="Comma TradingView markets (recipe default US + Europe).")
    ew_p.add_argument("--recipe", default=None, help="Default config/generic_utils/earnings_week.json.")
    ew_p.add_argument("--out-dir", default=None, help="Default PACK/earnings_week.")
    ew_p.add_argument("--line", action="store_true", help="One line per named id.")
    ew_p.add_argument("--quiet", action="store_true", help="Print one summary line.")
    ew_p.set_defaults(func=_cmd_earnings_week)

    wp_p = sub.add_parser("week-plays", help="Earnings-week names and the Book with the day's suites side by side: calendar, alpha deciles and bands, entry band / kill, signal-lab print cells.")
    wp_p.add_argument("--pack", default="latest")
    wp_p.add_argument("--ids", default=None, help="Comma ids (bare tickers resolve); default every calendar name.")
    wp_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    wp_p.add_argument("--db", default=None, help="All-fields day file for Book fundamentals (default the newest).")
    wp_p.add_argument("--recipe", default=None, help="Default config/generic_utils/week_plays.json.")
    wp_p.add_argument("--out-dir", default=None, help="Default PACK/week_plays.")
    wp_p.add_argument("--line", action="store_true", help="One line per play row (the --ids rows when given).")
    wp_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    wp_p.set_defaults(func=_cmd_week_plays)

    dt_p = sub.add_parser("deterioration", help="Drawdown x deterioration study on the panel store (what a drawdown did next, split by estimate / target / trend / cash letters) and every name's state now, with the Book's cell dollars.")
    dt_p.add_argument("--pack", default="latest")
    dt_p.add_argument("--ids", default=None, help="Comma ids kept in whatever the floor; bare tickers resolve.")
    dt_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    dt_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    dt_p.add_argument("--recipe", default=None, help="Default config/generic_utils/deterioration.json.")
    dt_p.add_argument("--first-day", default=None, help="YYYY-MM-DD: backward cells from this store day (a regime check).")
    dt_p.add_argument("--out-dir", default=None, help="Default PACK/deterioration.")
    dt_p.add_argument("--line", action="store_true", help="One line per Book / named row plus the summary.")
    dt_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    dt_p.set_defaults(func=_cmd_deterioration)

    rl_p = sub.add_parser("recovery", help="Recovery ladder per Book line: average cost after an add at the close / bounce band, the rise back to break even and to a gain, the add that makes the 50-day / half-way / street price break the line even, and the add's loss at the kill.")
    rl_p.add_argument("--pack", default="latest")
    rl_p.add_argument("--ids", default=None, help="Comma Book ids (default every Book line); bare tickers resolve.")
    rl_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    rl_p.add_argument("--recipe", default=None, help="Default config/generic_utils/recovery_ladder.json.")
    rl_p.add_argument("--out-dir", default=None, help="Default PACK/recovery_ladder.")
    rl_p.add_argument("--line", action="store_true", help="One line per Book line plus the summary.")
    rl_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    rl_p.set_defaults(func=_cmd_recovery)

    rc_p = sub.add_parser("recovery-clock", help="Recovery clock: how long each drawdown state took to regain a rise, half the gap and the high on the panel store (Kaplan-Meier, censored), how far it fell first, and each Book line's odds and median days to its cost, the 50-day and half-way.")
    rc_p.add_argument("--pack", default="latest")
    rc_p.add_argument("--ids", default=None, help="Comma ids read beside the Book; bare tickers resolve.")
    rc_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    rc_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    rc_p.add_argument("--recipe", default=None, help="Default config/generic_utils/recovery_clock.json.")
    rc_p.add_argument("--out-dir", default=None, help="Default PACK/recovery_clock.")
    rc_p.add_argument("--line", action="store_true", help="One line per Book / named line plus the summary.")
    rc_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    rc_p.set_defaults(func=_cmd_recovery_clock)

    cy_p = sub.add_parser("cycle", help="Cycle scenarios: k more boom years after the street's year 1, then an EPS drop d from the peak and a recovery to a normalized level, valued on owner cash and a through-cycle exit P/E; the drop each price already pays for and the boom years it needs; optional per-line price shocks in Book dollars.")
    cy_p.add_argument("--pack", default="latest")
    cy_p.add_argument("--ids", default=None, help="Comma ids read beside the Book; bare tickers resolve (US listings first).")
    cy_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    cy_p.add_argument("--value-csv", default=None, help="value_names.csv of a finvalue run (default the newest).")
    cy_p.add_argument("--shocks", default=None, help="CSV: symbol, optional role / note, one column per scenario of percent price moves.")
    cy_p.add_argument("--recipe", default=None, help="Default config/generic_utils/cycle_scenarios.json.")
    cy_p.add_argument("--out-dir", default=None, help="Default PACK/cycle_scenarios.")
    cy_p.add_argument("--line", action="store_true", help="One line per name plus the stress totals and the summary.")
    cy_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    cy_p.set_defaults(func=_cmd_cycle)

    pv_p = sub.add_parser("peer-value", help="Peer value: each name's multiple against its industry median and against what the industry pays for the same growth and quality (ridge fit per group), percentiles and spreads to peers, PEG and priced-in growth vs the group, nearest peers, and whether the peer gap paid on the store's forward returns.")
    pv_p.add_argument("--pack", default="latest")
    pv_p.add_argument("--ids", default=None, help="Comma ids read beside the Book; bare tickers resolve.")
    pv_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    pv_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    pv_p.add_argument("--recipe", default=None, help="Default config/generic_utils/peer_value.json.")
    pv_p.add_argument("--no-evidence", action="store_true", help="Skip the backtest on the store's labels.")
    pv_p.add_argument("--out-dir", default=None, help="Default PACK/peer_value.")
    pv_p.add_argument("--line", action="store_true", help="One line per Book / named name, the evidence lines and the summary.")
    pv_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    pv_p.set_defaults(func=_cmd_peer_value)

    ob_p = sub.add_parser("board", help="Opportunity board: one row per name (the outlook names) with peer value, deterioration, the beat bar, forward value, pattern letters and store beta joined, the close against the bounce band and the beat-bar cell per axis; column groups for grids and screens.")
    ob_p.add_argument("--pack", default="latest")
    ob_p.add_argument("--ids", default=None, help="Comma ids kept on the board; bare tickers resolve.")
    ob_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    ob_p.add_argument("--recipe", default=None, help="Default config/generic_utils/opportunity_board.json.")
    ob_p.add_argument("--out-dir", default=None, help="Default PACK/board.")
    ob_p.add_argument("--line", action="store_true", help="One line per Book / named name and the summary.")
    ob_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    ob_p.set_defaults(func=_cmd_board)

    lb_p = sub.add_parser("lane-book", help="Lane book: lanes of opportunity (one SQL condition each, recipe lane_book.json) tested on the panel store (raw / excess per horizon, odds of a +/-10% move, block t, halves, months) and read on today's names with band, kill, targets and catalysts; value quintile x catalyst timing; industry rotation quadrants with their record; the dated timeline by week; the cash above the floor.")
    lb_p.add_argument("--pack", default="latest")
    lb_p.add_argument("--ids", default=None, help="Comma ids always kept on the names table; bare tickers resolve.")
    lb_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    lb_p.add_argument("--recipe", default=None, help="Default config/generic_utils/lane_book.json.")
    lb_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    lb_p.add_argument("--calendar", default=None, help="Default config/generic_utils/catalyst_calendar.json.")
    lb_p.add_argument("--out-dir", default=None, help="Default PACK/lane_book.")
    lb_p.add_argument("--line", action="store_true", help="One line per lane (split all) and per Book / named name, plus the summary.")
    lb_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    lb_p.set_defaults(func=_cmd_lane_book)

    dn_p = sub.add_parser("downside", help="Downside risk: the store's cap-weighted market, median name, cluster and today's Book (today's weights) step by step, their windows, worst drawdowns and the Book lines replayed through them, betas per line and the Book's loss for each market move.")
    dn_p.add_argument("--pack", default="latest")
    dn_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    dn_p.add_argument("--recipe", default=None, help="Default config/generic_utils/downside_risk.json.")
    dn_p.add_argument("--out-dir", default=None, help="Default PACK/downside.")
    dn_p.add_argument("--line", action="store_true", help="One line per Book line, scenario, cap / Book horizon and episode, plus the summary.")
    dn_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    dn_p.set_defaults(func=_cmd_downside)

    er_p = sub.add_parser("event-risk", help="Event risk: every floor name's crowd (its most-correlated names on the store), the crowd's move, worst moves and the stress on each Book line, dated calendar events touching the line (mapped, binary, already moved), letters EPRUSBTVD$ and a WARN level per line; sell / buy candidate lists, event reactions per industry, crash follow-through and the letters' point-in-time evidence.")
    er_p.add_argument("--pack", default="latest")
    er_p.add_argument("--ids", default="", help="Extra EXCHANGE:TICKER ids to put on the book table (no dollars).")
    er_p.add_argument("--as-of", default=None, help="YYYY-MM-DD: cut the store at this file (point-in-time check; Book weights stay today's).")
    er_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    er_p.add_argument("--recipe", default=None, help="Default config/generic_utils/event_risk.json.")
    er_p.add_argument("--out-dir", default=None, help="Default PACK/event_risk.")
    er_p.add_argument("--line", action="store_true", help="One line per Book line, crash follow-through horizon and 28d evidence state, plus the summary.")
    er_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    er_p.set_defaults(func=_cmd_event_risk)

    nd_p = sub.add_parser("dossier", help="Name dossier (the deep-dive agent's data) on named ids (or a themes.json theme): the pack's widest row per name plus forward value, alpha bands and state, peers, cycle, event risk, print night, stance; the store's daily progression, movement anatomy, prints, analog forward paths (raw and excess, all floor and same industry), evidence directions per horizon, a research plan for the agent's outside read. No ranking, no score.")
    nd_p.add_argument("--ids", default="", help="EXCHANGE:TICKER ids (bare tickers are qualified on the newest all-fields run).")
    nd_p.add_argument("--theme", default=None, help="Add the ids of this config/generic_utils/themes.json theme.")
    nd_p.add_argument("--question", default=None, help="A question the agent answers on top of the names (e.g. how trading an IPO would work).")
    nd_p.add_argument("--label", default=None, help="Folder / page label (default from the question or the tickers).")
    nd_p.add_argument("--pack", default="latest")
    nd_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    nd_p.add_argument("--recipe", default=None, help="Default config/generic_utils/name_dossier.json.")
    nd_p.add_argument("--out-dir", default=None, help="Default logs/tradingview_analysis/name_dossier/<YYYYMMDD_HHMM>_<label>.")
    nd_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    nd_p.set_defaults(func=_cmd_dossier)

    ndp_p = sub.add_parser("dossier-page", help="Render a dossier folder's page from its tables and the agent's judgment (dossier.judgment.json).")
    ndp_p.add_argument("--dir", required=True, help="The dossier folder (dossier.duckdb, spec.json).")
    ndp_p.add_argument("--judgment", default=None, help="Judgment JSON (default DIR/dossier.judgment.json when it exists; none = data-only page).")
    ndp_p.add_argument("--out-dir", default=None, help="Where the page goes (default the dossier folder).")
    ndp_p.add_argument("--strict", action="store_true", help="Fail on any page warning.")
    ndp_p.set_defaults(func=_cmd_dossier_page)

    sc_p = sub.add_parser("symbol-check", help="Where each held / named id is found (generic_utils.symbol_resolver): alias map (renames, exchange moves), the newest all-fields run, the panel store's last day; ids that look old (missing from the newest all-fields run, or stale in the store) with candidates (same company under another id, same ticker on another US exchange). Reports, never rewrites.")
    sc_p.add_argument("--ids", default="", help="Ids to check (EXCHANGE:TICKER or bare). Default: every line of the holdings config.")
    sc_p.add_argument("--holdings", default=None, help="Holdings config (default config/holdings_scoring/current_holdings.json).")
    sc_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    sc_p.add_argument("--stale-days", type=int, default=5, help="Store's last day this many days before its newest file = stale.")
    sc_p.add_argument("--out", default=None, help="Write every row to this CSV.")
    sc_p.add_argument("--flagged", action="store_true", help="Print only ids that look old or were aliased.")
    sc_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    sc_p.set_defaults(func=_cmd_symbol_check)

    se_p = sub.add_parser("sentiment", help="Market sentiment: the store's daily breadth, volume, stretch, appetite, earnings-reaction, revision and model readings (plus MTP climate and dated OUTSIDE readings), each against its history and the cap index's forward return; named technical patterns. No composite.")
    se_p.add_argument("--pack", default="latest", help="Pack whose sentiment folder is the default output.")
    se_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    se_p.add_argument("--recipe", default=None, help="Default config/generic_utils/market_sentiment.json.")
    se_p.add_argument("--out-dir", default=None, help="Default PACK/sentiment.")
    se_p.add_argument("--line", action="store_true", help="One line per reading, pattern and OUTSIDE row, the highlights, then the summary.")
    se_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    se_p.set_defaults(func=_cmd_sentiment)

    sa_p = sub.add_parser("sentiment-add", help="Append one dated OUTSIDE sentiment reading (fear / greed, AAII, VIX, put / call, prediction market) to config/generic_utils/sentiment_outside.json.")
    sa_p.add_argument("--kind", required=True, help="Short key, e.g. cnn_fear_greed, aaii_bull_bear, vix_close, put_call, polymarket_dem_sweep.")
    sa_p.add_argument("--value", required=True)
    sa_p.add_argument("--date", default=None, help="YYYY-MM-DD the reading is for (default today).")
    sa_p.add_argument("--unit", default=None)
    sa_p.add_argument("--source", default=None)
    sa_p.add_argument("--link", default=None)
    sa_p.add_argument("--read", default=None, help="One line: what the reading measures.")
    sa_p.add_argument("--file", default=None, help="Default the recipe's outside_file.")
    sa_p.add_argument("--recipe", default=None)
    sa_p.set_defaults(func=_cmd_sentiment_add)

    pw_p = sub.add_parser("pathway",help="Investing pathway: the Book's factor tilt vs what each factor paid (signal-lab IC, this month, the trailing tape), 1M / 3M return split into market / industry / stock, the regime series, and each trim's or starter's effect on the tilt.")
    pw_p.add_argument("--pack", default="latest")
    pw_p.add_argument("--ids", default=None, help="Comma ids kept among the candidates whatever the floor; bare tickers resolve.")
    pw_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    pw_p.add_argument("--store", default=None, help="Panel store DuckDB (panel_store.json path by default).")
    pw_p.add_argument("--recipe", default=None, help="Default config/generic_utils/pathway.json.")
    pw_p.add_argument("--out-dir", default=None, help="Default PACK/pathway.")
    pw_p.add_argument("--line", action="store_true", help="One line per factor and per attribution horizon plus the summary.")
    pw_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    pw_p.set_defaults(func=_cmd_pathway)

    ol_p = sub.add_parser("outlook", help="Catalyst outlook: every US name on the floor with its catalysts over the horizon (earnings + slot, ex-dividend, IPO lockup, dated OUTSIDE events) and the day's suites, columns grouped technical / setup / surprise / value / progression.")
    ol_p.add_argument("--pack", default="latest")
    ol_p.add_argument("--ids", default=None, help="Comma ids kept in (and priced) whatever the floor; bare tickers resolve.")
    ol_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    ol_p.add_argument("--db", default=None, help="All-fields day file (default the newest).")
    ol_p.add_argument("--recipe", default=None, help="Default config/generic_utils/catalyst_outlook.json.")
    ol_p.add_argument("--out-dir", default=None, help="Default PACK/outlook.")
    ol_p.add_argument("--line", action="store_true", help="One line per --ids row plus the summary.")
    ol_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    ol_p.set_defaults(func=_cmd_outlook)

    pn_p = sub.add_parser("print-night", help="Print night: every name whose print reaction lands on one session (default the next: tonight's AMC and tomorrow's BMO) with its measured pointers: the quintile it sits in per pre-print field, what that quintile did on past prints (reaction, hold-through) and the arrow where the lift is clear; the post-print digest once printed.")
    pn_p.add_argument("--pack", default="latest")
    pn_p.add_argument("--react-on", default=None, help="Reaction session YYYY-MM-DD, 'today' (digest the prints reacting on the scan day) or 'next' (default).")
    pn_p.add_argument("--sessions", type=int, default=1, help="Reaction sessions from --react-on on (default 1; 5 = the rest of the week).")
    pn_p.add_argument("--ids", default=None, help="Comma ids added to the session's names (a name printing on another day); with --line, the rows printed. Bare tickers resolve.")
    pn_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    pn_p.add_argument("--store", default=None, help="Panel store DuckDB (recipe path by default).")
    pn_p.add_argument("--recipe", default=None, help="Default config/generic_utils/print_night.json.")
    pn_p.add_argument("--out-dir", default=None, help="Default PACK/print_night.")
    pn_p.add_argument("--line", action="store_true", help="One line per name (or per --ids row) plus the summary.")
    pn_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    pn_p.set_defaults(func=_cmd_print_night)

    bb_p = sub.add_parser("beat-bar", help="Beat bar: for every reporter of this week and next (week-plays, before the print), what a beat and a miss paid on past prints set up like it (terciles of run-up, the 3-month tape and the 3-month EPS revision), the share of beats sold, the surprise quintile the set-up needed (the bar), the name's own beat record, and the beat / miss reads mixed by p_beat. Pointer cells split beats by one pre-print field's quintile.")
    bb_p.add_argument("--pack", default="latest")
    bb_p.add_argument("--weeks", default=None, help="Comma week codes from week-plays (default the recipe's W0,W1).")
    bb_p.add_argument("--ids", default=None, help="Comma ids added (any week-plays row); with --line, the rows printed. Bare tickers resolve.")
    bb_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    bb_p.add_argument("--store", default=None, help="Panel store DuckDB (recipe path by default).")
    bb_p.add_argument("--recipe", default=None, help="Default config/generic_utils/beat_bar.json.")
    bb_p.add_argument("--out-dir", default=None, help="Default PACK/beat_bar.")
    bb_p.add_argument("--line", action="store_true", help="One line per name (or per --ids row) plus the summary.")
    bb_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    bb_p.set_defaults(func=_cmd_beat_bar)

    pb_p = sub.add_parser("print-brief", help="Print brief: one card per print-night reporter (PACK/print_night): a short written read from the pack's numbers (into the print, value to peers and its own history, what the quarter has to show, the industry's prints this season, the pointers, the levels) and what to read on the print.")
    pb_p.add_argument("--pack", default="latest")
    pb_p.add_argument("--ids", default=None, help="Comma ids (bare tickers match): print their headlines (--line: every text).")
    pb_p.add_argument("--recipe", default=None, help="Default config/generic_utils/print_brief.json.")
    pb_p.add_argument("--out-dir", default=None, help="Default PACK/print_brief.")
    pb_p.add_argument("--line", action="store_true", help="With --ids, every text of each named card.")
    pb_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    pb_p.set_defaults(func=_cmd_print_brief)

    pr_p = sub.add_parser("play-review", help="A prior day's judgment courses and published levels against a later pack: ret vs the tape, band / kill state on opens and closes, print state now.")
    pr_p.add_argument("--pack", default="latest", help="The later pack (default newest).")
    pr_p.add_argument("--judgment", default=None, help="Prior judgment.json (default the newest logs/canvases/<day>/judgment.json before the pack's scan day).")
    pr_p.add_argument("--before", default=None, help="YYYYMMDD: pick the newest judgment strictly before this day instead of the pack's scan day.")
    pr_p.add_argument("--prior-pack", default=None, help="Default the judgment's reviewed_pack / pack.")
    pr_p.add_argument("--ids", default=None, help="Comma ids (bare tickers match); default every prior course.")
    pr_p.add_argument("--theme", default=None, help="Comma theme names from config/generic_utils/themes.json.")
    pr_p.add_argument("--out-dir", default=None, help="Default PACK/play_review.")
    pr_p.add_argument("--line", action="store_true", help="One line per week-plays name (or --ids row) plus the by-course summary.")
    pr_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    pr_p.set_defaults(func=_cmd_play_review)

    ps_p = sub.add_parser("panel-store", help="Append new all-fields day files (and their prediction runs) to the point-in-time panel store; rebuild prints, labels, features.")
    ps_p.add_argument("--recipe", default=None, help="Default config/generic_utils/panel_store.json.")
    ps_p.add_argument("--store", default=None, help="Store DuckDB (recipe path by default).")
    ps_p.add_argument("--root", default=None, help="All-fields root (recipe default).")
    ps_p.add_argument("--rebuild", action="store_true", help="Re-ingest every day file.")
    ps_p.add_argument("--no-derived", action="store_true", help="Ingest only; leave series / prints / labels / features.")
    ps_p.add_argument("--derived", action="store_true", help="Rebuild series / prints / labels / features even when nothing new was ingested (after a measure change).")
    ps_p.add_argument("--no-predictions", action="store_true", help="Skip joining move-prediction runs.")
    ps_p.add_argument("--quiet", action="store_true", help="Print the summary line only.")
    ps_p.set_defaults(func=_cmd_panel_store)

    sl_p = sub.add_parser("signal-lab", help="Backward evidence on the panel store: feature IC by horizon, print event study, briefing-pack list returns.")
    sl_p.add_argument("--pack", default="latest", help="Output beside a pack (PACK/signal_lab); 'none' for a stamped run folder.")
    sl_p.add_argument("--out-dir", default=None)
    sl_p.add_argument("--studies", default=None, help="Comma of factor,prints,scans (default all).")
    sl_p.add_argument("--horizons", default=None, help="Comma horizon names from panel_store.json (default the lab recipe's).")
    sl_p.add_argument("--features", default=None, help="Comma feature names (default all).")
    sl_p.add_argument("--min-mcap", type=float, default=None, help="Study floor in USD (recipe default 1e9).")
    sl_p.add_argument("--first-day", default=None)
    sl_p.add_argument("--last-day", default=None)
    sl_p.add_argument("--recipe", default=None, help="Default config/generic_utils/signal_lab.json.")
    sl_p.add_argument("--store-recipe", default=None)
    sl_p.add_argument("--store", default=None)
    sl_p.add_argument("--no-update", action="store_true", help="Read the store as it is (the refresh producer already updated it).")
    sl_p.add_argument("--line", action="store_true", help="Print the highlights digest.")
    sl_p.add_argument("--quiet", action="store_true")
    sl_p.set_defaults(func=_cmd_signal_lab)

    an_p = sub.add_parser("anatomy", help="Winners anatomy on the panel store: what the top / bottom tail of later movers looked like before (single legs, pairs of extreme legs), a hold-out read on the last window, the catalyst mix, today's names on the stable patterns.")
    an_p.add_argument("--pack", default="latest", help="Output beside a pack (PACK/anatomy); 'none' for a stamped run folder.")
    an_p.add_argument("--out-dir", default=None)
    an_p.add_argument("--horizons", default=None, help="Comma horizon names (default the recipe's 7d,14d,28d).")
    an_p.add_argument("--tail-pct", type=float, default=None, help="Tail share per day (recipe default 5).")
    an_p.add_argument("--test", action="append", default=None,
                      help="A named pattern to test (repeatable): legs feat:lo|hi|qN or feat<=x joined by '&', e.g. 'r_1m:lo & pt_up:hi & dte<=28'.")
    an_p.add_argument("--first-day", default=None)
    an_p.add_argument("--last-day", default=None)
    an_p.add_argument("--recipe", default=None, help="Default config/generic_utils/anatomy.json.")
    an_p.add_argument("--store-recipe", default=None)
    an_p.add_argument("--store", default=None)
    an_p.add_argument("--line", action="store_true", help="Print the highlights digest.")
    an_p.add_argument("--quiet", action="store_true")
    an_p.set_defaults(func=_cmd_anatomy)

    am_p = sub.add_parser("alpha-model", help="Walk-forward excess-return forecast per horizon with out-of-sample track record, baselines, deciles and price bands.")
    am_p.add_argument("--pack", default="latest", help="Joins and output beside a pack (PACK/alpha_model); 'none' for a stamped run folder.")
    am_p.add_argument("--out-dir", default=None)
    am_p.add_argument("--horizons", default=None, help="Comma horizon names (default the model recipe's).")
    am_p.add_argument("--ids", default=None, help="Comma ids: one line each (bare tickers resolve).")
    am_p.add_argument("--recipe", default=None, help="Default config/generic_utils/alpha_model.json.")
    am_p.add_argument("--store-recipe", default=None)
    am_p.add_argument("--store", default=None)
    am_p.add_argument("--no-update", action="store_true", help="Read the store as it is (the refresh producer already updated it).")
    am_p.add_argument("--no-log", action="store_true", help="Do not append today's forecasts to the live log.")
    am_p.add_argument("--line", action="store_true", help="Print the highlights digest.")
    am_p.add_argument("--quiet", action="store_true")
    am_p.set_defaults(func=_cmd_alpha_model)

    sk_p = sub.add_parser("judgment-skeleton", help="Day judgment skeleton: template block ids with shapes, carried courses and scans.")
    sk_p.add_argument("--day", required=True)
    sk_p.add_argument("--pack", default="latest")
    sk_p.add_argument("--carry-from", default=None, help="Prior day YYYYMMDD (its live judgment) or a judgment .json path.")
    sk_p.add_argument("--templates", default=None, help="Comma template names (default: the --set from daily_refresh.json).")
    sk_p.add_argument("--set", choices=["core", "all"], default="core", help="core = brief + board + movers; all = every page's blocks.")
    sk_p.add_argument("--out", default=None, help="Default logs/canvases/<day>/judgment.json.")
    sk_p.add_argument("--force", action="store_true")
    sk_p.set_defaults(func=_cmd_judgment_skeleton)

    pa_p = sub.add_parser("page-all", help="Render every day template (+ extras) with one judgment; one line per page.")
    pa_p.add_argument("--day", required=True)
    pa_p.add_argument("--pack", default="latest")
    pa_p.add_argument("--judgment", default=None, help="Omit for the data-only pages.")
    pa_p.add_argument("--out-dir", default=None, help="Run folder (default: the open run, else the day folder).")
    pa_p.add_argument("--templates", default=None, help="Comma names (default: the --set from daily_refresh.json).")
    pa_p.add_argument("--set", choices=["core", "all"], default="core", help="core = brief + board + movers (templates); all = every page (templates_all).")
    pa_p.add_argument("--extra", default=None, help="Comma extra template paths (run-local templates).")
    pa_p.add_argument("--ledger", default=None)
    pa_p.add_argument("--show", type=int, default=3, help="Warnings shown per page.")
    pa_p.add_argument("--strict", action="store_true")
    pa_p.set_defaults(func=_cmd_page_all)

    dd_p = sub.add_parser("deep-dive", help="Deep-dive page for themes / ids: name-levels, then a template per group, rendered.")
    dd_p.add_argument("--pack", default="latest")
    dd_p.add_argument("--day", required=True)
    dd_p.add_argument("--theme", default=None, help="Comma theme names (one tab each).")
    dd_p.add_argument("--ids", default=None, help="Comma ids for an extra group.")
    dd_p.add_argument("--ids-label", default="Watch")
    dd_p.add_argument("--levels-dir", default=None, help="Reuse an existing name_levels folder instead of running it.")
    dd_p.add_argument("--judgment", default=None, help="Judgment with pages.deepdive blocks and courses (omit for data-only).")
    dd_p.add_argument("--out-dir", required=True, help="Run folder (page-run --mode ide).")
    dd_p.add_argument("--name", default="deep-dive-{DAY}")
    dd_p.add_argument("--title", default="Deep dive {DAY}")
    dd_p.add_argument("--strict", action="store_true")
    dd_p.set_defaults(func=_cmd_deep_dive)

    intra_p = sub.add_parser(
        "intraday",
        help="Drift between two runs of one all-fields day file (early vs later session), per name and per industry. No cutoff.",
    )
    intra_p.add_argument("--db", default=None, help="All-fields day DuckDB (default: the newest day file).")
    intra_p.add_argument("--run-a", default=None, help="Early run id (default: the day's first run).")
    intra_p.add_argument("--run-b", default=None, help="Later run id (default: the day's last run).")
    intra_p.add_argument("--min-mcap", type=float, default=None)
    intra_p.add_argument("--exchanges", default="NASDAQ,NYSE,AMEX", help="Comma list; empty string for every venue.")
    intra_p.add_argument("--ids", default=None, help="Comma ids (EXCHANGE:TICKER or bare).")
    intra_p.add_argument("--flat-pct", type=float, default=0.25)
    intra_p.add_argument("--group-min-n", type=int, default=3)
    intra_p.add_argument("--out-dir", required=True)
    intra_p.add_argument("--quiet", action="store_true")
    intra_p.set_defaults(func=_cmd_intraday)

    safety_p = sub.add_parser(
        "safety-band",
        help="Margin of safety (EPS x own / peer PE), buy-in-red band, kill, add trigger, trim zone. No buy/sell choice.",
    )
    safety_p.add_argument("--csv", required=True, help="Catalog with close, atr or atrp, vs50, street_px (an entry-book CSV adds the reclaim band).")
    safety_p.add_argument("--lens-csv", default=None, help="Rows with pe_nfy and pe_nfy_ind_med (GMV lens_tape via named).")
    safety_p.add_argument("--history-csv", default=None, help="history CSV: symbol, as_of, close, change, price_target_average, earnings_per_share_forecast_next_fy.")
    safety_p.add_argument("--recipe", default=None, help="Default config/generic_utils/safety_band.json.")
    safety_p.add_argument("--source-note", default=None)
    safety_p.add_argument("--out-dir", required=True)
    safety_p.add_argument("--quiet", action="store_true")
    safety_p.set_defaults(func=_cmd_safety_band)

    timing_p = sub.add_parser(
        "timing-levels",
        help="Entry band, timing kill, and thesis-change prices. Not a buy list.",
    )
    timing_p.add_argument("--csv", default=None, help="Tape or catalog CSV.")
    timing_p.add_argument("--db", default=None, help="DuckDB. Use with --table. Not a substitute for the MTP run.")
    timing_p.add_argument("--table", default=None)
    timing_p.add_argument("--recipe", default=None, help="Default config/generic_utils/timing_levels.json.")
    timing_p.add_argument(
        "--screen",
        nargs="?",
        const="low_downside",
        default=None,
        help="Apply a caller where after the prices. Default file is low_downside.json. Pass a path to use another.",
    )
    timing_p.add_argument("--stop-atr", type=float, default=None, help="Kill width in ATR. Default 1.0, or the row's stop_atr_units.")
    timing_p.add_argument("--source-note", default=None)
    timing_p.add_argument("--out-dir", required=True)
    timing_p.add_argument("--quiet", action="store_true")
    timing_p.set_defaults(func=_cmd_timing_levels)

    pay_p = sub.add_parser(
        "payoff-focus",
        help="Ten-name band: discount with momentum, or strength at a premium. Sorted by upside/downside, not a 0-100.",
    )
    pay_p.add_argument("--csv", default=None, help="Tape or catalog CSV with close, vs50, bo, and peer or val_vs_ind.")
    pay_p.add_argument("--db", default=None, help="Pack or suite DuckDB. Use with --table.")
    pay_p.add_argument("--table", default=None, help="Table inside --db. Not all_fields_rows.")
    pay_p.add_argument("--recipe", default=None, help="Default config/generic_utils/payoff_focus.json.")
    pay_p.add_argument("--top", type=int, default=None, help="Focus size. Default 10.")
    pay_p.add_argument("--cap", type=int, default=None, help="Max names per industry. Default 2. -1 turns the cap off.")
    pay_p.add_argument("--out-dir", required=True)
    pay_p.add_argument("--quiet", action="store_true")
    pay_p.set_defaults(func=_cmd_payoff_focus)

    cyc_p = sub.add_parser(
        "book-cycle",
        help="P/B, revenue, net margin, and the window high/low. Labels only. Not a 0-100.",
    )
    cyc_p.add_argument("--tape", required=True, help="Tape CSV. Industry filter uses the recipe list.")
    cyc_p.add_argument("--fund", default=None, help="All-fields rows: net margin, revenue growth, earnings growth.")
    cyc_p.add_argument("--span", default=None, help="series_span CSV with close_min and close_max.")
    cyc_p.add_argument("--peer", default=None, help="Full-tape derive CSV with pb_vs_ind.")
    cyc_p.add_argument("--recipe", default=None, help="Default config/generic_utils/book_cycle.json.")
    cyc_p.add_argument("--source-note", default=None)
    cyc_p.add_argument("--out-dir", required=True)
    cyc_p.add_argument("--quiet", action="store_true")
    cyc_p.set_defaults(func=_cmd_book_cycle)

    sess_p = sub.add_parser(
        "book-sessions",
        help="Holdings membership over a window, optional brief-history join. Not the fund path.",
    )
    sess_p.add_argument("--lookback-days", type=int, default=180)
    sess_p.add_argument("--start", default=None, help="YYYY-MM-DD or DD_MM_YYYY.")
    sess_p.add_argument("--end", default=None)
    sess_p.add_argument("--holdings-runs-root", default=None)
    sess_p.add_argument("--briefs-db", default=None, help="brief_history.duckdb to left-join labels.")
    sess_p.add_argument("--out-dir", default=None)
    sess_p.add_argument("--quiet", action="store_true")
    sess_p.set_defaults(func=_cmd_book_sessions)

    ses_p = sub.add_parser(
        "session",
        help="Join two all-fields scans for names that reported on a date.",
    )
    ses_p.add_argument("--prior-db", required=True)
    ses_p.add_argument("--current-db", required=True)
    ses_p.add_argument("--earn-on", default=None, help="YYYY-MM-DD of the print. Omit when --ids is set.")
    ses_p.add_argument("--prior-run", default=None)
    ses_p.add_argument("--current-run", default=None)
    ses_p.add_argument(
        "--ids",
        default=None,
        help="Comma symbols. Joins premarket for that list and does not use --earn-on.",
    )
    ses_p.add_argument(
        "--exchanges",
        default="NASDAQ,NYSE,AMEX",
        help="Comma list. Pass an empty string for every exchange.",
    )
    ses_p.add_argument("--out-dir", default=None)
    ses_p.add_argument("--include-rows", action="store_true")
    ses_p.add_argument("--quiet", action="store_true")
    ses_p.set_defaults(func=_cmd_session)

    pre_p = sub.add_parser(
        "premarket-book",
        help="Book plus a caller context list, with a premarket mark. Not a compile.",
    )
    pre_p.add_argument("--prior-db", default=None)
    pre_p.add_argument("--current-db", default=None)
    pre_p.add_argument("--recipe", default=None)
    pre_p.add_argument("--out-dir", default=None)
    pre_p.add_argument("--quiet", action="store_true")
    pre_p.set_defaults(func=_cmd_premarket_book)

    exp_p = sub.add_parser(
        "export",
        help="Write named CSVs to a DuckDB + overview.log run folder.",
    )
    exp_p.add_argument("--out-dir", required=True)
    exp_p.add_argument(
        "--table",
        action="append",
        required=True,
        help="Repeatable name=csv_path (e.g. radar=radar.csv).",
    )
    exp_p.add_argument("--source-note", default=None)
    exp_p.add_argument("--note", action="append", default=None)
    exp_p.add_argument("--duckdb-name", default="scan.duckdb")
    exp_p.set_defaults(func=_cmd_export)

    mesh_p = sub.add_parser(
        "mesh",
        help="Mesh letters F C P D T (labels, not a score) on a base list, with the 25-week bo span and forming sleeve.",
    )
    mesh_p.add_argument("--csv", default=None)
    mesh_p.add_argument("--db", default=None)
    mesh_p.add_argument("--table", default=None)
    mesh_p.add_argument("--span-db", default=None, help="weeks_progression.duckdb (table weeks_span).")
    mesh_p.add_argument("--span-table", default="weeks_span")
    mesh_p.add_argument("--forming-db", default=None, help="playbook.duckdb (table forming).")
    mesh_p.add_argument("--forming-table", default="forming")
    mesh_p.add_argument("--recipe", default=None, help="Default config/generic_utils/mesh.json.")
    mesh_p.add_argument("--out-dir", required=True)
    mesh_p.add_argument("--quiet", action="store_true")
    mesh_p.set_defaults(func=_cmd_mesh)

    capplan_p = sub.add_parser(
        "capital-plan",
        help="Recycle cash from EXIT/TRIM rows into NEW/ADD rows. Cash and industry NAV %% after. Courses come from the caller.",
    )
    capplan_p.add_argument("--plan", default=None, help="CSV: symbol, course, optional usd, frac, px, ind, note.")
    capplan_p.add_argument("--judgment", default=None, help="Day judgment: rows from capital_plan.<--key> (same rows UPDATE replays).")
    capplan_p.add_argument("--key", default="capital_plan", help="capital_plan key in the judgment (capital_plan or capital_plan_queue).")
    capplan_p.add_argument("--pack", default=None, help="briefing_pack.json: book rows and capital.cash_usd.")
    capplan_p.add_argument("--book-csv", default=None, help="Book rows with symbol/ticker, value_usd, ind (overrides pack book).")
    capplan_p.add_argument("--cash", type=float, default=None, help="Cash USD (overrides pack capital).")
    capplan_p.add_argument("--recipe", default=None, help="Default config/generic_utils/capital_plan.json.")
    capplan_p.add_argument("--out-dir", default=None, help="Default with --judgment and --pack: PACK/<key>.")
    capplan_p.add_argument("--source-note", default=None)
    capplan_p.add_argument("--quiet", action="store_true")
    capplan_p.set_defaults(func=_cmd_capital_plan)

    page_p = sub.add_parser(
        "page",
        help="Render an operator page (JSON spec -> self-contained HTML with one Ledger tab).",
    )
    page_p.add_argument("--spec", default=None, help="Page spec JSON, usually logs/canvases/YYYYMMDD/[HHMM_mode/]NAME.json.")
    page_p.add_argument("--template", default=None, help="Template name in config/generic_utils/pages (daily_focus, movers, playbook) or a path.")
    page_p.add_argument("--pack", default="latest", help="Pack folder for {PACK} (default: newest briefing_pack_*).")
    page_p.add_argument("--day", default=None, help="YYYYMMDD for {DAY} and the page name.")
    page_p.add_argument("--judgment", default=None, help="Day judgment JSON (logs/canvases/YYYYMMDD/judgment.json).")
    page_p.add_argument("--out-dir", default=None,
                        help="Template pages: run folder to write into. Default $CANVAS_RUN_DIR, else logs/canvases/YYYYMMDD/.")
    page_p.add_argument("--manual", action="store_true", help="Data-only page from the template (name suffix -manual).")
    page_p.add_argument("--out", default=None, help="HTML path. Default: NAME.html beside the spec.")
    page_p.add_argument("--ledger", default=None, help="Standing ledger JSON. Default config/generic_utils/canvas_ledger.json.")
    page_p.add_argument("--strict", action="store_true", help="Exit non-zero when a header or coded value has no Ledger row.")
    page_p.add_argument("--no-index", action="store_true", help="Skip rewriting logs/canvases/index.html.")
    page_p.add_argument("--quiet", action="store_true")
    page_p.set_defaults(func=_cmd_page)

    run_p = sub.add_parser(
        "page-run",
        help="Open a canvas run folder logs/canvases/YYYYMMDD/HHMM_MODE/ and print it (pass it as page --out-dir).",
    )
    run_p.add_argument("--day", required=True, help="YYYYMMDD.")
    run_p.add_argument("--mode", default="ide", help="full | update | manual | ide (folder suffix).")
    run_p.add_argument("--pack", default="latest", help="Pack recorded in run.json (default: newest).")
    run_p.add_argument("--origin", default="opened from the IDE")
    run_p.set_defaults(func=_cmd_page_run)

    priors_p = sub.add_parser(
        "priors",
        help="Write the day's priors file from the judgment, every key EXCHANGE:TICKER (merges into an existing file).",
    )
    priors_p.add_argument("--day", required=True, help="YYYYMMDD.")
    priors_p.add_argument("--judgment", default=None, help="Default logs/canvases/YYYYMMDD/judgment.json.")
    priors_p.add_argument("--pack", default="latest", help="Pack the judgment was written on (default: newest).")
    priors_p.add_argument("--priors-dir", default=None, help="Default logs/AI_ANALYSIS_UTILS/priors.")
    priors_p.add_argument("--source", default=None, help="One line: which run wrote it.")
    priors_p.add_argument("--notes", default=None, help="Plan summary line (cash after, queue, shorts).")
    priors_p.add_argument("--skip", default=None, help="Comma courses to leave out unless held. Default PASS,WATCH,STAND_ASIDE; '' keeps all.")
    priors_p.add_argument("--replace", action="store_true", help="Overwrite instead of merging into the day's file.")
    priors_p.set_defaults(func=_cmd_priors)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    main()
