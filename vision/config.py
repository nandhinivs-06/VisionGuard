"""Settings for the video detection & alert system (Member 3). All values can be overridden."""
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
INCIDENTS_DIR = ROOT / "incidents"


def default_model():
    """Best available model: the selected/fine-tuned one, else the pretrained base (ONNX preferred - ~2x faster on CPU)."""
    for name in ("fire_smoke.onnx", "fire_smoke.pt", "base_dfire_yolov8n.onnx", "base_dfire_yolov8n.pt"):
        if (MODELS / name).exists():
            return str(MODELS / name)
    return str(MODELS / "fire_smoke.pt")


@dataclass
class Settings:
    model_path: str = field(default_factory=default_model)
    imgsz: int = 640
    conf_threshold: float = 0.3          # step 4: detections below this are ignored (tuned on val videos)
    sample_fps: float = 5.0              # step 2: frames analysed per second of video (others only displayed)
    window: int = 10                     # step 5: sliding window of analysed frames ...
    min_hits: int = 5                    # ... a class is CONFIRMED when seen in >= min_hits of the last `window` frames
    clear_hits: int = 2                  # event ends when seen in <= clear_hits of the last `window` frames
    camera_name: str = "Camera 1"
    save_incidents: bool = True
    clip_before_sec: float = 3.0         # step 8: clip = a few seconds before + after confirmation
    clip_after_sec: float = 5.0
    incidents_dir: Path = INCIDENTS_DIR
    db_path: Path = INCIDENTS_DIR / "incidents.db"
    notify_email: bool = False           # step 9 (optional): needs SMTP_* environment variables, see vision/notifier.py
