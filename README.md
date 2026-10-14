# Fire & Smoke Detection from CCTV Video

Pipeline: **CCTV/video → OpenCV frames → YOLO (fire, smoke) → confidence filter → multi-frame confirmation → alert + dashboard → incident storage**

This folder contains **Member 1's part: Video Dataset & Preprocessing** (`dataset_prep/`). It's built on **public fire/smoke VIDEO datasets**: 141 videos (95 fire/smoke, 46 no fire) from FIRESENSE, KMU, Bilkent VisiFire, USTC and Hugging Face. See [DATASETS.md](DATASETS.md) for sources, licences and citations.

| Member 1 task | Done by |
|---|---|
| Download and organise the videos | `dataset_prep.download` + `dataset_prep.build_videos` + `dataset_prep.organize` |
| Separate fire/smoke and non-fire videos | `dataset_prep.organize --separate` → `data/organized/positive`, `negative` |
| Split videos into train / val / test | `dataset_prep.split` |
| Extract frames | `dataset_prep.extract_frames` |
| Prepare / handle annotations | GT `.txt` per video (`build_videos`); frame labels (`extract_frames`); bounding boxes via CVAT/Roboflow (`dataset_prep.annotations`) |
| Dataset statistics | `dataset_prep.report` |

## Getting the data
The videos and frames (~2.3 GB) are **not in this repo**: some videos are over GitHub's 100 MB file limit, and several datasets only allow research use. Only `data/metadata/` is committed (video list, frozen split, frame index, report). Get the data in either of these ways:
- **Shared zip** from the team drive: unzip it into this folder, then run `python -m dataset_prep.run_all --skip-download`.
- **From the original sources:** run `python -m dataset_prep.run_all`, which downloads ~3 GB.

Both give exactly the same videos and train/val/test split, because `data/metadata/splits.csv` is committed.

## Dataset summary

| split | videos | fire/smoke | no fire | hours | frames (1 fps) |
|---|---|---|---|---|---|
| train | 93 | 65 | 28 | 1.97 | 6,195 |
| val | 24 | 15 | 9 | 0.40 | 1,403 |
| test | 24 | 15 | 9 | 0.35 | 1,284 |

## Setup
```bash
pip install -r requirements.txt
```
Run every command from this folder (`cv project/`).

## After unzipping the shared zip
The zip contains each video only once: the original downloads in `data/raw/<source>/`. To recreate the merged `data/raw/videos/` and the train/val/test folders in `data/videos/`, run:
```bash
python -m dataset_prep.run_all --skip-download
```
It takes about a minute and keeps exactly the same split, because `data/metadata/splits.csv` is included.

## Rebuild everything
```bash
python -m dataset_prep.run_all
```
This runs: download (~3 GB, resumable) → build_videos → organize → split → extract frames → report.

## Step by step

### 0. Download
```bash
python -m dataset_prep.download --list
python -m dataset_prep.download                       # all sources
python -m dataset_prep.download --only kmu firesense
```
- Files go to `data/raw/<source>/`. Big files download over several parallel connections.
- Each file's size is checked against the server, and stalled or truncated downloads are resumed automatically.
- A `DONE` file marks a finished source.

### 1. Merge, organise, separate
```bash
python -m dataset_prep.build_videos        # -> data/raw/videos/<source>__<name> + GT .txt
python -m dataset_prep.organize --separate # -> data/metadata/videos.csv, data/organized/{positive,negative}
```
- `build_videos` labels each video from its dataset's folder structure, removes duplicate clips, and writes the GT file: `0` = fire from the start, empty = no fire.
- `videos.csv` has one row per video: source, label, fire start, fps, frames, resolution, duration and notes.

### 2. Train / val / test split
```bash
python -m dataset_prep.split --materialize        # 70 / 15 / 15, seed 42
```
- The split is by **video**, so frames of one video never appear in two splits.
- It's stratified by **source and label**, so every dataset and both classes appear in every split.
- It's frozen once created. `--force` re-splits.
- Output: `data/videos/<split>/videos/` + `data/videos/<split>/gt/`, used for the video-level evaluation (false alarms per hour, alert delay, missed events, FPS).

### 3. Extract frames
```bash
python -m dataset_prep.extract_frames --fps 1 --max-frames-per-video 300
python -m dataset_prep.extract_frames --splits test --fps 5      # denser, one split
```
- Frames go to `data/frames/<split>/<video>/<video>_<frame>.jpg`.
- The index is `data/metadata/frames.csv`, with a label per frame: `fire`, `no_fire` or `uncertain`.

### 4. Bounding-box annotations for YOLO (fire / smoke)
The video datasets say *whether* there is fire, not *where*. To train YOLO, draw boxes on a sample of the fire frames:
```bash
python -m dataset_prep.annotations export --per-video 20     # -> data/annotation/to_label/<split>/
```
Upload `data/annotation/to_label/` to **CVAT** or **Roboflow** with labels in this order: `fire`, `smoke`. Don't rename the files. Then export as **YOLO** and run:
```bash
python -m dataset_prep.annotations import --labels path/to/unzipped_export --clean
python -m dataset_prep.annotations validate
```
This builds `data/yolo/` (`data.yaml`, `images/`, `labels/`). It:
- keeps the video-level split
- matches classes by name
- undoes Roboflow's file renaming
- adds background frames from no-fire videos

Train with:
```bash
yolo detect train data=data/yolo/data.yaml model=yolov8n.pt epochs=100 imgsz=640
```

### 5. Report
```bash
python -m dataset_prep.report      # -> data/metadata/dataset_report.md
```

## What Member 1 hands to the team

| For | File / folder |
|---|---|
| System evaluation on video (false alarms, alert delay, missed events, FPS) | `data/videos/test/videos/` + `data/videos/test/gt/` |
| Training / validation videos | `data/videos/train/`, `data/videos/val/` |
| Frame-level experiments | `data/metadata/frames.csv` + `data/frames/` |
| YOLO training set (after annotation) | `data/yolo/data.yaml` |
| Report / presentation | `data/metadata/dataset_report.md`, `DATASETS.md` |

## Folder layout
```
cv project/
├── dataset_prep/            # Member 1 code
├── DATASETS.md              # sources, licences, citations
├── docs/                    # ONFIRE2023 request email (only if you want that set too)
└── data/                    # large, not for git
    ├── raw/<source>/        # downloads: firesense, kmu, visifire, ustc_vsd, hf_fire_videos
    ├── raw/videos/          # merged videos + GT .txt (141 videos)
    ├── metadata/            # videos.csv, splits.csv, frames.csv, video_sources.csv, report
    ├── organized/           # positive/ negative/
    ├── videos/              # <split>/videos + <split>/gt
    ├── frames/              # <split>/<video>/*.jpg
    └── annotation/          # frames to label (step 4)
```

The ONFIRE2023 contest set can still be added later: put it in `data/raw/onfire2023/` and run `python -m dataset_prep.organize --raw data/raw/onfire2023`.
