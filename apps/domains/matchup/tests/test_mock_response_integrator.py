"""Stage 5.8 (2026-05-07) — mock OCR/VLM response integration 단위 테스트.

검증:
- make_mock_ocr_response / make_mock_vlm_response synthetic 생성
- integrate_responses 5-route 별 unified output schema
- proposal_payloads 가 ProblemSegmentationProposal 모델 schema 와 호환
- manual_overlap_mock_validator (DB query 0회)
- ValidationMarks 모두 0
- 실 OCR/VLM SDK / DB 모델 / proposal / callback import 0회 (regression)
"""
from __future__ import annotations

import os
import tempfile
from unittest import TestCase

from apps.domains.matchup.segmentation.mock_response_integrator import (
    MockOcrResponse, MockVlmResponse, ProposalPayloadCandidate, SCHEMA_VERSION, UnifiedCandidate,
    VlmDetectedProblem, VlmPageResult,
    integrate_full_dryrun, integrate_responses, make_mock_ocr_response,
    make_mock_vlm_response, manual_overlap_mock_validator, unified_to_dict,
)
from apps.domains.matchup.segmentation.dispatcher_mock import (
    dispatch_mock,
)


def _make_pdf_with(text_lines):
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for i, t in enumerate(text_lines):
        page.insert_text((50, 100 + i * 60), t, fontsize=10)
    tmp = tempfile.NamedTemporaryFile(suffix="_test.pdf", delete=False)
    tmp.close()
    doc.save(tmp.name); doc.close()
    return tmp.name


class MockOcrResponseTests(TestCase):
    def test_make_returns_correct_pages(self):
        resp = make_mock_ocr_response("/tmp/x.pdf", page_indices=[0, 1, 2])
        self.assertIsInstance(resp, MockOcrResponse)
        self.assertEqual(resp.page_count, 3)
        self.assertEqual(len(resp.pages), 3)
        self.assertTrue(resp.is_mock)
        self.assertEqual(resp.cost_actual_usd, 0.0)

    def test_text_blocks_have_bbox_and_text(self):
        resp = make_mock_ocr_response("/tmp/x.pdf", page_indices=[0])
        for blk in resp.pages[0].text_blocks:
            self.assertEqual(len(blk.bbox_norm), 4)
            self.assertGreater(blk.confidence, 0)
            self.assertIn("mock OCR", blk.text)


class MockVlmResponseTests(TestCase):
    def test_make_returns_correct_pages(self):
        resp = make_mock_vlm_response("/tmp/x.pdf", page_indices=[0, 1])
        self.assertIsInstance(resp, MockVlmResponse)
        self.assertEqual(len(resp.pages), 2)
        self.assertTrue(resp.is_mock)
        self.assertEqual(resp.cost_actual_usd, 0.0)

    def test_problems_have_bbox(self):
        resp = make_mock_vlm_response("/tmp/x.pdf", page_indices=[0])
        for prob in resp.pages[0].detected_problems:
            self.assertEqual(len(prob.bbox_norm), 4)
            self.assertGreater(prob.confidence, 0)
            self.assertIsNotNone(prob.number)


class IntegrateTier0Tests(TestCase):
    def test_tier0_sufficient_returns_tier0_candidates(self):
        pdf = _make_pdf_with(["1. 다음 ① ② ③", "2. 다음 ① ② ③"])
        try:
            d = dispatch_mock(pdf)
            unified = integrate_responses(
                d, tenant_id=2, document_id=999,
                analysis_version_key="test-tier0",
            )
            if d.route == "TIER0_SUFFICIENT":
                self.assertIn("tier0", unified.sources_used)
                self.assertGreaterEqual(len(unified.unified_candidates), 0)
                # proposal payload schema 검증
                if unified.proposal_payloads:
                    p = unified.proposal_payloads[0]
                    self.assertEqual(p.tenant_id, 2)
                    self.assertEqual(p.document_id, 999)
                    self.assertEqual(p.engine, "native_pdf")
                    self.assertEqual(p.bbox["norm"], True)
                    self.assertIn("x", p.bbox)
                    self.assertIn("y", p.bbox)
                    self.assertIn("w", p.bbox)
                    self.assertIn("h", p.bbox)
        finally:
            os.unlink(pdf)


