#!/usr/bin/env python3
"""catalyst_probe.py -- launch a command, record wall-clock, write a report.

U1: harness skeleton. U2: the 5 probes (probes.py) are wired into
`run` below and populate report["probes"]. U3: report shaping/rendering
moved out to report.py -- this file just gathers meta/probes and
writes the files.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import probes
import report


def cmd_run(job_argv):
    if not job_argv:
        print("catalyst_probe: no command given after --", file=sys.stderr)
        return 2

    # ponytail: stdlib subprocess, not a plugin system.
    # New session -> new process group, so a SIGINT/SIGTERM to us can be
    # forwarded to the whole job tree (job may fork children of its own).
    proc = subprocess.Popen(job_argv, start_new_session=True)
    pgid = os.getpgid(proc.pid)

    def forward(signum, _frame):
        try:
            os.killpg(pgid, signum)
        except ProcessLookupError:
            pass  # job already reaped (race between reap and a second signal)
        # a probe's perf/bpftrace subprocess isn't in the job's process group
        # -- without this it'd sit out its own timeout after the job's gone.
        probes.kill_active_probe()

    old_handlers = {
        sig: signal.signal(sig, forward) for sig in (signal.SIGINT, signal.SIGTERM)
    }

    # U2: energy/mem_bw/comm need to attach to the job WHILE it runs, so they
    # run here -- before our own proc.wait() -- as separate perf/bpftrace
    # subprocesses; the job keeps running in the background meanwhile (no
    # Python threading needed for those, that's just normal OS process
    # concurrency). imbalance/io bracket the run with /proc snapshots
    # instead -- see the two comments below for why each needs a different
    # trick to avoid racing proc.wait()'s reap.
    priv = probes.detect_privilege()
    pre_task = probes.read_task_stats(proc.pid)
    pre_io = probes.read_proc_io(proc.pid)

    # /proc/<pid>/io needs a live mm to satisfy its ptrace check -- unlike
    # /proc/<pid>/task/*/stat it goes permission-denied the instant the job
    # becomes a zombie, so "read it once right before reaping" (which works
    # fine for task/stat below) silently returns nothing here. A cheap
    # background sampler keeps the last good reading instead.
    io_latest = {"snap": pre_io}
    stop_sampler = threading.Event()

    def _sample_io():
        while not stop_sampler.is_set():
            snap = probes.read_proc_io(proc.pid)
            if snap is not None:
                io_latest["snap"] = snap
            stop_sampler.wait(0.1)

    sampler = threading.Thread(target=_sample_io, daemon=True)
    sampler.start()

    # ponytail: a watcher thread captures the job's real exit instant via
    # WNOWAIT concurrently with the probes below. Running this on the main
    # thread AFTER the probes (as before) made wall_s include the probes'
    # own ~1s-per-probe attach time on top of the job's actual runtime --
    # invisible on a box with no perf/bpftrace (probes return instantly) but
    # a real ~3s inflation on any box where they're installed.
    job_end_box = {}

    def _wait_for_exit():
        try:
            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
        except (AttributeError, ChildProcessError, OSError):
            pass  # no waitid (non-Linux) -- proc.wait() below still gets exit_code
        job_end_box["t"] = time.monotonic()

    waiter = threading.Thread(target=_wait_for_exit, daemon=True)

    start = time.monotonic()
    waiter.start()

    probe_results = {}
    probe_results["energy"] = probes.probe_energy(proc.pid, priv)
    probe_results["mem_bw"] = probes.probe_mem_bw(proc.pid, priv)
    probe_results["comm"] = probes.probe_comm(proc.pid, priv)

    # Whichever finishes last -- the probes above or the job itself -- this
    # blocks until the watcher thread has the job's true exit timestamp. The
    # zombie still exposes final utime/stime (task/stat below) until the
    # real proc.wait() reaps it further down.
    waiter.join()
    job_end = job_end_box["t"]
    stop_sampler.set()
    sampler.join(timeout=1)
    if sampler.is_alive():
        # ponytail: don't trust a snapshot the sampler might still be mid-write
        # on -- flag it instead of silently reporting stale io bytes as fact.
        print("catalyst_probe: io sampler did not stop in time, post_io snapshot may be stale", file=sys.stderr)

    post_task = probes.read_task_stats(proc.pid)
    post_io = io_latest["snap"]

    probe_results["io"] = probes.probe_io(proc.pid, priv, pre_io, post_io)

    exit_code = proc.wait()  # job already exited above -- this just reaps it
    wall_s = job_end - start

    probe_results["imbalance"] = probes.probe_imbalance(proc.pid, pre_task, post_task, wall_s)

    for sig, handler in old_handlers.items():
        signal.signal(sig, handler)

    # pid suffix: two invocations launched in the same second would otherwise
    # resolve to the same runs/<ts>/ dir and silently clobber each other.
    ts = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    run_dir = Path("runs") / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "command": job_argv,
        "pid": proc.pid,
        "pgid": pgid,
        "exit_code": exit_code,
        "wall_s": wall_s,
    }
    # aggregation/shaping lives in report.py (U3); this stays orchestration:
    # gather meta/probes, call out, write files.
    full_report = report.build_report(meta, probe_results)
    (run_dir / "report.json").write_text(json.dumps(full_report, indent=2))
    (run_dir / "report.md").write_text(report.render_markdown(full_report))

    return exit_code


def main():
    parser = argparse.ArgumentParser(prog="catalyst_probe")
    sub = parser.add_subparsers(dest="subcommand", required=True)
    run_p = sub.add_parser("run", help="run a command and record wall-clock + report")
    # REMAINDER slurps everything after "run", including the literal "--",
    # so the wrapped command's own flags never get parsed as ours.
    run_p.add_argument("job", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    job_argv = args.job
    if job_argv and job_argv[0] == "--":
        job_argv = job_argv[1:]

    return cmd_run(job_argv)


if __name__ == "__main__":
    sys.exit(main())
