"""Generic TradingView dataset interrogation CLI.

Replaces ad-hoc agent SQL / `_tmp_*.py`. No eligibility cutoffs.
Subcommands: recipes, inventory, summary, group, histogram, counts,
overlap, fetch, named, history, span, weight-group, join, derive, focus,
pack-focus, movers, setup, forward, risk, export.
Rank-only (no --where): rank_cli.py.
All-suite manual runner: src/run_operator_suites.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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
    from generic_utils.series import alias_fields, fetch_named, parse_csv_list, parse_equals, parse_rename
    from generic_utils.scan_sources import rows_to_csv

    columns = parse_csv_list(args.fields)
    fetched = fetch_named(
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
    rows = alias_fields(fetched["rows"], parse_rename(args.rename))
    payload: dict[str, Any] = {"spec": fetched["spec"], "rows": rows}
    if args.out:
        path = rows_to_csv(args.out, rows)
        payload["out"] = path.as_posix()
    _print_payload(payload, include_rows=not args.out or args.include_rows)
    return payload


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

    recipe = load_setup_recipe(args.recipe)
    rows = _load_pack_or_source_rows(args)
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
    )
    out_rows, cover_spec = _apply_cover(out_rows, args, clamp=True)
    spec = {
        "recipe": str(args.recipe),
        "setup_recipe": str(args.setup_recipe),
        "n": len(out_rows),
        "peer_n": len(peer) if peer is not None else len(rows),
        "note": (
            "fwd_street_* = analyst PT leftover; fwd_pack_* = max(street, edge); "
            "fp_* = financial_projection. Empty pe_fwd is a gap, not EV/Rev."
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

    named_p = sub.add_parser("named", help="Named-column fetch. Never SELECT *.")
    named_p.add_argument("--db", required=True)
    named_p.add_argument("--table", required=True)
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
        help="Book NAV + loss ladders (position % / $ / % of NAV). No trim/EXIT cutoff.",
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    main()
