"""Incident storage (pipeline step 8): SQLite record + detection frame + short video clip."""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import cv2

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera TEXT, source TEXT,
    type TEXT,                 -- fire / smoke
    severity TEXT,             -- HIGH / MEDIUM
    confidence REAL,           -- max confidence while confirming
    started_at TEXT,           -- wall-clock time of the alert
    ended_at TEXT,
    video_time_sec REAL,       -- position in the video when confirmed
    snapshot_path TEXT, clip_path TEXT,
    status TEXT,               -- ACTIVE / RESOLVED
    review TEXT DEFAULT 'PENDING',  -- operator review: PENDING / CONFIRMED / FALSE_ALARM
    note TEXT DEFAULT ''
)"""
REVIEWS = ("PENDING", "CONFIRMED", "CONFIRMED_FIRE", "CONFIRMED_SMOKE", "FALSE_ALARM")


def severity_for(label):
    return "HIGH" if label == "fire" else "MEDIUM"


class IncidentStore:
    def __init__(self, db_path, media_dir):
        self.db_path = Path(db_path)
        self.media_dir = Path(media_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(incidents)")}
            if "review" not in cols:  # databases created before operator review existed
                c.execute("ALTER TABLE incidents ADD COLUMN review TEXT DEFAULT 'PENDING'")
            if "note" not in cols:
                c.execute("ALTER TABLE incidents ADD COLUMN note TEXT DEFAULT ''")

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def open_incident(self, camera, source, label, confidence, video_time, snapshot):
        ts = datetime.now()
        stem = f"{ts:%Y%m%d_%H%M%S}_{label}_{abs(hash((camera, source, video_time))) % 10000:04d}"
        snap_path = self.media_dir / f"{stem}.jpg"
        cv2.imwrite(str(snap_path), snapshot)
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO incidents (camera, source, type, severity, confidence, started_at, video_time_sec,"
                " snapshot_path, clip_path, status) VALUES (?,?,?,?,?,?,?,?,?, 'ACTIVE')",
                (camera, source, label, severity_for(label), round(confidence, 4), ts.isoformat(timespec="seconds"),
                 round(video_time, 2), str(snap_path), str(self.media_dir / f"{stem}.mp4")))
            return cur.lastrowid, self.media_dir / f"{stem}.mp4"

    def set_clip_path(self, incident_id, path):
        with self._lock, self._conn() as c:
            c.execute("UPDATE incidents SET clip_path = ? WHERE id = ?", (str(path), incident_id))

    def update_confidence(self, incident_id, confidence):
        with self._lock, self._conn() as c:
            c.execute("UPDATE incidents SET confidence = MAX(confidence, ?) WHERE id = ?", (round(confidence, 4), incident_id))

    def close_incident(self, incident_id):
        with self._lock, self._conn() as c:
            c.execute("UPDATE incidents SET status='RESOLVED', ended_at=? WHERE id=? AND status='ACTIVE'",
                      (datetime.now().isoformat(timespec="seconds"), incident_id))

    def all(self, limit=500):
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,))]

    def get(self, incident_id):
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            r = c.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            return dict(r) if r else None

    def review(self, incident_id, review=None, note=None):
        """Operator feedback: mark an incident as a confirmed fire or a false alarm, and/or add a note."""
        if review is not None and review not in REVIEWS:
            raise ValueError(f"review must be one of {REVIEWS}")
        with self._lock, self._conn() as c:
            if review is not None:
                c.execute("UPDATE incidents SET review = ? WHERE id = ?", (review, incident_id))
            if note is not None:
                c.execute("UPDATE incidents SET note = ? WHERE id = ?", (note[:2000], incident_id))
        return self.get(incident_id)

    def delete(self, incident_id):
        """Delete one incident and its snapshot / clip files."""
        r = self.get(incident_id)
        if not r:
            return False
        for key in ("snapshot_path", "clip_path"):
            if r.get(key):
                Path(r[key]).unlink(missing_ok=True)
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
        return True

    def clear(self):
        for r in self.all(100000):
            for key in ("snapshot_path", "clip_path"):
                if r.get(key):
                    Path(r[key]).unlink(missing_ok=True)
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM incidents")


# Browser-playable first (the dashboard plays clips in the browser): H.264 MP4, then VP8 WebM, then MPEG-4.
CODECS = (("avc1", ".mp4"), ("VP80", ".webm"), ("mp4v", ".mp4"))


class ClipWriter:
    """Writes `before` frames already buffered + frames for `after_sec` more seconds to a video file."""

    def __init__(self, path, before_frames, fps, after_sec):
        self.fps = max(1.0, fps)
        self.remaining = int(after_sec * self.fps)
        h, w = before_frames[-1].shape[:2] if before_frames else (480, 640)
        self.size = (w, h)
        self.writer = None
        for fourcc, ext in CODECS:
            self.path = Path(path).with_suffix(ext)
            self.writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*fourcc), self.fps, (w, h))
            if self.writer.isOpened():
                break
        for f in before_frames:
            self.writer.write(f)

    def write(self, frame):
        """Returns False when the clip is complete."""
        if frame.shape[1::-1] != self.size:
            frame = cv2.resize(frame, self.size)
        self.writer.write(frame)
        self.remaining -= 1
        if self.remaining <= 0:
            self.close()
            return False
        return True

    def close(self):
        if self.writer is not None:
            self.writer.release()
            self.writer = None