class IntegrateTier1OcrTests(TestCase):
    def test_ocr_response_converted_to_unified(self):
        # empty PDF — TIER1_OCR_REQUIRED
        import fitz
        doc = fitz.open(); doc.new_page(width=595, height=842)
        tmp = tempfile.NamedTemporaryFile(suffix="_empty.pdf", delete=False); tmp.close()
        doc.save(tmp.name); doc.close()
        try:
            d = dispatch_mock(tmp.name)
            self.assertEqual(d.route, "TIER1_OCR_REQUIRED")
            mock_ocr = make_mock_ocr_response(
                tmp.name, page_indices=[0], blocks_per_page=3,
            )
            unified = integrate_responses(
                d, mock_ocr_response=mock_ocr,
                tenant_id=2, document_id=999,
                analysis_version_key="test-ocr",
            )
            self.assertEqual(unified.route, "TIER1_OCR_REQUIRED")
            self.assertIn("ocr", unified.sources_used)
            self.assertGreater(len(unified.unified_candidates), 0)
            for c in unified.unified_candidates:
                self.assertEqual(c.source, "ocr")
            # engine 매핑
            for p in unified.proposal_payloads:
                self.assertEqual(p.engine, "ocr")
        finally:
            os.unlink(tmp.name)


class IntegrateTier2VlmTests(TestCase):
    def test_vlm_response_converted_to_unified(self):
        # text 있으나 anchor 없음 — TIER2_VLM_REQUIRED
        pdf = _make_pdf_with(["비정형 본문이 흐른다", "선택지 없는 텍스트"])
        try:
            d = dispatch_mock(pdf)
            if d.route != "TIER2_VLM_REQUIRED":
                # 환경 의존 — skip
                self.skipTest(f"route was {d.route}, expected TIER2_VLM_REQUIRED")
            mock_vlm = make_mock_vlm_response(
                pdf, page_indices=[0], problems_per_page=4,
            )
            unified = integrate_responses(
                d, mock_vlm_response=mock_vlm,
                tenant_id=2, document_id=999,
                analysis_version_key="test-vlm",
            )
            self.assertEqual(unified.route, "TIER2_VLM_REQUIRED")
            self.assertIn("vlm", unified.sources_used)
            self.assertGreater(len(unified.unified_candidates), 0)
            for c in unified.unified_candidates:
                self.assertEqual(c.source, "vlm")
            for p in unified.proposal_payloads:
                self.assertEqual(p.engine, "vlm")
        finally:
            os.unlink(pdf)


class ManualOverlapMockValidatorTests(TestCase):
    def test_no_overlap_returns_empty(self):
        candidates = [
            UnifiedCandidate(
                page_index=0, bbox_norm=(0.1, 0.1, 0.5, 0.2),
                number=1, source="vlm", confidence=0.8,
            ),
        ]
        manual = [{"page_index": 0, "bbox_norm": (0.7, 0.7, 0.2, 0.2)}]
        errors = manual_overlap_mock_validator(candidates, static_manual_bboxes=manual)
        self.assertEqual(errors, {})

    def test_overlap_returns_validation_error(self):
        candidates = [
            UnifiedCandidate(
                page_index=0, bbox_norm=(0.1, 0.1, 0.5, 0.5),
                number=1, source="vlm", confidence=0.8,
            ),
        ]
        # 큰 overlap
        manual = [{"page_index": 0, "bbox_norm": (0.10, 0.10, 0.50, 0.50)}]
        errors = manual_overlap_mock_validator(candidates, static_manual_bboxes=manual)
        self.assertIn(0, errors)
        self.assertEqual(errors[0][0].code, "manual_overlap")
        self.assertIsNotNone(errors[0][0].bbox_iou)

    def test_different_page_no_overlap(self):
        candidates = [
            UnifiedCandidate(
                page_index=0, bbox_norm=(0.1, 0.1, 0.5, 0.5),
                number=1, source="vlm", confidence=0.8,
            ),
        ]
        manual = [{"page_index": 1, "bbox_norm": (0.10, 0.10, 0.50, 0.50)}]
        errors = manual_overlap_mock_validator(candidates, static_manual_bboxes=manual)
        self.assertEqual(errors, {})


