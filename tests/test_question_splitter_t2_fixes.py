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
    _references_prior_context,
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


def test_anchor_pattern_accepts_duplicate_rendered_number():
    """PyMuPDF가 겹친 번호를 `98 98.`처럼 추출해도 하나의 anchor로 인식."""
    assert _extract_question_number("98 98. 내신기출 그림은 콩팥에서") == 98


def test_anchor_pattern_after_source_prefix():
    """`[언남고 기출] / 1.` 같은 출처 prefix 뒤 문항 번호도 인식한다."""
    assert _extract_question_number("[ 언남고 기출 ] / 1. 그림은 생태계") == 1
    assert (
        _extract_question_number("[2019년 고1 6월 학평 통합과학 19번] / 16. 다음은")
        == 16
    )
    assert (
        _extract_question_number("[2017. 7. 평가원 16번 문제]\n  \n18.\n다음은")
        == 18
    )
    assert _extract_question_number("[보기] ㄱ. 옳은 설명") is None


def test_anchor_pattern_rejects_double_paren():
    """`1.1` 또는 `1..` 같이 같은 구두점이 연속이면 anchor 아님."""
    assert _extract_question_number("1..something") is None


def test_anchor_pattern_section_offset():
    """`[서답형 4]` → 104 (offset 100 + 4)."""
    assert _extract_question_number("[서답형 4] 주어진 단어를") == 104
    assert _extract_question_number("서답형 \n[\n1] 다음은 분자식을 나타낸 것이다.") == 101


def test_anchor_pattern_section_requires_hyeong():
    """본문 텍스트 "서술 1가지 방법" — 형 없으면 section anchor 아님 (false positive 차단)."""
    # 형이 없으면 섹션 패턴 매치 안 됨 → 선택형 패턴도 시도하지만 "서술 1"는 _QUESTION_PATTERN
    # 시작이 숫자가 아니므로 None.
    assert _extract_question_number("서술 1가지 방법을 설명한다") is None
    # `서술형 1` 또는 `[서답형 4]`는 정상 매치
    assert _extract_question_number("서술형 1. 다음 글을 읽고") == 101
    assert _extract_question_number("[서술형 2] 풀이 과정을") == 102


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


def test_skip_workbook_answer_grid_with_step_sections():
    """상용 workbook 끝부분의 Step별 정답/해설참조 grid는 문항 페이지가 아니다."""
    blocks = _blocks(
        "Part 03 생명체 구성 물질의 형성",
        "Step 1. 개념완성",
        "01 해설 참조 02 해설 참조 03 해설 참조 04 해설 참조 05 해설 참조",
        "Step 2. 내신완성",
        "01 / ① / 02 / ⑤ / 03 / ① / 04 해설 참조 / 05 / ⑤",
        "06 / ④ / 07 해설 참조 / 08 / ④ / 09 / ①",
        "Step 3. 수능완성",
        "01 / ⑤ / 02 / ⑤ / 03 / ⑤ / 04 / ④ / 05 / ⑤",
        "06 / ⑤ / 07 / ④ / 08 / ② / 09 / ③ / 10 / ⑤",
    )
    assert is_non_question_page(blocks) is True


def test_skip_project_answer_grid_with_linebreak_cells():
    """번호와 정답이 줄바꿈 분리된 PROJECT/고난도 정답표도 차단한다."""
    blocks = _blocks(
        "신과함께 PROJECT",
        "Part 01 화학과 우리 생활",
        "고난도 수능 모의평가 문항으로 1등급 만들기",
        "01\n④\n02\n⑤\n03\n③\n04\n④\n05\n④",
        "06\n⑤\n07\n⑤\n08\n⑤\n09\n⑤\n10\n⑤",
        "고난도 대치동 기출변형 문항으로 1등급 다지기",
        "01\n⑤\n02\n⑤\n03\n⑤\n04\n④\n05\n②",
        "06\n⑤\n07\n⑤\n08\n③\n09\n⑤\n10\n⑤",
    )
    assert is_non_question_page(blocks) is True


