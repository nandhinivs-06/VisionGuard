"""Member 2 - Step 3: evaluate the models and hand the best one to the video system.

For every candidate (pretrained base, and the fine-tuned model if it exists):
  1. IMAGE level on the D-Fire test set (4,306 images with boxes):
     Precision, Recall, F1, mAP50, mAP50-95 per class + confusion matrix.
  2. FRAME level on our own CCTV videos (unseen during training):
     each frame is "fire/smoke" if the model finds anything >= conf, compared
     with the frame label from Member 1 -> Precision, Recall, F1, false-positive rate.
The winner is chosen on the VALIDATION videos (test videos stay untouched for
the final report), then copied to models/fire_smoke.pt and exported to
models/fire_smoke.onnx (~2x faster on CPU) for Member 3.

    python -m training.evaluate
Output: reports/model_evaluation.md, reports/confusion_matrix_<model>.png
"""
import argparse
import csv
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DFIRE = ROOT / "data" / "dfire_yolo"
FRAMES_CSV = ROOT / "data" / "metadata" / "frames.csv"
REPORTS = ROOT / "reports"
MODELS = ROOT / "models"


def f1(p, r):
    return 2 * p * r / (p + r) if p + r else 0.0


def image_metrics(pt_path, tag, imgsz):
    from ultralytics import YOLO

    m = YOLO(str(pt_path))
    res = m.val(data=str(DFIRE / "data.yaml"), split="test", imgsz=imgsz, batch=16, device="cpu",
                plots=True, project=str(ROOT / "runs"), name=f"eval_{tag}", exist_ok=True, verbose=False)
    box = res.box
    out = {"all": dict(P=box.mp, R=box.mr, F1=f1(box.mp, box.mr), mAP50=box.map50, mAP50_95=box.map)}
    for i, c in enumerate(res.ap_class_index):
        name = m.names[int(c)]
        p, r = float(box.p[i]), float(box.r[i])
        out[name] = dict(P=p, R=r, F1=f1(p, r), mAP50=float(box.ap50[i]), mAP50_95=float(box.ap[i]))
    cm = Path(res.save_dir) / "confusion_matrix.png"
    if cm.exists():
        REPORTS.mkdir(exist_ok=True)
        shutil.copy2(cm, REPORTS / f"confusion_matrix_{tag}.png")
    return out


def frame_metrics(model_path, split, conf, imgsz):
    """Frame-level fire/smoke vs no-fire on our CCTV videos."""
    from ultralytics import YOLO

    m = YOLO(str(model_path), task="detect")
    rows = [r for r in csv.DictReader(open(FRAMES_CSV, encoding="utf-8"))
            if r["split"] == split and r["frame_label"] in ("fire", "no_fire")]
    tp = fp = tn = fn = 0
    neg_videos_fp = set()
    for r in rows:
        res = m.predict(str(ROOT / "data" / r["path"]), imgsz=imgsz, conf=conf, verbose=False)[0]
        pred, gt = len(res.boxes) > 0, r["frame_label"] == "fire"
        tp += pred and gt
        fp += pred and not gt
        tn += (not pred) and (not gt)
        fn += (not pred) and gt
        if pred and not gt and r["video_label"] == "negative":
            neg_videos_fp.add(r["video_id"])
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return dict(frames=len(rows), TP=tp, FP=fp, TN=tn, FN=fn, P=p, R=rc, F1=f1(p, rc),
                FPR=fp / (fp + tn) if fp + tn else 0.0, acc=(tp + tn) / max(1, len(rows)),
                neg_videos_with_fp=len(neg_videos_fp))


def to_onnx(pt_path, imgsz):
    from ultralytics import YOLO

    return Path(YOLO(str(pt_path)).export(format="onnx", imgsz=imgsz, simplify=True, verbose=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conf", type=float, default=0.5, help="confidence threshold used by the video system")
    ap.add_argument("--imgsz", type=int, default=640)
    a = ap.parse_args(argv)

    candidates = {"base": MODELS / "base_dfire_yolov8n.pt"}
    ft = ROOT / "runs" / "finetune" / "weights" / "best.pt"
    if ft.exists():
        candidates["finetuned"] = ft
    results = {}
    for tag, pt in candidates.items():
        print(f"\n=== {tag}: {pt}")
        onnx = to_onnx(pt, a.imgsz)
        results[tag] = {"pt": pt, "onnx": onnx,
                        "image": image_metrics(pt, tag, a.imgsz),
                        "val_frames": frame_metrics(onnx, "val", a.conf, a.imgsz),
                        "test_frames": frame_metrics(onnx, "test", a.conf, a.imgsz)}
        print(tag, "image:", {k: round(v, 3) for k, v in results[tag]["image"]["all"].items()})
        print(tag, "val frames:", {k: round(v, 3) if isinstance(v, float) else v for k, v in results[tag]["val_frames"].items()})

    best = max(results, key=lambda t: (results[t]["val_frames"]["F1"], results[t]["image"]["all"]["mAP50"]))
    MODELS.mkdir(exist_ok=True)
    shutil.copy2(results[best]["pt"], MODELS / "fire_smoke.pt")
    shutil.copy2(results[best]["onnx"], MODELS / "fire_smoke.onnx")

    L = ["# Model evaluation (Member 2)", "",
         f"Selected model: **{best}**, chosen on F1 over the *validation* video frames. "
         "It is saved as `models/fire_smoke.pt` and `models/fire_smoke.onnx`.", "",
         "## 1. Image level: D-Fire test set (4,306 images, bounding boxes)", "",
         "| model | class | Precision | Recall | F1 | mAP50 | mAP50-95 |", "|---|---|---|---|---|---|---|"]
    for t, r in results.items():
        for cls, m in r["image"].items():
            L.append(f"| {t} | {cls} | {m['P']:.3f} | {m['R']:.3f} | {m['F1']:.3f} | {m['mAP50']:.3f} | {m['mAP50_95']:.3f} |")
    L += ["", "Confusion matrices: " + ", ".join(f"`reports/confusion_matrix_{t}.png`" for t in results), "",
          f"## 2. Frame level: our CCTV videos (conf >= {a.conf}, unseen during training)", "",
          "A frame counts as positive if any fire or smoke box is found.", "",
          "| model | split | frames | Precision | Recall | F1 | false-positive rate | accuracy | negative videos with a false positive |",
          "|---|---|---|---|---|---|---|---|---|"]
    for t, r in results.items():
        for split in ("val_frames", "test_frames"):
            m = r[split]
            L.append(f"| {t} | {split.split('_')[0]} | {m['frames']} | {m['P']:.3f} | {m['R']:.3f} | {m['F1']:.3f} | "
                     f"{m['FPR']:.3f} | {m['acc']:.3f} | {m['neg_videos_with_fp']} |")
    L += ["", "Video-level system metrics (false alarms per hour, alert delay, missed events, FPS) are in "
          "`reports/system_evaluation.md`, produced by `python -m system_test.evaluate_system`."]
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "model_evaluation.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
