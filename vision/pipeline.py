"""Video processing & alert pipeline (Member 3).

CCTV / video -> OpenCV -> frame sampling -> YOLO -> confidence filtering ->
multi-frame confirmation -> confirmed event -> alert + incident storage.

    from vision.pipeline import VideoProcessor
    for r in VideoProcessor("video.mp4").run():
        ...  # r.frame (annotated), r.status, r.alerts, r.detections, ...
"""
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2

from .config import Settings
from .confirm import SlidingWindowConfirmer
from .detector import Detector, draw
from .incidents import ClipWriter, IncidentStore, severity_for
from .notifier import format_alert, send_email

STATUS_COLORS = {"NORMAL": (0, 160, 0), "SMOKE DETECTED": (0, 140, 255), "FIRE DETECTED": (0, 0, 255)}


@dataclass
class FrameResult:
    frame: object                      # annotated BGR frame (boxes + status bar)
    index: int                         # frame number in the source
    video_time: float                  # seconds since start of the source
    analysed: bool                     # True if YOLO ran on this frame (frame sampling)
    detections: list                   # filtered detections currently shown
    rejected: int                      # weak detections dropped by the confidence filter on this frame
    status: str                        # NORMAL / SMOKE DETECTED / FIRE DETECTED
    active: list                       # labels currently confirmed
    alerts: list = field(default_factory=list)   # alerts raised on this frame
    hits: dict = field(default_factory=dict)     # {"fire": 6, "smoke": 0} hits in the sliding window
    events: dict = field(default_factory=dict)   # confirmed events so far {"fire": 1, "smoke": 2}
    proc_fps: float = 0.0              # YOLO frames analysed per second (processing speed)
    online: bool = True


def open_source(source):
    """Video file path, webcam index ('0', 0) or stream URL (rtsp://, http://)."""
    src = int(source) if str(source).isdigit() else str(source)
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video source: {source}")
    live = isinstance(src, int) or "://" in str(src)
    return cap, live


class VideoProcessor:
    def __init__(self, source, settings=None, detector=None, store=None, log=print):
        self.source = source
        self.s = settings or Settings()
        self.detector = detector or Detector(self.s.model_path, self.s.imgsz, self.s.conf_threshold)
        self.detector.conf_threshold = self.s.conf_threshold
        self.store = store if store is not None else (
            IncidentStore(self.s.db_path, self.s.incidents_dir / "media") if self.s.save_incidents else None)
        self.confirmer = SlidingWindowConfirmer(window=self.s.window, min_hits=self.s.min_hits,
                                                clear_hits=self.s.clear_hits)
        self.log = log
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self, realtime=False):
        """Generator over FrameResult for every frame of the source.
        realtime=True paces video files at their native speed (for live viewing)."""
        cap, live = open_source(self.source)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        fps = fps if 1 <= fps <= 120 else 25.0
        step = max(1, round(fps / self.s.sample_fps)) if self.s.sample_fps > 0 else 1
        before = deque(maxlen=max(1, int(self.s.clip_before_sec * fps)))
        window_conf = {l: deque(maxlen=self.s.window) for l in self.confirmer.history}
        incident_ids, clips, events = {}, [], {l: 0 for l in self.confirmer.history}
        shown, rejected = [], 0
        idx, t_start, last_live_analysis, proc_times = -1, time.time(), 0.0, deque(maxlen=20)
        name = Path(str(self.source)).name if not live else str(self.source)
        try:
            while not self._stop:
                ok, frame = cap.read()
                if not ok:
                    if live:  # camera dropped: report offline and retry
                        yield FrameResult(None, idx, time.time() - t_start, False, [], 0, "OFFLINE", [], online=False)
                        time.sleep(1.0)
                        cap.release()
                        cap, _ = open_source(self.source)
                        continue
                    break
                idx += 1
                vt = (time.time() - t_start) if live else idx / fps
                if live:
                    analyse = time.time() - last_live_analysis >= 1.0 / max(self.s.sample_fps, 0.1)
                else:
                    analyse = idx % step == 0

                alerts = []
                if analyse:
                    last_live_analysis = time.time()
                    t0 = time.perf_counter()
                    shown, weak = self.detector.detect(frame)
                    proc_times.append(time.perf_counter() - t0)
                    rejected = len(weak)
                    for l in window_conf:
                        window_conf[l].append(max((d.confidence for d in shown if d.label == l), default=0.0))
                    started, ended = self.confirmer.update(shown)
                else:
                    started, ended = [], []

                active = [l for l, a in self.confirmer.active.items() if a]
                status = "FIRE DETECTED" if "fire" in active else "SMOKE DETECTED" if "smoke" in active else "NORMAL"
                annotated = self._overlay(draw(frame, shown), status, vt, live)

                for label in started:
                    events[label] += 1
                    conf = max(window_conf[label])
                    alert = {"type": label, "camera": self.s.camera_name, "confidence": conf,
                             "time": datetime.now().strftime("%H:%M:%S"), "video_time": round(vt, 1),
                             "severity": severity_for(label), "source": name}
                    if self.store:
                        iid, clip_path = self.store.open_incident(self.s.camera_name, name, label, conf, vt, annotated)
                        incident_ids[label] = iid
                        alert["incident_id"] = iid
                        clip = ClipWriter(clip_path, list(before), fps if not live else self.s.sample_fps * 2,
                                          self.s.clip_after_sec)
                        self.store.set_clip_path(iid, clip.path)
                        clips.append(clip)
                        if self.s.notify_email:
                            try:
                                snap = self.store.all(1)[0]["snapshot_path"] if self.store else None
                            except Exception:
                                snap = None
                            alert["email"] = send_email(alert, snap)
                    elif self.s.notify_email:
                        alert["email"] = send_email(alert)
                    self.log(format_alert(alert) + f"\nVideo time: {vt:.1f}s\n")
                    alerts.append(alert)
                for label in active:
                    if self.store and label in incident_ids and analyse and window_conf[label]:
                        self.store.update_confidence(incident_ids[label], window_conf[label][-1])
                for label in ended:
                    if label in incident_ids:
                        self.store.close_incident(incident_ids.pop(label))

                before.append(annotated)
                clips = [c for c in clips if c.write(annotated)]
                yield FrameResult(annotated, idx, vt, analyse, shown, rejected, status, active, alerts,
                                  {l: self.confirmer.hits(l) for l in window_conf}, dict(events),
                                  len(proc_times) / sum(proc_times) if proc_times else 0.0)
                if realtime and not live:
                    delay = t_start + vt - time.time()
                    if delay > 0:
                        time.sleep(delay)
        finally:
            cap.release()
            for c in clips:
                c.close()
            for iid in incident_ids.values():
                self.store.close_incident(iid)

    def _overlay(self, frame, status, vt, live):
        h, w = frame.shape[:2]
        color = STATUS_COLORS.get(status, (0, 160, 0))
        clock = datetime.now().strftime("%H:%M:%S") if live else f"t={vt:.1f}s"
        text = f"{self.s.camera_name} | {status} | {clock}"
        scale = 0.55
        tw = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0]
        if tw > w - 12:  # shrink to fit small frames (e.g. 320x240 CCTV)
            scale *= (w - 12) / tw
        bar = max(16, int(26 * scale / 0.55))
        cv2.rectangle(frame, (0, 0), (w, bar), color, -1)
        cv2.putText(frame, text, (6, int(bar * 0.7)), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
        return frame
