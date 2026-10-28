# VisionGuard: Fire & Smoke Detection from CCTV Video

**CCTV / video → OpenCV → frame sampling → YOLO (fire, smoke) → confidence filtering → multi-frame confirmation → confirmed event → dashboard + alert → incident storage**

| Member | Part | Code | Status |
|---|---|---|---|
| 1 | Video dataset & preprocessing | `dataset_prep/` | ✅ 141 public videos, video-level train/val/test split, frames, annotation tools |
| 2 | AI / YOLO model | `training/` | ✅ YOLOv8n fire/smoke model, fine-tuning + evaluation (P, R, F1, mAP, confusion matrix) → `models/fire_smoke.onnx` |
| 3 | Video processing & alert system | `vision/` | ✅ OpenCV pipeline, confidence filter, sliding-window confirmation, alerts, incidents (SQLite + snapshot + clip), optional e-mail |
| 4 | Dashboard & testing | `webapp/`, `system_test/` | ✅ HTML/CSS/JS web dashboard (live video, alarms, incidents, analytics), full-system test (false alarms/hour, alert delay, missed events, FPS) |

## Results (unseen test videos, CPU only)

| | |
|---|---|
| Fire/smoke events detected | **11 / 15** test videos |
| False alarms | **1** (car headlights at night), 2.8 per hour of video |
| Alert delay | **8.1 s mean / 4.0 s median** |
| Speed | **17.7 FPS** YOLO on a laptop CPU (ONNX Runtime), 3.1× faster than real time |
| Model, D-Fire test images | Precision 0.766, Recall 0.688, F1 0.725, **mAP50 0.755** |

The four missed videos are hard cases: two night scenes, faint smoke in a dark room, and a 7-second tunnel clip. Full numbers are in `reports/`:
- [model_evaluation.md](reports/model_evaluation.md)
- [system_evaluation.md](reports/system_evaluation.md)
- [system_settings_sweep.md](reports/system_settings_sweep.md)

## Quick start

