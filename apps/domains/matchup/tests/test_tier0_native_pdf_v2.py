"""Stage 5.2 (2026-05-06) — Tier 0 v2 정밀화 단위 테스트.

검증 항목:
- _is_line_leading_word: leftmost word strict
- _is_answer_or_explanation_page: 정답표 / 해설지 / standalone / zb 차단
- detect_columns: 1단/2단/4단 분포 추정
- detect_problem_anchors_v2: line_start strict + 60 상한 + column 근접
- classify_page_role_v2: 키워드 영역 제한 + anchor 우선
- cross_page_validate: 중복 number / sequence continuity
- analyze_pdf_v2: tier1_required / scanned PDF 분류
"""
from __future__ import annotations

from unittest import TestCase

from academy.adapters.ai.detection.tier0_native_pdf import (
    PageBlocks,
    _MAX_LEGIT_QUESTION_NUMBER_V2,
    _is_answer_or_explanation_page,
    _is_line_leading_word,
    classify_page_role_v2,
    cross_page_validate,
    detect_columns,
    detect_problem_anchors_v2,
    NumberAnchor,
)


def _word(x0, y0, x1, y1, text):
    return {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "text": text}


def _make_page(*, words=None, blocks=None, has_text=True, page_w=595.0, page_h=842.0, page_index=0):
    return PageBlocks(
        page_index=page_index, page_width=page_w, page_height=page_h,
        has_embedded_text=has_text, text_blocks=blocks or [],
        word_blocks=words or [], image_blocks=[],
    )


class IsLineLeadingWordTests(TestCase):
    def test_alone_word_is_leading(self):
        w1 = _word(50, 100, 70, 115, "1.")
        self.assertTrue(_is_line_leading_word(w1, [w1]))

    def test_leftmost_word_is_leading(self):
        w1 = _word(50, 100, 70, 115, "1.")
        w2 = _word(80, 100, 200, 115, "다음 그림은")
        self.assertTrue(_is_line_leading_word(w1, [w1, w2]))

    def test_inline_number_is_not_leading(self):
        """본문 안 inline '1.' 패턴 — 같은 line 의 leading word 아님."""
        w_lead = _word(50, 100, 200, 115, "다음에서")
        w_inline = _word(220, 100, 240, 115, "1.")  # 본문 중간 inline
        self.assertFalse(_is_line_leading_word(w_inline, [w_lead, w_inline]))

    def test_different_y_band_independent(self):
        """다른 라인 word 는 leading 판단에 무관."""
        w_other_line = _word(40, 50, 80, 65, "X")
        w_target = _word(50, 100, 70, 115, "1.")
        self.assertTrue(_is_line_leading_word(w_target, [w_other_line, w_target]))


