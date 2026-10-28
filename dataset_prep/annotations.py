"""Step 4 - Prepare / handle bounding-box annotations for the YOLO model.

ONFIRE2023 only gives the fire START TIME per video, not boxes. To train YOLO
(classes: 0=fire, 1=smoke) we need boxes, so this module:

  export    pick fire frames to annotate -> data/annotation/to_label/<split>/
            (upload that folder to CVAT or Roboflow and draw fire/smoke boxes)
  import    take the YOLO-format export from CVAT/Roboflow and build the final
            dataset in data/yolo/ (+ background frames, + data.yaml), keeping
            the video-level train/val/test split
  validate  check every label file in data/yolo/ and print class counts

    python -m dataset_prep.annotations export --per-video 20
    python -m dataset_prep.annotations import --labels path/to/cvat_or_roboflow_export
    python -m dataset_prep.annotations validate
"""
import argparse
import random
import re
import shutil
from collections import Counter
from pathlib import Path

from .common import (
    ANNOTATION_DIR, CLASSES, DATA_DIR, FRAMES_CSV, SPLITS, YOLO_DIR,
    natural_key, read_csv, require, write_csv,
)

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
NAME_FILES = ("obj.names", "classes.txt", "data.yaml", "_darknet.labels")


# ---------------------------------------------------------------- export
def cmd_export(args):
    require(FRAMES_CSV, "run `python -m dataset_prep.extract_frames` first")
    frames = [r for r in read_csv(FRAMES_CSV) if r["frame_label"] == "fire" and r["split"] in args.splits]
    by_video = {}
    for r in frames:
        by_video.setdefault((r["split"], r["video_id"]), []).append(r)

    out = ANNOTATION_DIR / "to_label"
    chosen = []
    for (split, vid), rows in sorted(by_video.items(), key=lambda kv: natural_key(kv[0][1])):
        rows.sort(key=lambda r: int(r["frame_idx"]))
        if args.per_video and len(rows) > args.per_video:  # evenly spaced over the fire part
            step = (len(rows) - 1) / (args.per_video - 1) if args.per_video > 1 else 0
            rows = [rows[round(i * step)] for i in range(args.per_video)]
        for r in rows:
            src = DATA_DIR / r["path"]
            dst = out / split / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            chosen.append({**r, "export_path": dst.relative_to(DATA_DIR).as_posix()})

    (ANNOTATION_DIR / "classes.txt").write_text("\n".join(CLASSES) + "\n", encoding="utf-8")
    write_csv(ANNOTATION_DIR / "to_label_manifest.csv", chosen)
    print(f"Exported {len(chosen)} frames from {len(by_video)} positive videos to {out}")
    for s in args.splits:
        print(f"  {s}: {sum(r['split'] == s for r in chosen)}")
    print(f"Label classes (in this exact order): {list(CLASSES)} -> {ANNOTATION_DIR / 'classes.txt'}")


# ---------------------------------------------------------------- import
def original_stem(stem):
    """Undo Roboflow renaming: 'Video0_000123_jpg.rf.3f2a...' -> 'Video0_000123'."""
    return re.sub(r"_(jpg|jpeg|png)\.rf\.[0-9a-fA-F]+$", "", stem)


def read_export_class_names(root):
    for p in root.rglob("*"):
        if p.name not in NAME_FILES:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if p.suffix == ".yaml":
            m = re.search(r"names:\s*\[(.*?)\]", text, re.S)
            if m:
                return [n.strip().strip("'\"") for n in m.group(1).split(",") if n.strip()]
            m = re.search(r"names:\s*\n((?:\s+(?:\d+:\s*|-\s*).+\n?)+)", text)
            if m:
                return [re.sub(r"^\s*(\d+:|-)\s*", "", l).strip().strip("'\"")
                        for l in m.group(1).splitlines() if l.strip()]
        else:
            return [l.strip() for l in text.splitlines() if l.strip()]
    return None


def build_class_map(names):
    if not names:
        print(f"WARNING: no class-name file found in the export - assuming class ids are {list(CLASSES)}")
        return {i: i for i in range(len(CLASSES))}
    cmap = {}
    for i, n in enumerate(names):
        key = n.lower()
        if key in CLASSES:
            cmap[i] = CLASSES.index(key)
        else:
            print(f"WARNING: export class '{n}' (id {i}) is not in {list(CLASSES)} - its boxes are dropped")
    print(f"Export classes {names} -> mapped to {list(CLASSES)}: {cmap}")
    return cmap


def convert_label(path, cmap):
    """Return cleaned YOLO lines, remapped to our class ids; raises on malformed lines."""
    lines = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 5:
            raise ValueError(f"{path}:{n}: expected 5 values (class cx cy w h), got {len(parts)}"
                             " - export as YOLO *detection* boxes, not polygons")
        cls, *xywh = int(float(parts[0])), *map(float, parts[1:])
        if cls not in cmap:
            continue
        xywh = [min(max(v, 0.0), 1.0) for v in xywh]
        if xywh[2] <= 0 or xywh[3] <= 0:
            continue
        lines.append(f"{cmap[cls]} " + " ".join(f"{v:.6f}" for v in xywh))
    return lines


