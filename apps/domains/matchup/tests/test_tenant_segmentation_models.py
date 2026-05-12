"""Stage 5.4.6 (2026-05-06) — TenantSegmentationProfile / LayoutFingerprint /
ManualCorrectionDelta 모델 단위 테스트.

검증:
- 모델 정의 / default 값 / JSONField mutable shared 방지
- tenant FK 필수
- is_active default False (gradual rollout)
- LayoutFingerprint tenant + document + version unique
- ManualCorrectionDelta correction_type / source choices
- 기존 segmentation parser 미참조 (regression)
- selected_problem_ids / hit_report 미접근 보장 (정적 검증)

DB 무관 mock — 모델 schema / default callable 만 검증.
"""
from __future__ import annotations

from unittest import TestCase

from apps.domains.matchup.models import (
    LayoutFingerprint,
    ManualCorrectionDelta,
    TenantSegmentationProfile,
)


# ── TenantSegmentationProfile ──


class TenantSegmentationProfileSchemaTests(TestCase):
    def test_default_is_active_false(self):
        """gradual rollout — 기본 is_active=False."""
        field = TenantSegmentationProfile._meta.get_field("is_active")
        self.assertEqual(field.default, False)

    def test_default_fallback_to_global_true(self):
        field = TenantSegmentationProfile._meta.get_field("fallback_to_global")
        self.assertEqual(field.default, True)

    def test_jsonfield_defaults_callable(self):
        """mutable shared object 방지 — default 가 callable (dict / list) 이어야."""
        for name in (
            "paper_type_thresholds", "paper_type_expected_max",
            "auto_approve_thresholds", "bbox_stats", "column_count_distribution",
        ):
            field = TenantSegmentationProfile._meta.get_field(name)
            self.assertEqual(field.default, dict, f"{name} default must be dict (callable)")

        # list type
        field = TenantSegmentationProfile._meta.get_field("common_layout_clusters")
        self.assertEqual(field.default, list)

    def test_tenant_fk_one_to_one(self):
        """tenant 와 OneToOne — tenant 별 단일 profile."""
        from django.db.models import OneToOneField
        field = TenantSegmentationProfile._meta.get_field("tenant")
        self.assertIsInstance(field, OneToOneField)

    def test_profile_version_default_1(self):
        field = TenantSegmentationProfile._meta.get_field("profile_version")
        self.assertEqual(field.default, 1)

    def test_confidence_score_default_zero(self):
        field = TenantSegmentationProfile._meta.get_field("confidence_score")
        self.assertEqual(field.default, 0.0)

    def test_independent_default_instances(self):
        """default callable — 인스턴스마다 별도 dict/list."""
        a = TenantSegmentationProfile()
        b = TenantSegmentationProfile()
        a.paper_type_thresholds["exam"] = {"x": 1}
        # b 는 a 의 dict 와 분리되어야 함
        self.assertEqual(b.paper_type_thresholds, {})
        a.common_layout_clusters.append("single_column")
        self.assertEqual(b.common_layout_clusters, [])


# ── LayoutFingerprint ──


class LayoutFingerprintSchemaTests(TestCase):
    def test_tenant_fk_required(self):
        from django.db.models import ForeignKey
        field = LayoutFingerprint._meta.get_field("tenant")
        self.assertIsInstance(field, ForeignKey)
        self.assertFalse(field.null)

    def test_document_fk_required(self):
        from django.db.models import ForeignKey
        field = LayoutFingerprint._meta.get_field("document")
        self.assertIsInstance(field, ForeignKey)
        self.assertFalse(field.null)

    def test_jsonfield_defaults_callable(self):
        for name in ("page_size", "y_gap_distribution", "font_size_distribution"):
            field = LayoutFingerprint._meta.get_field(name)
            self.assertEqual(field.default, dict, f"{name} default must be dict")

        for name in ("x0_clusters", "filename_patterns"):
            field = LayoutFingerprint._meta.get_field(name)
            self.assertEqual(field.default, list, f"{name} default must be list")

    def test_unique_constraint_per_doc_version(self):
        constraint_names = {c.name for c in LayoutFingerprint._meta.constraints}
        self.assertIn("uniq_layout_fingerprint_per_doc_version", constraint_names)

    def test_similarity_cluster_id_db_indexed(self):
        field = LayoutFingerprint._meta.get_field("similarity_cluster_id")
        self.assertTrue(field.db_index)

    def test_default_column_count_1(self):
        field = LayoutFingerprint._meta.get_field("column_count")
        self.assertEqual(field.default, 1)

    def test_independent_defaults(self):
        a = LayoutFingerprint()
        b = LayoutFingerprint()
        a.x0_clusters.append(0.5)
        self.assertEqual(b.x0_clusters, [])
        a.page_size["w"] = 100
        self.assertEqual(b.page_size, {})


