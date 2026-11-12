| Measure | 18 Oct (14-class) | b3-standin-20min | b3-realboards-20min |
|---|---|---|---|
| Average FPS (depth frames / seconds, whole sampled run) | 29.6 (35550 frames in 1200 s) | 29.6 (35580 frames in 1200 s) | 29.6 (35565 frames in 1200 s) |
| 15-frame windows after minute 1: median / p5 / p1 / min | 30.0 / 28.5 / 20.2 / 18.9 (2250 windows) | 30.0 / 28.6 / 20.4 / 19.0 (2252 windows) | 30.0 / 28.5 / 20.4 / 18.0 (2247 windows) |
| Capture → depth callback: median / p95 / p99 / max (ms) | 104 / 127 / 132 / 147 (2251 lines) | 106 / 129 / 134 / 164 (2253 lines) | 106 / 129 / 136 / 158 (2252 lines) |
| App CPU, average (% of one core) | 73 % | 78 % | 77 % |
| Busiest core, average / max | 28 % / 38 % | 30 % / 40 % | 31 % / 64 % |
| App memory, minute 1 → end | 195 → 199 MB (+2.2 %) | 200 → 203 MB (+1.6 %) | 199 → 204 MB (+2.8 %) |
| App CPU, first 3 min after minute 1 / last 3 min | 72 % / 75 % | 75 % / 80 % | 77 % / 76 % |
| SoC temperature first sample / min / max (every 10 s) | 59.5 / 58.4 / 65.5 °C | 52.9 / 52.9 / 61.7 °C | 51.8 / 51.8 / 63.9 °C |
| vcgencmd measure_temp before / after | 57.1 °C / 64.2 °C | 52.7 °C / 60.4 °C | 51.6 °C / 58.7 °C |
| Throttle flags (every 10 s) | 0x0 | 0x0 | 0x0 |
| Dip episodes below 25 FPS (start minute, length; dips <30 s apart merged) | 3.6 min, 19.3 s; 9.4 min, 19.4 s; 15.7 min, 19.5 s | 3.0 min, 19.2 s; 9.2 min, 19.4 s; 15.5 min, 18.7 s | 5.2 min, 19.3 s; 11.2 min, 28.9 s; 17.7 min, 19.3 s |
