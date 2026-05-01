"""PaperType 분류기 단위 테스트 — 매치업 splitter dispatcher SSOT.

목적:
- 텍스트 분포 휴리스틱(_detect_column_layout / _detect_quad_layout) 결과 캡처
- 픽셀 기반 dual-col 백업이 텍스트 휴리스틱 실패 시에만 동작
- 외부 신호(handwriting / has_embedded_text) 반영
- NON_QUESTION 차단

운영 결함 회귀:
- T2 doc#177/#291/#294 폰사진 시험지: OCR 블록 분포 부족 → text 휴리스틱은 single,
  픽셀 백업으로 dual 복구. SCAN_DUAL 분류 락.
- 학생 답안지 폰사진(handwriting_score 0.8+): STUDENT_ANSWER_PHOTO 분류로
  자동분리 신뢰도 강등 신호 (Source 게이트 입력).
"""
from __future__ import annotations

import academy.domain.tools.paper_type as pt_mod
from academy.domain.tools.paper_type import (
    PaperType,
    PaperTypeResult,
    classify_paper_type,
)
from academy.domain.tools.question_splitter import TextBlock


def _blocks(*lines):
    return [
        TextBlock(text=t, x0=0.0, y0=float(i * 20), x1=500.0, y1=float(i * 20 + 18))
        for i, t in enumerate(lines)
    ]


# ── 1. 입력 신호 부재 ──

def test_unknown_when_no_signals():
    """text_blocks도 image_path도 없으면 UNKNOWN."""
    result = classify_paper_type()
    assert result.paper_type is PaperType.UNKNOWN
    assert result.confidence == 0.0
    assert result.is_low_confidence_source is True


# ── 2. NON_QUESTION 차단 (텍스트로만 판정 가능) ──

def test_non_question_lorem_cover():
    """Lorem ipsum placeholder 표지 → NON_QUESTION."""
    blocks = _blocks(
        "RUNNER'S HIGH WITH GOD MIN",
        "adipiscing elit, sed diam nonummy nibh",
        "euismod tincidunt ut laoreet dolore magna",
        "aliquam erat volutpat",
    )
    result = classify_paper_type(
        text_blocks=blocks,
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=True,
    )
    assert result.paper_type is PaperType.NON_QUESTION
    assert result.is_non_question is True


def test_non_question_answer_table():
    """정답표 페이지 → NON_QUESTION."""
    blocks = _blocks(
        "1. ④  2. ④  3. ①  4. ③  5. ③  6. ④  7. ④  8. ④  9. ⑤  10. ⑤"
    )
    result = classify_paper_type(
        text_blocks=blocks,
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=True,
    )
    assert result.paper_type is PaperType.NON_QUESTION


# ── 3. STUDENT_ANSWER_PHOTO (handwriting + 스캔본) ──

def test_student_answer_photo_high_handwriting_scan():
    """handwriting_score 매우 높음 + has_embedded_text=False → STUDENT_ANSWER_PHOTO."""
    result = classify_paper_type(
        image_path="/fake/path.png",
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=False,
        handwriting_score=0.85,
    )
    assert result.paper_type is PaperType.STUDENT_ANSWER_PHOTO
    assert result.is_handwriting_present is True
    assert result.is_low_confidence_source is True


def test_handwriting_alone_does_not_flag_pdf():
    """handwriting_score 높아도 has_embedded_text=True (인쇄본 PDF)면 답안지 분류 안 함."""
    blocks = _blocks(
        "1. 다음 중 옳은 것은?",
        "① A ② B ③ C ④ D ⑤ E",
    )
    result = classify_paper_type(
        text_blocks=blocks,
        image_path="/fake/path.png",
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=True,
        handwriting_score=0.9,
    )
    # 인쇄본 PDF는 인쇄 텍스트 자체가 writing_score를 올림 — STUDENT_ANSWER_PHOTO 아님
    assert result.paper_type is not PaperType.STUDENT_ANSWER_PHOTO


def test_handwriting_below_threshold_ignored():
    """handwriting_score 0.6 ~ 0.78은 신호로만 (분류 결정에 영향 없음)."""
    blocks = _blocks(
        "1. 다음 중 옳은 것은?",
        "① A ② B ③ C ④ D ⑤ E",
    )
    result = classify_paper_type(
        text_blocks=blocks,
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=False,
        handwriting_score=0.65,
    )
    assert result.paper_type is not PaperType.STUDENT_ANSWER_PHOTO
    assert result.is_handwriting_present is True


# ── 4. QUADRANT (4분할) ──