def test_skip_chemistry_test_appendix_page():
    """화학 workbook의 TEST 부록/정답 나열 페이지는 문제 crop 대상이 아니다."""
    blocks = _blocks(
        "※ 다음 이온의 이온식을 쓰시오.",
        "1. 수소이온 2. 나트륨이온 3. 리튬이온",
        "4. 칼륨이온 5. 마그네슘이온 6. 칼슘이온",
        "7. 은이온 8. 알루미늄이온 9. 아이오딘화이온",
        "10. 구리이온 11. 염화이온 12. 납이온",
        "13. 산화이온 14. 황화이온 15. 플루오린화이온",
        "이온과 이온화 TEST",
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


def test_zb_marker_page_with_choice_questions_is_not_non_question():
    """zb 원본 ID가 붙어도 객관식 문제 신호가 있으면 본문 문제로 유지한다."""
    blocks = _blocks(
        "1. 그림은 질소 분자를 나타낸 것이다.zb1)",
        "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?",
        "< 보 기 > ㄱ. 공유 결합이다. ㄴ. 전자쌍을 공유한다.",
        "① ㄱ ② ㄴ ③ ㄱ, ㄴ",
        "2. 그림은 화합물 MX를 나타낸 것이다.zb2)",
        "3. 그림은 A와 B의 전자 배치를 나타낸 것이다.zb3)",
    )
    assert is_non_question_page(blocks) is False


def test_skip_chapter_concept_page_without_question_signal():
    """T1 doc 615 p101: CHAPTER 개념/추가설명 페이지가 문제 1개로 들어가던 회귀 차단."""
    blocks = _blocks(
        "CHAPTER 04 지구 시스템의 구성요소와 상호작용",
        "추가 설명",
        "Ⅰ. 지구 시스템의 구성 요소",
        "지구계 계(system): 상호작용하는 구성 요소들의 집합",
        "- 지구계 구성 요소 5개",
        "1) 외권: 지표로부터 약 1천 킬로미터 이상의 우주 공간",
        "외권은 지구상에 존재하는 4개의 권역과 모두 상호작용할 수 있음",
        "2) 지권: 지각과 지구 내부를 포함하는 영역",
        "지구 내부 에너지 맨틀의 대류로 인한 지각 변동이 일어남",
    )
    assert is_non_question_page(blocks) is True


def test_skip_chapter_science_basics_scale_concept_page():
    """T1 doc 615 p6: 과학의 기초/시간·공간 규모 개념 페이지는 문항이 아니다."""
    blocks = _blocks(
        "과학의 기초 01 CHAPTER",
        "1. 시간과 공간 자연 세계 규모(scale): 어떤 자연 현상의 크기 범위",
        "자연 현상들은 시간 규모와 공간 규모가 매우 다양함",
        "미시 세계: 아주 작은 물체나 현상을 다루는 세계",
        "거시 세계: 큰 물체나 현상을 다루는 세계",
        "Ex) 원자, 분자, 이온 등",
        "Ex) 나무, 동물, 천체 등",
        "시간 규모: 나이 - 100억 년",
        "공간 규모: 지름 - 62 kpc",
    )
    assert is_non_question_page(blocks) is True


def test_skip_color_workbook_chemistry_concept_note_page():
    """T2 26-1m 컬러 workbook 추가설명/필기형 화학 개념 노트는 문제로 자르지 않는다."""
    blocks = _blocks(
        "추가 설명",
        "2. 다양한 원소들의 성질",
        "1) 금속 원소",
        "최외각전자 1, 2, 3개",
        "전자를 잃기 쉽다",
        "광택, 열 전도, 전기 전도",
        "2) 비금속 원소",
        "전자를 얻기 쉽다",
        "공유 결합을 통해 전자를 모두 씀",
        "전기전도성 거의 X",
        "3) 비활성 기체",
        "4) 알칼리 금속",
    )
    assert is_non_question_page(blocks) is True


def test_skip_color_workbook_chemistry_formula_note_page():
    """T2 26-1m 이온결합 예시/공식 노트는 번호가 있어도 문항이 아니다."""
    blocks = _blocks(
        "- 이온 결합 그림 그리기",
        "1) MgO",
        "Mg : 3주기 2족 -> [Ne]",
        "가장 쉽게 안정화되는 방법 : 전자 2개를 버리기",
        "O : 2주기 16족 -> [Ne]",
        "2) CaO",
        "Ca : 4주기 2족 -> [Ar]",
        "3) Li2O",
        "Li : 2주기 1족 -> [He]",
    )
    assert is_non_question_page(blocks) is True


def test_skip_color_workbook_semiconductor_concept_page():
    """T2 26-1m 반도체/다이오드 개념 페이지는 문제 crop 대상이 아니다."""
    blocks = _blocks(
        "물질의 전기적 성질",
        "CHAPTER 03",
        "1. 신소재의 개발과 이용",
        "1. 전기적 성질을 이용한 신소재",
        "[전기적 성질에 따른 물질 분류]",
        "1. 순수 반도체 : 불순물 없이 완벽한 결정 구조를 갖는 반도체",
        "2. 불순물 반도체 : 전기 전도성을 증가시킨 것",
        "도체 절연체 반도체",
        "전기저항 전류 다이오드 트랜지스터",
    )
    assert is_non_question_page(blocks) is True


def test_skip_color_workbook_metal_bond_concept_page():
    """T2 26-1m 금속결합 성질 정리 페이지는 번호가 있어도 문항이 아니다."""
    blocks = _blocks(
        "금속 결합 물질",
        "금속 양이온 + 자유 전자",
        "금속 결합 물질은 자기 원소로만 결합할 수 있음",
        "1. 광택 O",
        "2. 전기전도성 O, 열 전도성 O",
        "3. 전성 O, 연성 O",
        "자유전자가 존재하기 때문에 전류가 잘 통한다.",
        "금속 양이온과 자유전자 간의 인력이 매우 커 잘 녹지 않는다.",
        "녹는점이 높다.",
        "금속은 힘을 가해도 자유전자가 따라오며 인력이 유지된다.",
        "자유전자의 충돌이 에너지를 전달한다.",
    )
    assert is_non_question_page(blocks) is True


def test_skip_standalone_jeongdap_answer_page():
    """OCR layout 깨진 해설지: "정답 ③" 만 3+ 반복 (N. 접두어 없음)."""
    blocks = _blocks(
        "정답 ③ ㄱ, ㄷ, ㄹ",
        "정답 ⑤",
        "정답 ② 풀이 과정",
        "정답 ④",
    )
    assert is_non_question_page(blocks) is True


def test_keeps_question_with_single_jeongdap_in_body():
    """본문에 '정답' 1회 (보기 ⑤ 옆) 있는 페이지는 차단하지 않음."""
    blocks = _blocks(
        "1. 다음 중 옳은 것은?",
        "① A ② B ③ C ④ D ⑤ E",
        "정답을 표시하시오.",  # 본문에 정답 단어 등장 가능
    )
    assert is_non_question_page(blocks) is False


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


def test_keeps_chapter_header_page_with_real_questions():
    """CHAPTER/개념 헤더가 있어도 보기와 문항 지시문이 있으면 문제 페이지로 유지."""
    blocks = _blocks(
        "CHAPTER 04 지구 시스템의 구성요소와 상호작용",
        "개념완성",
        "1. 그림은 지구 시스템의 구성 요소를 나타낸 것이다.",
        "이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?",
        "ㄱ. 외권은 기권 밖의 우주 공간이다.",
        "ㄴ. 지권은 지구 내부를 포함한다.",
        "① ㄱ ② ㄴ ③ ㄱ, ㄴ ④ ㄴ, ㄷ ⑤ ㄱ, ㄴ, ㄷ",
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


def test_prefer_marginal_preserves_body_anchors_in_other_column():
    """workbook 모드에서도 marginal이 없는 반대쪽 column의 prefix형 본문 anchor는 보존."""
    blocks = [
        TextBlock("9.\n다음은 물질 A에 대한 설명이다.", 70, 150, 300, 170),
        TextBlock("보기 ㄱ. 설명", 80, 250, 280, 260),
        TextBlock("10.\n그림은 물질 B를 나타낸 것이다.", 70, 450, 300, 470),
        TextBlock("보기 ㄴ. 설명", 80, 550, 280, 560),
        TextBlock(
            "[2021학년도 고3 10월 학평 1번]\n11.\n다음은 탄소 화합물 설명이다.",
            330,
            135,
            560,
            170,
        ),
        TextBlock("보기 ㄷ. 설명", 340, 250, 550, 260),
        TextBlock(
            "[2019학년도 고2 6월 학평 2번]\n12.\n다음은 문명 물질 설명이다.",
            330,
            430,
            560,
            465,
        ),
        TextBlock("보기 ㄹ. 설명", 340, 550, 550, 560),
    ]

    regions = split_questions(
        blocks,
        page_width=612.0,
        page_height=858.0,
        page_index=0,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [9, 10, 11, 12]
    assert all(r.bbox[0] < 315 for r in regions[:2])
    assert all(r.bbox[0] > 300 for r in regions[2:])


def test_prefer_marginal_preserves_source_prefixed_body_anchor_same_column():
    """같은 column의 source-prefixed body anchor는 소문항이 아니라 독립 문항이다."""
    blocks = [
        TextBlock(
            "[2019년 고1 6월 학평 통합과학 4번]\n17.\n다음은 보드게임 문제이다.",
            79,
            135,
            311,
            169,
        ),
        TextBlock("보기 ㄱ. 설명", 85, 250, 300, 260),
        TextBlock("18.\n그림은 우주의 일부를 나타낸 것이다.", 79, 439, 390, 469),
        TextBlock("보기 ㄴ. 설명", 85, 550, 300, 560),
    ]

    regions = split_questions(
        blocks,
        page_width=612.0,
        page_height=864.0,
        page_index=0,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [17, 18]


def test_prefer_marginal_still_drops_same_column_subitems():
    """source prefix가 없는 같은 column의 (1)(2) 소문항은 main crop으로 승격하지 않는다."""
    blocks = [
        TextBlock("25.\n그림은 지권의 구조를 나타낸 것이다.", 79, 154, 262, 169),
        TextBlock("(1) 대류 현상의 원인을 서술하시오.", 79, 671, 367, 679),
        TextBlock("(2) 기상 현상이 나타나는 권을 쓰시오.", 79, 723, 317, 731),
    ]

    regions = split_questions(
        blocks,
        page_width=612.0,
        page_height=864.0,
        page_index=0,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [25]


def test_split_questions_recovers_inline_main_anchor_inside_merged_block():
    """선택지와 다음 문항 시작이 한 block에 합쳐져도 줄 시작 main anchor는 복구한다."""
    blocks = [
        TextBlock("3.\n그림은 X(g)에 대한 자료이다.", 42, 130, 283, 147),
        TextBlock(
            "① ㄱ\n② ㄷ\n③ ㄱ, ㄴ\n④ ㄴ, ㄷ\n⑤ ㄱ, ㄴ, ㄷ\n4.\n다음은 원자 X~Z에 대한 자료이다.",
            42,
            421,
            277,
            467,
        ),
        TextBlock("5.\n다음은 다른 자료이다.", 304, 130, 551, 147),
    ]

    regions = split_questions(
        blocks,
        page_width=612.0,
        page_height=858.0,
        page_index=0,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [3, 4, 5]
    q3 = next(r for r in regions if r.number == 3)
    q4 = next(r for r in regions if r.number == 4)
    assert q3.bbox[3] <= q4.bbox[1]


def test_split_questions_includes_separate_source_prefix_above_anchor():
    """출처 prefix가 번호 줄과 분리된 PDF block이어도 같은 문항 상단에 포함한다."""
    blocks = [
        TextBlock("[2022년 고1 9월 학평 통합과학 18번]", 79, 135, 233, 144),
        TextBlock("61.\n다음은 2, 3주기 원소 A~D에 대한 자료이다.", 79, 153, 271, 168),
        TextBlock("이에 대한 설명으로 옳은 것은?", 79, 244, 435, 253),
        TextBlock("62.\n다음은 다른 문항이다.", 79, 520, 271, 535),
    ]

    regions = split_questions(
        blocks,
        page_width=612.0,
        page_height=864.0,
        page_index=0,
        prefer_marginal=True,
    )

    q61 = next(r for r in regions if r.number == 61)
    assert q61.bbox[1] <= 135


def test_split_questions_does_not_treat_bogi_as_source_prefix():
    """[보기] 같은 bracket label은 출처 prefix 상단 확장 신호가 아니다."""
    blocks = [
        TextBlock("[보기]", 79, 135, 120, 144),
        TextBlock("1.\n다음 설명으로 옳은 것은?", 79, 153, 271, 168),
        TextBlock("2.\n다음은 다른 문항이다.", 79, 520, 271, 535),
    ]

    regions = split_questions(
        blocks,
        page_width=612.0,
        page_height=864.0,
        page_index=0,
        prefer_marginal=True,
    )

    q1 = next(r for r in regions if r.number == 1)
    assert q1.bbox[1] > 145


def test_source_prefixed_main_question_absorbs_parenthesized_subitems():
    """source-prefixed 큰 문항 내부 (1)(2)(3)은 별도 crop anchor가 아니다."""
    blocks = [
        TextBlock(
            "[ 언남고 기출 ]\n9. 표는 방형구 조사 결과이다.",
            28,
            70,
            566,
            98,
        ),
        TextBlock("(1) ⓐ와 ⓑ에 해당하는 수를 쓰시오.", 28, 287, 181, 296),
        TextBlock("(2) 우점종과 중요치를 쓰시오.", 28, 368, 401, 377),
        TextBlock("(3) A와 B의 상대 빈도를 비교하시오.", 28, 436, 199, 445),
    ]

    regions = split_questions(
        blocks,
        page_width=595.0,
        page_height=841.0,
        page_index=0,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [9]
    assert regions[0].bbox[3] > 445


def test_source_prefixed_main_question_absorbs_plain_procedure_numbers():
    """source-prefixed 큰 문항 내부 1/2/3 절차번호는 별도 crop anchor가 아니다."""
    blocks = [
        TextBlock(
            "[ 학교 기출 ]\n20. 다음 탐구 과정에 대한 설명으로 옳은 것은?",
            320,
            80,
            570,
            110,
        ),
        TextBlock("1. 시험관에 물질 A를 넣는다.", 330, 180, 540, 192),
        TextBlock("2. 온도를 높인다.", 330, 225, 500, 237),
        TextBlock("3. 색 변화를 관찰한다.", 330, 270, 540, 282),
        TextBlock("21. 다음은 다른 문항이다.", 320, 520, 570, 540),
    ]

    regions = split_questions(
        blocks,
        page_width=612.0,
        page_height=864.0,
        page_index=0,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [20, 21]
    assert regions[0].bbox[3] > 282


def test_source_prefixed_main_question_preserves_following_question_10():
    """출처 원문 번호 24가 붙은 실제 q9 뒤의 q10은 내부 절차번호가 아니다."""
    blocks = [
        TextBlock(
            "9. 2주기 원소의 주소 화합물에 대한 자료이다.",
            320,
            80,
            590,
            110,
        ),
        TextBlock("24. (가)~(다)에서 2주기 원자는 옥텟 규칙을 만족한다.", 320, 125, 590, 145),
        TextBlock("이에 대한 설명으로 옳은 것만을 고른 것은?", 320, 280, 590, 292),
        TextBlock("10. 물질 A~D에 대한 몇 가지 성질을 나타낸 것이다.", 320, 410, 590, 430),
        TextBlock("11. 다음은 다른 문항이다.", 320, 650, 590, 670),
    ]

    regions = split_questions(
        blocks,
        page_width=612.0,
        page_height=864.0,
        page_index=0,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [9, 10, 11, 24]


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


def test_validate_drops_isolated_high_space_ocr_number():
    """Sparse scan OCR garbage like 429 must not survive beside normal exam numbers."""
    from academy.domain.tools.question_splitter import QuestionRegion

    page0 = [
        QuestionRegion(number=n, bbox=(0, 0, 500, 100), page_index=0)
        for n in (10, 11, 12, 13, 429)
    ]

    out = validate_anchors_across_pages([page0])

    assert [r.number for r in out[0]] == [10, 11, 12, 13]


def test_validate_preserves_sparse_official_exam_excerpt_numbers():
    """발췌형 공식 문항 번호는 큰 gap이 있어도 tail anchor 묶음이면 유지."""
    from academy.domain.tools.question_splitter import QuestionRegion

    page0 = [
        QuestionRegion(number=n, bbox=(0, 0, 500, 100), page_index=0)
        for n in (1, 2, 3, 4, 11)
    ]
    page1 = [
        QuestionRegion(number=n, bbox=(0, 0, 500, 100), page_index=1)
        for n in (13, 19, 20)
    ]

    out = validate_anchors_across_pages([page0, page1])

    nums_kept = [r.number for page in out for r in page]
    assert nums_kept == [1, 2, 3, 4, 11, 13, 19, 20]


def test_split_questions_ignores_exam_header_page_number():
    """문제지 헤더의 페이지 번호는 문항 1 anchor가 아니다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(text="1", x0=30, y0=42, x1=52, y1=62),
        TB(text="2028학년도 대학수학능력시험 예시문항 문제지", x0=180, y0=45, x1=520, y1=72),
        TB(text="과학탐구 영역(통합과학)", x0=210, y0=90, x1=500, y1=120),
        TB(text="2. 표는 길이의 측정 표준에 대한 내용이다.", x0=55, y0=180, x1=430, y1=205),
        TB(text="이에 대한 설명으로 옳은 것은?", x0=55, y0=300, x1=430, y1=325),
        TB(text="3. 다음은 구리를 이용한 실험이다.", x0=55, y0=430, x1=430, y1=455),
        TB(text="4. 다음은 지구를 구성하는 물질에 대한 설명이다.", x0=520, y0=180, x1=900, y1=205),
    ]

    regions = split_questions(blocks, 1000.0, 1400.0, paper_type=pt)

    assert [r.number for r in regions] == [2, 3, 4]


def test_split_questions_ignores_exam_header_table_value_block():
    """시험 정보 표의 값 블록 첫 줄 숫자는 문항 anchor가 아니다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(
            text="학년\n과목\n고사일\n선택형 문항 번호\n배점\n서답형 문항 번호\n총 면수",
            x0=44,
            y0=70,
            x1=545,
            y1=92,
        ),
        TB(
            text="2\n생명과학Ⅰ\n25\n4월23일(수)\n1번~20번\n65점\n1번~5번\n35점\n8면",
            x0=44,
            y0=97,
            x1=545,
            y1=108,
        ),
        TB(text="2025학년도 1학기 중간고사 문제지", x0=170, y0=128, x1=470, y1=150),
        TB(text="1. 생물의 특성에 대한 설명으로 옳은 것은?", x0=55, y0=183, x1=430, y1=206),
        TB(text="① 물질대사를 하지 않는다. ② 자극에 반응한다.", x0=55, y0=250, x1=430, y1=272),
        TB(text="2. 그림은 세포 소기관을 나타낸 것이다.", x0=55, y0=382, x1=430, y1=405),
        TB(text="3. 다음은 효소 반응에 대한 설명이다.", x0=520, y0=183, x1=900, y1=206),
    ]

    regions = split_questions(blocks, 1000.0, 1400.0, paper_type=pt)
    workbook_regions = split_questions(
        blocks,
        1000.0,
        1400.0,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [1, 2, 3]
    assert [r.number for r in workbook_regions] == [1, 2, 3]


def test_section_subitems_do_not_cut_written_question_region():
    """서답형 본문 (1)(2)는 별도 문제 anchor가 아니라 같은 문항 내용이다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(text="서답형3. 다음은 대뇌 겉질에 관한 자료이다. [총 5점]", x0=40, y0=40, x1=440, y1=62),
        TB(text="그림 (가)는 뇌의 구조이다.", x0=45, y0=120, x1=430, y1=150),
        TB(text="(1) 그림 (가)에서 A~F 중 반사를 담당하는 부분을 쓰시오.", x0=50, y0=250, x1=430, y1=275),
        TB(text="(2) 사람의 뇌 G가 손상되면 어떤 현상이 나타나는가?", x0=50, y0=360, x1=430, y1=385),
        TB(text="서답형4. 그림은 심장에 분포한 자율 신경을 나타낸 것이다.", x0=520, y0=40, x1=930, y1=62),
        TB(text="(1) 신경 (가)와 (나)의 이름을 쓰시오.", x0=530, y0=260, x1=920, y1=285),
    ]

    regions = split_questions(blocks, 1000.0, 1400.0, paper_type=pt)

    assert [r.number for r in regions] == [103, 104]
    assert regions[0].bbox[3] > 380


def test_split_questions_handles_wrapped_written_section_labels_and_footer_numbers():
    """PyMuPDF may split `[서답형 1]` into `서답형 \\n[\\n1]`.

    The section label should start a written-response problem, while footer
    checklist numbers remain outside the detected question set.
    """
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(text="서답형 \n[\n1] 다음은 6가지 분자의 분자식을 나타낸 것이다.", x0=40, y0=180, x1=460, y1=205),
        TB(text="(1) 주어진 분자 중 무극성 공유 결합이 있는 분자를 고르시오.", x0=55, y0=260, x1=450, y1=285),
        TB(text="서답형 \n[\n2] 그림은 폼알데하이드의 구조식을 나타낸 것이다.", x0=40, y0=520, x1=460, y1=545),
        TB(text="(1) 올바른 루이스 구조식을 나타내시오.", x0=55, y0=620, x1=450, y1=645),
        TB(text="서답형 \n[\n3] 염화 수소 분자와 산소 분자를 비교한 것이다.", x0=540, y0=180, x1=960, y1=205),
        TB(text="(1) 각 분자의 극성 여부를 서술하시오.", x0=555, y0=260, x1=950, y1=285),
        TB(text="서답형 \n[\n4] 그림은 이온 사이의 거리와 에너지 변화를 나타낸 것이다.", x0=540, y0=520, x1=960, y1=545),
        TB(text="(1) 점 A에서 작용하는 힘을 서술하시오.", x0=555, y0=620, x1=950, y1=645),
        TB(text="1. 답안지의 해당란을 확인하십시오.", x0=560, y0=1180, x1=940, y1=1200),
        TB(text="2. 저작권에 의해 전재와 복제는 금지됩니다.", x0=560, y0=1220, x1=940, y1=1240),
    ]

    regions = split_questions(blocks, 1000.0, 1400.0, paper_type=pt)

    assert [r.number for r in regions] == [101, 102, 103, 104]
    assert all(r.number not in {1, 2} for r in regions)


def test_parenthesized_subitems_under_large_main_question_stay_inside_region():
    """43번 같은 main 문항 아래 (1)(2)는 독립 1번/2번 문항이 아니다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(text="43. 그림은 생명체를 구성하는 물질 (가)~(다)를 나타낸 것이다.", x0=40, y0=80, x1=450, y1=105),
        TB(text="(1) (가)~(다), ㉠에 해당하는 물질의 이름을 각각 쓰시오.", x0=50, y0=380, x1=450, y1=405),
        TB(text="(2) (가)~(다)의 기본 단위체를 각각 쓰시오.", x0=50, y0=520, x1=450, y1=545),
        TB(text="44. 단백질에 대한 설명으로 옳지 않은 것만을 모두 고르면?", x0=540, y0=80, x1=930, y1=105),
    ]

    regions = split_questions(blocks, 1000.0, 1400.0, paper_type=pt)

    assert [r.number for r in regions] == [43, 44]
    assert regions[0].bbox[3] > 540


def test_split_section_marginal_number_uses_section_number_space():
    """쪼개진 '서답형 6.'의 standalone 숫자는 일반 6번으로 중복 제거되지 않는다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(text="서답형5. 다음은 민말이집 신경 A와 B에 대한 자료이다.", x0=40, y0=40, x1=440, y1=62),
        TB(text="(1) 그림에서 ㄴ과 같은 상태를 쓰시오.", x0=50, y0=620, x1=430, y1=645),
        TB(text="6.", x0=620, y0=40, x1=642, y1=62),
        TB(text="서답형2. 그림은 지역 A에서 천이가 일어날 때 군집의 높이 변화를 나타낸 것이다.", x0=520, y0=42, x1=930, y1=64),
        TB(text="(1) ㄴ과 ㄷ의 이름을 각각 쓰시오.", x0=530, y0=260, x1=920, y1=285),
        TB(text="7.", x0=620, y0=430, x1=642, y1=452),
        TB(text="서답형4. 표 (가)는 지역 P에서 조사한 식물 종 개체 수를 나타낸 것이다.", x0=520, y0=432, x1=930, y1=454),
        TB(text="(1) ⓐ와 ⓑ에 해당하는 수를 각각 쓰시오.", x0=530, y0=720, x1=920, y1=745),
    ]

    regions = split_questions(
        blocks,
        1000.0,
        1400.0,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [105, 106, 107]


def test_footer_folio_does_not_expand_last_question_bbox():
    """하단 '1/6' folio는 마지막 문항의 실제 content로 보지 않는다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(text="2. 그림 (가)는 사람의 몸을 구성하는 원소의 질량비이다.", x0=70, y0=250, x1=450, y1=275),
        TB(text="이에 대한 옳은 설명만을 <보기>에서 있는 대로 고른 것은?", x0=70, y0=520, x1=450, y1=545),
        TB(text="5. 그림 (가)는 결합 구조를 나타낸 것이다.", x0=540, y0=250, x1=920, y1=275),
        TB(text="1/6", x0=455, y0=1260, x1=505, y1=1300),
    ]

    regions = split_questions(blocks, 1000.0, 1400.0, paper_type=pt)
    by_number = {r.number: r for r in regions}

    assert by_number[2].bbox[3] < 700


def test_validate_preserves_per_page_restart_workbook():
    """워크북/메인자료처럼 각 페이지가 anchor 1부터 다시 시작하는 doc은
    cross-page dedup으로 후속 페이지 anchor를 전부 drop 시키지 않아야 한다.

    실측 (T2 sample 지권의 변화 메인자료, 73 페이지): pre-fix 264 → 48 (81.8% drop).
    이 결함이 학원장 manual_create 4096건 (24% clean_pdf_dual under-cut) 의 본질.
    """
    from academy.domain.tools.question_splitter import QuestionRegion

    # 5 페이지 × anchor [1, 2, 3] 리셋 (전형적 워크북 패턴)
    pages = [
        [
            QuestionRegion(number=n, bbox=(0, 0, 500, 100), page_index=i)
            for n in (1, 2, 3)
        ]
        for i in range(5)
    ]
    out = validate_anchors_across_pages(pages)
    # 페이지마다 anchor 3개 전부 유지 — 15 region 총합
    total = sum(len(p) for p in out)
    assert total == 15, f"per-page-restart 패턴 워크북 anchor 손실: total={total}"
    # 각 페이지 anchor 1, 2, 3 그대로
    for i, page in enumerate(out):
        assert [r.number for r in page] == [1, 2, 3], (
            f"page {i}: {[r.number for r in page]}"
        )


def test_validate_per_page_restart_still_drops_outliers_per_page():
    """per-page-restart 모드에서도 페이지 안 outlier (예: 46) 는 drop.

    Why: 페이지 안에 "1, 2, 3, 4, 5, 46" 처럼 본문 false anchor 가 잡혔으면
    여전히 outlier 제거가 필요. global dedup 만 끄고 outlier 는 유지.
    """
    from academy.domain.tools.question_splitter import QuestionRegion

    # per-page-restart 패턴 — 각 페이지가 1-5 + outlier 46
    pages = [
        [
            QuestionRegion(number=n, bbox=(0, 0, 500, 100), page_index=i)
            for n in (1, 2, 3, 4, 5, 46)
        ]
        for i in range(5)
    ]
    out = validate_anchors_across_pages(pages)
    # outlier 46 은 각 페이지에서 drop, 1-5 는 유지
    for i, page in enumerate(out):
        nums = [r.number for r in page]
        assert 46 not in nums, f"page {i} outlier 46 not dropped: {nums}"
        assert nums == [1, 2, 3, 4, 5], f"page {i}: {nums}"


def test_validate_preserves_dense_fill_leading_continuation_before_restart():
    """Dense worksheet page can carry previous row 13 above a restarted 1-8 section."""
    from academy.domain.tools.question_splitter import QuestionRegion

    def row(num: int, page_idx: int, top: float) -> QuestionRegion:
        return QuestionRegion(
            number=num,
            bbox=(0.0, top, 100.0, top + 20.0),
            page_index=page_idx,
            semantic_flags=("short_workbook_prompt",),
        )

    pages = [
        [row(n, 0, n * 30.0) for n in range(1, 13)],
        [row(13, 1, 20.0)] + [row(n, 1, 100.0 + n * 30.0) for n in range(1, 9)],
        [row(n, 2, n * 30.0) for n in range(1, 5)],
        [row(n, 3, n * 30.0) for n in range(1, 5)],
        [row(n, 4, n * 30.0) for n in range(1, 5)],
    ]

    out = validate_anchors_across_pages(pages)

    assert [r.number for r in out[1]] == [13, 1, 2, 3, 4, 5, 6, 7, 8]


def test_validate_preserves_late_section_restart_after_high_sequence():
    """연속 번호 자료 뒤쪽의 새 기출 섹션 1번 재시작은 전역 중복으로 버리지 않는다."""
    from academy.domain.tools.question_splitter import QuestionRegion

    pages = [
        [
            QuestionRegion(
                number=i * 2 + 1,
                bbox=(0, 0, 500, 80),
                page_index=i,
            ),
            QuestionRegion(
                number=i * 2 + 2,
                bbox=(0, 80, 500, 160),
                page_index=i,
            ),
        ]
        for i in range(20)
    ]
    pages.extend(
        [
            [
                QuestionRegion(number=1, bbox=(0, 0, 500, 80), page_index=20),
                QuestionRegion(number=2, bbox=(0, 80, 500, 160), page_index=20),
            ],
            [
                QuestionRegion(number=3, bbox=(0, 0, 500, 80), page_index=21),
                QuestionRegion(number=4, bbox=(0, 80, 500, 160), page_index=21),
            ],
            [
                QuestionRegion(number=5, bbox=(0, 0, 500, 80), page_index=22),
                QuestionRegion(number=6, bbox=(0, 80, 500, 160), page_index=22),
            ],
            [
                QuestionRegion(number=7, bbox=(0, 0, 500, 80), page_index=23),
                QuestionRegion(number=8, bbox=(0, 80, 500, 160), page_index=23),
            ],
            [
                QuestionRegion(number=1, bbox=(0, 120, 500, 150), page_index=24),
                QuestionRegion(number=2, bbox=(0, 150, 500, 180), page_index=24),
                QuestionRegion(number=3, bbox=(0, 180, 500, 210), page_index=24),
                QuestionRegion(number=9, bbox=(0, 0, 500, 120), page_index=24),
            ],
        ]
    )

    out = validate_anchors_across_pages(pages)

    assert [r.number for r in out[20]] == [1, 2]
    assert [r.number for r in out[21]] == [3, 4]
    assert [r.number for r in out[22]] == [5, 6]
    assert [r.number for r in out[23]] == [7, 8]
    assert [r.number for r in out[24]] == [9]


def test_validate_preserves_step_section_restart_after_short_sequence():
    """Step 1의 1-12 뒤 Step 2가 1번부터 다시 시작해도 버리지 않는다."""
    from academy.domain.tools.question_splitter import QuestionRegion

    pages = [
        [
            QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=0)
            for i, n in enumerate((1, 2, 3, 4))
        ],
        [
            QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=1)
            for i, n in enumerate((5, 6, 7, 8))
        ],
        [
            QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=2)
            for i, n in enumerate((9, 10, 11, 12))
        ],
        [
            QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=3)
            for i, n in enumerate((1, 2, 3, 4))
        ],
        [
            QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=4)
            for i, n in enumerate((5, 6, 7, 8))
        ],
        [
            QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=5)
            for i, n in enumerate((9, 10, 11, 12))
        ],
    ]

    out = validate_anchors_across_pages(pages)

    assert [r.number for r in out[3]] == [1, 2, 3, 4]
    assert [r.number for r in out[4]] == [5, 6, 7, 8]
    assert [r.number for r in out[5]] == [9, 10, 11, 12]


def test_validate_preserves_consecutive_late_section_restarts():
    """후반부 새 섹션이 끝난 뒤 또 1번부터 시작하는 다음 섹션도 보존한다."""
    from academy.domain.tools.question_splitter import QuestionRegion

    pages = [
        [
            QuestionRegion(
                number=i * 2 + 1,
                bbox=(0, 0, 500, 80),
                page_index=i,
            ),
            QuestionRegion(
                number=i * 2 + 2,
                bbox=(0, 80, 500, 160),
                page_index=i,
            ),
        ]
        for i in range(20)
    ]
    pages.extend(
        [
            [
                QuestionRegion(number=1, bbox=(0, 0, 500, 80), page_index=20),
                QuestionRegion(number=2, bbox=(0, 80, 500, 160), page_index=20),
            ],
            [
                QuestionRegion(number=3, bbox=(0, 0, 500, 80), page_index=21),
                QuestionRegion(number=4, bbox=(0, 80, 500, 160), page_index=21),
            ],
            [
                QuestionRegion(number=5, bbox=(0, 0, 500, 80), page_index=22),
                QuestionRegion(number=6, bbox=(0, 80, 500, 160), page_index=22),
            ],
            [
                QuestionRegion(number=7, bbox=(0, 0, 500, 80), page_index=23),
                QuestionRegion(number=8, bbox=(0, 80, 500, 160), page_index=23),
            ],
            [
                QuestionRegion(number=1, bbox=(0, 0, 500, 80), page_index=24),
                QuestionRegion(number=2, bbox=(0, 80, 500, 160), page_index=24),
            ],
            [
                QuestionRegion(number=3, bbox=(0, 0, 500, 80), page_index=25),
                QuestionRegion(number=4, bbox=(0, 80, 500, 160), page_index=25),
            ],
        ]
    )

    out = validate_anchors_across_pages(pages)

    assert [r.number for r in out[20]] == [1, 2]
    assert [r.number for r in out[21]] == [3, 4]
    assert [r.number for r in out[22]] == [5, 6]
    assert [r.number for r in out[23]] == [7, 8]
    assert [r.number for r in out[24]] == [1, 2]
    assert [r.number for r in out[25]] == [3, 4]


def test_validate_rejects_mixed_false_low_anchors_after_short_sequence():
    """짧은 연속 번호 뒤 실제 다음 번호와 섞인 낮은 번호 오탐은 재시작으로 보지 않는다."""
    from academy.domain.tools.question_splitter import QuestionRegion

    page0 = [
        QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=0)
        for i, n in enumerate((1, 2, 3, 4))
    ]
    page1 = [
        QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=1)
        for i, n in enumerate((5, 6, 7, 8))
    ]
    page2 = [
        QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=2)
        for i, n in enumerate((1, 2, 3, 9, 10, 11, 12))
    ]
    page3 = [
        QuestionRegion(number=n, bbox=(0, i * 80, 500, i * 80 + 70), page_index=3)
        for i, n in enumerate((13, 14, 15, 16))
    ]

    out = validate_anchors_across_pages([page0, page1, page2, page3])

    assert [r.number for r in out[2]] == [9, 10, 11, 12]


