"""Stage 5.9 (2026-05-07) — proposal payload 검증 강화 단위 테스트.

검증:
- ENGINE_CHOICES / STATUS_CHOICES / APPROVABLE_STATUSES 운영 정의와 일치
- validate_payload_schema (1:1 매핑 / audit 필드 누설 차단)
- validate_payload_fields (bbox / engine / status / confidence / etc.)
- is_approvable (status + manual_overlap 영구 차단)
- validate_status_transition (approved/rejected → 변경 차단)
- static_manual_overlap_provider (DB query 0회)
- apply_manual_overlap_via_provider — 새 payload 반환 (immutable)
- assert_selected_problem_ids_independence (구조적 무관성)
- DB 모델 / 운영 helper / OCR/VLM SDK import 0회 regression
"""
from __future__ import annotations

from unittest import TestCase

from apps.domains.matchup.segmentation.mock_response_integrator import (
    ProposalPayloadCandidate, ValidationError,
)
from apps.domains.matchup.segmentation.proposal_payload_validator import (
    APPROVABLE_STATUSES, ENGINE_CHOICES, MANUAL_OVERLAP_IOU_THRESHOLD,
    PERMANENTLY_BLOCKING_CODES, SCHEMA_VERSION, STATUS_CHOICES,
    apply_manual_overlap_via_provider,
    assert_selected_problem_ids_independence,
    has_blocking_error, is_approvable, report_to_dict,
    static_manual_overlap_provider,
    validate_batch, validate_payload_fields,
    validate_payload_schema, validate_status_transition,
)


def _ok_payload(**overrides) -> ProposalPayloadCandidate:
    base = dict(
        tenant_id=2, document_id=99, page_number=0,
        detected_problem_number=1,
        bbox={"x": 0.10, "y": 0.20, "w": 0.50, "h": 0.30, "norm": True},
        engine="vlm", model_version="mock-1", confidence=0.85,
        status="pending",
        analysis_version_key="batch-x", image_key="",
        raw_response={}, validation_errors=[],
    )
    base.update(overrides)
    return ProposalPayloadCandidate(**base)


class ConstantsMirrorTests(TestCase):
    """본 모듈의 운영 mirror 가 운영 정의와 일치 — 모델 직접 import 안 함."""

    def test_engine_choices(self):
        # 운영 ProblemSegmentationProposal.ENGINE_CHOICES 와 동일
        self.assertEqual(
            ENGINE_CHOICES,
            frozenset({"yolo", "vlm", "ocr", "native_pdf", "manual_assist"}),
        )

    def test_status_choices(self):
        self.assertEqual(
            STATUS_CHOICES,
            frozenset({"pending", "needs_review", "rejected", "approved", "auto_passed"}),
        )

    def test_approvable_statuses(self):
        # proposal_helpers._APPROVABLE_STATUSES 와 동일
        self.assertEqual(
            APPROVABLE_STATUSES,
            frozenset({"pending", "needs_review", "auto_passed"}),
        )

    def test_manual_overlap_threshold(self):
        self.assertEqual(MANUAL_OVERLAP_IOU_THRESHOLD, 0.30)

    def test_blocking_codes_includes_manual_overlap(self):
        self.assertIn("manual_overlap", PERMANENTLY_BLOCKING_CODES)


class ValidatePayloadSchemaTests(TestCase):
    def test_ok_payload_no_violations(self):
        v = validate_payload_schema(_ok_payload())
        self.assertEqual(v, [])

    def test_audit_fields_not_in_payload_schema(self):
        # ProposalPayloadCandidate dataclass 자체에 audit 필드 없음 — 정상
        from dataclasses import fields
        keys = {f.name for f in fields(ProposalPayloadCandidate)}
        self.assertNotIn("reviewed_by", keys)
        self.assertNotIn("reviewed_at", keys)
        self.assertNotIn("promoted_problem", keys)
        self.assertNotIn("selected_problem_ids", keys)