def test_quadrant_detected_from_text_blocks():
    """4분면에 텍스트 분포 + 가운데 gutter → QUADRANT."""
    pw, ph = 1000.0, 1400.0

    def quad_blocks(qid: int, base_x: float, base_y: float):
        return [
            TextBlock(text=f"{qid}. 다음", x0=base_x + 50, y0=base_y + 50, x1=base_x + 400, y1=base_y + 70),
            TextBlock(text="① A ② B", x0=base_x + 50, y0=base_y + 90, x1=base_x + 400, y1=base_y + 110),
            TextBlock(text="<보기>", x0=base_x + 50, y0=base_y + 130, x1=base_x + 400, y1=base_y + 150),
        ]

    blocks = (
        quad_blocks(1, 0, 0)
        + quad_blocks(2, 500, 0)
        + quad_blocks(3, 0, 700)
        + quad_blocks(4, 500, 700)
    )
    result = classify_paper_type(
        text_blocks=blocks,
        page_width=pw,
        page_height=ph,
        has_embedded_text=True,
    )
    assert result.paper_type is PaperType.QUADRANT
    assert result.is_quadrant is True
    assert result.is_dual_column is False


# ── 5. DUAL — 텍스트 분포로 인식 ──

def _dual_real_question_blocks(pw: float):
    """본문 시험지 dual-col blocks — is_non_question_page 우회용 보기/지시문 포함."""
    return [
        # 좌측 column
        TextBlock(text="1. 다음 중 옳은 것을 고르시오", x0=50, y0=100, x1=450, y1=120),
        TextBlock(text="① A ② B ③ C ④ D ⑤ E", x0=50, y0=140, x1=450, y1=160),
        TextBlock(text="2. 그림은 어떤 분자의 구조를 나타낸 것이다", x0=50, y0=300, x1=450, y1=320),
        TextBlock(text="① 가 ② 나 ③ 다 ④ 라 ⑤ 마", x0=50, y0=340, x1=450, y1=360),
        # 우측 column
        TextBlock(text="3. 다음 중 옳은 것은?", x0=550, y0=100, x1=950, y1=120),
        TextBlock(text="① 옳다 ② 그르다 ③ 모르겠다 ④ 답 ⑤ 정답", x0=550, y0=140, x1=950, y1=160),
        TextBlock(text="4. 보기에서 옳은 것을 모두 고르시오", x0=550, y0=300, x1=950, y1=320),
        TextBlock(text="① ㄱ ② ㄴ ③ ㄷ ④ ㄱ,ㄴ ⑤ ㄴ,ㄷ", x0=550, y0=340, x1=950, y1=360),
    ]


def test_clean_pdf_dual_via_text():
    """텍스트 PDF + 좌/우 분포 anchor → CLEAN_PDF_DUAL."""
    pw = 1000.0
    result = classify_paper_type(
        text_blocks=_dual_real_question_blocks(pw),
        page_width=pw,
        page_height=1400.0,
        has_embedded_text=True,
    )
    assert result.paper_type is PaperType.CLEAN_PDF_DUAL
    assert result.is_dual_column is True
    assert result.confidence >= 0.85


def test_scan_dual_via_text():
    """스캔본 + 텍스트 분포 dual → SCAN_DUAL."""
    pw = 1000.0
    result = classify_paper_type(
        text_blocks=_dual_real_question_blocks(pw),
        page_width=pw,
        page_height=1400.0,
        has_embedded_text=False,
    )
    assert result.paper_type is PaperType.SCAN_DUAL
    assert result.is_dual_column is True


# ── 6. DUAL — 픽셀 백업 (텍스트 분포 부족) ──

def test_scan_dual_via_pixel_backup(monkeypatch):
    """텍스트 분포 single이지만 픽셀 백업이 dual → SCAN_DUAL.

    T2 폰사진 시험지 결함 회귀: OCR 블록 분포가 부족해 _detect_column_layout이 single로
    판정. 픽셀 기반 dual-col 감지(OpenCV projection)가 백업 신호로 dual 복구.
    """
    # 텍스트는 본문이지만 분포는 single (좌측만 점유)
    blocks = [
        TextBlock(text="1. 다음 중 옳은 것을 고르시오", x0=50, y0=100, x1=400, y1=120),
        TextBlock(text="① A ② B ③ C ④ D ⑤ E", x0=50, y0=140, x1=400, y1=160),
        TextBlock(text="2. 그림은 어떤 분자의 구조를 나타낸 것이다", x0=50, y0=300, x1=400, y1=320),
        TextBlock(text="① 가 ② 나 ③ 다 ④ 라 ⑤ 마", x0=50, y0=340, x1=400, y1=360),
    ]
    monkeypatch.setattr(
        pt_mod, "_detect_dual_column_from_pixels", lambda p: True
    )
    result = classify_paper_type(
        text_blocks=blocks,
        image_path="/fake/path.png",
        page_width=1000.0,
        page_height=1400.0,
        has_embedded_text=False,
    )
    assert result.paper_type is PaperType.SCAN_DUAL
    assert result.is_dual_column is True
    # 픽셀 백업은 confidence 낮음
    assert result.confidence < 0.85


