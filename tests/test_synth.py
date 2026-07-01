"""Tests for harness/synthetic_workload.py -- synthetic validation workload (U4).

Invoked through catalyst_probe.py's own CLI, same pattern as test_cli.py, so
this exercises the whole harness end-to-end, not just the workload script.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "harness" / "catalyst_probe.py"
WORKLOAD = REPO_ROOT / "harness" / "synthetic_workload.py"


def run_synth(flag_args, cwd):
    return subprocess.run(
        [sys.executable, str(CLI), "run", "--", sys.executable, str(WORKLOAD), *flag_args],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )


def latest_report(cwd):
    run_dirs = list((Path(cwd) / "runs").iterdir())
    assert len(run_dirs) == 1
    return json.loads((run_dirs[0] / "report.json").read_text())


class TestSynth(unittest.TestCase):
    def test_io_on_measurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_synth(["--io", "on"], cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = latest_report(tmp)

            # io byte count + wall-clock are /proc-based, not perf-based --
            # measurable on any box regardless of perf/bpftrace install.
            io = report["probes"]["io"]
            self.assertEqual(io["status"], "ok", io)
            self.assertGreater(io["detail"]["bytes"]["write_bytes"], 0)
            self.assertGreater(report["wall_s"], 0)

            # perf-backed probes are allowed to read "unavailable" on a box
            # without perf/bpftrace -- only check shape when actually "ok".
            energy = report["probes"]["energy"]
            if energy["status"] == "ok":
                self.assertIsInstance(energy["value"], (int, float))

    def test_io_off_drops_bytes(self):
        with tempfile.TemporaryDirectory() as tmp_on, tempfile.TemporaryDirectory() as tmp_off:
            run_synth(["--io", "on"], cwd=tmp_on)
            run_synth(["--io", "off"], cwd=tmp_off)

            on_report = latest_report(tmp_on)["probes"]["io"]
            on_bytes = on_report["detail"]["bytes"].get("write_bytes", 0) if on_report["status"] == "ok" else 0
            off_report = latest_report(tmp_off)["probes"]["io"]
            off_bytes = off_report["detail"]["bytes"].get("write_bytes", 0) if off_report["status"] == "ok" else 0

            self.assertLess(off_bytes, on_bytes)


if __name__ == "__main__":
    unittest.main()