The repo includes the video dataset: 141 videos stored with **Git LFS**, plus frames, annotation frames and metadata (~2.3 GB). Install Git LFS once before cloning (https://git-lfs.com):
```bash
git lfs install
git clone https://github.com/nareanraj/CV.git
cd CV
pip install -r requirements.txt
python -m dataset_prep.run_all --skip-download   # ~1 min: rebuilds data/videos/{train,val,test} from data/raw
```
Python 3.10–3.12. A GPU is not needed.

- **Downloaded a ZIP from GitHub instead?** It contains only small LFS pointer files. Run `python -m dataset_prep.run_all` to download the videos from the original sources instead (~3 GB).
- **D-Fire training images** are not included. Get them with `python -m training.prepare_dfire`; they are only needed for training.

**Run the dashboard:**
```bash
python -m webapp.server        # or double-click start_dashboard.bat
```
Open http://localhost:8000, choose a source (test video, upload, webcam or CCTV/RTSP camera) and press **▶ Start monitoring**.

**Run without the dashboard:**
```bash
python -m vision.run --source data/videos/test/videos/firesense__posVideo8.877.avi --show
python -m vision.run --source 0 --show                          # webcam
python -m vision.run --source rtsp://user:pass@192.168.1.10/stream --camera "Warehouse Camera 1"
```

---

## Member 1: Video dataset & preprocessing (`dataset_prep/`)

The project uses 141 public fire/smoke videos from FIRESENSE, KMU, Bilkent VisiFire, USTC and Hugging Face: 95 with fire or smoke and 46 without. Sources, licences and citations are in [DATASETS.md](DATASETS.md).

| split | videos | fire/smoke | no fire | hours | frames (1 fps) |
|---|---|---|---|---|---|
| train | 93 | 65 | 28 | 1.97 | 6,195 |
| val | 24 | 15 | 9 | 0.40 | 1,403 |
| test | 24 | 15 | 9 | 0.35 | 1,284 |

```bash
python -m dataset_prep.download          # download the 5 video datasets (resumable)
python -m dataset_prep.build_videos      # merge, remove duplicates, write a ground-truth .txt per video
python -m dataset_prep.organize --separate   # videos.csv + data/organized/{positive,negative}
python -m dataset_prep.split --materialize   # video-level, stratified 70/15/15 split (frozen in splits.csv)
python -m dataset_prep.extract_frames --fps 1
python -m dataset_prep.annotations export    # frames for drawing boxes in CVAT/Roboflow (+ import / validate)
python -m dataset_prep.report            # data/metadata/dataset_report.md
python -m dataset_prep.run_all           # all of the above
```
The split is by **video**, so no video appears in two splits. It is stratified by source and label, and it is frozen, so every team member gets the same split.

## Member 2: AI / YOLO model (`training/`)

```bash
python -m training.prepare_dfire   # D-Fire: 21,527 CCTV-style images with fire/smoke boxes -> data/dfire_yolo
python -m training.train            # fine-tune (CPU: ~1 h; see options for a full GPU run)
python -m training.evaluate         # compare models, pick the best, export models/fire_smoke.{pt,onnx}
```

**Why D-Fire?** Our videos say *whether* there is fire, not *where*. A detector needs boxes, so it is trained on D-Fire, a public dataset of CCTV-style images with fire and smoke boxes.

**Model.** The starting point is YOLOv8n already trained on D-Fire ([rabahdev/fire-smoke-yolov8n](https://huggingface.co/rabahdev/fire-smoke-yolov8n), AGPL-3.0). Fine-tuning added:
- 400 **hard negatives** from our training videos: car lights, fog, and moving objects that look like flames.
- A random D-Fire sample, with the backbone frozen so it runs on a CPU.

**Selection.** Both models are evaluated:
- on the D-Fire **test** images: P, R, F1, mAP50, mAP50-95 per class, plus a confusion matrix;
- on **video frames** from our val and test splits.

The winner is chosen on the **validation** videos only. The short CPU fine-tune did not beat the base model, which scored 0.755 vs 0.755 mAP50 and had higher recall on our videos. So the **base model is the delivered model**: `models/fire_smoke.onnx`, plus `.pt` for further training. A longer training run on a GPU (e.g. Google Colab) is the obvious next step:
```bash
python -m training.train --epochs 30 --n-train 0 --freeze 0 --batch 32   # all of D-Fire, all layers
```
Class ids follow D-Fire (`0 = smoke`, `1 = fire`). All other code uses class **names**, so the id order doesn't matter.

## Member 3: Video processing & alert system (`vision/`)

| Step | File | What it does |
|---|---|---|
| Video input | `pipeline.py` | Video file, webcam index or RTSP/HTTP stream. Reconnects if a camera drops. |
| Frame sampling | `pipeline.py` | YOLO runs on `sample_fps` frames per second (default 5). Every frame is still displayed. |
| Detection | `detector.py` | YOLO via ONNX Runtime (about 2× faster than PyTorch on CPU). Returns label, box and confidence. |
| Confidence filtering | `detector.py` | Detections below `conf_threshold` are dropped. |
| Multi-frame confirmation | `confirm.py` | A sliding window per class: an event is CONFIRMED when the class appears in ≥ 7 of the last 10 analysed frames, and ends at ≤ 2 of 10. |
| Alert | `notifier.py` | Prints `🚨 FIRE DETECTED`, camera, confidence, time and severity (fire = HIGH, smoke = MEDIUM). Optional e-mail via `SMTP_*` environment variables. |
| Incident storage | `incidents.py` | SQLite `incidents/incidents.db`, the annotated detection frame (.jpg) and a clip from 3 s before to 5 s after confirmation (H.264 .mp4). |

The defaults (`conf_threshold = 0.3`, confirm 7 of 10) were chosen on the **validation** videos by `python -m system_test.evaluate_system --sweep`. The rule was the fewest missed fires plus false alarms. Settings are in `vision/config.py`; the CLI and dashboard can override them.

## Member 4: Dashboard & testing (`webapp/`, `system_test/`)

**Web dashboard** (`python -m webapp.server` → http://localhost:8000). It is plain HTML/CSS/JS with no external libraries, so it also works offline. A FastAPI backend runs the Member 3 pipeline and serves the live video as an MJPEG stream, plus live status through Server-Sent Events.

- **Live monitor:**
  - **Video:** the processed video with bounding boxes, LIVE badge, camera name, video time, FPS and a progress bar.
  - **Status banner:** green *Normal*, amber *SMOKE DETECTED*, or a pulsing red *CRITICAL ALERT: FIRE DETECTED*.
  - **Full-screen alarm:** shows camera, confidence, time, severity and the detection snapshot, with an optional siren. After *Acknowledge*, repeats of the same type within 60 s only go to the alert feed.
  - **Status cards:** camera online/offline, status, fire and smoke detections (with event counts), active alerts and confidence.
  - **Live chart:** fire and smoke confidence over time, with the threshold line.
  - **Confirmation meters:** how many of the last N frames detected fire or smoke, and where the confirmation threshold is.
  - **Alert feed:** thumbnails, type, severity, confidence and time.
  - **Source picker:** dataset test videos, drag-and-drop upload, webcam or CCTV/RTSP URL, with adjustable detection settings.
  - **Video tools:** fullscreen, snapshot download, and alert markers on the video timeline.
- **Incidents:** every confirmed event as a card, with filters (fire/smoke, review status) and search (camera, source, notes). Clicking a card opens a pop-up with:
  - the video clip and detection frame;
  - **operator review**: mark it *Confirmed fire* or *False alarm*;
  - **notes**, clip download and delete.

  **Export CSV** downloads the whole history (opens in Excel).
- **Analytics:**
  - **Live statistics:** incidents per day, fire vs smoke, and the operator-review false-alarm rate.
  - **System-test results:** KPIs, per-video results (detected / missed / false alarm), model metrics, the settings sweep and the confusion matrices.
- **Themes and preferences** (🎨 button):
  - **Dark / Light / Auto** (follows Windows), with 5 accent colours.
  - **Alarm settings:** alarm sound and volume, desktop notifications, full-screen alarm on/off, and snooze time.
  - **Background alerts:** if the tab is in the background, the browser shows a desktop notification and the tab title and icon flash.
  - Preferences are remembered.
- **Keyboard shortcuts** (press `?`):

  | Key | Action |
  |---|---|
  | `Space` | start / stop |
  | `F` | fullscreen |
  | `S` | snapshot |
  | `M` | sound on/off |
  | `T` | switch theme |
  | `1` `2` `3` | switch tabs |
  | `Esc` | acknowledge alarm |

A simpler Streamlit version is still available: `python -m streamlit run dashboard/app.py`.

**System test** (`python -m system_test.evaluate_system`). It runs the complete pipeline on all unseen test videos, using the ONFIRE 2023 rules:
- an alert counts if it comes no earlier than 5 s before the fire starts;
- any alert in a no-fire video is a false alarm.

It reports false alarms per hour, alert delay, missed events, video-level precision/recall/F1 and FPS. The output goes to `reports/system_evaluation.md`, with per-video results in `reports/system_evaluation_test.csv`.

---

## Folder layout
```
CV/
├── dataset_prep/      Member 1: download, organise, split, frames, annotations
├── training/          Member 2: D-Fire preparation, fine-tuning, evaluation
├── models/            fire_smoke.onnx (used by the system), fire_smoke.pt
├── vision/            Member 3: detector, confirmation, pipeline, alerts, incidents, CLI
├── webapp/            Member 4: web dashboard (server.py + static/ HTML, CSS, JS)
├── dashboard/         Member 4: simple Streamlit dashboard (alternative)
├── system_test/       Member 4: full-system evaluation
├── reports/           evaluation results (markdown, csv, confusion matrices)
├── DATASETS.md        video sources, licences, citations
├── start_dashboard.bat
└── data/              raw/<source>/ videos (Git LFS), frames/, annotation/, metadata/
```

## Limitations
- **No fire start times.** The public videos don't give them, so a positive video counts as fire from 0 s. The reported alert delay therefore includes the time before fire is visible.
- **Hard cases.** Night scenes and faint indoor smoke are the main misses. Car headlights caused the one false alarm. More night-time training data, or a longer GPU fine-tune, would help most.
- **Licences.** KMU, VisiFire and USTC videos are for research use. The base model is AGPL-3.0.
- **Not a certified system.** This is a student research project, not a certified fire alarm.
