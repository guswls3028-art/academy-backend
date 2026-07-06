"""Stage 5.0 (2026-05-06) — Tier 0 Native PDF parser prototype 단위 테스트.

검증 항목:
- detect_problem_anchors: arabic_dot / arabic_paren / circled / parened / line_start
- 잘못된 input (out-of-range / 빈 word) 무시
- derive_bbox_candidates: 인접 anchor 사이 영역 + 마지막 anchor 페이지 바닥까지
- bbox_norm 0~1 범위
- classify_page_role: cover/answer_key 키워드 / problem 분류
- analyze_pdf 인터페이스 (PyMuPDF 호출은 in-memory PDF 생성으로 통합 검증)

운영 DB 미접근. 실제 PDF 1개 (in-memory fitz 로 생성) → 전체 흐름 통합.
"""
from __future__ import annotations

from unittest import TestCase

from academy.adapters.tools.pymupdf_renderer import create_text_pdf_file
from academy.adapters.ai.detection.tier0_native_pdf import (
    PageBlocks,
    classify_page_role,
    derive_bbox_candidates,
    detect_problem_anchors,
)


def _make_word(x0, y0, x1, y1, text):
    return {"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1), "text": text}


def _make_page(
    *, text_blocks=None, word_blocks=None, has_text=True,
    page_w=595.0, page_h=842.0, page_index=0, image_blocks=None,
):
    return PageBlocks(
        page_index=page_index,
        page_width=page_w,
        page_height=page_h,
        has_embedded_text=has_text,
        text_blocks=text_blocks or [],
        word_blocks=word_blocks or [],
        image_blocks=image_blocks or [],
    )


class DetectProblemAnchorsTests(TestCase):
    def test_arabic_dot(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 70, 115, "1.")])
        anchors = detect_problem_anchors(page)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 1)
        self.assertEqual(anchors[0].style, "arabic_dot")
        self.assertGreater(anchors[0].confidence, 0.8)

    def test_arabic_paren(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 70, 115, "5)")])
        anchors = detect_problem_anchors(page)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 5)
        self.assertEqual(anchors[0].style, "arabic_paren")

    def test_circled_digit(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 70, 115, "①")])
        anchors = detect_problem_anchors(page)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 1)
        self.assertEqual(anchors[0].style, "circled")

    def test_circled_digit_high(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 70, 115, "⑮")])
        anchors = detect_problem_anchors(page)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 15)

    def test_parened_digit(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 70, 115, "⑴")])
        anchors = detect_problem_anchors(page)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 1)
        self.assertEqual(anchors[0].style, "parened")

    def test_arabic_bung(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 80, 115, "12번")])
        anchors = detect_problem_anchors(page)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 12)
        self.assertEqual(anchors[0].style, "arabic_bung")

    def test_inparen_arabic(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 80, 115, "(7)")])
        anchors = detect_problem_anchors(page)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 7)

    def test_out_of_range_number_ignored(self):
        """201 같은 너무 큰 숫자는 anchor 아님."""
        page = _make_page(word_blocks=[_make_word(50, 100, 100, 115, "999.")])
        self.assertEqual(detect_problem_anchors(page), [])

    def test_zero_ignored(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 70, 115, "0.")])
        self.assertEqual(detect_problem_anchors(page), [])

    def test_empty_text_ignored(self):
        page = _make_page(word_blocks=[_make_word(50, 100, 70, 115, "")])
        self.assertEqual(detect_problem_anchors(page), [])

    def test_non_anchor_text_ignored(self):
        """일반 텍스트는 anchor 아님."""
        page = _make_page(word_blocks=[_make_word(50, 100, 80, 115, "다음")])
        self.assertEqual(detect_problem_anchors(page), [])

    def test_left_margin_bonus(self):
        """좌측 30% 안 anchor 는 confidence bonus."""
        # left
        page_l = _make_page(
            word_blocks=[_make_word(20, 100, 40, 115, "1.")],
            page_w=595.0,
        )
        # right (50%+)
        page_r = _make_page(
            word_blocks=[_make_word(400, 100, 420, 115, "1.")],
            page_w=595.0,
        )
        a_l = detect_problem_anchors(page_l)[0]
        a_r = detect_problem_anchors(page_r)[0]
        self.assertGreater(a_l.confidence, a_r.confidence)

    def test_line_start_pattern_in_text_block(self):
        """text_block 첫 줄에 '1. xxx' — line_start anchor 추출."""
        page = _make_page(
            text_blocks=[{
                "x0": 50, "y0": 100, "x1": 500, "y1": 115,
                "text": "1. 다음 그림은 어떤 동물인가?",
            }],
        )
        anchors = detect_problem_anchors(page)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].number, 1)
        self.assertEqual(anchors[0].style, "line_start")

    def test_anchors_sorted_top_to_bottom(self):
        page = _make_page(word_blocks=[
            _make_word(50, 300, 70, 315, "3."),
            _make_word(50, 100, 70, 115, "1."),
            _make_word(50, 200, 70, 215, "2."),
        ])
        anchors = detect_problem_anchors(page)
        self.assertEqual([a.number for a in anchors], [1, 2, 3])

    def test_no_word_blocks(self):
        page = _make_page(word_blocks=[])
        self.assertEqual(detect_problem_anchors(page), [])


