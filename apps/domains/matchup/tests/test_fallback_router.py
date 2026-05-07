"""Stage 5.6 (2026-05-07) — fallback_router dry-run 단위 테스트.

검증:
- route_fallback 분기 (TIER1_OCR / TIER2_VLM / TIER2_VLM_HYBRID / YOLO_FAST_PATH / TIER0)
- mock cost cap 검증 (per_doc_cap_usd 안)
- mock_ocr_request / mock_vlm_request schema 정확성
- 실 OCR/VLM SDK import 0회 (regression)
- DB 모델 import 0회 (regression)
"""
from __future__ import annotations

from unittest import TestCase

from apps.domains.matchup.segmentation.fallback_router import (
    CostCapMock,
    FallbackRouteDecision,
    OcrMockRequest,
    VlmMockRequest,
    decision_to_dict,
    route_fallback,
)


def _tier0_result_template(**overrides) -> dict:
    base = {
        "version": "v5_4",
        "pdf_path": "/tmp/x.pdf",
        "page_count": 10,
        "text_pages": 10,
        "tier1_required": False,
        "tier1_reason": "",
        "_internal_paper_type": "exam",
        "_internal_paper_type_confidence": 0.9,
        "layout_v2": {"type": "single_column", "confidence": 0.8, "x0_clusters": [0.1, 0.5], "page_p50": 2},
        "anchor_filter_v54": {},
        "profile_used": False,
        "pages": [
            {
                "page_index": i, "page_width": 595, "page_height": 842,
                "has_embedded_text": True, "role": "problem",
                "role_confidence": 0.8, "anchor_count": 3,
                "anchors": [], "bbox_candidates": [{"bbox_norm": [0.1, 0.1, 0.7, 0.2]}] * 3,
            } for i in range(10)
        ],
        "cross_page": {
            "detected_total": 30, "expected_max": 30,
            "sequence_continuity": 0.9, "duplicates_dropped": 0,
            "suspicious_pages": [],
        },
    }
    base.update(overrides)
    return base


class RouteTier1OcrTests(TestCase):
    def test_tier1_required_routes_to_ocr(self):
        t0 = _tier0_result_template(
            tier1_required=True, tier1_reason="scanned_no_text_layer",
            text_pages=0,
        )
        d = route_fallback(t0)
        self.assertEqual(d.route, "TIER1_OCR_REQUIRED")
        self.assertIsNotNone(d.mock_ocr_request)
        self.assertIsNone(d.mock_vlm_request)
        self.assertIn("scanned", d.reason.lower())

    def test_ocr_request_includes_all_pages(self):
        t0 = _tier0_result_template(tier1_required=True, page_count=15)
        d = route_fallback(t0)
        self.assertEqual(len(d.mock_ocr_request.page_indices), 15)

    def test_ocr_cost_cap_within_default(self):
        t0 = _tier0_result_template(tier1_required=True, page_count=20)
        d = route_fallback(t0)
        cap = d.mock_ocr_request.cost_cap
        self.assertTrue(cap.within_cap)
        self.assertEqual(cap.estimated_units, 20)
        # google_cloud_vision $0.0015/page × 20 = $0.03 < $5 cap
        self.assertAlmostEqual(cap.estimated_total_usd, 0.03, places=4)

    def test_ocr_engine_tesseract_zero_cost(self):
        t0 = _tier0_result_template(tier1_required=True, page_count=20)
        d = route_fallback(t0, ocr_engine="tesseract")
        cap = d.mock_ocr_request.cost_cap
        self.assertEqual(cap.engine, "tesseract")
        self.assertEqual(cap.estimated_total_usd, 0.0)
        self.assertTrue(cap.within_cap)


class RouteTier2VlmTests(TestCase):
    def test_no_anchors_with_text_routes_to_vlm(self):
        t0 = _tier0_result_template(
            tier1_required=False,
            pages=[
                {
                    "page_index": i, "page_width": 595, "page_height": 842,
                    "has_embedded_text": True, "role": "problem",
                    "role_confidence": 0.8, "anchor_count": 0,
                    "anchors": [], "bbox_candidates": [],
                } for i in range(5)
            ],
            page_count=5,
        )
        d = route_fallback(t0)
        self.assertEqual(d.route, "TIER2_VLM_REQUIRED")
        self.assertIsNotNone(d.mock_vlm_request)

    def test_low_cand_ratio_routes_to_hybrid(self):
        # 10 page 중 problem page 8, but only 1 page 가 anchor 가짐
        # cand_per_problem_page = 1/8 = 0.125 < 0.25
        pages = []
        for i in range(10):
            anchor_cnt = 1 if i == 0 else 0
            cand = [{"bbox_norm": [0.1, 0.1, 0.7, 0.2]}] if i == 0 else []
            pages.append({
                "page_index": i, "page_width": 595, "page_height": 842,
                "has_embedded_text": True, "role": "problem",
                "role_confidence": 0.8, "anchor_count": anchor_cnt,
                "anchors": [], "bbox_candidates": cand,
            })
        t0 = _tier0_result_template(pages=pages, page_count=10)
        d = route_fallback(t0)
        self.assertEqual(d.route, "TIER2_VLM_HYBRID")
        self.assertIsNotNone(d.mock_vlm_request)
        # hybrid 는 anchor 0 인 page 만 VLM 으로
        self.assertGreater(len(d.mock_vlm_request.page_indices), 0)

    def test_vlm_cost_cap_within_default(self):
        t0 = _tier0_result_template(
            pages=[
                {
                    "page_index": i, "page_width": 595, "page_height": 842,
                    "has_embedded_text": True, "role": "problem",
                    "role_confidence": 0.8, "anchor_count": 0,
                    "anchors": [], "bbox_candidates": [],
                } for i in range(50)
            ],
            page_count=50,
        )
        d = route_fallback(t0, vlm_engine="gemini_vision")
        cap = d.mock_vlm_request.cost_cap
        # gemini $0.001875/call × 50 = $0.094 < $5 cap
        self.assertTrue(cap.within_cap)
        self.assertEqual(cap.engine, "gemini_vision")


