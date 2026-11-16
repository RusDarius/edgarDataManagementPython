"""Patch scan: a deterministic inventory of a change set, and of what the code base can do, for the patch notes.

Procedures (recipe config/generic_utils/patch_scan.json, skill .claude/skills/patch-notes):

- ``scan``: one change set (the staged index, the working tree, one commit or a range) read from git without checking
  anything out: files by area with lines added / deleted; per Python file the functions and classes added, removed and
  changed (AST compare, so comments and formatting do not count); CLI subcommands and flags added or removed; JSON
  recipes' top-level keys and named list entries (suites, templates, lanes) added, removed and changed; skills; tests;
  the wiring of each new module (test, recipe, CLI, refresh graph, run catalog, CLAUDE.md, skills); lint (pyflakes when
  installed) and the repo guards (forbidden paths, text markers, ``SELECT *`` on the wide tables); the longest new or
  changed functions; helper names the touched modules define that many generic_utils modules also define. Writes
  ``inventory.md`` (read this, not the diff), ``inventory.json`` and ``patch_note.skeleton.md`` under logs/patch_notes/.
- ``capabilities``: ``capability_index.md``: runners, every CLI subcommand with its help, modules with their one-line
  purpose / recipe / test file, recipes, page templates, the refresh graph, skills, prompts and paste blocks. Generated,
  so it is rewritten on every patch, never hand-edited.
- ``history``: ``commit_history.md``: one entry per commit (date, subject, size, areas, modules / skills / docs added).
- ``features``: ``feature_register.md``: per feature (a module with its CLI subcommands, refresh steps and page), page
  template and skill the commit it arrived in, the last change, the patch numbers that changed it and its status; the
  declared status changes of ``lifecycle.json`` (on demand, renamed, deprecated, removal requested, removed) with what
  still refers to each; refresh steps, default pages and CLI subcommands by first appearance and removal; files removed
  over the history. ``regen`` rewrites all three generated files.

``scan`` also tracks the life of a feature: which features a change set adds, updates or removes, everything it deletes
(file, function, subcommand, flag, recipe key, list entry, skill, page) with the working-tree files that still refer to
it, added lines that use something ``lifecycle.json`` declares deprecated, and added lines with lifecycle wording.

What it does not do: it does not judge. Which change matters, whether a long function is a problem, what a patch means
for the operator and every review finding are the patch note's (written by hand from this inventory and the code). No
cutoffs: the top-N lists are display knobs. It never stages, commits or changes a file outside its output paths.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE = REPO_ROOT / "config" / "generic_utils" / "patch_scan.json"
INDEX, WORKTREE = "INDEX", "WORKTREE"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's id of a tree with no files
NOTE_NAME = re.compile(r"^(\d{4})_.*\.md$")


def load_recipe(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFAULT_RECIPE).read_text(encoding="utf-8"))


# --- git ----------------------------------------------------------------------------------------------------


def git(*args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()[:300]}")
    return done.stdout


def resolve_mode(*, staged: bool = False, worktree: bool = False, commit: str | None = None, rng: str | None = None) -> dict[str, Any]:
    """{label, old, new, diff}: the two sides of the change set and the ``git diff`` arguments that list it."""
    if commit:
        has_parent = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{commit}^"], cwd=REPO_ROOT, capture_output=True).returncode == 0
        old = f"{commit}^" if has_parent else EMPTY_TREE  # the first commit is compared with the empty tree
        return {"label": f"commit {commit}", "old": old, "new": commit, "diff": [old, commit]}
    if rng:
        old, _, new = rng.replace("...", "..").partition("..")  # A...B is read as A..B: the two named sides
        if not old:
            raise ValueError(f"--range needs A..B, got {rng!r}")
        return {"label": f"range {rng}", "old": old, "new": new or "HEAD", "diff": [old, new or "HEAD"]}
    if worktree:
        return {"label": "working tree vs HEAD", "old": "HEAD", "new": WORKTREE, "diff": ["HEAD"]}
    return {"label": "staged vs HEAD", "old": "HEAD", "new": INDEX, "diff": ["--cached"]}


def changed_files(mode: Mapping[str, Any]) -> list[dict[str, Any]]:
    """[{path, status A|M|D, added, deleted}] (renames listed as a delete and an add; binary files count 0 lines)."""
    status = {}
    for line in git("diff", *mode["diff"], "--name-status", "--no-renames").splitlines():
        code, _, path = line.partition("\t")
        if path:
            status[path] = code[:1] if code[:1] in ("A", "M", "D") else "M"  # a type change (T) counts as modified
    rows = []
    for line in git("diff", *mode["diff"], "--numstat", "--no-renames").splitlines():
        add, dele, path = (line.split("\t") + ["", ""])[:3]
        if path:
            rows.append({"path": path, "status": status.get(path, "M"), "added": int(add) if add.isdigit() else 0,
                         "deleted": int(dele) if dele.isdigit() else 0})
    if mode["new"] == WORKTREE:  # files git does not track yet are part of what the next commit can carry
        for path in git("ls-files", "--others", "--exclude-standard").splitlines():
            text = blob(WORKTREE, path) if path else None
            if text is not None:
                binary = "\x00" in text[:8000]  # git's own test; a binary file counts 0 lines, as in --numstat
                rows.append({"path": path, "status": "A", "deleted": 0, "untracked": True,
                             "added": 0 if binary else text.count("\n") + (0 if text.endswith("\n") or not text else 1)})
    return rows


def blob(ref: str, path: str) -> str | None:
    """The file's text on one side of the change set (None when it does not exist there)."""
    if ref == WORKTREE:
        p = REPO_ROOT / path
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None
    done = subprocess.run(["git", "show", f"{'' if ref == INDEX else ref}:{path}"], cwd=REPO_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return done.stdout if done.returncode == 0 else None


# --- pure readers (tested) ----------------------------------------------------------------------------------


def area_of(path: str, areas: Sequence[Sequence[str]]) -> str:
    """Label of the first recipe prefix the path starts with ('other' when none)."""
    for prefix, label in areas:
        if path.startswith(prefix):
            return str(label)
    return "other"


def py_symbols(source: str | None) -> dict[str, dict[str, Any]]:
    """{name: {line, lines, sig}} for top-level functions, classes and ``Class.method``; {} when the source does not parse."""
    if not source:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out: dict[str, dict[str, Any]] = {}

    def add(name: str, node: ast.AST) -> None:
        end = getattr(node, "end_lineno", None) or node.lineno
        out[name] = {"line": node.lineno, "lines": end - node.lineno + 1, "kind": "class" if isinstance(node, ast.ClassDef) else "def",
                     "sig": hashlib.md5(ast.dump(node).encode("utf-8")).hexdigest()}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, node)
        elif isinstance(node, ast.ClassDef):
            add(node.name, node)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{node.name}.{sub.name}", sub)
    return out


def symbol_changes(old: str | None, new: str | None) -> dict[str, list[str]]:
    """Functions / classes added, removed and changed between two versions of a module (AST compare)."""
    a, b = py_symbols(old), py_symbols(new)
    return {"added": [n for n in b if n not in a], "removed": [n for n in a if n not in b],
            "changed": [n for n in b if n in a and a[n]["sig"] != b[n]["sig"]]}


def cli_surface(source: str | None) -> dict[str, Any]:
    """{'subcommands': {name: help}, 'flags': [--flag, ...]} from ``add_parser`` / ``add_argument`` calls."""
    subs: dict[str, str] = {}
    flags: list[str] = []
    if not source:
        return {"subcommands": subs, "flags": flags}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"subcommands": subs, "flags": flags}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        if node.func.attr == "add_parser":
            text = next((kw.value.value for kw in node.keywords if kw.arg in ("help", "description")
                         and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)), "")
            subs[first.value] = " ".join(text.split())
        elif node.func.attr == "add_argument" and first.value.startswith("--") and first.value not in flags:
            flags.append(first.value)
    return {"subcommands": subs, "flags": flags}


def _entry_id(item: Any) -> str | None:
    if isinstance(item, Mapping):
        for key in ("name", "key", "id", "study", "label"):
            if isinstance(item.get(key), str):
                return item[key]
    return None


def json_changes(old: str | None, new: str | None) -> dict[str, Any]:
    """Top-level keys added / removed / changed, and for lists of named entries the names added / removed / changed."""
    try:
        a = json.loads(old) if old else {}
        b = json.loads(new) if new else {}
    except json.JSONDecodeError:
        return {"error": "not valid JSON"}
    if not isinstance(a, dict) or not isinstance(b, dict):
        return {"note": "top level is not an object", "changed": a != b}
    out: dict[str, Any] = {"added": [k for k in b if k not in a], "removed": [k for k in a if k not in b],
                           "changed": [k for k in b if k in a and a[k] != b[k]], "lists": {}}
    for key in out["changed"] + out["added"]:
        va, vb = a.get(key) or [], b.get(key)
        if isinstance(vb, list) and vb and all(_entry_id(x) for x in vb) and isinstance(va, list):
            ia, ib = {_entry_id(x): x for x in va if _entry_id(x)}, {_entry_id(x): x for x in vb}
            diff = {"added": [n for n in ib if n not in ia], "removed": [n for n in ia if n not in ib],
                    "changed": [n for n in ib if n in ia and ia[n] != ib[n]]}
            if any(diff.values()):
                out["lists"][key] = diff
    return out


def skill_meta(text: str | None) -> dict[str, str]:
    """name / description from a SKILL.md front matter."""
    out = {"name": "", "description": ""}
    if not text or not text.startswith("---"):
        return out
    head = text.split("---", 2)[1]
    for key in out:
        m = re.search(rf"^{key}:\s*(.+)$", head, flags=re.MULTILINE)
        if m:
            out[key] = m.group(1).strip()
    return out