def test_validate_still_drops_unconfirmed_late_low_false_anchors():
    """다음 페이지 연속성이 없는 후반부 낮은 번호 오탐은 기존처럼 중복 제거한다."""
    from academy.domain.tools.question_splitter import QuestionRegion

    pages = [
        [
            QuestionRegion(
                number=i * 2 + 1,
                bbox=(0, 0, 500, 80),
                page_index=i,
            ),
            QuestionRegion(
                number=i * 2 + 2,
                bbox=(0, 80, 500, 160),
                page_index=i,
            ),
        ]
        for i in range(15)
    ]
    pages.append(
        [
            QuestionRegion(number=1, bbox=(0, 0, 500, 80), page_index=15),
            QuestionRegion(number=2, bbox=(0, 80, 500, 160), page_index=15),
            QuestionRegion(number=3, bbox=(0, 160, 500, 240), page_index=15),
        ]
    )

    out = validate_anchors_across_pages(pages)

    assert out[15] == []


def test_marginal_anchor_extracts_standalone_number():
    """marginal column standalone 'N.' / 'N' block 만 인식, 본문 anchor 거부."""
    from academy.domain.tools.question_splitter import _extract_marginal_question_number

    # accept
    assert _extract_marginal_question_number("3.") == 3
    assert _extract_marginal_question_number("3") == 3
    assert _extract_marginal_question_number("10.") == 10
    assert _extract_marginal_question_number("3 . ") == 3
    assert _extract_marginal_question_number("4 4.") == 4
    # reject (본문 anchor 형태)
    assert _extract_marginal_question_number("3. 다음") is None
    assert _extract_marginal_question_number("3.0") is None
    assert _extract_marginal_question_number("1)") is None
    assert _extract_marginal_question_number("501.") is None  # > 500 (max legit)


