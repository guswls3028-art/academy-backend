"""Stage 5.5.3 (2026-05-07) — Tier 0 v5_3 (profile dry-run) 단위 테스트.

검증:
- get_x0_regions_from_profile: profile / fallback
- filter_anchors_v5_3: profile 인자 받기
- analyze_pdf_v5_3: profile 인자 schema
- DB 모델 import 0회 (regression)
"""
from __future__ import annotations

from unittest import TestCase

from academy.adapters.ai.detection.tier0_native_pdf import (
    LAYOUT_FOUR_BLOCK,
    LAYOUT_SINGLE_COLUMN,
    LAYOUT_UNKNOWN,
    NumberAnchor,
    PAPER_TYPE_EXAM,
    PAPER_TYPE_REVIEW_HOMEWORK,
    PAPER_TYPE_WORKBOOK_MAIN,
    filter_anchors_v5_3,
    get_x0_regions_from_profile,
)


def _anchor(n, x0=50, y0=100, page_idx=0):
    return NumberAnchor(
        number=n, page_index=page_idx,
        bbox=(float(x0), float(y0), float(x0 + 20), float(y0 + 15)),
        text=f"{n}.", style="arabic_dot", confidence=0.9,
    )


# ── get_x0_regions_from_profile ──


class GetX0RegionsTests(TestCase):
    def test_profile_overrides_default(self):
        profile = {
            "paper_type_thresholds": {
                "workbook_main": {
                    "x0_allowed_regions": [[0.0, 0.10], [0.45, 0.55]],
                },
            },
        }
        regions = get_x0_regions_from_profile(
            PAPER_TYPE_WORKBOOK_MAIN, LAYOUT_FOUR_BLOCK, profile,
        )
        self.assertEqual(regions, [(0.0, 0.10), (0.45, 0.55)])

    def test_no_profile_uses_default(self):
        """profile=None 일 때 _LAYOUT_X0_ALLOW fallback."""
        regions = get_x0_regions_from_profile(
            PAPER_TYPE_EXAM, LAYOUT_SINGLE_COLUMN, None,
        )
        self.assertEqual(regions, [(0.0, 0.30)])  # _LAYOUT_X0_ALLOW[SINGLE_COLUMN]

    def test_profile_missing_paper_type_uses_default(self):
        """profile 에 paper_type 없으면 default."""
        profile = {"paper_type_thresholds": {}}
        regions = get_x0_regions_from_profile(
            PAPER_TYPE_REVIEW_HOMEWORK, LAYOUT_FOUR_BLOCK, profile,
        )
        self.assertEqual(regions, [(0.0, 0.18), (0.42, 0.62)])  # FOUR_BLOCK default

    def test_invalid_profile_format_uses_default(self):
        profile = {"paper_type_thresholds": "not_a_dict"}
        regions = get_x0_regions_from_profile(
            PAPER_TYPE_EXAM, LAYOUT_SINGLE_COLUMN, profile,
        )
        self.assertEqual(regions, [(0.0, 0.30)])

    def test_invalid_regions_format_uses_default(self):
        profile = {
            "paper_type_thresholds": {
                "exam": {"x0_allowed_regions": "not_a_list"},
            },
        }
        regions = get_x0_regions_from_profile(
            PAPER_TYPE_EXAM, LAYOUT_SINGLE_COLUMN, profile,
        )
        self.assertEqual(regions, [(0.0, 0.30)])

    def test_unknown_layout_fallback(self):
        regions = get_x0_regions_from_profile(
            PAPER_TYPE_EXAM, LAYOUT_UNKNOWN, None,
        )
        self.assertEqual(regions, [(0.0, 0.50)])  # UNKNOWN default


# ── filter_anchors_v5_3 ──


