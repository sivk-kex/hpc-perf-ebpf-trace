#!/usr/bin/env python3
"""probes.py -- five independent signal probes against a running job's PID.

U2: energy, mem_bw, imbalance, io, comm. Each probe degrades to
{"status": "unavailable", "reason": ...} on ANY failure -- missing binary,
no permission, unsupported event, dead pid. A probe never raises (KTD7);
one probe failing never affects the others.

Lazy mechanism per signal (no custom PMU code, no eBPF scheduler tracing,
no roofline math -- that's out of scope for U2):
  energy     -> perf stat -e power/energy-pkg/,power/energy-ram/ -p PID
  mem_bw     -> perf stat -e LLC-load-misses,LLC-loads -p PID
  imbalance  -> /proc/<pid>/task/*/stat utime+stime, two snapshots
  io         -> perf trace -s (syscall timing) + /proc/<pid>/io (bytes)
  comm       -> bpftrace comm.bt if usable, else perf trace on sendto/recvfrom
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

BPFTRACE_DIR = Path(__file__).resolve().parent / "bpftrace"

# ponytail: fixed ~1s attach window per live probe, not the full job
# duration -- a lazy signal, not a precise energy/time integral. Bump this
# if whole-run totals matter more than a quick sample.
SAMPLE_S = "1"

try:
    HZ = os.sysconf("SC_CLK_TCK")
except (ValueError, AttributeError):
    HZ = 100


def _unavailable(reason):
    return {"value": None, "unit": None, "source": None, "status": "unavailable", "reason": reason}


# ---------------------------------------------------------------------------
# privilege detection -- run ONCE per invocation (R3), each probe consults it
# ---------------------------------------------------------------------------

def detect_privilege():
    """Cheap, one-shot: what's on PATH, what needs root. No probe re-derives this."""
    try:
        paranoid = int(Path("/proc/sys/kernel/perf_event_paranoid").read_text().strip())
    except Exception:
        paranoid = None  # file missing/unreadable -- treat as unknown/restricted

    has_perf = shutil.which("perf") is not None
    has_bpftrace = shutil.which("bpftrace") is not None
    is_root = os.geteuid() == 0

    return {
        "perf_event_paranoid": paranoid,
        "has_perf": has_perf,
        "has_bpftrace": has_bpftrace,
        "is_root": is_root,
        # ponytail: real CAP_BPF check needs /proc/self/status bitmask
        # parsing; root covers the practical case for bpftrace in the wild.
        "can_bpftrace": has_bpftrace and is_root,
        "can_perf": has_perf and (is_root or (paranoid is not None and paranoid <= 1)),
    }


# ---------------------------------------------------------------------------
# perf stat / perf trace subprocess helpers (shared by several probes)
# ---------------------------------------------------------------------------

# ponytail: a probe's perf/bpftrace subprocess isn't in the wrapped job's
# process group, so catalyst_probe.py forwarding SIGINT/SIGTERM to the job
# alone leaves a live probe running out its own timeout unattended -- stash
# the in-flight Popen here so the harness's signal handler can kill it too.
_ACTIVE_PROC = {"proc": None}


