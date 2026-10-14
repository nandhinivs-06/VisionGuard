"""Step 2 - Split videos into train / val / test.

The split is done at VIDEO level (never at frame level) so frames of the same
video can't leak between train and test. It is stratified by source and label so every
split keeps the same mix of datasets and positive/negative ratio, and it is reproducible (fixed --seed).

Outputs:
    data/metadata/splits.csv          videos.csv + a `split` column
    data/metadata/{train,val,test}.txt
    data/videos/<split>/videos/*.mp4  (with --materialize)
    data/videos/<split>/gt/*.txt      ONFIRE-format GT, used for video-level evaluation

    python -m dataset_prep.split --materialize
"""
import argparse
import random

from .common import (
    METADATA_DIR, NEGATIVE, POSITIVE, RAW_DIR, SPLIT_VIDEOS_DIR, SPLITS, SPLITS_CSV, VIDEOS_CSV,
    link_or_copy, read_csv, require, write_csv,
)
from pathlib import Path


def stratified_split(rows, ratios, seed):
    rng = random.Random(seed)
    out = {s: [] for s in SPLITS}
    # stratify by (source, label) so every dataset and both classes appear in every split
    for key in sorted({(r.get("source", ""), r["label"]) for r in rows}):
        group = sorted((r for r in rows if (r.get("source", ""), r["label"]) == key), key=lambda r: r["video_id"])
        rng.shuffle(group)
        n = len(group)
        n_val, n_test = round(n * ratios[1]), round(n * ratios[2])
        if n >= 3:  # make sure val/test see at least one video of each class
            n_val, n_test = max(n_val, 1), max(n_test, 1)
        out["test"] += group[:n_test]
        out["val"] += group[n_test:n_test + n_val]
        out["train"] += group[n_test + n_val:]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ratios", type=float, nargs=3, default=[0.70, 0.15, 0.15], metavar=("TRAIN", "VAL", "TEST"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--materialize", action="store_true",
                    help="Place videos + ONFIRE-format GT into data/videos/<split>/")
    ap.add_argument("--raw", default=str(RAW_DIR), help="Raw dataset folder used in step 1")
    ap.add_argument("--link-mode", choices=["hardlink", "copy"], default="hardlink")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing split (changes train/test membership!)")
    args = ap.parse_args(argv)

    require(VIDEOS_CSV, "run `python -m dataset_prep.organize` first")
    if abs(sum(args.ratios) - 1.0) > 1e-6:
        raise SystemExit("--ratios must sum to 1")

    if SPLITS_CSV.exists() and not args.force:
        print(f"{SPLITS_CSV} already exists - keeping the existing split (use --force to re-split).")
        rows = read_csv(SPLITS_CSV)
    else:
        videos = read_csv(VIDEOS_CSV)
        usable = [r for r in videos if r["label"] in (POSITIVE, NEGATIVE) and r["readable"] == "True"]
        skipped = len(videos) - len(usable)
        if skipped:
            print(f"Skipping {skipped} videos with unknown label or unreadable file (see videos.csv 'note').")
        parts = stratified_split(usable, args.ratios, args.seed)
        rows = []
        for s in SPLITS:
            for r in parts[s]:
                rows.append({**r, "split": s})
        write_csv(SPLITS_CSV, rows, list(videos[0].keys()) + ["split"])
        for s in SPLITS:
            (METADATA_DIR / f"{s}.txt").write_text(
                "\n".join(r["filename"] for r in rows if r["split"] == s) + "\n", encoding="utf-8")
        print(f"Wrote {SPLITS_CSV} (seed={args.seed}, ratios={args.ratios})")

    print(f"\n{'split':<6} {'videos':>6} {'pos':>5} {'neg':>5} {'hours':>6}")
    for s in SPLITS:
        sr = [r for r in rows if r["split"] == s]
        pos = sum(r["label"] == POSITIVE for r in sr)
        hours = sum(float(r["duration_sec"]) for r in sr) / 3600
        print(f"{s:<6} {len(sr):>6} {pos:>5} {len(sr) - pos:>5} {hours:>6.2f}")

    if args.materialize:
        raw = Path(args.raw)
        for r in rows:
            base = SPLIT_VIDEOS_DIR / r["split"]
            link_or_copy(raw / r["rel_path"], base / "videos" / r["filename"], args.link_mode)
            gt = base / "gt" / f"{r['filename']}.txt"  # same naming as ONFIRE test.py results
            gt.parent.mkdir(parents=True, exist_ok=True)
            gt.write_text(r["fire_start_sec"] if r["label"] == POSITIVE else "", encoding="utf-8")
        print(f"\nVideos + GT placed in {SPLIT_VIDEOS_DIR}/<split>/ (mode={args.link_mode})")


if __name__ == "__main__":
    main()
