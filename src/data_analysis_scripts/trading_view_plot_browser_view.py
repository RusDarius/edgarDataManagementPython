from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Chrome on Windows often fails file:// URLs above the legacy MAX_PATH limit.
CHROME_FILE_URL_SAFE_LENGTH = 260

BACKWARDS_RUN_PREFIX = "backwards_prediction_analysis_"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def plots_root() -> Path:
    return _repo_root() / "plots"


def backwards_plots_root() -> Path:
    return plots_root() / "backwards"


def plot_browser_view_dir() -> Path:
    """Deprecated alias for backwards_plots_root(); kept for compatibility."""
    return backwards_plots_root()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "plot"


def path_to_file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def is_safe_for_chrome_file_url(path: Path) -> bool:
    return len(path_to_file_uri(path)) <= CHROME_FILE_URL_SAFE_LENGTH


def extract_backwards_run_slug(run_folder: str | Path) -> str:
    name = Path(run_folder).name
    if name.startswith(BACKWARDS_RUN_PREFIX):
        return name[len(BACKWARDS_RUN_PREFIX) :]
    return name


def resolve_backwards_symbol_plot_dir(
    run_slug: str,
    symbol_slug: str,
    *,
    plots_root_dir: Path | None = None,
) -> Path:
    root = (plots_root_dir or backwards_plots_root()).resolve()
    if symbol_slug == "universe":
        return root / _slugify(run_slug) / "universe"
    return root / _slugify(run_slug) / f"symbols__{_slugify(symbol_slug)}"


def resolve_backwards_plot_html_path(
    run_slug: str,
    symbol_slug: str,
    profile_slug: str,
    *,
    plots_root_dir: Path | None = None,
) -> Path:
    return (
        resolve_backwards_symbol_plot_dir(
            run_slug,
            symbol_slug,
            plots_root_dir=plots_root_dir,
        )
        / f"{_slugify(profile_slug)}.html"
    )


def plots_master_index_path(*, plots_root_dir: Path | None = None) -> Path:
    return (plots_root_dir or plots_root()).resolve() / "index.html"


def plots_latest_index_path(*, plots_root_dir: Path | None = None) -> Path:
    return (plots_root_dir or plots_root()).resolve() / "latest" / "index.html"


@dataclass(frozen=True)
class BackwardsPlotRef:
    run_slug: str
    symbol_slug: str
    profile_slug: str
    horizon_name: str
    database_path: str
    run_data_dir: str
    title: str | None = None


def _plot_label(symbol_slug: str, profile_slug: str) -> str:
    return f"{_slugify(symbol_slug)}/{_slugify(profile_slug)}"


def _build_plot_entry(
    html_path: Path,
    *,
    plot_ref: BackwardsPlotRef,
) -> dict[str, str]:
    uri = path_to_file_uri(html_path)
    label = _plot_label(plot_ref.symbol_slug, plot_ref.profile_slug)
    return {
        "label": label,
        "run_slug": _slugify(plot_ref.run_slug),
        "symbol_slug": _slugify(plot_ref.symbol_slug),
        "profile_slug": _slugify(plot_ref.profile_slug),
        "horizon_name": plot_ref.horizon_name,
        "title": plot_ref.title or "",
        "html_path": html_path.resolve().as_posix(),
        "browser_html_path": html_path.resolve().as_posix(),
        "browser_file_uri": uri,
        "chrome_file_url_safe": str(is_safe_for_chrome_file_url(html_path)).lower(),
        "database_path": plot_ref.database_path,
        "run_data_dir": plot_ref.run_data_dir,
    }


def _write_manifest(path: Path, manifest: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _write_plot_index_html(
    *,
    index_path: Path,
    title: str,
    subtitle: str,
    manifest: dict[str, dict[str, str]],
) -> Path:
    rows = []
    for label in sorted(manifest):
        entry = manifest[label]
        chart_title = entry.get("title") or label
        uri = entry["browser_file_uri"]
        meta = (
            f"run={entry.get('run_slug', '')} | "
            f"symbols={entry.get('symbol_slug', '')} | "
            f"profile={entry.get('profile_slug', '')}"
        )
        rows.append(
            f'<li><a href="{uri}">{chart_title}</a>'
            f'<div class="meta">{meta}</div></li>'
        )
    body = "\n".join(rows) if rows else "<li>No plots yet.</li>"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{title}</title>"
            "<style>body{font:14px Segoe UI,Arial,sans-serif;margin:24px;max-width:1100px;}"
            "h1{margin-bottom:6px;} .subtitle{color:#555;margin-top:0;}"
            ".meta{color:#666;font-size:12px;margin:0 0 14px 24px;}"
            "a{font-size:16px;}</style></head><body>"
            f"<h1>{title}</h1><p class='subtitle'>{subtitle}</p>"
            f"<ul>{body}</ul></body></html>"
        ),
        encoding="utf-8",
    )
    return index_path


