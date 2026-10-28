"""Member 2 - Step 2: fine-tune the fire/smoke YOLO model (CPU friendly).

Starts from a YOLOv8n already trained on D-Fire (rabahdev/fire-smoke-yolov8n,
test mAP50 0.754) and fine-tunes it on:
  * D-Fire train/val images (fire + smoke boxes), and
  * HARD NEGATIVES: 'no_fire' frames from our own train/val CCTV videos (Member 1),
    added as background images - this targets false alarms on car lights,
    reflections, fog, moving objects that look like smoke/flame.
Test videos are never used here.

    python -m training.train                       # default: CPU-sized run (~1 h)
    python -m training.train --epochs 20 --n-train 0 --freeze 0   # full D-Fire, all layers (GPU)
Output: runs/finetune/weights/best.pt  (then run python -m training.evaluate)
"""
import argparse
import csv
import shutil
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DFIRE = ROOT / "data" / "dfire_yolo"
FRAMES_CSV = ROOT / "data" / "metadata" / "frames.csv"
BASE = ROOT / "models" / "base_dfire_yolov8n.pt"
BASE_URL = "https://huggingface.co/rabahdev/fire-smoke-yolov8n/resolve/main/best.pt"


def ensure_base():
    if not BASE.exists():
        BASE.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading base model -> {BASE}")
        urllib.request.urlretrieve(BASE_URL, BASE)
    return BASE


def add_hard_negatives(max_per_video=40):
    """Copy no_fire frames of the train/val VIDEOS into the YOLO set as background images (empty labels)."""
    if not FRAMES_CSV.exists():
        print("no frames.csv - skipping hard negatives (run dataset_prep first)")
        return {}
    rows = [r for r in csv.DictReader(open(FRAMES_CSV, encoding="utf-8"))
            if r["split"] in ("train", "val") and r["frame_label"] == "no_fire"]
    per_video, added = {}, {"train": 0, "val": 0}
    for r in rows:
        per_video.setdefault(r["video_id"], []).append(r)
    for vid, vr in per_video.items():
        vr = vr[:: max(1, len(vr) // max_per_video)][:max_per_video]
        for r in vr:
            src = ROOT / "data" / r["path"]
            name = f"neg__{Path(r['path']).stem}"
            img = DFIRE / "images" / r["split"] / f"{name}.jpg"
            if not img.exists():
                shutil.copy2(src, img)
                (DFIRE / "labels" / r["split"] / f"{name}.txt").write_text("", encoding="utf-8")
            added[r["split"]] += 1
    print(f"hard negatives from our videos: {added}")
    return added


def write_lists(n_train, n_val, n_neg, seed=42):
    """Random (seeded) image lists so a CPU run sees a representative sample - not the first N files by name."""
    import random

    rng = random.Random(seed)
    lists = {}
    for split, n, nneg in (("train", n_train, n_neg), ("val", n_val, n_neg // 2)):
        imgs = sorted((DFIRE / "images" / split).glob("*.jpg"))
        dfire = [p for p in imgs if not p.name.startswith("neg__")]
        neg = [p for p in imgs if p.name.startswith("neg__")]
        pick = (rng.sample(dfire, min(n, len(dfire))) if n else dfire) + rng.sample(neg, min(nneg, len(neg)))
        rng.shuffle(pick)
        out = ROOT / "runs" / "finetune_data" / f"{split}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(p.as_posix() for p in pick) + "\n", encoding="utf-8")
        lists[split] = out
        print(f"{split}: {len(pick) - min(nneg, len(neg))} D-Fire + {min(nneg, len(neg))} hard-negative images")
    yaml = ROOT / "runs" / "finetune_data" / "data.yaml"
    yaml.write_text(f"train: {lists['train'].as_posix()}\nval: {lists['val'].as_posix()}\n"
                    f"test: {(DFIRE / 'images' / 'test').as_posix()}\nnc: 2\nnames:\n  0: smoke\n  1: fire\n",
                    encoding="utf-8")
    return yaml


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--n-train", type=int, default=2000, help="random D-Fire train images (0 = all)")
    ap.add_argument("--n-val", type=int, default=600, help="random D-Fire val images (0 = all)")
    ap.add_argument("--n-neg", type=int, default=400, help="hard-negative frames from our videos")
    ap.add_argument("--freeze", type=int, default=10, help="freeze the first N layers (backbone) - much faster on CPU")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr0", type=float, default=0.002, help="low LR: we fine-tune an already trained model")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-hard-negatives", action="store_true")
    a = ap.parse_args(argv)

    if not (DFIRE / "data.yaml").exists():
        raise SystemExit("D-Fire not prepared - run: python -m training.prepare_dfire")
    if not a.no_hard_negatives:
        add_hard_negatives()
    for cache in DFIRE.glob("labels/*.cache"):  # image list changed -> rebuild label cache
        cache.unlink()

    from ultralytics import YOLO

    model = YOLO(str(ensure_base()))
    data = write_lists(a.n_train, a.n_val, 0 if a.no_hard_negatives else a.n_neg)
    model.train(data=str(data), epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, freeze=a.freeze, device="cpu", workers=a.workers, optimizer="SGD", lr0=a.lr0,
                warmup_epochs=0, close_mosaic=1, patience=a.epochs, project=str(ROOT / "runs"),
                name="finetune", exist_ok=True, plots=True, verbose=False)
    print(f"\nbest weights: {ROOT / 'runs' / 'finetune' / 'weights' / 'best.pt'}")
    print("next: python -m training.evaluate")


if __name__ == "__main__":
    main()
