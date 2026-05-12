"""Stage 6.3F-3 (2026-05-07) — VLM schema normalizer unit tests.

검증:
- normalize_pixel_xywh_to_norm: 픽셀 (x,y,w,h) → norm (좌표계만)
- malformed (page_w/h 0 / 음수 / 잘못된 값) raise
- real_vlm_problem_to_mock: duck-type input → VlmDetectedProblem
- number=0 (운영 unknown) → None 매핑
- confidence float 보존 (OCR 와 달리 운영 surface)
- shared_with / page_role / paper_type 보존 (debug 메타)
- 운영 vlm_fallback 직접 import 0회 (regression)
- credential / signed URL / SDK module-level import 0회
"""
from __future__ import annotations

from dataclasses import dataclass, field
from unittest import TestCase

from academy.application.use_cases.ai.segmentation.mock_response_integrator import (
    MockVlmResponse, UnifiedCandidate, VlmDetectedProblem,
)
from academy.adapters.ai.vlm.schema_normalizer import (
    SCHEMA_VERSION,
    normalize_pixel_xywh_to_norm,
    real_vlm_problem_to_mock,
    real_vlm_problems_to_unified_candidates,
    real_vlm_result_to_mock_response,
    real_vlm_result_to_unified_candidates,
)


# ── duck-type fixtures (운영 dataclass 미import) ───────────────


@dataclass
class _FakeProblemBbox:
    """duck-type — academy.adapters.ai.detection.vlm_fallback.ProblemBbox 미import."""
    number: int
    bbox: tuple                         # (x, y, w, h) 픽셀
    confidence: float
    shared_with: list = field(default_factory=list)


@dataclass
class _FakeProblemBboxResult:
    """duck-type — ProblemBboxResult 미import."""
    page_role: str
    should_skip: bool
    problems: list
    confidence: float
    paper_type: str = "unknown"
    debug: dict = field(default_factory=dict)


# ── normalize_pixel_xywh_to_norm ───────────────────────────────


class NormalizePixelXywhTests(TestCase):
    def test_simple_conversion(self):
        # page 1000×2000 / pixel (100, 200, 500, 600) → (0.1, 0.1, 0.5, 0.3)
        result = normalize_pixel_xywh_to_norm(
            100, 200, 500, 600, page_width=1000, page_height=2000,
        )
        self.assertAlmostEqual(result[0], 0.1)
        self.assertAlmostEqual(result[1], 0.1)
        self.assertAlmostEqual(result[2], 0.5)
        self.assertAlmostEqual(result[3], 0.3)

    def test_zero_origin(self):
        result = normalize_pixel_xywh_to_norm(
            0, 0, 100, 200, page_width=1000, page_height=2000,
        )
        self.assertEqual(result, (0.0, 0.0, 0.1, 0.1))

    def test_full_page(self):
        result = normalize_pixel_xywh_to_norm(
            0, 0, 1000, 2000, page_width=1000, page_height=2000,
        )
        self.assertEqual(result, (0.0, 0.0, 1.0, 1.0))

    def test_zero_page_width_raises(self):
        with self.assertRaises(ValueError):
            normalize_pixel_xywh_to_norm(
                0, 0, 100, 100, page_width=0, page_height=2000,
            )

    def test_negative_page_height_raises(self):
        with self.assertRaises(ValueError):
            normalize_pixel_xywh_to_norm(
                0, 0, 100, 100, page_width=1000, page_height=-1,
            )

    def test_negative_w_or_h_raises(self):
        with self.assertRaises(ValueError):
            normalize_pixel_xywh_to_norm(
                0, 0, -100, 100, page_width=1000, page_height=1000,
            )
        with self.assertRaises(ValueError):
            normalize_pixel_xywh_to_norm(
                0, 0, 100, -100, page_width=1000, page_height=1000,
            )


# ── real_vlm_problem_to_mock ───────────────────────────────────


