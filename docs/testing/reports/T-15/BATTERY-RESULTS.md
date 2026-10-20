# Battery rundown: results and handoff for the paper

Written 2026-10-20 for the paper's author, to cite from. Source: measurement run 3 of T-15
([measurement-3.md](measurement-3.md) is the engineering report; this is the citable version). Evidence files are in
`evidence/` and named at the end.

## 1. Summary (paste-ready)

With detection and depth estimation both running at full load (about 30 frames per second at 640 x 480 on a Raspberry
Pi 5 with a Hailo-8 accelerator, a USB camera, and speech through earphones), the system ran for **at least 3.64 hours
on its battery**, from a cell voltage of 4.05 V down to 3.50 V. Over that time the frame rate (median 30.0 FPS), the
camera-to-callback delay (median 104 ms) and the SoC temperature (at most 67.8 C, no throttling and no under-voltage
flag) did not change. The measurement was stopped at 3.50 V on purpose, on the safe side of the UPS hat's cut-off (about
3.2 V), so **3.64 hours is a lower bound on the endurance, not the time to depletion.** The Pi stayed up for at least
another hour at idle (3.43 V at 01:43), and the remaining time under load was not measured.

## 2. What was measured, and how

| | |
|---|---|
| Platform | Raspberry Pi 5 (4 cores, 8 GB), Hailo-8 (26 TOPS), Arducam IMX291 USB camera at 640 x 480, X1202 UPS hat |
| Software | The project's pipeline in `mode both` (YOLOv8 detection and SC-DepthV3 depth, both on the Hailo), started the way the boot service starts it, **headless** (no preview window); speech through USB earphones |
| What was a stand-in | The glasses board: a program on a pseudo-terminal that acknowledged every packet, so **no motors ran**. No control panel (so no panel load, and mode "both") |
| Scene | An ordinary lit room with a table in view; a person was at the Pi at 187 minutes (see section 5) |
| Power | Battery only, **confirmed in all 1,307 samples** by the hat's own power-loss line (GPIO 6, read as an input, as the vendor's script does) |
| Start | 21:09:04 on 2026-10-19, on the charger's unplug. Cell 4.086 V unloaded; 4.049 V at the first loaded sample |
| Sampling | Every 10 s: cell voltage and the gauge's percentage (I2C address 0x36, the vendor's own formulas), the power-source line, SoC temperature, CPU clock and load, memory, the program's CPU and memory, the throttle flags (`vcgencmd get_throttled`); the program's own console log for frame rate and delay |
| Stop rule | Two voltage readings in a row at 3.5 V or under, so one noisy reading cannot end the run. It ended at 00:47:29 |
| Tools | `docs/testing/tools/perf_run.py` with `perf_sample.py`. Their own tests: 12, and a mutation check in which 10 of 10 deliberate breakages were caught. **An independent Tester pass of these tools was started on 2026-10-20 and had not finished at the time of writing.** |

## 3. Results

| Measure | Result |
|---|---|
| **Time on battery under full load** | **3.64 h** (13,101 s), 4.049 V to 3.499 V |
| Voltage at each half hour | 0.0 h 4.049 V; 0.5 h 4.010; 1.0 h 3.979; 1.5 h 3.876; 2.0 h 3.795; 2.5 h 3.714; 3.0 h 3.612; 3.5 h 3.524; end 3.499 |
| Shape of the discharge | Nearly flat near 4.0 V for the first hour, then close to a straight line downwards: **about 0.18 V an hour** from 1.5 h to the end (0.176 V/h over 1.5 to 3.5 h; 0.191 V/h over the last 30 minutes) |
| Frame rate (per 15 frames, after the first minute, 25,738 windows) | median 30.0, 5th percentile 28.5, 1st percentile 20.1, minimum 18.9 FPS; no window over 1 s |
| Camera-to-callback delay | median 104 ms, 95th percentile 127, 99th 132, maximum 154 (25,741 samples) |
| Program CPU | mean 74.7 % of one core (4 available); busiest core 28.7 % on average |
| SoC temperature | 55.1 C to 67.8 C, mean 63.9 C |
| Throttling | `0x0` in every sample: no under-voltage, no frequency cap, none since boot; the under-voltage alarm never rose, **including at 3.5 V on battery** |
| Clean stop | Ctrl+C: exit code 0 in 0.6 s |

Figure: `evidence/perf-rundown-voltage-figure.svg` (the cell voltage during the run, with the start, the 3.5 V stop and
the end marked; the script that draws it sits beside it). A three-panel version with frame rate and temperature is
`perf-rundown-paper-figure.png`.

## 4. Why 3.64 hours is a lower bound, and what is not known

1. **The stop at 3.5 V was my choice** (the tool's setting, proposed on 2026-10-19 and recorded in the run's system
   file). It was a safety margin: nothing in this project shuts the Pi down cleanly on a low battery (the task is
   not built), and the hat cuts power abruptly near 3.2 V, which risks the SD card. The vendor's own tool calls 3.4 to
   3.55 V "Low" and under 3.4 V "Critical", so the run stopped in the "Low" band.
