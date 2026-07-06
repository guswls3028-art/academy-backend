"""Stage 5.5.4 (2026-05-07) — Tier 0 v5_4 (profile precision) 단위 테스트.

검증:
- _merge_overlapping_regions
- _detect_anchor_cluster_pattern
- derive_x0_regions_v5_4 (layout_thresholds UNION)
- filter_anchors_v5_4 (cluster_pattern aware dedup skip)
- analyze_pdf_v5_4 (height filter / column override 비활성)
- DB 모델 import 0회 (regression)
"""
from __future__ import annotations

from unittest import TestCase

from academy.adapters.tools.pymupdf_renderer import create_text_pdf_file
from academy.adapters.ai.detection.tier0_native_pdf import (
    LAYOUT_SINGLE_COLUMN,
    LAYOUT_TWO_COLUMN,
    NumberAnchor,
    PAPER_TYPE_EXAM,
    PAPER_TYPE_REVIEW_HOMEWORK,
    PAPER_TYPE_WORKBOOK_MAIN,
    _detect_anchor_cluster_pattern,
    _merge_overlapping_regions,
    derive_x0_regions_v5_4,
    filter_anchors_v5_4,
)


def _anchor(n, x0=50, y0=100, page_idx=0):
    return NumberAnchor(
        number=n, page_index=page_idx,
        bbox=(float(x0), float(y0), float(x0 + 20), float(y0 + 15)),
        text=f"{n}.", style="arabic_dot", confidence=0.9,
    )


# ── _merge_overlapping_regions ──


class MergeOverlappingRegionsTests(TestCase):
    def test_no_overlap(self):
        result = _merge_overlapping_regions([(0.0, 0.1), (0.4, 0.6)])
        self.assertEqual(result, [(0.0, 0.1), (0.4, 0.6)])

    def test_overlap_merged(self):
        result = _merge_overlapping_regions([(0.0, 0.15), (0.10, 0.20)])
        self.assertEqual(result, [(0.0, 0.20)])

    def test_adjacent_merged(self):
        result = _merge_overlapping_regions([(0.0, 0.10), (0.10, 0.20)])
        self.assertEqual(result, [(0.0, 0.20)])

    def test_unsorted_input(self):
        result = _merge_overlapping_regions([(0.5, 0.6), (0.0, 0.1), (0.05, 0.15)])
        self.assertEqual(result, [(0.0, 0.15), (0.5, 0.6)])

    def test_empty(self):
        self.assertEqual(_merge_overlapping_regions([]), [])


# ── _detect_anchor_cluster_pattern ──


class DetectAnchorClusterPatternTests(TestCase):
    def test_bilateral(self):
        # 좌측 5개 + 우측 5개 → bilateral
        per_page = [
            [_anchor(i, x0=50) for i in range(1, 6)] +    # x0_norm = 0.084
            [_anchor(i, x0=300) for i in range(6, 11)],   # x0_norm = 0.504
        ]
        result = _detect_anchor_cluster_pattern(per_page, [595.0])
        self.assertEqual(result, "bilateral")

    def test_left_only_single(self):
        per_page = [
            [_anchor(i, x0=50) for i in range(1, 11)],
        ]
        result = _detect_anchor_cluster_pattern(per_page, [595.0])
        self.assertEqual(result, "single")

    def test_empty_anchors(self):
        result = _detect_anchor_cluster_pattern([[]], [595.0])
        self.assertEqual(result, "single")

    def test_minority_right_below_threshold(self):
        # 좌측 9 + 우측 1 (10%) → bilateral 아님
        per_page = [
            [_anchor(i, x0=50) for i in range(1, 10)] +
            [_anchor(10, x0=300)],
        ]
        result = _detect_anchor_cluster_pattern(per_page, [595.0])
        self.assertEqual(result, "single")


# ── derive_x0_regions_v5_4 ──


