"""Step 3 - Extract frames from the split videos with OpenCV.

Samples frames at a fixed rate (--fps), saves them as JPG and writes an index
(data/metadata/frames.csv) with a weak frame-level label derived from the
ONFIRE fire-start time:

    negative video                      -> no_fire
    positive video, t <  start - margin -> no_fire   (fire hasn't started yet)
    positive video, start - margin <= t < start -> uncertain (ignition moment, skip for training)
    positive video, t >= start          -> fire      (fire and/or smoke visible)

These labels are video-derived, NOT bounding boxes. Boxes come from the
annotation step (see dataset_prep.annotations).

    python -m dataset_prep.extract_frames --fps 1
    python -m dataset_prep.extract_frames --splits test --fps 5 --max-side 640
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

from .common import (
    DATA_DIR, FRAMES_CSV, FRAMES_DIR, NEGATIVE, RAW_DIR, SPLITS, SPLITS_CSV,
    read_csv, require, write_csv,
)

FIELDS = ["split", "video_id", "frame_idx", "time_sec", "video_label", "frame_label", "path"]


def frame_label(video_label, t, start, margin):
    if video_label == NEGATIVE:
        return "no_fire"
    if t >= start:
        return "fire"
    if t >= start - margin:
        return "uncertain"
    return "no_fire"


def target_indices(frame_count, fps, sample_fps, max_frames):
    step = max(fps / sample_fps, 1.0) if sample_fps > 0 else 1.0
    idx = np.unique(np.floor(np.arange(0, frame_count, step)).astype(int))
    if max_frames and len(idx) > max_frames:
        idx = idx[np.linspace(0, len(idx) - 1, max_frames).round().astype(int)]
    return set(idx.tolist())


def save_jpg(path, img, quality):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        buf.tofile(str(path))  # tofile handles any Windows path
    return ok


def extract_video(row, video_path, out_dir, args):
    fps = float(row["fps"])
    targets = target_indices(int(row["frame_count"]), fps, args.fps, args.max_frames_per_video)
    start = float(row["fire_start_sec"]) if row["fire_start_sec"] else None
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    rows, idx, last = [], 0, max(targets) if targets else -1
    while idx <= last and cap.grab():  # grab() is cheap; decode only sampled frames
        if idx in targets:
            name = f"{row['video_id']}_{idx:06d}.jpg"
            dst = out_dir / name
            if not dst.exists():
                ok, img = cap.retrieve()
                if not ok:
                    break
                if args.max_side and max(img.shape[:2]) > args.max_side:
                    s = args.max_side / max(img.shape[:2])
                    img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
                save_jpg(dst, img, args.quality)
            t = idx / fps
            rows.append({
                "split": row["split"], "video_id": row["video_id"], "frame_idx": idx,
                "time_sec": round(t, 3), "video_label": row["label"],
                "frame_label": frame_label(row["label"], t, start, args.uncertain_margin),
                "path": dst.relative_to(DATA_DIR).as_posix(),
            })
        idx += 1
    cap.release()
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--splits", nargs="+", choices=SPLITS, default=list(SPLITS))
    ap.add_argument("--fps", type=float, default=1.0, help="Frames sampled per second of video (0 = every frame)")
    ap.add_argument("--max-frames-per-video", type=int, default=0,
                    help="Cap per video (evenly spaced) so long videos don't dominate; 0 = no cap")
    ap.add_argument("--max-side", type=int, default=0, help="Resize so the longest side <= this; 0 = keep size")
    ap.add_argument("--uncertain-margin", type=float, default=2.0,
                    help="Seconds before the GT fire start marked 'uncertain'")
    ap.add_argument("--quality", type=int, default=92, help="JPEG quality")
    ap.add_argument("--raw", default=str(RAW_DIR))
    args = ap.parse_args(argv)

    require(SPLITS_CSV, "run `python -m dataset_prep.split` first")
    videos = [r for r in read_csv(SPLITS_CSV) if r["split"] in args.splits]
    raw = Path(args.raw)

    # keep index rows of splits we are not re-extracting now
    kept = [r for r in read_csv(FRAMES_CSV) if r["split"] not in args.splits] if FRAMES_CSV.exists() else []
    new = []
    for i, r in enumerate(videos, 1):
        rows = extract_video(r, raw / r["rel_path"], FRAMES_DIR / r["split"] / r["video_id"], args)
        new += rows
        n_fire = sum(x["frame_label"] == "fire" for x in rows)
        print(f"  [{i}/{len(videos)}] {r['split']:<5} {r['filename']:<20} {len(rows):>5} frames ({n_fire} fire)")

    write_csv(FRAMES_CSV, kept + new, FIELDS)
    print(f"\nWrote {FRAMES_CSV}")
    print(f"{'split':<6} {'frames':>7} {'fire':>7} {'no_fire':>8} {'uncertain':>9}")
    for s in args.splits:
        fr = [x for x in new if x["split"] == s]
        c = {k: sum(x["frame_label"] == k for x in fr) for k in ("fire", "no_fire", "uncertain")}
        print(f"{s:<6} {len(fr):>7} {c['fire']:>7} {c['no_fire']:>8} {c['uncertain']:>9}")


if __name__ == "__main__":
    main()