def test_question_anchor_accepts_ocr_slash_separator():
    """학생 시험지 사진 OCR이 '1.'을 '1 /'로 읽어도 문항 anchor로 인정한다."""
    from academy.domain.tools.question_splitter import _extract_question_number

    assert _extract_question_number("1 / 비생물과 구분되는 생물의 특성") == 1
    assert _extract_question_number("1/2 비율") is None
    assert _extract_question_number("[9, 10] 그림은 주기율표의 일부") == 9


def test_split_questions_prefer_marginal_workbook_main_only():
    """workbook 모드: marginal anchor 만 사용, 본문 sub-item anchor reject.

    학원장 mental model: Q3 (그림+sub-items 1-7) = 1 problem. sub-item 1, 2, ...
    들은 Q3 의 부분이지 독립 문제 아님.
    """
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 595.0, 842.0
    blocks = [
        # Q3 marginal big number
        TextBlock(text="3.", x0=28, y0=80, x1=42, y1=95),
        # Q3 stem + sub-items
        TextBlock(text="그림은 화산 활동에 관한 여러 가지 현상", x0=60, y0=80, x1=500, y1=95),
        TextBlock(text="1. 화산 분출물 (O/X)", x0=80, y0=120, x1=500, y1=135),
        TextBlock(text="2. 자연 현상 (O/X)", x0=80, y0=140, x1=500, y1=155),
        TextBlock(text="3. 마그마 (O/X)", x0=80, y0=160, x1=500, y1=175),
        # Q4 marginal big number
        TextBlock(text="4.", x0=28, y0=420, x1=42, y1=435),
        TextBlock(text="그림은 한 경계에서", x0=60, y0=420, x1=500, y1=435),
        TextBlock(text="1. A는 발산형 (O/X)", x0=80, y0=460, x1=500, y1=475),
        TextBlock(text="2. B는 보존형 (O/X)", x0=80, y0=480, x1=500, y1=495),
    ]
    regions = split_questions(
        blocks, pw, ph, page_index=5, prefer_marginal=True,
    )
    # 2 main anchors (Q3, Q4) only, sub-items rejected
    assert len(regions) == 2
    nums = sorted(r.number for r in regions)
    assert nums == [3, 4]
    # Q3 bbox spans from marginal "3." through sub-items down to before "4."
    q3 = [r for r in regions if r.number == 3][0]
    assert q3.bbox[1] < 100  # starts at top of page
    assert q3.bbox[3] > 200  # extends well past sub-items


def test_split_questions_dual_column_marginal_keeps_right_column():
    """2단 워크북의 우측 column 큰 번호도 marginal anchor로 인정한다.

    T1 실제 자료 `항상성과 호르몬 메인자료` 재처리에서 좌측 Q1/Q2/Q5/Q6만
    잘리고 우측 Q3/Q4/Q7/Q8이 통째 누락된 회귀를 고정한다.
    """
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 600.0, 840.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TextBlock(text="1.", x0=24, y0=90, x1=36, y1=105),
        TextBlock(text="1번 본문", x0=55, y0=90, x1=260, y1=105),
        TextBlock(text="2.", x0=24, y0=350, x1=36, y1=365),
        TextBlock(text="2번 본문", x0=55, y0=350, x1=260, y1=365),
        TextBlock(text="3.", x0=324, y0=90, x1=336, y1=105),
        TextBlock(text="3번 본문", x0=355, y0=90, x1=560, y1=105),
        TextBlock(text="4.", x0=324, y0=350, x1=336, y1=365),
        TextBlock(text="4번 본문", x0=355, y0=350, x1=560, y1=365),
        # 본문 하위 번호는 main problem이 아니므로 marginal-only에서 제외되어야 한다.
        TextBlock(text="1. 보기 ㄱ", x0=90, y0=150, x1=250, y1=165),
        TextBlock(text="2. 보기 ㄴ", x0=390, y0=150, x1=550, y1=165),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [1, 2, 3, 4]
    right_regions = [r for r in regions if r.number in (3, 4)]
    assert all(r.bbox[0] >= pw * 0.5 - 2 for r in right_regions)


def test_split_questions_dual_marginal_accepts_gutter_overlap_right_anchor():
    """우측 column 번호 block이 gutter를 살짝 침범해도 marginal anchor로 유지한다.

    T2 doc302 p15: PyMuPDF가 우측 Q7/Q8 block의 x0를 mid_x보다 약간 작게
    반환했다. workbook marginal-only 모드가 이를 body anchor로 분류하면 Q7/Q8이
    통째 누락되어 4문항 페이지가 2문항으로 잘린다.
    """
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 612.0, 864.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.85,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TextBlock(text="5.\n다음은 측정과 관련된 설명이다.", x0=42.5, y0=130.5, x1=280.6, y1=145.2),
        TextBlock(text="본문 5", x0=48.1, y0=176.3, x1=274.9, y1=196.9),
        TextBlock(text="6.\n다음은 자연과 관련된 설명이다.", x0=42.5, y0=428.0, x1=280.6, y1=442.7),
        TextBlock(text="본문 6", x0=48.1, y0=470.2, x1=277.1, y1=529.1),
        # x0=303.2 is slightly smaller than mid_x=306.0, but the block center is right-column.
        TextBlock(text="7.\n다양한 형태의 습도, 자기, 전기 신호", x0=303.2, y0=130.5, x1=541.3, y1=145.2),
        TextBlock(text="본문 7", x0=324.7, y0=153.4, x1=511.3, y1=162.4),
        TextBlock(text="8.\n아래의 빈칸에 알맞은 말을 써 넣으시오.", x0=303.2, y0=428.9, x1=468.9, y1=443.7),
        TextBlock(text="본문 8", x0=303.2, y0=465.3, x1=541.3, y1=582.2),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=14,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [5, 6, 7, 8]
    assert {r.number for r in regions if r.bbox[0] >= pw * 0.5 - 2} == {7, 8}


def test_split_questions_drops_shared_material_procedure_numbers():
    """공통자료 `[1~3]` 안의 실험 절차 번호는 main question anchor가 아니다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 595.0, 841.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.85,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TextBlock(text="[1~3] 다음은 소화효소 실험이다. 물음에 답하시오.", x0=28, y0=76, x1=292, y1=100),
        TextBlock(text="[실험 과정]", x0=36, y0=119, x1=80, y1=128),
        TextBlock(text="1. 물 10mL를 준비한다.", x0=36, y0=133, x1=295, y1=142),
        TextBlock(text="2. 소화제를 섞는다.", x0=36, y0=162, x1=295, y1=171),
        TextBlock(text="3. 색깔 변화를 관찰한다.", x0=36, y0=191, x1=295, y1=200),
        TextBlock(text="4. 셀로판 튜브를 준비한다.", x0=36, y0=234, x1=295, y1=243),
        TextBlock(text="5. 비커에 넣어 둔다.", x0=36, y0=346, x1=295, y1=355),
        TextBlock(text="6. 용액을 옮긴다.", x0=36, y0=479, x1=295, y1=488),
        TextBlock(text="7. 색 변화를 관찰한다.", x0=36, y0=522, x1=295, y1=531),
        TextBlock(text="1 1.", x0=311, y0=75, x1=439, y1=86),
        TextBlock(text="(가)~(다) 중 녹말이 분해된 셀로판 튜브", x0=311, y0=89, x1=559, y1=98),
        TextBlock(text="2 2.", x0=311, y0=299, x1=439, y1=310),
        TextBlock(text="시험관 b와 c에서 색깔이 변한 까닭", x0=311, y0=313, x1=559, y1=322),
        TextBlock(text="3 3.", x0=311, y0=465, x1=439, y1=476),
        TextBlock(text="소화효소의 역할을 설명하시오.", x0=311, y0=479, x1=559, y1=488),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [1, 2, 3]
    assert all(r.bbox[0] >= pw * 0.5 - 2 for r in regions)


def test_source_prefixed_anchor_wins_over_false_marginal_same_number():
    """출처 prefix 문항은 표 안의 단독 숫자 marginal 오탐보다 우선한다."""
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 595.0, 841.0
    blocks = [
        TextBlock(text="[2025 은광여고 기출] 3. 그림은 주기율표이다.", x0=28, y0=70, x1=284, y1=98),
        TextBlock(text="이에 대한 설명으로 옳은 것은?", x0=28, y0=220, x1=396, y1=229),
        TextBlock(text="3", x0=64, y0=484, x1=68, y1=493),  # table row number, not a question
        TextBlock(text="[2025 은광여고 기출] 4. 다음 문제", x0=28, y0=407, x1=193, y1=434),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        prefer_marginal=True,
    )

    by_num = {r.number: r for r in regions}
    assert sorted(by_num) == [3, 4]
    assert by_num[3].bbox[1] < 100


def test_split_questions_prefer_marginal_threshold_one_anchor():
    """prefer_marginal=True 면 marginal 1 개라도 있을 때 marginal-only.

    표지 페이지 + Q1 만 있는 워크북 첫 페이지 케이스. Q1 marginal "1." 만 keep,
    표지 텍스트에 잡힌 false anchor (body) 들 reject.
    """
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 595.0, 842.0
    blocks = [
        TextBlock(text="1.", x0=28, y0=200, x1=42, y1=215),
        TextBlock(text="2025년 5월 27일 기말고사 1차시 실시", x0=60, y0=200, x1=500, y1=215),
        # 표지에 false body anchor 잡힘
        TextBlock(text="3. 다음 항목을 모두 작성하시오", x0=80, y0=400, x1=500, y1=415),
    ]
    regions = split_questions(
        blocks, pw, ph, page_index=0, prefer_marginal=True,
    )
    # marginal anchor 1 만 keep
    assert len(regions) == 1
    assert regions[0].number == 1


def test_split_questions_no_prefer_marginal_school_exam_unchanged():
    """prefer_marginal=False (시험지 default) — marginal 1 개만 있어도 body anchor 그대로 사용.

    시험지에 우연히 standalone "1." 한 줄 등장해도 marginal-only 로 전환되지 않음.
    """
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 595.0, 842.0
    blocks = [
        # 우연한 marginal block (시험지 페이지 좌측 1번 표지)
        TextBlock(text="1.", x0=28, y0=80, x1=42, y1=95),
        # 시험지 본문 anchors
        TextBlock(text="2. 다음 문제를 풀이하시오", x0=60, y0=200, x1=500, y1=215),
        TextBlock(text="3. 다음 그림은 무엇을 나타내는가", x0=60, y0=400, x1=500, y1=415),
        TextBlock(text="4. 다음 중 옳은 것은", x0=60, y0=600, x1=500, y1=615),
    ]
    # prefer_marginal=False (default 시험지 모드): marginal 1 개 < 임계 2 → body 도 사용.
    # marginal "1." + body "2."/"3."/"4." 모두 keep → 4 regions.
    regions = split_questions(
        blocks, pw, ph, page_index=0, prefer_marginal=False,
    )
    assert len(regions) == 4
    nums = sorted(r.number for r in regions)
    assert nums == [1, 2, 3, 4]


def test_split_questions_scan_dual_ignores_false_marginal_only_switch():
    """스캔 시험지는 보기/손글씨 숫자 2개가 standalone이어도 body anchor를 버리지 않는다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 600.0, 840.0
    pt = PaperTypeResult(
        paper_type=PaperType.SCAN_DUAL,
        confidence=0.85,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=False,
    )
    blocks = [
        TextBlock(text="1. 첫 문항", x0=40, y0=100, x1=260, y1=120),
        TextBlock(text="2. 둘째 문항", x0=40, y0=360, x1=260, y1=380),
        TextBlock(text="2", x0=70, y0=700, x1=86, y1=718),  # 보기/손글씨 오인식
        TextBlock(text="3. 셋째 문항", x0=330, y0=100, x1=560, y1=120),
        TextBlock(text="2", x0=340, y0=700, x1=356, y1=718),  # 우측 column 오인식
        TextBlock(text="4. 넷째 문항", x0=330, y0=360, x1=560, y1=380),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        paper_type=pt,
        prefer_marginal=False,
    )

    assert [r.number for r in regions] == [1, 2, 3, 4]


