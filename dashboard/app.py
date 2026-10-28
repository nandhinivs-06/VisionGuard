"""VisionGuard dashboard (Member 4).

    streamlit run dashboard/app.py
"""
try:  # Windows: onnxruntime must be imported before torch/pandas/cv2, or its DLLs fail to load
    import onnxruntime  # noqa: F401
except ImportError:
    pass
import sys
import tempfile
import time
from pathlib import Path

import cv2
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision.config import MODELS, Settings  # noqa: E402
from vision.detector import Detector  # noqa: E402
from vision.incidents import IncidentStore  # noqa: E402
from vision.pipeline import VideoProcessor  # noqa: E402

st.set_page_config(page_title="VisionGuard", page_icon="🔥", layout="wide")
st.markdown("""
<style>
.alert-box {padding: 14px 18px; border-radius: 8px; font-size: 1.35rem; font-weight: 700; color: white; margin-bottom: 8px}
.alert-normal {background: #1e8e3e} .alert-smoke {background: #e37400} .alert-fire {background: #d93025}
.alert-offline {background: #5f6368}
</style>""", unsafe_allow_html=True)

DEFAULTS = Settings()


@st.cache_resource(show_spinner="Loading YOLO model...")
def load_detector(model_path, imgsz):
    return Detector(model_path, imgsz, 0.5)


def get_store():
    return IncidentStore(DEFAULTS.db_path, DEFAULTS.incidents_dir / "media")


def banner(status):
    cls, text = {"NORMAL": ("alert-normal", "✅ Status: NORMAL"),
                 "SMOKE DETECTED": ("alert-smoke", "⚠️ ALERT: SMOKE DETECTED"),
                 "FIRE DETECTED": ("alert-fire", "🚨 CRITICAL ALERT: FIRE DETECTED"),
                 "OFFLINE": ("alert-offline", "📷 Camera OFFLINE")}.get(status, ("alert-normal", status))
    return f'<div class="alert-box {cls}">{text}</div>'


# ---------------------------------------------------------------- sidebar
st.sidebar.title("🔥 VisionGuard")
st.sidebar.caption("Fire & smoke detection for CCTV")

src_type = st.sidebar.radio("Video source", ["Test video", "Upload video", "Webcam", "CCTV / RTSP URL"])
source = None
if src_type == "Test video":
    vids = sorted((ROOT / "data" / "videos").glob("*/videos/*"))
    if vids:
        labels = {f"{v.parent.parent.name} / {v.name}": v for v in vids}
        default = next((i for i, k in enumerate(labels) if k.startswith("test /")), 0)
        source = str(labels[st.sidebar.selectbox("Video", list(labels), index=default)])
    else:
        st.sidebar.warning("No dataset videos found. Run `python -m dataset_prep.run_all` first.")
elif src_type == "Upload video":
    up = st.sidebar.file_uploader("Video file", type=["mp4", "avi", "mov", "mkv", "wmv"])
    if up:
        tmp = Path(tempfile.gettempdir()) / f"visionguard_{up.name}"
        tmp.write_bytes(up.getbuffer())
        source = str(tmp)
elif src_type == "Webcam":
    source = str(st.sidebar.number_input("Webcam index", 0, 10, 0))
else:
    source = st.sidebar.text_input("Stream URL", placeholder="rtsp://user:pass@192.168.1.10:554/stream") or None

camera_name = st.sidebar.text_input("Camera name", "Warehouse Camera 1")
models = sorted([p for p in MODELS.glob("*") if p.suffix in (".onnx", ".pt")],
                key=lambda p: (not p.name.startswith("fire_smoke"), p.suffix != ".onnx", p.name))
model_path = st.sidebar.selectbox("Model", [str(p) for p in models] or [DEFAULTS.model_path],
                                  format_func=lambda p: Path(p).name)
with st.sidebar.expander("Detection settings", expanded=False):
    conf = st.slider("Confidence threshold", 0.1, 0.95, DEFAULTS.conf_threshold, 0.05)
    sample_fps = st.slider("Frames analysed per second", 1.0, 15.0, DEFAULTS.sample_fps, 1.0)
    window = st.slider("Confirmation window (frames)", 3, 30, DEFAULTS.window)
    min_hits = st.slider("Frames needed to confirm", 1, window, min(DEFAULTS.min_hits, window))
    realtime = st.checkbox("Play files at real speed", True)
    email = st.checkbox("Send e-mail alerts (needs SMTP_* env vars)", False)

c1, c2 = st.sidebar.columns(2)
start = c1.button("▶ Start", type="primary", width="stretch", disabled=source is None)
stop = c2.button("■ Stop", width="stretch")
if start:
    st.session_state.running = True
if stop:
    st.session_state.running = False

tab_live, tab_hist, tab_eval = st.tabs(["📺 Live monitor", "🗂️ Incident history", "📊 System test results"])

