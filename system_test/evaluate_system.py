"""Member 4 - complete-system test on unseen videos.

Runs the full pipeline (sampling -> YOLO -> confidence filter -> multi-frame
confirmation -> alert) on every video of a split and measures what matters
for continuous monitoring, using the ONFIRE 2023 rules:

  * an alert in a positive video at t >= max(0, fire_start - 5 s)  -> true positive (event detected)
  * any alert in a negative video, or earlier in a positive one    -> false alarm
  * a positive video without a valid alert                         -> missed event

Metrics: false alarms per hour, alert delay (s), missed events, video-level
precision / recall / F1, YOLO processing FPS and end-to-end real-time factor.

    python -m system_test.evaluate_system                    # test split, default settings
    python -m system_test.evaluate_system --min-hits 8 --conf 0.6
    python -m system_test.evaluate_system --sweep            # try several settings (on VAL videos)
Output: reports/system_evaluation.md + reports/system_eval_<split>.csv
"""
import argparse
import sys
import csv
import time
from pathlib import Path

from vision.config import Settings
from vision.confirm import SlidingWindowConfirmer
from vision.detector import Detector
from vision.pipeline import VideoProcessor

ROOT = Path(__file__).resolve().parent.parent
VIDEOS = ROOT / "data" / "videos"
REPORTS = ROOT / "reports"
TOLERANCE = 5.0  # ONFIRE: alerts up to 5 s before the annotated start are accepted


def evaluate(split, settings, detector, log=print):
    rows = []
    vids = sorted((VIDEOS / split / "videos").iterdir())
    for i, v in enumerate(vids, 1):
        gt_file = VIDEOS / split / "gt" / f"{v.name}.txt"
        gt_text = gt_file.read_text().strip() if gt_file.exists() else ""
        start = float(gt_text) if gt_text else None
        proc = VideoProcessor(str(v), settings, detector=detector, store=False, log=lambda *_: None)
        alerts, frames, analysed, duration = [], 0, 0, 0.0
        t0 = time.time()
        yolo_fps = 0.0
        for r in proc.run():
            frames += 1
            analysed += r.analysed
            duration = r.video_time
            yolo_fps = r.proc_fps
            alerts += [(a["video_time"], a["type"]) for a in r.alerts]
        wall = time.time() - t0
        if start is None:
            fa, tp_time = len(alerts), None
        else:
            valid = [t for t, _ in alerts if t >= max(0.0, start - TOLERANCE)]
            fa = len(alerts) - len([t for t, _ in alerts if t >= max(0.0, start - TOLERANCE)])
            tp_time = min(valid) if valid else None
        row = {"video": v.name, "label": "positive" if start is not None else "negative",
               "fire_start": start if start is not None else "", "duration_s": round(duration, 1),
               "alerts": len(alerts), "first_alert_s": round(alerts[0][0], 1) if alerts else "",
               "alert_types": " ".join(sorted({a for _, a in alerts})),
               "detected": "" if start is None else bool(tp_time is not None),
               "delay_s": round(max(0.0, tp_time - start), 1) if tp_time is not None else "",
               "false_alarms": fa, "frames": frames, "analysed": analysed,
               "yolo_fps": round(yolo_fps, 1), "realtime_factor": round(duration / wall, 2) if wall else 0}
        rows.append(row)
        log(f"  [{i}/{len(vids)}] {v.name:<55} {row['label']:<8} alerts={row['alerts']} "
            f"first={row['first_alert_s']} FA={fa} {'MISSED' if row['detected'] is False else ''}")
    return rows