class RealProblemToMockTests(TestCase):
    def test_basic_conversion(self):
        real = _FakeProblemBbox(
            number=5, bbox=(100, 200, 500, 600), confidence=0.85,
        )
        result = real_vlm_problem_to_mock(
            real, page_width=1000, page_height=2000,
        )
        self.assertIsInstance(result, VlmDetectedProblem)
        self.assertEqual(result.number, 5)
        self.assertAlmostEqual(result.bbox_norm[0], 0.1)
        self.assertAlmostEqual(result.bbox_norm[1], 0.1)
        self.assertAlmostEqual(result.bbox_norm[2], 0.5)
        self.assertAlmostEqual(result.bbox_norm[3], 0.3)
        # 운영 VLM confidence 보존 (OCR 와 달리)
        self.assertEqual(result.confidence, 0.85)

    def test_number_zero_maps_to_none(self):
        """운영 ProblemBbox.number=0 (unknown) → mock VlmDetectedProblem.number=None."""
        real = _FakeProblemBbox(number=0, bbox=(0, 0, 100, 100), confidence=0.5)
        result = real_vlm_problem_to_mock(
            real, page_width=1000, page_height=1000,
        )
        self.assertIsNone(result.number)

    def test_normal_number_preserved(self):
        for n in (1, 5, 42, 99):
            real = _FakeProblemBbox(number=n, bbox=(0, 0, 100, 100), confidence=0.5)
            result = real_vlm_problem_to_mock(
                real, page_width=1000, page_height=1000,
            )
            self.assertEqual(result.number, n)

    def test_invalid_bbox_raises(self):
        # bbox 가 list/tuple 아님
        @dataclass
        class _Bad: number: int = 1; bbox: str = "not_tuple"; confidence: float = 0.5
        with self.assertRaises(ValueError):
            real_vlm_problem_to_mock(_Bad(), page_width=1000, page_height=1000)

    def test_bbox_3_elements_raises(self):
        @dataclass
        class _Bad: number: int = 1; bbox: tuple = (0, 0, 100); confidence: float = 0.5
        with self.assertRaises(ValueError):
            real_vlm_problem_to_mock(_Bad(), page_width=1000, page_height=1000)

    def test_missing_confidence_falls_to_zero(self):
        @dataclass
        class _NoConf: number: int = 1; bbox: tuple = (0, 0, 100, 100)
        result = real_vlm_problem_to_mock(
            _NoConf(), page_width=1000, page_height=1000,
        )
        # 운영 confidence 없으면 0.0 (semantic: 미상)
        self.assertEqual(result.confidence, 0.0)


# ── real_vlm_result_to_mock_response ──────────────────────────


class RealResultToMockResponseTests(TestCase):
    def test_page_index_external_injection(self):
        problems = [
            _FakeProblemBbox(number=1, bbox=(0, 0, 100, 100), confidence=0.8),
            _FakeProblemBbox(number=2, bbox=(0, 200, 100, 100), confidence=0.9),
        ]
        result = _FakeProblemBboxResult(
            page_role="problem", should_skip=False, problems=problems,
            confidence=0.85, paper_type="clean_pdf_single",
        )
        response = real_vlm_result_to_mock_response(
            result, page_index=7,
            page_width=1000, page_height=2000,
        )
        self.assertIsInstance(response, MockVlmResponse)
        self.assertEqual(len(response.pages), 1)
        self.assertEqual(response.pages[0].page_index, 7)
        self.assertEqual(len(response.pages[0].detected_problems), 2)

    def test_is_mock_false_for_real_conversion(self):
        result = _FakeProblemBboxResult(
            page_role="problem", should_skip=False, problems=[], confidence=0.5,
        )
        response = real_vlm_result_to_mock_response(
            result, page_index=0, page_width=1000, page_height=1000,
        )
        self.assertFalse(response.is_mock)
        self.assertEqual(response.cost_actual_usd, 0.0)

    def test_engine_default_gemini(self):
        result = _FakeProblemBboxResult(
            page_role="problem", should_skip=False, problems=[], confidence=0.5,
        )
        response = real_vlm_result_to_mock_response(
            result, page_index=0, page_width=1000, page_height=1000,
        )
        self.assertEqual(response.engine, "gemini_vision")

    def test_page_meta_preserved_in_real_page_meta(self):
        problems = [
            _FakeProblemBbox(number=1, bbox=(0, 0, 100, 100), confidence=0.8,
                             shared_with=[2, 3]),
        ]
        result = _FakeProblemBboxResult(
            page_role="problem", should_skip=False, problems=problems,
            confidence=0.85, paper_type="quadrant",
            debug={"vlm_engine_actual": "gemini-2.5"},
        )
        response = real_vlm_result_to_mock_response(
            result, page_index=0, page_width=1000, page_height=1000,
        )
        page_meta = getattr(response, "real_page_meta", None)
        self.assertIsNotNone(page_meta)
        self.assertEqual(page_meta["page_role"], "problem")
        self.assertEqual(page_meta["paper_type"], "quadrant")
        self.assertEqual(page_meta["page_confidence"], 0.85)
        # shared_with per-problem
        self.assertEqual(page_meta["shared_with_per_problem"], [[2, 3]])
        # 운영 debug mirror
        self.assertEqual(page_meta["real_debug"]["vlm_engine_actual"], "gemini-2.5")