2. **After the stop the Pi kept running, at idle, with the program stopped.** Voltage, read by hand: 3.461 V at 01:26,
   3.454 V at 01:28, 3.434 V at 01:36, 3.430 V at 01:43 (the vendor's formula gave 0.2 % at 01:43). The charger went in at
   about 01:47, so the pack held for **at least 1 hour after the stop**. That hour is at idle and is **not comparable** to
   the 3.64 hours; it shows only that the cell had charge left below 3.5 V. The program was not running then.
3. **The time from 3.5 V to the shutdown point under load was not measured.** The voltage was falling at about 0.18 V an
   hour. Continued in a straight line that reaches 3.4 V in about half an hour and 3.3 V in about an hour, which would put
   the full-load time to the cut-off at roughly 4 to 4.7 hours. **This is an extrapolation, not a result.** A real cell
   usually falls faster near empty, so it is an order-of-magnitude guide only. Do not cite it as a measurement.
4. **The start was not from a fresh full charge** (4.05 V loaded, 4.09 V unloaded, against about 4.18 V for a full cell),
   so a full charge would add a little time at the flat top of the curve.
5. **The gauge's percentage was not used, and should not be quoted.** It read 90.2 % at the start and 2.0 % at the
   3.5 V stop, and on the evening before it read 85 % at a full-cell 4.185 V. The vendor's tools read the same register
   and would show the same numbers, so these are the "official" values, but they did not track the voltage. The
   measurement is the voltage and the time.
6. **Not measured:** the current or power drawn (the gauge reports neither; the system's own power reading was not
   recorded), so **no watt-hour figure is given**, and the **pack's capacity, number of cells and age are not recorded
   anywhere in the project: please fill them in.** Also not included: the real glasses board and its motors (they have
   their own cell, which the Pi cannot read), the control panel, a display, and the room temperature.
7. **One run, one pack, one scene.** There is no repeat. The Pi was reached over a remote session throughout; to my
   knowledge no tests or other work ran on it during the run.

## 5. What the data says about the system, apart from battery life

- **Performance did not degrade as the cell emptied:** the frame rate, delay, temperature and CPU load at 3.5 V match the
  start, and the Pi never reported under-voltage.
- **A periodic dip in frame rate:** 34 dips below 24 FPS in the 3.64 hours, a median 6.5 minutes apart, a median 19 s long,
  down to 18.9 FPS. The same dips, at about the same spacing (about 6 minutes), appeared in the two earlier 20-minute
  runs. The power source was not recorded in those runs (the Pi was probably on the charger), so they are probably not
  caused by the battery or its voltage, but this is not proven. The cause is not identified.
- **Memory grew about 7 to 11 MB an hour without levelling off** (196 MB at the start, 240 MB at the end; about 12 MB
  of that from two vision restarts). It fails the project's 10-minute stability rule (+5 %) at +21.8 % over this longer
  run.
- **The camera dropped on its own at 90.9 minutes, twice within 20 seconds**, with nobody touching it, at 3.88 V and no
  under-voltage flag. The camera-lost failsafe spoke its alert, played its vibration pattern, and the pipeline
  recovered in about a second each time. The same kind of drop was seen once on 2026-10-15.
- **The camera-view check fired on a real event** at 187 minutes: the picture went black, "Camera is blocked or it is too
  dark" was spoken after three checks, then a bright flash and a normal view, and "Camera view is back."
- **Two items for the detection owner:** a persistent table in the centre of the view was announced about every 1.3
  seconds for 31 minutes, and the detection frame-rate figure in the program's own log is inflated after a vision restart.

## 6. Handoff

**Files** (all under `docs/testing/reports/T-15/evidence/`)

| File | What it is |
|---|---|
| `perf-rundown-voltage-figure.svg` (and `.png`, `.py`) | **The figure for the paper:** the cell voltage during the run only, nothing after the stop, no frame rate, no temperature. The SVG is vector (sharp at any size). The small upward blip at 1.5 h is the camera dropping, when the load briefly fell |
| `perf-rundown-paper-figure.png` (and `.py`) | A three-panel version (voltage, frame rate, temperature) that also marks the idle readings after the stop; kept for reference |
| `perf-rundown.png` | The tool's own seven-panel chart (its battery panel shows the gauge's percentage, which this report does not use) |
| `perf-rundown.csv` | Every sample: elapsed time, voltage, gauge percentage, power source, temperature, CPU and memory |
| `perf-rundown.txt` | The tool's summary. **Ignore two of its lines:** the "drain by the gauge's percentage ... 3.9 h" estimate, and the "detection FPS median 54.5" (inflated after the restart) |
| `perf-rundown-app.log` | The program's console, a timestamp on every line (frame rate, delay, alerts, the camera drop) |
| `perf-rundown-system.txt` | The conditions at the start: kernel, versions, which files were uncommitted, the start voltage |

**To reproduce or extend**
- A new run: charge the pack, stop the boot service (`sudo systemctl stop second-vision`), then
  `my_hailo_env/bin/python docs/testing/tools/perf_run.py --duration 36000 --stop-at-volts 3.5 --wait-for-battery --label rundown`,
  and unplug the charger. It waits for the unplug and starts at once.
- **To measure the time to depletion, which this run did not:** start from a full charge (about 4.15 V or more) and stop
  lower, for example `--stop-at-volts 3.3`, **but only with a safety net**, because nothing shuts the Pi down cleanly. The
  vendor's terminal tool (`~/x120x/qtx120xTerminal.py`) schedules a shutdown five minutes after the gauge reads 15 % or less
  on battery; it is not running here, and it relies on the gauge's percentage, so a voltage floor should be added. The
  project's own low-battery shutdown task is the top of the fix ranking and is not built. Expect about 4.5 hours plus the
  charge time.
- For a figure that includes the glasses: run once with the real boards (`--real-boards`), and record the glasses
  cell's own voltage separately.

**Things only you can fill in:** the number, capacity and age of the cells in the X1202; the room temperature; whether
the camera sat in the same scene throughout. **Please also check the wording of "full load":** it means detection and
depth both running at the camera's 30 frames per second, with no motors, on a mostly static scene.
