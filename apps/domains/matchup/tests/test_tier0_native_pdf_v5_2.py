"""Stage 5.5.2 (2026-05-07) — Tier 0 v5_2 anchor 자체 개선 단위 테스트.

검증:
- estimate_inline_overflow: number 빈도 / unique ratio 임계
- x0_in_layout_allowed_region: layout-aware
- filter_anchors_v5_2: overflow → cross-page dedup + x0 demotion
- analyze_pdf_v5_2: 통합 + paper_type internal-only
- regression: v1~v5_2 callable
"""
from __future__ import annotations

from unittest import TestCase

from academy.adapters.ai.detection.tier0_native_pdf import (
    LAYOUT_FOUR_BLOCK,
    LAYOUT_PAGE_LEVEL,
    LAYOUT_SINGLE_COLUMN,
    LAYOUT_UNKNOWN,
    NumberAnchor,
    estimate_inline_overflow,
    filter_anchors_v5_2,
    x0_in_layout_allowed_region,
)


def _anchor(n, x0=50, y0=100, page_idx=0):
    return NumberAnchor(
        number=n, page_index=page_idx,
        bbox=(float(x0), float(y0), float(x0 + 20), float(y0 + 15)),
        text=f"{n}.", style="arabic_dot", confidence=0.9,
    )


class EstimateInlineOverflowTests(TestCase):
    def test_no_anchors_no_overflow(self):
        is_o, debug = estimate_inline_overflow([])
        self.assertFalse(is_o)
        self.assertEqual(debug["total"], 0)

    def test_high_freq_per_number_triggers(self):
        """number=1 이 5+ 페이지에 등장 → overflow."""
        per_page = [[_anchor(1, page_idx=p)] for p in range(5)]
        is_o, debug = estimate_inline_overflow(per_page)
        self.assertTrue(is_o)
        self.assertEqual(debug["max_freq_per_number"], 5)

    def test_low_unique_ratio_triggers(self):
        """unique 1, total 4 → 0.25 < 0.3 → overflow."""
        per_page = [
            [_anchor(1, page_idx=0), _anchor(1, page_idx=0)],
            [_anchor(1, page_idx=1), _anchor(1, page_idx=1)],
        ]
        is_o, debug = estimate_inline_overflow(per_page)
        self.assertTrue(is_o)
        self.assertLess(debug["unique_ratio"], 0.3)

    def test_normal_distribution_no_overflow(self):
        """unique 10, total 10 → no overflow."""
        per_page = [[_anchor(i, page_idx=i % 3) for i in range(1, 11)]]
        is_o, debug = estimate_inline_overflow(per_page)
        self.assertFalse(is_o)
        self.assertEqual(debug["unique_numbers"], 10)


class X0LayoutAllowedRegionTests(TestCase):
    def test_four_block_left_column(self):
        self.assertTrue(x0_in_layout_allowed_region(0.05, LAYOUT_FOUR_BLOCK))

    def test_four_block_right_column(self):
        self.assertTrue(x0_in_layout_allowed_region(0.50, LAYOUT_FOUR_BLOCK))

    def test_four_block_middle_blocked(self):
        self.assertFalse(x0_in_layout_allowed_region(0.30, LAYOUT_FOUR_BLOCK))

    def test_single_column_allows_left(self):
        self.assertTrue(x0_in_layout_allowed_region(0.10, LAYOUT_SINGLE_COLUMN))
        self.assertTrue(x0_in_layout_allowed_region(0.25, LAYOUT_SINGLE_COLUMN))

    def test_single_column_blocks_right(self):
        self.assertFalse(x0_in_layout_allowed_region(0.50, LAYOUT_SINGLE_COLUMN))

    def test_page_level_allows_all(self):
        self.assertTrue(x0_in_layout_allowed_region(0.50, LAYOUT_PAGE_LEVEL))
        self.assertTrue(x0_in_layout_allowed_region(0.95, LAYOUT_PAGE_LEVEL))

    def test_unknown_conservative(self):
        self.assertTrue(x0_in_layout_allowed_region(0.30, LAYOUT_UNKNOWN))
        self.assertFalse(x0_in_layout_allowed_region(0.70, LAYOUT_UNKNOWN))