def summarize(rows):
    pos = [r for r in rows if r["label"] == "positive"]
    neg = [r for r in rows if r["label"] == "negative"]
    tp = sum(r["detected"] is True for r in pos)
    missed = len(pos) - tp
    fa_total = sum(r["false_alarms"] for r in rows)
    fp_videos = sum(r["false_alarms"] > 0 for r in rows)
    neg_hours = sum(r["duration_s"] for r in neg) / 3600
    all_hours = sum(r["duration_s"] for r in rows) / 3600
    delays = [r["delay_s"] for r in pos if r["delay_s"] != ""]
    p = tp / (tp + fp_videos) if tp + fp_videos else 0.0
    rc = tp / len(pos) if pos else 0.0
    return {
        "videos": len(rows), "positive": len(pos), "negative": len(neg),
        "detected": tp, "missed": missed, "false_alarms": fa_total,
        "neg_videos_with_false_alarm": sum(r["false_alarms"] > 0 for r in neg),
        "false_alarms_per_hour": fa_total / all_hours if all_hours else 0.0,
        "false_alarms_per_hour_negative_video": sum(r["false_alarms"] for r in neg) / neg_hours if neg_hours else 0.0,
        "precision": p, "recall": rc, "f1": 2 * p * rc / (p + rc) if p + rc else 0.0,
        "mean_delay_s": sum(delays) / len(delays) if delays else 0.0,
        "median_delay_s": sorted(delays)[len(delays) // 2] if delays else 0.0,
        "yolo_fps": sum(r["yolo_fps"] for r in rows) / len(rows) if rows else 0.0,
        "realtime_factor": sum(r["duration_s"] for r in rows) / max(1e-9, sum(r["duration_s"] / max(r["realtime_factor"], 1e-9) for r in rows)),
        "hours": all_hours,
    }


def collect(split, settings, detector):
    """Run YOLO once per analysed frame (at a low threshold) and keep the best confidence per class.
    Confirmation can then be replayed for any threshold / window offline - same result as the live pipeline."""
    import cv2

    out = []
    low = Settings(**{**settings.__dict__, "conf_threshold": 0.25})
    detector.conf_threshold = 0.25
    for v in sorted((VIDEOS / split / "videos").iterdir()):
        gt = (VIDEOS / split / "gt" / f"{v.name}.txt").read_text().strip()
        cap = cv2.VideoCapture(str(v))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        fps = fps if 1 <= fps <= 120 else 25.0
        step = max(1, round(fps / low.sample_fps))
        frames, idx = [], -1
        while cap.grab():
            idx += 1
            if idx % step:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            kept, _ = detector.detect(frame)
            best = {}
            for d in kept:
                best[d.label] = max(best.get(d.label, 0.0), d.confidence)
            frames.append((idx / fps, best))
        cap.release()
        out.append({"video": v.name, "start": float(gt) if gt else None, "duration": idx / fps, "frames": frames})
    return out


def replay(collected, conf, window, min_hits, clear_hits=2):
    from types import SimpleNamespace

    rows = []
    for c in collected:
        conf_ = SlidingWindowConfirmer(window=window, min_hits=min_hits, clear_hits=min(clear_hits, min_hits - 1))
        alerts = []
        for t, best in c["frames"]:
            dets = [SimpleNamespace(label=l) for l, v in best.items() if v >= conf]
            started, _ = conf_.update(dets)
            alerts += [(t, l) for l in started]
        start = c["start"]
        if start is None:
            fa, tp_time = len(alerts), None
        else:
            valid = [t for t, _ in alerts if t >= max(0.0, start - TOLERANCE)]
            fa, tp_time = len(alerts) - len(valid), (min(valid) if valid else None)
        rows.append({"video": c["video"], "label": "positive" if start is not None else "negative",
                     "duration_s": c["duration"], "alerts": len(alerts), "false_alarms": fa,
                     "detected": "" if start is None else tp_time is not None,
                     "delay_s": round(max(0.0, tp_time - start), 1) if tp_time is not None else "",
                     "yolo_fps": 0, "realtime_factor": 1})
    return rows


def sweep(base, detector):
    """Choose confidence threshold + confirmation rule on the VALIDATION videos."""
    print("Collecting detections on VAL videos (one YOLO pass)...", flush=True)
    collected = collect("val", base, detector)
    lines = ["| conf | confirm | detected | missed | false alarms | FA/hour | mean delay s | F1 |",
             "|---|---|---|---|---|---|---|---|"]
    results = []
    for conf in (0.3, 0.4, 0.5, 0.6):
        for hits in (3, 5, 7, 9):
            m = summarize(replay(collected, conf, base.window, hits))
            results.append((conf, hits, m))
            lines.append(f"| {conf} | {hits}/{base.window} | {m['detected']}/{m['positive']} | {m['missed']} | "
                         f"{m['false_alarms']} | {m['false_alarms_per_hour']:.1f} | {m['mean_delay_s']:.1f} | {m['f1']:.3f} |")
    # fewest wrong decisions (missed fires + false alarms, each alert counted); ties -> fewer false alarms,
    # then shorter delay. (Video-level F1 hides repeated false alarms in one video, so it is not used here.)
    conf, hits, m = min(results, key=lambda r: (r[2]["missed"] + r[2]["false_alarms"], r[2]["false_alarms"],
                                                r[2]["mean_delay_s"]))
    text = (f"# Settings sweep (validation videos)\n\nModel `{Path(base.model_path).name}`, "
            f"{base.sample_fps:g} analysed frames/s, window {base.window} frames.\n\n" + "\n".join(lines) +
            f"\n\n**Selected: conf >= {conf}, confirm {hits}/{base.window}** "
            f"(fewest missed fires + false alarms on validation: {m['missed']} + {m['false_alarms']}; "
            f"ties broken by fewer false alarms, then shorter delay).\n")
    (REPORTS / "system_settings_sweep.md").write_text(text, encoding="utf-8")
    print(text)
    return conf, hits


def settings_text(s):
    return (f"conf >= {s.conf_threshold}, {s.sample_fps:g} analysed frames/s, confirm {s.min_hits}/{s.window} frames, "
            f"model `{Path(s.model_path).name}`")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):  # alerts contain emoji; Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--sample-fps", type=float, default=5.0)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--min-hits", type=int, default=7)
    ap.add_argument("--sweep", action="store_true", help="compare settings on the VAL split")
    ap.add_argument("--name", default="system_evaluation", help="report file name (reports/<name>.md)")
    a = ap.parse_args(argv)

    base = Settings(conf_threshold=a.conf, sample_fps=a.sample_fps, window=a.window, min_hits=a.min_hits,
                    save_incidents=False)
    if a.model:
        base.model_path = a.model
    detector = Detector(base.model_path, base.imgsz, base.conf_threshold)
    REPORTS.mkdir(exist_ok=True)

    if a.sweep:
        sweep(base, detector)
        return

    print(f"System test on {a.split} videos - {settings_text(base)}")
    rows = evaluate(a.split, base, detector)
    m = summarize(rows)
    with open(REPORTS / f"{a.name}_{a.split}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    L = ["# System evaluation (Member 4)", "",
         f"Full pipeline on the **{a.split}** videos. These were not used for training or for choosing the model. "
         f"Settings: {settings_text(base)}.", "",
         "| metric | value |", "|---|---|",
         f"| videos (fire/smoke / no fire) | {m['videos']} ({m['positive']} / {m['negative']}), {m['hours']:.2f} h |",
         f"| fire/smoke events detected | **{m['detected']} / {m['positive']}** |",
         f"| missed fire events | **{m['missed']}** |",
         f"| false alarms (total) | **{m['false_alarms']}** |",
         f"| no-fire videos with a false alarm | {m['neg_videos_with_false_alarm']} / {m['negative']} |",
         f"| false alarms per hour (all video) | **{m['false_alarms_per_hour']:.1f}** |",
         f"| false alarms per hour (no-fire video only) | {m['false_alarms_per_hour_negative_video']:.1f} |",
         f"| video-level precision / recall / F1 | {m['precision']:.3f} / {m['recall']:.3f} / {m['f1']:.3f} |",
         f"| alert delay, mean / median (s) | **{m['mean_delay_s']:.1f} / {m['median_delay_s']:.1f}** |",
         f"| YOLO processing speed | **{m['yolo_fps']:.1f} FPS** (CPU) |",
         f"| end-to-end speed | {m['realtime_factor']:.1f}x real time |", "",
         "Alert delay is measured from the annotated fire start. The public videos have no start times, so the start "
         "counts as 0 s, and the delay includes the time the fire takes to become visible.", "",
         f"Per-video results: `reports/{a.name}_{a.split}.csv`"]
    (REPORTS / f"{a.name}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n" + "\n".join(L))


if __name__ == "__main__":
    main()