def test_split_questions_scan_dual_filters_choice_number_outliers_before_cut():
    """보기 ⑤/⑦이 '5.'/'7.'로 OCR돼도 연속 문항 시퀀스만 남긴다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 600.0, 840.0
    pt = PaperTypeResult(
        paper_type=PaperType.SCAN_DUAL,
        confidence=0.85,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=True,
        has_embedded_text=False,
    )
    blocks = [
        TextBlock(text="1 / 비생물과 구분되는 생물의 특성", x0=45, y0=120, x1=280, y1=140),
        TextBlock(text="2. 그림 (가)와 (나)는 각각", x0=45, y0=360, x1=280, y1=380),
        TextBlock(text="5. (가)와 (나)는 세포막의 유무", x0=60, y0=650, x1=280, y1=670),
        TextBlock(text="3. 표는 생물의 특성의 예", x0=330, y0=120, x1=560, y1=140),
        TextBlock(text="7. (가)를 통해 종족을 유지한다", x0=345, y0=240, x1=560, y1=260),
        TextBlock(text="4. 표는 사람과 소나무 개체", x0=330, y0=360, x1=560, y1=380),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        paper_type=pt,
        prefer_marginal=False,
    )

    assert [r.number for r in regions] == [1, 2, 3, 4]
    q2 = next(r for r in regions if r.number == 2)
    assert q2.bbox[3] == ph  # false 5번 보기 앞에서 잘리지 않아야 함


def test_split_questions_scan_dual_expands_shared_range_material():
    """[9,10] 공통 자료는 묶인 각 문항 crop에 포함한다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 600.0, 840.0
    pt = PaperTypeResult(
        paper_type=PaperType.SCAN_DUAL,
        confidence=0.85,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=False,
    )
    blocks = [
        TextBlock(text="6. 왼쪽 문항", x0=40, y0=100, x1=260, y1=120),
        TextBlock(text="7. 왼쪽 둘째", x0=40, y0=400, x1=260, y1=420),
        TextBlock(text="[9, 10] 그림은 주기율표의 일부", x0=330, y0=100, x1=560, y1=120),
        TextBlock(text="9. A-D에 대한 설명", x0=330, y0=260, x1=560, y1=280),
        TextBlock(text="10. C와 D에 대한 설명", x0=330, y0=430, x1=560, y1=450),
        TextBlock(text="11. 실생활 물질", x0=330, y0=620, x1=560, y1=640),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        paper_type=pt,
        prefer_marginal=False,
    )

    by_num = {r.number: r for r in regions}
    assert [r.number for r in regions] == [6, 7, 9, 10, 11]
    assert by_num[9].bbox[1] < 110
    assert by_num[10].context_bbox is not None
    assert by_num[10].context_bbox[1] < 110
    assert by_num[10].bbox[1] < 110
    assert by_num[9].bbox[3] == by_num[10].bbox[3]
    assert by_num[10].bbox[3] <= by_num[11].bbox[1]
    assert "shared_context" in by_num[9].semantic_flags
    assert "shared_context_first" in by_num[9].semantic_flags
    assert "shared_context_later" in by_num[10].semantic_flags
    assert by_num[9].context_bbox == by_num[9].bbox
    assert by_num[9].audit_bbox == by_num[9].bbox
    assert by_num[10].audit_bbox is not None
    assert by_num[10].audit_bbox == by_num[10].bbox


def test_split_questions_shared_range_without_next_column_anchor_stops_at_group_bottom():
    """T2 doc302 p42: 마지막 좌측 shared group이 페이지 끝까지 과확장되면 안 된다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 612.0, 864.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.85,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TextBlock(
            text="[ 15~16 ] 그림은 주기율표의 일부를 나타낸 것이다.",
            x0=42.5,
            y0=130.9,
            x1=280.5,
            y1=152.5,
        ),
        TextBlock(text="물음에 답하시오.", x0=42.5, y0=160.0, x1=200.0, y1=175.0),
        TextBlock(text="15.\n위 A~E에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로", x0=42.5, y0=289.1, x1=283.1, y1=303.9),
        TextBlock(text="본문 15", x0=42.5, y0=340.0, x1=283.1, y1=451.4),
        TextBlock(text="16.\n위 A~E 중 탄소 화합물의 중심이 되는 원소의 기호를 쓰고,", x0=42.5, y0=455.4, x1=283.0, y1=470.2),
        TextBlock(text="본문 16", x0=42.5, y0=495.0, x1=282.0, y1=526.7),
        TextBlock(text="17.\n그림은 물과 이산화 탄소의 결합을 모형으로 나타낸 것이", x0=303.2, y0=130.5, x1=541.2, y1=145.2),
        TextBlock(text="본문 17", x0=303.2, y0=175.0, x1=540.0, y1=419.5),
        TextBlock(text="18.\n그림은 탄소 화합물의 주요 구성 원소인 A와 B의 바닥 상태", x0=303.2, y0=456.2, x1=543.9, y1=470.9),
        TextBlock(text="본문 18", x0=303.2, y0=500.0, x1=543.9, y1=772.9),
        TextBlock(text="43", x0=563.4, y0=819.8, x1=575.4, y1=830.8),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=42,
        paper_type=pt,
        prefer_marginal=True,
    )

    by_num = {r.number: r for r in regions}
    assert [r.number for r in regions] == [15, 16, 17, 18]
    assert by_num[15].bbox[1] < 140
    assert by_num[16].context_bbox is not None
    assert by_num[16].context_bbox[1] < 140
    assert by_num[16].bbox[1] < 140
    assert by_num[16].audit_bbox == by_num[16].body_bbox
    assert by_num[16].bbox[3] < ph * 0.70


def test_split_questions_full_width_anchor_stays_in_left_flow_for_y_end():
    """전폭 제목 block의 center가 gutter를 넘어도 다음 anchor 앞에서 잘린다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 595.0, 841.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.9,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TextBlock(text="37. 그림과 표를 보고 옳은 것을 고르시오.", x0=28.0, y0=66.0, x1=568.0, y1=79.0),
        TextBlock(text="37 본문", x0=64.0, y0=294.0, x1=500.0, y1=360.0),
        TextBlock(text="38. 다음 그림에 대한 설명으로 옳은 것은?", x0=28.0, y0=430.0, x1=382.0, y1=443.0),
        TextBlock(text="38 본문", x0=64.0, y0=510.0, x1=500.0, y1=650.0),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=35,
        paper_type=pt,
        prefer_marginal=True,
    )

    by_num = {r.number: r for r in regions}
    assert [r.number for r in regions] == [37, 38]
    assert by_num[37].bbox[3] < by_num[38].bbox[1]


def test_split_questions_semantic_flags_use_full_region_text():
    """번호 줄 아래 본문에 그림/표 문구가 있어도 시각문맥 문항으로 판정한다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 600.0, 840.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_SINGLE,
        confidence=0.9,
        is_dual_column=False,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TextBlock(text="1. 다음 자료에 대한 설명으로 옳은 것은?", x0=40, y0=100, x1=360, y1=120),
        TextBlock(text="그림은 생명체를 구성하는 물질을 나타낸 것이다.", x0=40, y0=145, x1=430, y1=165),
        TextBlock(text="2. 그 까닭을 설명하시오.", x0=40, y0=430, x1=360, y1=450),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        paper_type=pt,
        prefer_marginal=False,
    )

    by_num = {r.number: r for r in regions}
    assert "visual_context" in by_num[1].semantic_flags
    assert "visual_context" not in by_num[2].semantic_flags
    assert "reasoning_response" in by_num[2].semantic_flags


def test_prior_context_reference_does_not_match_word_suffix():
    """`단위`의 `위`는 선행 자료 참조가 아니다."""
    text = (
        "생명 시스템은 세포, 조직을 기본 단위로 하여 단계적으로 "
        "조직되어 개체를 구성한다."
    )

    assert _references_prior_context(text) is False
    assert _references_prior_context("위 그림을 보고 옳은 것을 고르시오.") is True


def test_question_type_label_belongs_to_following_question():
    """서술형/단답형 라벨은 앞 문항 끝이 아니라 다음 문항 시작에 포함한다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock, split_questions

    pw, ph = 600.0, 840.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_SINGLE,
        confidence=0.9,
        is_dual_column=False,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TextBlock(text="73. 그림은 태양의 내부 구조를 나타낸 것이다.", x0=70, y0=140, x1=520, y1=160),
        TextBlock(text="이에 대한 설명으로 옳은 것은?", x0=70, y0=320, x1=520, y1=340),
        TextBlock(text="서술형", x0=70, y0=500, x1=100, y1=512),
        TextBlock(text="74. 빅뱅 이후 우주의 밀도가 어떻게 변했는지 설명하시오.", x0=70, y0=530, x1=520, y1=550),
        TextBlock(text="서술형", x0=70, y0=650, x1=100, y1=662),
        TextBlock(text="75. 수소와 헬륨의 질량비를 쓰시오.", x0=70, y0=680, x1=520, y1=700),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        paper_type=pt,
        prefer_marginal=False,
    )
    by_num = {r.number: r for r in regions}

    assert by_num[73].bbox[3] < 500
    assert by_num[74].bbox[1] <= 500
    assert by_num[74].bbox[3] < 650
    assert by_num[75].bbox[1] <= 650


def test_validate_detection_threshold_protects_school_exam():
    """시험지에 본문 오탐으로 anchor 1개가 한 번 더 잡혀도 per-page-restart 로 오인식되면 안 됨.

    시험지에서 false anchor 는 isolated 패턴. 임계 (3 anchor × 2 페이지 이상) 미만이면
    continuous mode 유지.
    """
    from academy.domain.tools.question_splitter import (
        QuestionRegion,
        _detect_per_page_restart,
    )

    page0 = [
        QuestionRegion(number=n, bbox=(0, 0, 500, 100), page_index=0)
        for n in (1, 2, 3, 4, 5)
    ]
    # 본문 오탐: 페이지 1 에 anchor 4 ("그림 4는") 한 번 더 등장
    page1 = [
        QuestionRegion(number=4, bbox=(0, 0, 500, 100), page_index=1),
        QuestionRegion(number=6, bbox=(0, 100, 500, 200), page_index=1),
        QuestionRegion(number=7, bbox=(0, 200, 500, 300), page_index=1),
    ]
    assert not _detect_per_page_restart([page0, page1]), (
        "single false anchor 가 per-page-restart 로 오감지되면 안 됨"
    )
    # validate 가 continuous mode 로 동작 — 중복 anchor 4 drop
    out = validate_anchors_across_pages([page0, page1])
    p1_nums = [r.number for r in out[1]]
    assert 4 not in p1_nums, "중복 anchor 4 dedup 미작동"
    assert p1_nums == [6, 7]


def test_learning_concept_page_with_sparse_circled_section_numbers_is_non_question():
    """개념 설명 페이지의 ①/② 섹션 번호를 보기 번호로 오인하지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="3) 시간과 길이의 측정", x0=80, y0=120, x1=420, y1=145),
        TB(text="과거 : 천문학적 현상을 이용하여 시간을 측정하고", x0=80, y0=190, x1=520, y1=215),
        TB(text="현대 : 시간과 길이의 측정 기술이 발전", x0=80, y0=250, x1=520, y1=275),
        TB(text="① 시간 측정", x0=80, y0=340, x1=180, y1=365),
        TB(text="세슘 원자시계를 이용하여 정밀한 시간 측정 가능", x0=180, y0=340, x1=520, y1=365),
        TB(text="초고속 투과 전자 현미경을 이용하여 움직임을 분석할 수 있음", x0=180, y0=390, x1=550, y1=415),
        TB(text="② 길이 측정", x0=80, y0=500, x1=180, y1=525),
        TB(text="레이저 길이 측정기를 이용하여 정밀한 길이 측정 가능", x0=180, y0=500, x1=540, y1=525),
        TB(text="위성 위치 확인 시스템 GPS를 이용하여 위치를 확인할 수 있음", x0=180, y0=550, x1=550, y1=575),
        TB(text="추가 설명 : 세슘 원자시계", x0=620, y0=200, x1=780, y1=225),
        TB(text="원자에서 나오는 빛의 진동수를 이용하여 시간을 측정한다.", x0=620, y0=245, x1=800, y1=310),
        TB(text="추가 설명 : GPS 시스템", x0=620, y0=430, x1=780, y1=455),
        TB(text="인공위성을 통하여 위치, 시각 등의 정보를 알 수 있다.", x0=620, y0=480, x1=800, y1=540),
    ]

    assert is_non_question_page(blocks) is True


def test_answer_sheet_written_answer_table_is_non_question():
    """서답형 답안지 표는 문항 crop 대상이 아니다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="문항\n번호\n유형\n배점\n정답", x0=40, y0=60, x1=520, y1=90),
        TB(text="서답형1\n서술형", x0=40, y0=120, x1=160, y1=150),
        TB(text="밀도는 유도량에 해당한다.", x0=180, y0=120, x1=520, y1=150),
        TB(text="서답형2\n서술형", x0=40, y0=180, x1=160, y1=210),
        TB(text="(1) 별의 중심부 온도가 높다.", x0=180, y0=180, x1=520, y1=210),
        TB(text="서답형3\n서술형", x0=40, y0=240, x1=160, y1=270),
        TB(text="A와 B가 같다.", x0=180, y0=240, x1=520, y1=270),
    ]

    assert is_non_question_page(blocks) is True


