# PATH: apps/domains/matchup/source_types.py
"""매치업 자료 유형 SSOT.

업로드 시점에 학원장이 자료 유형을 명시 → worker dispatcher가 strategy 분기.
"학원자료 한 알고리즘으로 처리" 시 3일째 같은 곳 보수하는 결함의 본질을 제거.

이전 (2값): "reference" / "test"
현재 (7값): student_exam_photo / school_exam_pdf / commercial_workbook /
            academy_workbook / explanation / answer_key / other

동기 (2026-05-02 학원장 directive):
- 시판 교재 통째 page-as-problem
- 박철T 워크북 over-split (단원 번호를 문항 anchor로 오인)
- 학생 답안지 폰사진 C10 mismatch
- 4-quadrant strip 분할
→ 모두 source 정보 부족이 본질. 알고리즘 X 라우터 부재.
"""
from __future__ import annotations

from typing import Final, Literal


SourceType = Literal[
    "student_exam_photo",   # 학생 시험지/답안지 사진 (자동분할 보수, 수동 crop 우선)
    "school_exam_pdf",      # 학교 기출/시험지 PDF (현 anchor splitter 적합)
    "commercial_workbook",  # 시판 교재 (cover/index/explanation/answer_key skip + page/block)
    "academy_workbook",     # 학원 자체 워크북 (anchor + 보기/문항패턴 검증, over-split 방지)
    "explanation",          # 해설지 (매치업 후보 인덱싱 X)
    "answer_key",           # 답안지 (매치업 후보 제외)
    "other",                # 기타 (보수적 page-as-problem)
]


# 7-value 정식 enum
SOURCE_TYPES: Final[tuple[str, ...]] = (
    "student_exam_photo",
    "school_exam_pdf",
    "commercial_workbook",
    "academy_workbook",
    "explanation",
    "answer_key",
    "other",
)


# 한국어 레이블 (어드민 UI / 검수 화면 표시용)
SOURCE_TYPE_LABELS: Final[dict[str, str]] = {
    "student_exam_photo":  "학생 시험지/답안지 사진",
    "school_exam_pdf":     "학교 기출/시험지 PDF",
    "commercial_workbook": "시판 교재",
    "academy_workbook":    "학원 자체 워크북",
    "explanation":         "해설지",
    "answer_key":          "답안지",
    "other":               "기타",
}


# 매치업 후보 인덱싱 대상 — 시험 문제와 유사도 비교에 사용할 자료들.
# explanation/answer_key는 검색 결과에 노출되면 노이즈 → 인덱싱 X.
INDEXABLE_SOURCE_TYPES: Final[frozenset[str]] = frozenset({
    "student_exam_photo",
    "school_exam_pdf",
    "commercial_workbook",
    "academy_workbook",
    "other",
})


# Legacy 2-value (services/views에 잔존) → 7-value 안전한 default 매핑.
# 자동 backfill용 (사용자가 명시적으로 다시 지정하기 전까지의 잠정값).
LEGACY_INTENT_TO_SOURCE_TYPE: Final[dict[str, str]] = {
    "test":      "school_exam_pdf",   # 시험지는 학교 PDF로 가정 (학생 사진은 별도 식별 어려움)
    "exam_sheet": "school_exam_pdf",  # services._handle ... 에서 derived 값
    "reference": "academy_workbook",  # frontend intentToSourceType와 동일한 legacy 호환 default
}


def normalize_source_type(value: str | None) -> str:
    """입력값을 정식 7-value source_type으로 정규화.

    수용 입력:
    - 7-value enum 그대로 → 그대로 반환
    - legacy 2-value (test/reference/exam_sheet) → LEGACY_INTENT_TO_SOURCE_TYPE 매핑
    - 빈 값/미인식 → "other" (보수적 default)

    NEVER raise — 항상 안전한 값 반환.
    """
    if not value:
        return "other"
    v = value.strip().lower()
    if v in SOURCE_TYPES:
        return v
    if v in LEGACY_INTENT_TO_SOURCE_TYPE:
        return LEGACY_INTENT_TO_SOURCE_TYPE[v]
    return "other"


def resolve_upload_source_type(source_type: str | None, intent: str | None) -> str:
    """Resolve upload/promotion input with 7-value source_type precedence.

    Frontend uploads may include both `source_type` and legacy `intent`.
    The 7-value source_type is the routing signal; legacy intent is only a
    fallback for older clients.
    """
    if source_type and source_type.strip():
        return normalize_source_type(source_type)
    return normalize_source_type(intent)


def is_valid_source_type(value: str | None) -> bool:
    """값이 7-value enum에 속하는지 (legacy 매핑 X, 그대로 일치)."""
    return bool(value) and value in SOURCE_TYPES


def is_indexable(source_type: str | None) -> bool:
    """매치업 vector 검색 인덱스 대상인지. explanation/answer_key는 X."""
    return normalize_source_type(source_type) in INDEXABLE_SOURCE_TYPES
