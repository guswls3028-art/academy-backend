# apps/worker/ai_worker/ai/omr/roi_builder.py
"""
OMR v7 ROI 빌더

meta_generator.py의 mm 좌표를 워커 엔진이 사용하는 px 좌표로 변환한다.
v7 메타 형식: choices에 label("1"~"5"), center, radius_x, radius_y 포함.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def build_questions_payload_from_meta(
    *,
    meta: Dict[str, Any],
    image_size_px: Tuple[int, int],
) -> List[Dict[str, Any]]:
    """
    v7 meta(mm) → questions payload (px).

    engine.py의 detect_omr_answers_v7은 meta를 직접 받으므로
    이 함수는 하위호환 또는 디버깅용.
    """
    img_w, img_h = image_size_px

    page = meta.get("page") or {}
    page_w_mm = float(page.get("width") or 0.0)
    page_h_mm = float(page.get("height") or 0.0)
    if page_w_mm <= 0.0 or page_h_mm <= 0.0:
        raise ValueError("invalid meta page size")

    sx = img_w / page_w_mm
    sy = img_h / page_h_mm

    out: List[Dict[str, Any]] = []
    for q in meta.get("questions") or []:
        qnum = int(q.get("question_number") or 0)
        roi = q.get("roi") or {}

        x = _clamp(int(round(float(roi.get("x", 0)) * sx)), 0, img_w - 1)
        y = _clamp(int(round(float(roi.get("y", 0)) * sy)), 0, img_h - 1)
        w = _clamp(int(round(float(roi.get("w", 0)) * sx)), 1, img_w - x)
        h = _clamp(int(round(float(roi.get("h", 0)) * sy)), 1, img_h - y)

        choices = [
            str(c.get("label", str(i + 1)))
            for i, c in enumerate(q.get("choices", []))
        ]

        out.append({
            "question_id": qnum,
            "roi": {"x": x, "y": y, "w": w, "h": h},
            "choices": choices or ["1", "2", "3", "4", "5"],
        })

    out.sort(key=lambda d: int(d.get("question_id") or 0))
    return out
