# apps/worker/ai_worker/ai/omr/roi_builder.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import math


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def build_questions_payload_from_meta(
    *,
    meta: Dict[str, Any],
    image_size_px: Tuple[int, int],
) -> List[Dict[str, Any]]:
    """
    meta(mm) -> questions payload (px) for detect_omr_answers_v1()

    detect_omr_answers_v1 expects:
      questions: [{question_id, roi:{x,y,w,h}, choices:[...], axis:"x"}, ...]

    image_size_px: (width, height)
    """
    img_w, img_h = image_size_px

    page = meta.get("page") or {}
    size = page.get("size") or {}
    page_w_mm = float(size.get("width") or 0.0)
    page_h_mm = float(size.get("height") or 0.0)
    if page_w_mm <= 0.0 or page_h_mm <= 0.0:
        raise ValueError("invalid meta page size")

    # 정렬된 스캔/워프 결과는 "페이지 전체가 이미지 전체"라고 가정
    sx = img_w / page_w_mm
    sy = img_h / page_h_mm

    out: List[Dict[str, Any]] = []
    for q in (meta.get("questions") or []):
        qnum = int(q.get("question_number") or 0)
        roi = q.get("roi") or {}

        x_mm = float(roi.get("x") or 0.0)
        y_mm = float(roi.get("y") or 0.0)
        w_mm = float(roi.get("w") or 0.0)
        h_mm = float(roi.get("h") or 0.0)

        x = int(round(x_mm * sx))
        y = int(round(y_mm * sy))
        w = int(round(w_mm * sx))
        h = int(round(h_mm * sy))

        # 안전 클램프
        x = _clamp(x, 0, img_w - 1)
        y = _clamp(y, 0, img_h - 1)
        w = _clamp(w, 1, img_w - x)
        h = _clamp(h, 1, img_h - y)

        out.append(
            {
                "question_id": qnum,  # worker 엔진은 question_id만 사용
                "roi": {"x": x, "y": y, "w": w, "h": h},
                "choices": ["A", "B", "C", "D", "E"],
                "axis": "x",
            }
        )

    # question_id 순서 보장
    out.sort(key=lambda d: int(d.get("question_id") or 0))
    return out