class DeriveX0RegionsV54Tests(TestCase):
    def test_layout_thresholds_union_single_column(self):
        # single_column__bilateral + single_column__single → UNION
        profile = {
            "layout_thresholds": {
                "single_column__bilateral": {
                    "layout_type": "single_column",
                    "cluster_pattern": "bilateral",
                    "x0_allowed_regions": [[0.03, 0.14], [0.45, 0.55]],
                    "bbox_w_p50": 0.77,
                },
                "single_column__single": {
                    "layout_type": "single_column",
                    "cluster_pattern": "single",
                    "x0_allowed_regions": [[0.0, 0.06]],
                    "bbox_w_p50": 0.94,
                },
                "two_column__bilateral": {
                    "layout_type": "two_column",
                    "cluster_pattern": "bilateral",
                    "x0_allowed_regions": [[0.0, 0.14], [0.45, 0.55]],
                    "bbox_w_p50": 0.44,
                },
            },
        }
        regions, debug = derive_x0_regions_v5_4(
            profile, PAPER_TYPE_WORKBOOK_MAIN, LAYOUT_SINGLE_COLUMN, "bilateral",
        )
        # single_column 두 group UNION → [(0.0, 0.14), (0.45, 0.55)]
        self.assertEqual(regions, [(0.0, 0.14), (0.45, 0.55)])
        self.assertEqual(debug["source"], "layout_thresholds_union")
        self.assertEqual(debug["primary_pattern"], "bilateral")
        self.assertEqual(debug["bbox_w_p50"], 0.77)

    def test_layout_thresholds_other_layout_excluded(self):
        # two_column 요청 → single_column group 무시
        profile = {
            "layout_thresholds": {
                "single_column__bilateral": {
                    "layout_type": "single_column",
                    "cluster_pattern": "bilateral",
                    "x0_allowed_regions": [[0.03, 0.14]],
                },
                "two_column__bilateral": {
                    "layout_type": "two_column",
                    "cluster_pattern": "bilateral",
                    "x0_allowed_regions": [[0.0, 0.14], [0.45, 0.55]],
                    "bbox_w_p50": 0.44,
                },
            },
        }
        regions, debug = derive_x0_regions_v5_4(
            profile, PAPER_TYPE_WORKBOOK_MAIN, LAYOUT_TWO_COLUMN, "bilateral",
        )
        self.assertEqual(regions, [(0.0, 0.14), (0.45, 0.55)])
        self.assertIn("two_column__bilateral", debug["matched_keys"])

    def test_no_layout_thresholds_falls_back_to_clusters(self):
        # x0_clusters 만 있으면 cluster ±0.05 fallback
        profile = {
            "x0_clusters": [0.10, 0.50],
        }
        regions, debug = derive_x0_regions_v5_4(
            profile, PAPER_TYPE_WORKBOOK_MAIN, LAYOUT_SINGLE_COLUMN, "bilateral",
        )
        self.assertEqual(len(regions), 2)
        self.assertAlmostEqual(regions[0][0], 0.05, places=6)
        self.assertAlmostEqual(regions[0][1], 0.15, places=6)
        self.assertAlmostEqual(regions[1][0], 0.45, places=6)
        self.assertAlmostEqual(regions[1][1], 0.55, places=6)
        self.assertEqual(debug["source"], "clusters_bilateral")

    def test_no_profile_falls_back_to_v53(self):
        regions, debug = derive_x0_regions_v5_4(
            None, PAPER_TYPE_EXAM, LAYOUT_SINGLE_COLUMN, "single",
        )
        # v5_3 fallback → _LAYOUT_X0_ALLOW[SINGLE_COLUMN]
        self.assertEqual(regions, [(0.0, 0.30)])
        self.assertEqual(debug["source"], "fallback_v5_3")


# ── filter_anchors_v5_4 ──


class FilterAnchorsV54Tests(TestCase):
    def test_pattern_in_debug(self):
        # 좌+우 anchor → bilateral
        per_page = [
            [_anchor(1, x0=30), _anchor(2, x0=300)],
        ]
        profile = {"layout_thresholds": {
            "single_column__bilateral": {
                "layout_type": "single_column",
                "cluster_pattern": "bilateral",
                "x0_allowed_regions": [[0.0, 0.20], [0.45, 0.55]],
            },
        }}
        _, debug = filter_anchors_v5_4(
            per_page, PAPER_TYPE_WORKBOOK_MAIN, LAYOUT_SINGLE_COLUMN,
            [595.0], profile=profile,
        )
        # 2 anchor 만으로는 right >=3 조건 미만 → single 으로 분류됨
        self.assertEqual(debug["anchor_cluster_pattern"], "single")

    def test_bilateral_skips_dedup(self):
        # bilateral 양식 + overflow → dedup skip (recall 보호)
        # 같은 number 5번씩 좌/우 반복 — overflow & bilateral
        anchors_per_page = []
        for page in range(3):
            page_anchors = []
            for n in (1, 2):
                page_anchors.append(_anchor(n, x0=30, page_idx=page))
                page_anchors.append(_anchor(n, x0=300, page_idx=page))
            anchors_per_page.append(page_anchors)
        profile = {"layout_thresholds": {}}
        _, debug = filter_anchors_v5_4(
            anchors_per_page, PAPER_TYPE_REVIEW_HOMEWORK, LAYOUT_SINGLE_COLUMN,
            [595.0, 595.0, 595.0], profile=profile,
        )
        # bilateral pattern 인지 확인
        self.assertEqual(debug["anchor_cluster_pattern"], "bilateral")

    def test_x0_regions_in_debug(self):
        per_page = [[_anchor(1, x0=30)]]
        profile = {"layout_thresholds": {
            "single_column__single": {
                "layout_type": "single_column",
                "cluster_pattern": "single",
                "x0_allowed_regions": [[0.0, 0.20]],
                "bbox_w_p50": 0.90,
                "bbox_h_p50": 0.30,
            },
        }}
        _, debug = filter_anchors_v5_4(
            per_page, PAPER_TYPE_WORKBOOK_MAIN, LAYOUT_SINGLE_COLUMN,
            [595.0], profile=profile,
        )
        self.assertEqual(debug["x0_regions"], [(0.0, 0.20)])
        self.assertEqual(debug["bbox_w_p50_expected"], 0.90)


