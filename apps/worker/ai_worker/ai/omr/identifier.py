# apps/worker/ai_worker/ai/omr/identifier.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.omr.meta_px import build_page_scale_from_meta, PageScale


BBox = Tuple[int, int, int, int]


@dataclass(frozen=True)
class IdentifierConfigV1:
    """
    Identifier OMR v1 (8 digits, each digit 0~9 single mark).

    Principles:
    - ROI based fill score (same philosophy as detect_omr_answers_v1)
    - Robust to scan/photo noise by sampling a square ROI around each bubble
    - No DB, no external calls, worker-only judgement/extraction
    """
    # (OPS DEFAULT) 촬영/워프에서 중심 오차를 흡수하기 위해 소폭 확장
    # 주변 ROI(버블 중심 기준) 확장 계수: r * k
    roi_expand_k: float = 1.60

    # (OPS DEFAULT) blank 과다 방지: 실데이터에서 연필 농도 낮은 케이스 대응
    # blank 판단: 해당 digit에서 최고 fill이 이 값보다 작으면 blank
    blank_threshold: float = 0.055

    # (OPS DEFAULT) ambiguous 과다 방지: top-2 gap 기준 소폭 완화
    # ambiguous 판단: top-2 gap이 이 값보다 작으면 ambiguous
    conf_gap_threshold: float = 0.050

    # (운영 편의) digit-level confidence clamp
    min_confidence: float = 0.0
    max_confidence: float = 1.0


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _crop(gray: np.ndarray, bbox: BBox) -> np.ndarray:
    x, y, w, h = bbox
    x = _clamp(int(x), 0, gray.shape[1] - 1)
    y = _clamp(int(y), 0, gray.shape[0] - 1)
    w = max(1, int(w))
    h = max(1, int(h))
    w = min(w, gray.shape[1] - x)
    h = min(h, gray.shape[0] - y)
    return gray[y:y + h, x:x + w]