# ---------------------------------------------------------------- live monitor
with tab_live:
    banner_ph = st.empty()
    m = st.columns(7)
    ph = {k: m[i].empty() for i, k in enumerate(
        ["camera", "status", "fire", "smoke", "alerts", "conf", "fps"])}
    left, right = st.columns([3, 2])
    video_ph = left.empty()
    progress_ph = left.empty()
    right.subheader("Alerts")
    alerts_ph = right.empty()
    det_ph = right.empty()

    def show_metrics(camera, status, fire, smoke, active, confv, fps, last_time):
        banner_ph.markdown(banner(status), unsafe_allow_html=True)
        ph["camera"].metric("Camera", camera)
        ph["status"].metric("Status", status.title())
        ph["fire"].metric("🔥 Fire", fire)
        ph["smoke"].metric("💨 Smoke", smoke)
        ph["alerts"].metric("Active alerts", active)
        ph["conf"].metric("Confidence", f"{confv:.0%}" if confv else "–")
        ph["fps"].metric("Processing FPS", f"{fps:.1f}" if fps else "–", help=f"Last alert: {last_time or '–'}")

    if not st.session_state.get("running"):
        show_metrics("Idle", "NORMAL", 0, 0, 0, 0, 0, None)
        video_ph.info("Choose a video source in the sidebar and press **▶ Start**.")
    else:
        s = Settings(model_path=model_path, conf_threshold=conf, sample_fps=sample_fps, window=window,
                     min_hits=min_hits, clear_hits=max(0, min(2, min_hits - 1)), camera_name=camera_name,
                     notify_email=email)
        det = load_detector(model_path, s.imgsz)
        det.conf_threshold = conf
        proc = VideoProcessor(source, s, detector=det, store=get_store(), log=lambda *_: None)
        alert_log, last_alert, shown_every = [], None, 1
        total = int(cv2.VideoCapture(source).get(cv2.CAP_PROP_FRAME_COUNT) or 0) if src_type in (
            "Test video", "Upload video") else 0
        t_last_draw = 0.0
        try:
            for r in proc.run(realtime=realtime):
                if not st.session_state.get("running"):
                    proc.stop()
                    break
                for a in r.alerts:
                    last_alert = a["time"]
                    alert_log.insert(0, a)
                    st.toast(f"🚨 {a['type'].upper()} DETECTED on {a['camera']} ({a['confidence']:.0%})", icon="🔥")
                # redraw at most ~12x per second to keep the browser responsive
                if time.time() - t_last_draw < 0.08 and not r.alerts:
                    continue
                t_last_draw = time.time()
                if r.frame is not None:
                    video_ph.image(cv2.cvtColor(r.frame, cv2.COLOR_BGR2RGB), width="stretch")
                if total:
                    progress_ph.progress(min(1.0, (r.index + 1) / total), text=f"t = {r.video_time:.1f} s")
                fire = sum(d.label == "fire" for d in r.detections)
                smoke = sum(d.label == "smoke" for d in r.detections)
                confv = max((d.confidence for d in r.detections), default=0.0)
                show_metrics("Online" if r.online else "Offline", r.status, fire, smoke, len(r.active), confv,
                             r.proc_fps, last_alert)
                if alert_log:
                    alerts_ph.dataframe(pd.DataFrame([{
                        "time": a["time"], "type": a["type"].upper(), "confidence": f"{a['confidence']:.0%}",
                        "severity": a["severity"], "video t (s)": a["video_time"], "camera": a["camera"]}
                        for a in alert_log]), hide_index=True, width="stretch")
                else:
                    alerts_ph.success("No alerts")
                det_ph.caption(f"Sliding window hits: fire {r.hits.get('fire', 0)}/{window}, "
                               f"smoke {r.hits.get('smoke', 0)}/{window} (confirm at {min_hits}) · "
                               f"weak detections filtered on last frame: {r.rejected}")
        except RuntimeError as e:
            st.error(str(e))
        st.session_state.running = False
        st.info(f"Finished. Confirmed events: {len(alert_log)}. See **Incident history**.")

# ---------------------------------------------------------------- incident history
with tab_hist:
    store = get_store()
    rows = store.all()
    h1, h2 = st.columns([4, 1])
    h1.subheader(f"Incident history ({len(rows)})")
    if h2.button("Clear history"):
        store.clear()
        st.rerun()
    if not rows:
        st.info("No incidents yet.")
    else:
        df = pd.DataFrame(rows)
        st.dataframe(df[["id", "started_at", "camera", "type", "severity", "confidence", "status", "video_time_sec",
                         "source"]].assign(confidence=lambda d: (d.confidence * 100).round(0).astype(int).astype(str) + "%"),
                     hide_index=True, width="stretch")
        pick = st.selectbox("Show incident", df["id"], format_func=lambda i: next(
            f"#{r['id']}  {r['started_at']}  {r['type'].upper()}  {r['camera']}" for r in rows if r["id"] == i))
        inc = next(r for r in rows if r["id"] == pick)
        a, b = st.columns(2)
        if inc["snapshot_path"] and Path(inc["snapshot_path"]).exists():
            a.image(inc["snapshot_path"], caption="Detection frame", width="stretch")
        if inc["clip_path"] and Path(inc["clip_path"]).exists():
            b.video(inc["clip_path"])
            b.caption("Video clip around the confirmation")

# ---------------------------------------------------------------- reports
with tab_eval:
    for name in ("system_evaluation.md", "model_evaluation.md", "system_settings_sweep.md"):
        p = ROOT / "reports" / name
        if p.exists():
            st.markdown(p.read_text(encoding="utf-8"))
            st.divider()
    cms = sorted((ROOT / "reports").glob("confusion_matrix_*.png"))
    if cms:
        cols = st.columns(len(cms))
        for c, p in zip(cols, cms):
            c.image(str(p), caption=p.stem, width="stretch")
    if not (ROOT / "reports").exists():
        st.info("Run `python -m training.evaluate` and `python -m system_test.evaluate_system` to fill this page.") 