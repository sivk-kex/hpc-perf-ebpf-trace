"""Tests for probes.py -- the 5 signal probes (U2).

Parser/logic tests only: fixture text in, asserted values out. No root, no
real perf/bpftrace needed to run this suite (test-first per the build plan --
these were written against the fixtures before the parser bodies).
"""
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import probes  # noqa: E402


# ---------------------------------------------------------------------------
# (a) energy parser: sample `perf stat` RAPL block -> joules
# ---------------------------------------------------------------------------

PERF_STAT_ENERGY = """
 Performance counter stats for process id '12345':

             23.42 Joules power/energy-pkg/
              4.11 Joules power/energy-ram/

       2.001349938 seconds time elapsed

"""

PERF_STAT_ENERGY_UNSUPPORTED = """
 Performance counter stats for process id '12345':

     <not supported> power/energy-pkg/
     <not supported> power/energy-ram/

       1.000123456 seconds time elapsed

"""


class TestEnergyParser(unittest.TestCase):
    def test_extracts_joules(self):
        result = probes._parse_energy(PERF_STAT_ENERGY)
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["value"], 23.42 + 4.11, places=3)
        self.assertEqual(result["unit"], "J")
        self.assertEqual(result["source"], "perf_stat")
        self.assertAlmostEqual(result["detail"]["power/energy-pkg/"], 23.42, places=3)

    def test_unsupported_event_is_unavailable(self):
        result = probes._parse_energy(PERF_STAT_ENERGY_UNSUPPORTED)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("reason", result)


# ---------------------------------------------------------------------------
# bonus: mem_bw parser (same shape of risk as energy -- a regex over perf text)
# ---------------------------------------------------------------------------

PERF_STAT_MEM_BW = """
 Performance counter stats for process id '12345':

         8,431,221      LLC-load-misses
        51,204,933      LLC-loads

       1.001042317 seconds time elapsed

"""


PERF_STAT_MEM_BW_UNSUPPORTED = """
 Performance counter stats for process id '12345':

     <not supported> LLC-load-misses
     <not supported> LLC-loads

       1.000123456 seconds time elapsed

"""


class TestMemBwParser(unittest.TestCase):
    def test_extracts_rate(self):
        result = probes._parse_mem_bw(PERF_STAT_MEM_BW)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["unit"], "count/s")
        self.assertAlmostEqual(result["value"], 8431221 / 1.001042317, delta=10)
        self.assertEqual(result["detail"]["LLC-loads"], 51204933)

    def test_unsupported_event_is_unavailable(self):
        # "<not supported>" isn't a count line -- LLC-load-misses just never
        # shows up in counts, same shape of gap as energy's unsupported case.
        result = probes._parse_mem_bw(PERF_STAT_MEM_BW_UNSUPPORTED)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("reason", result)


# ---------------------------------------------------------------------------
# (b) imbalance: two synthetic /proc/.../stat-shaped snapshots -> busy:idle
# ---------------------------------------------------------------------------

class TestImbalance(unittest.TestCase):
    def test_busy_idle_and_straggler(self):
        # HZ ticks/sec; over a 10s window thread "1" did 8s of work
        # (mostly busy), thread "2" did only 1s (the straggler).
        hz = probes.HZ
        pre = {"1": {"utime": 0, "stime": 0}, "2": {"utime": 0, "stime": 0}}
        post = {
            "1": {"utime": 8 * hz, "stime": 0},
            "2": {"utime": 1 * hz, "stime": 0},
        }
        result = probes.probe_imbalance(pid=0, pre=pre, post=post, elapsed_s=10)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["detail"]["straggler_tid"], "2")
        per_thread = result["detail"]["per_thread"]
        self.assertEqual(per_thread["1"]["busy_ticks"], 8 * hz)
        self.assertEqual(per_thread["2"]["busy_ticks"], 1 * hz)
        # straggler's own busy:idle ratio is the reported top-level value
        self.assertAlmostEqual(result["value"], per_thread["2"]["ratio"])
        self.assertLess(per_thread["2"]["ratio"], per_thread["1"]["ratio"])

    def test_parse_stat_line_handles_parens_in_comm(self):
        # comm field can contain spaces/parens, e.g. "(my (weird) proc)"
        line = "100 (my (weird) proc) S " + " ".join(["1"] * 10) + " 300 50 0 0 20 0 1 0"
        parsed = probes._parse_stat_line(line)
        self.assertEqual(parsed, {"utime": 300, "stime": 50})


# ---------------------------------------------------------------------------
# (c) io parser: sample `perf trace -s` block + sample /proc/.../io block
# ---------------------------------------------------------------------------

PERF_TRACE_IO = """
 Summary of events:

 sleep (12345), 6 events, 100.0%

   syscall            calls  total       min       avg       max       stddev
                               (msec)    (msec)    (msec)    (msec)      (%)
   --------------- -------- --------- --------- --------- ---------  ------
   write                   3     2.145     0.201     0.715     1.300    18.20%
   fsync                   2     6.732     3.001     3.366     3.731     5.44%
   read                    1     0.012     0.012     0.012     0.012     0.00%

"""

PROC_IO = """rchar: 104857600
wchar: 52428800
syscr: 245
syscw: 128
read_bytes: 40960000
write_bytes: 20480000
cancelled_write_bytes: 0
"""


