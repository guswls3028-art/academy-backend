# apps/worker/ai/detection/segment_ocr.py
"""
OCR 기반 문항 세그멘테이션 — 스캔본 시험지(임베디드 텍스트 없음)용.

흐름:
  1. Google Vision OCR → 픽셀 좌표 텍스트 블록 추출
  2. 기존 question_splitter(2단/그림 로직)에 공급
  3. QuestionRegion → (x, y, w, h) BBox로 변환

텍스트 기반 분할과 OpenCV fallback 사이의 중간 경로.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import cv2  # type: ignore

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


def segment_questions_ocr(image_path: str) -> List[BBox]:
    """
    이미지에 OCR을 적용하여 문항 영역을 검출.

    Returns:
        [(x, y, w, h), ...] — 문항 번호 검출 실패 시 빈 리스트.
    """
    regions = segment_questions_ocr_regions(image_path)
    return [
        (
            int(r[0]),
            int(r[1]),
            int(r[2] - r[0]),
            int(r[3] - r[1]),
        )
        for r in regions
    ]


def segment_questions_ocr_regions(image_path: str) -> List[Tuple[float, float, float, float, int]]:
    """
    OCR 기반 문항 영역 검출 — 번호 정보 포함 반환.

    Returns:
        [(x0, y0, x1, y1, question_number), ...]
    """
    try:
        from apps.worker.ai_worker.ai.ocr.google import google_ocr_blocks
    except ImportError as e:
        logger.warning("OCR_SEGMENT_IMPORT_FAIL | %s", e)
        return []

    try:
        from academy.domain.tools.question_splitter import (
            is_non_question_page,
            split_questions,
            TextBlock as SplitterTextBlock,
        )
    except ImportError as e:
        logger.warning("OCR_SEGMENT_SPLITTER_IMPORT_FAIL | %s", e)
        return []

    # 이미지 크기 확인
    img = cv2.imread(image_path)
    if img is None:
        logger.warning("OCR_SEGMENT_IMG_READ_FAIL | path=%s", image_path)
        return []
    h_img, w_img = img.shape[:2]

    # Google Vision OCR — bbox 포함
    try:
        ocr_blocks = google_ocr_blocks(image_path)
    except Exception as e:
        logger.warning("OCR_SEGMENT_VISION_FAIL | path=%s | error=%s", image_path, e)
        return []

    if not ocr_blocks:
        logger.info("OCR_SEGMENT_EMPTY | path=%s", image_path)
        return []

    # SplitterTextBlock 변환 (이미 픽셀 좌표계)
    splitter_blocks = [
        SplitterTextBlock(text=b.text, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
        for b in ocr_blocks
    ]

    # 비문항 페이지(정답지/해설지/표지 등) 스킵
    if is_non_question_page(splitter_blocks):
        logger.info("OCR_SEGMENT_SKIP_NON_QUESTION | path=%s", image_path)
        return []

    # question_splitter 호출 — 픽셀 좌표계에서 동작
    regions = split_questions(
        text_blocks=splitter_blocks,
        page_width=float(w_img),
        page_height=float(h_img),
        page_index=0,
    )

    if not regions:
        logger.info(
            "OCR_SEGMENT_NO_REGIONS | path=%s | blocks=%d",
            image_path, len(ocr_blocks),
        )
        return []

    logger.info(
        "OCR_SEGMENT_OK | path=%s | blocks=%d | regions=%d | nums=%s",
        image_path, len(ocr_blocks), len(regions),
        [r.number for r in regions],
    )

    return [
        (r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3], r.number)
        for r in regions
    ]


def is_ocr_available() -> bool:
    """
    Vision API 크레덴셜이 설정되어 있는지 확인.
    GOOGLE_CREDENTIALS_JSON 또는 GOOGLE_APPLICATION_CREDENTIALS 둘 중 하나.
    """
    import os
    return bool(
        os.getenv("GOOGLE_CREDENTIALS_JSON")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
