"""Member 3 - video processing & alert system."""
try:  # Windows: onnxruntime must load its DLLs before torch/pandas/cv2, or importing it later fails
    import onnxruntime  # noqa: F401
except ImportError:
    pass
