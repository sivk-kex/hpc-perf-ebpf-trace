# catalyst_probe

Wraps an unmodified DFT/AIMD job, attaches 5 independent probes while it
runs, and reports the aggregate metric (wall-clock/exit code) next to what
the probes actually saw -- the point being the aggregate hides what the
probes reveal. stdlib-only, degrades cleanly if perf/bpftrace/root aren't
available.

## Run it

```
python3 catalyst_probe.py run -- <cmd> [args...]
```

Writes `runs/<timestamp>-<pid>/report.json` and `report.md`, exits with
the wrapped command's own exit code.

## The 5 probes (`probes.py`)

| probe | signal | needs |
|---|---|---|
| `energy` | RAPL package+ram joules | perf |
| `mem_bw` | LLC-load-misses rate | perf |
| `imbalance` | busy:idle ratio, straggler thread | /proc only |
| `io` | write() time + bytes written | perf (timing) + /proc (bytes) |
| `comm` | time in sendto/recvfrom | bpftrace (root) or perf fallback |

Any probe that can't get what it needs reports `{"status": "unavailable",
"reason": ...}` instead of raising -- one probe missing never breaks the
report or the other 4.

## Validate without real DFT (`synthetic_workload.py`)

```
python3 catalyst_probe.py run -- python3 synthetic_workload.py --io on --imbalance on
```

Stdlib-only workload that exercises all 5 signals for real (real memory
traffic, real writes+fsync, real thread skew) so the harness is provable
end-to-end on a laptop with no perf/bpftrace/root/DFT installed.

## Compare configs (`pilot_sweep.py`)

```
python3 pilot_sweep.py --spec sweep.json
```

```json
{"repeats": 3, "configs": [
  {"label": "traj_freq_low",  "argv": ["real_aimd_job", "--traj-every", "100"]},
  {"label": "traj_freq_high", "argv": ["real_aimd_job", "--traj-every", "1"]}
]}
```

Runs each config N times, aggregates wall_s + each probe's value (mean/
stdev), writes `runs/sweep-<timestamp>/comparison.md` -- the aggregate
metric side by side with the 5 numbers, per config. Point `argv` at a real
job once on real cloud/DFT infra; proven here against the synthetic
workload.

## Test

```
python3 -m unittest discover -s tests -v
```

29 tests, no perf/bpftrace/root required -- probes that need them just
assert `status == "unavailable"` shape instead of a value.

→ skipped: real cloud/DFT pilot run (U5's actual execution, not the sweep
tooling itself), paper4.tex scaffold. Add when pointed at real infra.
