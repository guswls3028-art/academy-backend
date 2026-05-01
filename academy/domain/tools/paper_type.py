"""PaperType 분류기 SSOT — 매치업 splitter dispatcher 진입점.

이전 상태: split_questions 안에 _detect_column_layout / _detect_quad_layout
휴리스틱이 숨겨져 있어, OCR/이미지 경로에서 텍스트 블록 분포가 부족하면
dual/quad 미인식 → strip cut. 폰사진/스캔본에서 운영 결함 다수 발생.

현재: classify_paper_type()이 (1) 텍스트 분포 휴리스틱 + (2) 픽셀 기반 백업 +
(3) 외부 신호(handwriting/has_embedded_text)를 종합해 PaperType을 결정.
splitter는 PaperTypeResult를 받아 휴리스틱을 우회하고 강제 분기한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class PaperType(str, Enum):
    """매치업 시험지 페이지 유형."""

    # 텍스트 추출 가능한 PDF — 단일 컬럼 객관식
    CLEAN_PDF_SINGLE = "clean_pdf_single"
    # 텍스트 추출 가능한 PDF — 2단 컬럼 (객관식 좌/우 분포)
    CLEAN_PDF_DUAL = "clean_pdf_dual"
    # 4분할(2x2) 시험지 — 한 페이지에 4문항 grid 배치
    QUADRANT = "quadrant"
    # 스캔본 시험지 (OCR 적용) — 단일 컬럼
    SCAN_SINGLE = "scan_single"
    # 스캔본 시험지 (OCR 적용) — 2단 컬럼
    SCAN_DUAL = "scan_dual"
    # 학생 답안지 폰사진 — 학생 필기 침범 / perspective / 회전.
    # 자동분리 신뢰도 낮음. 사용자 경고 + 부분 page-as-problem 적합.
    STUDENT_ANSWER_PHOTO = "student_answer_photo"
    # 학습자료 본문 — Step N. / 항목번호가 본문에 다수 등장. over-extraction 위험.
    SIDE_NOTES = "side_notes"
    # 비-문항 페이지 — 표지 / 정답지 / 해설지 / 목차 / 디자인 표지 등.
    NON_QUESTION = "non_question"
    # 분류 불명 — splitter가 기존 휴리스틱 사용 (호환성).
    UNKNOWN = "unknown"


@dataclass
class PaperTypeResult:
    """분류 결과 + 파생 boolean + 디버그 신호."""

    paper_type: PaperType
    confidence: float
    is_dual_column: bool
    is_quadrant: bool
    is_handwriting_present: bool
    has_embedded_text: bool
    debug: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_non_question(self) -> bool:
        return self.paper_type is PaperType.NON_QUESTION

    @property
    def is_low_confidence_source(self) -> bool:
        """자동분리 신뢰도가 낮은 source. 사용자 경고 후보."""
        return self.paper_type in (
            PaperType.STUDENT_ANSWER_PHOTO,
            PaperType.UNKNOWN,
        )


def classify_paper_type(
    *,
    text_blocks: Optional[List[Any]] = None,  # SplitterTextBlock 리스트
    image_path: Optional[str] = None,
    page_width: float = 0.0,
    page_height: float = 0.0,
    has_embedded_text: bool = False,
    handwriting_score: Optional[float] = None,
) -> PaperTypeResult:
    """페이지 유형 분류.

    우선순위:
      1. NON_QUESTION (text_blocks로 판정)
      2. STUDENT_ANSWER_PHOTO (handwriting_score 강한 신호 + 스캔본)
      3. QUADRANT (text 휴리스틱)
      4. DUAL (text 휴리스틱 또는 픽셀 백업)
      5. SINGLE default (스캔본/PDF 구분)

    text_blocks와 image_path 둘 다 None이면 UNKNOWN.
    """
    from academy.domain.tools.question_splitter import (
        _detect_column_layout,
        _detect_quad_layout,
        is_non_question_page,
    )

    debug: Dict[str, Any] = {"has_embedded_text": has_embedded_text}

    # 입력 신호 부재 → UNKNOWN
    if text_blocks is None and image_path is None:
        return PaperTypeResult(
            paper_type=PaperType.UNKNOWN,
            confidence=0.0,
            is_dual_column=False,
            is_quadrant=False,
            is_handwriting_present=False,
            has_embedded_text=has_embedded_text,
            debug=debug,
        )

    # 1. 비-문항 페이지
    if text_blocks:
        try:
            if is_non_question_page(text_blocks):
                return PaperTypeResult(
                    paper_type=PaperType.NON_QUESTION,
                    confidence=0.9,
                    is_dual_column=False,
                    is_quadrant=False,
                    is_handwriting_present=False,
                    has_embedded_text=has_embedded_text,
                    debug={**debug, "reason": "is_non_question_page"},
                )
        except Exception as e:  # noqa: BLE001
            debug["non_question_check_error"] = str(e)

    # 2. 학생 답안지 폰사진
    # writing_score는 인쇄 텍스트도 높게 나오므로 단독 사용 불가.
    # 신뢰 조건: 스캔본(=embedded text 없음) + 매우 높은 writing_score.
    is_hw_present = handwriting_score is not None and handwriting_score >= 0.6
    if (
        handwriting_score is not None
        and handwriting_score >= 0.78
        and not has_embedded_text
    ):
        return PaperTypeResult(
            paper_type=PaperType.STUDENT_ANSWER_PHOTO,
            confidence=float(handwriting_score),
            is_dual_column=False,
            is_quadrant=False,
            is_handwriting_present=True,
            has_embedded_text=has_embedded_text,
            debug={**debug, "handwriting_score": handwriting_score},
        )

    # 3. 4-quadrant
    is_quad = False
    if text_blocks and page_width > 0 and page_height > 0:
        try:
            is_quad = _detect_quad_layout(text_blocks, page_width, page_height)
        except Exception as e:  # noqa: BLE001
            debug["quad_check_error"] = str(e)
    debug["is_quad_text"] = is_quad

    if is_quad:
        return PaperTypeResult(
            paper_type=PaperType.QUADRANT,
            confidence=0.85,
            is_dual_column=False,
            is_quadrant=True,
            is_handwriting_present=is_hw_present,
            has_embedded_text=has_embedded_text,
            debug=debug,
        )

    # 4. Dual-column — 텍스트 휴리스틱 + 픽셀 백업
    is_dual_text = False
    if text_blocks and page_width > 0:
        try:
            is_dual_text = _detect_column_layout(text_blocks, page_width)
        except Exception as e:  # noqa: BLE001
            debug["dual_text_error"] = str(e)
    debug["is_dual_text"] = is_dual_text

    is_dual_pixel = False
    if not is_dual_text and image_path:
        is_dual_pixel = _detect_dual_column_from_pixels(image_path)
    debug["is_dual_pixel"] = is_dual_pixel

    is_dual = is_dual_text or is_dual_pixel

    if is_dual:
        ptype = (
            PaperType.CLEAN_PDF_DUAL if has_embedded_text else PaperType.SCAN_DUAL
        )
        confidence = 0.85 if is_dual_text else 0.65
        return PaperTypeResult(
            paper_type=ptype,
            confidence=confidence,
            is_dual_column=True,
            is_quadrant=False,
            is_handwriting_present=is_hw_present,
            has_embedded_text=has_embedded_text,
            debug=debug,
        )

    # 5. Single-column default
    ptype = (
        PaperType.CLEAN_PDF_SINGLE if has_embedded_text else PaperType.SCAN_SINGLE
    )
    return PaperTypeResult(
        paper_type=ptype,
        confidence=0.7,
        is_dual_column=False,
        is_quadrant=False,
        is_handwriting_present=is_hw_present,
        has_embedded_text=has_embedded_text,
        debug=debug,
    )


def _detect_dual_column_from_pixels(image_path: str) -> bool:
    """이미지 픽셀 기반 dual-col 백업 감지.

    OCR 블록 분포 휴리스틱이 폰사진/저해상도 스캔본에서 dual-col을 못 잡는
    케이스의 백업. OpenCV projection profile을 재사용.
    """
    try:
        from academy.adapters.ai.detection.segment_opencv import (
            detect_dual_column_pixel,
        )

        return detect_dual_column_pixel(image_path)
    except Exception:
        return False