def kill_active_probe():
    proc = _ACTIVE_PROC["proc"]
    if proc is not None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _run(cmd, timeout):
    """subprocess.run(capture_output=True, text=True, timeout=...)-alike,
    but keeps the live Popen in _ACTIVE_PROC for kill_active_probe()."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _ACTIVE_PROC["proc"] = proc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        _ACTIVE_PROC["proc"] = None
    return stdout + stderr


def _run_perf_stat(events, pid, timeout=5):
    """`perf stat -e <events> -p <pid> -- sleep 1` -- attach to a live pid for
    a bounded window using `sleep N` as perf's own duration timer. Returns
    combined stdout+stderr text (perf writes its stats table to stderr)."""
    cmd = ["perf", "stat", "-e", ",".join(events), "-p", str(pid), "--", "sleep", SAMPLE_S]
    return _run(cmd, timeout)


def _run_perf_trace(pid, timeout=5):
    cmd = ["perf", "trace", "-s", "-p", str(pid), "--", "sleep", SAMPLE_S]
    return _run(cmd, timeout)


def _perf_stat_probe(pid, priv, events, parse_fn):
    """Shared guard/call/parse/degrade shape behind probe_energy and
    probe_mem_bw -- they differ only in which events they ask perf for and
    which parser reads the result."""
    if not priv["has_perf"]:
        return _unavailable("perf not installed")
    try:
        text = _run_perf_stat(events, pid)
        return parse_fn(text)
    except Exception as e:
        return _unavailable(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 1. energy (KTD1)
# ponytail: perf reads RAPL; sysfs energy_uj fallback only if perf lacks the
# event -- not implemented here, perf-missing already degrades cleanly and a
# second fallback layer is speculative until someone hits that box.
# ---------------------------------------------------------------------------

_JOULES_RE = re.compile(r"^\s*([\d,]+\.?\d*)\s+Joules\s+(\S+)", re.MULTILINE)


def _parse_energy(text):
    found = {}
    for m in _JOULES_RE.finditer(text):
        found[m.group(2)] = float(m.group(1).replace(",", ""))
    if not found:
        return _unavailable("no RAPL energy events in perf stat output (unsupported or no permission)")
    return {"value": sum(found.values()), "unit": "J", "source": "perf_stat", "status": "ok", "detail": found}


def probe_energy(pid, priv=None):
    priv = priv or detect_privilege()
    return _perf_stat_probe(pid, priv, ["power/energy-pkg/", "power/energy-ram/"], _parse_energy)


# ---------------------------------------------------------------------------
# 2. mem_bw (KTD2) -- raw counter value/rate only, no peak/roofline comparison
# ponytail: LLC-load-misses only -- uncore mem-bw PMU names are host/CPU
# specific, add real bandwidth counters if a specific box needs them.
# ---------------------------------------------------------------------------

_COUNT_RE = re.compile(r"^\s*([\d,]+)\s+([A-Za-z0-9_./-]+)", re.MULTILINE)
_ELAPSED_RE = re.compile(r"([\d.]+)\s+seconds time elapsed")


def _parse_elapsed(text):
    m = _ELAPSED_RE.search(text)
    return float(m.group(1)) if m else None


def _parse_mem_bw(text):
    counts = {}
    for m in _COUNT_RE.finditer(text):
        name = m.group(2)
        if name in ("LLC-load-misses", "LLC-loads"):
            counts[name] = int(m.group(1).replace(",", ""))
    if "LLC-load-misses" not in counts:
        return _unavailable("LLC-load-misses not counted (unsupported event or no permission)")
    elapsed = _parse_elapsed(text)
    value, unit = counts["LLC-load-misses"], "count"
    if elapsed:
        value, unit = value / elapsed, "count/s"
    return {"value": value, "unit": unit, "source": "perf_stat", "status": "ok", "detail": counts}


def probe_mem_bw(pid, priv=None):
    priv = priv or detect_privilege()
    return _perf_stat_probe(pid, priv, ["LLC-load-misses", "LLC-loads"], _parse_mem_bw)


# ---------------------------------------------------------------------------
# 3. imbalance (KTD3)
# ponytail: /proc over eBPF here -- the number is the same, the privilege
# cost isn't.
# ---------------------------------------------------------------------------

def _parse_stat_line(text):
    """/proc/<pid>/task/<tid>/stat -- comm field is "(name)" and may itself
    contain spaces/parens, so split after the LAST ')' and index from there.
    utime is field 14, stime field 15 -> rest[11], rest[12]."""
    try:
        rest = text.rsplit(")", 1)[1].split()
        return {"utime": int(rest[11]), "stime": int(rest[12])}
    except (IndexError, ValueError):
        return None


def read_task_stats(pid):
    """{tid: {"utime":.., "stime":..}} for all threads of pid, or None if the
    pid/task dir is gone (exited+reaped, or no permission)."""
    task_dir = Path(f"/proc/{pid}/task")
    try:
        tids = list(task_dir.iterdir())
    except OSError:
        return None
    out = {}
    for tdir in tids:
        try:
            text = (tdir / "stat").read_text()
        except OSError:
            continue  # thread exited between listdir and read -- not fatal
        parsed = _parse_stat_line(text)
        if parsed:
            out[tdir.name] = parsed
    return out or None


def _compute_imbalance(pre, post, elapsed_s):
    elapsed_ticks = max(elapsed_s, 0) * HZ
    if elapsed_ticks < 2:
        # below tick granularity, busy_ticks vs elapsed_ticks is quantization
        # noise, not a real busy:idle signal -- the 1-tick idle floor below
        # would otherwise fabricate a ratio out of that noise.
        return _unavailable(f"elapsed window too short ({elapsed_ticks:.2f} ticks) for tick-granularity imbalance")
    per_thread = {}
    for tid, end in post.items():
        st = pre.get(tid, {"utime": 0, "stime": 0})
        busy = (end["utime"] + end["stime"]) - (st["utime"] + st["stime"])
        # ponytail: floor idle at 1 tick so the ratio stays JSON-safe (no
        # inf) when a thread is ~100% busy.
        idle = max(1.0, elapsed_ticks - busy)
        per_thread[tid] = {"busy_ticks": busy, "idle_ticks": idle, "ratio": busy / idle}
    if not per_thread:
        return _unavailable("no threads observed")
    straggler = min(per_thread, key=lambda t: per_thread[t]["busy_ticks"])
    return {
        "value": per_thread[straggler]["ratio"],
        "unit": "busy:idle (straggler thread)",
        "source": "proc",
        "status": "ok",
        "detail": {"per_thread": per_thread, "straggler_tid": straggler, "clk_tck": HZ},
    }


def probe_imbalance(pid, pre=None, post=None, elapsed_s=None):
    """pre/post: {tid: {"utime":.., "stime":..}} snapshots -- pass real
    read_task_stats() results from the orchestrator, or synthetic fixtures
    in tests. elapsed_s is the wall-clock window the snapshots bracket."""
    try:
        if pre is None:
            pre = read_task_stats(pid)
        if post is None:
            post = read_task_stats(pid)
        if not pre or not post:
            return _unavailable("could not read /proc/<pid>/task (pid gone or no permission)")
        if elapsed_s is None:
            return _unavailable("no elapsed_s given to compute idle time against")
        return _compute_imbalance(pre, post, elapsed_s)
    except Exception as e:
        return _unavailable(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 4. io (KTD4) -- perf trace syscall timing + /proc/<pid>/io byte counts
# ---------------------------------------------------------------------------

_PERF_TRACE_ROW_RE = re.compile(r"^\s*(\w+)\s+(\d+)\s+([\d.]+)\s", re.MULTILINE)


def _parse_perf_trace_summary(text, syscalls):
    """Pull the 'total' (msec) column per syscall name out of `perf trace -s`
    summary output. Syscalls not observed are simply absent, not an error."""
    out = {}
    for m in _PERF_TRACE_ROW_RE.finditer(text):
        name, total_ms = m.group(1), m.group(3)
        if name in syscalls:
            out[name] = float(total_ms) / 1000.0  # msec -> seconds
    return out


def _parse_proc_io(text):
    out = {}
    for line in text.splitlines():
        key, sep, val = line.partition(":")
        if sep and key.strip() in ("read_bytes", "write_bytes"):
            try:
                out[key.strip()] = int(val.strip())
            except ValueError:
                pass
    return out


def read_proc_io(pid):
    try:
        return _parse_proc_io(Path(f"/proc/{pid}/io").read_text())
    except Exception:
        return None


def probe_io(pid, priv=None, pre_io=None, post_io=None):
    priv = priv or detect_privilege()
    reasons = []

    try:
        timing = {}
        if priv["has_perf"]:
            try:
                text = _run_perf_trace(pid)
                timing = _parse_perf_trace_summary(text, {"write", "fsync", "pwrite", "pwrite64"})
            except Exception as e:
                reasons.append(f"perf trace: {type(e).__name__}: {e}")
        else:
            reasons.append("perf not installed")

        if post_io is None:
            post_io = read_proc_io(pid)
        byte_counts = {}
        if post_io is not None:
            base = pre_io or {}
            byte_counts = {k: post_io.get(k, 0) - base.get(k, 0) for k in ("read_bytes", "write_bytes")}
        else:
            reasons.append("/proc/<pid>/io unreadable (pid gone or no permission)")

        if not timing and not byte_counts:
            return _unavailable("; ".join(reasons) or "no io data")

        return {
            "value": byte_counts.get("write_bytes", 0),
            "unit": "bytes_written",
            "source": "perf_trace+proc",
            "status": "ok",
            "detail": {"syscall_time_s": timing, "bytes": byte_counts},
        }
    except Exception as e:
        return _unavailable(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 5. comm (KTD5) -- bpftrace on socket syscalls, else perf trace fallback.
# Single-node MPI is usually shared-mem (vader) so this may legitimately
# read ~0 -- that's a correct result, not a bug.
# ---------------------------------------------------------------------------

_BPFTRACE_COMM_RE = re.compile(
    r"sendto_ns=(\d+)\s+sendto_count=(\d+)\s+recvfrom_ns=(\d+)\s+recvfrom_count=(\d+)"
)


def _parse_bpftrace_comm(text):
    m = _BPFTRACE_COMM_RE.search(text)
    if not m:
        return None
    sendto_ns, sendto_n, recvfrom_ns, recvfrom_n = (int(g) for g in m.groups())
    return {
        "sendto_s": sendto_ns / 1e9,
        "sendto_count": sendto_n,
        "recvfrom_s": recvfrom_ns / 1e9,
        "recvfrom_count": recvfrom_n,
    }


def _probe_comm_bpftrace(pid, timeout=10):
    script = BPFTRACE_DIR / "comm.bt"
    text = _run(["timeout", SAMPLE_S, "bpftrace", str(script), str(pid)], timeout)
    parsed = _parse_bpftrace_comm(text)
    if parsed is None:
        return _unavailable("bpftrace produced no parseable output (script error or unsupported kernel)")
    total_s = parsed["sendto_s"] + parsed["recvfrom_s"]
    return {"value": total_s, "unit": "s", "source": "bpftrace", "status": "ok", "detail": parsed}


def _probe_comm_perf_trace(pid):
    text = _run_perf_trace(pid)
    timing = _parse_perf_trace_summary(text, {"sendto", "recvfrom"})
    # empty timing here means perf ran fine and just saw no socket syscalls
    # (shared-mem single-node MPI) -- a real 0, not a failure.
    return {"value": sum(timing.values()), "unit": "s", "source": "perf_trace", "status": "ok", "detail": timing}


def probe_comm(pid, priv=None):
    priv = priv or detect_privilege()
    try:
        if priv["can_bpftrace"]:
            return _probe_comm_bpftrace(pid)
        if priv["has_perf"]:
            return _probe_comm_perf_trace(pid)
        return _unavailable("neither bpftrace nor perf available")
    except Exception as e:
        return _unavailable(f"{type(e).__name__}: {e}")
