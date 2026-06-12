"""Stage 6.3V (2026-05-07) — fingerprint_collector 단위 테스트.

검증:
- measure_from_callback: derived 측정값 산출 (paper_type / page_count / page_size /
  column_count / filename_patterns ad-hoc meta)
- save_fingerprint:
  * 정상 INSERT/UPDATE
  * tenant mismatch 거부
  * MatchupDocument 미존재 시 graceful False
  * Django ORM 예외 시 graceful False (본 흐름 영향 0 보장)
- collect_and_save: 모든 예외 swallow, 어떤 raise 도 호출자에게 전파 X
- 운영 무거운 dependency import 회귀 (segment_dispatcher / vlm_fallback / google ocr)
"""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from academy.application.use_cases.ai.segmentation.fingerprint_collector import (
    FingerprintMeasurement,
    collect_and_save,
    measure_from_callback,
    save_fingerprint,
)


# ── 더미 doc 객체 ─────────────────────────────────────────────────────────

def _make_doc(
    *,
    doc_id: int = 100,
    tenant_id: int = 1,
    source_type: str = "academy_workbook",
    category: str = "고1 통합과학",
    title: str = "테스트 워크북.pdf",
    meta: dict | None = None,
):
    return SimpleNamespace(
        id=doc_id,
        tenant_id=tenant_id,
        source_type=source_type,
        category=category,
        title=title,
        meta=meta or {},
    )


# ── measure_from_callback ────────────────────────────────────────────────

class TestMeasureFromCallback(TestCase):
    def test_derives_paper_type_from_summary(self):
        doc = _make_doc(meta={
            "paper_type_summary": {"primary": "clean_pdf_dual", "counts": {"clean_pdf_dual": 14}},
            "segmentation_method": "anchor_text_based",
            "processing_quality": "precise_split",
            "bbox_null_ratio": 0.05,
            "indexable": True,
            "page_dimensions": [[1653, 2337], [1653, 2337]],
        })
        m = measure_from_callback(
            doc=doc,
            result_payload={},
            problem_count=23,
            cropped_problem_count=22,
        )
        self.assertEqual(m.paper_type, "clean_pdf_dual")
        self.assertEqual(m.page_count, 2)
        self.assertEqual(m.page_size, {"width": 1653, "height": 2337, "dpi": 200})
        self.assertEqual(m.column_count, 2)
        self.assertEqual(m.tenant_id, 1)
        self.assertEqual(m.document_id, 100)

    def test_quad_layout_detected(self):
        doc = _make_doc(meta={
            "paper_type_summary": {"primary": "quadrant", "counts": {"quadrant": 4}},
        })
        m = measure_from_callback(doc=doc, result_payload={}, problem_count=8)
        self.assertEqual(m.column_count, 4)

    def test_filename_meta_carries_source_type_and_quality(self):
        doc = _make_doc(
            source_type="student_exam_photo",
            meta={"processing_quality": "page_fallback", "indexable": False},
        )
        m = measure_from_callback(
            doc=doc, result_payload={"segmentation_method": "page_fallback"},
            problem_count=4, cropped_problem_count=0,
        )
        self.assertEqual(len(m.filename_patterns), 1)
        fm = m.filename_patterns[0]
        self.assertEqual(fm["source_type"], "student_exam_photo")
        self.assertEqual(fm["processing_quality"], "page_fallback")
        self.assertTrue(fm["page_level_fallback"])
        self.assertFalse(fm["indexable"])
        self.assertEqual(fm["problem_count"], 4)
        self.assertEqual(fm["cropped_problem_count"], 0)
        self.assertEqual(fm["fingerprint_schema_version"], "v1")

    def test_unmeasured_fields_default_zero(self):
        # 본 stage 에선 text_density / image_density / anchor_density 측정 X
        doc = _make_doc(meta={})
        m = measure_from_callback(doc=doc, result_payload={}, problem_count=0)
        self.assertEqual(m.text_density, 0.0)
        self.assertEqual(m.image_density, 0.0)
        self.assertEqual(m.anchor_density, 0.0)
        self.assertEqual(m.x0_clusters, [])
        self.assertEqual(m.y_gap_distribution, {})
        self.assertEqual(m.font_size_distribution, {})
        self.assertEqual(m.similarity_cluster_id, "")

    def test_handles_missing_meta_gracefully(self):
        doc = _make_doc(meta=None)
        m = measure_from_callback(doc=doc, result_payload={}, problem_count=0)
        self.assertEqual(m.paper_type, "")
        self.assertEqual(m.page_count, 0)


# ── save_fingerprint graceful degradation ────────────────────────────────