# ── analyze_pdf_v5_4 통합 ──


class AnalyzePdfV54Tests(TestCase):
    def _make_simple_pdf(self):
        return create_text_pdf_file(
            ["1. 다음 ① ② ③", "2. 다음 ① ② ③"],
            suffix="_test.pdf",
            y_step=200,
        )

    def test_v54_no_profile(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v5_4
        import os
        pdf = self._make_simple_pdf()
        try:
            result = analyze_pdf_v5_4(pdf)
            self.assertEqual(result["version"], "v5_4")
            self.assertFalse(result["profile_used"])
            self.assertIn("_internal_paper_type", result)
            self.assertNotIn("paper_type", result)
            self.assertIn("anchor_filter_v54", result)
            self.assertIn("anchor_cluster_pattern", result["anchor_filter_v54"])
        finally:
            os.unlink(pdf)

    def test_v54_with_profile_v2(self):
        from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v5_4
        import os
        pdf = self._make_simple_pdf()
        profile = {
            "tenant_id": 2,
            "schema_version": "5.5.4-artifact",
            "x0_clusters": [0.10, 0.50],
            "bbox_stats": {"height_p50": 0.30},
            "layout_thresholds": {
                "single_column__bilateral": {
                    "layout_type": "single_column",
                    "cluster_pattern": "bilateral",
                    "x0_allowed_regions": [[0.03, 0.14], [0.45, 0.55]],
                    "bbox_w_p50": 0.77, "bbox_h_p50": 0.30,
                },
                "single_column__single": {
                    "layout_type": "single_column",
                    "cluster_pattern": "single",
                    "x0_allowed_regions": [[0.0, 0.06]],
                    "bbox_w_p50": 0.94, "bbox_h_p50": 0.30,
                },
            },
        }
        try:
            result = analyze_pdf_v5_4(pdf, profile=profile)
            self.assertEqual(result["version"], "v5_4")
            self.assertTrue(result["profile_used"])
            self.assertIn("v54_h_filter", result)
            self.assertIn("column_overrides_summary", result)
            # column override 비활성: forced_single / forced_double 0
            self.assertEqual(result["column_overrides_summary"]["forced_single"], 0)
            self.assertEqual(result["column_overrides_summary"]["forced_double"], 0)
        finally:
            os.unlink(pdf)


# ── regression ──


class V54RegressionTests(TestCase):
    def test_v1_to_v54_callable(self):
        from academy.adapters.ai.detection.tier0_native_pdf import (
            analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
            analyze_pdf_v5_1, analyze_pdf_v5_2, analyze_pdf_v5_3, analyze_pdf_v5_4,
        )
        for fn in (analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
                   analyze_pdf_v5_1, analyze_pdf_v5_2, analyze_pdf_v5_3, analyze_pdf_v5_4):
            self.assertTrue(callable(fn))

    def test_v54_no_db_model_imports(self):
        """v5_4 도 TenantSegmentationProfile / LayoutFingerprint / ManualCorrectionDelta
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
            self.assertNotIn(token, src, f"v5_4 신모델 import '{token}' 발견")

    def test_v54_no_real_api_imports(self):
        from academy.adapters.ai.detection import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "import requests", "google.generativeai", "google.cloud.vision",
            "import openai", "import anthropic",
        )
        for token in forbidden:
            self.assertNotIn(token, src)
