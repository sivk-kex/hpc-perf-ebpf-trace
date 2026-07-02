# Pilot findings

## Job
Bulk silicon (2-atom FCC-diamond cell, `celldm(1)=10.20`), Born-Oppenheimer
AIMD (`calculation='md'`, `ion_dynamics='verlet'`), Quantum ESPRESSO 6.7
`pw.x`. `ecutwfc=45 Ry`, 6x6x6 k-mesh, 50 MD steps, `dt=20` a.u.,
`conv_thr=1e-8`. Pseudopotential: `Si.pbe-rrkj.UPF` (bundled with the
Debian/Ubuntu `quantum-espresso` package). Single MPI rank (serial), no
parallelism — a real, but minimal, limitation of this pilot (see below).

## Infra
Azure for Students VM, `Standard_D2s_v3` (2 vCPU, non-burstable; 8GB RAM),
Ubuntu 22.04.5 LTS, kernel `6.8.0-1059-azure`. `perf_event_paranoid` was `4`
by default on this image (locked down harder than stock) and lowered to
`-1`. As expected, RAPL energy and PMU (`mem_bw`) events are blocked by the
hypervisor regardless of `perf_event_paranoid` — both probes report
`unavailable`, consistent with the harness's degrade-on-fail contract, not
a bug.

## Config axis
QE's `disk_io` control parameter: `high` (dumps wavefunctions to disk every
MD step) vs. `low` (skips per-step wavefunction dumps). This is the
trajectory/checkpoint-write-frequency axis Section~2's near-absent I/O
hypothesis was about. 2 configs x 10 repeats, via `pilot_sweep.py` (bumped
from an initial n=3 pilot once the VM was confirmed reusable — same configs,
tighter stats, no change in scope or claims).

## 1. Measured bottleneck, absolute units
`io` probe (`perf trace` + `/proc/<pid>/io`), bytes written:
- `disk_io_high`: mean 2.479e7 B, stdev 1.431e6 B (n=10)
- `disk_io_low`: mean 2.066e7 B, stdev 1.217e6 B (n=10)

~20% more bytes written under `disk_io=high`. The gap (4.13e6 B) is ~3x
either config's own stdev — a clean separation, not a borderline one.

## 2. Aggregate metric, shown blind to it
Wall-clock:
- `disk_io_high`: mean 40.787s, stdev 0.408s
- `disk_io_low`: mean 40.527s, stdev 0.154s

~0.6% difference — statistically indistinguishable given the stdevs. A
profiler reporting wall-clock alone would show these two configs as the
same run.

## 3. Config-delta comparison
The two configs differ *only* in `disk_io`. Wall-clock: flat (~0.6%, within
noise). `io` bytes written: ~20% apart, well outside noise (gap ~3x either
stdev). The delta is visible in the kernel-level probe and invisible in the
aggregate metric — this is the paper's claim, demonstrated with n=10.

Other probes this run: `comm` = 0s both configs (single rank, no MPI
traffic — expected, not a finding). `imbalance` (busy:idle) 125 vs 132.3,
stdev 35.6 vs 7.91 — got noisier at n=10, confirms it's not a clean
secondary signal, not claimed as a finding. `energy`/`mem_bw`: unavailable
both configs (hypervisor-blocked, expected).

## 4. Repro
```
git clone https://github.com/sivk-kex/hpc-perf-ebpf-trace
cd hpc-perf-ebpf-trace
sudo apt-get install -y linux-tools-common linux-tools-generic \
  linux-tools-$(uname -r) bpftrace quantum-espresso
sudo sysctl -w kernel.perf_event_paranoid=-1
python3 pilot_sweep.py --spec pilot/sweep.json
```
Raw per-repeat reports and the aggregated comparison:
`pilot/sweep-results/sweep.json`, `pilot/sweep-results/comparison.md`.

## Honest caveats (do not overstate in the paper)
- n=10 repeats per config; io-bytes CV is ~5.8% (high) / ~5.9% (low) — the
  ~20% delta is a clean separation now, but still one job, one axis, one
  VM — say "measurable and consistent", not "generalizable".
- Single MPI rank, single node: this pilot does not exercise the `comm`
  probe or multi-rank imbalance at all — it demonstrates the *method*, not
  a claim about where time goes in production-scale catalysis runs.
- Absolute I/O volume here (tens of MB) is small; the finding is that the
  method surfaces a hidden, config-driven delta, not that I/O dominates
  this particular job's wall-clock (it doesn't — wall-clock is flat).