# ── ManualCorrectionDelta ──


class ManualCorrectionDeltaSchemaTests(TestCase):
    def test_correction_type_choices(self):
        choices = dict(ManualCorrectionDelta.CORRECTION_TYPE_CHOICES)
        for key in (
            "approve", "reject", "bbox_adjust", "split", "merge",
            "manual_create", "number_adjust", "text_adjust",
        ):
            self.assertIn(key, choices, f"missing correction_type {key}")

    def test_source_choices(self):
        choices = dict(ManualCorrectionDelta.SOURCE_CHOICES)
        self.assertIn("user_ui", choices)
        self.assertIn("admin_review", choices)

    def test_default_source_user_ui(self):
        field = ManualCorrectionDelta._meta.get_field("source")
        self.assertEqual(field.default, "user_ui")

    def test_proposal_problem_document_nullable(self):
        for name in ("proposal", "problem", "document"):
            field = ManualCorrectionDelta._meta.get_field(name)
            self.assertTrue(field.null, f"{name} must be nullable")
            self.assertTrue(field.blank, f"{name} must allow blank")

    def test_bbox_fields_nullable(self):
        for name in ("original_bbox", "corrected_bbox", "iou_with_ai"):
            field = ManualCorrectionDelta._meta.get_field(name)
            self.assertTrue(field.null)

    def test_tenant_fk_required(self):
        from django.db.models import ForeignKey
        field = ManualCorrectionDelta._meta.get_field("tenant")
        self.assertIsInstance(field, ForeignKey)
        self.assertFalse(field.null)

    def test_no_selected_problem_ids_field(self):
        """ManualCorrectionDelta 는 selected_problem_ids 필드 미보유.

        manual cut audit 모델이 hit_report selected_problem_ids 까지 변경하지 않음을
        schema 단계에서 보장.
        """
        field_names = {f.name for f in ManualCorrectionDelta._meta.get_fields()}
        self.assertNotIn("selected_problem_ids", field_names)


# ── 격리 검증 ──


class CrossTenantIsolationTests(TestCase):
    """tenant 간 profile / fingerprint 공유 금지 — schema 단계 정적 검증."""

    def test_tenant_profile_one_to_one(self):
        """OneToOneField — tenant 당 1 profile, 다른 tenant profile 참조 불가."""
        from django.db.models import OneToOneField
        field = TenantSegmentationProfile._meta.get_field("tenant")
        self.assertIsInstance(field, OneToOneField)

    def test_layout_fingerprint_tenant_indexed(self):
        """tenant FK db_index — tenant 내부 query 가 first-class."""
        field = LayoutFingerprint._meta.get_field("tenant")
        self.assertTrue(field.db_index)

    def test_correction_delta_tenant_indexed(self):
        field = ManualCorrectionDelta._meta.get_field("tenant")
        self.assertTrue(field.db_index)


class ParserNoReferenceTests(TestCase):
    """기존 segmentation parser (tier0_native_pdf / vlm_mock_contract) 가
    이 신 모델을 import 하지 않음 (역참조 방지 — schema 만 추가, 로직 미연결)."""

    def test_tier0_does_not_import_new_models(self):
        """tier0_native_pdf 가 신 모델 import / .objects 호출 X.

        주석/docstring 안 reference 는 허용 (예: profile schema 설명).
        """
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
            self.assertNotIn(
                token, src,
                f"tier0_native_pdf 가 신 모델 '{token}' 호출 — 역참조 발생",
            )

    def test_vlm_mock_does_not_import_new_models(self):
        from academy.adapters.ai.vlm import mock_contract
        import inspect
        src = inspect.getsource(vlm_mock_contract)
        forbidden = (
            "TenantSegmentationProfile",
            "LayoutFingerprint",
            "ManualCorrectionDelta",
        )
        for token in forbidden:
            self.assertNotIn(token, src)
