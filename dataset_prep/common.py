"""Shared helpers for the ONFIRE2023 dataset-preparation pipeline (Member 1)."""
import csv
import os
import re
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / "videos"  # merged public videos (build_videos.py)
METADATA_DIR = DATA_DIR / "metadata"
VIDEOS_CSV = METADATA_DIR / "videos.csv"
SPLITS_CSV = METADATA_DIR / "splits.csv"
FRAMES_DIR = DATA_DIR / "frames"
FRAMES_CSV = METADATA_DIR / "frames.csv"
SPLIT_VIDEOS_DIR = DATA_DIR / "videos"
ANNOTATION_DIR = DATA_DIR / "annotation"
YOLO_DIR = DATA_DIR / "yolo"

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".mpg", ".mpeg", ".wmv", ".m4v", ".flv"}
SPLITS = ("train", "val", "test")
# Class ids shared with the YOLO model (Member 2) — keep this order everywhere.
CLASSES = ("fire", "smoke")

POSITIVE, NEGATIVE, UNKNOWN = "positive", "negative", "unknown"


def find_videos(root):
    """All video files under `root` (recursive), sorted naturally (Video2 < Video10)."""
    root = Path(root)
    vids = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    return sorted(vids, key=lambda p: natural_key(p.name))


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def index_gt_files(root):
    """Map lowercase lookup keys -> .txt path for every ground-truth file under root.

    ONFIRE GT files may be named `Video0.txt` or `Video0.mp4.txt`; both are indexed
    so either naming style matches its video.
    """
    index = {}
    for p in Path(root).rglob("*.txt"):
        name = p.name.lower()
        index.setdefault(name[:-4], p)  # "video0.mp4" or "video0"
    return index


def find_gt_for_video(video_path, gt_index):
    name = video_path.name.lower()
    return gt_index.get(name) or gt_index.get(Path(name).stem)


def parse_gt_file(path):
    """Return the fire-start value in the file, or None if the file is empty (negative video).

    ONFIRE format: a positive video's .txt contains a single number (the fire start
    instant); a negative video's .txt is empty.
    """
    text = Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        raise ValueError(f"Unrecognised ground-truth content in {path}: {text[:80]!r}")
    return float(m.group(0))


def probe_video(path):
    """Read basic video properties with OpenCV. Falls back to counting frames if needed."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"readable": False, "fps": 0.0, "frame_count": 0, "width": 0, "height": 0, "duration_sec": 0.0}
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if n <= 0:  # some containers don't report a frame count
        n = 0
        while cap.grab():
            n += 1
    cap.release()
    if fps <= 0 or fps > 240:
        fps = 25.0  # sane default for broken headers
    return {
        "readable": True,
        "fps": round(fps, 4),
        "frame_count": n,
        "width": w,
        "height": h,
        "duration_sec": round(n / fps, 3) if fps else 0.0,
    }


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fieldnames=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def require(path, hint):
    if not Path(path).exists():
        raise SystemExit(f"Missing {path}\n-> {hint}")


def link_or_copy(src, dst, mode="copy"):
    """Place src at dst using a hardlink (no extra disk space, same drive) or a copy."""
    import shutil

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    if mode == "hardlink":
        try:
            os.link(src, dst)
            return
        except OSError:
            pass  # different drive / unsupported FS -> fall back to copy
    shutil.copy2(src, dst)