class TestSaveFingerprintGraceful(TestCase):
    def _measurement(self, **overrides):
        kwargs = dict(tenant_id=1, document_id=100)
        kwargs.update(overrides)
        return FingerprintMeasurement(**kwargs)

    def test_fail_on_doc_not_found_returns_false(self):
        # MatchupDocument.DoesNotExist → False, 본 흐름 raise X
        with patch(
            "apps.domains.matchup.models.MatchupDocument.objects.get",
            side_effect=__import__("apps.domains.matchup.models", fromlist=["MatchupDocument"]).MatchupDocument.DoesNotExist,
        ):
            ok = save_fingerprint(self._measurement(document_id=99999))
        self.assertFalse(ok)

    def test_fail_on_tenant_mismatch_returns_false(self):
        # doc tenant 와 measurement tenant 가 다르면 silent False
        fake_doc = SimpleNamespace(id=100, tenant_id=99)
        with patch(
            "apps.domains.matchup.models.MatchupDocument.objects.get",
            return_value=fake_doc,
        ):
            ok = save_fingerprint(self._measurement(tenant_id=1, document_id=100))
        self.assertFalse(ok)

    def test_fail_on_orm_exception_returns_false(self):
        # update_or_create 자체가 raise 해도 swallow + False
        fake_doc = SimpleNamespace(id=100, tenant_id=1)
        with patch(
            "apps.domains.matchup.models.MatchupDocument.objects.get",
            return_value=fake_doc,
        ), patch(
            "apps.domains.matchup.models.LayoutFingerprint.objects.update_or_create",
            side_effect=RuntimeError("simulated DB error"),
        ):
            ok = save_fingerprint(self._measurement(tenant_id=1, document_id=100))
        self.assertFalse(ok)

    def test_success_calls_update_or_create_with_correct_keys(self):
        fake_doc = SimpleNamespace(id=100, tenant_id=1)
        m = self._measurement(
            tenant_id=1, document_id=100,
            paper_type="clean_pdf_dual",
            page_count=14,
        )
        captured = {}

        def fake_update_or_create(**kwargs):
            captured.update(kwargs)
            return MagicMock(), True

        with patch(
            "apps.domains.matchup.models.MatchupDocument.objects.get",
            return_value=fake_doc,
        ), patch(
            "apps.domains.matchup.models.LayoutFingerprint.objects.update_or_create",
            side_effect=fake_update_or_create,
        ):
            ok = save_fingerprint(m)

        self.assertTrue(ok)
        self.assertEqual(captured["tenant_id"], 1)
        self.assertEqual(captured["document_id"], 100)
        self.assertEqual(captured["fingerprint_version"], 1)
        self.assertEqual(captured["defaults"]["paper_type"], "clean_pdf_dual")
        self.assertEqual(captured["defaults"]["page_count"], 14)


# ── collect_and_save: 어떤 예외도 호출자에게 전파 X ────────────────────

class TestCollectAndSave(TestCase):
    def test_swallows_measure_exception(self):
        # measure_from_callback 안에서 예외 발생해도 swallow
        broken = SimpleNamespace()  # tenant_id / id 모두 없음 → AttributeError
        ok = collect_and_save(doc=broken, result_payload={}, problem_count=0)
        self.assertFalse(ok)

    def test_swallows_save_exception(self):
        doc = _make_doc()
        with patch(
            "academy.application.use_cases.ai.segmentation.fingerprint_collector.save_fingerprint",
            side_effect=RuntimeError("simulated save failure"),
        ):
            ok = collect_and_save(doc=doc, result_payload={}, problem_count=5)
        self.assertFalse(ok)

    def test_returns_true_on_success(self):
        doc = _make_doc(meta={"processing_quality": "precise_split"})
        with patch(
            "academy.application.use_cases.ai.segmentation.fingerprint_collector.save_fingerprint",
            return_value=True,
        ):
            ok = collect_and_save(doc=doc, result_payload={}, problem_count=3)
        self.assertTrue(ok)


# ── 운영 무거운 dependency 회귀 ──────────────────────────────────────────

class TestNoOperationalDeps(TestCase):
    def test_no_segment_dispatcher_or_vlm_or_ocr(self):
        mod_name = "academy.application.use_cases.ai.segmentation.fingerprint_collector"
        forbidden = [
            "academy.adapters.ai.detection.segment_dispatcher",
            "academy.adapters.ai.detection.vlm_fallback",
            "academy.adapters.ai.ocr.google",
            "ultralytics",
            "cv2",
        ]
        before_modules = set(sys.modules)
        previous_module = sys.modules.pop(mod_name, None)
        try:
            importlib.import_module(mod_name)
            loaded = [
                m for m in forbidden
                if m in sys.modules and m not in before_modules
            ]
        finally:
            for m in forbidden:
                if m not in before_modules:
                    sys.modules.pop(m, None)
            sys.modules.pop(mod_name, None)
            if previous_module is not None:
                sys.modules[mod_name] = previous_module
        self.assertEqual(
            loaded, [],
            f"fingerprint_collector 가 운영 dep 끌어들임: {loaded}",
        )

    def test_module_uses_lazy_django_import(self):
        # save_fingerprint 호출 전엔 Django model import 가 sys.modules 에 없어야 한다
        # (collect_and_save 가 호출 시점에만 lazy import — 모듈 로드 자체로는 0 의존)
        mod_name = "academy.application.use_cases.ai.segmentation.fingerprint_collector"
        # 운영 import 후 source 검사 — 함수 안에서만 model 을 import 하는지
        spec = importlib.util.find_spec(mod_name)  # type: ignore[attr-defined]
        with open(spec.origin, encoding="utf-8") as f:
            src = f.read()
        # top-level 에 LayoutFingerprint / MatchupDocument import 가 없어야 한다
        # (함수 안 lazy import 만 허용)
        for forbidden_top in (
            "\nfrom apps.domains.matchup.models import",
            "\nimport apps.domains.matchup.models",
        ):
            self.assertNotIn(
                forbidden_top, src,
                f"top-level 에 model import 발견 — lazy import 로 변경 필요: {forbidden_top!r}",
            )
