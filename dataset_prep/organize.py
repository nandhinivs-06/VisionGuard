"""Step 1 - Organise the raw ONFIRE2023 videos.

Scans the raw dataset folder, pairs every video with its ground-truth .txt,
reads video properties with OpenCV and writes data/metadata/videos.csv.
Videos are separated into positive (fire/smoke) and negative (no fire).

    python -m dataset_prep.organize                       # default: data/raw/videos (build_videos.py)
    python -m dataset_prep.organize --raw data/raw/onfire2023 --separate   # e.g. ONFIRE2023 if obtained
"""
import argparse
from pathlib import Path

from .common import (
    METADATA_DIR, NEGATIVE, POSITIVE, RAW_DIR, UNKNOWN, VIDEOS_CSV, DATA_DIR,
    find_gt_for_video, find_videos, index_gt_files, link_or_copy, parse_gt_file,
    probe_video, write_csv,
)

FIELDS = [
    "video_id", "filename", "rel_path", "source", "label", "fire_start_sec", "fire_start_frame",
    "fps", "frame_count", "width", "height", "duration_sec", "readable", "gt_file", "note",
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(RAW_DIR), help="Folder with the ONFIRE videos (searched recursively)")
    ap.add_argument("--gt-dir", default=None, help="Folder with GT .txt files (default: same as --raw)")
    ap.add_argument("--gt-unit", choices=["seconds", "frames"], default="seconds",
                    help="Unit of the fire start value inside GT files (ONFIRE uses seconds)")
    ap.add_argument("--separate", action="store_true",
                    help="Also place videos into data/organized/{positive,negative}/")
    ap.add_argument("--link-mode", choices=["hardlink", "copy"], default="hardlink")
    args = ap.parse_args(argv)

    raw = Path(args.raw)
    videos = find_videos(raw) if raw.exists() else []
    if not videos:
        raise SystemExit(f"No videos found in {raw}\n-> run `python -m dataset_prep.download` and `python -m dataset_prep.build_videos` first "
                         f"(see README.md).")
    gt_index = index_gt_files(args.gt_dir or raw)
    print(f"Found {len(videos)} videos and {len(gt_index)} .txt files in {raw}")

    rows = []
    for i, v in enumerate(videos, 1):
        info = probe_video(v)
        gt = find_gt_for_video(v, gt_index)
        notes = []
        start_sec = start_frame = ""
        if gt is None:
            label = UNKNOWN
            notes.append("no GT file")
        else:
            value = parse_gt_file(gt)
            if value is None:
                label = NEGATIVE
            else:
                label = POSITIVE
                fps = info["fps"]
                if args.gt_unit == "seconds":
                    start_sec, start_frame = value, int(round(value * fps))
                else:
                    start_frame, start_sec = int(value), round(value / fps, 3)
                if info["duration_sec"] and start_sec > info["duration_sec"]:
                    notes.append("fire start after video end - check GT unit")
        if not info["readable"]:
            notes.append("OpenCV cannot read video")
        rows.append({
            "video_id": v.stem, "filename": v.name, "rel_path": v.relative_to(raw).as_posix(),
            "source": v.name.split("__")[0] if "__" in v.name else raw.name,
            "label": label, "fire_start_sec": start_sec, "fire_start_frame": start_frame,
            **info, "gt_file": gt.relative_to(args.gt_dir or raw).as_posix() if gt else "",
            "note": "; ".join(notes),
        })
        print(f"  [{i}/{len(videos)}] {v.name:<20} {label:<9} "
              f"{info['width']}x{info['height']} {info['fps']:.1f}fps {info['duration_sec']:.0f}s "
              f"{'start=' + str(start_sec) + 's' if start_sec != '' else ''} {rows[-1]['note']}")

    dup = {r["video_id"] for r in rows if sum(x["video_id"] == r["video_id"] for x in rows) > 1}
    if dup:
        raise SystemExit(f"Duplicate video names in different folders: {sorted(dup)} - rename them first.")

    write_csv(VIDEOS_CSV, rows, FIELDS)
    for label in (POSITIVE, NEGATIVE):
        names = [r["filename"] for r in rows if r["label"] == label]
        (METADATA_DIR / f"{label}_videos.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
        if args.separate:
            for r in rows:
                if r["label"] == label:
                    link_or_copy(raw / r["rel_path"], DATA_DIR / "organized" / label / r["filename"], args.link_mode)

    counts = {k: sum(r["label"] == k for r in rows) for k in (POSITIVE, NEGATIVE, UNKNOWN)}
    print(f"\nWrote {VIDEOS_CSV}")
    print(f"positive (fire/smoke): {counts[POSITIVE]}   negative: {counts[NEGATIVE]}   unknown: {counts[UNKNOWN]}")
    problems = [r for r in rows if r["note"]]
    if problems:
        print(f"WARNING: {len(problems)} videos have notes - check the 'note' column in videos.csv")


if __name__ == "__main__":
    main()
