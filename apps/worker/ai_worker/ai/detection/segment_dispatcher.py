# apps/worker/ai/detection/segment_dispatcher.py
from __future__ import annotations

from typing import List, Tuple

from apps.worker.ai_worker.ai.config import AIConfig
from apps.worker.ai_worker.ai.detection.segment_opencv import segment_questions_opencv
from apps.worker.ai_worker.ai.detection.segment_yolo import segment_questions_yolo

BBox = Tuple[int, int, int, int]


def segment_questions(image_path: str) -> List[BBox]:
    """
    worker-side segmentation single entrypoint
    """
    cfg = AIConfig.load()
    engine = (cfg.QUESTION_SEGMENTATION_ENGINE or "auto").lower()

    if engine == "opencv":
        return segment_questions_opencv(image_path)
    if engine == "yolo":
        return segment_questions_yolo(image_path)

    # auto: yolo -> opencv
    try:
        boxes = segment_questions_yolo(image_path)
        if boxes:
            return boxes
    except Exception:
        pass
    return segment_questions_opencv(image_path)
