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
import re
from typing import List, Optional, Tuple

import cv2  # type: ignore

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


# Vision API document_text_detection은 같은 y-line 위에 있는 좌/우 컬럼 텍스트를 한 줄로
# 묶어서 반환하는 케이스가 있다. 이때 "5. 그림 (가) ... 7. 그림은 생태계..." 처럼 한 line
# 안에 두 문항 anchor가 동거하면 split_questions가 첫 anchor만 잡고 두 번째는 잃는다.
# 이를 위해 OCR 결과 블록에서 multi-anchor line을 제2 anchor 위치 기준으로 가상 분할.
# 운영 doc#329 page 1: "5. ... 7. ..." 한 줄로 묶여 Q7 anchor 손실 → 본 fix로 복구.
_ANCHOR_INLINE_PATTERN = re.compile(r"\b(\d{1,3})\s*[.)](?=\s|[가-힣A-Za-z(<【\[\"'“‘])")


# 섹션 헤더 키워드 — 이 키워드가 블록 안에 있으면 multi-anchor 분할을 적용하지 않는다.
# split하면 "서 답형 1. ..." 의 "1." 부분이 sub-block으로 떨어져 선택형 #1 anchor로
# 오인식되며, 실제 서답형(=101)으로 매핑되지 않는다.
_SECTION_KEYWORDS = ("서답", "서술", "논답", "논술", "단답", "단술", "약술", "약답")


def _split_multi_anchor_blocks(blocks: List["SplitterTextBlock"]) -> List["SplitterTextBlock"]:  # type: ignore[name-defined]
    """텍스트 블록에서 두 개 이상의 선택형 anchor 패턴이 동거하면 가상 sub-block으로 분할.

    Vision OCR이 좌/우 컬럼 같은 y-line을 한 line으로 묶을 때, 한 블록 안에 "5. ... 7. ..."
    처럼 두 anchor가 동거하여 split_questions가 첫 anchor만 잡고 두 번째를 잃는 결함 보정.

    분할은 텍스트 내 anchor의 글자 위치에 비례하여 x 좌표를 잘라 새 SplitterTextBlock을 만든다.

    예외: 블록 안에 서답형/서술형/논술형/단답형/약술형 키워드가 있으면 split하지 않는다.
    이런 블록은 question_splitter가 _SECTION_PATTERN으로 직접 처리해야 한다.
    """
    from academy.domain.tools.question_splitter import TextBlock as SplitterTextBlock

    out: List[SplitterTextBlock] = []
    for b in blocks:
        text = b.text or ""
        # 길이가 너무 짧으면 anchor가 1개라도 분할 의미 없음
        if len(text) < 20:
            out.append(b)
            continue
        # 섹션 헤더 블록은 split 금지
        normalized = re.sub(r"\s+", "", text)
        if any(kw in normalized for kw in _SECTION_KEYWORDS):
            out.append(b)
            continue
        matches = list(_ANCHOR_INLINE_PATTERN.finditer(text))
        # 첫 anchor가 텍스트 맨 앞이 아닐 수도 있음(블록 prefix 노이즈). 2개 이상일 때만 split.
        if len(matches) < 2:
            out.append(b)
            continue
        # 두 anchor 모두 1~60 범위(선택형) 여야 split — 보기 ① 또는 본문 숫자 false positive 차단.
        nums = []
        for m in matches:
            try:
                n = int(m.group(1))
                if 1 <= n <= 60:
                    nums.append(n)
            except ValueError:
                continue
        if len(nums) < 2:
            out.append(b)
            continue
        # 각 anchor 시작점을 기준으로 sub-block 만들기
        bw = b.x1 - b.x0
        if bw <= 0 or len(text) == 0:
            out.append(b)
            continue
        for i, m in enumerate(matches):
            sub_start = m.start()
            sub_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sub_text = text[sub_start:sub_end].strip()
            if not sub_text:
                continue
            # 글자 위치 비율로 x 좌표 분할
            ratio_start = sub_start / len(text)
            ratio_end = sub_end / len(text)
            new_x0 = b.x0 + bw * ratio_start
            new_x1 = b.x0 + bw * ratio_end
            out.append(SplitterTextBlock(
                text=sub_text,
                x0=float(new_x0),
                y0=b.y0,
                x1=float(new_x1),
                y1=b.y1,
            ))
    return out


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
        from academy.adapters.ai.ocr.google import google_ocr_blocks
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
    # 예외는 상위(dispatcher)로 전파 — dispatcher가 OpenCV fallback 판단
    ocr_blocks = google_ocr_blocks(image_path)

    if not ocr_blocks:
        logger.info("OCR_SEGMENT_EMPTY | path=%s", image_path)
        return []

    # SplitterTextBlock 변환 (이미 픽셀 좌표계)
    splitter_blocks = [
        SplitterTextBlock(text=b.text, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
        for b in ocr_blocks
    ]

    # 멀티 anchor 라인 분할 — Vision OCR이 좌/우 컬럼을 한 줄로 묶는 경우 대비.
    # 분할 후에도 is_non_question_page 판정은 동일하게 작동하도록 splitter_blocks 갱신.
    splitter_blocks = _split_multi_anchor_blocks(splitter_blocks)

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