class IsAnswerOrExplanationPageTests(TestCase):
    def test_clean_question_page_passes(self):
        page = _make_page(blocks=[
            {"x0": 0, "y0": 0, "x1": 100, "y1": 50, "text": "1. 다음 중 옳은 것을 고르시오. 보기에서 ① ② ③ ④ ⑤"}
        ])
        blocked, reason = _is_answer_or_explanation_page(page)
        self.assertFalse(blocked)

    def test_answer_table_blocked(self):
        """1.④ 2.③ 3.② 4.⑤ 5.① — 정답표 5+."""
        page = _make_page(blocks=[
            {"x0": 0, "y0": 0, "x1": 100, "y1": 50,
             "text": "1.④ 2.③ 3.② 4.⑤ 5.① 6.② 7.③"},
        ])
        blocked, reason = _is_answer_or_explanation_page(page)
        self.assertTrue(blocked)
        self.assertEqual(reason, "answer_table")

    def test_explanation_page_blocked(self):
        page = _make_page(blocks=[
            {"x0": 0, "y0": 0, "x1": 100, "y1": 50,
             "text": "1. 정답 ④ 문제 해설 ... 2. 정답 ③ 문제 해설 ... 3. 정답 ① 문제 해설 ..."}
        ])
        blocked, reason = _is_answer_or_explanation_page(page)
        self.assertTrue(blocked)
        self.assertEqual(reason, "explanation_page")

    def test_standalone_answer_blocked(self):
        page = _make_page(blocks=[
            {"x0": 0, "y0": 0, "x1": 100, "y1": 50,
             "text": "정답 ① 정답 ④ 정답 ②"}
        ])
        blocked, reason = _is_answer_or_explanation_page(page)
        self.assertTrue(blocked)
        self.assertEqual(reason, "standalone_answer")

    def test_zb_marker_blocked(self):
        page = _make_page(blocks=[
            {"x0": 0, "y0": 0, "x1": 100, "y1": 50,
             "text": "5. zb5) 다음 글을 읽고 11. zb11) 다음은 17. zb17) 그림"}
        ])
        blocked, reason = _is_answer_or_explanation_page(page)
        self.assertTrue(blocked)
        self.assertEqual(reason, "zb_markers")

    def test_answer_table_with_question_indicator_passes(self):
        """정답표 패턴 + 본문 지시문 → 본문 페이지 (학습 자료)."""
        page = _make_page(blocks=[
            {"x0": 0, "y0": 0, "x1": 100, "y1": 50,
             "text": "1.① 2.② 3.③ 4.④ 5.⑤ 다음 중 옳은 것을 고르시오"}
        ])
        blocked, reason = _is_answer_or_explanation_page(page)
        self.assertFalse(blocked)


class DetectColumnsTests(TestCase):
    def test_few_words_returns_single_column(self):
        page_w = 595.0
        cols = detect_columns([_word(50, 100, 60, 115, "a")], page_w)
        self.assertEqual(cols.column_count, 1)
        self.assertEqual(cols.column_lefts, [0.0])

    def test_two_columns_detected(self):
        """좌측 (x≈30) 10개 + 우측 (x≈300) 10개 → 2 column."""
        words = []
        for i in range(20):
            words.append(_word(30, 100 + i * 20, 50, 115 + i * 20, "L"))
            words.append(_word(300, 100 + i * 20, 320, 115 + i * 20, "R"))
        cols = detect_columns(words, 595.0)
        self.assertEqual(cols.column_count, 2)
        # 좌측 lefts < 우측 lefts
        self.assertLess(cols.column_lefts[0], cols.column_lefts[1])

    def test_single_column_when_words_concentrated(self):
        """word x0 가 좁은 영역 (≈30) 에만 모이면 1 column."""
        words = [_word(30 + i % 5, 100 + i * 12, 50, 115 + i * 12, "x") for i in range(40)]
        cols = detect_columns(words, 595.0)
        self.assertEqual(cols.column_count, 1)


class DetectProblemAnchorsV2Tests(TestCase):
    def test_line_leading_word_picked(self):
        """라인의 가장 왼쪽 word 인 '1.' picking."""
        page = _make_page(words=[
            _word(50, 100, 70, 115, "1."),
            _word(80, 100, 200, 115, "다음 중 옳은 것은"),
        ])
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v2(page, cols)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 1)

    def test_inline_number_pruned(self):
        """본문 inline '1.' 같은 false positive 차단."""
        page = _make_page(words=[
            _word(50, 100, 200, 115, "다음에서 보기를 참고하여"),  # leading word
            _word(220, 100, 240, 115, "1."),                       # inline (line 시작 X)
        ])
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v2(page, cols)
        self.assertEqual(len(anchors), 0)

    def test_number_above_60_dropped(self):
        """60 초과 — _MAX_LEGIT_QUESTION_NUMBER 운영 정의."""
        page = _make_page(words=[_word(50, 100, 80, 115, "61.")])
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v2(page, cols)
        self.assertEqual(anchors, [])

    def test_max_legit_60(self):
        page = _make_page(words=[_word(50, 100, 80, 115, "60.")])
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v2(page, cols)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 60)

    def test_answer_table_page_returns_empty(self):
        """정답표 페이지 — anchor 0개."""
        page = _make_page(
            words=[_word(50, 100, 80, 115, "1.")],
            blocks=[{
                "x0": 0, "y0": 0, "x1": 100, "y1": 50,
                "text": "1.④ 2.③ 3.② 4.⑤ 5.① 6.② 7.③",
            }],
        )
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v2(page, cols)
        self.assertEqual(anchors, [])

    def test_dedup_same_number_same_page(self):
        """같은 페이지에서 같은 번호 anchor 중복은 conf 가장 높은 것만."""
        page = _make_page(words=[
            _word(50, 100, 70, 115, "1."),    # leading, conf high
            _word(50, 200, 60, 215, "1)"),    # leading, conf 낮음
        ])
        cols = detect_columns(page.word_blocks, page.page_width)
        anchors = detect_problem_anchors_v2(page, cols)
        # 둘 다 leading 이지만 dedup 후 1개 (number=1)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 1)
        # conf 더 높은 "1." 선택
        self.assertGreaterEqual(anchors[0].confidence, 0.85)