def first_sentence(text: str | None, limit: int = 150) -> str:
    """First sentence of a docstring or description on one line, cut at ``limit`` characters."""
    flat = " ".join((text or "").strip().split())
    m = re.search(r"(?<!e\.g)(?<!i\.e)(?<!vs)(?<!etc)(?<=[a-z0-9\)\]])\.\s", flat)
    flat = flat[: m.start() + 1] if m else flat
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def module_doc(source: str | None) -> str:
    try:
        return first_sentence(ast.get_docstring(ast.parse(source or "")) or "")
    except SyntaxError:
        return "(does not parse)"


def next_number(notes_dir: Path) -> int:
    """The next patch number: one past the highest ``NNNN_*.md`` in the notes folder (0000 is the baseline)."""
    found = [int(m.group(1)) for p in notes_dir.glob("*.md") if (m := NOTE_NAME.match(p.name))]
    return max(found) + 1 if found else 0


def lint(source: str, path: str) -> list[str]:
    """pyflakes messages for one source text ('pyflakes not installed' once when it is missing)."""
    try:
        from pyflakes.api import check
        from pyflakes.reporter import Reporter
    except ImportError:
        return ["pyflakes not installed"]
    out, err = io.StringIO(), io.StringIO()
    check(source, path, Reporter(out, err))
    return [ln.strip() for ln in (out.getvalue() + err.getvalue()).splitlines() if ln.strip()]


def _count_broad_except(source: str | None) -> int:
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return 0
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)
               and (n.type is None or (isinstance(n.type, ast.Name) and n.type.id in ("Exception", "BaseException"))))


# --- scan ---------------------------------------------------------------------------------------------------


def _read(path: str) -> str:
    p = REPO_ROOT / path
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def cli_aliases(cli_source: str | None) -> dict[str, list[str]]:
    """{module stem: [subcommand, ...]} from the ``_cmd_<name>`` functions of a CLI source that import the module
    (``_cmd_peer_value`` importing ``generic_utils.peer_value`` -> 'peer-value'; ``_cmd_anatomy`` -> winners_anatomy)."""
    out: dict[str, list[str]] = defaultdict(list)
    try:
        tree = ast.parse(cli_source or "")
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_cmd_"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom) and sub.module:
                    name = node.name[5:].replace("_", "-")
                    stem = sub.module.rsplit(".", 1)[-1]
                    if name not in out[stem]:
                        out[stem].append(name)
    return out


def mention_tokens(path: str, aliases: Sequence[str]) -> list[str]:
    """Strings that mean 'this module' in docs and configs: the file name, the dotted module, a multi-word stem (plain or
    dashed) and ``<cli>.py <subcommand>`` for each CLI alias. A one-word stem alone ('valuation') is too common to count."""
    stem = Path(path).stem
    tokens = [f"{stem}.py", f".{stem} ", f".{stem})", f".{stem}`"]  # the searched text has single spaces for whitespace
    if "_" in stem:
        tokens += [stem, stem.replace("_", "-")]
    return tokens + [f".py {a}" for a in aliases] + [f'.py", "{a}"' for a in aliases]  # prose / shell, and a JSON argv list


def wiring_rows(new_modules: Sequence[str], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """For each new module: its test file, recipe, CLI subcommands, and where the working tree mentions it (refresh graph,
    run catalog, CLAUDE.md, skills). A blank cell is a fact, not a verdict: a helper module needs no skill."""
    cfg = recipe.get("wiring") or {}
    mentions = {k: _read(v) for k, v in (cfg.get("mentions") or {}).items()}
    aliases = cli_aliases(mentions.get("cli"))
    # one space for every run of whitespace: an indented JSON argv list then reads `tv_scan_cli.py", "board"` on one line
    mentions = {k: " ".join(v.split()) + " " for k, v in mentions.items() if k != "cli"}
    skills = {p.parent.name: " ".join(p.read_text(encoding="utf-8", errors="replace").split()) + " "
              for p in (REPO_ROOT / str(cfg.get("skills_dir") or ".claude/skills")).glob("*/SKILL.md")}
    tests_dir = REPO_ROOT / str(cfg.get("tests_dir") or "src/tests")
    test_text = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in tests_dir.glob("test_*.py")} if tests_dir.exists() else {}
    rows = []
    for path in new_modules:
        stem = Path(path).stem
        subs = aliases.get(stem, [])
        tokens = mention_tokens(path, subs)
        own = f"test_{stem}.py"
        imports = re.compile(rf"(?:from|import)\s+[\w.]*\b{re.escape(stem)}\b")
        tests = [own] if own in test_text else [n for n, t in test_text.items() if imports.search(t)][:3]
        source = _read(path)
        recipes = [p.relative_to(REPO_ROOT).as_posix() for d in cfg.get("recipe_dirs") or []
                   for p in sorted((REPO_ROOT / d).glob("*.json")) if p.stem == stem.removeprefix("run_") or p.name in source]
        row = {"module": path, "tests": ", ".join(tests), "recipe": ", ".join(recipes), "cli": ", ".join(subs)}
        for key, text in mentions.items():
            row[key] = "yes" if any(t in text for t in tokens) else ""
        row["skills"] = ", ".join(sorted(k for k, t in skills.items() if any(tok in t for tok in tokens)))
        rows.append(row)
    return rows


