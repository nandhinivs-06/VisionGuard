# System evaluation (Member 4)

Full pipeline on the **test** videos. These were not used for training or for choosing the model. Settings: conf >= 0.3, 5 analysed frames/s, confirm 7/10 frames, model `fire_smoke.onnx`.

| metric | value |
|---|---|
| videos (fire/smoke / no fire) | 24 (15 / 9), 0.35 h |
| fire/smoke events detected | **11 / 15** |
| missed fire events | **4** |
| false alarms (total) | **1** |
| no-fire videos with a false alarm | 1 / 9 |
| false alarms per hour (all video) | **2.8** |
| false alarms per hour (no-fire video only) | 12.4 |
| video-level precision / recall / F1 | 0.917 / 0.733 / 0.815 |
| alert delay, mean / median (s) | **8.1 / 4.0** |
| YOLO processing speed | **17.7 FPS** (CPU) |
| end-to-end speed | 3.1x real time |

Alert delay is measured from the annotated fire start. The public videos have no start times, so the start counts as 0 s, and the delay includes the time the fire takes to become visible.

Per-video results: `reports/system_evaluation_test.csv`
