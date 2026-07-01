#!/usr/bin/env python3
"""pilot_sweep.py -- U5: run catalyst_probe across >=2 configs x N repeats,
aggregate wall_s + each probe's value per config, and render them side by
side -- the roadmap's "capture the 5-number report + the matched wall-clock
that hides the finding" step. Point --spec at real AIMD configs (e.g.
trajectory-output freq high/low) once on real infra; proven here against
synthetic_workload.py so the sweep mechanics don't need cloud/DFT to test.

--spec is a JSON file: {"repeats": N, "configs": [{"label":.., "argv":[..]}]}
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "harness" / "catalyst_probe.py"


def _latest_report(cwd):
    run_dirs = sorted((Path(cwd) / "runs").iterdir(), key=lambda p: p.stat().st_mtime)
    return json.loads((run_dirs[-1] / "report.json").read_text())


def run_once(argv, cwd):
    cp = subprocess.run(
        [sys.executable, str(CLI), "run", "--", *argv],
        cwd=cwd, capture_output=True, text=True, timeout=120,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"probe run failed ({cp.returncode}): {cp.stderr}")
    return _latest_report(cwd)


def _numeric_values(reports, probe_name):
    return [
        r["probes"][probe_name]["value"] for r in reports
        if r["probes"].get(probe_name, {}).get("status") == "ok"
        and isinstance(r["probes"][probe_name].get("value"), (int, float))
    ]


def aggregate(reports):
    """mean/stdev of wall_s and each probe's numeric value across repeats. A
    probe that's unavailable in every repeat stays unavailable here too --
    same "never hide a gap" contract the probes themselves follow."""
    wall = [r["wall_s"] for r in reports]
    agg = {
        "repeats": len(reports),
        "wall_s": {"mean": statistics.mean(wall), "stdev": statistics.stdev(wall) if len(wall) > 1 else 0.0},
        "probes": {},
    }
    for name in reports[0]["probes"]:
        vals = _numeric_values(reports, name)
        if vals:
            unit = next(r["probes"][name]["unit"] for r in reports if r["probes"][name].get("status") == "ok")
            agg["probes"][name] = {
                "mean": statistics.mean(vals), "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0, "unit": unit,
            }
        else:
            agg["probes"][name] = {"unavailable": True}
    return agg


def render_comparison(repeats, configs_agg):
    lines = ["# catalyst_probe pilot sweep", "", f"repeats per config: {repeats}", "",
             "## aggregate metric (what a normal profiler would show you)", ""]
    for label, agg in configs_agg:
        lines.append(f"- **{label}**: wall_s mean={agg['wall_s']['mean']:.3f}s stdev={agg['wall_s']['stdev']:.3f}")
    lines += ["", "## the 5 located numbers, per config", ""]
    for name in configs_agg[0][1]["probes"]:
        lines.append(f"### {name}")
        for label, agg in configs_agg:
            p = agg["probes"][name]
            line = f"- **{label}**: unavailable" if p.get("unavailable") else \
                f"- **{label}**: mean={p['mean']:.4g} stdev={p['stdev']:.4g} {p['unit']}"
            lines.append(line)
        lines.append("")
    return "\n".join(lines)


def run_sweep(spec, cwd):
    """cwd: where catalyst_probe runs write runs/<ts>/ and where this sweep's
    own runs/sweep-<ts>/ output lands -- same relative-to-cwd convention
    catalyst_probe.py itself uses."""
    configs_agg, raw = [], {}
    for cfg in spec["configs"]:
        reports = [run_once(cfg["argv"], cwd) for _ in range(spec["repeats"])]
        configs_agg.append((cfg["label"], aggregate(reports)))
        raw[cfg["label"]] = reports

    out_dir = Path(cwd) / "runs" / f"sweep-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sweep.json").write_text(json.dumps(
        {"repeats": spec["repeats"], "aggregates": dict(configs_agg), "raw": raw}, indent=2))
    (out_dir / "comparison.md").write_text(render_comparison(spec["repeats"], configs_agg))
    return out_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, help='JSON: {"repeats":N,"configs":[{"label":..,"argv":[..]}]}')
    args = parser.parse_args()
    spec = json.loads(Path(args.spec).read_text())
    out_dir = run_sweep(spec, cwd=Path.cwd())
    print(f"wrote {out_dir}/comparison.md")


if __name__ == "__main__":
    main()