def test_fill_in_workbook_page_with_write_instruction_is_question():
    """빈칸형 워크북의 '적으시오' 지시문은 개념 페이지 필터보다 강하다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="신민T WORKBOOK", x0=20, y0=20, x1=180, y1=42),
        TB(text="3.\n다음은 기권에 대한 그림과 내용이다. 빈칸에 올바른 답을 적으시오.", x0=30, y0=80, x1=360, y1=100),
        TB(text="1. 기권은 (   )개의 층상구조로 이루어져 있다.", x0=30, y0=320, x1=560, y1=340),
        TB(text="2. 대류가 일어나는 (       )한 대기층은 (       )이다.", x0=30, y0=360, x1=560, y1=380),
        TB(text="8. 열권은 높이 올라갈수록 (       ) 에너지를 흡수하여 기온이 (     )진다.", x0=30, y0=520, x1=560, y1=540),
    ]

    assert is_non_question_page(blocks) is False


def test_unit_inner_concept_pages_with_numbered_titles_are_non_question():
    """단원 내지의 '페이지 | N. 단원명' 번호를 문항 번호로 오인하지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="7 | 2 . 빅뱅과 우주 초기 원소의 생성", x0=20, y0=80, x1=360, y1=110),
        TB(text="( 1 ) 기본 입자 : 빅뱅 직후에 생긴 입자", x0=70, y0=160, x1=420, y1=190),
        TB(text="( 2 ) 양성자 , 중성자", x0=70, y0=220, x1=300, y1=250),
        TB(text="참고 ! 섭씨 온도와 절대 온도", x0=500, y0=160, x1=720, y1=190),
        TB(text="-273℃ = 0K 절 = 섭 + 273", x0=500, y0=200, x1=720, y1=230),
        TB(text="질량과 전하는 실제 수치가 아닌 상대적 수치를 사용", x0=500, y0=240, x1=760, y1=270),
        TB(text="Ex ) 쿼크 3개가 모여 각각의 쿼크의 질량은 1 / 3", x0=500, y0=280, x1=780, y1=310),
        TB(text="쿼크 3개가 모여 하나의 양성자가 된다.", x0=500, y0=220, x1=780, y1=260),
        TB(text="up 쿼크 down 쿼크 전하량, 질량", x0=500, y0=300, x1=780, y1=340),
    ]

    assert is_non_question_page(blocks) is True


def test_unit_inner_concept_page_without_pipe_is_non_question():
    """페이지번호와 단원번호가 공백으로 붙은 개념 본문도 비문항으로 본다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="11 2 . 지구와 생명체를 구성하는 원소의 생성", x0=40, y0=80, x1=420, y1=110),
        TB(text="1 . 우주, 지구, 생명체를 구성하는 주요 원소의 질량비", x0=60, y0=140, x1=520, y1=170),
        TB(text="- 우주에서 가장 많은 원소 : 수소 > 헬륨", x0=80, y0=190, x1=430, y1=220),
        TB(text="- 지각에서 가장 많은 원소 : 산 > 규 > 알 > 철", x0=80, y0=240, x1=450, y1=270),
        TB(text="- 해양에서 가장 많은 원소 : 산 > 수 > 염 > 나", x0=80, y0=290, x1=450, y1=320),
        TB(text="- 대기에서 가장 많은 원소 : 질 > 산 > 아", x0=80, y0=340, x1=430, y1=370),
        TB(text="암석 성분 = SiO2 = 규산염 광물", x0=80, y0=410, x1=400, y1=440),
    ]

    assert is_non_question_page(blocks) is True


def test_dense_list_concept_page_without_step_is_non_question():
    """Step 이전 개념 본문의 1) 2) 3) 목록 번호를 문항 번호로 보지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="12 - 별의 탄생", x0=40, y0=80, x1=180, y1=110),
        TB(text="1 ) 성운의 형성 성간 물질 가스 구름 성운", x0=60, y0=140, x1=420, y1=170),
        TB(text="2 ) 원시별의 생성 원시별", x0=60, y0=210, x1=360, y1=240),
        TB(text="3 ) 별 ( 주계열성 ) 의 탄생 주계열성 수소 핵융합 반응", x0=60, y0=280, x1=500, y1=310),
        TB(text="중력 수축 = 내부 압력 별의 크기가 일정함", x0=60, y0=350, x1=480, y1=380),
        TB(text="중심부에서 수소 핵융합 반응이 일어나 에너지를 방출하는 천체", x0=60, y0=420, x1=560, y1=450),
        TB(text="참고 ! 성운 내부의 밀도가 큰 곳에서 여러 개의 원시별이 생성", x0=60, y0=490, x1=580, y1=520),
    ]

    assert is_non_question_page(blocks) is True


def test_past_exam_analysis_table_is_non_question():
    """기출 분석/문제 유형 표의 숫자를 문항 번호로 보지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="숙명여고 [빅뱅과 원소의 생성 4개년 기출 분석] 문제 유형", x0=40, y0=80, x1=520, y1=110),
        TB(text="2022 2023 2024 2025", x0=80, y0=170, x1=360, y1=200),
        TB(text="우주론 비교 스펙트럼 분석 빅뱅 우주론 1 빅뱅 우주론 2", x0=80, y0=250, x1=540, y1=280),
        TB(text="수소 헬륨 탄생과 질량비 우주배경복사 3", x0=80, y0=330, x1=540, y1=360),
    ]

    assert is_non_question_page(blocks) is True


def test_note_concept_page_is_non_question():
    """✑ Note 개념 본문의 단원 번호를 문항 번호로 보지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="빅뱅과 원소의 생성", x0=40, y0=70, x1=260, y1=100),
        TB(text="Ⅰ ✑ Note", x0=40, y0=110, x1=160, y1=140),
        TB(text="1. 빅뱅 우주론의 확립", x0=60, y0=160, x1=260, y1=190),
        TB(text="⑴ 우주와 관련된 주요 논쟁", x0=60, y0=220, x1=300, y1=250),
        TB(text="우주가 팽창하고 있음을 관측을 통해 확인하였다.", x0=80, y0=280, x1=520, y1=310),
        TB(text="정상 우주론과 빅뱅 우주론이 대립하였다.", x0=80, y0=340, x1=520, y1=370),
        TB(text="수소와 헬륨의 질량비, 우주 배경 복사가 증거이다.", x0=80, y0=400, x1=540, y1=430),
    ]

    assert is_non_question_page(blocks) is True


def test_biology_chapter_concept_page_is_non_question():
    """생명과학 CHAPTER 개념 본문의 번호를 문항 번호로 보지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="6 [ Li : Fe ] 생명과학은 철쌤 CHAPTER 04 신경계", x0=40, y0=70, x1=520, y1=100),
        TB(text="1. 중추 신경계", x0=60, y0=130, x1=240, y1=160),
        TB(text="1. 신경계 : 감각기에서 보내는 정보를 받아들이고 반응 명령을 전달하는 기관계", x0=70, y0=190, x1=560, y1=220),
        TB(text="사람의 신경계 = 중추신경계 + 말초신경계", x0=70, y0=250, x1=520, y1=280),
        TB(text="2. 중추 신경계 : 뇌와 척수, 연합 뉴런으로 구성", x0=70, y0=310, x1=540, y1=340),
        TB(text="① 뇌: 대뇌, 소뇌, 사이뇌, 뇌줄기 등으로 구성", x0=70, y0=370, x1=540, y1=400),
        TB(text="② 척수: 척수 반사의 중추", x0=70, y0=430, x1=360, y1=460),
    ]

    assert is_non_question_page(blocks) is True


def test_hormone_homeostasis_concept_page_is_non_question():
    """호르몬/항상성 개념 본문의 과정 번호를 문항 번호로 보지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="생명과학 Ⅰ 9 체온유지가 중요한 이유", x0=40, y0=70, x1=420, y1=100),
        TB(text="1. 물질대사에 관여하는 효소는 단백질로 구성되어 있다.", x0=60, y0=130, x1=540, y1=160),
        TB(text="2. 우리 몸의 효소는 최적 온도가 36.5℃이다.", x0=60, y0=190, x1=500, y1=220),
        TB(text="② 길항 작용: 같은 기관에 대해 서로 반대로 작용한다.", x0=60, y0=260, x1=540, y1=290),
        TB(text="3. 항상성 조절 방법", x0=60, y0=330, x1=300, y1=360),
        TB(text="① 사이뇌 시상 하부 중추, 혈중 포도당 농도 0.1% 유지", x0=70, y0=390, x1=560, y1=420),
        TB(text="② 인슐린과 글루카곤의 길항 작용", x0=70, y0=450, x1=460, y1=480),
        TB(text="4. 체온 조절: 열 발생과 열 발산 조절", x0=60, y0=520, x1=460, y1=550),
    ]

    assert is_non_question_page(blocks) is True


def test_step_question_page_with_unit_context_stays_question():
    """Step 문항 페이지는 단원명/페이지번호가 있어도 비문항으로 차단하지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="17", x0=20, y0=20, x1=35, y1=40),
        TB(text="Step 1. 개념완성", x0=60, y0=70, x1=220, y1=95),
        TB(text="1.", x0=60, y0=150, x1=80, y1=170),
        TB(text="그림은 빅뱅 우주론을 모형을 나타낸 것이다.", x0=90, y0=150, x1=420, y1=180),
        TB(text="옳은 것은 ○표, 옳지 않은 것은 ×표 하시오.", x0=90, y0=190, x1=450, y1=220),
        TB(text="2.", x0=60, y0=360, x1=80, y1=380),
        TB(text="빅뱅 우주론의 증거에 대한 설명으로 옳은 것은 쓰시오.", x0=90, y0=360, x1=470, y1=390),
    ]

    assert is_non_question_page(blocks) is False


def test_fill_blank_question_page_is_not_non_question():
    """빈칸 채우기/OX 선택 문항은 하위 번호가 많아도 비문항이 아니다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, is_non_question_page

    blocks = [
        TB(text="7.", x0=60, y0=100, x1=80, y1=120),
        TB(text="그림 (가)와 (나)는 서로 다른 두 우주론을 나타낸 것이다.", x0=90, y0=100, x1=520, y1=130),
        TB(text="이에 대하여 다음 빈칸을 채우거나 옳고 그름을 선택하시오.", x0=90, y0=140, x1=540, y1=170),
        TB(text="1. (가)에 해당하는 우주론은 (       )우주론 이다.", x0=100, y0=210, x1=520, y1=240),
        TB(text="2. (나)에 해당하는 우주론은 (       )우주론 이다.", x0=100, y0=260, x1=520, y1=290),
        TB(text="3. 우주배경복사는 ( (가) / (나) )를 지지하는 증거이다.", x0=100, y0=310, x1=540, y1=340),
    ]

    assert is_non_question_page(blocks) is False


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


def test_forced_quad_layout_keeps_column_major_numbering():
    """QUADRANT 분류 경로에서 좌상→좌하→우상→우하 번호를 버리지 않는다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 1000.0, 1400.0

    def quad_blocks(qid: int, base_x: float, base_y: float):
        return [
            TB(text=f"{qid}) 다음 중 옳은 것은?", x0=base_x + 50, y0=base_y + 50, x1=base_x + 400, y1=base_y + 70),
            TB(text="자료이다.", x0=base_x + 50, y0=base_y + 100, x1=base_x + 400, y1=base_y + 130),
            TB(text="① ㄱ ② ㄴ ③ ㄷ ④ ㄱ, ㄴ ⑤ ㄱ, ㄴ, ㄷ", x0=base_x + 50, y0=base_y + 170, x1=base_x + 430, y1=base_y + 200),
        ]

    blocks = (
        quad_blocks(1, 0, 0)        # TL
        + quad_blocks(3, 500, 0)    # TR
        + quad_blocks(2, 0, 700)    # BL
        + quad_blocks(4, 500, 700)  # BR
    )
    forced_quad = PaperTypeResult(
        paper_type=PaperType.QUADRANT,
        confidence=0.9,
        is_dual_column=False,
        is_quadrant=True,
        is_handwriting_present=False,
        has_embedded_text=True,
    )

    regions = split_questions(blocks, pw, ph, page_index=0, paper_type=forced_quad)

    assert sorted(r.number for r in regions) == [1, 2, 3, 4]
    by_num = {r.number: r.bbox for r in regions}
    assert by_num[2][0] < 500 and by_num[2][1] >= 698
    assert by_num[3][0] >= 498 and by_num[3][3] <= 710


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


def test_dual_column_anchor_distribution_fallback():
    """우측 column block 수가 적어도 anchor 분포로 dual-column 인식.

    운영 doc#177 결함 시나리오 재현: Vision OCR이 우측 column의 본문/보기를
    잘 못 잡아서 우측 anchor만 잡힌 경우 기존 heuristic (right_count > 20%)로는
    미인식되지만 anchor 분포로 인식해야 함.
    """
    from academy.domain.tools.question_splitter import TextBlock as TB, _detect_column_layout

    pw = 8400.0
    blocks = [
        # 좌측 column: anchor 1, 2, 3 + 본문 블록 다수
        TB(text="1. 그림", x0=100, y0=200, x1=4000, y1=300),
        TB(text="2. 다음", x0=100, y0=2000, x1=4000, y1=2100),
        TB(text="3. 그림", x0=100, y0=4000, x1=4000, y1=4100),
        *[TB(text="본문", x0=200, y0=float(y), x1=3900, y1=float(y + 50))
          for y in range(400, 9000, 600)],
        # 우측 column: anchor 4, 5, 6 + 본문 적음 (3개)
        TB(text="4. 그림", x0=4500, y0=200, x1=8200, y1=300),
        TB(text="5. 그림", x0=4500, y0=3000, x1=8200, y1=3100),
        TB(text="6. 다음", x0=4500, y0=6000, x1=8200, y1=6100),
    ]
    assert _detect_column_layout(blocks, pw) is True


def test_dual_column_single_right_anchor_distribution_fallback():
    """좌측 2문항 + 우측 1문항인 2단 페이지도 single-column으로 합치지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, _detect_column_layout

    pw = 595.0
    blocks = [
        TB(text="98 98. 내신기출 그림은", x0=36, y0=75, x1=280, y1=98),
        TB(text="99 99. EBS. 수특 표는", x0=36, y0=420, x1=280, y1=443),
        TB(text="100 100. EBS. 수특 다음은", x0=311, y0=75, x1=559, y1=110),
        TB(text="왼쪽 본문", x0=50, y0=120, x1=260, y1=150),
        TB(text="오른쪽 본문", x0=320, y0=130, x1=540, y1=160),
    ]

    assert _detect_column_layout(blocks, pw) is True


