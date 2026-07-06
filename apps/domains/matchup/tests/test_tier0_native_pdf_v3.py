"""Stage 5.3 (2026-05-06) — Tier 0 v3 paper_type-aware 단위 테스트.

검증 항목:
- classify_paper_type_prototype: 파일명 / 본문 / anchor density 휴리스틱
- _has_choice_pattern_nearby: 학습자료 strict 동반 검증
- _filter_anchors_by_y_gap: y-gap 너무 가까운 anchor pruning
- detect_problem_anchors_v3: paper_type 별 정책
  - exam: v2 그대로
  - 학습자료: 선택지 동반 + y-gap + max 30
  - answer/cover: anchor 0
- analyze_pdf_v3: paper_type 결합 + tier1_required + integration
"""
from __future__ import annotations

from unittest import TestCase

from academy.adapters.tools.pymupdf_renderer import create_text_pdf_file
from academy.adapters.ai.detection.tier0_native_pdf import (
    PAPER_TYPE_ADVANCED_MATERIAL,
    PAPER_TYPE_ANSWER_EXPLANATION,
    PAPER_TYPE_COVER,
    PAPER_TYPE_EXAM,
    PAPER_TYPE_KILLER_TEST,
    PAPER_TYPE_MOCK_EXAM,
    PAPER_TYPE_REVIEW_HOMEWORK,
    PAPER_TYPE_UNKNOWN,
    PAPER_TYPE_WORKBOOK_MAIN,
    NumberAnchor,
    PageBlocks,
    _filter_anchors_by_y_gap,
    _has_choice_pattern_nearby,
    classify_paper_type_prototype,
    detect_columns,
    detect_problem_anchors_v3,
)


def _word(x0, y0, x1, y1, text):
    return {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "text": text}


def _make_page(*, words=None, blocks=None, has_text=True, page_w=595.0, page_h=842.0, page_index=0):
    return PageBlocks(
        page_index=page_index, page_width=page_w, page_height=page_h,
        has_embedded_text=has_text, text_blocks=blocks or [],
        word_blocks=words or [], image_blocks=[],
    )


def _anchor(n, x0, y0, x1=None, y1=None, conf=0.9):
    return NumberAnchor(
        number=n, page_index=0,
        bbox=(float(x0), float(y0), float(x1 if x1 else x0 + 20), float(y1 if y1 else y0 + 15)),
        text=f"{n}.", style="arabic_dot", confidence=conf,
    )


# ── classify_paper_type_prototype ──


class ClassifyPaperTypeTests(TestCase):
    def test_filename_review_homework(self):
        pt, conf, debug = classify_paper_type_prototype(file_name="2-1-1 빅뱅이론 복습과제 중대부고.pdf")
        self.assertEqual(pt, PAPER_TYPE_REVIEW_HOMEWORK)
        self.assertGreaterEqual(conf, 0.8)

    def test_filename_advanced_material(self):
        pt, _, _ = classify_paper_type_prototype(file_name="중대부고 빅뱅 객서심화.pdf")
        self.assertEqual(pt, PAPER_TYPE_ADVANCED_MATERIAL)

    def test_filename_workbook_main(self):
        pt, _, _ = classify_paper_type_prototype(file_name="중대부고 메인자료 2026.pdf")
        self.assertEqual(pt, PAPER_TYPE_WORKBOOK_MAIN)

    def test_filename_workbook_naeji(self):
        pt, _, _ = classify_paper_type_prototype(file_name="26-1m 중대부고 내지.pdf")
        self.assertEqual(pt, PAPER_TYPE_WORKBOOK_MAIN)

    def test_filename_mock_exam(self):
        pt, _, _ = classify_paper_type_prototype(file_name="신민 모의고사 내신용.pdf")
        self.assertEqual(pt, PAPER_TYPE_MOCK_EXAM)

    def test_filename_killer_test(self):
        pt, _, _ = classify_paper_type_prototype(file_name="별의 진화 고난도 킬러 TEST.pdf")
        self.assertEqual(pt, PAPER_TYPE_KILLER_TEST)

    def test_filename_exam(self):
        pt, _, _ = classify_paper_type_prototype(file_name="2024학년도 1학기 중간고사 1학년 통합과학.pdf")
        self.assertEqual(pt, PAPER_TYPE_EXAM)

    def test_anchor_density_high_signals_learning(self):
        """파일명 hint 없는 PDF — anchor density 25+ 면 학습자료 의심."""
        pt, _, debug = classify_paper_type_prototype(
            file_name="unknown.pdf",
            pages_full_text="",
            total_anchors=300, page_count=10,
        )
        self.assertEqual(pt, PAPER_TYPE_ADVANCED_MATERIAL)

    def test_anchor_density_low_signals_exam(self):
        pt, _, _ = classify_paper_type_prototype(
            file_name="unknown.pdf",
            pages_full_text="",
            total_anchors=20, page_count=10,
        )
        self.assertEqual(pt, PAPER_TYPE_EXAM)

    def test_unknown_when_no_signals(self):
        pt, _, _ = classify_paper_type_prototype(file_name="random.pdf")
        self.assertEqual(pt, PAPER_TYPE_UNKNOWN)

    def test_body_keyword_answer(self):
        pt, _, _ = classify_paper_type_prototype(
            file_name="random.pdf", pages_full_text="정답과 해설",
        )
        self.assertEqual(pt, PAPER_TYPE_ANSWER_EXPLANATION)


