# apps/worker/ai/detection/segment_yolo.py
from __future__ import annotations

from functools import lru_cache
from typing import List, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai.config import AIConfig

BBox = Tuple[int, int, int, int]


class YoloNotConfiguredError(RuntimeError):
    pass


try:
    import onnxruntime as ort  # type: ignore
    _HAS_ORT = True
except Exception:
    _HAS_ORT = False


@lru_cache()
def _get_session():
    cfg = AIConfig.load()

    if not _HAS_ORT:
        raise YoloNotConfiguredError("onnxruntime not installed")

    if not cfg.YOLO_QUESTION_MODEL_PATH:
        raise YoloNotConfiguredError("YOLO_QUESTION_MODEL_PATH not set")

    providers = ["CPUExecutionProvider"]
    return ort.InferenceSession(str(cfg.YOLO_QUESTION_MODEL_PATH), providers=providers)


def _preprocess(image_bgr, input_size: int):
    h0, w0 = image_bgr.shape[:2]
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(image_rgb, (input_size, input_size))
    resized = resized.astype(np.float32) / 255.0

    tensor = np.transpose(resized, (2, 0, 1))
    tensor = np.expand_dims(tensor, axis=0)

    scale_x = w0 / float(input_size)
    scale_y = h0 / float(input_size)
    return tensor, scale_x, scale_y


def _nms(boxes, scores, iou_threshold: float):
    if len(boxes) == 0:
        return []

    boxes = boxes.astype(np.float32)
    scores = scores.astype(np.float32)

    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h

        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        idxs = np.where(iou <= iou_threshold)[0]
        order = order[idxs + 1]

    return keep


def segment_questions_yolo(image_path: str) -> List[BBox]:
    cfg = AIConfig.load()
    sess = _get_session()

    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        return []

    input_tensor, scale_x, scale_y = _preprocess(image_bgr, cfg.YOLO_QUESTION_INPUT_SIZE)

    input_name = sess.get_inputs()[0].name
    outputs = sess.run(None, {input_name: input_tensor})
    preds = outputs[0]
    if preds.ndim == 3:
        preds = preds[0]

    boxes = []
    scores = []

    for det in preds:
        cx, cy, w, h, obj_conf = det[:5]
        cls_scores = det[5:]
        cls_conf = float(cls_scores.max()) if cls_scores.size > 0 else 1.0

        score = float(obj_conf * cls_conf)
        if score < cfg.YOLO_QUESTION_CONF_THRESHOLD:
            continue

        x1 = (cx - w / 2.0) * scale_x
        y1 = (cy - h / 2.0) * scale_y
        x2 = (cx + w / 2.0) * scale_x
        y2 = (cy + h / 2.0) * scale_y

        boxes.append([x1, y1, x2, y2])
        scores.append(score)

    if not boxes:
        return []

    boxes_np = np.array(boxes)
    scores_np = np.array(scores)

    keep_idx = _nms(boxes_np, scores_np, cfg.YOLO_QUESTION_IOU_THRESHOLD)

    final: List[BBox] = []
    for i in keep_idx:
        x1, y1, x2, y2 = boxes_np[i]
        final.append((int(x1), int(y1), int(x2 - x1), int(y2 - y1)))

    final.sort(key=lambda b: (b[1], b[0]))
    return final