def test_dual_column_uses_full_column_width():
    """dual-column에서 region_blocks의 x range가 좁아도 column 전체 width를 사용.

    운영 doc#294 q19 bbox=(2465, 1019, 204, 78) — width=204 (페이지 8400의 2.4%).
    region_blocks가 anchor 한 개만 포함하면 x range 좁아져 strip width.
    fix 적용 후 column 전체 width 사용.
    """
    from academy.domain.tools.question_splitter import (
        TextBlock as TB,
        split_questions,
    )

    blocks = [
        TB(text="1. 그림", x0=4500, y0=1000, x1=4700, y1=1100),  # 우측 column anchor
        TB(text="2. 다음", x0=4500, y0=2000, x1=4700, y1=2100),  # 우측 column 다음 anchor
        # 좌측에 다른 anchor 추가해서 dual-column 인식
        TB(text="3. 그림", x0=100, y0=1000, x1=300, y1=1100),
        TB(text="4. 다음", x0=100, y0=2000, x1=300, y1=2100),
    ]
    pw, ph = 8400.0, 11200.0
    regions = split_questions(blocks, pw, ph, page_index=0)
    by_num = {r.number: r.bbox for r in regions}

    # q1 (우측 column) — width가 column 전체 (절반 page_width 이상)
    if 1 in by_num:
        x0, y0, x1, y1 = by_num[1]
        width = x1 - x0
        assert width >= pw * 0.4, (
            f"q1 width={width} too narrow (column full width 사용 안 됨)"
        )


def test_dual_classified_page_with_left_anchors_and_wide_text_uses_single_width():
    """dual로 분류되어도 anchor가 좌측뿐이고 본문이 전폭이면 single-wide로 자른다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 1000.0, 1400.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.85,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(text="5. 다음은 자료이다.", x0=100, y0=200, x1=420, y1=230),
        TB(text="제임스웹 우주 망원경에는 거대한 주경이 달려 있다.", x0=110, y0=270, x1=930, y1=310),
        TB(text="이에 대한 옳은 설명만을 보기에서 고른 것은?", x0=110, y0=520, x1=760, y1=550),
        TB(text="6. 다음은 길이를 측정하는 다양한 사례이다.", x0=100, y0=760, x1=650, y1=790),
        TB(text="지구에서 레이저로 빛을 쏘아 거리를 측정한다.", x0=110, y0=830, x1=940, y1=870),
    ]

    regions = split_questions(blocks, pw, ph, page_index=0, paper_type=pt)

    assert [r.number for r in regions] == [5, 6]
    assert regions[0].bbox[2] > pw * 0.80
    assert regions[1].bbox[2] > pw * 0.80


def test_dual_classified_full_width_anchor_is_not_right_column_anchor():
    """전폭 문항 첫 줄이 우측까지 길어도 우측 컬럼 anchor로 오인하지 않는다.

    T2 doc302 p28: Q10 첫 줄이 page 오른쪽까지 길어 center가 mid_x를 넘는다.
    이를 우측 column으로 보면 Q9가 Q10 앞에서 끝나지 않고 footer 근처까지
    늘어나 GT와 매칭되지 않는다.
    """
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 612.0, 864.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.85,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    blocks = [
        TB(text="9.\n그림은 도로에서 발생한 소리를 스마트폰으로 측정한 결과이다.", x0=70.9, y0=149.6, x1=414.5, y1=167.7),
        TB(text="이에 대한 옳은 설명만을 <보기>에서 있는 대로 고른 것은?", x0=70.9, y0=261.8, x1=265.7, y1=270.8),
        TB(text="① ㄱ / ② ㄴ / ③ ㄱ, ㄷ / ④ ㄴ, ㄷ / ⑤ ㄱ, ㄴ, ㄷ", x0=76.1, y0=357.9, x1=477.4, y1=365.9),
        TB(text="10.\n그림 (가)는 자연에서 발생한 신호를, (나)는 전기 신호로 변환한 것이다.", x0=70.9, y0=450.4, x1=569.7, y1=465.2),
        TB(text="로그 신호 중 하나이다.", x0=101.5, y0=471.9, x1=204.1, y1=489.7),
        TB(text="이에 대한 옳은 설명만을 <보기>에서 있는 대로 고른 것은?", x0=70.9, y0=594.2, x1=265.7, y1=603.2),
        TB(text="① ㄱ / ② ㄷ / ③ ㄱ, ㄴ / ④ ㄴ, ㄷ / ⑤ ㄱ, ㄴ, ㄷ", x0=76.1, y0=690.3, x1=477.4, y1=698.3),
        TB(text="28", x0=37.0, y0=819.8, x1=48.9, y1=830.8),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=27,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [9, 10]
    q9, q10 = regions
    assert q9.bbox[3] < q10.bbox[1]
    assert q9.bbox[2] > pw * 0.75
    assert q10.bbox[2] > pw * 0.75


def test_pixel_only_dual_text_pdf_uses_single_y_order():
    """픽셀만 dual인 embedded-text PDF는 y 분할을 단일열 순서로 수행한다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 612.0, 864.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.72,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={
            "has_embedded_text": True,
            "is_dual_text": False,
            "is_dual_pixel": True,
        },
    )
    blocks = [
        TB(text="[2021년 고1 11월 학평 통합과학 7번]", x0=79.3, y0=135.0, x1=300.0, y1=145.0),
        TB(text="25.\n표 (가)는 사람을 구성하는 물질 A, B의 특성이다.", x0=79.3, y0=153.4, x1=541.4, y1=168.2),
        TB(text="이에 대한 설명으로 옳은 것은?", x0=79.3, y0=300.0, x1=450.0, y1=315.0),
        TB(text="26.\n다음은 생명 현상과 관련된 반응의 화학 반응식이다.", x0=79.3, y0=475.8, x1=293.9, y1=490.6),
        TB(text="이에 대한 설명으로 옳은 것은?", x0=79.3, y0=700.0, x1=450.0, y1=715.0),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=86,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [25, 26]
    assert regions[0].bbox[3] <= regions[1].bbox[1]
    assert regions[0].bbox[2] > pw * 0.75


def test_pixel_only_dual_with_bilateral_marginal_anchors_restores_dual_strategy():
    """픽셀만 dual이어도 좌우 큰 번호가 있으면 실제 2단 워크북으로 자른다."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.72,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={
            "has_embedded_text": True,
            "is_dual_text": False,
            "is_dual_pixel": True,
        },
    )
    blocks = [
        TB(text="37\n37.", x0=36.2, y0=74.9, x1=163.7, y1=85.9),
        TB(text="왼쪽 상단 산골짜기 자료", x0=40.0, y0=110.0, x1=270.0, y1=330.0),
        TB(text="38\n38.", x0=36.2, y0=452.6, x1=163.7, y1=463.7),
        TB(text="왼쪽 하단 식물 군집 자료", x0=40.0, y0=500.0, x1=270.0, y1=760.0),
        TB(text="39\n39.", x0=311.0, y0=74.9, x1=438.5, y1=85.9),
        TB(text="오른쪽 상단 표 자료", x0=315.0, y0=115.0, x1=560.0, y1=390.0),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=9,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [37, 38, 39]
    by_num = {r.number: r.bbox for r in regions}
    assert by_num[37][3] < by_num[38][1]
    assert by_num[39][0] >= pw * 0.45


def test_pixel_only_dual_color_workbook_keeps_body_anchors_and_subitems():
    """T2 26-1m 컬러 workbook p15: 2단 문항 5~8, 괄호 소문항은 독립 문항 아님."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 612.0, 864.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.65,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={
            "has_embedded_text": True,
            "is_dual_text": False,
            "is_dual_pixel": True,
        },
    )
    blocks = [
        TB(text="5.\n다음은 측정과 관련된 설명이다. ( ) 안에 들어갈 알맞은 말", x0=42.5, y0=130.5, x1=280.6, y1=145.2),
        TB(text="을 쓰시오. 5)", x0=64.0, y0=153.4, x1=107.4, y1=162.4),
        TB(text="( ㉠ )이란 어떤 양을 측정하는 기준으로 쓰기 위하여 단위를 정의하고 이", x0=48.1, y0=176.3, x1=274.9, y1=184.1),
        TB(text="를 재현하는 측정 기기, 측정 방법 체계를 정한 것을 말한다.", x0=48.1, y0=189.1, x1=228.1, y1=196.9),
        TB(text="6.\n다음은 자연과 관련된 설명이다. ( ) 안에 들어갈 알맞은 말", x0=42.5, y0=428.0, x1=280.6, y1=442.7),
        TB(text="을 쓰시오. 6)", x0=64.0, y0=450.7, x1=107.4, y1=459.8),
        TB(text="하루 동안 기온은 계속 변하고, 바닷물의 높이도 주기적으로 변한다.", x0=48.1, y0=470.2, x1=277.1, y1=478.0),
        TB(text="7.\n다양한 형태의 습도, 자기, 전기 신호를 전기 신호로 변환해야 하", x0=303.2, y0=130.5, x1=541.3, y1=145.2),
        TB(text="는데, 이러한 역할을 하는 소자를 무엇이라고 하는가? 7)", x0=324.7, y0=153.4, x1=511.3, y1=162.4),
        TB(text="8.\n아래의 빈칸에 알맞은 말을 써 넣으시오. 8)", x0=303.2, y0=428.9, x1=468.9, y1=443.7),
        TB(text="(1) 비접촉형 체온계에는 적외선을 감지하는 ( )센서가 있다.", x0=303.2, y0=465.3, x1=487.3, y1=473.3),
        TB(text="(2) 자동차는 앞뒤 범퍼에 ( )를 감지하는 센서가 있어서, 반사되어 오는 신", x0=303.2, y0=492.2, x1=541.3, y1=500.2),
        TB(text="호를 감지하여 장애물까지의 거리를 측정한다.", x0=316.8, y0=506.2, x1=443.9, y1=514.2),
        TB(text="(3) 스마트폰 속에는 기기가 ( )지는 방향을 감지하는 센서가 있어서 스마트", x0=303.2, y0=533.1, x1=541.3, y1=541.1),
        TB(text="폰을 기울이며 게임 속 자동차의 방향을 바꿀 수 있다.", x0=316.8, y0=547.2, x1=467.5, y1=555.3),
        TB(text="(4) 가스 누설 경보기에는 미세한 가스를 감지하는 ( ) 센서가 있다.", x0=303.2, y0=574.1, x1=508.9, y1=582.2),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=14,
        paper_type=pt,
        prefer_marginal=False,
    )

    assert [r.number for r in regions] == [5, 6, 7, 8]
    by_num = {r.number: r.bbox for r in regions}
    assert by_num[5][2] < pw * 0.50
    assert by_num[6][2] < pw * 0.50
    assert by_num[7][0] > pw * 0.45
    assert by_num[8][0] > pw * 0.45
    assert by_num[8][3] > 580.0


