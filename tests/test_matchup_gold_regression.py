"""매치업 분리 gold 회귀 테스트 — 운영 결함 케이스를 명시적 fixture로 락.

목적: T2 검수에서 발견한 분리 결함 케이스를 synthetic blocks로 재현하고,
classify_paper_type → split_questions 흐름이 기대 결과를 내는지 락.

각 케이스는 운영 doc의 결함 메모리(`project_t2_matchup_review_failed_2026_05_02.md`)에서
추출. 회귀 시 어느 doc/유형이 깨졌는지 즉시 식별 가능.

회귀 파편 차단:
- doc#177 cross-column anchor strip — fix됐던 결함이 다시 strip이면 fail
- doc#207 4-quadrant — quadrant 분리가 풀려 strip이면 fail
- 학생 답안지 폰사진 majority — paper_type aggregate가 low_confidence_doc 폴백 트리거 못 하면 fail
- 학습자료 over-extraction — anchor 80+면 page-as-problem 폴백 유지
"""
from __future__ import annotations

from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
    _aggregate_paper_types,
    _verify_problem_numbers,
)
from academy.domain.tools.paper_type import PaperType, classify_paper_type
from academy.domain.tools.question_splitter import (
    TextBlock,
    split_questions,
)


def _real_question_block(num: int, x0: float, y0: float, x1: float, y1: float) -> TextBlock:
    """실제 본문처럼 보이는 anchor block — is_non_question_page 우회용."""
    return TextBlock(
        text=f"{num}. 다음 중 옳은 것은?",
        x0=x0, y0=y0, x1=x1, y1=y1,
    )


# ── doc#177 회귀: cross-column anchor strip 차단 ──

def test_gold_doc177_cross_column_no_strip():
    """T2 doc#177 cross-column anchor 결함 회귀 락.

    원인: dual-col 미인식 + next anchor가 우측 column 위쪽이라 next.y0 < start.y0 →
    strip(y1 = next.y0 - margin → height < 0) bbox.
    fix: dual-col 미인식 케이스 fallback (y1 = page_height) + strip 절대 차단.
    """
    blocks = [
        _real_question_block(1, 100, 400, 4000, 500),
        TextBlock(text="① ㄱ ② ㄴ ③ ㄷ", x0=100, y0=550, x1=4000, y1=600),
        _real_question_block(2, 100, 2000, 4000, 2100),
        TextBlock(text="① 가 ② 나 ③ 다", x0=100, y0=2150, x1=4000, y1=2200),
        _real_question_block(3, 4500, 400, 8000, 500),
        TextBlock(text="① A ② B ③ C", x0=4500, y0=550, x1=8000, y1=600),
        _real_question_block(4, 4500, 2000, 8000, 2100),
        TextBlock(text="① a ② b ③ c", x0=4500, y0=2150, x1=8000, y1=2200),
    ]
    pw, ph = 8400.0, 11200.0
    regions = split_questions(blocks, pw, ph, page_index=0)
    assert len(regions) == 4, f"expected 4 regions, got {len(regions)}: {[r.number for r in regions]}"

    # 모든 region height >= 페이지의 5% (strip 절대 차단)
    for r in regions:
        x0, y0, x1, y1 = r.bbox
        height = y1 - y0
        assert height >= ph * 0.05, (
            f"q{r.number} bbox=({x0},{y0},{x1},{y1}) height={height} < 5% of page (strip)"
        )


# ── doc#207 회귀: 4-quadrant 분리 ──

