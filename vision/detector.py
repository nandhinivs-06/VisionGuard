"""YOLO fire/smoke detector with confidence filtering (pipeline steps 3 and 4)."""
from dataclasses import dataclass

import cv2

COLORS = {"fire": (0, 0, 255), "smoke": (200, 200, 200)}


@dataclass
class Detection:
    label: str          # "fire" or "smoke" (taken from the model's class names, so id order doesn't matter)
    confidence: float
    box: tuple          # x1, y1, x2, y2 in pixels


class Detector:
    def __init__(self, model_path, imgsz=640, conf_threshold=0.5):
        from ultralytics import YOLO

        self.model = YOLO(model_path, task="detect")
        self.imgsz = imgsz
        self.conf_threshold = conf_threshold
        self.names = {int(k): v.lower() for k, v in self.model.names.items()}

    def detect(self, frame):
        """Return (kept, rejected): detections at/above the threshold, and weak ones that were filtered out."""
        r = self.model.predict(frame, imgsz=self.imgsz, conf=min(0.25, self.conf_threshold), verbose=False)[0]
        kept, rejected = [], []
        for cls, conf, xyxy in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
            d = Detection(self.names[int(cls)], float(conf), tuple(int(v) for v in xyxy))
            (kept if d.confidence >= self.conf_threshold else rejected).append(d)
        return kept, rejected


def draw(frame, detections):
    """Draw bounding boxes + 'fire 94%' labels on a copy of the frame."""
    out = frame.copy()
    for d in detections:
        c = COLORS.get(d.label, (0, 255, 255))
        x1, y1, x2, y2 = d.box
        cv2.rectangle(out, (x1, y1), (x2, y2), c, 2)
        text = f"{d.label} {d.confidence:.0%}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(out, (x1, max(0, y1 - th - 6)), (x1 + tw + 4, y1), c, -1)
        cv2.putText(out, text, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return out