def test_dense_short_fill_in_rows_do_not_expand_to_page_end():
    """짧은 빈칸형 줄문항이 연속되면 5% strip 방어로 전고 확장하지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    blocks = [
        TB(
            text=f"{num}. (        )은 단위 시간 동안의 빈칸을 의미한다.",
            x0=28.0,
            y0=80.0 + (num - 1) * 42.0,
            x1=420.0,
            y1=95.0 + (num - 1) * 42.0,
        )
        for num in range(1, 10)
    ]

    regions = split_questions(blocks, pw, ph, page_index=8, prefer_marginal=True)

    assert [r.number for r in regions] == list(range(1, 10))
    assert all((r.bbox[3] - r.bbox[1]) < ph * 0.12 for r in regions[:8])
    assert regions[0].bbox[3] <= regions[1].bbox[1]


def test_fill_in_worksheet_prompt_splits_dense_rows():
    """빈칸 워크시트 한 장은 내부 행번호 단위로 자른다."""
    from academy.domain.tools.question_splitter import (
        TextBlock as TB,
        is_non_question_page,
        split_questions,
    )

    pw, ph = 595.0, 841.0
    blocks = [
        TB(
            text="1. 다음은 지각과 생명체 구성 물질에 대한 내용이다. 빈칸에 알맞은 말을 써 넣으시오.",
            x0=28.0,
            y0=80.0,
            x1=500.0,
            y1=95.0,
        ),
        *[
            TB(
                text=f"{num}. 생명체 구성 물질: (     ) > (     ) > 기타",
                x0=28.0,
                y0=105.0 + num * 24.0,
                x1=520.0,
                y1=115.0 + num * 24.0,
            )
            for num in range(1, 13)
        ],
    ]

    assert is_non_question_page(blocks) is False
    regions = split_questions(blocks, pw, ph, page_index=8, prefer_marginal=True)

    assert [r.number for r in regions] == list(range(1, 13))
    assert all((r.bbox[3] - r.bbox[1]) < ph * 0.12 for r in regions[:11])
    assert regions[0].bbox[3] <= regions[1].bbox[1]


def test_fill_in_page_with_multiple_top_level_numbers_keeps_each_question():
    """33/34/35처럼 상위 문항이 여러 개면 빈칸 지시문이 있어도 병합하지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    blocks = [
        TB(
            text="33.\n시스템과 관련한 내용이다. 다음 빈칸에 알맞은 말을 써 넣으시오.",
            x0=28.0,
            y0=75.0,
            x1=335.0,
            y1=90.0,
        ),
        TB(text="1. 자연은 (      ) 시스템으로 구성되어 있다.", x0=28.0, y0=105.0, x1=450.0, y1=114.0),
        TB(
            text="34.\n다음은 중력과 관련된 여러 가지 자연현상이다.",
            x0=28.0,
            y0=282.0,
            x1=510.0,
            y1=297.0,
        ),
        TB(text="1. 수증기를 포함한 공기의 대류", x0=28.0, y0=312.0, x1=250.0, y1=321.0),
        TB(
            text="35.\n그림과 같이 높이가 같은 두 탑 위에서 물체 A를 던졌다.",
            x0=28.0,
            y0=494.0,
            x1=570.0,
            y1=508.0,
        ),
        TB(text="1. 두 탑 사이의 거리는 (      )m이다.", x0=28.0, y0=656.0, x1=180.0, y1=665.0),
    ]

    regions = split_questions(blocks, pw, ph, page_index=21, prefer_marginal=True)

    assert [r.number for r in regions] == [33, 34, 35]


def test_short_final_written_prompt_tightens_to_content():
    """마지막 서술형 한 줄 문항은 페이지 끝까지 빈 공간을 먹지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    blocks = [
        TB(text="5. 그림은 인체를 구성하는 일부 성분의 비율을 나타낸 것이다.", x0=28.0, y0=70.0, x1=520.0, y1=84.0),
        TB(text="이에 대한 설명으로 옳은 것을 고른 것은?", x0=28.0, y0=250.0, x1=360.0, y1=265.0),
        TB(text="6. 탄소가 생명체에서 중요한 역할을 하는 이유에 대해 서술하시오.", x0=28.0, y0=430.0, x1=315.0, y1=444.0),
    ]

    regions = split_questions(blocks, pw, ph, page_index=16, prefer_marginal=True)

    assert [r.number for r in regions] == [5, 6]
    assert regions[1].bbox[3] < ph * 0.65


def test_short_final_visual_prompt_keeps_visual_body():
    """마지막 짧은 서술형이어도 그림/그래프를 언급하면 아래 시각 자료를 포함한다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    blocks = [
        TB(text="13. 그림은 빅뱅 후 어느 시기의 변화이다.", x0=28.0, y0=70.0, x1=310.0, y1=84.0),
        TB(text="이에 대한 설명으로 옳은 것은?", x0=28.0, y0=220.0, x1=330.0, y1=235.0),
        TB(
            text="14. 그림에서 A와 B는 은하까지의 거리와 후퇴 속도이다. 서술하시오.",
            x0=28.0,
            y0=422.0,
            x1=567.0,
            y1=436.0,
        ),
        TB(text="20 15 10 5 거리(Mpc)", x0=180.0, y0=500.0, x1=360.0, y1=515.0),
    ]

    regions = split_questions(blocks, pw, ph, page_index=22, prefer_marginal=True)

    assert [r.number for r in regions] == [13, 14]
    assert regions[1].bbox[3] > ph * 0.60


def test_short_final_visual_prompt_without_text_body_keeps_minimum_visual_space():
    """그림을 언급한 마지막 단문은 그림이 텍스트 block으로 안 잡혀도 strip으로 남기지 않는다."""
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    blocks = [
        TB(text="11. 판의 경계에 대한 설명으로 옳은 것은?", x0=28.0, y0=70.0, x1=360.0, y1=84.0),
        TB(
            text="12. 그림은 화산 분출 전후 기온 편차를 나타낸 것이다. 원인을 쓰시오.",
            x0=28.0,
            y0=430.0,
            x1=470.0,
            y1=444.0,
        ),
    ]

    regions = split_questions(blocks, pw, ph, page_index=114, prefer_marginal=True)

    assert [r.number for r in regions] == [11, 12]
    assert regions[1].bbox[3] - regions[1].bbox[1] >= ph * 0.20
    assert regions[1].bbox[3] < ph * 0.80


def test_split_questions_cross_column_anchor_fallback():
    """dual-column 미인식 케이스에서 next anchor가 위에 있어도 strip(10px)이 안 나옴.

    운영 doc#177 q1 결함 (bbox=[0, 377, 8400, 63] = strip 10px) 재현.
    좌측 anchor "1." 다음에 우측 anchor "3."이 정렬되어 next_block.y0 < start_block.y0.
    fix 적용 후 y1=page_height fallback으로 정상 height.
    """
    from academy.domain.tools.question_splitter import (
        TextBlock as TB,
        split_questions,
    )

    # dual-column이지만 우측 block이 매우 적어서 _detect_column_layout 미인식 가능
    blocks = [
        TB(text="1. 그림", x0=100, y0=400, x1=4000, y1=500),
        TB(text="2. 다음", x0=100, y0=2000, x1=4000, y1=2100),
        TB(text="3. 그림", x0=4500, y0=400, x1=8000, y1=500),
        TB(text="4. 다음", x0=4500, y0=2000, x1=8000, y1=2100),
    ]
    pw, ph = 8400.0, 11200.0
    regions = split_questions(blocks, pw, ph, page_index=0)
    assert len(regions) == 4
    by_num = {r.number: r.bbox for r in regions}

    # 모든 region의 height >= 100 (strip 결함 차단)
    for num, (x0, y0, x1, y1) in by_num.items():
        height = y1 - y0
        assert height >= 100, (
            f"q{num} bbox=({x0},{y0},{x1},{y1}) height={height} too small (strip)"
        )


def test_cross_column_shared_instruction_collapses_to_one_physical_group():
    """[1~3] left shared experiment + right answer prompts is one crop group."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.99,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={"has_embedded_text": True, "is_dual_text": True, "is_dual_pixel": False},
    )
    blocks = [
        TB(text="[1~3] 다음은 소화 효소 실험이다. 물음에 답하시오.", x0=28, y0=76, x1=292, y1=99),
        TB(text="1. 물 10mL를 준비한다.", x0=36, y0=133, x1=295, y1=142),
        TB(text="2. 소화제를 섞는다.", x0=36, y0=162, x1=295, y1=171),
        TB(text="3. 녹말 용액을 넣는다.", x0=36, y0=191, x1=295, y1=200),
        TB(text="4. 셀로판 튜브를 장치한다.", x0=36, y0=234, x1=295, y1=243),
        TB(text="5. 표와 같이 관찰한다.", x0=36, y0=346, x1=295, y1=355),
        TB(text="1\n1.", x0=311, y0=75, x1=439, y1=86),
        TB(text="(가)~(다) 중 기호를 쓰고 까닭을 서술하시오.", x0=311, y0=89, x1=559, y1=113),
        TB(text="2\n2.", x0=311, y0=299, x1=439, y1=310),
        TB(text="색깔이 변한 까닭을 쓰시오.", x0=311, y0=314, x1=559, y1=338),
        TB(text="3\n3.", x0=311, y0=465, x1=439, y1=476),
        TB(text="소화효소의 역할을 설명하시오.", x0=311, y0=480, x1=559, y1=504),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=0,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [1]
    assert "shared_group" in regions[0].semantic_flags
    assert regions[0].bbox[0] < pw * 0.08
    assert regions[0].bbox[2] > pw * 0.90
    assert regions[0].bbox[3] > ph * 0.55


def test_marginal_axis_tick_labels_are_not_question_anchors():
    """Graph tick labels like 40/80 near the gutter are not workbook anchors."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 612.0, 858.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.99,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={"has_embedded_text": True, "is_dual_text": True, "is_dual_pixel": False},
    )
    blocks = [
        TB(text="9.\n그림은 실린더 반응 전후를 나타낸 것이다.", x0=42, y0=153, x1=280, y1=168),
        TB(text="10.\n다음은 A(g)와 B(g)가 반응하여 C(g)를 생성하는 반응이다.", x0=303, y0=155, x1=544, y1=170),
        TB(text="80", x0=362, y0=300, x1=371, y1=309),
        TB(text="40", x0=362, y0=337, x1=372, y1=346),
        TB(text="C(g)의 질량(g)", x0=330, y0=455, x1=500, y1=470),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=40,
        paper_type=pt,
        prefer_marginal=True,
    )

    assert [r.number for r in regions] == [9, 10]


def test_section_step_prefix_is_included_for_first_problem():
    """Commercial workbook Step title above the first item is product context."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 612.0, 864.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.99,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={"has_embedded_text": True, "is_dual_text": True, "is_dual_pixel": False},
    )
    blocks = [
        TB(text="Step 1. 개념완성", x0=111, y0=136, x1=190, y1=146),
        TB(text="3.\n그림 (가), (나)는 공기 중과 진공 중의 낙하 모습이다.", x0=332, y0=164, x1=570, y1=179),
        TB(text="1.\n다음 글에서 설명하는 것은 무엇인지 쓰시오.", x0=71, y0=166, x1=245, y1=181),
        TB(text="자연에 존재하는 여러 가지 힘들이 상호 작용한다.", x0=77, y0=194, x1=303, y1=202),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=151,
        paper_type=pt,
        prefer_marginal=True,
    )

    first = next(r for r in regions if r.number == 1)
    assert first.bbox[1] < 140.0


def test_short_written_prompt_tightens_without_running_to_next_question():
    """One-line written workbook prompts keep bounded answer space."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_SINGLE,
        confidence=0.99,
        is_dual_column=False,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={"has_embedded_text": True, "is_dual_text": False, "is_dual_pixel": False},
    )
    blocks = [
        TB(text="35. 빅뱅 우주에 대한 설명으로 옳지 않은 것은?", x0=28, y0=70, x1=236, y1=82),
        TB(text="36. 빅뱅 후 최초로 만들어진 별의 구성 원소는 무엇이며, 이유를 서술하시오.", x0=28, y0=233, x1=421, y1=245),
        TB(text="37. 그림 (가)는 연속 스펙트럼을 나타낸 것이다.", x0=28, y0=382, x1=339, y1=394),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=33,
        paper_type=pt,
        prefer_marginal=True,
    )

    q36 = next(r for r in regions if r.number == 36)
    assert ph * 0.06 < q36.bbox[3] - q36.bbox[1] < ph * 0.10


def test_wide_single_line_written_prompt_uses_tighter_min_height():
    """Full-width one-line written prompts are not padded like answer-space rows."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 595.0, 841.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_SINGLE,
        confidence=0.99,
        is_dual_column=False,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={"has_embedded_text": True, "is_dual_text": False, "is_dual_pixel": False},
    )
    blocks = [
        TB(
            text="49. 우주 전역에서 수소와 헬륨의 스펙트럼이 관측되는 까닭을 서술하시오.",
            x0=28,
            y0=80,
            x1=558,
            y1=92,
        ),
        TB(text="50. 그림은 별의 진화 과정을 나타낸 것이다.", x0=28, y0=430, x1=360, y1=442),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=39,
        paper_type=pt,
        prefer_marginal=True,
    )

    q49 = next(r for r in regions if r.number == 49)
    assert ph * 0.04 < q49.bbox[3] - q49.bbox[1] < ph * 0.06


def test_dual_column_short_written_prompt_preserves_column_width():
    """Short right-column calculation prompts keep the manual crop column width."""
    from academy.domain.tools.paper_type import PaperType, PaperTypeResult
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    pw, ph = 612.0, 864.0
    pt = PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL,
        confidence=0.99,
        is_dual_column=True,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={"has_embedded_text": True, "is_dual_text": True, "is_dual_pixel": False},
    )
    blocks = [
        TB(text="7. 앞 문항", x0=304, y0=120, x1=545, y1=132),
        TB(
            text="8. 다음 기체 분자의 물질량을 구하시오.",
            x0=304,
            y0=460,
            x1=455,
            y1=472,
        ),
        TB(text="(1) 조건 A", x0=314, y0=505, x1=420, y1=517),
        TB(text="(2) 조건 B", x0=314, y0=545, x1=420, y1=557),
    ]

    regions = split_questions(
        blocks,
        pw,
        ph,
        page_index=30,
        paper_type=pt,
        prefer_marginal=True,
    )

    q8 = next(r for r in regions if r.number == 8)
    assert q8.bbox[2] > pw * 0.88