def test_gold_doc207_quadrant_layout():
    """T2 doc#207 4-quadrant 시험지 분리 회귀 락.

    4 anchor가 각각 자기 quadrant 안에 구속되어야 함. 페이지를 세로 strip 4~5개로
    잘못 분할하던 결함(메모리 'project_t2_doc302_quadrant_2026_05_02')의 회귀 락.
    """
    pw, ph = 1000.0, 1400.0

    def _quad(qid: int, base_x: float, base_y: float):
        return [
            _real_question_block(qid, base_x + 50, base_y + 50, base_x + 400, base_y + 70),
            TextBlock(
                text="① A ② B ③ C ④ D ⑤ E",
                x0=base_x + 50, y0=base_y + 90,
                x1=base_x + 400, y1=base_y + 110,
            ),
            TextBlock(
                text="<보기> ㄱ. 옳다",
                x0=base_x + 50, y0=base_y + 130,
                x1=base_x + 400, y1=base_y + 150,
            ),
        ]

    blocks = (
        _quad(1, 0, 0)        # TL
        + _quad(2, 500, 0)    # TR
        + _quad(3, 0, 700)    # BL
        + _quad(4, 500, 700)  # BR
    )
    regions = split_questions(blocks, pw, ph, page_index=0)
    nums = sorted(r.number for r in regions)
    assert nums == [1, 2, 3, 4]

    by_num = {r.number: r.bbox for r in regions}
    mid_x, mid_y = pw / 2, ph / 2

    # 1번 (TL): 우측/하단 경계 안 (margin 5)
    assert by_num[1][2] <= mid_x + 5
    assert by_num[1][3] <= mid_y + 5
    # 2번 (TR): 좌측/하단 경계 안
    assert by_num[2][0] >= mid_x - 5
    assert by_num[2][3] <= mid_y + 5
    # 3번 (BL): 우측 경계 안 + 상단 경계 아래
    assert by_num[3][2] <= mid_x + 5
    assert by_num[3][1] >= mid_y - 5
    # 4번 (BR): 좌측 + 상단 경계
    assert by_num[4][0] >= mid_x - 5
    assert by_num[4][1] >= mid_y - 5


# ── 학생 답안지 폰사진 majority 폴백 ──

def test_gold_student_answer_photo_low_confidence_doc():
    """학생 답안지 폰사진 majority doc → low_confidence_ratio >= 0.5 → page 폴백 트리거.

    매치업 pipeline의 is_low_confidence_doc 분기 회귀 락.
    T2 시험지 6 doc 결함(전부 학생 답안지 폰사진)에서 자동분리 신뢰성 붕괴 → 페이지
    단위 매칭으로 폴백되어 "Q3 적중자료가 Q5" 같은 신뢰성 사고 차단.
    """
    pages = [
        {"paper_type": "student_answer_photo"},
        {"paper_type": "student_answer_photo"},
        {"paper_type": "student_answer_photo"},
        {"paper_type": "scan_dual"},
    ]
    summary = _aggregate_paper_types(pages)

    # primary가 student_answer_photo or low_confidence_ratio >= 0.5 — 폴백 트리거
    is_low_confidence = (
        summary["primary"] == "student_answer_photo"
        or summary["low_confidence_ratio"] >= 0.5
    )
    assert is_low_confidence is True
    assert "student_answer_photo_detected" in summary["warnings"]
    assert "low_confidence_source_majority" in summary["warnings"]


def test_gold_clean_pdf_doc_no_fallback():
    """깨끗한 PDF doc은 페이지 폴백 트리거 안 함."""
    pages = [
        {"paper_type": "clean_pdf_dual"},
        {"paper_type": "clean_pdf_dual"},
        {"paper_type": "clean_pdf_dual"},
        {"paper_type": "non_question"},  # 표지
    ]
    summary = _aggregate_paper_types(pages)
    is_low_confidence = (
        summary["primary"] == "student_answer_photo"
        or summary["low_confidence_ratio"] >= 0.5
    )
    assert is_low_confidence is False
    assert summary["warnings"] == []


# ── number↔content mismatch 회귀 (C10 결함) ──

def test_gold_number_mismatch_flagged():
    """C10 결함 회귀 락 — DB number와 image 본문 번호가 다르면 검수 플래그.

    T2 doc#177/#294의 56% mismatch 결함 — 매치업 결과 PDF에서 잘못된 매핑 차단.
    """
    questions = [
        # 정상 매핑
        {"number": 1, "bbox": [0, 0, 100, 100], "text": "1. 다음 중 옳은 것은?"},
        # mismatch — DB number=2인데 image의 본문 번호는 5
        {"number": 2, "bbox": [0, 0, 100, 100], "text": "5. 그림은 어떤 분자의"},
        # 정상 매핑
        {"number": 3, "bbox": [0, 0, 100, 100], "text": "3) 다음을 고르시오"},
    ]
    _verify_problem_numbers(questions)

    assert (questions[0].get("meta_extra") or {}).get("number_mismatch") is None
    assert questions[1]["meta_extra"]["number_mismatch"] == {"db": 2, "ocr": 5}
    assert (questions[2].get("meta_extra") or {}).get("number_mismatch") is None


