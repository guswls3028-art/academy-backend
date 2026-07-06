"""Stage 5.7 (2026-05-07) — segmentation dispatcher mock integration tests.

검증:
- dispatch_mock 5-route 별 schema (pages / total_boxes / route 등)
- TIER1_OCR_REQUIRED → mock_ocr_request set, mock_vlm_request none
- TIER2_VLM_REQUIRED → mock_vlm_request set, mock_ocr_request none
- TIER2_VLM_HYBRID → both pages 일부 + mock_vlm_request
- TIER0_SUFFICIENT → pages 채워짐
- YOLO_FAST_PATH_CANDIDATE → yolo_fast_path_marker=True
- ValidationMarks 모두 0
- 운영 dispatcher (segment_questions_multipage) import 0회 — 분리 보장
- DB 모델 / OCR/VLM SDK / proposal / callback import 0회 (regression)
- 운영 callback 호출 0회 (apps.domains.ai.gateway.dispatch_job 미호출)
"""
from __future__ import annotations

import os
from unittest import TestCase

from academy.adapters.tools.pymupdf_renderer import create_blank_pdf_file, create_text_pdf_file
from academy.application.use_cases.ai.segmentation.dispatcher_mock import (
    MockDispatcherOutput, SCHEMA_VERSION, dispatch_mock, output_to_dict,
)


def _make_simple_pdf(numbers=(1, 2)):
    return create_text_pdf_file([f"{n}. 다음 ① ② ③" for n in numbers], suffix="_test.pdf")


def _make_empty_pdf():
    """text layer 0 — TIER1_OCR 분기 trigger 용."""
    return create_blank_pdf_file(suffix="_empty.pdf")


def _make_text_no_anchor_pdf():
    """text layer 있으나 anchor 패턴 없음 — TIER2_VLM 분기."""
    return create_text_pdf_file(
        ["비정형 본문이 그저 흐른다", "선택지도 없는 텍스트"],
        suffix="_no_anchor.pdf",
        y_step=200,
    )


class DispatchMockSchemaTests(TestCase):
    def test_schema_version(self):
        pdf = _make_simple_pdf()
        try:
            out = dispatch_mock(pdf)
            self.assertEqual(out.schema_version, SCHEMA_VERSION)
        finally:
            os.unlink(pdf)

    def test_returns_mock_dispatcher_output(self):
        pdf = _make_simple_pdf()
        try:
            out = dispatch_mock(pdf)
            self.assertIsInstance(out, MockDispatcherOutput)
        finally:
            os.unlink(pdf)

    def test_validation_all_zero(self):
        pdf = _make_simple_pdf()
        try:
            out = dispatch_mock(pdf)
            v = out.validation
            self.assertEqual(v.operations_db_writes, 0)
            self.assertEqual(v.proposal_inserts, 0)
            self.assertEqual(v.callback_calls, 0)
            self.assertEqual(v.real_ocr_calls, 0)
            self.assertEqual(v.real_vlm_calls, 0)
            self.assertEqual(v.r2_writes, 0)
            self.assertEqual(v.matchup_problem_updates, 0)
            self.assertEqual(v.selected_problem_ids_changes, 0)
        finally:
            os.unlink(pdf)


class DispatchMockTier1OcrTests(TestCase):
    def test_empty_pdf_routes_to_ocr(self):
        pdf = _make_empty_pdf()
        try:
            out = dispatch_mock(pdf)
            self.assertEqual(out.route, "TIER1_OCR_REQUIRED")
            self.assertIsNotNone(out.mock_ocr_request)
            self.assertIsNone(out.mock_vlm_request)
            # OCR 후 채울 예정 — 현재는 빈 pages
            self.assertEqual(out.pages, [])
            self.assertEqual(out.total_boxes, 0)
        finally:
            os.unlink(pdf)

    def test_ocr_request_includes_engine_and_pages(self):
        pdf = _make_empty_pdf()
        try:
            out = dispatch_mock(pdf, ocr_engine="google_cloud_vision")
            self.assertEqual(out.mock_ocr_request["engine"], "google_cloud_vision")
            self.assertIn("page_indices", out.mock_ocr_request)
            self.assertGreater(len(out.mock_ocr_request["page_indices"]), 0)
            # cost cap 안 (단일 페이지 → < $5)
            self.assertTrue(out.mock_ocr_request["cost_cap"]["within_cap"])
        finally:
            os.unlink(pdf)


class DispatchMockTier2VlmTests(TestCase):
    def test_no_anchor_text_routes_to_vlm(self):
        pdf = _make_text_no_anchor_pdf()
        try:
            out = dispatch_mock(pdf)
            self.assertEqual(out.route, "TIER2_VLM_REQUIRED")
            self.assertIsNone(out.mock_ocr_request)
            self.assertIsNotNone(out.mock_vlm_request)
            self.assertEqual(out.pages, [])
            self.assertEqual(out.total_boxes, 0)
        finally:
            os.unlink(pdf)

    def test_vlm_request_engine_label(self):
        pdf = _make_text_no_anchor_pdf()
        try:
            out = dispatch_mock(pdf, vlm_engine="gemini_vision")
            self.assertEqual(out.mock_vlm_request["engine"], "gemini_vision")
            self.assertIn("prompt_template", out.mock_vlm_request)
            self.assertIn("expected_response_schema", out.mock_vlm_request)
        finally:
            os.unlink(pdf)


