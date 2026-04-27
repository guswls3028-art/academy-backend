"""
Question splitter regression tests — Tenant 2 자동분리 결함 fix 락.

운영 케이스 (2026-04-26 Tenant 2 tchul):
- 모의고사 "12)그림은..." 공백 없는 anchor 미검출 → 패턴 보강
- 학습자료 "RUNNER'S HIGH WITH GOD MIN" + lorem ipsum placeholder 표지 → skip
- 학습자료 정답표 "1. ④ 2. ④ 3. ① ..." 60+ 폭증 → skip
- 학습자료 해설지 "10. 정답 ④ 문제 해설 ㄱ. ..." → skip
- 학습자료 본문 "5. zb5)" 학습 항목 ID → skip
- 모의고사 헤더 "제 4교시 / 신민T 모의고사 / 탐구 영역 / 홀수형" → skip

각 케이스는 운영 PDF 시각 검수 후 실제 텍스트에서 추출.
"""
from __future__ import annotations

from academy.domain.tools.question_splitter import (
    TextBlock,
    is_non_question_page,
    _extract_question_number,
    split_questions,
    validate_anchors_across_pages,
)


def _blocks(*lines):
    """라인 리스트를 TextBlock 리스트로 변환 (y 좌표는 행 인덱스로 부여)."""
    return [
        TextBlock(text=t, x0=0.0, y0=float(i * 20), x1=500.0, y1=float(i * 20 + 18))
        for i, t in enumerate(lines)
    ]


# ── 1. anchor 패턴: 닫는 ")"/"." 뒤 공백 없는 케이스 ──

def test_anchor_pattern_close_paren_no_space():
    """`12)그림은...` 처럼 ")" 뒤 공백 없는 anchor도 인식."""
    assert _extract_question_number("12)그림은 주기율표를 나타낸 것이다") == 12


def test_anchor_pattern_dot_no_space():
    """`5.다음은...` 처럼 "." 뒤 공백 없는 anchor도 인식."""
    assert _extract_question_number("5.다음은 측정과 관련된 설명이다") == 5


def test_anchor_pattern_with_space_still_works():
    """기존 `1. 그림은...` 공백 anchor 회귀 방지."""
    assert _extract_question_number("1. 그림은 빅뱅 우주론") == 1
    assert _extract_question_number("3) 다음은 어느") == 3


def test_anchor_pattern_rejects_double_paren():
    """`1.1` 또는 `1..` 같이 같은 구두점이 연속이면 anchor 아님."""
    assert _extract_question_number("1..something") is None


def test_anchor_pattern_section_offset():
    """`[서답형 4]` → 104 (offset 100 + 4)."""
    assert _extract_question_number("[서답형 4] 주어진 단어를") == 104


# ── 2. 표지/헤더 페이지 차단 ──

def test_skip_lorem_ipsum_cover():
    """`RUNNER'S HIGH WITH GOD MIN` + lorem ipsum placeholder 표지 차단."""
    blocks = _blocks(
        "RUNNER'S HIGH WITH GOD MIN",
        "diam non",
        "adipiscing elit, sed diam nonummy nibh",
        "horeet dolore magna",
        "euismod tincidunt ut laoreet dolore magna",
        "aliquam erat volutpat. Ut wisi enim ad aliquan",
    )
    assert is_non_question_page(blocks) is True


def test_skip_design_cover_two_keywords():
    """디자인 키워드 2+ 동시 등장 → 표지 (길이 800 미만)."""
    blocks = _blocks(
        "신과 함께 PROJECT WORKBOOK",
        "통합과학",
        "1학기 중간고사 대비 문항편",
        "INTEGRATED SCIENCE",
        "신민 편저",
    )
    assert is_non_question_page(blocks) is True


