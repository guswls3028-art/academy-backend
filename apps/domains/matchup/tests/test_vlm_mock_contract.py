"""Stage 5.4 (2026-05-06) — VLM mock contract 단위 테스트.

검증:
- VlmBbox / VlmProblemDetection / VlmPageRequest / VlmPageResponse dataclass
- VlmPageType enum
- needs_vlm_fallback 휴리스틱
- validate_vlm_response schema validator
- MockVlmClient — 호출 0회 보장 (실 API 안 함)
"""
from __future__ import annotations

from unittest import TestCase

from apps.domains.matchup.segmentation.vlm_mock_contract import (
    MockVlmClient,
    VlmBbox,
    VlmPageRequest,
    VlmPageResponse,
    VlmPageType,
    VlmProblemDetection,
    VlmSchemaError,
    needs_vlm_fallback,
    validate_vlm_response,
)


class VlmDataclassTests(TestCase):
    def test_bbox_to_dict(self):
        b = VlmBbox(x=0.1, y=0.2, w=0.3, h=0.4, norm=True)
        self.assertEqual(b.to_dict(), {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4, "norm": True})

    def test_problem_to_dict(self):
        p = VlmProblemDetection(
            number=1, bbox=VlmBbox(0.1, 0.2, 0.3, 0.4), confidence=0.85, reason="visible",
        )
        d = p.to_dict()
        self.assertEqual(d["number"], 1)
        self.assertEqual(d["confidence"], 0.85)
        self.assertEqual(d["reason"], "visible")
        self.assertEqual(d["bbox"]["x"], 0.1)

    def test_page_response_to_dict(self):
        resp = VlmPageResponse(
            page_type=VlmPageType.PROBLEM,
            problems=[VlmProblemDetection(1, VlmBbox(0, 0, 1, 1), 0.9)],
            needs_review=False, confidence=0.9,
        )
        d = resp.to_dict()
        self.assertEqual(d["page_type"], "problem")
        self.assertEqual(len(d["problems"]), 1)


class NeedsVlmFallbackTests(TestCase):
    def test_tier1_required_triggers(self):
        need, reason = needs_vlm_fallback(
            paper_type="exam", tier1_required=True, tier0_anchor_count=0,
        )
        self.assertTrue(need)
        self.assertEqual(reason, "tier1_required")

    def test_unknown_no_anchor_triggers(self):
        need, reason = needs_vlm_fallback(
            paper_type="exam", tier1_required=False,
            tier0_anchor_count=0, page_role="unknown",
        )
        self.assertTrue(need)
        self.assertEqual(reason, "tier0_no_anchor")

    def test_high_duplicate_ratio_triggers(self):
        need, reason = needs_vlm_fallback(
            paper_type="review_homework", tier1_required=False,
            tier0_anchor_count=100, cross_page_duplicate_ratio=0.7,
        )
        self.assertTrue(need)
        self.assertEqual(reason, "duplicate_anchor_polution")

    def test_normal_case_does_not_trigger(self):
        need, reason = needs_vlm_fallback(
            paper_type="exam", tier1_required=False,
            tier0_anchor_count=20, page_role="problem",
        )
        self.assertFalse(need)


class ValidateVlmResponseTests(TestCase):
    def test_valid_response(self):
        payload = {
            "page_type": "problem",
            "problems": [
                {"number": 1, "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4, "norm": True},
                 "confidence": 0.85, "reason": "ok"},
            ],
            "needs_review": False,
            "confidence": 0.85,
        }
        resp = validate_vlm_response(payload)
        self.assertEqual(resp.page_type, VlmPageType.PROBLEM)
        self.assertEqual(len(resp.problems), 1)
        self.assertEqual(resp.problems[0].number, 1)

    def test_invalid_page_type_raises(self):
        with self.assertRaises(VlmSchemaError) as ctx:
            validate_vlm_response({"page_type": "INVALID", "problems": []})
        self.assertIn("page_type", str(ctx.exception))

    def test_problems_not_list_raises(self):
        with self.assertRaises(VlmSchemaError):
            validate_vlm_response({"page_type": "problem", "problems": "not_a_list"})

    def test_problem_number_out_of_range_raises(self):
        payload = {
            "page_type": "problem",
            "problems": [
                {"number": 999, "bbox": {"x": 0, "y": 0, "w": 1, "h": 1}, "confidence": 0.5},
            ],
        }
        with self.assertRaises(VlmSchemaError) as ctx:
            validate_vlm_response(payload)
        self.assertIn("out of range", str(ctx.exception))

    def test_problem_confidence_out_of_range_raises(self):
        payload = {
            "page_type": "problem",
            "problems": [
                {"number": 1, "bbox": {"x": 0, "y": 0, "w": 1, "h": 1}, "confidence": 1.5},
            ],
        }
        with self.assertRaises(VlmSchemaError):
            validate_vlm_response(payload)

    def test_bbox_missing_field_raises(self):
        payload = {
            "page_type": "problem",
            "problems": [{"number": 1, "bbox": {"x": 0, "y": 0}, "confidence": 0.5}],
        }
        with self.assertRaises(VlmSchemaError):
            validate_vlm_response(payload)

    def test_payload_not_dict_raises(self):
        with self.assertRaises(VlmSchemaError):
            validate_vlm_response("not_a_dict")  # type: ignore[arg-type]


