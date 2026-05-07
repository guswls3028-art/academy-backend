"""Stage 5.5.5 (2026-05-07) — Tier 0 v5_5 (anchor recall plateau test) 단위 테스트.

검증:
- _V55_MAX_LEGIT_QUESTION_NUMBER 상수 = 100
- detect_problem_anchors_v5_5 (max_number 60 → 100 범위)
- analyze_pdf_v5_5 entrypoint (v5_4 filter 재사용 / 신규 schema 키)
- v5_4 filter / x0 region / height filter 모두 유지 (FP 폭증 방어)
- DB 모델 import 0회 (regression)
- v5_5 의 final 결정: bare_digit / dedup relax / choice range 확장 모두 거부
  (Stage 5.5.5 1차 시도 결과 FP 폭증으로 채택 X)
"""
from __future__ import annotations

from unittest import TestCase

from apps.domains.matchup.segmentation.tier0_native_pdf import (
    LAYOUT_SINGLE_COLUMN,
    NumberAnchor,
    PAPER_TYPE_EXAM,
    PAPER_TYPE_WORKBOOK_MAIN,
    _V55_MAX_LEGIT_QUESTION_NUMBER,
    detect_problem_anchors_v5_5,
    filter_anchors_v5_4,
)


class V55ConstantsTests(TestCase):
    def test_max_question_number_100(self):
        self.assertEqual(_V55_MAX_LEGIT_QUESTION_NUMBER, 100)


class V55AnchorMaxNumberTests(TestCase):
    def _make_pdf_with_numbers(self, numbers):
        import fitz, tempfile
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        for i, n in enumerate(numbers):
            page.insert_text((50, 100 + i * 60), f"{n}. 다음 ① ② ③", fontsize=10)
        tmp = tempfile.NamedTemporaryFile(suffix="_test.pdf", delete=False)
        tmp.close()
        doc.save(tmp.name)
        doc.close()
        return tmp.name

    def test_v5_5_admits_number_70(self):
        from apps.domains.matchup.segmentation.tier0_native_pdf import (
            extract_page_blocks, detect_columns,
        )
        import os
        pdf = self._make_pdf_with_numbers([70])
        try:
            pages = extract_page_blocks(pdf)
            cols = detect_columns(pages[0].word_blocks, pages[0].page_width)
            anchors = detect_problem_anchors_v5_5(
                pages[0], cols, PAPER_TYPE_EXAM,
            )
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0].number, 70)
        finally:
            os.unlink(pdf)

    def test_v5_5_rejects_number_above_100(self):
        from apps.domains.matchup.segmentation.tier0_native_pdf import (
            extract_page_blocks, detect_columns,
        )
        import os
        pdf = self._make_pdf_with_numbers([101])
        try:
            pages = extract_page_blocks(pdf)
            cols = detect_columns(pages[0].word_blocks, pages[0].page_width)
            anchors = detect_problem_anchors_v5_5(
                pages[0], cols, PAPER_TYPE_EXAM,
            )
            self.assertEqual(len(anchors), 0)
        finally:
            os.unlink(pdf)

    def test_v5_5_rejects_bare_digit(self):
        """v5_5 1차 시도에서 bare_digit 추가는 FP 폭증으로 reject 됨 — 채택 X."""
        from apps.domains.matchup.segmentation.tier0_native_pdf import (
            extract_page_blocks, detect_columns,
        )
        import fitz, tempfile, os
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        # bare digit "02" — period 없음
        page.insert_text((50, 100), "02 다음 그림은 ① ② ③", fontsize=10)
        tmp = tempfile.NamedTemporaryFile(suffix="_test.pdf", delete=False)
        tmp.close()
        doc.save(tmp.name)
        doc.close()
        try:
            pages = extract_page_blocks(tmp.name)
            cols = detect_columns(pages[0].word_blocks, pages[0].page_width)
            anchors = detect_problem_anchors_v5_5(
                pages[0], cols, PAPER_TYPE_EXAM,
            )
            # bare_digit 미admit — 0개 anchor
            self.assertEqual(len(anchors), 0)
        finally:
            os.unlink(tmp.name)


class V55AnalyzePdfTests(TestCase):
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

    def test_v5_5_returns_v5_5_version(self):
        from apps.domains.matchup.segmentation.tier0_native_pdf import analyze_pdf_v5_5
        import os
        pdf = self._make_simple_pdf()
        try:
            result = analyze_pdf_v5_5(pdf)
            self.assertEqual(result["version"], "v5_5")
            self.assertIn("v55_cand_total", result)
            self.assertIn("v54_cand_total_for_compare", result)
            self.assertIn("v55_explosion_marker", result)
            self.assertIn("v55_h_filter", result)
            self.assertIn("anchor_filter_v55", result)
            # v5_4 filter 재사용 — anchor_cluster_pattern 키 포함
            self.assertIn("anchor_cluster_pattern", result["anchor_filter_v55"])
        finally:
            os.unlink(pdf)

    def test_v5_5_paper_type_internal_only(self):
        from apps.domains.matchup.segmentation.tier0_native_pdf import analyze_pdf_v5_5
        import os
        pdf = self._make_simple_pdf()
        try:
            result = analyze_pdf_v5_5(pdf)
            self.assertIn("_internal_paper_type", result)
            self.assertNotIn("paper_type", result)
        finally:
            os.unlink(pdf)


class V55RegressionTests(TestCase):
    def test_v1_to_v5_5_callable(self):
        from apps.domains.matchup.segmentation.tier0_native_pdf import (
            analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
            analyze_pdf_v5_1, analyze_pdf_v5_2, analyze_pdf_v5_3, analyze_pdf_v5_4,
            analyze_pdf_v5_5,
        )
        for fn in (analyze_pdf, analyze_pdf_v2, analyze_pdf_v3, analyze_pdf_v4,
                   analyze_pdf_v5_1, analyze_pdf_v5_2, analyze_pdf_v5_3,
                   analyze_pdf_v5_4, analyze_pdf_v5_5):
            self.assertTrue(callable(fn))

    def test_v5_5_no_db_model_imports(self):
        from apps.domains.matchup.segmentation import tier0_native_pdf
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
            self.assertNotIn(token, src, f"v5_5 신모델 import '{token}' 발견")

    def test_v5_5_no_real_api_imports(self):
        from apps.domains.matchup.segmentation import tier0_native_pdf
        import inspect
        src = inspect.getsource(tier0_native_pdf)
        forbidden = (
            "import requests", "google.generativeai", "google.cloud.vision",
            "import openai", "import anthropic",
        )
        for token in forbidden:
            self.assertNotIn(token, src)