def test_skip_exam_header_three_keywords():
    """`제 N 교시 / 탐구 영역 / 홀수형` 시험지 헤더 3+ 키워드 차단."""
    blocks = _blocks(
        "제 4 교시",
        "신민 T 신념 모의고사 통합 과학 N 제",
        "탐구 영역",
        "홀수 형",
        "성명",
        "수험번호",
    )
    assert is_non_question_page(blocks) is True


# ── 3. 정답표 / 해설지 차단 ──

def test_skip_answer_table():
    """정답표 페이지: "1. ④ 2. ④ 3. ① 4. ③ ..." 5+ 차단."""
    blocks = _blocks(
        "1. ④  2. ④  3. ①  4. ③  5. ③  6. ④  7. ④  8. ④  9. ⑤  10. ⑤"
    )
    assert is_non_question_page(blocks) is True


def test_skip_explanation_page():
    """해설지: "10. 정답 ④ 문제 해설 ㄱ." 패턴 3+ 차단."""
    blocks = _blocks(
        "10. 정답 ④",
        "문제 해설 ㄱ. (가)는 그림자의 길이를 이용하여",
        "11. 정답 ⑤",
        "문제 해설 길이는 기본량이다",
        "12. 정답 ②",
    )
    assert is_non_question_page(blocks) is True


def test_skip_zb_marker_page():
    """학습자료 본문 "5. zb5)" 학습 항목 ID 페이지 차단."""
    blocks = _blocks(
        "5. zb5) 다음 글을 읽고 물음에 답하시오.",
        "11. zb11) 다음은 지구와 달 사이의",
        "17. zb17) 그림 (가)는 레이저 길이 측정기,",
    )
    assert is_non_question_page(blocks) is True


# ── 4. 본문 페이지는 skip되지 않음 ──

def test_keeps_real_question_page():
    """본문 시험지 페이지는 skip되지 않음."""
    blocks = _blocks(
        "1) 다음은 압력에 대한 설명이다.",
        "<보기> ㄱ. 힘은 가속도 법칙에 의해 정의된다.",
        "ㄴ. 힘의 단위 N은 기본 단위이다.",
        "ㄷ. 압력의 단위 Pa을 기본 단위로 옳게 나타낸 것은?",
        "옳은 것만을 <보기>에서 있는 대로 고른 것은?",
        "① ㄱ ② ㄴ ③ ㄷ ④ ㄱ, ㄴ ⑤ ㄴ, ㄷ",
    )
    assert is_non_question_page(blocks) is False


def test_keeps_short_question_page_with_indicator():
    """짧은 페이지여도 보기 ① 또는 지시문이 있으면 본문."""
    blocks = _blocks(
        "1. 다음 중 옳은 것은?",
        "① A ② B ③ C ④ D ⑤ E",
    )
    assert is_non_question_page(blocks) is False


# ── 5. split + validate 통합 ──

def test_split_with_no_space_anchors():
    """`12)` 공백 없는 anchor 다수 페이지에서 모두 인식."""
    blocks = _blocks(
        "12)그림은 주기율표의 일부를 나타낸 것이다.",
        "13)다음은 원소 X와 Y의 안정한 이온을 표시한 것이다.",
        "14)다음은 어떤 원자의 전자 배치를 나타낸 것이다.",
        "15)그림은 화합물 ABC의 화학 결합 모형이다.",
    )
    regions = split_questions(blocks, page_width=500.0, page_height=200.0, page_index=0)
    assert [r.number for r in regions] == [12, 13, 14, 15]


def test_validate_drops_cross_page_duplicate():
    """크로스-페이지 중복 anchor 제거 (본문 내 '그림 4는' 오탐)."""
    from academy.domain.tools.question_splitter import QuestionRegion

    page0 = [
        QuestionRegion(number=1, bbox=(0, 0, 500, 100), page_index=0),
        QuestionRegion(number=2, bbox=(0, 100, 500, 200), page_index=0),
    ]
    page1 = [
        QuestionRegion(number=2, bbox=(0, 0, 500, 100), page_index=1),  # 본문 내 오탐
        QuestionRegion(number=3, bbox=(0, 100, 500, 200), page_index=1),
    ]
    out = validate_anchors_across_pages([page0, page1])
    assert [r.number for r in out[0]] == [1, 2]
    assert [r.number for r in out[1]] == [3]