class RouteYoloFastPathTests(TestCase):
    def test_high_profile_match_routes_to_yolo(self):
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
                },
                "single_column__single": {
                    "layout_type": "single_column",
                    "cluster_pattern": "single",
                    "sample_count": 720,
                    "x0_allowed_regions": [[0.0, 0.06]],
                },
            },
        }
        t0 = _tier0_result_template(
            cross_page={"detected_total": 30, "expected_max": 30,
                        "sequence_continuity": 0.85, "duplicates_dropped": 0,
                        "suspicious_pages": []},
            profile_used=True,
        )
        d = route_fallback(t0, profile=profile)
        self.assertEqual(d.route, "YOLO_FAST_PATH_CANDIDATE")
        self.assertIsNone(d.mock_ocr_request)
        self.assertIsNone(d.mock_vlm_request)


class RouteTier0SufficientTests(TestCase):
    def test_default_to_tier0(self):
        # profile 없음 + cand 충분 + tier1 미요청 → TIER0_SUFFICIENT
        t0 = _tier0_result_template()
        d = route_fallback(t0)
        self.assertEqual(d.route, "TIER0_SUFFICIENT")
        self.assertIsNone(d.mock_ocr_request)
        self.assertIsNone(d.mock_vlm_request)


class DecisionToDictTests(TestCase):
    def test_decision_serializable(self):
        t0 = _tier0_result_template(tier1_required=True, page_count=5)
        d = route_fallback(t0)
        out = decision_to_dict(d)
        self.assertIn("route", out)
        self.assertIn("mock_ocr_request", out)
        self.assertEqual(out["route"], "TIER1_OCR_REQUIRED")
        # mock_ocr_request 가 dict 로 변환됨
        self.assertIsInstance(out["mock_ocr_request"], dict)
        self.assertIn("cost_cap", out["mock_ocr_request"])

    def test_no_mock_when_not_applicable(self):
        t0 = _tier0_result_template()
        d = route_fallback(t0)
        out = decision_to_dict(d)
        self.assertNotIn("mock_ocr_request", out)
        self.assertNotIn("mock_vlm_request", out)


class RouterRegressionTests(TestCase):
    def test_no_real_api_imports(self):
        """실 OCR/VLM SDK import 0회 — string label 은 허용 (mock label).

        docstring / engine label string mention 은 제외 — 실제 import 또는 호출만 검사.
        """
        from apps.domains.matchup.segmentation import fallback_router
        import inspect
        src = inspect.getsource(fallback_router)
        # docstring 제거
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden_imports = (
            "import requests",
            "from requests",
            "import google.generativeai",
            "from google.generativeai",
            "import google.cloud",
            "from google.cloud",
            "import openai",
            "from openai",
            "import anthropic",
            "from anthropic",
            "import pytesseract",
            "from pytesseract",
        )
        for token in forbidden_imports:
            self.assertNotIn(token, src,
                             f"router 에서 실 SDK import '{token}' 발견")
        # 호출 패턴
        forbidden_calls = (
            ".generate_content(", ".images.generate(",
            "Tesseract.image_to_string", "annotate_image(",
            "boto3.client(",
        )
        for token in forbidden_calls:
            self.assertNotIn(token, src,
                             f"router 에서 실 SDK 호출 '{token}' 발견")

    def test_no_db_model_imports(self):
        """모델 클래스 import 또는 .objects access 가 코드 path 에 0회.

        docstring / 주석 mention 은 허용 (사용자 directive 준수 명시).
        """
        from apps.domains.matchup.segmentation import fallback_router
        import inspect
        src = inspect.getsource(fallback_router)
        # docstring 제거 — 첫 번째 triple-quoted block 통째로 제거
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "import TenantSegmentationProfile", "import LayoutFingerprint",
            "import ManualCorrectionDelta", "import MatchupProblem",
            "import ProblemSegmentationProposal",
            "TenantSegmentationProfile.objects",
            "LayoutFingerprint.objects",
            "ManualCorrectionDelta.objects",
            "MatchupProblem.objects",
            "ProblemSegmentationProposal.objects",
            ".objects.create(", ".objects.bulk_create(", ".objects.update(",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"router 에서 DB 모델 access '{token}' 발견")

    def test_route_decision_dataclass_fields(self):
        d = FallbackRouteDecision(
            route="TIER0_SUFFICIENT", reason="test",
            confidence=0.5, tier0_summary={},
        )
        # 필수 필드
        self.assertTrue(hasattr(d, "route"))
        self.assertTrue(hasattr(d, "reason"))
        self.assertTrue(hasattr(d, "tier0_summary"))
        self.assertTrue(hasattr(d, "mock_ocr_request"))
        self.assertTrue(hasattr(d, "mock_vlm_request"))
