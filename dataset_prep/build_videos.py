"""Merge the downloaded VIDEO datasets into one folder: data/raw/videos/

Each video is hard-linked (no extra disk space) as `<source>__<name>.<ext>`
next to an ONFIRE-style ground-truth file `<source>__<name>.<ext>.txt`:

    positive (fire and/or smoke) -> "0"    fire visible from the first frame
    negative (no fire)           -> ""     empty file

The public video sets only say WHETHER a video contains fire, not WHEN it
starts, so positives get start time 0 (fire present from the beginning).
Edit a .txt by hand if you know a later start time.

    python -m dataset_prep.build_videos
    python -m dataset_prep.organize          # then continue with the normal pipeline
"""
import argparse
import hashlib
import re

from .common import DATA_DIR, METADATA_DIR, VIDEO_EXTS, find_videos, link_or_copy, write_csv

RAW = DATA_DIR / "raw"
OUT = RAW / "videos"


def label_hf_fire_videos(rel):
    if "/non_fire/" in rel:
        return "negative", ""
    if "/fire/" in rel:
        return "positive", "fire"
    return None, ""


def label_visifire(rel):
    name = rel.lower()
    if name.startswith("fireclips/"):
        return "positive", "fire"
    if name.startswith("forestsmoke/"):
        return "positive", "smoke"
    if "carlights" in name or "car_counting" in name:  # car headlights / traffic: classic false alarms
        return "negative", ""
    if name.startswith("smokeclips/"):
        return "positive", "smoke"
    if "barbeq" in name:
        return "positive", "fire"
    return None, ""


def label_firesense(rel):
    """FIRESENSE folders: fire_videos/{pos,neg}/..., smoke_videos/{pos,neg}/... (names checked case-insensitively)."""
    parts = [p.lower() for p in rel.split("/")]
    kind = "smoke" if any("smoke" in p for p in parts) else "fire"
    if any(re.fullmatch(r"neg\w*|negative\w*|non.?fire\w*|no.?fire\w*", p) for p in parts[:-1]):
        return "negative", ""
    if any(re.fullmatch(r"pos\w*|positive\w*", p) for p in parts[:-1]):
        return "positive", kind
    stem = parts[-1]
    if re.match(r"(neg|nofire|non)", stem):
        return "negative", ""
    if re.match(r"(pos|fire|smoke|flame)", stem):
        return "positive", kind
    return None, ""


def label_kmu(rel):
    folder = rel.split("/")[0].lower()
    if folder.startswith("smoke_flame_like"):  # moving objects that look like smoke/flame
        return "negative", ""
    if "flame" in folder:
        return "positive", "fire"
    if "smoke" in folder:
        return "positive", "smoke"
    return None, ""


def label_ustc_vsd(rel):
    # smoke videos: Dry_leaf_smoke_02, Cotton_rope_smoke_04, Black_smoke_517; others are non-smoke scenes
    return ("positive", "smoke") if "smoke" in rel.lower() else ("negative", "")


# Bilkent's *_Output clips are their algorithm's results with detections drawn on the frames.
SKIP = re.compile(r"_output\.[a-z0-9]+$", re.I)


def file_hash(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


LABELERS = {"firesense": label_firesense, "kmu": label_kmu, "visifire": label_visifire,
            "ustc_vsd": label_ustc_vsd, "hf_fire_videos": label_hf_fire_videos}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", nargs="+", choices=list(LABELERS), default=list(LABELERS))
    ap.add_argument("--link-mode", choices=["hardlink", "copy"], default="hardlink")
    args = ap.parse_args(argv)

    rows, unknown, dupes = [], [], []
    seen = {}  # content hash -> filename (the same clip is shared between folders/datasets)
    for src in args.sources:
        root = RAW / src
        if not (root / "DONE").exists():
            print(f"--- {src}: not downloaded (run `python -m dataset_prep.download --only {src}`) - skipped")
            continue
        vids = [v for v in find_videos(root) if v.suffix.lower() in VIDEO_EXTS]
        for v in vids:
            rel = v.relative_to(root).as_posix()
            if SKIP.search(rel):
                continue
            label, kind = LABELERS[src](rel)
            if label is None:
                unknown.append(f"{src}/{rel}")
                continue
            digest = file_hash(v)
            if digest in seen:
                dupes.append(f"{src}/{rel} = {seen[digest]}")
                continue
            name = f"{src}__{re.sub(r'[^A-Za-z0-9._-]+', '_', v.stem)}{v.suffix.lower()}"
            if any(r["filename"] == name for r in rows):  # same name, different content
                name = f"{src}__{re.sub(r'[^A-Za-z0-9._-]+', '_', v.parent.name)}__{name.split('__', 1)[1]}"
            seen[digest] = name
            link_or_copy(v, OUT / name, args.link_mode)
            (OUT / f"{name}.txt").write_text("0" if label == "positive" else "", encoding="utf-8")
            rows.append({"filename": name, "source": src, "original": rel, "label": label, "kind": kind})
        pos = sum(r["source"] == src and r["label"] == "positive" for r in rows)
        neg = sum(r["source"] == src and r["label"] == "negative" for r in rows)
        print(f"--- {src}: {pos} positive, {neg} negative")

    write_csv(METADATA_DIR / "video_sources.csv", rows, ["filename", "source", "original", "label", "kind"])
    print(f"\n{len(rows)} videos in {OUT}  (positive {sum(r['label'] == 'positive' for r in rows)}, "
          f"negative {sum(r['label'] == 'negative' for r in rows)})")
    if dupes:
        print(f"Skipped {len(dupes)} duplicate videos (identical content):")
        for d in dupes:
            print("   " + d)
    if unknown:
        print(f"WARNING: {len(unknown)} videos skipped because their label could not be inferred:")
        for u in unknown[:20]:
            print("   " + u)
    print("Next: python -m dataset_prep.organize")


if __name__ == "__main__":
    main()
