"""paper_type → source_type 자동 derive 헬퍼 (Stage 6.7-policy Phase 1, 2026-05-09).

학원장 directive 2026-05-09: "내부 알고리즘으로 알아서 구분".
업로드 시점엔 paper_type 분류가 아직 없으므로 frontend 가 filename 휴리스틱으로
잠정 추천. 분석 완료 후 callback 에서 paper_type 신호로 보정.

원칙:
- 사용자가 명시 선택한 값(meta.source_type_origin == "user")은 보호.
- 이미 specific 한 값(student_exam_photo / school_exam_pdf / ...)은 보호.
- 모호한 default("" 또는 "other") 만 paper_type 으로 보정.
- 매핑은 confidence 100% 인 것만. 애매하면 None 반환 = 유지.

산정 결정 (분포 vs primary):
- paper_type_summary.primary 만 사용 (가장 빈도 높은 페이지 paper_type).
- 분포 cross-tab 은 별도 학습 데이터로 누적, 본 derive 는 단일 신호.
"""
from __future__ import annotations

from typing import Optional


# 명시 선택값으로 간주되는 source_type — 이 값들은 보정 대상 X.
_SPECIFIC_SOURCE_TYPES = frozenset({
    "student_exam_photo",
    "school_exam_pdf",
    "commercial_workbook",
    "academy_workbook",
    "explanation",
    "answer_key",
})


def derive_source_type_from_paper_type(
    paper_type_primary: Optional[str],
    current_source_type: Optional[str],
) -> Optional[str]:
    """paper_type primary 신호로 source_type 보정 추천.

    Args:
        paper_type_primary: paper_type_summary.primary (predicted page type)
        current_source_type: 현재 doc.meta["source_type"] (filename heuristic 또는 user 입력)

    Returns:
        새 source_type 추천. 변경 불필요 시 None.

    매핑 (confidence 100% 만):
    - student_answer_photo → student_exam_photo (학생 폰사진은 100%)
    - scan_single/dual + cur in ("", "other") → school_exam_pdf
    - side_notes + cur in ("", "other") → academy_workbook

    유지 (None 반환):
    - clean_pdf_*: 학교/시판/학원 구분 불가 (Phase 2 OCR 휴리스틱 필요)
    - quadrant: 학교 시험지 가능성 높지만 표본 부족
    - non_question: 표지/목차/해설 다양 — 단일 매핑 위험
    - unknown: 신호 부족
    """
    if not paper_type_primary:
        return None
    pt = paper_type_primary.lower().strip()
    cur = (current_source_type or "").lower().strip()

    # 1. 학생 답안지 폰사진 — 100% 매핑.
    if pt == "student_answer_photo":
        if cur == "student_exam_photo":
            return None  # 이미 정합
        return "student_exam_photo"

    # 사용자/filename 휴리스틱이 specific 값을 이미 골랐으면 보호.
    if cur in _SPECIFIC_SOURCE_TYPES:
        return None

    # 2. 스캔본 → 학교 시험지 PDF default (학원 워크북도 가능하나 학교 더 흔함).
    if pt in ("scan_single", "scan_dual"):
        return "school_exam_pdf"

    # 3. 학습자료 본문 → 학원 자체 워크북.
    if pt == "side_notes":
        return "academy_workbook"

    # 4. 기타 (clean_pdf_*, quadrant, non_question, unknown) — 보정 X.
    return None