class FilterAnchorsV52Tests(TestCase):
    def test_no_overflow_keeps_all(self):
        per_page = [[_anchor(i, x0=30) for i in range(1, 6)]]  # x0 norm = 30/595 ≈ 0.05
        result, debug = filter_anchors_v5_2(per_page, LAYOUT_SINGLE_COLUMN, [595.0])
        self.assertFalse(debug["applied_dedup"])
        self.assertEqual(len(result[0]), 5)

    def test_overflow_triggers_first_occurrence_dedup(self):
        """number 1, 2 가 5 페이지에 반복 → overflow → first occurrence keep."""
        per_page = [
            [_anchor(1, x0=30, page_idx=p), _anchor(2, x0=30, page_idx=p)]
            for p in range(5)
        ]
        result, debug = filter_anchors_v5_2(per_page, LAYOUT_SINGLE_COLUMN, [595.0] * 5)
        self.assertTrue(debug["applied_dedup"])
        # 첫 페이지만 (1, 2)
        self.assertEqual(len(result[0]), 2)
        for p in range(1, 5):
            self.assertEqual(result[p], [])

    def test_x0_demotion_drops_low_confidence(self):
        """four_block layout 에서 x0 가 middle (0.30) 인 anchor 는 demotion → drop."""
        # x0=178 in 595-page = 0.30 → four_block 허용 영역 밖 (0.18~0.42 사이 middle)
        per_page = [[_anchor(1, x0=178, page_idx=0)]]
        result, debug = filter_anchors_v5_2(per_page, LAYOUT_FOUR_BLOCK, [595.0])
        # confidence 0.9 * 0.5 = 0.45 → 0.3 이상이라 keep
        self.assertEqual(len(result[0]), 1)
        # 더 낮은 confidence (0.5 * 0.5 = 0.25 < 0.3) 는 drop
        per_page2 = [[NumberAnchor(
            number=1, page_index=0, bbox=(178, 100, 198, 115),
            text="1.", style="arabic_dot", confidence=0.5,
        )]]
        result2, debug2 = filter_anchors_v5_2(per_page2, LAYOUT_FOUR_BLOCK, [595.0])
        self.assertEqual(len(result2[0]), 0)
        self.assertEqual(debug2["demoted_count"], 1)


class AnalyzePdfV52IntegrationTests(TestCase):
    def _make_simple_pdf(self):
        import fitz, tempfile
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 100), "1. 다음 그림은? ① ② ③", fontsize=10)
        page.insert_text((50, 300), "2. 다음 식물? ① ② ③", fontsize=10)
        tmp = tempfile.NamedTemporaryFile(suffix="_test.pdf", delete=False)
        tmp.close()
        doc.save(tmp.name)
        doc.close()
        return tmp.name

    def test_v52_internal_paper_type(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v5_2
        import os
        pdf = self._make_simple_pdf()
        try:
            result = analyze_pdf_v5_2(pdf)
            self.assertEqual(result["version"], "v5_2")
            self.assertIn("_internal_paper_type", result)
            self.assertNotIn("paper_type", result)
        finally:
            os.unlink(pdf)

    def test_v52_anchor_filter_in_output(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v5_2
        import os
        pdf = self._make_simple_pdf()
        try:
            result = analyze_pdf_v5_2(pdf)
            self.assertIn("anchor_filter_v52", result)
            self.assertIn("overflow", result["anchor_filter_v52"])
        finally:
            os.unlink(pdf)


class V52RegressionTests(TestCase):
    def test_v1_to_v52_callable(self):
        from academy.adapters.ai.detection.tier0_native_pdf import (
            analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
            analyze_pdf_v5_1, analyze_pdf_v5_2,
        )
        for fn in (analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
                   analyze_pdf_v5_1, analyze_pdf_v5_2):
            self.assertTrue(callable(fn))

    def test_no_orm_write(self):
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "from apps.domains.matchup.models import",
            "import MatchupProblem",
            "MatchupProblem.objects",
            "ProblemSegmentationProposal.objects",
            ".bulk_create(",
        )
        for token in forbidden:
            self.assertNotIn(token, src)

    def test_no_real_api_imports(self):
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "import requests", "from requests",
            "google.generativeai", "google.cloud.vision",
            "import openai", "import anthropic",
        )
        for token in forbidden:
            self.assertNotIn(token, src)