def test_pixel_backup_skipped_when_text_already_dual(monkeypatch):
    """텍스트 휴리스틱이 이미 dual을 잡으면 픽셀 백업은 호출되지 않음."""
    pw = 1000.0
    pixel_called = {"count": 0}

    def fake_pixel(_path: str) -> bool:
        pixel_called["count"] += 1
        return False

    monkeypatch.setattr(pt_mod, "_detect_dual_column_from_pixels", fake_pixel)
    result = classify_paper_type(
        text_blocks=_dual_real_question_blocks(pw),
        image_path="/fake/path.png",
        page_width=pw,
        page_height=1400.0,
        has_embedded_text=False,
    )
    assert result.is_dual_column is True
    assert pixel_called["count"] == 0  # 텍스트로 잡혔으면 픽셀 백업 호출 안 함


# ── 7. SINGLE default ──

def test_clean_pdf_single_default():
    """단일 컬럼 텍스트 PDF → CLEAN_PDF_SINGLE."""
    blocks = _blocks(
        "1. 다음 중 옳은 것은?",
        "① A ② B ③ C ④ D ⑤ E",
        "2. 그림은",
        "① a ② b ③ c ④ d ⑤ e",
    )
    result = classify_paper_type(
        text_blocks=blocks,
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=True,
    )
    assert result.paper_type is PaperType.CLEAN_PDF_SINGLE
    assert result.is_dual_column is False
    assert result.is_quadrant is False


def test_scan_single_default():
    """스캔본 단일 컬럼 → SCAN_SINGLE."""
    blocks = _blocks(
        "1. 다음 중 옳은 것은?",
        "① A ② B ③ C ④ D ⑤ E",
    )
    result = classify_paper_type(
        text_blocks=blocks,
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=False,
    )
    assert result.paper_type is PaperType.SCAN_SINGLE


# ── 8. PaperTypeResult flags ──

def test_low_confidence_flag_for_unknown_and_photo():
    """UNKNOWN과 STUDENT_ANSWER_PHOTO는 is_low_confidence_source=True."""
    unknown = classify_paper_type()
    assert unknown.is_low_confidence_source is True

    photo = classify_paper_type(
        image_path="/fake.png",
        has_embedded_text=False,
        handwriting_score=0.85,
    )
    assert photo.is_low_confidence_source is True


def test_clean_pdf_not_low_confidence():
    """CLEAN_PDF_* 는 신뢰도 높음 — is_low_confidence_source=False."""
    blocks = _blocks("1. 다음 중 옳은 것은?", "① A ② B")
    result = classify_paper_type(
        text_blocks=blocks,
        page_width=500.0,
        page_height=700.0,
        has_embedded_text=True,
    )
    assert result.paper_type is PaperType.CLEAN_PDF_SINGLE
    assert result.is_low_confidence_source is False


# ── 9. PaperTypeResult가 split_questions에 그대로 통과되는지 ──

def test_paper_type_overrides_quad_heuristic():
    """paper_type=QUADRANT이면 split_questions가 quad 분기 사용 (휴리스틱 우회).

    텍스트 분포는 quad가 아니지만 paper_type이 quad로 명시되면 grid 분할이 적용됨을 확인.
    """
    from academy.domain.tools.question_splitter import split_questions

    pw, ph = 1000.0, 1400.0
    # 4 anchor — 4분면에 1개씩만 배치 (휴리스틱은 미달, paper_type으로 강제)
    blocks = [
        TextBlock(text="1. 다음", x0=100, y0=100, x1=400, y1=120),  # TL
        TextBlock(text="2. 그림", x0=600, y0=100, x1=900, y1=120),  # TR
        TextBlock(text="3. 다음", x0=100, y0=900, x1=400, y1=920),  # BL
        TextBlock(text="4. 그림", x0=600, y0=900, x1=900, y1=920),  # BR
    ]
    forced_quad = PaperTypeResult(
        paper_type=PaperType.QUADRANT,
        confidence=1.0,
        is_dual_column=False,
        is_quadrant=True,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    regions = split_questions(
        text_blocks=blocks,
        page_width=pw,
        page_height=ph,
        page_index=0,
        paper_type=forced_quad,
    )
    nums = sorted(r.number for r in regions)
    assert nums == [1, 2, 3, 4]

    # 각 region이 자기 quadrant 안에 있어야 함
    by_num = {r.number: r.bbox for r in regions}
    mid_x, mid_y = pw / 2, ph / 2
    # 1번 = TL: x1 <= mid_x + margin, y1 <= mid_y + margin
    assert by_num[1][2] <= mid_x + 5
    assert by_num[1][3] <= mid_y + 5
    # 4번 = BR: x0 >= mid_x - margin
    assert by_num[4][0] >= mid_x - 5


def test_paper_type_non_question_returns_empty():
    """paper_type=NON_QUESTION이면 split_questions가 빈 리스트 반환."""
    from academy.domain.tools.question_splitter import split_questions

    blocks = _blocks("1. 다음 중 옳은 것은?", "① A ② B")
    forced_skip = PaperTypeResult(
        paper_type=PaperType.NON_QUESTION,
        confidence=1.0,
        is_dual_column=False,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
    )
    regions = split_questions(
        text_blocks=blocks,
        page_width=500.0,
        page_height=700.0,
        page_index=0,
        paper_type=forced_skip,
    )
    assert regions == []
