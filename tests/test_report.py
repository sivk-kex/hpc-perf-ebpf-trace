"""Tests for report.py -- U3 report aggregation (pure functions)."""
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import report  # noqa: E402


META = {
    "command": ["echo", "hi"],
    "pid": 123,
    "pgid": 123,
    "exit_code": 0,
    "wall_s": 0.42,
}

FULL_PROBES = {
    "energy": {"value": 27.5, "unit": "J", "source": "perf_stat", "status": "ok"},
    "mem_bw": {"value": 8400.0, "unit": "count/s", "source": "perf_stat", "status": "ok"},
    "imbalance": {"value": 0.8, "unit": "busy:idle (straggler thread)", "source": "proc", "status": "ok"},
    "io": {"value": 20480000, "unit": "bytes_written", "source": "perf_trace+proc", "status": "ok"},
    "comm": {"value": 0.004, "unit": "s", "source": "bpftrace", "status": "ok"},
}


# ---------------------------------------------------------------------------
# (a) full probe set -> all 5 fields populated with units
# ---------------------------------------------------------------------------

class TestBuildReportFullSet(unittest.TestCase):
    def test_full_probe_set_populates_all_5(self):
        rep = report.build_report(META, FULL_PROBES)
        for name in ("energy", "mem_bw", "imbalance", "io", "comm"):
            self.assertEqual(rep["probes"][name]["status"], "ok")
            self.assertIsNotNone(rep["probes"][name]["unit"])
        # meta passed through untouched
        self.assertEqual(rep["command"], ["echo", "hi"])
        self.assertEqual(rep["exit_code"], 0)
        self.assertEqual(rep["wall_s"], 0.42)


# ---------------------------------------------------------------------------
# (b) one probe unavailable -> report still complete, field marked not dropped
# ---------------------------------------------------------------------------

class TestBuildReportPartialUnavailable(unittest.TestCase):
    def test_one_unavailable_probe_still_complete(self):
        probes = dict(FULL_PROBES)
        probes["comm"] = {
            "value": None, "unit": None, "source": None,
            "status": "unavailable", "reason": "neither bpftrace nor perf available",
        }
        rep = report.build_report(META, probes)
        self.assertEqual(set(rep["probes"]), {"energy", "mem_bw", "imbalance", "io", "comm"})
        self.assertEqual(rep["probes"]["comm"]["status"], "unavailable")
        self.assertIn("reason", rep["probes"]["comm"])
        # the other 4 are untouched
        self.assertEqual(rep["probes"]["energy"]["status"], "ok")


# ---------------------------------------------------------------------------
# (c) provenance block present and complete
# ---------------------------------------------------------------------------

class TestProvenance(unittest.TestCase):
    def test_provenance_block_present_and_complete(self):
        rep = report.build_report(META, FULL_PROBES)
        prov = rep["provenance"]
        for key in ("kernel", "perf_version", "privilege_path", "harness_git_sha", "host"):
            self.assertIn(key, prov)
            self.assertIsNotNone(prov[key])

    def test_provenance_survives_git_and_perf_missing(self):
        with unittest.mock.patch.object(report.subprocess, "run", side_effect=FileNotFoundError):
            rep = report.build_report(META, FULL_PROBES)
        self.assertEqual(rep["provenance"]["perf_version"], "unavailable")
        self.assertEqual(rep["provenance"]["harness_git_sha"], "unknown")
        # kernel/host don't shell out -- still populated
        self.assertTrue(rep["provenance"]["kernel"])
        self.assertTrue(rep["provenance"]["host"])


# ---------------------------------------------------------------------------
# render_markdown: 5 numbers + aggregate visible together, never crashes
# ---------------------------------------------------------------------------

class TestRenderMarkdown(unittest.TestCase):
    def test_renders_5_numbers_and_aggregate_together(self):
        rep = report.build_report(META, FULL_PROBES)
        md = report.render_markdown(rep)
        for name in ("energy", "mem_bw", "imbalance", "io", "comm"):
            self.assertIn(name, md)
        self.assertIn("27.5", md)          # energy value present
        self.assertIn("0.420", md)         # wall time formatted next to the 5 numbers
        self.assertIn("exit", md.lower())  # exit_code is the other half of the aggregate

    def test_missing_value_unit_renders_gracefully(self):
        # (d) a probe with only status+reason (no value/unit keys at all)
        probes = dict(FULL_PROBES)
        probes["energy"] = {"status": "unavailable", "reason": "perf not installed"}
        rep = report.build_report(META, probes)
        md = report.render_markdown(rep)  # must not raise KeyError
        self.assertIn("unavailable", md)
        self.assertIn("perf not installed", md)

    def test_missing_value_unit_despite_ok_status_does_not_crash(self):
        # pathological: status ok but value/unit missing -- still must not crash
        probes = dict(FULL_PROBES)
        probes["mem_bw"] = {"status": "ok", "source": "perf_stat"}
        rep = report.build_report(META, probes)
        md = report.render_markdown(rep)
        self.assertIn("mem_bw", md)


if __name__ == "__main__":
    unittest.main()
