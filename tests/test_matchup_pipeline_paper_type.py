"""매치업 pipeline의 paper_type 통합 + 번호 검증 단위 테스트.

목적:
- _verify_problem_numbers: number↔content mismatch 차단 (C10 결함)
- _aggregate_paper_types: doc 단위 분포 집계 + 경고 산출
- is_low_confidence_doc 분기: STUDENT_ANSWER_PHOTO majority → page-as-problem 폴백
"""
from __future__ import annotations

from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
    _aggregate_paper_types,
    _verify_problem_numbers,
)


# ── 1. _verify_problem_numbers ──

def test_verify_number_match_no_flag():
    """text 첫 줄 anchor와 q.number 일치 → meta_extra 변경 없음."""
    questions = [
        {"number": 1, "bbox": [0, 0, 100, 100], "text": "1. 다음 중 옳은 것은?\n① A ② B"},
        {"number": 2, "bbox": [0, 0, 100, 100], "text": "2) 그림은 어떤 분자의\n① 가 ② 나"},
    ]
    _verify_problem_numbers(questions)
    for q in questions:
        assert (q.get("meta_extra") or {}).get("number_mismatch") is None


def test_verify_number_mismatch_flagged():
    """text 첫 줄 anchor가 q.number와 다름 → meta_extra["number_mismatch"] 기록.

    C10 결함 회귀: DB number=3인데 image의 본문 번호가 5로 잡힌 케이스.
    """
    questions = [
        {"number": 3, "bbox": [0, 0, 100, 100], "text": "5. 다음 중 옳은 것은?\n① A ② B"},
    ]
    _verify_problem_numbers(questions)
    flag = questions[0]["meta_extra"]["number_mismatch"]
    assert flag == {"db": 3, "ocr": 5}


def test_verify_skips_page_fallback_problems():
    """bbox=None (페이지 폴백 problem)은 검증 대상 아님 — 페이지 전체 텍스트라 부적합."""
    questions = [
        {"number": 1, "bbox": None, "text": "5. 다음... 6. 그림..."},
    ]
    _verify_problem_numbers(questions)
    assert (questions[0].get("meta_extra") or {}).get("number_mismatch") is None


def test_verify_skips_when_no_anchor_in_text():
    """text 첫 줄에 anchor 없으면 검증 스킵 (false negative 안전망)."""
    questions = [
        {"number": 1, "bbox": [0, 0, 100, 100], "text": "다음 중 옳은 것은?\n① A"},
    ]
    _verify_problem_numbers(questions)
    assert (questions[0].get("meta_extra") or {}).get("number_mismatch") is None


def test_verify_skips_empty_text():
    """text 비어있으면 검증 스킵."""
    questions = [
        {"number": 1, "bbox": [0, 0, 100, 100], "text": ""},
        {"number": 2, "bbox": [0, 0, 100, 100], "text": None},
    ]
    _verify_problem_numbers(questions)
    for q in questions:
        assert (q.get("meta_extra") or {}).get("number_mismatch") is None


def test_verify_section_offset_matches():
    """서답형 [서답형 1] = 101 — section offset도 검증 통과."""
    questions = [
        {"number": 101, "bbox": [0, 0, 100, 100], "text": "[서술형 1] 다음 글을 읽고\n물음에 답하시오"},
    ]
    _verify_problem_numbers(questions)
    assert (questions[0].get("meta_extra") or {}).get("number_mismatch") is None


# ── 2. _aggregate_paper_types ──

def test_aggregate_empty_pages():
    """빈 페이지 리스트 → unknown primary."""
    summary = _aggregate_paper_types([])
    assert summary["primary"] == "unknown"
    assert summary["low_confidence_ratio"] == 0.0
    assert summary["warnings"] == []


def test_aggregate_clean_pdf_majority():
    """clean_pdf_single 다수 + 표지 1장 → primary=clean_pdf_single, 경고 없음."""
    pages = [
        {"paper_type": "clean_pdf_single"},
        {"paper_type": "clean_pdf_single"},
        {"paper_type": "clean_pdf_single"},
        {"paper_type": "non_question"},  # 표지
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["primary"] == "clean_pdf_single"
    assert summary["low_confidence_ratio"] == 0.0
    assert summary["warnings"] == []


def test_aggregate_student_answer_photo_warning():
    """학생 답안지 폰사진 1장만 있어도 student_answer_photo_detected 경고."""
    pages = [
        {"paper_type": "scan_dual"},
        {"paper_type": "student_answer_photo"},
        {"paper_type": "scan_dual"},
        {"paper_type": "scan_dual"},
    ]
    summary = _aggregate_paper_types(pages)
    assert "student_answer_photo_detected" in summary["warnings"]


def test_aggregate_low_confidence_majority():
    """STUDENT_ANSWER_PHOTO가 30% 이상이면 low_confidence_source_majority 경고."""
    pages = [
        {"paper_type": "student_answer_photo"},
        {"paper_type": "student_answer_photo"},
        {"paper_type": "scan_dual"},
        {"paper_type": "scan_dual"},
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["low_confidence_ratio"] == 0.5
    assert "low_confidence_source_majority" in summary["warnings"]
    assert "student_answer_photo_detected" in summary["warnings"]


def test_aggregate_unknown_only():
    """모두 unknown → low_confidence_ratio = 1.0."""
    pages = [
        {"paper_type": "unknown"},
        {"paper_type": "unknown"},
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["low_confidence_ratio"] == 1.0
    assert summary["primary"] == "unknown"


def test_aggregate_distribution_counts():
    """distribution이 정확한 카운트 반환."""
    pages = [
        {"paper_type": "clean_pdf_dual"},
        {"paper_type": "clean_pdf_dual"},
        {"paper_type": "non_question"},
        {"paper_type": "quadrant"},
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["distribution"] == {
        "clean_pdf_dual": 2,
        "non_question": 1,
        "quadrant": 1,
    }


def test_aggregate_missing_paper_type_defaults_unknown():
    """paper_type 키 없는 페이지는 unknown으로 처리."""
    pages = [
        {"paper_type": "clean_pdf_single"},
        {},  # 누락
    ]
    summary = _aggregate_paper_types(pages)
    assert summary["distribution"].get("unknown") == 1


def test_aggregate_non_question_majority_warning():
    """비-문항 페이지가 50% 이상 + 4페이지 이상 → non_question_majority 경고."""
    pages = [
        {"paper_type": "non_question"},
        {"paper_type": "non_question"},
        {"paper_type": "non_question"},
        {"paper_type": "clean_pdf_single"},
    ]
    summary = _aggregate_paper_types(pages)
    assert "non_question_majority" in summary["warnings"]


def test_aggregate_non_question_minor_no_warning():
    """비-문항 페이지가 짧은 doc (3 페이지 이하)에서는 non_question_majority 경고 안 함."""
    pages = [
        {"paper_type": "non_question"},
        {"paper_type": "non_question"},
        {"paper_type": "clean_pdf_single"},
    ]
    summary = _aggregate_paper_types(pages)
    assert "non_question_majority" not in summary["warnings"]
