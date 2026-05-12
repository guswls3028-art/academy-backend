"""Stage 6.3P-1 (2026-05-07) — preprocessing_input_contract 단위 테스트.

검증:
- raw_page_image 보존 (frozen — overwrite 차단)
- 5단 stage 입력 분리 + 클래스 식별
- stage 별 허용 transform 매트릭스 (binary 가 embedding/VLM 에서 reject 등)
- bbox-changing transform 시 transform_metadata + inverse_supported 강제
- source_type 분기:
  - native PDF + 충분 density: camera preprocessing 금지
  - student_exam_photo / scanned_pdf / image_only_page: 후보 허용
  - school_exam_pdf 가 native + density 낮으면 image fallback
  - explanation / answer_key / commercial_workbook: skip
- page_level_fallback 정책: success 아님 + direct_hit_label 차단
- 운영 wiring guard: Stage 6.3P-1 phase 에선 항상 raise
- 운영 무거운 dependency import 회귀 (segment_dispatcher / google ocr / VLM SDK)
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import FrozenInstanceError
from unittest import TestCase

from academy.domain.tools.preprocessing.contract import (
    DIRECT_HIT_LABEL_ALLOWED_FOR_FALLBACK,
    PAGE_LEVEL_FALLBACK_IS_SUCCESS,
    TEXT_DENSITY_THRESHOLD,
    DetectInputImage,
    EmbeddingInputImage,
    InvalidPreprocessingTransform,
    MatchupQuality,
    MissingTransformMetadata,
    OcrInputImage,
    PreprocessTransformMetadata,
    PreprocessingDecision,
    PreprocessingStage,
    RawPageInput,
    SegmentationStatus,
    SourceType,
    TransformKind,
    UnsafeTransformWiring,
    VlmInputImage,
    assert_no_operational_wiring,
    decide_preprocessing,
)


def _raw() -> RawPageInput:
    return RawPageInput(
        image_key="r2://tenant1/doc1/page0.png",
        width=1653,
        height=2337,
        page_index=0,
        pdf_dpi=200,
    )


# ── raw 보존 ──────────────────────────────────────────────────────────────

class TestRawImmutability(TestCase):
    def test_raw_is_frozen(self):
        raw = _raw()
        with self.assertRaises(FrozenInstanceError):
            raw.image_key = "another"  # type: ignore[misc]

    def test_raw_holds_dimensions_and_pdf_dpi(self):
        raw = _raw()
        self.assertEqual(raw.width, 1653)
        self.assertEqual(raw.height, 2337)
        self.assertEqual(raw.pdf_dpi, 200)
        self.assertEqual(raw.page_index, 0)


# ── 5단 입력 분리 ─────────────────────────────────────────────────────────

class TestStageInputSeparation(TestCase):
    def test_distinct_classes(self):
        raw = _raw()
        d = DetectInputImage(raw=raw)
        o = OcrInputImage(raw=raw)
        e = EmbeddingInputImage(raw=raw)
        v = VlmInputImage(raw=raw)
        self.assertIsNot(type(d), type(o))
        self.assertIsNot(type(o), type(e))
        self.assertIsNot(type(e), type(v))

    def test_raw_shared_by_reference(self):
        raw = _raw()
        d = DetectInputImage(raw=raw)
        e = EmbeddingInputImage(raw=raw)
        # 같은 raw 객체를 가리키지만 stage 입력 자체는 분리된 dataclass
        self.assertIs(d.raw, raw)
        self.assertIs(e.raw, raw)

    def test_inputs_are_frozen(self):
        d = DetectInputImage(raw=_raw())
        with self.assertRaises(FrozenInstanceError):
            d.transforms = (TransformKind.CLAHE,)  # type: ignore[misc]


# ── stage 별 허용 transform 매트릭스 ──────────────────────────────────────

class TestAllowedTransforms(TestCase):
    def test_detect_allows_clahe_grayscale_contrast(self):
        DetectInputImage(
            raw=_raw(),
            transforms=(TransformKind.CLAHE, TransformKind.GRAYSCALE, TransformKind.CONTRAST),
        ).validate()

    def test_detect_rejects_deskew_kind(self):
        # deskew 는 detect stage 에 허용되지 않는 transform (좌표 변경 위험 — OCR 만 허용)
        d = DetectInputImage(
            raw=_raw(),
            transforms=(TransformKind.DESKEW,),
        )
        with self.assertRaises(InvalidPreprocessingTransform):
            d.validate()

    def test_detect_rejects_perspective(self):
        d = DetectInputImage(
            raw=_raw(),
            transforms=(TransformKind.PERSPECTIVE_RECTIFY,),
        )
        with self.assertRaises(InvalidPreprocessingTransform):
            d.validate()

    def test_ocr_rejects_binary(self):
        o = OcrInputImage(
            raw=_raw(),
            transforms=(TransformKind.BINARY_THRESHOLD,),
        )
        with self.assertRaises(InvalidPreprocessingTransform):
            o.validate()

    def test_ocr_allows_clahe(self):
        OcrInputImage(
            raw=_raw(),
            transforms=(TransformKind.CLAHE,),
        ).validate()

    def test_embedding_rejects_binary(self):
        e = EmbeddingInputImage(
            raw=_raw(),
            transforms=(TransformKind.BINARY_THRESHOLD,),
        )
        with self.assertRaises(InvalidPreprocessingTransform):
            e.validate()

    def test_embedding_rejects_strong_contrast(self):
        e = EmbeddingInputImage(
            raw=_raw(),
            transforms=(TransformKind.CONTRAST,),
        )
        with self.assertRaises(InvalidPreprocessingTransform):
            e.validate()

    def test_embedding_rejects_deskew(self):
        # CLIP 은 deskew 받지 않는다 — raw crop 우선 contract
        e = EmbeddingInputImage(
            raw=_raw(),
            transforms=(TransformKind.DESKEW,),
            transform_metadata=PreprocessTransformMetadata(
                transform_kind=TransformKind.DESKEW,
                inverse_supported=True,
            ),
        )
        with self.assertRaises(InvalidPreprocessingTransform):
            e.validate()

    def test_embedding_allows_mild_clahe(self):
        EmbeddingInputImage(
            raw=_raw(),
            transforms=(TransformKind.CLAHE,),
        ).validate()

    def test_vlm_allows_scale(self):
        VlmInputImage(
            raw=_raw(),
            transforms=(TransformKind.SCALE,),
            transform_metadata=PreprocessTransformMetadata(
                transform_kind=TransformKind.SCALE,
                src_shape=(2337, 1653),
                dst_shape=(1600, 1132),
                scale=0.6845,
                inverse_supported=True,
            ),
        ).validate()

    def test_vlm_rejects_binary(self):
        v = VlmInputImage(
            raw=_raw(),
            transforms=(TransformKind.BINARY_THRESHOLD,),
        )
        with self.assertRaises(InvalidPreprocessingTransform):
            v.validate()


# ── bbox-changing transform 시 metadata 강제 ──────────────────────────────

class TestBboxChangingTransformMetadata(TestCase):
    def test_deskew_in_ocr_requires_metadata(self):
        with self.assertRaises(MissingTransformMetadata):
            OcrInputImage(
                raw=_raw(),
                transforms=(TransformKind.DESKEW,),
            ).validate()

    def test_deskew_metadata_without_inverse_rejected(self):
        meta = PreprocessTransformMetadata(
            transform_kind=TransformKind.DESKEW,
            src_shape=(2337, 1653),
            dst_shape=(2337, 1653),
            deskew_angle_deg=2.5,
            inverse_supported=False,
        )
        with self.assertRaises(UnsafeTransformWiring):
            OcrInputImage(
                raw=_raw(),
                transforms=(TransformKind.DESKEW,),
                transform_metadata=meta,
            ).validate()

    def test_deskew_metadata_with_inverse_ok(self):
        meta = PreprocessTransformMetadata(
            transform_kind=TransformKind.DESKEW,
            src_shape=(2337, 1653),
            dst_shape=(2337, 1653),
            deskew_angle_deg=2.5,
            inverse_supported=True,
        )
        OcrInputImage(
            raw=_raw(),
            transforms=(TransformKind.DESKEW,),
            transform_metadata=meta,
        ).validate()  # no raise

    def test_scale_in_vlm_requires_metadata(self):
        with self.assertRaises(MissingTransformMetadata):
            VlmInputImage(
                raw=_raw(),
                transforms=(TransformKind.SCALE,),
            ).validate()

    def test_changes_bbox_for_deskew_perspective_resize_scale(self):
        for kind in (
            TransformKind.DESKEW,
            TransformKind.PERSPECTIVE_RECTIFY,
            TransformKind.RESIZE,
            TransformKind.SCALE,
        ):
            meta = PreprocessTransformMetadata(transform_kind=kind)
            self.assertTrue(meta.changes_bbox_coordinates(), f"{kind} should change bbox")

    def test_changes_bbox_false_for_clahe_grayscale_binary(self):
        for kind in (
            TransformKind.CLAHE,
            TransformKind.GRAYSCALE,
            TransformKind.BINARY_THRESHOLD,
            TransformKind.AUTOCONTRAST,
            TransformKind.CONTRAST,
            TransformKind.UNSHARP_MASK,
        ):
            meta = PreprocessTransformMetadata(transform_kind=kind)
            self.assertFalse(meta.changes_bbox_coordinates(), f"{kind} should NOT change bbox")

    def test_safe_for_wiring(self):
        clahe_only = PreprocessTransformMetadata(transform_kind=TransformKind.CLAHE)
        self.assertTrue(clahe_only.is_safe_for_operational_wiring())

        deskew_no_inverse = PreprocessTransformMetadata(
            transform_kind=TransformKind.DESKEW,
            inverse_supported=False,
        )
        self.assertFalse(deskew_no_inverse.is_safe_for_operational_wiring())

        deskew_with_inverse = PreprocessTransformMetadata(
            transform_kind=TransformKind.DESKEW,
            inverse_supported=True,
        )
        self.assertTrue(deskew_with_inverse.is_safe_for_operational_wiring())


# ── source_type 분기 ─────────────────────────────────────────────────────

class TestSourceTypeDecision(TestCase):
    def test_native_pdf_with_density_skips_preprocessing(self):
        d = decide_preprocessing(
            source_type=SourceType.ACADEMY_WORKBOOK,
            is_native_pdf=True,
            text_density=0.05,
        )
        self.assertFalse(d.detect_apply_clahe)
        self.assertFalse(d.ocr_apply_clahe_deskew)
        self.assertFalse(d.camera_preprocessing_allowed())
        self.assertTrue(d.embedding_use_raw_crop)
        self.assertTrue(d.vlm_use_raw)

    def test_native_pdf_without_density_provided_skips(self):
        # text_density=None → native PDF 일 때 보수적으로 skip
        d = decide_preprocessing(
            source_type=SourceType.ACADEMY_WORKBOOK,
            is_native_pdf=True,
            text_density=None,
        )
        self.assertFalse(d.camera_preprocessing_allowed())

    def test_student_exam_photo_is_candidate(self):
        d = decide_preprocessing(
            source_type=SourceType.STUDENT_EXAM_PHOTO,
            is_native_pdf=False,
        )
        self.assertTrue(d.detect_apply_clahe)
        self.assertTrue(d.ocr_apply_clahe_deskew)
        self.assertTrue(d.camera_preprocessing_allowed())
        # CLIP 입력은 raw crop 우선 — Stage 6.3P audit manual 경로 위반 fix 후속
        self.assertTrue(d.embedding_use_raw_crop)
        self.assertTrue(d.vlm_use_raw)

    def test_scanned_pdf_is_candidate(self):
        d = decide_preprocessing(
            source_type=SourceType.SCANNED_PDF,
            is_native_pdf=False,
        )
        self.assertTrue(d.detect_apply_clahe)
        self.assertTrue(d.ocr_apply_clahe_deskew)

    def test_image_only_page_is_candidate(self):
        d = decide_preprocessing(
            source_type=SourceType.IMAGE_ONLY_PAGE,
            is_native_pdf=False,
        )
        self.assertTrue(d.camera_preprocessing_allowed())

    def test_school_exam_pdf_native_with_density_skips(self):
        d = decide_preprocessing(
            source_type=SourceType.SCHOOL_EXAM_PDF,
            is_native_pdf=True,
            text_density=0.05,
        )
        self.assertFalse(d.camera_preprocessing_allowed())

    def test_school_exam_pdf_scanned_uses_preprocessing(self):
        d = decide_preprocessing(
            source_type=SourceType.SCHOOL_EXAM_PDF,
            is_native_pdf=False,
            text_density=0.005,
        )
        self.assertTrue(d.camera_preprocessing_allowed())

    def test_explanation_skipped_even_when_image_based(self):
        d = decide_preprocessing(
            source_type=SourceType.EXPLANATION,
            is_native_pdf=False,
            text_density=0.005,
        )
        self.assertFalse(d.camera_preprocessing_allowed())

    def test_answer_key_skipped(self):
        d = decide_preprocessing(
            source_type=SourceType.ANSWER_KEY,
            is_native_pdf=True,
        )
        self.assertFalse(d.camera_preprocessing_allowed())

    def test_commercial_workbook_skipped(self):
        d = decide_preprocessing(
            source_type=SourceType.COMMERCIAL_WORKBOOK,
            is_native_pdf=False,
        )
        self.assertFalse(d.camera_preprocessing_allowed())

    def test_native_pdf_low_density_falls_through_to_image(self):
        # native PDF 인데 text density 가 낮으면 image-based 로 떨어진다
        d = decide_preprocessing(
            source_type=SourceType.SCHOOL_EXAM_PDF,
            is_native_pdf=True,
            text_density=0.005,
        )
        self.assertTrue(d.camera_preprocessing_allowed())

    def test_other_source_type_conservative_default(self):
        d = decide_preprocessing(
            source_type=SourceType.OTHER,
            is_native_pdf=False,
        )
        self.assertFalse(d.camera_preprocessing_allowed())

    def test_decision_carries_rationale(self):
        d = decide_preprocessing(
            source_type=SourceType.STUDENT_EXAM_PHOTO,
            is_native_pdf=False,
        )
        self.assertIsInstance(d, PreprocessingDecision)
        self.assertTrue(d.rationale)


# ── page_level_fallback 정책 ─────────────────────────────────────────────

class TestPageLevelFallbackPolicy(TestCase):
    def test_fallback_is_not_success(self):
        self.assertFalse(PAGE_LEVEL_FALLBACK_IS_SUCCESS)

    def test_direct_hit_disabled_for_fallback(self):
        self.assertFalse(DIRECT_HIT_LABEL_ALLOWED_FOR_FALLBACK)

    def test_segmentation_status_enum_values(self):
        self.assertEqual(SegmentationStatus.PROBLEM_LEVEL.value, "problem_level")
        self.assertEqual(SegmentationStatus.PAGE_LEVEL_FALLBACK.value, "page_level_fallback")
        self.assertEqual(SegmentationStatus.FAILED.value, "failed")

    def test_quality_enum_values(self):
        self.assertEqual(MatchupQuality.HIGH.value, "high")
        self.assertEqual(MatchupQuality.MEDIUM.value, "medium")
        self.assertEqual(MatchupQuality.LOW.value, "low")
        self.assertEqual(MatchupQuality.UNKNOWN.value, "unknown")


# ── 운영 wiring guard ────────────────────────────────────────────────────

class TestOperationalWiringGuard(TestCase):
    def test_assert_no_operational_wiring_always_raises(self):
        d = DetectInputImage(raw=_raw())
        with self.assertRaises(UnsafeTransformWiring):
            assert_no_operational_wiring(d)

    def test_guard_message_mentions_stage(self):
        try:
            assert_no_operational_wiring(DetectInputImage(raw=_raw()))
        except UnsafeTransformWiring as e:
            self.assertIn("6.3P-1", str(e))


# ── 텍스트 density 임계 ──────────────────────────────────────────────────

class TestTextDensityThreshold(TestCase):
    def test_threshold_is_dryrun_calibrated(self):
        # Stage 6.3P dry-run 측정 (academy_workbook 0.0232, scanned 0.0517) 기반
        self.assertGreater(TEXT_DENSITY_THRESHOLD, 0.020)
        self.assertLess(TEXT_DENSITY_THRESHOLD, 0.050)


# ── 운영 무거운 dependency import 회귀 ──────────────────────────────────

class TestNoOperationalDeps(TestCase):
    """본 모듈 import 만으로 운영 무거운 dependency 가 transitive 하게 import 되어선
    안 된다. import 되면 본 모듈이 운영 path 에 wiring 된 회귀 신호.
    """

    def test_no_segment_dispatcher_or_vlm_or_ocr_imported(self):
        mod_name = "academy.domain.tools.preprocessing.contract"
        # 깨끗한 상태에서 다시 로드
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        importlib.import_module(mod_name)

        forbidden = [
            "academy.adapters.ai.detection.segment_dispatcher",
            "academy.adapters.ai.detection.vlm_fallback",
            "academy.adapters.ai.ocr.google",
            "ultralytics",
        ]
        loaded = []
        for fmod in forbidden:
            if fmod in sys.modules:
                loaded.append(fmod)
        self.assertEqual(
            loaded, [],
            f"preprocessing_input_contract import 가 운영 path 를 끌어들임: {loaded}",
        )

    def test_no_django_orm_import(self):
        # django.db.models 가 transitive 하게 끌려오면 본 contract 가 model 의존 회귀
        mod_name = "academy.domain.tools.preprocessing.contract"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        # Django 자체는 conftest 에서 setup 됐을 수 있으므로 import 자체로 판단 X.
        # contract 모듈 source 안에 'django' 또는 'models' import 가 없는지 path 검사
        spec = importlib.util.find_spec(mod_name)  # type: ignore[attr-defined]
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.origin)
        with open(spec.origin, encoding="utf-8") as f:
            src = f.read()
        # 본 contract 는 stdlib 만 의존 — django/numpy/cv2/PIL import 0
        for forbidden in ("import django", "from django", "import cv2", "import numpy", "from PIL"):
            self.assertNotIn(
                forbidden, src,
                f"contract 모듈 source 에 {forbidden!r} 가 있음 — stdlib only contract 위반",
            )


# ── stage enum coverage ──────────────────────────────────────────────────

class TestStageEnumCoverage(TestCase):
    def test_all_stages_have_allowed_set(self):
        from academy.domain.tools.preprocessing.contract import (
            _ALLOWED_TRANSFORMS_BY_STAGE,
        )
        for stage in PreprocessingStage:
            self.assertIn(stage, _ALLOWED_TRANSFORMS_BY_STAGE)

    def test_raw_stage_allows_only_none(self):
        from academy.domain.tools.preprocessing.contract import (
            _ALLOWED_TRANSFORMS_BY_STAGE,
        )
        self.assertEqual(
            _ALLOWED_TRANSFORMS_BY_STAGE[PreprocessingStage.RAW],
            frozenset({TransformKind.NONE}),
        )
