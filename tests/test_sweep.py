"""Tests for harness/pilot_sweep.py -- U5 pilot sweep runner.

2 configs x 1 repeat against synthetic_workload.py (io on vs off) -- proves
the sweep mechanics (multi-config x multi-repeat -> aggregate -> compare)
without needing real cloud/DFT. Point --spec at a real job later.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "harness"))
import pilot_sweep  # noqa: E402

WORKLOAD = REPO_ROOT / "harness" / "synthetic_workload.py"

SPEC = {
    "repeats": 1,
    "configs": [
        {"label": "io_off", "argv": [sys.executable, str(WORKLOAD), "--io", "off"]},
        {"label": "io_on", "argv": [sys.executable, str(WORKLOAD), "--io", "on"]},
    ],
}


class TestPilotSweep(unittest.TestCase):
    def test_sweep_compares_two_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = pilot_sweep.run_sweep(SPEC, cwd=tmp)

            sweep = json.loads((out_dir / "sweep.json").read_text())
            self.assertEqual(set(sweep["aggregates"].keys()), {"io_off", "io_on"})
            for label in ("io_off", "io_on"):
                self.assertEqual(sweep["aggregates"][label]["repeats"], 1)
                self.assertIn("wall_s", sweep["aggregates"][label])

            # io bytes: "off" must aggregate lower than "on" -- same contract
            # test_synth.py checks per-run, now checked through the sweep path.
            io_off = sweep["aggregates"]["io_off"]["probes"]["io"]
            io_on = sweep["aggregates"]["io_on"]["probes"]["io"]
            off_mean = io_off["mean"] if "mean" in io_off else 0
            on_mean = io_on["mean"] if "mean" in io_on else 0
            self.assertLess(off_mean, on_mean)

            comparison = (out_dir / "comparison.md").read_text()
            self.assertIn("io_off", comparison)
            self.assertIn("io_on", comparison)


if __name__ == "__main__":
    unittest.main()
