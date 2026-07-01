#!/usr/bin/env python3
"""report.py -- U3: shape run meta + the 5 probe results into one report dict,
and render that dict to markdown. `build_report` is pure (no I/O of its own)
so it's trivial to unit test; `_provenance` is the one exception -- kernel/
perf-version/git-sha genuinely require a syscall or subprocess, so that bit
is isolated here instead of leaking shell-outs into the rest of the module.
"""
import platform
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

PROBE_ORDER = ["energy", "mem_bw", "imbalance", "io", "comm"]


def _provenance(probes):
    # ponytail: best-effort, never let missing perf/git break report building.
    try:
        out = subprocess.run(["perf", "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
        perf_version = out or "unavailable"
    except Exception:
        perf_version = "unavailable"

    try:
        cp = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, cwd=_REPO_ROOT,
        )
        # a failed rev-parse (e.g. zero-commit repo) still prints the literal
        # arg "HEAD" to stdout with a non-zero exit code -- check returncode,
        # not just whether stdout is non-empty.
        harness_git_sha = cp.stdout.strip() if cp.returncode == 0 and cp.stdout.strip() else "unknown"
    except Exception:
        harness_git_sha = "unknown"

    return {
        "kernel": platform.release(),
        "perf_version": perf_version,
        # which mechanism actually backed each probe's number, e.g.
        # {"energy": "perf_stat", "comm": None} when comm was unavailable.
        "privilege_path": {name: p.get("source") for name, p in probes.items()},
        "harness_git_sha": harness_git_sha,
        "host": platform.node(),
    }


def build_report(meta, probes):
    """meta: {command, pid, pgid, exit_code, wall_s}. probes: the 5-entry
    dict from harness/probes.py. Pure merge + provenance -- no other I/O."""
    report = dict(meta)
    report["probes"] = probes
    report["provenance"] = _provenance(probes)
    return report


def _fmt_probe_line(name, p):
    if p.get("status") != "ok":
        reason = p.get("reason", "no reason given")
        return f"- **{name}**: unavailable: {reason}"
    value, unit, source = p.get("value"), p.get("unit"), p.get("source")
    if value is None or unit is None:
        return f"- **{name}**: unavailable: status ok but no value/unit recorded"
    return f"- **{name}**: {value} {unit} (via {source})"


def render_markdown(report):
    """Plain f-strings, no template engine -- this is the whole markdown."""
    wall_s = report.get("wall_s")
    wall_str = f"{wall_s:.3f}s" if isinstance(wall_s, (int, float)) else "unknown"
    exit_code = report.get("exit_code")

    probes = report.get("probes", {})
    # canonical 5 first, in order; anything unexpected still shows up after.
    names = [n for n in PROBE_ORDER if n in probes] + [n for n in probes if n not in PROBE_ORDER]
    probe_lines = "\n".join(_fmt_probe_line(n, probes[n]) for n in names) or "(no probes recorded)"

    prov = report.get("provenance", {})
    prov_lines = "\n".join(f"- {k}: {v}" for k, v in prov.items()) or "(no provenance recorded)"

    return (
        "# catalyst_probe report\n\n"
        f"- command: `{' '.join(str(a) for a in report.get('command', []))}`\n"
        f"- exit code: {exit_code}\n"
        f"- wall time: {wall_str}\n\n"
        "## aggregate metric (what a normal profiler would show you)\n\n"
        f"- {wall_str} wall-clock / exit {exit_code}\n\n"
        "## the 5 located numbers\n\n"
        f"{probe_lines}\n\n"
        "## provenance\n\n"
        f"{prov_lines}\n"
    )
