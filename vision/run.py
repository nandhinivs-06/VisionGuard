"""Run fire/smoke detection on a video file, webcam or CCTV stream.

    python -m vision.run --source data/videos/test/videos/kmu__outdoor_daytime_10m_gasoline_CCD_001.avi --show
    python -m vision.run --source 0 --show                       # webcam
    python -m vision.run --source rtsp://user:pass@ip/stream     # CCTV camera
    python -m vision.run --source video.mp4 --output out.mp4     # save the annotated video
"""
import argparse
import sys

import cv2

from .config import Settings
from .pipeline import VideoProcessor


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):  # alerts contain emoji; Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="video path, webcam index or rtsp/http URL")
    ap.add_argument("--model", default=None)
    ap.add_argument("--camera", default="Camera 1")
    ap.add_argument("--conf", type=float, default=0.3, help="confidence threshold")
    ap.add_argument("--sample-fps", type=float, default=5.0, help="frames analysed per second")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--min-hits", type=int, default=7)
    ap.add_argument("--show", action="store_true", help="show the processed video (press q to quit)")
    ap.add_argument("--realtime", action="store_true", help="play files at normal speed")
    ap.add_argument("--output", default=None, help="save annotated video to this .mp4")
    ap.add_argument("--no-save", action="store_true", help="don't store incidents")
    ap.add_argument("--email", action="store_true", help="send e-mail alerts (SMTP_* env vars)")
    a = ap.parse_args(argv)

    s = Settings(camera_name=a.camera, conf_threshold=a.conf, sample_fps=a.sample_fps, window=a.window,
                 min_hits=a.min_hits, save_incidents=not a.no_save, notify_email=a.email)
    if a.model:
        s.model_path = a.model
    print(f"Model: {s.model_path}\nSource: {a.source}\n")
    proc = VideoProcessor(a.source, s)
    writer, n_alerts, last = None, 0, None
    for r in proc.run(realtime=a.realtime):
        if r.frame is None:
            continue
        last = r
        n_alerts += len(r.alerts)
        if a.output:
            if writer is None:
                h, w = r.frame.shape[:2]
                writer = cv2.VideoWriter(a.output, cv2.VideoWriter_fourcc(*"mp4v"), 25, (w, h))
            writer.write(r.frame)
        if a.show:
            cv2.imshow("VisionGuard", r.frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                proc.stop()
    if writer:
        writer.release()
    cv2.destroyAllWindows()
    if last:
        print(f"Done: {last.index + 1} frames, {n_alerts} alerts, events {last.events}, "
              f"processing {last.proc_fps:.1f} analysed frames/s")


if __name__ == "__main__":
    main()
