"""Tests for harness/catalyst_probe.py -- CLI skeleton (U1)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "harness" / "catalyst_probe.py"


def run_cli(job_args, cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "run", "--", *job_args],
        cwd=cwd,
    )


class TestCLI(unittest.TestCase):
    def test_wall_time_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                [sys.executable, "-c", "import time; time.sleep(0.3)"], cwd=tmp
            )
            self.assertEqual(proc.returncode, 0)

            run_dirs = list((Path(tmp) / "runs").iterdir())
            self.assertEqual(len(run_dirs), 1)
            report = json.loads((run_dirs[0] / "report.json").read_text())
            self.assertAlmostEqual(report["wall_s"], 0.3, delta=0.25)
            self.assertTrue((run_dirs[0] / "report.md").exists())
            # U2: probes are wired in now -- no longer an empty placeholder.
            # Each of the 5 degrades independently (KTD7), so every entry
            # must at least carry a status; real values depend on whether
            # this box has perf/bpftrace + permission.
            self.assertEqual(set(report["probes"]), {"energy", "mem_bw", "imbalance", "io", "comm"})
            for name, p in report["probes"].items():
                self.assertIn(p["status"], ("ok", "unavailable"), name)

    def test_exit_code_propagated(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli([sys.executable, "-c", "import sys; sys.exit(7)"], cwd=tmp)
            self.assertEqual(proc.returncode, 7)

    def test_runs_dir_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(["echo", "hi"], cwd=tmp)
            runs_dir = Path(tmp) / "runs"
            self.assertTrue(runs_dir.is_dir())
            run_dirs = list(runs_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            report = json.loads((run_dirs[0] / "report.json").read_text())
            self.assertEqual(report["command"], ["echo", "hi"])


if __name__ == "__main__":
    unittest.main()