class ValidatePayloadFieldsTests(TestCase):
    def test_ok_payload(self):
        v = validate_payload_fields(_ok_payload())
        self.assertEqual(v, [])

    def test_invalid_engine(self):
        v = validate_payload_fields(_ok_payload(engine="invalid_engine"))
        self.assertTrue(any(viol.field == "engine" for viol in v))

    def test_invalid_status(self):
        v = validate_payload_fields(_ok_payload(status="weird"))
        self.assertTrue(any(viol.field == "status" for viol in v))

    def test_confidence_out_of_range(self):
        v = validate_payload_fields(_ok_payload(confidence=1.5))
        self.assertTrue(any(viol.field == "confidence" for viol in v))
        v2 = validate_payload_fields(_ok_payload(confidence=-0.1))
        self.assertTrue(any(viol.field == "confidence" for viol in v2))

    def test_negative_page_number(self):
        v = validate_payload_fields(_ok_payload(page_number=-1))
        self.assertTrue(any(viol.field == "page_number" for viol in v))

    def test_bbox_missing_keys(self):
        v = validate_payload_fields(_ok_payload(bbox={"x": 0.1, "y": 0.2}))
        keys = {viol.field for viol in v}
        self.assertIn("bbox.w", keys)
        self.assertIn("bbox.h", keys)
        self.assertIn("bbox.norm", keys)

    def test_bbox_norm_must_be_bool(self):
        v = validate_payload_fields(_ok_payload(
            bbox={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "norm": "yes"},
        ))
        self.assertTrue(any(viol.field == "bbox.norm" for viol in v))

    def test_validation_errors_must_be_list(self):
        bad = _ok_payload()
        bad.validation_errors = "not_a_list"  # type: ignore
        v = validate_payload_fields(bad)
        self.assertTrue(any(viol.field == "validation_errors" for viol in v))

    def test_model_version_too_long(self):
        v = validate_payload_fields(_ok_payload(model_version="a" * 65))
        self.assertTrue(any(viol.field == "model_version" for viol in v))


class IsApprovableTests(TestCase):
    def test_pending_no_blocking_is_approvable(self):
        self.assertTrue(is_approvable(_ok_payload(status="pending")))

    def test_needs_review_approvable(self):
        self.assertTrue(is_approvable(_ok_payload(status="needs_review")))

    def test_auto_passed_approvable(self):
        self.assertTrue(is_approvable(_ok_payload(status="auto_passed")))

    def test_rejected_not_approvable(self):
        self.assertFalse(is_approvable(_ok_payload(status="rejected")))

    def test_approved_not_approvable(self):
        # 이미 approved 면 다시 approve 대상 X
        self.assertFalse(is_approvable(_ok_payload(status="approved")))

    def test_manual_overlap_blocks_approve(self):
        p = _ok_payload(status="pending", validation_errors=[
            ValidationError(code="manual_overlap", detail="overlap", bbox_iou=0.5),
        ])
        self.assertFalse(is_approvable(p))


class HasBlockingErrorTests(TestCase):
    def test_no_errors_not_blocked(self):
        blocked, codes = has_blocking_error(_ok_payload())
        self.assertFalse(blocked)
        self.assertEqual(codes, [])

    def test_manual_overlap_blocks(self):
        p = _ok_payload(validation_errors=[
            ValidationError(code="manual_overlap", detail="x"),
        ])
        blocked, codes = has_blocking_error(p)
        self.assertTrue(blocked)
        self.assertIn("manual_overlap", codes)

    def test_other_error_does_not_block(self):
        p = _ok_payload(validation_errors=[
            ValidationError(code="some_warning", detail="y"),
        ])
        blocked, _ = has_blocking_error(p)
        self.assertFalse(blocked)


class ValidateStatusTransitionTests(TestCase):
    def test_pending_to_approved_ok(self):
        self.assertIsNone(validate_status_transition("pending", "approved"))

    def test_pending_to_rejected_ok(self):
        self.assertIsNone(validate_status_transition("pending", "rejected"))

    def test_approved_to_anything_blocked(self):
        v = validate_status_transition("approved", "rejected")
        self.assertIsNotNone(v)
        self.assertEqual(v.code, "invalid_choice")

    def test_rejected_to_anything_blocked(self):
        v = validate_status_transition("rejected", "approved")
        self.assertIsNotNone(v)

    def test_invalid_status_blocked(self):
        v = validate_status_transition("weird", "approved")
        self.assertIsNotNone(v)


class StaticManualOverlapProviderTests(TestCase):
    def test_no_match_no_overlap(self):
        provider = static_manual_overlap_provider([
            {"document_id": 99, "page_number": 0, "bbox_norm": (0.7, 0.7, 0.2, 0.2)},
        ])
        overlaps, iou, _ = provider(99, 0, {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2})
        self.assertFalse(overlaps)
        self.assertEqual(iou, 0.0)

    def test_overlap_detected(self):
        provider = static_manual_overlap_provider([
            {"document_id": 99, "page_number": 0,
             "bbox_norm": (0.10, 0.10, 0.50, 0.50),
             "manual_problem_id": 1234},
        ])
        overlaps, iou, conflict_id = provider(
            99, 0, {"x": 0.10, "y": 0.10, "w": 0.50, "h": 0.50},
        )
        self.assertTrue(overlaps)
        self.assertEqual(iou, 1.0)
        self.assertEqual(conflict_id, 1234)

    def test_different_doc_no_overlap(self):
        provider = static_manual_overlap_provider([
            {"document_id": 100, "page_number": 0,
             "bbox_norm": (0.10, 0.10, 0.50, 0.50)},
        ])
        overlaps, _, _ = provider(99, 0, {"x": 0.10, "y": 0.10, "w": 0.50, "h": 0.50})
        self.assertFalse(overlaps)

    def test_invalid_bbox_conservative_overlap(self):
        provider = static_manual_overlap_provider([])
        overlaps, iou, _ = provider(99, 0, {"x": "abc"})
        self.assertTrue(overlaps)
        self.assertEqual(iou, -1.0)