def test_validate_drops_outlier_after_gap():
    """sequence outlier (median gap 대비 5x + abs >= 5) 드롭."""
    from academy.domain.tools.question_splitter import QuestionRegion

    page0 = [
        QuestionRegion(number=n, bbox=(0, 0, 500, 100), page_index=0)
        for n in (1, 2, 3, 4, 5)
    ]
    page1 = [QuestionRegion(number=46, bbox=(0, 0, 500, 100), page_index=1)]
    out = validate_anchors_across_pages([page0, page1])
    nums_kept = [r.number for page in out for r in page]
    assert 46 not in nums_kept


# ── 6. 4분할(quad) layout 감지 ──

def test_quad_layout_detected_and_split():
    """2x2 4분할 시험지: 4 quadrant 각각의 anchor가 인식되고 박스가 quadrant 경계로 구속."""
    from academy.domain.tools.question_splitter import (
        TextBlock as TB,
        _detect_quad_layout,
        split_questions,
    )

    pw, ph = 1000.0, 1400.0
    # 4분면 중심 좌표 — TL/TR/BL/BR 각각 anchor 1개 + 본문 3블록
    def quad_blocks(qid: int, base_x: float, base_y: float):
        return [
            TB(text=f"{qid}. 다음 중 옳은 것은?", x0=base_x + 50, y0=base_y + 50, x1=base_x + 400, y1=base_y + 70),
            TB(text="① A ② B ③ C ④ D ⑤ E", x0=base_x + 50, y0=base_y + 90, x1=base_x + 400, y1=base_y + 110),
            TB(text="<보기> ㄱ. 옳음", x0=base_x + 50, y0=base_y + 130, x1=base_x + 400, y1=base_y + 150),
        ]

    blocks = (
        quad_blocks(1, 0, 0)               # TL
        + quad_blocks(2, 500, 0)           # TR
        + quad_blocks(3, 0, 700)           # BL
        + quad_blocks(4, 500, 700)         # BR
    )
    assert _detect_quad_layout(blocks, pw, ph) is True

    regions = split_questions(blocks, pw, ph, page_index=0)
    nums = sorted(r.number for r in regions)
    assert nums == [1, 2, 3, 4]

    # 각 region이 자기 quadrant 경계 안에 있어야 함
    by_num = {r.number: r.bbox for r in regions}
    # 1번 = TL: x < 500 + margin, y < 700 + margin
    assert by_num[1][2] <= 510 and by_num[1][3] <= 710
    # 2번 = TR: x > 500 - margin
    assert by_num[2][0] >= 498
    # 3번 = BL: y > 700 - margin
    assert by_num[3][1] >= 698
    # 4번 = BR: 둘 다
    assert by_num[4][0] >= 498 and by_num[4][1] >= 698


def test_quad_layout_not_falsely_detected_on_dual_column():
    """일반 2단 페이지는 quad로 잘못 감지되지 않음."""
    from academy.domain.tools.question_splitter import TextBlock as TB, _detect_quad_layout

    pw, ph = 1000.0, 1400.0
    blocks = []
    # 좌측 컬럼에 전체 높이로 텍스트 (TL + BL 모두 텍스트 多)
    for y in range(50, 1300, 50):
        blocks.append(TB(text="left", x0=50, y0=float(y), x1=400, y1=float(y + 30)))
    # 우측 컬럼 동일 — 가운데 가로 gutter 없음
    for y in range(50, 1300, 50):
        blocks.append(TB(text="right", x0=550, y0=float(y), x1=950, y1=float(y + 30)))

    assert _detect_quad_layout(blocks, pw, ph) is False
