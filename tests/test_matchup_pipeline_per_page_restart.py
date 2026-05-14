"""matchup_pipeline._boxes_to_questions per-page-restart counter mode regression.

운영 결함 (doc 768 73 페이지 워크북):
- splitter 가 페이지별 anchor 262 개 검출 (validate fix 후)
- _boxes_to_questions 가 segment number 그대로 사용 → cross-page seen_numbers 충돌로
  27 개로 drop (운영 실측, 2026-05-14)
- 학원장 manual_create 부담 = under-cut 본질

본 테스트:
1. 워크북 패턴 (per-page-restart) — 모든 box 보존, counter mode
2. 시험지 패턴 (continuous) — segment number 그대로 사용 (회귀 방지)
3. 워크북 모드에서 local_number 메타 보존 (학원장 검수 시 참조)
"""
from __future__ import annotations

from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
    _boxes_to_questions,
    _detect_per_page_restart_from_pages,
)


def _make_page(page_idx, paper_type, numbers):
    return {
        "page_index": page_idx,
        "image_path": f"/tmp/page_{page_idx}.png",
        "paper_type": paper_type,
        "boxes": [(0, i * 100, 500, (i + 1) * 100) for i in range(len(numbers))],
        "numbers": list(numbers),
    }


def test_per_page_restart_detected_for_workbook():
    """워크북: 5 페이지 모두 anchor [1,2,3] 리셋 → per-page-restart 감지."""
    pages = [_make_page(i, "clean_pdf_single", [1, 2, 3]) for i in range(5)]
    assert _detect_per_page_restart_from_pages(pages) is True


def test_per_page_restart_not_detected_for_school_exam():
    """시험지: 8 페이지 continuous 1-N → per-page-restart 미감지."""
    pages = [
        _make_page(i, "clean_pdf_dual", [i * 4 + 1, i * 4 + 2, i * 4 + 3])
        for i in range(7)
    ]
    assert _detect_per_page_restart_from_pages(pages) is False


def test_workbook_counter_mode_preserves_all_boxes():
    """워크북 모드: 5 페이지 × 3 box = 15 box 전부 보존 (1~15 counter).

    이전: segment number [1,2,3] 만 살아남고 후속 페이지 anchor 12 개 drop
          (실측 운영 doc 768: 262 → 27 drop).
    """
    pages = [_make_page(i, "clean_pdf_single", [1, 2, 3]) for i in range(5)]
    questions = _boxes_to_questions(pages)
    assert len(questions) == 15
    nums = [q["number"] for q in questions]
    assert nums == list(range(1, 16)), f"counter mode 미작동: {nums}"

    # 모든 box page_index 보존
    page_indices = [q["page_index"] for q in questions]
    assert page_indices == [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]


def test_workbook_local_number_preserved_in_meta():
    """워크북 모드: local_number 가 entry 에 보존되어 학원장 검수 시 페이지-로컬 번호 표시 가능."""
    pages = [_make_page(i, "clean_pdf_single", [1, 2, 3]) for i in range(5)]
    questions = _boxes_to_questions(pages)
    locals_ = [q.get("local_number") for q in questions]
    assert locals_ == [1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3]


def test_school_exam_counter_mode_NOT_forced():
    """시험지 (continuous numbering): segment number 그대로 사용.

    회귀 방지: 시험지에 false anchor 가 일부 페이지에 잡혀도 per-page-restart 로
    오인식되지 않음. unique constraint 충돌은 normal dedup 으로 처리.
    """
    pages = [
        _make_page(i, "clean_pdf_dual", [i * 4 + 1, i * 4 + 2, i * 4 + 3])
        for i in range(7)
    ]
    questions = _boxes_to_questions(pages)
    nums = [q["number"] for q in questions]
    # segment number 그대로: 1,2,3, 5,6,7, 9,10,11, ...
    expected = [n for i in range(7) for n in (i * 4 + 1, i * 4 + 2, i * 4 + 3)]
    assert nums == expected, f"시험지 segment number 변형됨: {nums}"
    # local_number 메타는 시험지 모드에서는 박지 않음
    assert all("local_number" not in q for q in questions)


def test_school_exam_with_single_duplicate_anchor_still_continuous():
    """시험지: 본문 false anchor 로 한 페이지에 anchor 1 한 번 더 → per-page-restart 가
    오인식되지 않음. 임계 (3 anchor × 2 페이지) 미만이면 continuous 유지.
    """
    # 7 페이지 continuous + 마지막 페이지에 false anchor 1, 2 (총 2 페이지 anchor 1, 2 등장)
    pages = []
    for i in range(6):
        pages.append(_make_page(i, "clean_pdf_dual", [i * 3 + 1, i * 3 + 2, i * 3 + 3]))
    # 마지막 페이지 false anchor
    pages.append(_make_page(6, "clean_pdf_dual", [1, 2, 19]))

    assert _detect_per_page_restart_from_pages(pages) is False
    questions = _boxes_to_questions(pages)
    # false anchor 1, 2 는 normal dedup 으로 drop → 페이지 6 에서 19 만 살아남음
    nums_by_page: dict = {}
    for q in questions:
        nums_by_page.setdefault(q["page_index"], []).append(q["number"])
    assert nums_by_page[6] == [19]


def test_workbook_skip_non_problem_pages_still_works():
    """워크북 모드에서도 paper_type=non_question/cover 페이지는 skip."""
    pages = [
        _make_page(0, "cover", []),
        _make_page(1, "clean_pdf_single", [1, 2, 3]),
        _make_page(2, "non_question", []),
        _make_page(3, "clean_pdf_single", [1, 2, 3]),
        _make_page(4, "clean_pdf_single", [1, 2, 3]),
        _make_page(5, "clean_pdf_single", [1, 2, 3]),
        _make_page(6, "clean_pdf_single", [1, 2, 3]),
        _make_page(7, "answer_key", []),
    ]
    questions = _boxes_to_questions(pages)
    # 5 question pages × 3 box = 15
    assert len(questions) == 15
    page_indices = {q["page_index"] for q in questions}
    assert page_indices == {1, 3, 4, 5, 6}, f"non-problem 페이지 포함됨: {page_indices}"
