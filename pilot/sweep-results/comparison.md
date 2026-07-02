# catalyst_probe pilot sweep

repeats per config: 10

## aggregate metric (what a normal profiler would show you)

- **disk_io_high**: wall_s mean=40.787s stdev=0.408
- **disk_io_low**: wall_s mean=40.527s stdev=0.154

## the 5 located numbers, per config

### energy
- **disk_io_high**: unavailable
- **disk_io_low**: unavailable

### mem_bw
- **disk_io_high**: unavailable
- **disk_io_low**: unavailable

### comm
- **disk_io_high**: mean=0 stdev=0 s
- **disk_io_low**: mean=0 stdev=0 s

### io
- **disk_io_high**: mean=2.479e+07 stdev=1.431e+06 bytes_written
- **disk_io_low**: mean=2.066e+07 stdev=1.217e+06 bytes_written

### imbalance
- **disk_io_high**: mean=125 stdev=35.59 busy:idle (straggler thread)
- **disk_io_low**: mean=132.3 stdev=7.91 busy:idle (straggler thread)
