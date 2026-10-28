"""VisionGuard web dashboard - backend (Member 4).

FastAPI server around the Member 3 pipeline (vision/). It serves the HTML/CSS/JS
front end in webapp/static and:

    GET  /api/stream      live annotated video (MJPEG, use as <img src>)
    GET  /api/events      live status + alerts (Server-Sent Events)
    GET  /api/sources     dataset videos + models
    POST /api/upload      upload a video (raw body, ?name=file.mp4)
    POST /api/start       start monitoring {source, camera, model, conf, sample_fps, window, min_hits, realtime}
    POST /api/stop        stop monitoring
    GET  /api/incidents   incident history       DELETE /api/incidents  clear it
    GET  /api/analytics   model + system evaluation results (from reports/)
    GET  /media/...       incident snapshots and clips

    python -m webapp.server            # http://localhost:8080
"""
try:  # Windows: onnxruntime must load its DLLs before torch/cv2
    import onnxruntime  # noqa: F401
except ImportError:
    pass

import asyncio
import csv
import json
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision.config import MODELS, Settings  # noqa: E402
from vision.detector import Detector  # noqa: E402
from vision.incidents import IncidentStore  # noqa: E402
from vision.notifier import get_email_config, save_email_config, send_test_email  # noqa: E402
from vision.pipeline import VideoProcessor  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
UPLOADS = ROOT / "incidents" / "uploads"
DEFAULTS = Settings()
STORE = IncidentStore(DEFAULTS.db_path, DEFAULTS.incidents_dir / "media")
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".mpg", ".webm"}