def _load_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_manifest_file(path: Path, entry: dict[str, str]) -> dict[str, dict[str, str]]:
    manifest = _load_manifest(path)
    manifest[entry["label"]] = entry
    _write_manifest(path, manifest)
    return manifest


def _write_run_info(
    run_slug: str,
    *,
    database_path: str,
    run_folder: str,
    plots_root_dir: Path | None = None,
) -> Path:
    run_dir = (plots_root_dir or backwards_plots_root()).resolve() / _slugify(run_slug)
    run_dir.mkdir(parents=True, exist_ok=True)
    info_path = run_dir / "run_info.json"
    info_path.write_text(
        json.dumps(
            {
                "run_slug": _slugify(run_slug),
                "run_folder": run_folder,
                "database_path": database_path,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return info_path


def _rebuild_run_indexes(
    run_slug: str,
    *,
    plots_root_dir: Path | None = None,
) -> dict[str, str]:
    root = (plots_root_dir or backwards_plots_root()).resolve()
    run_dir = root / _slugify(run_slug)
    run_manifest: dict[str, dict[str, str]] = {}

    for manifest_path in sorted(run_dir.glob("**/manifest.json")):
        if manifest_path.parent == run_dir:
            continue
        run_manifest.update(_load_manifest(manifest_path))

    run_manifest_path = run_dir / "manifest.json"
    _write_manifest(run_manifest_path, run_manifest)
    run_index = _write_plot_index_html(
        index_path=run_dir / "index.html",
        title=f"Backwards plots: {run_slug}",
        subtitle="All symbol sets and profiles for this backwards run.",
        manifest=run_manifest,
    )
    return {
        "run_manifest_path": run_manifest_path.as_posix(),
        "run_index_path": run_index.as_posix(),
        "run_index_file_uri": path_to_file_uri(run_index),
    }


def _set_latest_run(run_slug: str, *, plots_root_dir: Path | None = None) -> dict[str, str]:
    root = (plots_root_dir or plots_root()).resolve()
    latest_meta_path = root / "latest.json"
    backwards_root = root if root.name == "backwards" else root / "backwards"
    run_dir = backwards_root / _slugify(run_slug)
    run_index = run_dir / "index.html"
    latest_meta = {
        "run_slug": _slugify(run_slug),
        "run_index_path": run_index.as_posix(),
        "run_index_file_uri": path_to_file_uri(run_index) if run_index.is_file() else "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    latest_meta_path.write_text(json.dumps(latest_meta, indent=2), encoding="utf-8")

    latest_index = plots_latest_index_path(plots_root_dir=root)
    latest_index.parent.mkdir(parents=True, exist_ok=True)
    if run_index.is_file():
        latest_index.write_text(run_index.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        _write_plot_index_html(
            index_path=latest_index,
            title="Latest backwards plots",
            subtitle=f"Run {run_slug}",
            manifest={},
        )
    return {
        "latest_meta_path": latest_meta_path.as_posix(),
        "latest_index_path": latest_index.as_posix(),
        "latest_index_file_uri": path_to_file_uri(latest_index),
    }


def refresh_plots_master_index(*, plots_root_dir: Path | None = None) -> dict[str, str]:
    root = (plots_root_dir or plots_root()).resolve()
    backwards_root = root / "backwards"
    latest_meta_path = root / "latest.json"
    latest_slug = ""
    if latest_meta_path.is_file():
        latest_slug = json.loads(latest_meta_path.read_text(encoding="utf-8")).get("run_slug", "")

    run_sections: list[str] = []
    for run_dir in sorted(backwards_root.glob("*/index.html") if backwards_root.is_dir() else []):
        run_slug = run_dir.parent.name
        run_uri = path_to_file_uri(run_dir)
        latest_badge = " <strong>(latest)</strong>" if run_slug == latest_slug else ""
        run_sections.append(
            f'<li><a href="{run_uri}">{run_slug}</a>{latest_badge}</li>'
        )

    latest_index_uri = path_to_file_uri(plots_latest_index_path(plots_root_dir=root))
    master_index = plots_master_index_path(plots_root_dir=root)
    master_index.parent.mkdir(parents=True, exist_ok=True)
    master_index.write_text(
        (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Plot index</title>"
            "<style>body{font:14px Segoe UI,Arial,sans-serif;margin:24px;max-width:900px;}"
            "a{font-size:16px;}</style></head><body>"
            "<h1>Plot index</h1>"
            f"<p><a href='{latest_index_uri}'>Open latest plot set</a></p>"
            "<h2>Backwards runs</h2><ul>"
            + ("\n".join(run_sections) if run_sections else "<li>No backwards plots yet.</li>")
            + "</ul></body></html>"
        ),
        encoding="utf-8",
    )
    return {
        "plots_index_path": master_index.as_posix(),
        "plots_index_file_uri": path_to_file_uri(master_index),
        "latest_index_file_uri": latest_index_uri,
    }


def register_backwards_plot(
    html_path: Path,
    *,
    plot_ref: BackwardsPlotRef,
    plots_root_dir: Path | None = None,
) -> dict[str, str]:
    """Register a plot under plots/backwards/{run}/{symbol_set}/{profile}.html."""
    resolved_html = html_path.resolve()
    if not resolved_html.is_file():
        raise FileNotFoundError(resolved_html)

    root = (plots_root_dir or plots_root()).resolve()
    entry = _build_plot_entry(resolved_html, plot_ref=plot_ref)
    symbol_dir = resolve_backwards_symbol_plot_dir(
        plot_ref.run_slug,
        plot_ref.symbol_slug,
        plots_root_dir=root / "backwards",
    )
    symbol_manifest_path = symbol_dir / "manifest.json"
    symbol_manifest = _merge_manifest_file(symbol_manifest_path, entry)
    symbol_index = _write_plot_index_html(
        index_path=symbol_dir / "index.html",
        title=f"Plots: {plot_ref.symbol_slug}",
        subtitle=f"Run {plot_ref.run_slug}",
        manifest=symbol_manifest,
    )

    _write_run_info(
        plot_ref.run_slug,
        database_path=plot_ref.database_path,
        run_folder=str(Path(plot_ref.database_path).parent),
        plots_root_dir=root / "backwards",
    )
    run_indexes = _rebuild_run_indexes(plot_ref.run_slug, plots_root_dir=root / "backwards")
    latest_indexes = _set_latest_run(plot_ref.run_slug, plots_root_dir=root)
    master_indexes = refresh_plots_master_index(plots_root_dir=root)

    entry.update(run_indexes)
    entry.update(latest_indexes)
    entry.update(master_indexes)
    entry["symbol_manifest_path"] = symbol_manifest_path.as_posix()
    entry["symbol_index_path"] = symbol_index.as_posix()
    entry["symbol_index_file_uri"] = path_to_file_uri(symbol_index)
    entry["index_file_uri"] = master_indexes["plots_index_file_uri"]
    return entry


def get_backwards_plot_entry(
    run_slug: str,
    symbol_slug: str,
    profile_slug: str,
    *,
    plots_root_dir: Path | None = None,
) -> dict[str, str]:
    root = (plots_root_dir or backwards_plots_root()).resolve()
    manifest_path = (
        resolve_backwards_symbol_plot_dir(run_slug, symbol_slug, plots_root_dir=root)
        / "manifest.json"
    )
    manifest = _load_manifest(manifest_path)
    label = _plot_label(symbol_slug, profile_slug)
    if label not in manifest:
        raise KeyError(f"Plot not found: {label} in {manifest_path}")
    return manifest[label]


def get_browser_view_entry(label: str, **kwargs) -> dict[str, str]:
    """Deprecated: flat labels are no longer used. Parse run/symbol/profile if needed."""
    raise KeyError(
        "Flat browser-view labels were replaced by run-organized plots. "
        "Use get_backwards_plot_entry(run_slug, symbol_slug, profile_slug) instead."
    )


def write_plot_locations_sidecar(
    run_data_dir: Path,
    *,
    plot_entry: dict[str, str],
    profile_slug: str,
) -> Path:
    """Store pointers from logs run data folder to the canonical plots/ HTML."""
    sidecar_path = run_data_dir / "plot_locations.json"
    payload: dict[str, object] = {}
    if sidecar_path.is_file():
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    profiles = payload.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        payload["profiles"] = profiles
    profiles[_slugify(profile_slug)] = {
        "html_path": plot_entry["html_path"],
        "browser_file_uri": plot_entry["browser_file_uri"],
        "label": plot_entry["label"],
    }
    payload["plots_index_file_uri"] = plot_entry.get("plots_index_file_uri", "")
    payload["latest_index_file_uri"] = plot_entry.get("latest_index_file_uri", "")
    payload["run_slug"] = plot_entry.get("run_slug", "")
    sidecar_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return sidecar_path


# Legacy no-ops kept so older imports do not break tests immediately.
def finalize_browser_view_plot(**kwargs):  # type: ignore[no-untyped-def]
    raise RuntimeError("finalize_browser_view_plot was replaced by register_backwards_plot")


def publish_plot_html_for_browser(**kwargs):  # type: ignore[no-untyped-def]
    raise RuntimeError("publish_plot_html_for_browser was replaced by register_backwards_plot")


def resolve_browser_view_path(label: str, **kwargs) -> Path:
    raise RuntimeError("resolve_browser_view_path was replaced by resolve_backwards_plot_html_path")


__all__ = [
    "BACKWARDS_RUN_PREFIX",
    "BackwardsPlotRef",
    "CHROME_FILE_URL_SAFE_LENGTH",
    "backwards_plots_root",
    "extract_backwards_run_slug",
    "get_backwards_plot_entry",
    "is_safe_for_chrome_file_url",
    "path_to_file_uri",
    "plot_browser_view_dir",
    "plots_latest_index_path",
    "plots_master_index_path",
    "plots_root",
    "refresh_plots_master_index",
    "register_backwards_plot",
    "resolve_backwards_plot_html_path",
    "resolve_backwards_symbol_plot_dir",
    "write_plot_locations_sidecar",
]