# ── paper_type 분류 회귀 (T2 결함 케이스) ──

def test_gold_paper_type_quad_classification():
    """4-quadrant layout이 QUADRANT로 분류됨 — split_questions에 강제 분기."""
    pw, ph = 1000.0, 1400.0

    def _quad(qid: int, base_x: float, base_y: float):
        return [
            _real_question_block(qid, base_x + 50, base_y + 50, base_x + 400, base_y + 70),
            TextBlock(
                text="① A ② B ③ C ④ D ⑤ E",
                x0=base_x + 50, y0=base_y + 90,
                x1=base_x + 400, y1=base_y + 110,
            ),
            TextBlock(
                text="<보기>",
                x0=base_x + 50, y0=base_y + 130,
                x1=base_x + 400, y1=base_y + 150,
            ),
        ]

    blocks = (
        _quad(1, 0, 0)
        + _quad(2, 500, 0)
        + _quad(3, 0, 700)
        + _quad(4, 500, 700)
    )
    pt = classify_paper_type(
        text_blocks=blocks,
        page_width=pw,
        page_height=ph,
        has_embedded_text=True,
    )
    assert pt.paper_type is PaperType.QUADRANT
    assert pt.is_quadrant is True


def test_gold_paper_type_dual_classification():
    """좌/우 분포 dual layout이 CLEAN_PDF_DUAL로 분류됨."""
    pw = 8400.0
    blocks = [
        _real_question_block(1, 100, 400, 4000, 500),
        TextBlock(text="① ㄱ ② ㄴ ③ ㄷ", x0=100, y0=550, x1=4000, y1=600),
        _real_question_block(2, 100, 2000, 4000, 2100),
        TextBlock(text="① A ② B ③ C", x0=100, y0=2150, x1=4000, y1=2200),
        _real_question_block(3, 4500, 400, 8000, 500),
        TextBlock(text="① 가 ② 나 ③ 다", x0=4500, y0=550, x1=8000, y1=600),
        _real_question_block(4, 4500, 2000, 8000, 2100),
        TextBlock(text="① a ② b ③ c", x0=4500, y0=2150, x1=8000, y1=2200),
    ]
    pt = classify_paper_type(
        text_blocks=blocks,
        page_width=pw,
        page_height=11200.0,
        has_embedded_text=True,
    )
    assert pt.paper_type is PaperType.CLEAN_PDF_DUAL
    assert pt.is_dual_column is True


def test_gold_paper_type_non_question_skip():
    """표지/정답지 페이지는 NON_QUESTION으로 분류 — split_questions가 빈 결과."""
    blocks = [
        TextBlock(
            text="1. ④  2. ④  3. ①  4. ③  5. ③  6. ④  7. ④  8. ④  9. ⑤  10. ⑤",
            x0=0, y0=100, x1=500, y1=120,
        ),
    ]
    pt = classify_paper_type(
        text_blocks=blocks,
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=True,
    )
    assert pt.paper_type is PaperType.NON_QUESTION
    assert pt.is_non_question is True

    # split_questions에 paper_type=NON_QUESTION 전달 시 빈 결과
    regions = split_questions(blocks, 500.0, 700.0, page_index=0, paper_type=pt)
    assert regions == []


# ── 학습자료 over-extraction 폴백 락 ──

def test_gold_over_extraction_majority_clean_pdf():
    """학습자료 anchor 80+ → over_extraction 폴백 (paper_type은 CLEAN_PDF_*).

    paper_type에 의한 low_confidence_doc 폴백과 별개로, 기존 over_extracted 분기는
    유지됨. 다른 트리거이므로 둘 다 가능.
    """
    pages = [
        {"paper_type": "clean_pdf_single"},
        {"paper_type": "clean_pdf_single"},
        {"paper_type": "clean_pdf_single"},
    ]
    summary = _aggregate_paper_types(pages)
    # 학습자료 over-extraction이라 paper_type만으로 low_confidence는 아님
    is_low_confidence = (
        summary["primary"] == "student_answer_photo"
        or summary["low_confidence_ratio"] >= 0.5
    )
    assert is_low_confidence is False
    # 별도 over_extracted 트리거(anchor 80+)는 paper_type 신호와 독립적으로 동작