class FilterAnchorsV53Tests(TestCase):
    def test_profile_used_marker(self):
        per_page = [[_anchor(1, x0=30)]]
        profile = {
            "paper_type_thresholds": {
                "workbook_main": {"x0_allowed_regions": [[0.0, 0.30]]},
            },
        }
        result, debug = filter_anchors_v5_3(
            per_page, PAPER_TYPE_WORKBOOK_MAIN, LAYOUT_FOUR_BLOCK,
            [595.0], profile=profile,
        )
        self.assertTrue(debug["profile_used"])

    def test_no_profile_marker(self):
        per_page = [[_anchor(1, x0=30)]]
        result, debug = filter_anchors_v5_3(
            per_page, PAPER_TYPE_EXAM, LAYOUT_SINGLE_COLUMN,
            [595.0], profile=None,
        )
        self.assertFalse(debug["profile_used"])

    def test_profile_x0_regions_in_debug(self):
        per_page = [[_anchor(1, x0=30)]]
        profile = {
            "paper_type_thresholds": {
                "exam": {"x0_allowed_regions": [[0.0, 0.20]]},
            },
        }
        result, debug = filter_anchors_v5_3(
            per_page, PAPER_TYPE_EXAM, LAYOUT_SINGLE_COLUMN,
            [595.0], profile=profile,
        )
        self.assertEqual(debug["x0_regions"], [(0.0, 0.20)])


# ── analyze_pdf_v5_3 통합 ──


class AnalyzePdfV53Tests(TestCase):
    def _make_simple_pdf(self):
        import fitz, tempfile
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 100), "1. 다음 ① ② ③", fontsize=10)
        page.insert_text((50, 300), "2. 다음 ① ② ③", fontsize=10)
        tmp = tempfile.NamedTemporaryFile(suffix="_test.pdf", delete=False)
        tmp.close()
        doc.save(tmp.name)
        doc.close()
        return tmp.name

    def test_v53_no_profile(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v5_3
        import os
        pdf = self._make_simple_pdf()
        try:
            result = analyze_pdf_v5_3(pdf)
            self.assertEqual(result["version"], "v5_3")
            self.assertFalse(result["profile_used"])
            self.assertIn("_internal_paper_type", result)
            self.assertNotIn("paper_type", result)
        finally:
            os.unlink(pdf)

    def test_v53_with_profile(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v5_3
        import os
        pdf = self._make_simple_pdf()
        profile = {
            "tenant_id": 2,
            "paper_type_thresholds": {
                "exam": {"x0_allowed_regions": [[0.0, 0.30]]},
            },
        }
        try:
            result = analyze_pdf_v5_3(pdf, profile=profile)
            self.assertEqual(result["version"], "v5_3")
            self.assertTrue(result["profile_used"])
            self.assertIn("anchor_filter_v53", result)
            self.assertIn("x0_regions", result["anchor_filter_v53"])
        finally:
            os.unlink(pdf)


# ── regression ──


class V53RegressionTests(TestCase):
    def test_v1_to_v53_callable(self):
        from academy.adapters.ai.detection.tier0_native_pdf import (
            analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
            analyze_pdf_v5_1, analyze_pdf_v5_2, analyze_pdf_v5_3,
        )
        for fn in (analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
                   analyze_pdf_v5_1, analyze_pdf_v5_2, analyze_pdf_v5_3):
            self.assertTrue(callable(fn))

    def test_no_db_model_imports(self):
        """v5_3 가 TenantSegmentationProfile / LayoutFingerprint / ManualCorrectionDelta
        모델 클래스 import 하지 않음 — JSON dict 만 사용."""
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden_patterns = (
            "import TenantSegmentationProfile",
            "import LayoutFingerprint",
            "import ManualCorrectionDelta",
            "TenantSegmentationProfile.objects",
            "LayoutFingerprint.objects",
            "ManualCorrectionDelta.objects",
        )
        for token in forbidden_patterns:
            self.assertNotIn(token, src, f"v5_3 신모델 import '{token}' 발견")

    def test_no_real_api_imports(self):
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "import requests", "google.generativeai", "google.cloud.vision",
            "import openai", "import anthropic",
        )
        for token in forbidden:
            self.assertNotIn(token, src)
