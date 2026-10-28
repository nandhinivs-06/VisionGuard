"""Member 2 - Step 1: get the D-Fire image dataset (fire/smoke bounding boxes) in YOLO format.

Our CCTV videos (Member 1) say *whether* there is fire, not *where*, so the
detector is trained on D-Fire: 21,527 CCTV-style images with YOLO boxes.
Class ids are kept as in D-Fire and in the base model: 0 = smoke, 1 = fire.
All later code uses class *names*, never ids.

    python -m training.prepare_dfire

Output: data/dfire_yolo/{images,labels}/{train,val,test} + data/dfire_yolo/data.yaml
(train = D-Fire train minus 10% for val, test = D-Fire test, 4,306 images)
"""
import argparse
import hashlib
from pathlib import Path

from dataset_prep.common import DATA_DIR
from dataset_prep.download import download, hf_files

RAW = DATA_DIR / "raw_images" / "dfire"
OUT = DATA_DIR / "dfire_yolo"
NAMES = {0: "smoke", 1: "fire"}


def split_for(name, source_split):
    if source_split == "test":
        return "test"
    h = int(hashlib.md5(name.encode()).hexdigest()[:8], 16)
    return "val" if h % 10 == 0 else "train"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--segments", type=int, default=4)
    args = ap.parse_args(argv)

    files = hf_files("badsaarow/d-fire", r"^data/.*\.parquet$")
    for i, (url, rel, size) in enumerate(files, 1):
        dst = RAW / rel
        if dst.exists() and (not size or dst.stat().st_size == size):
            continue
        if dst.exists():
            dst.replace(dst.with_name(dst.name + ".part"))
        print(f"[{i}/{len(files)}] downloading {rel} ({(size or 0) / 2**20:.0f} MB)", flush=True)
        download(url, dst, size, args.segments)

    import pyarrow.parquet as pq

    counts = {}
    for f in sorted((RAW / "data").glob("*.parquet")):
        source_split = "test" if f.name.startswith("test") else "train"
        for batch in pq.ParquetFile(f).iter_batches(batch_size=256, columns=["image", "label", "filename"]):
            for r in batch.to_pylist():
                name = Path(r["filename"] or r["image"]["path"]).stem
                split = split_for(name, source_split)
                img = OUT / "images" / split / f"{name}.jpg"
                lbl = OUT / "labels" / split / f"{name}.txt"
                img.parent.mkdir(parents=True, exist_ok=True)
                lbl.parent.mkdir(parents=True, exist_ok=True)
                if not img.exists():
                    img.write_bytes(r["image"]["bytes"])
                lines = [l for l in (r["label"] or "").splitlines() if len(l.split()) == 5]
                lbl.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
                counts[split] = counts.get(split, 0) + 1
        print(f"converted {f.name}: {counts}", flush=True)

    (OUT / "data.yaml").write_text(
        f"path: {OUT.as_posix()}\ntrain: images/train\nval: images/val\ntest: images/test\n"
        f"nc: 2\nnames:\n  0: smoke\n  1: fire\n", encoding="utf-8")
    print(f"\nD-Fire ready in {OUT}: {counts}")


if __name__ == "__main__":
    main()
