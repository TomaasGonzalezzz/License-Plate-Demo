"""The three neural networks, all run with ONNX Runtime.

* Vehicles: RF-DETR (Apache-2.0, Roboflow), pretrained on COCO, exported to ONNX
  and distributed by ``open-image-models``. Same model family as the counting demo,
  without needing PyTorch at run time.
* Plates: the YOLOv9-based plate detector of ``open-image-models`` (MIT code). It
  runs on the full-resolution crop of each vehicle, where the plate is many more
  pixels than in the downscaled frame.
* Reading: ``fast-plate-ocr`` (MIT code). Default model ``cct-s-v2-global-model``;
  ``argentinian-plates-cnn-synth-model`` is trained only on Argentine plates.
  See docs/EVALUATION.md for how they compare.

Weights are downloaded on first use into each library's cache (``~/.cache``),
never into this repository.

Device: ``cpu`` (default), ``dml`` (DirectML: any DirectX 12 GPU on Windows,
including AMD Radeon) or ``auto``. If the GPU session cannot be created or fails
on its first inference, everything falls back to the CPU with a warning.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("platedemo")

VEHICLES = ("car", "truck", "bus", "motorcycle")


def providers_for(device: str) -> list[str]:
    import onnxruntime as ort

    available = ort.get_available_providers()
    if device == "cpu":
        return ["CPUExecutionProvider"]
    if device in ("dml", "auto"):
        if "DmlExecutionProvider" in available:
            return ["DmlExecutionProvider", "CPUExecutionProvider"]
        if device == "dml":
            warnings.warn("DirectML is not available in this onnxruntime build; using CPU. "
                          "Install onnxruntime-directml to use a DirectX 12 GPU.", stacklevel=2)
        return ["CPUExecutionProvider"]
    raise ValueError(f"device must be cpu, dml or auto, got {device!r}")


def session_options():
    """One thread per physical core: on laptops with hyper-threading this is clearly faster than the default."""
    import os

    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return so


def _quiet_onnxruntime() -> None:
    import onnxruntime as ort

    ort.set_default_logger_severity(3)
    logging.getLogger("open_image_models").setLevel(logging.WARNING)
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(("open_image_models", "fast_plate_ocr")):
            logging.getLogger(name).setLevel(logging.WARNING)


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    label: str = ""

    @property
    def width(self) -> float:
        return self.x2 - self.x1


class _WithFallback:
    """Build a model on the requested providers; rebuild on CPU if the GPU path fails."""

    def __init__(self, build, providers: list[str], probe: np.ndarray):
        self._build = build
        self.providers = providers
        try:
            self.model = build(providers)
            self._call(probe)
        except Exception as e:  # noqa: BLE001 - any GPU failure means: use the CPU
            if providers == ["CPUExecutionProvider"]:
                raise
            warnings.warn(f"GPU inference failed ({e.__class__.__name__}: {e}); falling back to CPU.", stacklevel=2)
            self.providers = ["CPUExecutionProvider"]
            self.model = build(self.providers)

    def _call(self, image):
        return self.model.predict(image)


class VehicleDetector(_WithFallback):
    def __init__(self, model: str = "rf-detr-nano-384-coco", threshold: float = 0.45, device: str = "cpu"):
        from open_image_models.detection.factory import create_detector

        _quiet_onnxruntime()
        super().__init__(lambda p: create_detector(model, conf_thresh=threshold, providers=p, sess_options=session_options()),
                         providers_for(device), np.zeros((384, 640, 3), np.uint8))

    def __call__(self, frame_bgr: np.ndarray, classes=VEHICLES) -> list[Box]:
        out = []
        for d in self.model.predict(frame_bgr):
            if d.label in classes:
                b = d.bounding_box
                out.append(Box(b.x1, b.y1, b.x2, b.y2, float(d.confidence), d.label))
        return out


class PlateDetector(_WithFallback):
    def __init__(self, model: str = "yolo-v9-t-640-license-plate-end2end", threshold: float = 0.3, device: str = "cpu"):
        from open_image_models.detection.factory import create_detector

        _quiet_onnxruntime()
        super().__init__(lambda p: create_detector(model, conf_thresh=threshold, providers=p, sess_options=session_options()),
                         providers_for(device), np.zeros((320, 320, 3), np.uint8))

    def __call__(self, crop_bgr: np.ndarray) -> list[Box]:
        return [Box(*d.bounding_box.xyxy, float(d.confidence), "plate") for d in self.model.predict(crop_bgr)]


class PlateReader:
    def __init__(self, model: str = "cct-s-v2-global-model", device: str = "cpu"):
        from fast_plate_ocr import LicensePlateRecognizer

        _quiet_onnxruntime()
        providers = providers_for(device)
        self.providers = providers
        try:
            self.ocr = LicensePlateRecognizer(model, providers=providers, sess_options=session_options())
            self.gray = self.ocr.config.image_color_mode == "grayscale"
            self._read(np.zeros((40, 120, 3), np.uint8))
        except Exception as e:  # noqa: BLE001
            if providers == ["CPUExecutionProvider"]:
                raise
            warnings.warn(f"GPU OCR failed ({e}); falling back to CPU.", stacklevel=2)
            self.ocr = LicensePlateRecognizer(model, providers=["CPUExecutionProvider"])
            self.providers = ["CPUExecutionProvider"]
            self.gray = self.ocr.config.image_color_mode == "grayscale"
        self.model_name = model
        self.gray = self.ocr.config.image_color_mode == "grayscale"

    def _read(self, crop_bgr: np.ndarray):
        img = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY if self.gray else cv2.COLOR_BGR2RGB)
        return self.ocr.run(img, return_confidence=True)[0]

    def __call__(self, crop_bgr: np.ndarray) -> tuple[str, np.ndarray]:
        """Read one plate crop; return the raw text (OCR padding removed) and per-character confidences."""
        r = self._read(crop_bgr)
        text = r.plate.replace("_", "")
        probs = np.asarray(r.char_probs, dtype=float).ravel()[: len(r.plate)]
        keep = [i for i, c in enumerate(r.plate) if c != "_"]
        return text, probs[keep] if len(probs) else np.zeros(len(text))
