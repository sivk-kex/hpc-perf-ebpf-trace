## catalyst_probe

A stdlib-only wrapper that launches a command, times its wall-clock duration, and writes a report -- the harness frame that later units (probes, report schema) hang off of. Run it as `python harness/catalyst_probe.py run -- <cmd> [args...]`, e.g. `python harness/catalyst_probe.py run -- echo hi`; it writes `runs/<timestamp>/report.json` and `runs/<timestamp>/report.md` and exits with the wrapped command's own exit code.