class ClassifyPageRoleV2Tests(TestCase):
    def test_keyword_in_top_area_classifies_cover(self):
        """페이지 상단 25% 안 키워드 — cover 로 분류."""
        page = _make_page(blocks=[{
            "x0": 0, "y0": 50, "x1": 100, "y1": 100,  # 상단 25% (page_h=842 → 25%≈210)
            "text": "2026 표지",
        }])
        role = classify_page_role_v2(page, anchors=[])
        self.assertEqual(role.role, "cover")

    def test_keyword_in_body_with_many_anchors_is_problem(self):
        """본문 깊숙이 '기출' 같은 키워드 있어도 anchor 5+ 면 problem."""
        anchors = [
            NumberAnchor(number=i, page_index=0, bbox=(50, 100 + i * 50, 70, 115 + i * 50),
                         text=f"{i}.", style="arabic_dot", confidence=0.9)
            for i in range(1, 7)
        ]
        page = _make_page(blocks=[{
            "x0": 0, "y0": 50, "x1": 100, "y1": 100,
            "text": "1. 다음 중 옳은 것은 — 2024 기출 변형",  # 키워드 본문에
        }])
        role = classify_page_role_v2(page, anchors=anchors)
        self.assertEqual(role.role, "problem")

    def test_answer_key_blocked_first(self):
        """정답표/해설은 _is_answer_or_explanation_page 먼저 차단 → answer_key."""
        page = _make_page(blocks=[{
            "x0": 0, "y0": 0, "x1": 100, "y1": 50,
            "text": "1. 정답 ④ 문제 해설 / 2. 정답 ③ 문제 해설 / 3. 정답 ① 문제 해설",
        }])
        role = classify_page_role_v2(page, anchors=[])
        self.assertEqual(role.role, "answer_key")

    def test_no_anchors_no_keywords_is_unknown(self):
        page = _make_page(blocks=[{
            "x0": 0, "y0": 0, "x1": 100, "y1": 50, "text": "..."
        }])
        role = classify_page_role_v2(page, anchors=[])
        self.assertEqual(role.role, "unknown")

    def test_no_text_returns_unknown(self):
        page = _make_page(has_text=False)
        role = classify_page_role_v2(page, anchors=[])
        self.assertEqual(role.role, "unknown")


