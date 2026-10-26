"""Run subprocess steps as a dependency graph. Generic: no step names or paths baked in.

Each step is {"name", "argv", "after": [names], "optional": bool}. A step starts
once every step in `after` has finished OK. Independent steps run in parallel up
to `max_workers` (1 = the old serial order). A failed step skips its dependents;
an `optional` failure does not. Every step logs to <log_dir>/<name>.log and the
result carries start / end / seconds / rc so a run can be timed afterwards.
"""

from __future__ import annotations

import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def substitute(value: Any, mapping: Mapping[str, str]) -> Any:
    """Replace {KEY} placeholders in strings, lists, and dicts."""
    if isinstance(value, str):
        for key, sub in mapping.items():
            value = value.replace("{" + key + "}", sub)
        return value
    if isinstance(value, list):
        return [substitute(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, mapping) for k, v in value.items()}
    return value


def validate_steps(steps: Sequence[Mapping[str, Any]]) -> None:
    names = [str(s["name"]) for s in steps]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate step names: {names}")
    known = set(names)
    for step in steps:
        missing = [a for a in step.get("after") or [] if a not in known]
        if missing:
            raise ValueError(f"Step {step['name']!r} waits on unknown steps {missing}")


def run_dag(
    steps: Sequence[Mapping[str, Any]],
    *,
    log_dir: str | Path,
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    max_workers: int = 4,
    python: str | None = None,
    echo: bool = True,
) -> list[dict[str, Any]]:
    """Run steps; argv[0] == "python" is replaced by the current interpreter."""
    validate_steps(steps)
    log_root = Path(log_dir)
    log_root.mkdir(parents=True, exist_ok=True)
    interpreter = python or sys.executable
    pending = {str(s["name"]): dict(s) for s in steps}
    status: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}

    def run_one(step: Mapping[str, Any]) -> dict[str, Any]:
        argv = [interpreter if a == "python" else a for a in step["argv"]]
        log_path = log_root / f"{step['name']}.log"
        start = time.time()
        started = _stamp()
        if echo:
            print(f"=== {started} START {step['name']}", flush=True)
        with log_path.open("w", encoding="utf-8") as handle:
            try:
                rc = subprocess.run(argv, cwd=str(cwd), env=dict(env) if env else None,
                                    stdout=handle, stderr=subprocess.STDOUT).returncode
            except OSError as exc:  # could not start (missing binary, bad cwd): fail this step only
                print(f"could not start: {exc}", file=handle)
                rc = -1
        seconds = round(time.time() - start, 1)
        if echo:
            print(f"=== {_stamp()} END {step['name']} rc={rc} {seconds}s", flush=True)
        return {"name": step["name"], "rc": rc, "start": started, "end": _stamp(),
                "seconds": seconds, "log": log_path.as_posix(), "optional": bool(step.get("optional"))}

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        running: dict[Future, str] = {}
        while pending or running:
            for name, step in list(pending.items()):
                deps = step.get("after") or []
                if any(status.get(d) in ("failed", "skipped") for d in deps):
                    status[name] = "skipped"
                    results[name] = {"name": name, "rc": None, "skipped_because": [d for d in deps if status.get(d) in ("failed", "skipped")]}
                    del pending[name]
                elif all(status.get(d) == "ok" for d in deps) and len(running) < max(1, max_workers):
                    running[pool.submit(run_one, step)] = name
                    del pending[name]
            if not running:
                if pending:
                    raise RuntimeError(f"Steps cannot start (cycle?): {sorted(pending)}")
                break
            done, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for future in done:
                name = running.pop(future)
                result = future.result()
                results[name] = result
                ok = result["rc"] == 0 or result["optional"]
                status[name] = "ok" if ok else "failed"
    return [results[str(s["name"])] for s in steps]