# ── _has_choice_pattern_nearby ──


class HasChoicePatternNearbyTests(TestCase):
    def test_choices_present(self):
        words = [
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "다음 그림은"),
            _word(50, 130, 60, 145, "①"),
            _word(80, 130, 100, 145, "맞다"),
        ]
        anchor = _anchor(1, 50, 100)
        self.assertTrue(_has_choice_pattern_nearby(anchor, words))

    def test_no_choices(self):
        words = [
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "단순한 본문"),
        ]
        anchor = _anchor(1, 50, 100)
        self.assertFalse(_has_choice_pattern_nearby(anchor, words))

    def test_question_indicator(self):
        words = [
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "다음 중 옳은 것을 고르시오"),
        ]
        anchor = _anchor(1, 50, 100)
        self.assertTrue(_has_choice_pattern_nearby(anchor, words))

    def test_anchor_not_in_words(self):
        """anchor word 가 word_blocks 에 없으면 False."""
        words = [_word(80, 100, 100, 115, "다른 단어")]
        anchor = _anchor(1, 50, 100)  # bbox 매치 X
        self.assertFalse(_has_choice_pattern_nearby(anchor, words))


# ── _filter_anchors_by_y_gap ──


class FilterAnchorsByYGapTests(TestCase):
    def test_drops_close_pair(self):
        """같은 column 안 y-gap < 30pt → 후순위 drop."""
        anchors = [
            _anchor(1, 50, 100),
            _anchor(2, 50, 110),  # 너무 가까움 (gap=10 < 30)
            _anchor(3, 50, 200),  # 충분히 떨어짐
        ]
        out = _filter_anchors_by_y_gap(anchors)
        nums = sorted(a.number for a in out)
        self.assertEqual(nums, [1, 3])

    def test_different_columns_independent(self):
        """다른 column 의 close anchor 는 drop X."""
        anchors = [
            _anchor(1, 50, 100),
            _anchor(2, 300, 110),  # 다른 column (x0 차이 250+)
        ]
        out = _filter_anchors_by_y_gap(anchors)
        self.assertEqual(len(out), 2)

    def test_empty_input(self):
        self.assertEqual(_filter_anchors_by_y_gap([]), [])

    def test_single_anchor(self):
        anchors = [_anchor(1, 50, 100)]
        self.assertEqual(_filter_anchors_by_y_gap(anchors), anchors)


# ── detect_problem_anchors_v3 ──


class DetectProblemAnchorsV3Tests(TestCase):
    def _basic_page_with_choices(self):
        return _make_page(words=[
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "다음 중"),
            _word(50, 130, 60, 145, "①"),
            _word(50, 200, 70, 215, "2."),
            _word(80, 200, 200, 215, "옳은 것은"),
            _word(50, 230, 60, 245, "②"),
        ])

    def test_exam_paper_uses_v2_policy(self):
        """exam 은 v2 그대로 — choice 동반 검증 X."""
        page = _make_page(words=[
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "다음에서"),
        ])
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v3(page, cols, PAPER_TYPE_EXAM)
        # v2 와 동일 — 1개
        self.assertEqual(len(anchors), 1)

    def test_learning_strict_requires_choice_nearby(self):
        """학습자료 — 선택지 패턴 동반된 anchor 만 통과.

        anchor 1. 주변에 ① 있음 (동반).
        anchor 2. 주변에 distractor word 다수 — neighbor range ±20 안 ①/다음/옳은 없음.
        """
        # anchor 2 주변에 distractor 다수 추가해서 neighbor range 안에 choice 없게 만듦
        page = _make_page(words=[
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "다음 중"),
            _word(50, 130, 60, 145, "①"),  # choice 동반
            # 1번 anchor 와 2번 anchor 사이에 distractor 25개
        ] + [
            _word(50 + (i % 5) * 30, 200 + i * 5, 80, 215, f"w{i}")
            for i in range(25)
        ] + [
            _word(50, 700, 70, 715, "2."),
            _word(80, 700, 200, 715, "단순"),  # choice 없음
        ] + [
            _word(50 + (i % 5) * 30, 720 + i * 5, 80, 730, f"x{i}")
            for i in range(25)
        ])
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v3(page, cols, PAPER_TYPE_REVIEW_HOMEWORK)
        # number 1만 통과 (choice 동반)
        nums = [a.number for a in anchors]
        self.assertEqual(nums, [1])

    def test_learning_no_choices_anywhere_returns_empty(self):
        """학습자료 페이지에 선택지 패턴이 페이지 어디에도 없으면 anchor=[]."""
        page = _make_page(words=[
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "예제 풀이 단계"),
            _word(50, 200, 70, 215, "2."),
        ])
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v3(page, cols, PAPER_TYPE_ADVANCED_MATERIAL)
        self.assertEqual(anchors, [])

    def test_answer_explanation_returns_empty(self):
        page = self._basic_page_with_choices()
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v3(page, cols, PAPER_TYPE_ANSWER_EXPLANATION)
        self.assertEqual(anchors, [])

    def test_cover_returns_empty(self):
        page = self._basic_page_with_choices()
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v3(page, cols, PAPER_TYPE_COVER)
        self.assertEqual(anchors, [])

    def test_learning_y_gap_pruning(self):
        """학습자료 — 같은 column 안 y-gap 너무 가까운 anchor 추가 drop."""
        # 모든 anchor 가 choice 동반하지만 y-gap 작음
        words = [
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "다음 중"),
            _word(50, 110, 60, 125, "①"),  # 1번 line 선택지
            # 2. 가 1. 와 y-gap 너무 가까움
            _word(50, 115, 70, 130, "2."),
            _word(80, 115, 200, 130, "옳은 것은"),
            _word(50, 125, 60, 140, "②"),
        ]
        page = _make_page(words=words)
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v3(page, cols, PAPER_TYPE_REVIEW_HOMEWORK)
        # y-gap < 30 → 2번 drop, 1번만
        self.assertLessEqual(len(anchors), 1)

    def test_learning_too_many_anchors_returns_empty(self):
        """학습자료 페이지에 anchor 30+ (y-gap 충분) — 본문 폭증 의심, anchor=[]."""
        words = []
        # y_step=50 → y-gap pruning 통과 (>= _MIN_ANCHOR_Y_GAP=30)
        for i in range(1, 36):
            y = 100 + i * 50
            words.append(_word(50, y, 70, y + 15, f"{i}."))
            words.append(_word(80, y, 200, y + 15, "다음 중"))
            words.append(_word(50, y + 25, 60, y + 40, "①"))
        page = _make_page(words=words, page_h=2500.0)  # 큰 페이지
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v3(page, cols, PAPER_TYPE_ADVANCED_MATERIAL)
        # 30+ → 본문 폭증 의심으로 anchor=[]
        self.assertEqual(anchors, [])