class CrossPageValidateTests(TestCase):
    def test_clean_sequence_full_continuity(self):
        per_page = [
            [
                NumberAnchor(number=i, page_index=0, bbox=(50, 100, 70, 115),
                             text=f"{i}.", style="arabic_dot", confidence=0.9)
                for i in range(1, 6)
            ],
            [
                NumberAnchor(number=i, page_index=1, bbox=(50, 100, 70, 115),
                             text=f"{i}.", style="arabic_dot", confidence=0.9)
                for i in range(6, 11)
            ],
        ]
        cross = cross_page_validate(per_page)
        self.assertEqual(cross.detected_total, 10)
        self.assertEqual(cross.expected_max, 10)
        self.assertAlmostEqual(cross.sequence_continuity, 1.0)
        self.assertEqual(cross.duplicates_dropped, 0)

    def test_duplicate_number_marks_suspicious(self):
        per_page = [
            [NumberAnchor(number=1, page_index=0, bbox=(0, 0, 0, 0),
                          text="1.", style="arabic_dot", confidence=0.9)],
            [NumberAnchor(number=1, page_index=1, bbox=(0, 0, 0, 0),
                          text="1.", style="arabic_dot", confidence=0.9)],
        ]
        cross = cross_page_validate(per_page)
        self.assertEqual(cross.duplicates_dropped, 1)
        self.assertIn(1, cross.suspicious_pages)

    def test_partial_continuity(self):
        """1, 3, 5 만 detect — continuity 3/5 = 0.6."""
        per_page = [[
            NumberAnchor(number=n, page_index=0, bbox=(0, 0, 0, 0),
                         text=f"{n}.", style="arabic_dot", confidence=0.9)
            for n in (1, 3, 5)
        ]]
        cross = cross_page_validate(per_page)
        self.assertAlmostEqual(cross.sequence_continuity, 3 / 5)


class AnalyzePdfV2IntegrationTests(TestCase):
    """analyze_pdf_v2 통합 — in-memory PDF + tier1_required 분류."""

    def _make_text_pdf(self):
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 100), "1. 다음 그림은 어떤 동물인가?", fontsize=12)
        page.insert_text((50, 300), "2. 다음 식물의 이름은?", fontsize=12)
        page.insert_text((50, 500), "3. 다음 시는 누가 썼는가?", fontsize=12)
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        doc.save(tmp.name)
        doc.close()
        return tmp.name

    def _make_image_only_pdf(self):
        """text 레이어 없는 PDF (image only) — Tier 1 후보."""
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        # 빈 페이지 — text 없음
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        doc.save(tmp.name)
        doc.close()
        return tmp.name

    def test_text_pdf_not_tier1_required(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v2
        import os
        pdf = self._make_text_pdf()
        try:
            result = analyze_pdf_v2(pdf)
            self.assertEqual(result["version"], "v2")
            self.assertFalse(result["tier1_required"])
            self.assertEqual(result["page_count"], 1)
            page = result["pages"][0]
            self.assertEqual(page["anchor_count"], 3)
            # cross-page validation 결과 포함
            self.assertIn("cross_page", result)
            cp = result["cross_page"]
            self.assertEqual(cp["detected_total"], 3)
        finally:
            os.unlink(pdf)

    def test_image_only_pdf_marked_tier1_required(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v2
        import os
        pdf = self._make_image_only_pdf()
        try:
            result = analyze_pdf_v2(pdf)
            self.assertTrue(result["tier1_required"])
            self.assertEqual(result["tier1_reason"], "scanned_no_text_layer")
        finally:
            os.unlink(pdf)


class V2RegressionTests(TestCase):
    """v2 가 v1 의 핵심 격리 원칙 유지 — ORM 미접근."""

    def test_v2_does_not_import_orm(self):
        """v2 함수 추가 후에도 tier0_native_pdf 모듈은 ORM/모델 미import."""
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "from apps.domains.matchup.models",
            "MatchupProblem",
            "MatchupHitReport",
            "ProblemSegmentationProposal",
            ".objects.",
            ".save(",
            ".delete(",
            ".bulk_create(",
        )
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"tier0_native_pdf v2 추가 후 forbidden token '{token}' 발견 — 운영 격리 위반",
            )

    def test_v1_functions_still_present(self):
        """v2 추가가 v1 함수를 깨뜨리지 않음 (regression)."""
        from academy.adapters.ai.detection.tier0_native_pdf import (
            analyze_pdf,
            classify_page_role,
            derive_bbox_candidates,
            detect_problem_anchors,
            extract_page_blocks,
        )
        for fn in (analyze_pdf, classify_page_role, derive_bbox_candidates,
                   detect_problem_anchors, extract_page_blocks):
            self.assertTrue(callable(fn))

    def test_v2_max_legit_constant_is_60(self):
        self.assertEqual(_MAX_LEGIT_QUESTION_NUMBER_V2, 60)
