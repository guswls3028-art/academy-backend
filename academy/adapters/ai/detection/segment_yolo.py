# apps/worker/ai/detection/segment_yolo.py
"""
YOLOv8 기반 시험지 문항 세그멘테이션.

Ultralytics API를 사용하여 학습된 모델로 문항 영역을 직접 검출.
모델 경로: AIConfig.YOLO_QUESTION_MODEL_PATH (.pt 또는 .onnx)
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import List, Tuple

from academy.adapters.ai.config import AIConfig

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


class YoloNotConfiguredError(RuntimeError):
    pass


@lru_cache()
def _get_model():
    """YOLO 모델 로드 (LRU 캐시 — 프로세스 당 1회)."""
    cfg = AIConfig.load()

    if not cfg.YOLO_QUESTION_MODEL_PATH:
        raise YoloNotConfiguredError("YOLO_QUESTION_MODEL_PATH not set")

    try:
        from ultralytics import YOLO  # type: ignore
    except ImportError as e:
        raise YoloNotConfiguredError(f"ultralytics not installed: {e}") from e

    model_path = str(cfg.YOLO_QUESTION_MODEL_PATH)
    logger.info("YOLO_MODEL_LOAD | path=%s", model_path)
    return YOLO(model_path)


def segment_questions_yolo(
    image_path: str,
    *,
    source_type: str | None = None,
) -> List[BBox]:
    """
    YOLO 모델로 문항 영역 검출.

    Args:
        source_type: 양식 신호 (P1.5, 2026-05-06). commercial_workbook / academy_workbook /
            student_exam_photo / school_exam_pdf. 양식별 conf override (AIConfig.YOLO_QUESTION_CONF_MAP).
            None 이면 default conf (회귀 0). 호출 chain 점진 적용.

    Returns:
        [(x, y, w, h), ...] — 문항 바운딩 박스 (좌상단 기준, 픽셀 좌표)
    """
    cfg = AIConfig.load()
    model = _get_model()

    # 양식별 conf 적용 — source_type 매칭 시 매핑 사용, 없으면 default.
    conf = cfg.YOLO_QUESTION_CONF_THRESHOLD
    if source_type and isinstance(cfg.YOLO_QUESTION_CONF_MAP, dict):
        conf = cfg.YOLO_QUESTION_CONF_MAP.get(source_type, conf)

    results = model(
        image_path,
        imgsz=cfg.YOLO_QUESTION_INPUT_SIZE,
        conf=conf,
        iou=cfg.YOLO_QUESTION_IOU_THRESHOLD,
        verbose=False,
    )

    if not results or len(results) == 0:
        return []

    detections = results[0].boxes
    if detections is None or len(detections) == 0:
        return []

    boxes: List[BBox] = []
    for box in detections:
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
        w = int(x2 - x1)
        h = int(y2 - y1)
        if w > 0 and h > 0:
            boxes.append((int(x1), int(y1), w, h))

    # 정렬: 위→아래, 왼쪽→오른쪽
    boxes.sort(key=lambda b: (b[1], b[0]))

    logger.info(
        "YOLO_SEGMENT | path=%s | source_type=%s | conf=%.2f | boxes=%d",
        image_path, source_type or "-", conf, len(boxes),
    )
    return boxes