class ApplyManualOverlapTests(TestCase):
    def test_overlap_marks_rejected(self):
        provider = static_manual_overlap_provider([
            {"document_id": 99, "page_number": 0,
             "bbox_norm": (0.10, 0.10, 0.50, 0.50)},
        ])
        before = _ok_payload(status="pending")
        after = apply_manual_overlap_via_provider(before, provider)
        self.assertEqual(after.status, "rejected")
        self.assertTrue(any(e.code == "manual_overlap" for e in after.validation_errors))
        # immutable — 원본 미변경
        self.assertEqual(before.status, "pending")
        self.assertEqual(before.validation_errors, [])

    def test_no_overlap_unchanged(self):
        provider = static_manual_overlap_provider([
            {"document_id": 99, "page_number": 0,
             "bbox_norm": (0.7, 0.7, 0.2, 0.2)},
        ])
        before = _ok_payload(status="pending")
        after = apply_manual_overlap_via_provider(before, provider)
        self.assertEqual(after.status, "pending")
        self.assertEqual(after, before)

    def test_no_duplicate_manual_overlap_error(self):
        provider = static_manual_overlap_provider([
            {"document_id": 99, "page_number": 0,
             "bbox_norm": (0.10, 0.10, 0.50, 0.50)},
        ])
        before = _ok_payload(status="pending", validation_errors=[
            ValidationError(code="manual_overlap", detail="prior", bbox_iou=0.4),
        ])
        after = apply_manual_overlap_via_provider(before, provider)
        # 중복 추가 안 됨
        manual_count = sum(1 for e in after.validation_errors if e.code == "manual_overlap")
        self.assertEqual(manual_count, 1)


class SelectedProblemIdsIndependenceTests(TestCase):
    def test_independence_holds_for_ok_payload(self):
        self.assertTrue(assert_selected_problem_ids_independence([_ok_payload(), _ok_payload()]))


class ValidateBatchTests(TestCase):
    def test_batch_report_counts(self):
        payloads = [
            _ok_payload(status="pending"),                    # approvable
            _ok_payload(status="approved"),                   # not approvable
            _ok_payload(status="rejected", validation_errors=[
                ValidationError(code="manual_overlap", detail="x", bbox_iou=0.5),
            ]),                                               # blocked + not approvable
            _ok_payload(status="needs_review"),               # approvable
        ]
        report = validate_batch(payloads)
        self.assertEqual(report.total, 4)
        self.assertEqual(report.schema_ok_count, 4)
        self.assertEqual(report.field_ok_count, 4)
        self.assertEqual(report.approvable_count, 2)
        self.assertEqual(report.blocked_count, 1)

    def test_report_to_dict_serializable(self):
        report = validate_batch([_ok_payload()])
        d = report_to_dict(report)
        self.assertEqual(d["schema_version"], SCHEMA_VERSION)
        import json
        json.dumps(d, default=str)


class ValidatorRegressionTests(TestCase):
    def test_no_real_api_imports(self):
        from apps.domains.matchup.segmentation import proposal_payload_validator
        import inspect
        src = inspect.getsource(proposal_payload_validator)
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
        )
        for token in forbidden_imports:
            self.assertNotIn(token, src,
                             f"validator 에서 실 SDK import '{token}' 발견")

    def test_no_db_or_helper_imports(self):
        """validator 가 운영 ProblemSegmentationProposal 모델 / proposal_helpers / ai gateway 미import.

        호환성은 STATUS_CHOICES / ENGINE_CHOICES / APPROVABLE_STATUSES mirror constants 로 보장.
        """
        from apps.domains.matchup.segmentation import proposal_payload_validator
        import inspect
        src = inspect.getsource(proposal_payload_validator)
        if src.startswith('"""'):
            end = src.find('"""', 3)
            if end > 0:
                src = src[end + 3:]
        forbidden = (
            "import ProblemSegmentationProposal",
            "import MatchupProblem",
            "ProblemSegmentationProposal.objects",
            "MatchupProblem.objects",
            ".objects.create(", ".objects.bulk_create(",
            "from apps.domains.matchup.proposal_helpers",
            "from apps.domains.matchup.models",
            "from apps.domains.ai.gateway",
            "dispatch_job(",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"validator 에서 운영 import '{token}' 발견")
