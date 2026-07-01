#!/usr/bin/env python3
"""synthetic_workload.py -- U4: stdlib-only workload exercising all 5
catalyst_probe signals (energy, mem_bw, imbalance, io, comm) without any
real DFT/AIMD code, so the harness is provable end-to-end on any Linux box.
Point `catalyst_probe.py run --` at this.

energy + mem-bw : one thread churns a big double array (real memory
                  traffic + CPU, not a no-op spin)
imbalance       : --imbalance on/off -- one busy thread + idle stragglers
                  vs. evenly-loaded threads; read via /proc/<pid>/task/*/stat
io              : --io on/off -- periodic write()+fsync() to a temp file,
                  mimicking AIMD trajectory dumps
comm            : MPI allreduce IF mpi4py is importable, else a legit no-op
                  (no mpi4py, or single rank, reads ~0 -- same as U2's comm
                  probe on shared-mem single-node MPI, not a bug)
"""
import argparse
import os
import tempfile
import threading
import time
from array import array

# ponytail: must outlast catalyst_probe's live-probe attach windows (energy,
# mem_bw, comm each hold the job for perf/bpftrace's SAMPLE_S=1s, run
# sequentially -> ~3s worst case per probes.py) so those probes have
# a running job left to sample, not just "a couple of seconds" for its own sake.
DURATION_S = 4.0
ARRAY_LEN = 500_000        # 4MB of doubles per thread -- real traffic, not a laptop-killer
IO_CHUNK_BYTES = 1_000_000
IO_INTERVAL_S = 0.8

# ponytail: mkstemp() with no dir= resolves to tempfile.gettempdir(), which on
# most distros is tmpfs (/tmp) -- writes there never reach a block device, so
# /proc/<pid>/io's write_bytes (what probe_io reads) stays 0 forever no
# matter what --io says. Writing next to this script instead keeps it on
# whatever real filesystem the repo is checked out on, so the io probe
# actually sees bytes regardless of the job's cwd.
_IO_DIR = os.path.dirname(os.path.abspath(__file__))


def mem_bw_churn(end_time):
    """Elementwise read-modify-write over a big double array until the
    monotonic deadline -- real memory bandwidth, feeds energy + mem_bw."""
    buf = array("d", [0.0]) * ARRAY_LEN
    while time.monotonic() < end_time:
        for i in range(len(buf)):
            buf[i] = buf[i] * 1.0000001 + 1.0


def _sleepy(end_time):
    while time.monotonic() < end_time:
        time.sleep(0.05)


def run_compute(end_time, imbalance):
    """One always-busy thread (mem-bw/energy signal) plus three more that
    either sleep (--imbalance on: real busy:idle skew, the straggler
    pattern probe_imbalance looks for) or also churn (--imbalance off:
    evenly loaded, nothing to single out)."""
    others = [_sleepy] * 3 if imbalance else [mem_bw_churn] * 3
    threads = [threading.Thread(target=fn, args=(end_time,)) for fn in [mem_bw_churn] + others]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def run_io(end_time):
    """Periodic big write()+fsync() -- mimics AIMD trajectory dumps."""
    chunk = os.urandom(IO_CHUNK_BYTES)
    fd, path = tempfile.mkstemp(prefix="synth_traj_", dir=_IO_DIR)
    try:
        while time.monotonic() < end_time:
            os.write(fd, chunk)
            os.fsync(fd)
            time.sleep(IO_INTERVAL_S)
    finally:
        os.close(fd)
        os.remove(path)


def run_comm():
    """comm signal: only fires if mpi4py is importable -- optional, never a
    hard dependency. No mpi4py -> nothing to reduce with, legit no-op."""
    try:
        from mpi4py import MPI
    except ImportError:
        return
    comm = MPI.COMM_WORLD
    comm.allreduce(comm.Get_rank(), op=MPI.SUM)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--io", choices=("on", "off"), default="on")
    parser.add_argument("--imbalance", choices=("on", "off"), default="on")
    args = parser.parse_args()

    end_time = time.monotonic() + DURATION_S

    io_thread = None
    if args.io == "on":
        io_thread = threading.Thread(target=run_io, args=(end_time,))
        io_thread.start()

    run_comm()
    run_compute(end_time, args.imbalance == "on")

    if io_thread:
        io_thread.join()


if __name__ == "__main__":
    main()