def _fill_score(roi_gray: np.ndarray) -> float:
    """
    Same core idea as OMR v1:
    - blur
    - OTSU + INV
    - filled pixel ratio
    """
    if roi_gray.size == 0:
        return 0.0

    blur = cv2.GaussianBlur(roi_gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    filled = float(np.sum(th > 0))
    total = float(th.size) if th.size > 0 else 1.0
    score = filled / total
    return float(max(0.0, min(1.0, score)))


def _bubble_roi_bbox_px(
    *,
    center_px: Tuple[int, int],
    r_px: int,
    cfg: IdentifierConfigV1,
    img_w: int,
    img_h: int,
) -> BBox:
    cx, cy = center_px
    side = int(round(max(2, r_px) * cfg.roi_expand_k)) * 2
    x = int(cx - side // 2)
    y = int(cy - side // 2)
    x = _clamp(x, 0, img_w - 1)
    y = _clamp(y, 0, img_h - 1)
    w = _clamp(side, 1, img_w - x)
    h = _clamp(side, 1, img_h - y)
    return (x, y, w, h)


def detect_identifier_v1(
    *,
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    cfg: Optional[IdentifierConfigV1] = None,
) -> Dict[str, Any]:
    """
    Extract identifier(8 digits) from aligned full-page image.

    meta requirements:
      meta["identifier"]["bubbles"] list with:
        - digit_index (1..8)
        - number (0..9)
        - center: {x(mm), y(mm)}
        - r(mm)

    return contract:
      {
        "identifier": "12345678" | None,
        "digits": [{"digit_index":1,"value":1,"status":"ok|blank|ambiguous","confidence":0.91,"marks":[...]}...],
        "confidence": 0.0~1.0,
        "status": "ok|ambiguous|blank|error"
      }
    """
    cfg = cfg or IdentifierConfigV1()

    if image_bgr is None or image_bgr.size == 0:
        return {"identifier": None, "digits": [], "confidence": 0.0, "status": "error"}

    # 대용량 이미지 리사이징 (처리 전)
    image_bgr, _ = resize_if_large(image_bgr, max_megapixels=4.0)
    
    h, w = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    ident = meta.get("identifier") or {}
    bubbles = list(ident.get("bubbles") or [])
    if not bubbles:
        return {"identifier": None, "digits": [], "confidence": 0.0, "status": "error"}

    scale: PageScale = build_page_scale_from_meta(meta=meta, image_size_px=(w, h))

    # group bubbles by digit_index
    by_digit: Dict[int, List[Dict[str, Any]]] = {}
    for b in bubbles:
        try:
            di = int(b.get("digit_index") or 0)
        except Exception:
            continue
        if di <= 0:
            continue
        by_digit.setdefault(di, []).append(b)

    digits_out: List[Dict[str, Any]] = []
    identifier_chars: List[str] = []
    status_rollup = "ok"
    confidences: List[float] = []

    for digit_index in sorted(by_digit.keys()):
        bs = by_digit[digit_index]

        marks: List[Dict[str, Any]] = []
        for b in bs:
            num = int(b.get("number") or 0)
            c = b.get("center") or {}
            r_mm = float(b.get("r") or 0.0)

            cx_mm = float(c.get("x") or 0.0)
            cy_mm = float(c.get("y") or 0.0)

            cx_px, cy_px = scale.mm_to_px_point(cx_mm, cy_mm)

            # radius: use average scale for robustness (mm->px)
            r_px_x = max(1, scale.mm_to_px_len_x(r_mm))
            r_px_y = max(1, scale.mm_to_px_len_y(r_mm))
            r_px = max(1, int(round((r_px_x + r_px_y) / 2.0)))

            bbox = _bubble_roi_bbox_px(
                center_px=(cx_px, cy_px),
                r_px=r_px,
                cfg=cfg,
                img_w=w,
                img_h=h,
            )
            roi = _crop(gray, bbox)
            fill = _fill_score(roi)

            marks.append(
                {
                    "number": int(num),
                    "fill": float(fill),
                    "center_px": {"x": int(cx_px), "y": int(cy_px)},
                    "roi_px": {"x": int(bbox[0]), "y": int(bbox[1]), "w": int(bbox[2]), "h": int(bbox[3])},
                }
            )

        marks_sorted = sorted(marks, key=lambda m: float(m.get("fill") or 0.0), reverse=True)
        top = marks_sorted[0] if marks_sorted else {"number": 0, "fill": 0.0}
        second = marks_sorted[1] if len(marks_sorted) > 1 else {"number": 0, "fill": 0.0}

        top_fill = float(top.get("fill") or 0.0)
        second_fill = float(second.get("fill") or 0.0)
        gap = float(top_fill - second_fill)

        if top_fill < cfg.blank_threshold:
            digits_out.append(
                {
                    "digit_index": int(digit_index),
                    "value": None,
                    "status": "blank",
                    "confidence": 0.0,
                    "marks": marks_sorted,
                }
            )
            identifier_chars.append("?")
            status_rollup = "blank" if status_rollup == "ok" else status_rollup
            continue

        if gap < cfg.conf_gap_threshold:
            digits_out.append(
                {
                    "digit_index": int(digit_index),
                    "value": int(top.get("number") or 0),
                    "status": "ambiguous",
                    "confidence": float(max(cfg.min_confidence, min(cfg.max_confidence, top_fill))),
                    "gap": float(gap),
                    "marks": marks_sorted,
                }
            )
            identifier_chars.append(str(int(top.get("number") or 0)))
            status_rollup = "ambiguous" if status_rollup in ("ok",) else status_rollup
            confidences.append(float(top_fill))
            continue

        # ok
        conf = float(max(cfg.min_confidence, min(cfg.max_confidence, top_fill)))
        digits_out.append(
            {
                "digit_index": int(digit_index),
                "value": int(top.get("number") or 0),
                "status": "ok",
                "confidence": conf,
                "gap": float(gap),
                "marks": marks_sorted,
            }
        )
        identifier_chars.append(str(int(top.get("number") or 0)))
        confidences.append(conf)

    # identifier validity: must be 8 digits all ok/ambiguous (blank이면 ? 포함)
    identifier = "".join(identifier_chars)
    if "?" in identifier:
        identifier_final: Optional[str] = None
    else:
        identifier_final = identifier

    # overall confidence: conservative (mean of digit conf where available)
    overall_conf = float(sum(confidences) / len(confidences)) if confidences else 0.0

    return {
        "identifier": identifier_final,
        "raw_identifier": identifier,  # '?' 포함 가능 (운영 디버그/리트라이용)
        "digits": digits_out,
        "confidence": float(max(0.0, min(1.0, overall_conf))),
        "status": status_rollup,
    }