class Monitor:
    """Runs one VideoProcessor in a background thread and keeps the latest frame/status for the web clients."""

    def __init__(self):
        self.lock = threading.Lock()
        self.new_frame = threading.Condition(self.lock)
        self.thread = None
        self.proc = None
        self.detectors = {}
        self.jpeg = None
        self.frame_id = 0
        self.alerts = deque(maxlen=50)
        self.trace = deque(maxlen=150)      # recent (t, fire_conf, smoke_conf) for the live chart
        self.status = self._idle()

    @staticmethod
    def _idle(**extra):
        return {"running": False, "camera": "", "source": "", "online": False, "status": "IDLE",
                "fire": 0, "smoke": 0, "active": [], "confidence": 0.0, "fps": 0.0, "hits": {"fire": 0, "smoke": 0},
                "window": DEFAULTS.window, "min_hits": DEFAULTS.min_hits, "video_time": 0.0, "progress": None,
                "events": {"fire": 0, "smoke": 0}, "rejected": 0, "error": None, "duration": None, "live": False,
                **extra}

    def detector(self, model_path):
        if model_path not in self.detectors:
            self.detectors[model_path] = Detector(model_path, DEFAULTS.imgsz, DEFAULTS.conf_threshold)
        return self.detectors[model_path]

    def start(self, cfg):
        self.stop()
        email_enabled = bool(cfg.get("email", False))
        s = Settings(model_path=cfg.get("model") or DEFAULTS.model_path,
                     conf_threshold=float(cfg.get("conf", DEFAULTS.conf_threshold)),
                     sample_fps=float(cfg.get("sample_fps", DEFAULTS.sample_fps)),
                     window=int(cfg.get("window", DEFAULTS.window)),
                     camera_name=cfg.get("camera") or "Camera 1",
                     notify_email=email_enabled)
        s.min_hits = max(1, min(int(cfg.get("min_hits", DEFAULTS.min_hits)), s.window))
        s.clear_hits = min(DEFAULTS.clear_hits, s.min_hits - 1)
        source = str(cfg["source"])
        cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
        if not cap.isOpened():
            raise HTTPException(400, f"Cannot open video source: {source}")
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.release()
        live = source.isdigit() or "://" in source
        det = self.detector(s.model_path)
        det.conf_threshold = s.conf_threshold
        self.proc = VideoProcessor(source, s, detector=det, store=STORE, log=lambda *_: None)
        with self.lock:
            self.alerts.clear()
            self.trace.clear()
            self.jpeg = None
            self.status = self._idle(running=True, camera=s.camera_name, source=Path(source).name if not live else source,
                                     status="STARTING", window=s.window, min_hits=s.min_hits, live=live,
                                     duration=round(total / fps, 1) if total and not live and 1 <= fps <= 120 else None)
        self.thread = threading.Thread(target=self._run, args=(self.proc, bool(cfg.get("realtime", True)), total, live),
                                       daemon=True)
        self.thread.start()

    def _run(self, proc, realtime, total, live):
        try:
            for r in proc.run(realtime=realtime):
                fire_c = max((d.confidence for d in r.detections if d.label == "fire"), default=0.0)
                smoke_c = max((d.confidence for d in r.detections if d.label == "smoke"), default=0.0)
                jpeg = None
                if r.frame is not None:
                    ok, buf = cv2.imencode(".jpg", r.frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    jpeg = buf.tobytes() if ok else None
                with self.lock:
                    if jpeg:
                        self.jpeg = jpeg
                        self.frame_id += 1
                    if r.analysed:
                        self.trace.append((round(r.video_time, 2), round(fire_c, 3), round(smoke_c, 3)))
                    for a in r.alerts:
                        self.alerts.appendleft({**a, "confidence": round(a["confidence"], 3),
                                                "snapshot": media_url(_snapshot_of(a.get("incident_id")))})
                    self.status.update({
                        "online": r.online, "status": r.status, "active": r.active,
                        "fire": sum(d.label == "fire" for d in r.detections),
                        "smoke": sum(d.label == "smoke" for d in r.detections),
                        "confidence": round(max(fire_c, smoke_c), 3), "fps": round(r.proc_fps, 1),
                        "hits": r.hits, "events": r.events, "rejected": r.rejected,
                        "video_time": round(r.video_time, 1),
                        "progress": round((r.index + 1) / total, 4) if total and not live else None})
                    self.new_frame.notify_all()
        except Exception as e:  # report to the UI instead of dying silently
            with self.lock:
                self.status["error"] = str(e)
        finally:
            with self.lock:
                self.status.update(running=False, status="STOPPED" if self.status.get("status") != "IDLE" else "IDLE",
                                   online=False)
                self.new_frame.notify_all()

    def stop(self):
        if self.proc:
            self.proc.stop()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        self.proc = self.thread = None

    def snapshot(self):
        with self.lock:
            return {**self.status, "alerts": list(self.alerts), "trace": list(self.trace)}


def _snapshot_of(incident_id):
    if not incident_id:
        return None
    for r in STORE.all(20):
        if r["id"] == incident_id:
            return r["snapshot_path"]
    return None


def media_url(path):
    if not path:
        return None
    p = Path(path)
    try:
        return "/media/" + p.relative_to(DEFAULTS.incidents_dir).as_posix()
    except ValueError:
        return None


MON = Monitor()
app = FastAPI(title="VisionGuard")
DEFAULTS.incidents_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(DEFAULTS.incidents_dir)), name="media")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
if (ROOT / "reports").exists():
    app.mount("/reports", StaticFiles(directory=str(ROOT / "reports")), name="reports")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/sources")