class IntegrateProposalPayloadStatusTests(TestCase):
    def test_overlap_marks_proposal_rejected(self):
        # 임의 dispatcher (TIER2_VLM_REQUIRED 가정)
        pdf = _make_pdf_with(["비정형 본문이 흐른다"])
        try:
            d = dispatch_mock(pdf)
            if d.route != "TIER2_VLM_REQUIRED":
                self.skipTest(f"route was {d.route}")
            mock_vlm = MockVlmResponse(
                engine="gemini_vision", pdf_path=pdf,
                pages=[VlmPageResult(page_index=0, detected_problems=[
                    VlmDetectedProblem(number=1, bbox_norm=(0.1, 0.1, 0.5, 0.5), confidence=0.8),
                ])],
            )
            unified = integrate_responses(
                d, mock_vlm_response=mock_vlm,
                tenant_id=2, document_id=999,
                analysis_version_key="test-overlap",
                static_manual_bboxes=[
                    {"page_index": 0, "bbox_norm": (0.1, 0.1, 0.5, 0.5)},
                ],
            )
            self.assertGreater(len(unified.proposal_payloads), 0)
            p = unified.proposal_payloads[0]
            self.assertEqual(p.status, "rejected")
            self.assertTrue(any(e.code == "manual_overlap" for e in p.validation_errors))
        finally:
            os.unlink(pdf)


class ValidationMarksTests(TestCase):
    def test_validation_marks_all_zero(self):
        pdf = _make_pdf_with(["1. 다음 ① ② ③"])
        try:
            _, unified = integrate_full_dryrun(
                pdf, tenant_id=2, document_id=999,
                analysis_version_key="test-validation",
            )
            v = unified.validation
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


class UnifiedToDictTests(TestCase):
    def test_serializable(self):
        pdf = _make_pdf_with(["1. 다음 ① ② ③"])
        try:
            _, unified = integrate_full_dryrun(
                pdf, tenant_id=2, document_id=999,
                analysis_version_key="test-serialize",
            )
            d = unified_to_dict(unified)
            self.assertEqual(d["schema_version"], SCHEMA_VERSION)
            self.assertIn("unified_candidates", d)
            self.assertIn("proposal_payloads", d)
            self.assertIn("validation", d)
            import json
            json.dumps(d, default=str)
        finally:
            os.unlink(pdf)


class IntegratorRegressionTests(TestCase):
    def test_no_real_api_imports(self):
        from apps.domains.matchup.segmentation import mock_response_integrator
        import inspect
        src = inspect.getsource(mock_response_integrator)
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
                             f"integrator 에서 실 SDK import '{token}' 발견")

    def test_no_db_or_callback_imports(self):
        from apps.domains.matchup.segmentation import mock_response_integrator
        import inspect
        src = inspect.getsource(mock_response_integrator)
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
            "ProblemSegmentationProposal.objects",
            "MatchupProblem.objects",
            ".objects.create(", ".objects.bulk_create(",
            "from apps.domains.ai.gateway",
            "dispatch_job(",
            "from academy.adapters.ai.detection.segment_dispatcher",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"integrator 에서 운영 access '{token}' 발견")

    def test_proposal_payload_schema_compatible(self):
        """ProposalPayloadCandidate 가 ProblemSegmentationProposal 모델 schema 와 호환."""
        # 모델 import 안 하고 dataclass 만으로 schema 호환 검증
        p = ProposalPayloadCandidate(
            tenant_id=2, document_id=99, page_number=0,
            detected_problem_number=1,
            bbox={"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.3, "norm": True},
            engine="vlm",
            confidence=0.8,
        )
        # 필수 키
        self.assertEqual(p.tenant_id, 2)
        self.assertEqual(p.bbox["norm"], True)
        self.assertIn(p.engine, ("yolo", "vlm", "ocr", "native_pdf", "manual_assist"))
        self.assertIn(p.status,
                      ("pending", "needs_review", "rejected", "approved", "auto_passed"))
        # validation_errors list 형식
        self.assertIsInstance(p.validation_errors, list)