class AnalyzePdfV3IntegrationTests(TestCase):
    def _make_review_homework_pdf(self):
        """파일명에 '복습과제' — 학습자료 strict 적용."""
        return create_text_pdf_file(
            [
                "1. 다음 중 옳은 것을 고르시오 ① 가 ② 나 ③ 다",
                "2. 다음 그림에서 옳은 것은 ① ② ③ ④ ⑤",
                "1. 학습 목표를 확인한다",
            ],
            suffix="_복습과제_test.pdf",
            y_step=200,
        )

    def _make_exam_pdf(self):
        return create_text_pdf_file(
            ["1. 다음 그림은 어떤 동물인가?", "2. 다음 식물의 이름은?"],
            suffix="_중간고사_test.pdf",
            font_size=12,
            y_step=200,
        )

    def test_v3_classifies_review_homework_filename(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v3
        import os
        pdf = self._make_review_homework_pdf()
        try:
            result = analyze_pdf_v3(pdf, file_name=os.path.basename(pdf))
            self.assertEqual(result["version"], "v3")
            self.assertEqual(result["paper_type"], PAPER_TYPE_REVIEW_HOMEWORK)
            self.assertGreaterEqual(result["paper_type_confidence"], 0.8)
        finally:
            os.unlink(pdf)

    def test_v3_classifies_exam_filename(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v3
        import os
        pdf = self._make_exam_pdf()
        try:
            result = analyze_pdf_v3(pdf, file_name=os.path.basename(pdf))
            self.assertEqual(result["paper_type"], PAPER_TYPE_EXAM)
        finally:
            os.unlink(pdf)

    def test_v3_paper_type_in_output(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v3
        import os
        pdf = self._make_exam_pdf()
        try:
            result = analyze_pdf_v3(pdf)
            self.assertIn("paper_type", result)
            self.assertIn("paper_type_confidence", result)
            self.assertIn("paper_type_debug", result)
            self.assertIn("cross_page", result)
        finally:
            os.unlink(pdf)


class V3RegressionTests(TestCase):
    def test_v3_does_not_import_orm(self):
        """v3 추가 후에도 tier0_native_pdf 모듈 ORM 미import."""
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "from apps.domains.matchup.models",
            "MatchupProblem",
            "ProblemSegmentationProposal",
            ".objects.",
            ".save(",
            ".delete(",
            ".bulk_create(",
        )
        for token in forbidden:
            self.assertNotIn(token, src, f"v3 추가 후 forbidden token '{token}' 발견")

    def test_v1_v2_v3_all_callable(self):
        """v1/v2/v3 모두 호출 가능 (regression)."""
        from academy.adapters.ai.detection.tier0_native_pdf import (
            analyze_pdf, analyze_pdf_v2, analyze_pdf_v3,
            detect_problem_anchors, detect_problem_anchors_v2, detect_problem_anchors_v3,
        )
        for fn in (analyze_pdf, analyze_pdf_v2, analyze_pdf_v3,
                   detect_problem_anchors, detect_problem_anchors_v2,
                   detect_problem_anchors_v3):
            self.assertTrue(callable(fn))