class MockVlmClientTests(TestCase):
    def test_default_response_is_unknown_needs_review(self):
        client = MockVlmClient()
        request = VlmPageRequest(
            document_id=100, page_number=3, page_image_key="r2/page/3.png",
            paper_type_hint="unknown", tier0_anchor_count=0, tier1_required=True,
        )
        resp = client.detect_problems_for_page(request)
        self.assertEqual(resp.page_type, VlmPageType.UNKNOWN)
        self.assertTrue(resp.needs_review)
        self.assertEqual(resp.problems, [])

    def test_set_response_overrides(self):
        client = MockVlmClient()
        custom = VlmPageResponse(
            page_type=VlmPageType.PROBLEM,
            problems=[VlmProblemDetection(1, VlmBbox(0.1, 0.1, 0.5, 0.5), 0.9)],
        )
        client.set_response(page_number=5, response=custom)
        request = VlmPageRequest(
            document_id=100, page_number=5, page_image_key="r2/p/5.png",
            paper_type_hint="exam", tier0_anchor_count=0, tier1_required=False,
        )
        resp = client.detect_problems_for_page(request)
        self.assertEqual(resp, custom)

    def test_call_count_tracking(self):
        client = MockVlmClient()
        request = VlmPageRequest(
            document_id=1, page_number=1, page_image_key="x",
            paper_type_hint="unknown", tier0_anchor_count=0, tier1_required=False,
        )
        for _ in range(3):
            client.detect_problems_for_page(request)
        self.assertEqual(client.call_count, 3)
        self.assertEqual(len(client.call_log), 3)


class NoRealApiContractTests(TestCase):
    """vlm_mock_contract 모듈 자체가 실 API client / requests / google.cloud 등 import 안 함.

    실 호출 가능한 라이브러리 import 가 있으면 dispatcher 가 실수로 호출할 위험 — 차단.
    """

    def test_no_real_api_imports(self):
        from apps.domains.matchup.segmentation import vlm_mock_contract
        import inspect
        src = inspect.getsource(vlm_mock_contract)
        forbidden = (
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "google.cloud",
            "openai",
            "anthropic",
            "from anthropic",
            "google.generativeai",
        )
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"vlm_mock_contract 에 실 API client import 발견 — '{token}'",
            )

    def test_no_orm_imports(self):
        """실 ORM import / .objects. / .save() 호출 X.

        주석/docstring 안에 reference 로 언급된 클래스명 (예: ProblemSegmentationProposal)
        은 허용 — 실 호출 token 만 검사.
        """
        from apps.domains.matchup.segmentation import vlm_mock_contract
        import inspect
        src = inspect.getsource(vlm_mock_contract)
        # 코드 import / 호출 패턴만 검사 (주석 reference 는 허용)
        forbidden_patterns = (
            "from apps.domains.matchup.models import",
            "import MatchupProblem",
            "import ProblemSegmentationProposal",
            "MatchupProblem.objects",
            "ProblemSegmentationProposal.objects",
            ".save()",
            ".bulk_create(",
        )
        for token in forbidden_patterns:
            self.assertNotIn(token, src, f"vlm_mock_contract ORM 호출 '{token}' 발견")