def cmd_import(args):
    require(FRAMES_CSV, "run `python -m dataset_prep.extract_frames` first")
    labels_root = Path(args.labels)
    require(labels_root, "pass the folder of the YOLO export from CVAT/Roboflow")
    frames = {Path(r["path"]).stem: r for r in read_csv(FRAMES_CSV)}
    cmap = build_class_map(read_export_class_names(labels_root))

    if YOLO_DIR.exists() and args.clean:
        shutil.rmtree(YOLO_DIR)
    annotated, unmatched = {}, []
    for p in labels_root.rglob("*.txt"):
        if p.name in NAME_FILES or p.name.lower() in ("train.txt", "valid.txt", "val.txt", "test.txt"):
            continue
        stem = original_stem(p.stem)
        if stem not in frames:
            unmatched.append(p.name)
            continue
        annotated[stem] = convert_label(p, cmap)
    if unmatched:
        print(f"WARNING: {len(unmatched)} label files don't match any extracted frame, e.g. {unmatched[:3]}")
    if not annotated:
        raise SystemExit("No label files matched extracted frames - check the export folder / file names.")

    def place(stem, lines):
        r = frames[stem]
        src = DATA_DIR / r["path"]
        img_dst = YOLO_DIR / "images" / r["split"] / src.name
        lbl_dst = YOLO_DIR / "labels" / r["split"] / f"{stem}.txt"
        img_dst.parent.mkdir(parents=True, exist_ok=True)
        lbl_dst.parent.mkdir(parents=True, exist_ok=True)
        if not img_dst.exists():
            shutil.copy2(src, img_dst)
        lbl_dst.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    for stem, lines in annotated.items():
        place(stem, lines)

    # Background (negative) frames teach the model what is NOT fire -> fewer false alarms.
    rng = random.Random(args.seed)
    summary = []
    for s in SPLITS:
        n_ann = sum(frames[k]["split"] == s for k in annotated)
        pool = sorted(k for k, r in frames.items()
                      if r["split"] == s and r["frame_label"] == "no_fire" and k not in annotated)
        n_bg = min(len(pool), round(n_ann * args.background_ratio))
        for stem in rng.sample(pool, n_bg):
            place(stem, [])
        summary.append((s, n_ann, n_bg))

    yaml = (f"# Generated by dataset_prep.annotations import\n"
            f"path: {YOLO_DIR.as_posix()}\n"
            f"train: images/train\nval: images/val\ntest: images/test\n\n"
            f"nc: {len(CLASSES)}\nnames:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(CLASSES)))
    (YOLO_DIR / "data.yaml").write_text(yaml, encoding="utf-8")

    print(f"\nBuilt YOLO dataset in {YOLO_DIR}")
    print(f"{'split':<6} {'annotated':>9} {'background':>10}")
    for s, a, b in summary:
        print(f"{s:<6} {a:>9} {b:>10}")
    print(f"Train with: yolo detect train data=\"{(YOLO_DIR / 'data.yaml').as_posix()}\" model=yolov8n.pt")
    cmd_validate(args)


# ---------------------------------------------------------------- validate
def cmd_validate(args):
    require(YOLO_DIR / "images", "run `python -m dataset_prep.annotations import` first")
    errors, total = [], Counter()
    print(f"\n{'split':<6} {'images':>7} {'bg':>5} " + " ".join(f"{c:>7}" for c in CLASSES))
    for s in SPLITS:
        img_dir, lbl_dir = YOLO_DIR / "images" / s, YOLO_DIR / "labels" / s
        imgs = [p for p in img_dir.glob("*") if p.suffix.lower() in IMG_EXTS] if img_dir.exists() else []
        cnt, bg = Counter(), 0
        for img in imgs:
            lbl = lbl_dir / f"{img.stem}.txt"
            if not lbl.exists():
                errors.append(f"missing label for {img}")
                continue
            lines = [l for l in lbl.read_text(encoding="utf-8").splitlines() if l.strip()]
            bg += not lines
            for n, l in enumerate(lines, 1):
                p = l.split()
                try:
                    c, vals = int(p[0]), list(map(float, p[1:]))
                    assert len(vals) == 4 and 0 <= c < len(CLASSES)
                    assert all(0 <= v <= 1 for v in vals) and vals[2] > 0 and vals[3] > 0
                    cnt[CLASSES[c]] += 1
                except (AssertionError, ValueError, IndexError):
                    errors.append(f"{lbl}:{n}: bad line {l!r}")
        if lbl_dir.exists():
            img_stems = {p.stem for p in imgs}
            errors += [f"label without image: {p}" for p in lbl_dir.glob("*.txt") if p.stem not in img_stems]
        total += cnt
        print(f"{s:<6} {len(imgs):>7} {bg:>5} " + " ".join(f"{cnt[c]:>7}" for c in CLASSES))
    if errors:
        print(f"\n{len(errors)} problems found:")
        for e in errors[:20]:
            print("  " + e)
        raise SystemExit(1)
    print("All labels valid.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="choose fire frames to annotate")
    e.add_argument("--per-video", type=int, default=20, help="max frames per positive video (0 = all)")
    e.add_argument("--splits", nargs="+", choices=SPLITS, default=list(SPLITS))
    i = sub.add_parser("import", help="build data/yolo from a CVAT/Roboflow YOLO export")
    i.add_argument("--labels", required=True, help="folder of the YOLO export (searched recursively)")
    i.add_argument("--background-ratio", type=float, default=0.3,
                   help="background frames added per annotated frame (per split)")
    i.add_argument("--seed", type=int, default=42)
    i.add_argument("--clean", action="store_true", help="delete data/yolo before building")
    sub.add_parser("validate", help="check data/yolo labels")
    args = ap.parse_args(argv)
    {"export": cmd_export, "import": cmd_import, "validate": cmd_validate}[args.cmd](args)


if __name__ == "__main__":
    main()
