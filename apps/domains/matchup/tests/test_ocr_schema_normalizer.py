"""Stage 6.3F-2 (2026-05-07) — OCR schema normalizer unit tests.

검증:
- normalize_pixel_corner_to_norm_xywh: pixel → normalized 변환
- malformed bbox / 0 page_width / corner 순서 위반 raise
- real_ocr_block_to_mock_block: duck-type input → OcrTextBlock
- confidence None default + 명시 주입
- real_ocr_blocks_to_mock_response: page_index 외부 주입 / is_mock=False 마킹
- real_ocr_blocks_to_unified_candidates: UnifiedCandidate.source='ocr' / confidence None 보존 (A안)
- 운영 google_ocr / segment_dispatcher / proposal_helpers / DB 모델 / SDK import 0회
- mock_response_integrator OcrTextBlock.confidence Optional 변경 회귀 (default None)
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest import TestCase

from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
    MockOcrResponse, OcrPageResult, OcrTextBlock, UnifiedCandidate,
)
from academy.adapters.ai.ocr.schema_normalizer import (
    SCHEMA_VERSION,
    normalize_pixel_corner_to_norm_xywh,
    real_ocr_block_to_mock_block,
    real_ocr_blocks_to_mock_response,
    real_ocr_blocks_to_unified_candidates,
)


@dataclass
class _FakeRealOcrBlock:
    """duck-type — 운영 academy.adapters.ai.ocr.google.OCRTextBlock 와 동일 형식.

    운영 dataclass 직접 import 안 함 — 본 normalizer 는 duck-typed.
    """
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


# ── normalize_pixel_corner_to_norm_xywh ──────────────────────────


class NormalizePixelCornerTests(TestCase):
    def test_simple_conversion(self):
        # page 1000×2000 / corner (100, 200, 600, 800) → (0.1, 0.1, 0.5, 0.3)
        result = normalize_pixel_corner_to_norm_xywh(
            100, 200, 600, 800, page_width=1000, page_height=2000,
        )
        self.assertAlmostEqual(result[0], 0.1)
        self.assertAlmostEqual(result[1], 0.1)
        self.assertAlmostEqual(result[2], 0.5)
        self.assertAlmostEqual(result[3], 0.3)

    def test_zero_origin_corner(self):
        result = normalize_pixel_corner_to_norm_xywh(
            0, 0, 100, 200, page_width=1000, page_height=2000,
        )
        self.assertEqual(result, (0.0, 0.0, 0.1, 0.1))

    def test_full_page_corner(self):
        result = normalize_pixel_corner_to_norm_xywh(
            0, 0, 1000, 2000, page_width=1000, page_height=2000,
        )
        self.assertEqual(result, (0.0, 0.0, 1.0, 1.0))

    def test_zero_page_width_raises(self):
        with self.assertRaises(ValueError):
            normalize_pixel_corner_to_norm_xywh(
                100, 200, 600, 800, page_width=0, page_height=2000,
            )

    def test_zero_page_height_raises(self):
        with self.assertRaises(ValueError):
            normalize_pixel_corner_to_norm_xywh(
                100, 200, 600, 800, page_width=1000, page_height=0,
            )

    def test_negative_page_width_raises(self):
        with self.assertRaises(ValueError):
            normalize_pixel_corner_to_norm_xywh(
                100, 200, 600, 800, page_width=-1000, page_height=2000,
            )

    def test_corner_order_violation_raises(self):
        # x1 < x0
        with self.assertRaises(ValueError):
            normalize_pixel_corner_to_norm_xywh(
                600, 200, 100, 800, page_width=1000, page_height=2000,
            )
        # y1 < y0
        with self.assertRaises(ValueError):
            normalize_pixel_corner_to_norm_xywh(
                100, 800, 600, 200, page_width=1000, page_height=2000,
            )


# ── real_ocr_block_to_mock_block ────────────────────────────────


class RealBlockToMockBlockTests(TestCase):
    def test_basic_conversion_no_confidence(self):
        real = _FakeRealOcrBlock(
            text="1. Sample question",
            x0=100, y0=200, x1=600, y1=800,
        )
        result = real_ocr_block_to_mock_block(
            real, page_width=1000, page_height=2000,
        )
        self.assertIsInstance(result, OcrTextBlock)
        self.assertEqual(result.text, "1. Sample question")
        self.assertIsNone(result.confidence)
        self.assertAlmostEqual(result.bbox_norm[0], 0.1)
        self.assertAlmostEqual(result.bbox_norm[1], 0.1)
        self.assertAlmostEqual(result.bbox_norm[2], 0.5)
        self.assertAlmostEqual(result.bbox_norm[3], 0.3)

    def test_explicit_confidence(self):
        real = _FakeRealOcrBlock(text="x", x0=0, y0=0, x1=100, y1=100)
        result = real_ocr_block_to_mock_block(
            real, page_width=1000, page_height=1000, confidence=0.92,
        )
        self.assertEqual(result.confidence, 0.92)

    def test_empty_text_handled(self):
        real = _FakeRealOcrBlock(text="", x0=0, y0=0, x1=100, y1=100)
        result = real_ocr_block_to_mock_block(
            real, page_width=1000, page_height=1000,
        )
        self.assertEqual(result.text, "")

    def test_none_text_handled(self):
        # text=None 인 duck-type
        @dataclass
        class _NullText:
            text: type(None) = None
            x0: float = 0; y0: float = 0; x1: float = 100; y1: float = 100
        result = real_ocr_block_to_mock_block(
            _NullText(), page_width=1000, page_height=1000,
        )
        self.assertEqual(result.text, "")

    def test_missing_attribute_raises(self):
        @dataclass
        class _Incomplete:
            text: str = "x"
            # x0 / y0 / x1 / y1 missing
        with self.assertRaises(AttributeError):
            real_ocr_block_to_mock_block(
                _Incomplete(), page_width=1000, page_height=1000,
            )


# ── real_ocr_blocks_to_mock_response ───────────────────────────


class RealBlocksToMockResponseTests(TestCase):
    def test_page_index_external_injection(self):
        blocks = [
            _FakeRealOcrBlock(text="1. a", x0=10, y0=20, x1=110, y1=80),
            _FakeRealOcrBlock(text="2. b", x0=10, y0=200, x1=110, y1=260),
        ]
        result = real_ocr_blocks_to_mock_response(
            blocks, page_index=5,
            page_width=1000, page_height=2000,
        )
        self.assertIsInstance(result, MockOcrResponse)
        self.assertEqual(len(result.pages), 1)
        self.assertEqual(result.pages[0].page_index, 5)
        self.assertEqual(len(result.pages[0].text_blocks), 2)

    def test_is_mock_false_for_real_conversion(self):
        result = real_ocr_blocks_to_mock_response(
            [], page_index=0, page_width=1000, page_height=1000,
        )
        self.assertFalse(result.is_mock)
        self.assertEqual(result.cost_actual_usd, 0.0)

    def test_engine_label_default_and_override(self):
        # default
        r1 = real_ocr_blocks_to_mock_response(
            [], page_index=0, page_width=1000, page_height=1000,
        )
        self.assertEqual(r1.engine, "google_cloud_vision")
        # override
        r2 = real_ocr_blocks_to_mock_response(
            [], page_index=0, page_width=1000, page_height=1000,
            engine="tesseract",
        )
        self.assertEqual(r2.engine, "tesseract")

    def test_confidences_per_block(self):
        blocks = [
            _FakeRealOcrBlock(text="x", x0=0, y0=0, x1=100, y1=100),
            _FakeRealOcrBlock(text="y", x0=0, y0=200, x1=100, y1=300),
        ]
        result = real_ocr_blocks_to_mock_response(
            blocks, page_index=0,
            page_width=1000, page_height=1000,
            confidences=[0.95, None],
        )
        self.assertEqual(result.pages[0].text_blocks[0].confidence, 0.95)
        self.assertIsNone(result.pages[0].text_blocks[1].confidence)

    def test_confidences_length_mismatch_raises(self):
        blocks = [_FakeRealOcrBlock(text="x", x0=0, y0=0, x1=100, y1=100)]
        with self.assertRaises(ValueError):
            real_ocr_blocks_to_mock_response(
                blocks, page_index=0,
                page_width=1000, page_height=1000,
                confidences=[0.9, 0.8],
            )

    def test_pdf_path_metadata_only(self):
        result = real_ocr_blocks_to_mock_response(
            [], page_index=0, page_width=1000, page_height=1000,
            pdf_path="/sandbox/dummy.pdf",
        )
        self.assertEqual(result.pdf_path, "/sandbox/dummy.pdf")


# ── real_ocr_blocks_to_unified_candidates ──────────────────────


class RealBlocksToUnifiedTests(TestCase):
    def test_unified_source_ocr(self):
        blocks = [_FakeRealOcrBlock(text="1. q", x0=10, y0=20, x1=110, y1=80)]
        result = real_ocr_blocks_to_unified_candidates(
            blocks, page_index=3,
            page_width=1000, page_height=2000,
        )
        self.assertEqual(len(result), 1)
        c = result[0]
        self.assertIsInstance(c, UnifiedCandidate)
        self.assertEqual(c.source, "ocr")
        self.assertEqual(c.page_index, 3)
        self.assertEqual(c.number, 1)   # "1. q" → number=1

    def test_confidence_none_preserved_at_unified_level(self):
        """A안 — UnifiedCandidate.confidence None 그대로 보존. 0.0 fallback 안 함.

        '신뢰도 정보 없음' 의미를 '낮은 신뢰도' 와 구분. 0.0 으로의 변환은
        ProposalPayloadCandidate (DB 호환) 시점만 적용.
        """
        blocks = [_FakeRealOcrBlock(text="2. q", x0=0, y0=0, x1=100, y1=100)]
        result = real_ocr_blocks_to_unified_candidates(
            blocks, page_index=0,
            page_width=1000, page_height=1000,
            confidences=None,
        )
        # confidence=None 보존 (의미: 정보 없음)
        self.assertIsNone(result[0].confidence)
        # debug 에서 raw 미존재 마킹
        self.assertFalse(result[0].debug.get("confidence_raw_present"))

    def test_confidence_explicit_preserved(self):
        blocks = [_FakeRealOcrBlock(text="3. q", x0=0, y0=0, x1=100, y1=100)]
        result = real_ocr_blocks_to_unified_candidates(
            blocks, page_index=0,
            page_width=1000, page_height=1000,
            confidences=[0.88],
        )
        self.assertEqual(result[0].confidence, 0.88)
        self.assertTrue(result[0].debug.get("confidence_raw_present"))

    def test_bbox_norm_shape(self):
        blocks = [_FakeRealOcrBlock(text="x", x0=100, y0=200, x1=600, y1=800)]
        result = real_ocr_blocks_to_unified_candidates(
            blocks, page_index=0,
            page_width=1000, page_height=2000,
        )
        bn = result[0].bbox_norm
        self.assertEqual(len(bn), 4)
        self.assertAlmostEqual(bn[0], 0.1)
        self.assertAlmostEqual(bn[1], 0.1)
        self.assertAlmostEqual(bn[2], 0.5)
        self.assertAlmostEqual(bn[3], 0.3)


# ── OcrTextBlock confidence Optional default 회귀 ──────────────


class OcrTextBlockOptionalConfidenceTests(TestCase):
    def test_default_confidence_is_none(self):
        blk = OcrTextBlock(bbox_norm=(0.1, 0.1, 0.5, 0.3), text="x")
        self.assertIsNone(blk.confidence)

    def test_explicit_confidence_preserved(self):
        blk = OcrTextBlock(bbox_norm=(0, 0, 1, 1), text="x", confidence=0.85)
        self.assertEqual(blk.confidence, 0.85)

    def test_none_confidence_preserved_in_unified(self):
        """A안 — OcrTextBlock confidence=None → UnifiedCandidate.confidence=None 보존.

        '신뢰도 정보 없음' 과 '낮은 신뢰도(0.0)' 의미 분리.
        """
        from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
            _ocr_response_to_unified,
        )
        resp = MockOcrResponse(
            engine="google_cloud_vision", pdf_path="", page_count=1,
            pages=[OcrPageResult(page_index=0, text_blocks=[
                OcrTextBlock(bbox_norm=(0, 0, 1, 1), text="1. x"),  # confidence=None default
            ])],
        )
        unified = _ocr_response_to_unified(resp)
        # A안 — None 보존
        self.assertIsNone(unified[0].confidence)
        self.assertFalse(unified[0].debug.get("confidence_raw_present"))


# ── A안 — _to_proposal_payload confidence None → 0.0 fallback + 마킹 ─────


class ProposalPayloadConfidenceMarkingTests(TestCase):
    """A안 + B-style 마킹 — UnifiedCandidate.confidence None 일 때
    ProposalPayloadCandidate.confidence=0.0 으로 변환 + raw_response 에 audit 마킹.
    """

    def test_none_unified_confidence_falls_to_zero_with_marking(self):
        from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
            UnifiedCandidate, _to_proposal_payload,
        )
        c = UnifiedCandidate(
            page_index=0, bbox_norm=(0.1, 0.1, 0.5, 0.3),
            number=1, source="ocr", confidence=None,
            debug={"text_preview": "1. x", "confidence_raw_present": False},
        )
        payload = _to_proposal_payload(
            c, tenant_id=1, document_id=99,
            analysis_version_key="stage6.3F-2-test",
        )
        # DB FloatField 호환 — 0.0
        self.assertEqual(payload.confidence, 0.0)
        # raw_response 마킹 — audit / debug
        self.assertTrue(payload.raw_response.get("confidence_missing"))
        self.assertEqual(
            payload.raw_response.get("confidence_strategy"),
            "missing_to_zero_for_db_compat",
        )
        self.assertIsNone(payload.raw_response.get("source_confidence"))
        # ranking 해석 TODO 마킹 보존
        self.assertIn("TODO_ranking_interpretation", payload.raw_response)

    def test_explicit_confidence_no_missing_marking(self):
        from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
            UnifiedCandidate, _to_proposal_payload,
        )
        c = UnifiedCandidate(
            page_index=0, bbox_norm=(0.1, 0.1, 0.5, 0.3),
            number=1, source="ocr", confidence=0.85,
            debug={"text_preview": "1. x"},
        )
        payload = _to_proposal_payload(
            c, tenant_id=1, document_id=99,
            analysis_version_key="stage6.3F-2-test",
        )
        self.assertEqual(payload.confidence, 0.85)
        self.assertFalse(payload.raw_response.get("confidence_missing"))
        self.assertEqual(payload.raw_response.get("source_confidence"), 0.85)

    def test_zero_confidence_distinct_from_none(self):
        """confidence=0.0 (명시 낮음) 은 confidence=None (정보 없음) 과 다름."""
        from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
            UnifiedCandidate, _to_proposal_payload,
        )
        c_zero = UnifiedCandidate(
            page_index=0, bbox_norm=(0.1, 0.1, 0.5, 0.3),
            number=1, source="ocr", confidence=0.0,
            debug={},
        )
        payload_zero = _to_proposal_payload(
            c_zero, tenant_id=1, document_id=99, analysis_version_key="t",
        )
        # confidence=0.0 명시 → confidence_missing=False
        self.assertEqual(payload_zero.confidence, 0.0)
        self.assertFalse(payload_zero.raw_response.get("confidence_missing"))
        self.assertEqual(payload_zero.raw_response.get("source_confidence"), 0.0)

    def test_validator_accepts_zero_confidence_payload(self):
        """ProposalPayloadCandidate.confidence=0.0 은 validator 통과 (DB compat)."""
        from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
            UnifiedCandidate, _to_proposal_payload,
        )
        from academy.application.use_cases.ai.segmentation.proposal_payload_validator import (
            validate_payload,
        )
        c = UnifiedCandidate(
            page_index=0, bbox_norm=(0.1, 0.1, 0.5, 0.3),
            number=1, source="ocr", confidence=None,
            debug={},
        )
        payload = _to_proposal_payload(
            c, tenant_id=1, document_id=99, analysis_version_key="t",
        )
        result = validate_payload(payload)
        self.assertTrue(result.schema_ok)
        self.assertTrue(result.field_ok)


# ── 통합 호환성 ──────────────────────────────────────────


class IntegrationCompatibilityTests(TestCase):
    def test_synthetic_and_real_produce_same_unified_schema(self):
        """synthetic mock generator 와 real → mock 변환이 동일 unified schema 출력."""
        from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
            make_mock_ocr_response, _ocr_response_to_unified,
        )
        # synthetic
        synth = make_mock_ocr_response("/tmp/x.pdf", page_indices=[0], blocks_per_page=1)
        synth_unified = _ocr_response_to_unified(synth)
        # real-equivalent
        real_blocks = [_FakeRealOcrBlock(text="1. mock OCR text block",
                                         x0=10, y0=20, x1=80, y1=80)]
        real_unified = real_ocr_blocks_to_unified_candidates(
            real_blocks, page_index=0,
            page_width=100, page_height=100,
            confidences=[0.85],
        )
        # 둘 다 UnifiedCandidate, source='ocr', number=1
        self.assertEqual(synth_unified[0].source, real_unified[0].source)
        self.assertEqual(synth_unified[0].number, real_unified[0].number)
        # 같은 dataclass 형식
        self.assertIsInstance(real_unified[0], type(synth_unified[0]))


# ── regression ──────────────────────────────────────────


class NormalizerRegressionTests(TestCase):
    def test_no_real_api_imports(self):
        from academy.adapters.ai.ocr import schema_normalizer as ocr_schema_normalizer
        import inspect
        src = inspect.getsource(ocr_schema_normalizer)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "import requests", "from requests",
            "import google.generativeai", "from google.generativeai",
            "import google.cloud", "from google.cloud",
            "import openai", "from openai",
            "import anthropic", "from anthropic",
            "import pytesseract", "from pytesseract",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"normalizer 에서 실 SDK import '{token}' 발견")

    def test_no_operating_helper_imports(self):
        from academy.adapters.ai.ocr import schema_normalizer as ocr_schema_normalizer
        import inspect
        src = inspect.getsource(ocr_schema_normalizer)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        # 운영 OCRTextBlock / google_ocr / segment_dispatcher / proposal_helpers /
        # DB 모델 직접 import 0회
        forbidden = (
            "from academy.adapters.ai.ocr",
            "import google_ocr_blocks", "import google_ocr",
            "from academy.adapters.ai.detection.segment_dispatcher",
            "from apps.domains.matchup.proposal_helpers",
            "from apps.domains.matchup.models",
            "from apps.domains.ai.gateway",
            "from apps.domains.ai.callbacks",
            ".objects.create(", ".objects.bulk_create(",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"normalizer 에서 운영 import '{token}' 발견")

    def test_schema_version_set(self):
        self.assertEqual(SCHEMA_VERSION, "6.3F-2-ocr-normalizer-1")