class DeriveBboxCandidatesTests(TestCase):
    def test_empty_anchors_returns_empty(self):
        page = _make_page()
        self.assertEqual(derive_bbox_candidates([], page), [])

    def test_single_anchor_uses_bottom_as_y1(self):
        page = _make_page(page_h=842.0)
        anchors = detect_problem_anchors(_make_page(
            word_blocks=[_make_word(50, 100, 70, 115, "1.")],
            page_h=842.0,
        ))
        candidates = derive_bbox_candidates(anchors, page)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        # y1 < page_h (5% margin)
        self.assertLess(c.bbox[3], 842.0)
        # bbox_norm 0~1 모두
        for v in c.bbox_norm:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_two_anchors_split_at_second_y0(self):
        word_a = _make_word(50, 100, 70, 115, "1.")
        word_b = _make_word(50, 400, 70, 415, "2.")
        page = _make_page(word_blocks=[word_a, word_b])
        anchors = detect_problem_anchors(page)
        candidates = derive_bbox_candidates(anchors, page)
        self.assertEqual(len(candidates), 2)
        # 첫 candidate y1 == 두 번째 anchor y0
        self.assertEqual(candidates[0].bbox[3], 400.0)
        # 두 번째 candidate 시작 y0 == 두 번째 anchor y0
        self.assertEqual(candidates[1].bbox[1], 400.0)

    def test_bbox_norm_consistency(self):
        page = _make_page(page_w=595.0, page_h=842.0,
                           word_blocks=[_make_word(50, 100, 70, 115, "1.")])
        anchors = detect_problem_anchors(page)
        c = derive_bbox_candidates(anchors, page)[0]
        x0, y0, x1, y1 = c.bbox
        nx, ny, nw, nh = c.bbox_norm
        self.assertAlmostEqual(nx, x0 / 595.0)
        self.assertAlmostEqual(ny, y0 / 842.0)
        self.assertAlmostEqual(nw, (x1 - x0) / 595.0)
        self.assertAlmostEqual(nh, (y1 - y0) / 842.0)

    def test_text_preview_collected_from_words_in_region(self):
        anchor_word = _make_word(50, 100, 70, 115, "1.")
        body_word = _make_word(80, 105, 200, 115, "다음")
        body_word2 = _make_word(80, 130, 200, 145, "그림")
        page = _make_page(word_blocks=[anchor_word, body_word, body_word2])
        anchors = detect_problem_anchors(page)
        candidates = derive_bbox_candidates(anchors, page)
        self.assertIn("다음", candidates[0].text_preview)
        self.assertIn("그림", candidates[0].text_preview)


