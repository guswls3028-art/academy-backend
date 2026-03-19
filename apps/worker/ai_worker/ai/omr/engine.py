# apps/worker/ai_worker/ai/omr/engine.py
"""
OMR 객관식 답안 검출 엔진 v7

omr-sheet.html SSOT 레이아웃 기준.
meta_generator.py의 좌표를 사용하여 스캔 이미지에서 마킹된 버블을 감지한다.

원리:
1. 워프된 A4 landscape 이미지를 받는다
2. 메타의 mm 좌표를 px로 변환한다
3. 각 문항의 각 버블 ROI에서 fill ratio를 측정한다
4. 가장 높은 fill의 버블을 정답으로 판정한다
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.omr.meta_px import build_page_scale_from_meta, PageScale
from apps.worker.ai_worker.ai.omr.types import OMRAnswerV1


@dataclass(frozen=True)
class AnswerDetectConfig:
    """객관식 버블 감지 설정."""
    # ROI 확장 계수 (버블 반지름 × k)
    roi_expand_k: float = 1.55
    # blank 판단: 최고 fill이 이 값 미만이면 blank
    blank_threshold: float = 0.060
    # ambiguous 판단: top-2 gap이 이 값 미만이면 ambiguous
    conf_gap_threshold: float = 0.055
    # 이진화 threshold
    binarize_threshold: int = 140


def detect_omr_answers_v7(
    *,
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    config: Optional[AnswerDetectConfig] = None,
) -> List[OMRAnswerV1]:
    """
    워프된 A4 이미지에서 객관식 답안을 감지한다.

    Args:
        image_bgr: 워프된 BGR 이미지 (전체 페이지 = 전체 이미지)
        meta: build_omr_meta() 결과
        config: 감지 설정

    Returns:
        문항별 OMRAnswerV1 리스트
    """
    if config is None:
        config = AnswerDetectConfig()

    scale = build_page_scale_from_meta(
        meta=meta,
        image_size_px=(image_bgr.shape[1], image_bgr.shape[0]),
    )

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, config.binarize_threshold, 255, cv2.THRESH_BINARY_INV)

    results: List[OMRAnswerV1] = []

    for q in meta.get("questions", []):
        q_num = int(q.get("question_number", 0))
        choices = q.get("choices", [])
        if not choices:
            continue

        try:
            answer = _detect_single_question(
                binary=binary,
                scale=scale,
                q_num=q_num,
                choices=choices,
                config=config,
                img_shape=image_bgr.shape,
            )
            results.append(answer)
        except Exception:
            results.append(OMRAnswerV1(
                version="v7",
                question_id=q_num,
                detected=[],
                marking="blank",
                confidence=0.0,
                status="error",
            ))

    return results


def _detect_single_question(
    *,
    binary: np.ndarray,
    scale: PageScale,
    q_num: int,
    choices: List[Dict[str, Any]],
    config: AnswerDetectConfig,
    img_shape: Tuple[int, ...],
) -> OMRAnswerV1:
    """단일 문항의 버블 fill ratio 측정 및 판정."""
    img_h, img_w = img_shape[:2]
    fills: List[Tuple[str, float]] = []

    for ch in choices:
        label = str(ch.get("label", ""))
        center = ch.get("center", {})
        cx_mm = float(center.get("x", 0))
        cy_mm = float(center.get("y", 0))
        rx_mm = float(ch.get("radius_x", 1.8))
        ry_mm = float(ch.get("radius_y", 2.6))

        cx_px, cy_px = scale.mm_to_px_point(cx_mm, cy_mm)
        rx_px = max(1, int(round(rx_mm * config.roi_expand_k * scale.sx)))
        ry_px = max(1, int(round(ry_mm * config.roi_expand_k * scale.sy)))

        x1 = max(0, cx_px - rx_px)
        y1 = max(0, cy_px - ry_px)
        x2 = min(img_w, cx_px + rx_px)
        y2 = min(img_h, cy_px + ry_px)

        roi = binary[y1:y2, x1:x2]
        if roi.size == 0:
            fills.append((label, 0.0))
            continue

        fill = float(np.count_nonzero(roi)) / roi.size
        fills.append((label, fill))

    if not fills:
        return OMRAnswerV1(
            version="v7", question_id=q_num,
            detected=[], marking="blank",
            confidence=0.0, status="error",
        )

    # 정렬: fill 높은 순
    fills.sort(key=lambda x: x[1], reverse=True)
    top_label, top_fill = fills[0]
    second_fill = fills[1][1] if len(fills) > 1 else 0.0
    gap = top_fill - second_fill

    # 판정
    if top_fill < config.blank_threshold:
        return OMRAnswerV1(
            version="v7", question_id=q_num,
            detected=[], marking="blank",
            confidence=0.0, status="blank",
            raw={"fills": {l: round(f, 4) for l, f in fills}},
        )

    if gap < config.conf_gap_threshold:
        # 복수 마킹 가능성
        marked = [l for l, f in fills if f >= config.blank_threshold]
        return OMRAnswerV1(
            version="v7", question_id=q_num,
            detected=marked,
            marking="multi" if len(marked) > 1 else "single",
            confidence=round(gap, 4),
            status="ambiguous",
            raw={"fills": {l: round(f, 4) for l, f in fills}},
        )

    confidence = min(1.0, gap / 0.3)  # gap 0.3 이상이면 confidence 1.0

    return OMRAnswerV1(
        version="v7", question_id=q_num,
        detected=[top_label],
        marking="single",
        confidence=round(confidence, 4),
        status="ok",
        raw={"fills": {l: round(f, 4) for l, f in fills}},
    )
