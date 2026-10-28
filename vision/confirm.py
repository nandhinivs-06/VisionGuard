"""Multi-frame confirmation (pipeline step 5).

One sliding window per class ("fire", "smoke") over the last `window` analysed
frames. A class becomes CONFIRMED when it was detected in >= `min_hits` of
them (e.g. 7 of 10), and the event ends when it drops to <= `clear_hits`.
Single-frame detections from reflections, lights or dust never reach the
threshold, so they never raise an alert.
"""
from collections import deque


class SlidingWindowConfirmer:
    def __init__(self, labels=("fire", "smoke"), window=10, min_hits=5, clear_hits=2):
        if not 0 <= clear_hits < min_hits <= window:
            raise ValueError("need 0 <= clear_hits < min_hits <= window")
        self.window, self.min_hits, self.clear_hits = window, min_hits, clear_hits
        self.history = {l: deque(maxlen=window) for l in labels}
        self.active = {l: False for l in labels}

    def update(self, detections):
        """Feed the filtered detections of one analysed frame.
        Returns (started, ended): labels whose event was just confirmed / just cleared."""
        present = {d.label for d in detections}
        started, ended = [], []
        for label, hist in self.history.items():
            hist.append(label in present)
            hits = sum(hist)
            if not self.active[label] and hits >= self.min_hits:
                self.active[label] = True
                started.append(label)
            elif self.active[label] and hits <= self.clear_hits:
                self.active[label] = False
                ended.append(label)
        return started, ended

    def hits(self, label):
        return sum(self.history[label])

    def reset(self):
        for h in self.history.values():
            h.clear()
        for l in self.active:
            self.active[l] = False