def sources():
    vids = []
    for split in ("test", "val", "train"):
        for v in sorted((ROOT / "data" / "videos" / split / "videos").glob("*")):
            gt = ROOT / "data" / "videos" / split / "gt" / f"{v.name}.txt"
            label = "fire/smoke" if gt.exists() and gt.read_text().strip() else "no fire"
            vids.append({"path": str(v), "name": v.name, "split": split, "label": label,
                         "dataset": v.name.split("__")[0] if "__" in v.name else ""})
    for v in sorted(UPLOADS.glob("*")) if UPLOADS.exists() else []:
        vids.append({"path": str(v), "name": v.name, "split": "uploaded", "label": "", "dataset": "upload"})
    models = sorted((p for p in MODELS.glob("*") if p.suffix in (".onnx", ".pt")),
                    key=lambda p: (not p.name.startswith("fire_smoke"), p.suffix != ".onnx", p.name))
    return {"videos": vids, "models": [{"path": str(m), "name": m.name} for m in models],
            "defaults": {"model": DEFAULTS.model_path, "conf": DEFAULTS.conf_threshold, "sample_fps": DEFAULTS.sample_fps,
                         "window": DEFAULTS.window, "min_hits": DEFAULTS.min_hits}}


@app.post("/api/upload")
async def upload(request: Request, name: str):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)
    if Path(safe).suffix.lower() not in VIDEO_EXTS:
        raise HTTPException(400, f"Unsupported file type: {safe}")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dst = UPLOADS / safe
    with open(dst, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    return {"path": str(dst), "name": safe}


@app.post("/api/start")
async def start(request: Request):
    cfg = await request.json()
    if not cfg.get("source") and cfg.get("source") != 0:
        raise HTTPException(400, "No video source given")
    await asyncio.to_thread(MON.start, cfg)
    return {"ok": True}


@app.post("/api/stop")
async def stop():
    await asyncio.to_thread(MON.stop)
    return {"ok": True}


@app.get("/api/email")
def get_email():
    cfg = get_email_config()
    return {
        "host": cfg.get("host", ""),
        "port": cfg.get("port", 587),
        "user": cfg.get("user", ""),
        "to": cfg.get("to", ""),
        "enabled": cfg.get("enabled", False),
        "has_password": bool(cfg.get("password")),
    }


@app.post("/api/email")
async def update_email(request: Request):
    data = await request.json()
    saved = save_email_config(data)
    return {
        "ok": True,
        "config": {
            "host": saved.get("host", ""),
            "port": saved.get("port", 587),
            "user": saved.get("user", ""),
            "to": saved.get("to", ""),
            "enabled": saved.get("enabled", False),
            "has_password": bool(saved.get("password")),
        }
    }


@app.post("/api/email/test")
async def test_email(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    ok, msg = await asyncio.to_thread(send_test_email, data if data else None)
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@app.get("/api/status")
def status():
    return MON.snapshot()


@app.get("/api/events")
async def events(request: Request):
    async def gen():
        while not await request.is_disconnected():
            yield f"data: {json.dumps(MON.snapshot())}\n\n"
            await asyncio.sleep(0.25)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/stream")
async def stream(request: Request):
    def wait_frame(last):
        with MON.new_frame:
            MON.new_frame.wait_for(lambda: MON.frame_id != last, timeout=1.0)
            return MON.frame_id, MON.jpeg

    async def gen():
        last = -1
        while not await request.is_disconnected():
            fid, jpeg = await asyncio.to_thread(wait_frame, last)
            if jpeg is None or fid == last:
                continue
            last = fid
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
            await asyncio.sleep(0.03)  # cap at ~30 fps to the browser
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/incidents")
def incidents():
    rows = STORE.all()
    for r in rows:
        r["snapshot_url"] = media_url(r["snapshot_path"])
        r["clip_url"] = media_url(r["clip_path"]) if r["clip_path"] and Path(r["clip_path"]).exists() else None
    return rows


@app.delete("/api/incidents")
def clear_incidents():
    STORE.clear()
    return {"ok": True}


@app.patch("/api/incidents/{incident_id}")
async def review_incident(incident_id: int, request: Request):
    body = await request.json()
    try:
        r = STORE.review(incident_id, body.get("review"), body.get("note"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not r:
        raise HTTPException(404, "incident not found")
    return r


@app.delete("/api/incidents/{incident_id}")
def delete_incident(incident_id: int):
    if not STORE.delete(incident_id):
        raise HTTPException(404, "incident not found")
    return {"ok": True}


@app.get("/api/incidents.csv")
def incidents_csv():
    import io

    cols = ["id", "started_at", "ended_at", "camera", "source", "type", "severity", "confidence", "status",
            "review", "note", "video_time_sec", "snapshot_path", "clip_path"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(STORE.all(100000))
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=visionguard_incidents.csv"})


@app.get("/api/stats")
def stats():
    """Live statistics from the incident database (for the analytics charts)."""
    from collections import Counter
    from datetime import date, timedelta

    rows = STORE.all(100000)
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    by_hour = Counter(int(r["started_at"][11:13]) for r in rows if r["started_at"] and len(r["started_at"]) >= 13)
    reviews = Counter(r.get("review") or "PENDING" for r in rows)
    confirmed = reviews.get("CONFIRMED", 0) + reviews.get("CONFIRMED_FIRE", 0) + reviews.get("CONFIRMED_SMOKE", 0)
    reviewed = confirmed + reviews.get("FALSE_ALARM", 0)
    return {
        "total": len(rows),
        "by_type": dict(Counter(r["type"] for r in rows)),
        "by_review": {k: reviews.get(k, 0) for k in ("PENDING", "CONFIRMED", "CONFIRMED_FIRE", "CONFIRMED_SMOKE", "FALSE_ALARM")},
        "false_alarm_rate": round(reviews["FALSE_ALARM"] / reviewed, 3) if reviewed else None,
        "by_day": [{"day": d, "fire": sum(1 for r in rows if r["started_at"][:10] == d and r["type"] == "fire"),
                    "smoke": sum(1 for r in rows if r["started_at"][:10] == d and r["type"] == "smoke")} for d in days],
        "by_hour": [by_hour.get(h, 0) for h in range(24)],
        "by_camera": dict(Counter(r["camera"] for r in rows).most_common(8)),
        "avg_confidence": round(sum(r["confidence"] or 0 for r in rows) / len(rows), 3) if rows else None,
    }


def _md_table(text, header_start):
    """Parse the first markdown table whose header starts with `header_start`."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("| " + header_start):
            head = [c.strip() for c in l.strip("|").split("|")]
            rows = []
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                rows.append(dict(zip(head, [c.strip().replace("**", "") for c in row.strip("|").split("|")])))
            return rows
    return []


@app.get("/api/analytics")
def analytics():
    rep = ROOT / "reports"
    out = {"system": [], "videos": [], "model_images": [], "model_frames": [], "sweep": [], "selected": None,
           "settings": None, "confusion": [f"/reports/{p.name}" for p in sorted(rep.glob("confusion_matrix_*.png"))]}
    if (rep / "system_evaluation.md").exists():
        t = (rep / "system_evaluation.md").read_text(encoding="utf-8")
        out["system"] = _md_table(t, "metric")
        m = re.search(r"Settings: (.*?)\.\s*$", t, re.M)
        out["settings"] = m.group(1).replace("`", "") if m else None
    if (rep / "system_evaluation_test.csv").exists():
        out["videos"] = list(csv.DictReader(open(rep / "system_evaluation_test.csv", encoding="utf-8")))
    if (rep / "model_evaluation.md").exists():
        t = (rep / "model_evaluation.md").read_text(encoding="utf-8")
        out["model_images"] = _md_table(t, "model | class")
        out["model_frames"] = _md_table(t, "model | split")
        m = re.search(r"Selected model: \*\*(\w+)\*\*", t)
        out["selected"] = m.group(1) if m else None
    if (rep / "system_settings_sweep.md").exists():
        out["sweep"] = _md_table((rep / "system_settings_sweep.md").read_text(encoding="utf-8"), "conf")
    return JSONResponse(out)


def main():
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="VisionGuard web dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    print(f"VisionGuard dashboard: http://localhost:{a.port}")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
