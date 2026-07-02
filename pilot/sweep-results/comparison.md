# catalyst_probe pilot sweep

repeats per config: 3

## aggregate metric (what a normal profiler would show you)

- **disk_io_high**: wall_s mean=40.896s stdev=0.353
- **disk_io_low**: wall_s mean=40.493s stdev=0.195

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
- **disk_io_high**: mean=2.46e+07 stdev=2.085e+06 bytes_written
- **disk_io_low**: mean=2.141e+07 stdev=3.394e+05 bytes_written

### imbalance
- **disk_io_high**: mean=129 stdev=9.057 busy:idle (straggler thread)
- **disk_io_low**: mean=135.6 stdev=1.971 busy:idle (straggler thread)