def shared_helpers(touched: Mapping[str, Sequence[str]], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Top-level names the touched modules define (added or changed) that at least ``shared_helper_min_modules`` modules of
    the shared-helper folder also define: candidates for one shared helper, listed with the modules that carry them."""
    folder = REPO_ROOT / str(recipe.get("shared_helper_dir") or "src/generic_utils")
    need = int(recipe.get("shared_helper_min_modules") or 4)
    owners: dict[str, list[str]] = defaultdict(list)
    for p in sorted(folder.glob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owners[node.name].append(p.stem)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):  # _f = to_float
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        owners[tgt.id].append(p.stem)
    mine = {n for names in touched.values() for n in names}
    rows = [{"name": n, "modules": len(m), "in": ", ".join(m[:10]) + (" …" if len(m) > 10 else "")}
            for n, m in owners.items() if n in mine and len(m) >= need and not n.endswith("_local_main") and n != "main"]
    return sorted(rows, key=lambda r: (-r["modules"], r["name"]))


# --- lifecycle: what a change set removes, what still points at it, what is declared deprecated -----------------


def _globs(patterns: Sequence[str]) -> list[re.Pattern[str]]:
    return [re.compile("^" + re.escape(g).replace(r"\*", "[^/]*") + "$") for g in patterns]


def load_lifecycle(recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Declared status changes (hand-written ``lifecycle.json``): [{names, kind, status, patch, replaced_by, note, ...}]."""
    path = REPO_ROOT / str((recipe.get("lifecycle") or {}).get("file") or "-")
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [dict(e) for e in data.get("entries") or [] if isinstance(e, dict) and e.get("status") and e.get("names")]


def corpus(recipe: Mapping[str, Any]) -> dict[str, str]:
    """{path: text} of the working-tree files a reference can live in (tracked and untracked; recipe roots / suffixes)."""
    cfg = recipe.get("lifecycle") or {}
    roots = tuple(cfg.get("reference_roots") or ["src/", "config/", ".claude/skills/", "documentation/", "CLAUDE.md", "README.md"])
    skip = tuple(cfg.get("reference_skip") or [])
    suffixes = tuple(cfg.get("reference_suffixes") or [".py", ".json", ".md", ".txt", ".mdc"])
    cap = int(cfg.get("max_file_bytes") or 2_000_000)
    paths = set(git("ls-files").splitlines()) | set(git("ls-files", "--others", "--exclude-standard").splitlines())
    out: dict[str, str] = {}
    for path in sorted(paths):
        if not path.startswith(roots) or (skip and path.startswith(skip)) or not path.endswith(suffixes):
            continue
        p = REPO_ROOT / path
        if p.is_file() and p.stat().st_size <= cap:
            out[path] = p.read_text(encoding="utf-8", errors="replace")
    return out


def references(patterns: Sequence[str], texts: Mapping[str, str], *, exclude: Sequence[str] = (), scope: Sequence[str] = (),
               require: str | None = None) -> list[tuple[str, int]]:
    """[(path, matches)] for the files that match any regex, most matches first. ``scope`` keeps path prefixes,
    ``require`` keeps files that also match that regex (a function name counts only where its module is named).
    A pattern that does not compile is skipped: the caller reports it."""
    regs = []
    for p in patterns:
        try:
            regs.append(re.compile(p))
        except re.error:
            continue
    need = re.compile(require) if require else None
    rows = []
    for path, text in texts.items():
        if path in exclude or (scope and not path.startswith(tuple(scope))) or (need and not need.search(text)):
            continue
        n = sum(len(r.findall(text)) for r in regs)
        if n:
            rows.append((path, n))
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def removal_patterns(kind: str, name: str, path: str) -> dict[str, Any]:
    """How a removed thing is still referred to: {patterns, scope, require, exclude} for ``references``."""
    stem, esc = Path(path).stem, re.escape(name)
    if kind == "module":
        pats = [rf"\b{re.escape(stem)}\.py\b", rf"(?:from|import)\s+[\w.]*\b{re.escape(stem)}\b"] + ([rf"\b{re.escape(stem)}\b"] if "_" in stem else [])
        return {"patterns": pats, "exclude": [path]}
    if kind == "package":  # an import of the package or a path into it
        return {"patterns": [rf"(?:from|import)\s+[\w.]*\b{esc}\b", rf"\b{esc}/"], "exclude": [path]}
    if kind == "function":  # the bare name, in files that also name its module
        return {"patterns": [rf"\b{esc}\b"], "require": rf"\b{re.escape(stem)}\b", "exclude": [path]}
    if kind == "subcommand":  # shell / prose form and the JSON argv form
        return {"patterns": [rf"\.py\s+{esc}(?![\w-])", rf'\.py",\s*"{esc}"']}
    if kind == "recipe key":
        return {"patterns": [rf"""(?:\[|get\()\s*["']{esc}["']"""], "require": rf"\b{re.escape(stem)}\b", "exclude": [path]}
    if kind == "list entry":  # another entry of the same file may still name it (a suite in someone's `after`)
        return {"patterns": [rf'"{esc}"'], "scope": [path]}
    if kind == "skill":
        return {"patterns": [rf"skills/{esc}\b", rf"`{esc}`"], "exclude": [path]}
    if kind == "page template":
        return {"patterns": [rf"pages/{esc}\.json", rf"--template\s+{esc}\b", rf"--templates\s+[\w,]*\b{esc}\b"], "exclude": [path]}
    return {"patterns": [re.escape(path)], "exclude": [path]}  # any other file: its full path


def _refs_text(rows: Sequence[tuple[str, int]], limit: int = 5) -> str:
    return "; ".join(f"{p} ({n})" for p, n in rows[:limit]) + (f"; +{len(rows) - limit} files" if len(rows) > limit else "")


def added_lines(mode: Mapping[str, Any], files: Sequence[Mapping[str, Any]]) -> dict[str, list[tuple[int, str]]]:
    """{path: [(line number on the new side, text)]} for the lines a change set adds (every line of an untracked file)."""
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    path, line = "", 0
    for raw in git("diff", *mode["diff"], "-U0", "--no-renames", "--no-color", "--no-ext-diff").splitlines():
        if raw.startswith("+++ b/") or raw.startswith("+++ /dev/null"):
            path = raw[6:].rstrip("\t") if raw.startswith("+++ b/") else ""
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\S+ \+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path:
            out[path].append((line, raw[1:]))
            line += 1
    for f in files:
        if f.get("untracked"):
            text = blob(WORKTREE, f["path"]) or ""
            if "\x00" not in text[:8000]:
                out[f["path"]] = list(enumerate(text.splitlines(), 1))
    return out


def lifecycle_signals(added: Mapping[str, Sequence[tuple[int, str]]], recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Added lines that carry a lifecycle word (deprecated, retired, legacy, ...): wording to read, not a verdict."""
    cfg = recipe.get("lifecycle") or {}
    words = [str(w) for w in cfg.get("signal_words") or []]
    skip = tuple(cfg.get("signal_skip") or [])
    if not words:
        return {"total": 0, "rows": []}
    reg = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE)
    rows = [{"path": path, "line": n, "word": m.group(0).lower(), "text": " ".join(text.split())[:170]}
            for path, lines in sorted(added.items()) if not (skip and path.startswith(skip))
            for n, text in lines if (m := reg.search(text))]
    return {"total": len(rows), "rows": rows[: int(cfg.get("signal_limit") or 30)]}


def deprecated_uses(added: Mapping[str, Sequence[tuple[int, str]]], entries: Sequence[Mapping[str, Any]], recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Added lines that match a pattern of a lifecycle entry whose status says 'do not build on this' (recipe
    ``guard_statuses``), outside the files the entry allows (its own fallback reader)."""
    cfg = recipe.get("lifecycle") or {}
    guarded = set(cfg.get("guard_statuses") or ["deprecated", "removal_requested", "removed", "retired"])
    skip = tuple(cfg.get("signal_skip") or [])
    out = []
    for e in entries:
        if e.get("status") not in guarded or not e.get("patterns") or e.get("guard") is False:
            continue
        try:
            reg = re.compile("|".join(f"(?:{p})" for p in e["patterns"]))
        except re.error:
            out.append({"kind": "lifecycle pattern", "path": str((cfg.get("file") or "")), "detail": f"entry {e['names'][0]!r}: a pattern does not compile"})
            continue
        allow = tuple(e.get("allow") or [])
        for path, lines in sorted(added.items()):
            if (skip and path.startswith(skip)) or (allow and path.startswith(allow)):
                continue
            hits = [n for n, text in lines if reg.search(text)]
            if hits:
                out.append({"kind": f"{e['status']} item used", "path": path,
                            "detail": f"{', '.join(e['names'][:3])} (line {hits[0]}{f', {len(hits)} lines' if len(hits) > 1 else ''})"
                                      + (f"; use {e['replaced_by']}" if e.get("replaced_by") else "")})
    return out


# --- features: one key per module, with the parts that belong to it ----------------------------------------------


def cli_primary(cli_source: str | None) -> dict[str, str]:
    """{subcommand: module stem} - the module a ``_cmd_<name>`` function is the front of: the import named like the
    command, else one whose name contains it (``anatomy`` -> winners_anatomy), else the import the fewest commands share."""
    out: dict[str, str] = {}
    try:
        tree = ast.parse(cli_source or "")
    except SyntaxError:
        return out
    imports: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_cmd_"):
            found = sorted(((sub.lineno, sub.module.rsplit(".", 1)[-1]) for sub in ast.walk(node)
                            if isinstance(sub, ast.ImportFrom) and sub.module), key=lambda t: t[0])
            imports[node.name[5:]] = list(dict.fromkeys(s for _, s in found))
    used = defaultdict(int)  # a helper (series, run_export) is imported by many commands, the fronted module by few
    for stems in imports.values():
        for s in stems:
            used[s] += 1
    for key, stems in imports.items():
        if stems:
            out[key.replace("_", "-")] = (next((s for s in stems if s == key), None)
                                          or next((s for s in stems if key in s or s in key), None)
                                          or min(stems, key=lambda s: (used[s], stems.index(s))))
    return out


def _task_feature(argv: Sequence[Any], primary: Mapping[str, Mapping[str, str]]) -> str:
    """The feature a refresh step runs: ``<cli>.py <subcommand>`` -> that command's module; another script -> its stem."""
    items = [str(a) for a in argv]
    for i, item in enumerate(items):
        if item.endswith(".py"):
            subs = primary.get(item.replace("\\", "/"))
            if subs is not None and i + 1 < len(items) and items[i + 1] in subs:
                return subs[items[i + 1]]
            return Path(item).stem
    return ""


def feature_map(recipe: Mapping[str, Any]) -> dict[str, Any]:
    """The working tree as features. A feature is one module (recipe ``wiring.module_globs``), keyed by its file stem,
    with the parts found by name: recipe (same stem), test file, the CLI subcommands it is the front of, the refresh
    steps that run it, the page templates named like one of those. ``by_path`` maps each of those files to its key."""
    cfg = recipe.get("wiring") or {}
    feat_cfg = recipe.get("features") or {}
    globs = _globs(cfg.get("module_globs") or [])
    tracked = set(git("ls-files").splitlines()) | set(git("ls-files", "--others", "--exclude-standard").splitlines())
    modules = sorted(p for p in tracked if p.endswith(".py") and not p.endswith("__init__.py") and any(g.match(p) for g in globs)
                     and (REPO_ROOT / p).is_file())
    cli_files = [str(c) for c in feat_cfg.get("cli_files") or ([cfg["mentions"]["cli"]] if (cfg.get("mentions") or {}).get("cli") else [])]
    primary = {c: cli_primary(_read(c)) for c in cli_files}
    tests_dir = str(cfg.get("tests_dir") or "src/tests")
    recipes = {p.stem: p.relative_to(REPO_ROOT).as_posix() for d in cfg.get("recipe_dirs") or [] for p in sorted((REPO_ROOT / d).glob("*.json"))}
    features: dict[str, dict[str, Any]] = {}
    by_path: dict[str, str] = {}
    for path in modules:
        key = Path(path).stem
        test = f"{tests_dir}/test_{key}.py"
        base = key.removeprefix("run_")  # a runner takes `<base>.json` only when no module of that name owns it
        rec = recipes.get(key, "") or ("" if base in features else recipes.get(base, ""))
        features[key] = {"key": key, "path": path, "doc": module_doc(_read(path)), "recipe": rec,
                         "test": test if (REPO_ROOT / test).is_file() else "", "cli": [], "steps": [], "pages": []}
        by_path[path] = key
        for part in (features[key]["recipe"], features[key]["test"]):
            if part:
                by_path.setdefault(part, key)
    for cli_file, subs in primary.items():
        for sub, stem in subs.items():
            if stem in features:
                features[stem]["cli"].append(sub)
    refresh_path = str((cfg.get("mentions") or {}).get("refresh") or "")
    steps: dict[str, str] = {}
    if refresh_path and (REPO_ROOT / refresh_path).is_file():
        graph = json.loads(_read(refresh_path))
        for stage in feat_cfg.get("refresh_stages") or ["producers", "suites"]:
            for task in graph.get(stage) or []:
                key = _task_feature(task.get("argv") or task.get("cmd") or [], primary)
                steps[str(task.get("name"))] = key
                if key in features:
                    features[key]["steps"].append(str(task.get("name")))
    pages_dir = REPO_ROOT / str(feat_cfg.get("page_dir") or (recipe.get("capability") or {}).get("page_dir") or "-")
    pages: dict[str, dict[str, Any]] = {}
    for p in sorted(pages_dir.glob("*.json")) if pages_dir.is_dir() else []:
        name, rel = p.stem, p.relative_to(REPO_ROOT).as_posix()
        owner = next((k for k, f in features.items() if name.replace("_", "-") in f["cli"]), "") or (name if name in features else "") or steps.get(name, "")
        owner = owner if owner in features else ""
        pages[name] = {"name": name, "path": rel, "feature": owner}
        if owner:
            features[owner]["pages"].append(name)
            by_path[rel] = owner
    return {"features": features, "by_path": by_path, "pages": pages, "steps": steps, "primary": primary}


def features_touched(files: Sequence[Mapping[str, Any]], cli_changes: Sequence[Mapping[str, Any]], fmap: Mapping[str, Any],
                     recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per feature the change set touches: new / updated / removed (by its module file) and which parts changed."""
    globs = _globs((recipe.get("wiring") or {}).get("module_globs") or [])
    rows: dict[str, dict[str, Any]] = {}

    def row(key: str) -> dict[str, Any]:
        return rows.setdefault(key, {"feature": key, "state": "updated", "parts": [], "cli_added": [], "cli_removed": []})

    page_dir = str((recipe.get("features") or {}).get("page_dir") or "-").rstrip("/") + "/"
    for f in files:
        path = f["path"]
        if path.endswith("__init__.py"):
            continue
        key = fmap["by_path"].get(path) or (Path(path).stem if path.endswith(".py") and any(g.match(path) for g in globs) else "")
        if not key and path.startswith(page_dir) and path.endswith(".json"):  # a page no module owns is a feature of its own
            rows[f"(page) {Path(path).stem}"] = {"feature": f"(page) {Path(path).stem}", "parts": [f"page {f['status']}"], "cli_added": [],
                                                "cli_removed": [], "state": {"A": "new", "D": "removed"}.get(f["status"], "updated")}
        if not key:
            continue
        r = row(key)
        feature = fmap["features"].get(key) or {}
        part = ("module" if path == feature.get("path") or (not feature and path.endswith(".py")) else
                "recipe" if path == feature.get("recipe") else "tests" if path == feature.get("test") else "page")
        r["parts"].append(f"{part} {f['status']}")
        if part == "module":
            r["state"] = {"A": "new", "D": "removed"}.get(f["status"], "updated")
    for c in cli_changes:
        subs = fmap["primary"].get(c["path"]) or {}
        for name in c.get("subcommands_added") or {}:
            if subs.get(name):
                row(subs[name])["cli_added"].append(name)
        for name in c.get("subcommands_removed") or []:
            rows.setdefault(f"(cli) {name}", {"feature": f"(cli) {name}", "state": "removed", "parts": [], "cli_added": [], "cli_removed": [name]})
    order = {"removed": 0, "new": 1, "updated": 2}
    return sorted(rows.values(), key=lambda r: (order[r["state"]], r["feature"]))


def build_scan(mode: Mapping[str, Any], recipe: Mapping[str, Any]) -> dict[str, Any]:
    files = changed_files(mode)
    areas: dict[str, dict[str, int]] = defaultdict(lambda: {"A": 0, "M": 0, "D": 0, "added": 0, "deleted": 0})
    for f in files:
        f["area"] = area_of(f["path"], recipe.get("areas") or [])
        a = areas[f["area"]]
        a[f["status"] if f["status"] in "AMD" else "M"] += 1
        a["added"] += f["added"]
        a["deleted"] += f["deleted"]
    guards = recipe.get("guards") or {}
    star = re.compile(r"SELECT\s+\*\s+FROM\s+\"?(" + "|".join(map(re.escape, guards.get("sql_star_tables") or ["-"])) + r")\b", re.IGNORECASE)
    lint_skip = set(recipe.get("lint_skip") or [])
    py, configs, skills, tests, docs, findings, cli = [], [], [], [], [], [], []
    removed: list[dict[str, Any]] = []
    page_dir = str((recipe.get("capability") or {}).get("page_dir") or "-").rstrip("/") + "/"
    functions: list[dict[str, Any]] = []
    touched: dict[str, list[str]] = {}
    for f in files:
        path, status = f["path"], f["status"]
        if any(path == bad for bad in guards.get("forbidden_paths") or []):
            findings.append({"kind": "forbidden path", "path": path, "detail": "must not be in a change set"})
        for pat in guards.get("path_patterns") or []:
            if path.startswith(pat) or (pat.startswith("_") and Path(path).name.startswith(pat)):
                findings.append({"kind": "path pattern", "path": path, "detail": f"matches {pat!r}"})
        old = None if status == "A" else blob(mode["old"], path)
        new = None if status == "D" else blob(mode["new"], path)
        for marker in guards.get("text_markers") or []:  # files that describe the marker (rules, notes) are skipped by prefix
            if new and new.count(marker) > (old or "").count(marker) and not path.startswith(tuple(guards.get("text_marker_skip") or ["-"])):
                findings.append({"kind": "text marker", "path": path, "detail": marker})
        if path.endswith(".py"):
            ch = symbol_changes(old, new)
            syms = py_symbols(new)
            rec = {"path": path, "status": status, "area": f["area"], "added_lines": f["added"], "deleted_lines": f["deleted"],
                   "doc": module_doc(new) if new else "", **ch}
            touched[path] = ch["added"] + ch["changed"]
            if status == "D" and path.endswith("__init__.py"):  # the package goes with its __init__
                removed.append({"kind": "package", "name": Path(path).parent.name, "path": path})
            elif status == "D":
                removed.append({"kind": "module" if f["area"] != "tests" else "file", "name": Path(path).stem, "path": path})
            else:  # top-level names only: a method goes with its class
                removed += [{"kind": "function", "name": n, "path": path} for n in ch["removed"] if "." not in n]
            for name in ch["added"] + ch["changed"]:
                if syms[name]["kind"] == "def":
                    functions.append({"path": path, "name": name, "lines": syms[name]["lines"], "line": syms[name]["line"],
                                      "state": "new" if name in ch["added"] else "changed"})
            if new is not None:
                try:
                    ast.parse(new)
                except SyntaxError as exc:
                    findings.append({"kind": "syntax", "path": path, "detail": f"does not parse (line {exc.lineno}: {exc.msg})"})
                if path not in lint_skip:
                    old_msgs = {m.split(":", 3)[-1].strip() for m in (lint(old, path) if old else [])}
                    for msg in lint(new, path):
                        if msg.split(":", 3)[-1].strip() not in old_msgs:  # only what this change set brings
                            findings.append({"kind": "lint", "path": path, "detail": msg.replace(path + ":", "line ")})
                more = _count_broad_except(new) - _count_broad_except(old)
                if more > 0:
                    rec["broad_except_added"] = more
                n_star = len(star.findall(new)) - len(star.findall(old or ""))
                if n_star > 0:
                    findings.append({"kind": "SELECT * on a wide table", "path": path, "detail": f"{n_star} new"})
            a, b = cli_surface(old), cli_surface(new)
            subs_new = {k: v for k, v in b["subcommands"].items() if k not in a["subcommands"]}
            subs_gone = [k for k in a["subcommands"] if k not in b["subcommands"]]
            flags_new = [x for x in b["flags"] if x not in a["flags"]] if status != "A" else b["flags"]
            flags_gone = [x for x in a["flags"] if x not in b["flags"]] if status == "M" else []
            if subs_new or subs_gone or flags_new or flags_gone:
                cli.append({"path": path, "subcommands_added": subs_new, "subcommands_removed": subs_gone, "flags_added": flags_new,
                            "flags_removed": flags_gone})
            if status == "M":  # a deleted CLI file is one removal (the module), not one per subcommand
                removed += [{"kind": "subcommand", "name": n, "path": path} for n in subs_gone]
                removed += [{"kind": "flag", "name": n, "path": path} for n in flags_gone]
            (tests if f["area"] == "tests" else py).append(rec)
        elif path.endswith(".json"):
            rec = {"path": path, "status": status, "area": f["area"], "added_lines": f["added"], "deleted_lines": f["deleted"]}
            rec.update(json_changes(old, new) if status != "D" else {})
            if rec.get("error"):
                findings.append({"kind": "json", "path": path, "detail": rec["error"]})
            configs.append(rec)
            if status == "D":
                removed.append({"kind": "page template" if path.startswith(page_dir) else "file", "name": Path(path).stem, "path": path})
            else:
                removed += [{"kind": "recipe key", "name": k, "path": path} for k in rec.get("removed") or []]
                removed += [{"kind": "list entry", "name": n, "path": path, "list": key}
                            for key, d in (rec.get("lists") or {}).items() for n in d["removed"]]
        elif path.endswith("SKILL.md"):
            skills.append({"path": path, "status": status, "added_lines": f["added"], **skill_meta(new if status != "D" else old)})
            if status == "D":
                removed.append({"kind": "skill", "name": Path(path).parent.name, "path": path})
        else:
            docs.append({"path": path, "status": status, "area": f["area"], "added_lines": f["added"], "deleted_lines": f["deleted"]})
            if status == "D":
                removed.append({"kind": "file", "name": Path(path).name, "path": path})
    if removed:  # what in the working tree still points at each removed thing (a fact to check, not a verdict)
        texts = corpus(recipe)
        for item in removed:
            if item["kind"] == "flag":  # a flag name is shared by many commands: listed, not searched
                item["refs"], item["refs_n"] = "", 0
                continue
            rows = references(**removal_patterns(item["kind"], item["name"], item["path"]), texts=texts)
            item["refs"], item["refs_n"] = _refs_text(rows), sum(n for _, n in rows)
            if rows:
                findings.append({"kind": "removed but still referenced", "path": item["path"],
                                 "detail": f"{item['kind']} `{item['name']}`: {item['refs']}"})
    added = added_lines(mode, files)
    entries = load_lifecycle(recipe)
    findings += deprecated_uses(added, entries, recipe)
    fmap = feature_map(recipe)
    cfg = recipe.get("wiring") or {}
    globs = _globs(cfg.get("module_globs") or [])
    new_modules = [r["path"] for r in py if r["status"] == "A" and any(g.match(r["path"]) for g in globs) and not r["path"].endswith("__init__.py")]
    functions.sort(key=lambda r: -r["lines"])
    head = git("rev-parse", "--short", "HEAD", check=False).strip()
    return {"spec": {"mode": mode["label"], "old": mode["old"], "new": mode["new"], "head": head,
                     "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "files": len(files),
                     "added": sum(f["added"] for f in files), "deleted": sum(f["deleted"] for f in files),
                     "by_status": {s: sum(1 for f in files if f["status"] == s) for s in "AMD"}},
            "areas": [{"area": k, **v} for k, v in sorted(areas.items(), key=lambda kv: -(kv[1]["added"] + kv[1]["deleted"]))],
            "python": py, "tests": tests, "configs": configs, "skills": skills, "docs": docs, "cli": cli,
            "wiring": wiring_rows(new_modules, recipe), "findings": findings,
            "features": features_touched(files, cli, fmap, recipe), "removed": removed, "signals": lifecycle_signals(added, recipe),
            "largest_functions": functions[: int(recipe.get("largest_functions") or 12)],
            "shared_helpers": shared_helpers(touched, recipe)}


def _names(items: Sequence[str], limit: int = 14) -> str:
    return ", ".join(f"`{x}`" for x in items[:limit]) + (f" … +{len(items) - limit}" if len(items) > limit else "")


def scan_md(payload: Mapping[str, Any]) -> str:
    s = payload["spec"]
    out = [f"# Patch scan: {s['mode']}", "",
           f"Created {s['created_utc']}; HEAD `{s['head']}`; {s['files']} files (+{s['added']:,} / -{s['deleted']:,}); "
           f"added {s['by_status']['A']}, modified {s['by_status']['M']}, deleted {s['by_status']['D']}.", "",
           "## Areas", "", "| area | added | modified | deleted | + lines | - lines |", "|---|---|---|---|---|---|"]
    out += [f"| {a['area']} | {a['A']} | {a['M']} | {a['D']} | {a['added']:,} | {a['deleted']:,} |" for a in payload["areas"]]
    out += ["", "## Findings (lint and guards; facts to check, not verdicts)", ""]
    out += [f"- {f['kind']}: `{f['path']}` {f['detail']}" for f in payload["findings"]] or ["- none"]
    feats = payload.get("features") or []
    count = {k: sum(1 for r in feats if r["state"] == k) for k in ("new", "updated", "removed")}
    out += ["", f"## Features touched ({count['new']} new, {count['updated']} updated, {count['removed']} removed)", "",
            "A feature is a module with the parts found by name (recipe, tests, CLI subcommands, page). State is the module file's.",
            "", "| feature | state | parts changed | CLI added |", "|---|---|---|---|"]
    out += [f"| `{r['feature']}` | {r['state']} | {', '.join(r['parts'])} | {', '.join(r['cli_added'])} |" for r in feats] or ["| (none) | | | |"]
    out += ["", "## Removed in this change set (and what still refers to it in the working tree)", ""]
    out += [f"- {r['kind']} `{r['name']}` (`{r['path']}`" + (f", list `{r['list']}`" if r.get("list") else "") + "): "
            + (f"**{r['refs_n']} references**: {r['refs']}" if r.get("refs_n") else "not searched" if r["kind"] == "flag" else "no reference left")
            for r in payload.get("removed") or []] or ["- nothing removed"]
    sig = payload.get("signals") or {"total": 0, "rows": []}
    out += ["", f"## Lifecycle wording in added lines ({sig['total']}; deprecated / retired / legacy ..., to read, not a verdict)", ""]
    out += [f"- `{r['path']}:{r['line']}` [{r['word']}] {r['text']}" for r in sig["rows"]] or ["- none"]
    if sig["total"] > len(sig["rows"]):
        out.append(f"- ... {sig['total'] - len(sig['rows'])} more")
    new_py = [r for r in payload["python"] if r["status"] == "A"]
    mod_py = [r for r in payload["python"] if r["status"] == "M"]
    out += ["", f"## New modules ({len(new_py)})", ""]
    for r in new_py:
        public = [n for n in r["added"] if not n.split(".")[-1].startswith("_")]
        out.append(f"- `{r['path']}` ({r['added_lines']} lines, {len(r['added'])} defs): {r['doc']}  \n  public: {_names(public)}"
                   + (f"  \n  broad `except` handlers: {r['broad_except_added']}" if r.get("broad_except_added") else ""))
    out += ["", f"## Modified modules ({len(mod_py)})", ""]
    for r in mod_py:
        parts = [f"+ {_names(r['added'])}" if r["added"] else "", f"~ {_names(r['changed'])}" if r["changed"] else "",
                 f"- {_names(r['removed'])}" if r["removed"] else ""]
        out.append(f"- `{r['path']}` (+{r['added_lines']} / -{r['deleted_lines']}): " + "; ".join(p for p in parts if p)
                   + (f"; broad `except` +{r['broad_except_added']}" if r.get("broad_except_added") else ""))
    gone = [r for r in payload["python"] if r["status"] == "D"]
    if gone:
        out += ["", "## Deleted modules", ""] + [f"- `{r['path']}`" for r in gone]
    out += ["", "## CLI surface", ""]
    for c in payload["cli"]:
        for name, text in c["subcommands_added"].items():
            out.append(f"- `{Path(c['path']).name} {name}`: {first_sentence(text, 170)}")
        if c["subcommands_removed"]:
            out.append(f"- `{c['path']}` removed: {_names(c['subcommands_removed'])}")
        if c["flags_added"]:
            out.append(f"- `{c['path']}` flags: {_names(c['flags_added'], 30)}")
        if c.get("flags_removed"):
            out.append(f"- `{c['path']}` flags removed: {_names(c['flags_removed'], 30)}")
    if not payload["cli"]:
        out.append("- no change")
    out += ["", "## Wiring of the new modules (blank = not found in the working tree)", "",
            "| module | tests | recipe | cli | refresh | catalog | CLAUDE.md | skills |", "|---|---|---|---|---|---|---|---|"]
    out += [f"| `{w['module']}` | {w['tests']} | {w['recipe'].replace('config/generic_utils/', '')} | {w.get('cli', '')} | {w.get('refresh', '')} | {w.get('catalog', '')} | "
            f"{w.get('CLAUDE.md', '')} | {w['skills']} |" for w in payload["wiring"]] or ["| (no new module in the wiring globs) | | | | | | | |"]
    out += ["", "## Recipes and config", ""]
    for c in payload["configs"]:
        line = f"- `{c['path']}` ({c['status']}, +{c['added_lines']} / -{c['deleted_lines']})"
        if c["status"] == "A":
            line += f": keys {_names(c.get('added') or [], 12)}"
        else:
            bits = [f"+ {_names(c['added'], 8)}" if c.get("added") else "", f"~ {_names(c['changed'], 8)}" if c.get("changed") else "",
                    f"- {_names(c['removed'], 8)}" if c.get("removed") else ""]
            line += ": " + "; ".join(b for b in bits if b)
        out.append(line)
        for key, d in (c.get("lists") or {}).items():
            if c["status"] == "A":
                continue
            bits = [f"+ {_names(d['added'], 20)}" if d["added"] else "", f"~ {_names(d['changed'], 20)}" if d["changed"] else "",
                    f"- {_names(d['removed'], 20)}" if d["removed"] else ""]
            out.append(f"  - `{key}`: " + "; ".join(b for b in bits if b))
    out += ["", "## Skills", ""]
    out += [f"- `{k['name'] or Path(k['path']).parent.name}` ({k['status']}, +{k['added_lines']}): {first_sentence(k['description'], 200)}"
            for k in payload["skills"]] or ["- no change"]
    out += ["", f"## Tests ({len(payload['tests'])} files)", ""]
    for r in payload["tests"]:
        cases = [n for n in r["added"] if "." in n and n.split(".")[-1].startswith("test")]
        out.append(f"- `{r['path']}` ({r['status']}, +{r['added_lines']}): {len(cases)} new test methods"
                   + (f"; changed {_names(r['changed'], 6)}" if r["changed"] else ""))
    out += ["", "## Longest new / changed functions (size only)", "", "| function | lines | state |", "|---|---|---|"]
    out += [f"| `{f['path']}:{f['line']}` `{f['name']}` | {f['lines']} | {f['state']} |" for f in payload["largest_functions"]]
    out += ["", "## Helper names the touched modules share with many others (one shared helper is a candidate)", ""]
    out += [f"- `{h['name']}`: {h['modules']} modules ({h['in']})" for h in payload["shared_helpers"]] or ["- none"]
    out += ["", f"## Docs, prompts and other files ({len(payload['docs'])})", ""]
    out += [f"- `{d['path']}` ({d['status']}, +{d['added_lines']} / -{d['deleted_lines']})" for d in payload["docs"]]
    return "\n".join(out) + "\n"


def skeleton_md(payload: Mapping[str, Any], number: int, inventory: str) -> str:
    s = payload["spec"]
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = "\n".join(f"| {a['area']} | {a['A']} | {a['M']} | {a['D']} | +{a['added']:,} / -{a['deleted']:,} |" for a in payload["areas"])
    change = {"new": "added", "updated": "updated", "removed": "removed"}
    feats = "\n".join([f"| `{r['feature']}` | {change[r['state']]} | {', '.join(r['parts'] + ['cli ' + c for c in r['cli_added']])} | |"
                       for r in payload.get("features") or []]
                      + [f"| `{r['name']}` ({r['kind']}) | removed | `{r['path']}` | {r.get('refs_n') or 0} references left |"
                         for r in payload.get("removed") or []])
    return f"""# Patch {number:04d}: <title>

| | |
|---|---|
| Date | {day} |
| Commit | (pending; base `{s['head']}`) |
| Change set | {s['mode']}: {s['files']} files, +{s['added']:,} / -{s['deleted']:,} |
| Scan | `{inventory}` |

## Summary

<What the operator can do now that they could not before, in four to six sentences. Lead with the flows, not the files.>

## What changed for the operator

<One block per feature: what it answers, how to run it (command / paste / skill), what it reads and writes, how it joins the
daily flow, its limits. Group manual flows and agent flows.>

## Feature changes

<One row per feature this patch adds, updates, renames, moves out of the default flow, deprecates or removes. The rows
below come from the scan (module files); fill the last column, drop rows that changed nothing a user of the feature
would notice, and add what a file diff cannot show: a renamed column, a changed default, a page that is now on demand,
a removal the user asked for. Change is one of: added, updated, renamed, on_demand, deprecated, removal_requested,
removed. Every row that is not added / updated also goes into `lifecycle.json`.>

| feature | change | parts | what a user of it must know |
|---|---|---|---|
{feats}

## Technical changes

| area | added | modified | deleted | lines |
|---|---|---|---|---|
{rows}

<Per area: the modules, the functions that matter, behaviour changes in existing code, schema / column renames, recipe keys.>

## Review

<Findings from reading the code and running it: performance (measured where possible), correctness, quality, conventions.
One row each: severity, where, what, status (fixed in this patch / open). Say what was not reviewed.>

| # | severity | where | finding | status |
|---|---|---|---|---|

## Tests and checks

<Commands run and their result. Lint and guard findings from the scan and what was done about each.>

## Follow-ups

<Open items with enough detail to pick up cold.>
"""


def write_scan(payload: Mapping[str, Any], out_dir: str | Path | None, recipe: Mapping[str, Any]) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    slug = re.sub(r"[^a-z0-9]+", "-", payload["spec"]["mode"].lower()).strip("-")
    out = Path(out_dir) if out_dir else REPO_ROOT / str(recipe.get("out_root") or "logs/patch_notes") / f"{stamp}_{slug}"
    out.mkdir(parents=True, exist_ok=True)
    inv = out / "inventory.md"
    inv.write_text(scan_md(payload), encoding="utf-8")
    (out / "inventory.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    number = next_number(REPO_ROOT / str(recipe.get("notes_dir") or "documentation/patch_notes"))
    rel = inv.relative_to(REPO_ROOT).as_posix() if inv.is_relative_to(REPO_ROOT) else inv.as_posix()
    (out / "patch_note.skeleton.md").write_text(skeleton_md(payload, number, rel), encoding="utf-8")
    s = payload["spec"]
    return {"out_dir": out.as_posix(), "inventory": inv.as_posix(), "skeleton": (out / "patch_note.skeleton.md").as_posix(),
            "next_patch": f"{number:04d}", "files": s["files"], "added": s["added"], "deleted": s["deleted"],
            "new_modules": sum(1 for r in payload["python"] if r["status"] == "A"), "findings": len(payload["findings"])}


# --- capability index ---------------------------------------------------------------------------------------


def _cell(text: Any) -> str:
    """Text safe inside a markdown table cell (a pipe would start a new column)."""
    return str(text).replace("|", "\\|")


def _py_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.py") if "__pycache__" not in p.parts and p.name != "__init__.py")


def capabilities_md(recipe: Mapping[str, Any]) -> str:
    cap = recipe.get("capability") or {}
    rel = lambda p: p.relative_to(REPO_ROOT).as_posix()  # noqa: E731
    tests_dir = REPO_ROOT / str(cap.get("tests_dir") or "src/tests")
    tests = {p.name for p in tests_dir.glob("test_*.py")} if tests_dir.exists() else set()
    recipes = {p.stem: rel(p) for d in cap.get("recipe_dirs") or [] for p in sorted((REPO_ROOT / d).glob("*.json"))}
    head = git("rev-parse", "--short", "HEAD", check=False).strip()
    out = ["# Capability index (generated)", "",
           f"Generated by `python src/generic_utils/patch_scan.py capabilities` on {datetime.now(timezone.utc).strftime('%Y-%m-%d')} "
           f"from the working tree (HEAD `{head}`). Do not edit by hand: rerun the command. Grep this file for a verb or a "
           "noun before writing a new tool; the patch notes say why each piece exists.", ""]

    out += ["## Runners (entry points)", "", "| command | what it does | flags |", "|---|---|---|"]
    for p in sorted(REPO_ROOT.glob(str(cap.get("runner_glob") or "src/run_*.py"))) + [REPO_ROOT / "src" / "main.py"]:
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        surf = cli_surface(src)
        subs = ("subcommands: " + ", ".join(surf["subcommands"]) + "; ") if surf["subcommands"] else ""
        out.append(f"| `python {rel(p)}` | {_cell(module_doc(src))} | {subs}{' '.join(surf['flags'][:14])}{' …' if len(surf['flags']) > 14 else ''} |")

    for p in sorted((REPO_ROOT / "src").rglob("*.py")):
        if "__pycache__" in p.parts or "tests" in p.parts or p.name.startswith("run_"):
            continue
        surf = cli_surface(p.read_text(encoding="utf-8", errors="replace"))
        if len(surf["subcommands"]) >= 2:
            out += ["", f"## CLI `python {rel(p)} <subcommand>` ({len(surf['subcommands'])})", "", "| subcommand | help |", "|---|---|"]
            out += [f"| `{name}` | {_cell(first_sentence(text, 230))} |" for name, text in sorted(surf["subcommands"].items())]

    for d in cap.get("detail_packages") or []:
        folder = REPO_ROOT / d
        files = _py_files(folder) if folder.exists() else []
        out += ["", f"## Modules in `{d}` ({len(files)})", "", "| module | lines | purpose | recipe | test |", "|---|---|---|---|---|"]
        for p in files:
            src = p.read_text(encoding="utf-8", errors="replace")
            test = f"test_{p.stem}.py"
            out.append(f"| `{p.relative_to(folder).as_posix()}` | {src.count(chr(10)) + 1} | {_cell(module_doc(src))} | "
                       f"{'`' + recipes[p.stem] + '`' if p.stem in recipes else ''} | {test if test in tests else ''} |")
    for d in cap.get("other_packages") or []:
        folder = REPO_ROOT / d
        files = _py_files(folder) if folder.exists() else []
        if not files:
            continue
        out += ["", f"## Modules in `{d}` ({len(files)})", ""]
        for p in files:
            src = p.read_text(encoding="utf-8", errors="replace")
            doc = module_doc(src)
            out.append(f"- `{p.relative_to(folder).as_posix()}` ({src.count(chr(10)) + 1} lines)" + (f": {doc}" if doc else ""))

    refresh_path = REPO_ROOT / str(cap.get("refresh") or "")
    if refresh_path.is_file():
        cfg = json.loads(refresh_path.read_text(encoding="utf-8"))
        out += ["", f"## Daily refresh graph (`{rel(refresh_path)}`)", "",
                f"Default pages: {', '.join(cfg.get('templates') or [])}. All pages: {', '.join(cfg.get('templates_all') or [])}.", ""]
        for stage in ("producers", "suites"):
            out += [f"### {stage}", "", "| step | after | optional | command |", "|---|---|---|---|"]
            for t in cfg.get(stage) or []:
                cmd = " ".join(str(a) for a in t.get("argv") or t.get("cmd") or [])
                out.append(f"| `{t.get('name')}` | {', '.join(t.get('after') or [])} | {'yes' if t.get('optional') else ''} | `{_cell(cmd[:150])}` |")
            out.append("")

    out += ["", f"## Recipes ({len(recipes)})", "", "| recipe | top-level keys |", "|---|---|"]
    for stem, path in sorted(recipes.items()):
        try:
            data = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
            keys = ", ".join(list(data)[:14]) + (" …" if len(data) > 14 else "") if isinstance(data, dict) else "(list)"
        except json.JSONDecodeError:
            keys = "(not valid JSON)"
        out.append(f"| `{path}` | {_cell(keys)} |")

    pages = REPO_ROOT / str(cap.get("page_dir") or "")
    if pages.is_dir():
        out += ["", "## Page templates", "", "| template | page name | tabs | requires |", "|---|---|---|---|"]
        for p in sorted(pages.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            tabs = [str(t.get("label")) for t in data.get("tabs") or [] if isinstance(t, dict)]
            req = data.get("requires")
            out.append(f"| `{p.stem}` | {_cell(data.get('name') or '')} | {_cell(', '.join(tabs)[:160])} | {req if isinstance(req, str) else ', '.join(req or [])} |")

    skills_dir = REPO_ROOT / str(cap.get("skills_dir") or ".claude/skills")
    found = sorted(skills_dir.glob("*/SKILL.md")) if skills_dir.exists() else []
    out += ["", f"## Skills ({len(found)})", "", "| skill | use it for |", "|---|---|"]
    for p in found:
        meta = skill_meta(p.read_text(encoding="utf-8", errors="replace"))
        out.append(f"| `{meta['name'] or p.parent.name}` | {_cell(first_sentence(meta['description'], 260))} |")

    prompts = REPO_ROOT / str(cap.get("prompt_dir") or "")
    if prompts.is_dir():
        out += ["", "## Agent run prompts", ""] + [f"- `{rel(p)}`" for p in sorted(prompts.glob("*.txt"))]
    catalog = REPO_ROOT / str(cap.get("catalog") or "")
    if catalog.is_file():
        blocks = re.findall(r"^--- (.+?) ---\s*$", catalog.read_text(encoding="utf-8", errors="replace"), flags=re.MULTILINE)
        out += ["", f"## Run catalog blocks (`{rel(catalog)}`, {len(blocks)})", ""] + [f"- {b}" for b in blocks]

    out += ["", "## Documentation", ""]
    for d in cap.get("doc_dirs") or []:
        folder = REPO_ROOT / d
        if folder.is_dir():
            out.append(f"- `{d}/`: " + ", ".join(p.name for p in sorted(folder.glob("*.md"))))
    out += ["", f"## Tests ({len(tests)} files in `{rel(tests_dir)}`)", "", "Run `python -m pytest src/tests -q`; one file per module where the module table names it."]
    return "\n".join(out) + "\n"


# --- commit history -----------------------------------------------------------------------------------------


def history_md(recipe: Mapping[str, Any]) -> str:
    areas = recipe.get("areas") or []
    log = git("log", "--reverse", "--date=short", "--no-renames", "--numstat", "--format=@@%h%x09%ad%x09%s")
    added_log = git("log", "--reverse", "--no-renames", "--diff-filter=A", "--name-only", "--format=@@%h")
    new_files: dict[str, list[str]] = defaultdict(list)
    cur = ""
    for line in added_log.splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip()
        elif line.strip():
            new_files[cur].append(line.strip())
    commits: list[dict[str, Any]] = []
    for line in log.splitlines():
        if line.startswith("@@"):
            sha, day, subject = (line[2:].split("\t", 2) + ["", ""])[:3]
            commits.append({"sha": sha, "day": day, "subject": subject, "files": 0, "added": 0, "deleted": 0, "areas": defaultdict(int)})
        elif line.strip() and commits:
            add, dele, path = (line.split("\t") + ["", ""])[:3]
            c = commits[-1]
            c["files"] += 1
            c["added"] += int(add) if add.isdigit() else 0
            c["deleted"] += int(dele) if dele.isdigit() else 0
            c["areas"][area_of(path, areas)] += 1
    out = ["# Commit history (generated)", "",
           f"Generated by `python src/generic_utils/patch_scan.py history`: {len(commits)} commits, oldest first. Facts from git only "
           "(size, areas by files touched, files added). What each commit meant is in the numbered patch notes.", ""]
    for n, c in enumerate(commits, 1):
        top = sorted(c["areas"].items(), key=lambda kv: -kv[1])[:6]
        new = new_files.get(c["sha"], [])
        mods = [p for p in new if p.endswith(".py") and "/tests/" not in p and not p.endswith("__init__.py")]
        skills = [Path(p).parent.name for p in new if p.endswith("SKILL.md")]
        docs = [Path(p).name for p in new if p.startswith("documentation/") and p.endswith(".md")]
        tests = [p for p in new if "/tests/" in p and p.endswith(".py")]
        out += [f"### {n}. {c['day']} `{c['sha']}` {c['subject']}", "",
                f"- {c['files']} files, +{c['added']:,} / -{c['deleted']:,}; areas: " + ", ".join(f"{a} {k}" for a, k in top)]
        if mods:
            out.append(f"- new modules ({len(mods)}): " + ", ".join(f"`{p.removeprefix('src/')}`" for p in mods[:12]) + (" …" if len(mods) > 12 else ""))
        if skills:
            out.append(f"- new skills ({len(skills)}): " + ", ".join(skills))
        if tests:
            out.append(f"- new test files: {len(tests)}")
        if docs:
            out.append(f"- new docs ({len(docs)}): " + ", ".join(docs[:10]) + (" …" if len(docs) > 10 else ""))
        out.append("")
    return "\n".join(out)


# --- feature register ---------------------------------------------------------------------------------------


def path_history() -> dict[str, Any]:
    """{'commits': [{n, sha, day, subject}], 'events': {path: [(n, code)]}, 'renamed': {old: new}} from one ``git log``,
    oldest first. A rename carries the old path's events to the new path, so a moved file keeps its first date."""
    log = git("log", "--reverse", "--date=short", "-M", "--name-status", "--format=@@%h%x09%ad%x09%s")
    commits: list[dict[str, Any]] = []
    events: dict[str, list[tuple[int, str]]] = defaultdict(list)
    renamed: dict[str, str] = {}
    for line in log.splitlines():
        if line.startswith("@@"):
            sha, day, subject = (line[2:].split("\t", 2) + ["", ""])[:3]
            commits.append({"n": len(commits) + 1, "sha": sha, "day": day, "subject": subject})
        elif line.strip() and commits:
            parts = line.split("\t")
            code, n = parts[0][:1], commits[-1]["n"]
            if code == "R" and len(parts) >= 3:
                events[parts[2]] = events.pop(parts[1], []) + [(n, "R")]
                renamed[parts[1]] = parts[2]
            elif code == "C" and len(parts) >= 3:
                events[parts[2]].append((n, "A"))
            elif len(parts) >= 2:
                events[parts[1]].append((n, code))
    return {"commits": commits, "events": dict(events), "renamed": renamed}


def patch_index(notes_dir: Path, commits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The numbered patch files: [{number, file, title, commit_n, pending}]. ``commit_n`` is the index of the commit the
    patch ends at, read from its header's Commit row (the hash after 'base' is the parent, not the patch) or, for the
    baseline, from its title. ``pending`` = the row says pending and names no commit yet."""
    def index_of(sha: str) -> int | None:
        return next((c["n"] for c in commits if c["sha"] and (c["sha"].startswith(sha) or sha.startswith(c["sha"]))), None)

    out = []
    for p in sorted(notes_dir.glob("*.md")):
        m = NOTE_NAME.match(p.name)
        if not m:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        title = text.splitlines()[0].lstrip("# ").strip() if text.strip() else p.name
        row = re.search(r"^\|\s*Commit\s*\|(.*)\|\s*$", text, flags=re.MULTILINE)
        cell = row.group(1) if row else title
        found = [n for h in re.findall(r"`([0-9a-f]{7,40})`", re.sub(r"base\s+`[0-9a-f]{7,40}`", "", cell)) if (n := index_of(h))]
        out.append({"number": m.group(1), "file": p.name, "title": title, "commit_n": max(found) if found else None,
                    "pending": bool(row) and "pending" in cell.lower() and not found})
    return out


def patch_of(n: int | None, patches: Sequence[Mapping[str, Any]]) -> str:
    """The patch a commit belongs to: the first patch that ends at or after it. A later commit, or uncommitted work
    (``n`` None), belongs to the pending patch, written with a star; '-' when no patch file is pending."""
    if n is not None:
        for p in sorted((p for p in patches if p["commit_n"]), key=lambda p: p["commit_n"]):
            if n <= p["commit_n"]:
                return str(p["number"])
    pending = [p for p in patches if p["pending"]]
    return f"{pending[-1]['number']}*" if pending else "-"


def surface_history(path: str, extract: Any, hist: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """{name: {first, gone, gone_n}} for the names ``extract(text)`` finds in each committed version of one file and then
    in the working tree (``first`` / ``gone_n`` None = not committed yet). A name that comes back is no longer gone."""
    by_n = {c["n"]: c["sha"] for c in hist["commits"]}
    steps: list[tuple[int | None, str | None]] = [(n, blob(by_n[n], path)) for n, code in hist["events"].get(path, []) if code != "D"]
    steps.append((None, _read(path) or None))
    out: dict[str, dict[str, Any]] = {}
    for n, text in steps:
        if text is None:  # before a rename the file lived elsewhere
            continue
        try:
            names = set(extract(text) or [])
        except (ValueError, TypeError, AttributeError):  # a version that does not parse says nothing
            continue
        for name in names:
            if name not in out:
                out[name] = {"first": n, "gone": False, "gone_n": None}
            out[name]["gone"] = False
        for name, rec in out.items():
            if name not in names and not rec["gone"]:
                rec["gone"], rec["gone_n"] = True, n
    return out


def _graph_steps(text: str, stages: Sequence[str]) -> list[str]:
    data = json.loads(text)
    return [str(t.get("name")) for s in stages for t in data.get(s) or [] if isinstance(t, dict)]


def features_md(recipe: Mapping[str, Any]) -> str:
    """The feature register: every feature with when it arrived, when it last changed, in which patches, and its status."""
    cfg, wiring = recipe.get("features") or {}, recipe.get("wiring") or {}
    hist = path_history()
    by_n = {c["n"]: c for c in hist["commits"]}
    patches = patch_index(REPO_ROOT / str(recipe.get("notes_dir") or "documentation/patch_notes"), hist["commits"])
    fmap = feature_map(recipe)
    entries = load_lifecycle(recipe)
    untracked = set(git("ls-files", "--others", "--exclude-standard").splitlines())
    dirty = set(git("diff", "HEAD", "--name-only", "--no-renames").splitlines()) | untracked
    in_head = set(git("ls-tree", "-r", "--name-only", "HEAD").splitlines())

    def when(n: int | None) -> str:
        return f"{by_n[n]['day']} `{by_n[n]['sha']}`" if n in by_n else "not committed"

    def life(path: str, *more: str) -> dict[str, Any]:
        own = hist["events"].get(path, [])
        touched = sorted({n for p in (path, *more) if p for n, _ in hist["events"].get(p, [])})
        open_work = any(p in dirty for p in (path, *more) if p)
        seen = sorted({patch_of(n, patches) for n in touched} | ({patch_of(None, patches)} if open_work else set()))
        return {"since": when(own[0][0] if own else None), "last": "not committed" if open_work else when(touched[-1] if touched else None),
                "commits": len(own), "patches": ", ".join(seen),
                "state": "new" if path not in in_head else "changed" if open_work else "active"}

    def declared(names: Sequence[str], kinds: Sequence[str]) -> str:
        for e in entries:
            if (e.get("kind") or "") in kinds and any(n in e["names"] for n in names):
                return f"**{e['status']}** ({e.get('patch') or '?'})"
        return ""

    head = git("rev-parse", "--short", "HEAD", check=False).strip()
    pend = [p["number"] for p in patches if p["pending"]]
    out = ["# Feature register (generated)", "",
           f"Generated by `python src/generic_utils/patch_scan.py features` on {datetime.now(timezone.utc).strftime('%Y-%m-%d')} "
           f"from git history, the working tree (HEAD `{head}`) and `lifecycle.json`. Do not edit by hand: rerun the command.", "",
           "- A **feature** is one module with the parts found by name: its CLI subcommands, the refresh steps that run it, its page.",
           "- **since** / **last change** are commits (a renamed file keeps its first date). **changed in** lists patch numbers: "
           "`0000` is the baseline; a star marks the pending patch" + (f" (`{pend[-1]}`)" if pend else "") + ", not committed yet.",
           "- **status**: `active`, `new` (not in HEAD yet), `changed` (differs from HEAD), or a declared status in bold from "
           "`lifecycle.json` (`on_demand`, `renamed`, `deprecated`, `removal_requested`, `removed`).", ""]

    texts = corpus(recipe)
    out += ["## Declared lifecycle (`lifecycle.json`)", "",
            "| item | kind | status | patch | replaced by | references now | note |", "|---|---|---|---|---|---|---|"]
    for e in entries:
        refs = "no pattern"
        if e.get("patterns"):
            allow = tuple(e.get("allow") or [])  # path prefixes where a reference is expected (the fallback reader)
            rows = [r for r in references(e["patterns"], texts) if not (allow and r[0].startswith(allow))]
            refs = _refs_text(rows, 3) if rows else "none outside the allowed files" if allow else "none"
        out.append(f"| {_cell(', '.join(f'`{n}`' for n in e['names']))} | {e.get('kind') or ''} | {e['status']} | {e.get('patch') or ''} | "
                   f"{_cell(e.get('replaced_by') or '')} | {_cell(refs)} | {_cell(e.get('note') or '')} |")
    if not entries:
        out.append("| (none declared) | | | | | | |")

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in fmap["features"].values():
        groups[str(Path(f["path"]).parent.as_posix())].append(f)
    for folder in sorted(groups):
        rows = groups[folder]
        out += ["", f"## Features in `{folder}` ({len(rows)})", "",
                "| feature | what | entry points | since | last change | commits | changed in | status |", "|---|---|---|---|---|---|---|---|"]
        for f in rows:
            lf = life(f["path"], f["recipe"])
            entry = "; ".join(x for x in ("cli " + ", ".join(f"`{c}`" for c in f["cli"]) if f["cli"] else "",
                                          "step " + ", ".join(f"`{c}`" for c in f["steps"]) if f["steps"] else "",
                                          "page " + ", ".join(f"`{c}`" for c in f["pages"]) if f["pages"] else "") if x)
            status = declared([f["key"], *f["cli"]], ("module", "feature", "cli", "runner")) or lf["state"]
            out.append(f"| `{f['key']}` | {_cell(first_sentence(f['doc'], 90))} | {entry} | {lf['since']} | {lf['last']} | {lf['commits']} | "
                       f"{lf['patches']} | {status} |")

    refresh = str((wiring.get("mentions") or {}).get("refresh") or "")
    stages = cfg.get("refresh_stages") or ["producers", "suites"]
    graph = json.loads(_read(refresh)) if refresh and (REPO_ROOT / refresh).is_file() else {}
    default, every = list(graph.get("templates") or []), list(graph.get("templates_all") or [])
    out += ["", f"## Page templates ({len(fmap['pages'])})", "",
            "| template | rendered | feature | since | last change | changed in | status |", "|---|---|---|---|---|---|---|"]
    for name, pg in fmap["pages"].items():
        lf = life(pg["path"])
        where = "every refresh" if name in default else "on demand" if name in every else "by hand (`page --template`)"
        out.append(f"| `{name}` | {where} | {'`' + pg['feature'] + '`' if pg['feature'] else ''} | {lf['since']} | {lf['last']} | {lf['patches']} | "
                   f"{declared([name], ('page',)) or lf['state']} |")

    skills_dir = REPO_ROOT / str(wiring.get("skills_dir") or ".claude/skills")
    found = sorted(skills_dir.glob("*/SKILL.md")) if skills_dir.exists() else []
    out += ["", f"## Skills ({len(found)})", "", "| skill | since | last change | commits | changed in | status |", "|---|---|---|---|---|---|"]
    for p in found:
        lf = life(p.relative_to(REPO_ROOT).as_posix())
        out.append(f"| `{p.parent.name}` | {lf['since']} | {lf['last']} | {lf['commits']} | {lf['patches']} | {declared([p.parent.name], ('skill',)) or lf['state']} |")

    def arrivals(title: str, surf: Mapping[str, Mapping[str, Any]], left: str = "removed") -> list[str]:
        order = lambda v: (v is None, v or 0)  # noqa: E731  (not committed sorts last)
        first: dict[Any, list[str]] = defaultdict(list)
        gone: dict[Any, list[str]] = defaultdict(list)
        for name, rec in surf.items():
            (gone[rec["gone_n"]] if rec["gone"] else first[rec["first"]]).append(name)
        lines = ["", f"### {title} ({sum(len(v) for v in first.values())} live)", ""]
        lines += [f"- {when(n)} ({patch_of(n, patches)}): " + ", ".join(f"`{x}`" for x in sorted(first[n])) for n in sorted(first, key=order)]
        lines += [f"- **{left}** {when(n)} ({patch_of(n, patches)}): " + ", ".join(f"`{x}`" for x in sorted(gone[n])) for n in sorted(gone, key=order)]
        return lines

    out += ["", "## First seen and removed, by commit", ""]
    if refresh:
        out += arrivals(f"Refresh steps in `{refresh}`", surface_history(refresh, lambda t: _graph_steps(t, stages), hist))
        out += arrivals("Pages rendered on every refresh (`templates`)", surface_history(refresh, lambda t: json.loads(t).get("templates") or [], hist),
                        left="left the default set (the template may still exist: see the table above)")
    for cli_file in fmap["primary"]:
        out += arrivals(f"Subcommands of `{cli_file}`", surface_history(cli_file, lambda t: cli_surface(t)["subcommands"], hist))

    prefixes = tuple(cfg.get("removed_prefixes") or ["src/", "config/", ".claude/skills/", "scripts/"])
    gone_files: dict[int, list[str]] = defaultdict(list)
    for path, ev in hist["events"].items():
        if ev and ev[-1][1] == "D" and path.startswith(prefixes) and "__pycache__" not in path and not (REPO_ROOT / path).exists():
            gone_files[ev[-1][0]].append(path)
    out += ["", f"## Files removed over the history ({sum(len(v) for v in gone_files.values())}; "
                f"{len(hist['renamed'])} renames are carried as one file and not listed)", ""]
    limit = int(cfg.get("removed_names_per_commit") or 12)
    for n in sorted(gone_files):
        names = sorted(gone_files[n])
        out.append(f"- {when(n)} ({patch_of(n, patches)}) {by_n[n]['subject'][:70]}: " + ", ".join(f"`{x}`" for x in names[:limit])
                   + (f" … +{len(names) - limit}" if len(names) > limit else ""))
    if not gone_files:
        out.append("- none")
    return "\n".join(out) + "\n"


# --- CLI ----------------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Patch scan: change-set inventory, capability index and commit history for the patch notes.")
    parser.add_argument("--recipe", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sc = sub.add_parser("scan", help="Inventory of one change set (default: the staged index against HEAD).")
    sc.add_argument("--staged", action="store_true", help="The staged index against HEAD (default).")
    sc.add_argument("--worktree", action="store_true", help="Everything in the working tree against HEAD (staged and unstaged).")
    sc.add_argument("--commit", default=None, help="One commit against its parent.")
    sc.add_argument("--range", dest="rng", default=None, help="A..B (two commits).")
    sc.add_argument("--out-dir", default=None)
    ca = sub.add_parser("capabilities", help="Rewrite the generated capability index.")
    ca.add_argument("--out", default=None)
    hi = sub.add_parser("history", help="Rewrite the generated commit history.")
    hi.add_argument("--out", default=None)
    fe = sub.add_parser("features", help="Rewrite the generated feature register (since / last change / patches / status per feature, removals).")
    fe.add_argument("--out", default=None)
    sub.add_parser("regen", help="Rewrite the three generated files: capability index, commit history, feature register.")
    args = parser.parse_args(argv)
    recipe = load_recipe(args.recipe)
    notes = REPO_ROOT / str(recipe.get("notes_dir") or "documentation/patch_notes")
    generated = {"capabilities": ("capability_index.md", capabilities_md), "history": ("commit_history.md", history_md),
                 "features": ("feature_register.md", features_md)}

    def write(command: str, out: str | None = None) -> dict[str, Any]:
        name, build = generated[command]
        text = build(recipe)
        target = Path(out) if out else notes / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return {"out": target.as_posix(), "bytes": len(text.encode("utf-8")), "lines": text.count("\n")}

    if args.command == "scan":
        mode = resolve_mode(staged=args.staged, worktree=args.worktree, commit=args.commit, rng=args.rng)
        result = write_scan(build_scan(mode, recipe), args.out_dir, recipe)
    elif args.command == "regen":
        result = {command: write(command) for command in generated}
    else:
        result = write(args.command, args.out)
    print(json.dumps(result, indent=1))
    return result


def patch_scan_local_main() -> dict[str, Any]:
    """No-arg default: the staged change set."""
    return main(["scan"])


if __name__ == "__main__":
    if str(REPO_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "src"))
    main()