# ── real_vlm_result_to_unified_candidates ──────────────────────


class RealResultToUnifiedTests(TestCase):
    def test_unified_source_vlm(self):
        problems = [
            _FakeProblemBbox(number=1, bbox=(0, 0, 100, 100), confidence=0.8),
        ]
        result = _FakeProblemBboxResult(
            page_role="problem", should_skip=False, problems=problems,
            confidence=0.9, paper_type="clean_pdf_single",
        )
        candidates = real_vlm_result_to_unified_candidates(
            result, page_index=3,
            page_width=1000, page_height=1000,
        )
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertIsInstance(c, UnifiedCandidate)
        self.assertEqual(c.source, "vlm")
        self.assertEqual(c.page_index, 3)
        self.assertEqual(c.number, 1)
        self.assertEqual(c.confidence, 0.8)

    def test_page_meta_promoted_to_candidate_debug(self):
        problems = [
            _FakeProblemBbox(number=1, bbox=(0, 0, 100, 100), confidence=0.8,
                             shared_with=[2]),
        ]
        result = _FakeProblemBboxResult(
            page_role="problem", should_skip=False, problems=problems,
            confidence=0.9, paper_type="dual_column",
        )
        candidates = real_vlm_result_to_unified_candidates(
            result, page_index=0, page_width=1000, page_height=1000,
        )
        c = candidates[0]
        self.assertEqual(c.debug.get("page_role"), "problem")
        self.assertEqual(c.debug.get("paper_type"), "dual_column")
        self.assertEqual(c.debug.get("shared_with"), [2])
        self.assertFalse(c.debug.get("should_skip"))

    def test_number_zero_propagates_to_unified_none(self):
        """운영 number=0 → mock number=None → UnifiedCandidate.number=None."""
        problems = [_FakeProblemBbox(number=0, bbox=(0, 0, 100, 100), confidence=0.5)]
        result = _FakeProblemBboxResult(
            page_role="problem", should_skip=False, problems=problems, confidence=0.5,
        )
        candidates = real_vlm_result_to_unified_candidates(
            result, page_index=0, page_width=1000, page_height=1000,
        )
        self.assertIsNone(candidates[0].number)


class RealProblemsLightApiTests(TestCase):
    def test_problems_only_no_page_meta(self):
        """ProblemBbox list 만 받는 lighter API."""
        problems = [
            _FakeProblemBbox(number=1, bbox=(0, 0, 100, 100), confidence=0.8),
            _FakeProblemBbox(number=2, bbox=(0, 200, 100, 100), confidence=0.85),
        ]
        candidates = real_vlm_problems_to_unified_candidates(
            problems, page_index=5,
            page_width=1000, page_height=1000,
        )
        self.assertEqual(len(candidates), 2)
        for c in candidates:
            self.assertEqual(c.source, "vlm")
            self.assertEqual(c.page_index, 5)


# ── regression ─────────────────────────────────────────────────


class NormalizerRegressionTests(TestCase):
    def test_no_vlm_sdk_imports(self):
        from academy.adapters.ai.vlm import schema_normalizer as vlm_schema_normalizer
        import inspect
        src = inspect.getsource(vlm_schema_normalizer)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "import google.generativeai", "from google.generativeai",
            "import google.cloud", "from google.cloud",
            "import openai", "from openai",
            "import anthropic", "from anthropic",
            "import requests", "from requests",
        )
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"vlm_schema_normalizer 에서 실 SDK import '{token}' 발견",
            )

    def test_no_operating_vlm_helper_imports(self):
        from academy.adapters.ai.vlm import schema_normalizer as vlm_schema_normalizer
        import inspect
        src = inspect.getsource(vlm_schema_normalizer)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "from academy.adapters.ai.detection.vlm_fallback",
            "import vlm_fallback", "ProblemBboxResult.objects",
            "VLMVisionAdapter(", "GeminiVLMVisionAdapter(",
            "from academy.adapters.ai.detection.segment_dispatcher",
        )
        for token in forbidden:
            self.assertNotIn(
                token, src,
                f"vlm_schema_normalizer 에서 운영 VLM helper '{token}' 발견",
            )

    def test_schema_version_set(self):
        self.assertEqual(SCHEMA_VERSION, "6.3F-3-vlm-normalizer-1")