class TestIoParser(unittest.TestCase):
    def test_perf_trace_summary_extracts_write_and_fsync(self):
        timing = probes._parse_perf_trace_summary(PERF_TRACE_IO, {"write", "fsync", "pwrite"})
        self.assertAlmostEqual(timing["write"], 2.145 / 1000.0, places=6)
        self.assertAlmostEqual(timing["fsync"], 6.732 / 1000.0, places=6)
        self.assertNotIn("pwrite", timing)  # not observed -- absent, not an error
        self.assertNotIn("read", timing)  # not requested -- must not leak in

    def test_proc_io_extracts_byte_counts(self):
        counts = probes._parse_proc_io(PROC_IO)
        self.assertEqual(counts["read_bytes"], 40960000)
        self.assertEqual(counts["write_bytes"], 20480000)

    def test_measured_zero_bytes_is_ok_not_unavailable(self):
        # a job that genuinely wrote 0 bytes (pre == post) is a real
        # measurement, not a failure -- must not collapse into the same
        # "unavailable" shape as pid-gone/no-permission (test_io_missing_
        # perf_and_dead_pid below covers that case).
        zero_io = {"read_bytes": 0, "write_bytes": 0}
        result = probes.probe_io(pid=1, priv=NO_TOOLS_PRIV, pre_io=zero_io, post_io=zero_io)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["value"], 0)


# ---------------------------------------------------------------------------
# bonus: comm bpftrace summary line parser
# ---------------------------------------------------------------------------

BPFTRACE_COMM_OUT = (
    "comm.bt: tracing sendto/recvfrom for pid 12345\n"
    "sendto_ns=1500000 sendto_count=3 recvfrom_ns=2500000 recvfrom_count=5\n"
)


class TestCommParser(unittest.TestCase):
    def test_parses_bpftrace_summary_line(self):
        parsed = probes._parse_bpftrace_comm(BPFTRACE_COMM_OUT)
        self.assertEqual(parsed["sendto_count"], 3)
        self.assertAlmostEqual(parsed["sendto_s"], 0.0015)
        self.assertEqual(parsed["recvfrom_count"], 5)

    def test_zero_comm_is_ok_not_unavailable(self):
        # shared-mem single-node MPI (vader) legitimately shows ~0 -- must
        # not be reported as a probe failure.
        with mock.patch.object(probes, "_run_perf_trace", return_value=PERF_TRACE_IO):
            result = probes._probe_comm_perf_trace(pid=1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["value"], 0.0)


# ---------------------------------------------------------------------------
# (d) missing tool -> unavailable, never raises
# ---------------------------------------------------------------------------

NO_TOOLS_PRIV = {
    "perf_event_paranoid": 3,
    "has_perf": False,
    "has_bpftrace": False,
    "is_root": False,
    "can_bpftrace": False,
    "can_perf": False,
}


class TestDegradeOnMissingTool(unittest.TestCase):
    def test_energy_missing_perf(self):
        result = probes.probe_energy(pid=1, priv=NO_TOOLS_PRIV)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("reason", result)

    def test_mem_bw_missing_perf(self):
        result = probes.probe_mem_bw(pid=1, priv=NO_TOOLS_PRIV)
        self.assertEqual(result["status"], "unavailable")

    def test_comm_missing_everything(self):
        result = probes.probe_comm(pid=1, priv=NO_TOOLS_PRIV)
        self.assertEqual(result["status"], "unavailable")

    def test_io_missing_perf_and_dead_pid(self):
        result = probes.probe_io(pid=999999999, priv=NO_TOOLS_PRIV)
        self.assertEqual(result["status"], "unavailable")

    def test_imbalance_dead_pid(self):
        result = probes.probe_imbalance(pid=999999999, elapsed_s=1.0)
        self.assertEqual(result["status"], "unavailable")

    def test_subprocess_raising_does_not_propagate(self):
        # perf "available" per privilege check, but actually invoking it
        # blows up (binary vanished, sandboxed out, whatever) -- must still
        # degrade cleanly rather than raise out of the probe.
        priv = dict(NO_TOOLS_PRIV, has_perf=True)
        with mock.patch.object(probes.subprocess, "Popen", side_effect=FileNotFoundError("no perf")):
            result = probes.probe_energy(pid=1, priv=priv)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("no perf", result["reason"])

    def test_which_missing_drives_privilege_detection(self):
        with mock.patch.object(probes.shutil, "which", return_value=None):
            priv = probes.detect_privilege()
        self.assertFalse(priv["has_perf"])
        self.assertFalse(priv["has_bpftrace"])


# ---------------------------------------------------------------------------
# (e) signal-forwarding gap: a probe's subprocess isn't in the job's process
# group, so catalyst_probe.py's signal handler needs a direct way to kill it.
# ---------------------------------------------------------------------------

class TestActiveProcKill(unittest.TestCase):
    def test_kill_active_probe_terminates_in_flight_subprocess(self):
        result = {}

        def worker():
            try:
                probes._run(["sleep", "5"], timeout=10)
            except Exception as e:  # noqa: BLE001
                result["exc"] = e

        t = threading.Thread(target=worker)
        t.start()
        for _ in range(50):  # wait for _run to register the Popen
            if probes._ACTIVE_PROC["proc"] is not None:
                break
            time.sleep(0.02)
        self.assertIsNotNone(probes._ACTIVE_PROC["proc"], "worker never registered its subprocess")

        start = time.monotonic()
        probes.kill_active_probe()
        t.join(timeout=3)
        self.assertFalse(t.is_alive(), "sleep 5 outlived kill_active_probe()")
        self.assertLess(time.monotonic() - start, 3)
        self.assertIsNone(probes._ACTIVE_PROC["proc"])

    def test_kill_active_probe_is_noop_when_nothing_running(self):
        probes.kill_active_probe()  # must not raise


if __name__ == "__main__":
    unittest.main()