class ClassifyPageRoleTests(TestCase):
    def test_no_text_returns_unknown(self):
        page = _make_page(has_text=False)
        role = classify_page_role(page)
        self.assertEqual(role.role, "unknown")

    def test_cover_keyword(self):
        page = _make_page(text_blocks=[{
            "x0": 0, "y0": 0, "x1": 100, "y1": 50,
            "text": "2026 표지 시험지",
        }])
        role = classify_page_role(page)
        self.assertEqual(role.role, "cover")

    def test_answer_key_keyword(self):
        page = _make_page(text_blocks=[{
            "x0": 0, "y0": 0, "x1": 100, "y1": 50,
            "text": "정답 및 해설",
        }])
        role = classify_page_role(page)
        self.assertEqual(role.role, "answer_key")

    def test_index_keyword(self):
        page = _make_page(text_blocks=[{
            "x0": 0, "y0": 0, "x1": 100, "y1": 50,
            "text": "목차 차례",
        }])
        role = classify_page_role(page)
        self.assertEqual(role.role, "index")

    def test_problem_when_anchors_present(self):
        # word_blocks 에 anchor 2개 → role=problem
        page = _make_page(
            word_blocks=[
                _make_word(50, 100, 70, 115, "1."),
                _make_word(50, 200, 70, 215, "2."),
            ],
            text_blocks=[{
                "x0": 0, "y0": 0, "x1": 100, "y1": 50,
                "text": "수학 시험",
            }],
        )
        role = classify_page_role(page)
        self.assertEqual(role.role, "problem")

    def test_unknown_when_no_anchors_and_no_keywords(self):
        page = _make_page(text_blocks=[{
            "x0": 0, "y0": 0, "x1": 100, "y1": 50,
            "text": "그냥 텍스트",
        }])
        role = classify_page_role(page)
        self.assertEqual(role.role, "unknown")


class AnalyzePdfIntegrationTests(TestCase):
    """analyze_pdf 통합 테스트 — in-memory PDF 생성 후 전체 흐름 검증."""

    def _make_pdf_with_anchors(self):
        """PyMuPDF 로 in-memory PDF 1페이지 생성. 문항 anchor "1.", "2.", "3." 포함."""
        return create_text_pdf_file(
            [
                "1. 다음 그림은 어떤 동물인가?",
                "2. 다음 그림은 무슨 식물인가?",
                "3. 다음 시는 누가 썼는가?",
            ],
            font_size=12,
            y_step=200,
        )

    def test_analyze_pdf_extracts_three_anchors(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf
        import os

        pdf_path = self._make_pdf_with_anchors()
        try:
            result = analyze_pdf(pdf_path)
            self.assertEqual(result["page_count"], 1)
            page = result["pages"][0]
            self.assertTrue(page["has_embedded_text"])
            # role: problem (anchor 3개)
            self.assertEqual(page["role"], "problem")
            # anchor 3개 (1., 2., 3.)
            self.assertEqual(page["anchor_count"], 3)
            anchor_numbers = [a["number"] for a in page["anchors"]]
            self.assertEqual(sorted(anchor_numbers), [1, 2, 3])
            # bbox_candidates 3개
            self.assertEqual(len(page["bbox_candidates"]), 3)
            # 모두 bbox_norm 이 0~1
            for c in page["bbox_candidates"]:
                for v in c["bbox_norm"]:
                    self.assertGreaterEqual(v, 0.0)
                    self.assertLessEqual(v, 1.0)
        finally:
            os.unlink(pdf_path)


class RunnerNoDbWriteTests(TestCase):
    """segmentation_eval 커맨드가 어떤 운영 DB write 도 안 하는지 정적 검증.

    해당 커맨드는 analyze_pdf + JSON dump 만 — ORM .save/.create/.update/.delete 없음.
    """

    def test_command_module_does_not_import_models(self):
        """segmentation_eval 모듈 자체에 MatchupProblem / HitReportEntry import 없음."""
        from apps.domains.matchup.management.commands import segmentation_eval as cmd
        import inspect

        src = inspect.getsource(cmd)
        forbidden = (
            "MatchupProblem.objects",
            "MatchupHitReport.objects",
            "MatchupHitReportEntry.objects",
            ".update(",
            ".delete(",
            ".bulk_update(",
            ".bulk_create(",
        )
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"segmentation_eval 모듈에 forbidden token '{token}' 발견 — DB write 가능성",
            )

    def test_tier0_module_does_not_touch_orm(self):
        """tier0_native_pdf 모듈도 ORM import 0회."""
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect

        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "from apps.domains.matchup.models",
            "MatchupProblem",
            "MatchupDocument",
            "MatchupHitReport",
            "ProblemSegmentationProposal",
            ".objects.",
            ".save(",
        )
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"tier0_native_pdf 모듈에 forbidden token '{token}' — 운영 격리 위반",
            )