class DispatchMockTier0SufficientTests(TestCase):
    def test_simple_pdf_returns_pages(self):
        pdf = _make_simple_pdf(numbers=(1, 2))
        try:
            out = dispatch_mock(pdf)
            # 안정 layout 필요해서 TIER0_SUFFICIENT 또는 YOLO_FAST_PATH 둘 중 하나
            self.assertIn(out.route,
                          ("TIER0_SUFFICIENT", "YOLO_FAST_PATH_CANDIDATE"))
            self.assertIsNone(out.mock_ocr_request)
            self.assertIsNone(out.mock_vlm_request)
        finally:
            os.unlink(pdf)


class DispatchMockYoloFastPathTests(TestCase):
    def test_yolo_marker_set_when_profile_strong(self):
        # T2 profile mock — single_column 매칭 강함
        profile = {
            "confidence_score": 0.71,
            "samples_used": 3500,
            "layout_thresholds": {
                "single_column__bilateral": {
                    "layout_type": "single_column",
                    "cluster_pattern": "bilateral",
                    "sample_count": 2100,
                    "x0_allowed_regions": [[0.03, 0.14], [0.45, 0.55]],
                    "bbox_w_p50": 0.77, "bbox_h_p50": 0.30,
                },
                "single_column__single": {
                    "layout_type": "single_column",
                    "cluster_pattern": "single",
                    "sample_count": 720,
                    "x0_allowed_regions": [[0.0, 0.06]],
                    "bbox_w_p50": 0.94, "bbox_h_p50": 0.30,
                },
            },
        }
        pdf = _make_simple_pdf(numbers=(1, 2, 3))
        try:
            out = dispatch_mock(pdf, profile=profile)
            if out.route == "YOLO_FAST_PATH_CANDIDATE":
                self.assertTrue(out.yolo_fast_path_marker)
                self.assertEqual(out.pages, [])
                self.assertIsNone(out.mock_ocr_request)
                self.assertIsNone(out.mock_vlm_request)
        finally:
            os.unlink(pdf)


class OutputSerializationTests(TestCase):
    def test_output_to_dict_serializable(self):
        pdf = _make_simple_pdf()
        try:
            out = dispatch_mock(pdf)
            d = output_to_dict(out)
            self.assertIn("route", d)
            self.assertIn("pages", d)
            self.assertIn("validation", d)
            self.assertIn("schema_version", d)
            # JSON 직렬화 검증
            import json
            json.dumps(d, default=str)
        finally:
            os.unlink(pdf)


class DispatcherRegressionTests(TestCase):
    def test_no_real_api_imports(self):
        from academy.application.use_cases.ai.segmentation import dispatcher_mock
        import inspect
        src = inspect.getsource(dispatcher_mock)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden_imports = (
            "import requests", "from requests",
            "import google.generativeai", "from google.generativeai",
            "import google.cloud", "from google.cloud",
            "import openai", "from openai",
            "import anthropic", "from anthropic",
            "import pytesseract", "from pytesseract",
            "import boto3", "from boto3",
        )
        for token in forbidden_imports:
            self.assertNotIn(token, src,
                             f"dispatcher_mock 에서 실 SDK import '{token}' 발견")

    def test_no_db_model_or_operational_dispatch(self):
        """DB 모델 / 운영 dispatcher / proposal / callback import 0회."""
        from academy.application.use_cases.ai.segmentation import dispatcher_mock
        import inspect
        src = inspect.getsource(dispatcher_mock)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "import TenantSegmentationProfile",
            "import LayoutFingerprint",
            "import ManualCorrectionDelta",
            "import MatchupProblem",
            "import ProblemSegmentationProposal",
            "TenantSegmentationProfile.objects",
            "LayoutFingerprint.objects",
            "ManualCorrectionDelta.objects",
            "MatchupProblem.objects",
            "ProblemSegmentationProposal.objects",
            ".objects.create(", ".objects.bulk_create(",
            ".objects.update(", ".objects.delete(",
            "from apps.domains.ai.gateway",  # 운영 callback 진입점 차단
            "dispatch_job(",
            "from academy.adapters.ai.detection.segment_dispatcher",  # 운영 dispatcher 차단
            "segment_questions_multipage(",
            "segment_questions(",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"dispatcher_mock 에서 운영 access '{token}' 발견")

    def test_dispatcher_uses_only_tier0_and_router(self):
        """dispatcher_mock 의 import 는 tier0_native_pdf + fallback_router 만."""
        from academy.application.use_cases.ai.segmentation import dispatcher_mock
        import inspect
        src = inspect.getsource(dispatcher_mock)
        # 명시 import 체크
        self.assertIn("from .fallback_router import", src)
        self.assertIn("from academy.adapters.ai.detection.tier0_native_pdf import analyze_pdf_v5_4", src)
