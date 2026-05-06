"""Stage 3 Phase 3.4 (2026-05-06): ProblemSegmentationProposal admin API view 단위 테스트.

검증 항목:
- permission 가드: Admin only (_is_tenant_admin)
- tenant 격리: 자기 tenant proposal 외 access 시 404
- 입력 검증: invalid status / engine / document_id / json
- helper 위임: approve_proposal / reject_proposal 호출 + 결과 응답
- error mapping: ProposalApprovalError → 409
- list filter: document_id / status / engine / analysis_version_key
- pagination: limit / offset clamp
- _serialize_proposal 포맷 검증

DB 격리: helper 와 ORM access mock. 실제 DB 안 닿음.
"""
from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.domains.matchup.proposal_helpers import ProposalApprovalError
from apps.domains.matchup.views_proposal import (
    ProposalApproveView,
    ProposalDetailView,
    ProposalListView,
    ProposalRejectView,
    _serialize_proposal,
)


# ── helpers ──────────────────────────────────────────


def _make_request(*, method="GET", body=b"", get_params=None, is_admin=True):
    """RequestFactory 흉내 — view 안에서 사용하는 속성만."""
    req = MagicMock()
    req.method = method
    req.body = body
    req.GET = get_params or {}
    req.tenant = MagicMock(id=2)
    user = MagicMock()
    user.id = 42
    user.is_authenticated = True
    user.is_superuser = False
    user.is_staff = is_admin
    req.user = user
    return req


def _make_proposal(
    *, pid=1, tenant_id=2, document_id=100, status="pending",
    engine="yolo", model_version="v11", confidence=0.9,
    image_key="r2/p/1.png", bbox=None, validation_errors=None,
    page_number=1, detected_problem_number=5, analysis_version_key="batch-1",
    promoted_problem_id=None, reviewed_by_id=None, reviewed_at=None,
):
    p = MagicMock()
    p.id = pid
    p.tenant_id = tenant_id
    p.document_id = document_id
    p.analysis_version_key = analysis_version_key
    p.page_number = page_number
    p.detected_problem_number = detected_problem_number
    p.engine = engine
    p.model_version = model_version
    p.confidence = confidence
    p.status = status
    p.image_key = image_key
    p.bbox = bbox or {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "norm": True}
    p.validation_errors = validation_errors or []
    p.promoted_problem_id = promoted_problem_id
    p.reviewed_by_id = reviewed_by_id
    p.reviewed_at = reviewed_at
    from datetime import datetime, timezone
    p.created_at = datetime(2026, 5, 6, 9, 0, tzinfo=timezone.utc)
    p.updated_at = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
    p.refresh_from_db = MagicMock()
    return p


def _patch_admin(is_admin: bool):
    return patch(
        "apps.domains.matchup.views_proposal._is_tenant_admin",
        return_value=is_admin,
    )


def _patch_objects_filter(qs_result_proposals, count=None):
    """ProblemSegmentationProposal.objects.filter().filter()...order_by(...)[a:b] mock."""
    from apps.domains.matchup.models import ProblemSegmentationProposal

    qs = MagicMock()
    qs.filter = MagicMock(return_value=qs)
    qs.order_by = MagicMock(return_value=qs)
    qs.count = MagicMock(return_value=count if count is not None else len(qs_result_proposals))
    qs.__getitem__ = MagicMock(return_value=qs_result_proposals)

    objects = MagicMock()
    objects.filter = MagicMock(return_value=qs)
    return patch.object(ProblemSegmentationProposal, "objects", objects), objects, qs


def _patch_objects_get(proposal_or_none):
    """tenant 격리 사전 검증용 .objects.get() mock."""
    from apps.domains.matchup.models import ProblemSegmentationProposal

    objects = MagicMock()
    if proposal_or_none is None:
        from apps.domains.matchup.models import ProblemSegmentationProposal as cls
        # DoesNotExist 흉내 — 진짜 exception class 사용
        objects.get = MagicMock(side_effect=ProblemSegmentationProposal.DoesNotExist)
    else:
        objects.get = MagicMock(return_value=proposal_or_none)
    return patch.object(ProblemSegmentationProposal, "objects", objects), objects


# ── _serialize_proposal ──────────────────────────────────────────


class SerializerTests(TestCase):
    def test_basic_fields(self):
        p = _make_proposal(pid=42, status="needs_review", engine="vlm")
        out = _serialize_proposal(p)
        self.assertEqual(out["id"], 42)
        self.assertEqual(out["status"], "needs_review")
        self.assertEqual(out["engine"], "vlm")
        self.assertEqual(out["bbox"]["x"], 0.1)
        self.assertEqual(out["validation_errors"], [])

    def test_serializes_datetime(self):
        p = _make_proposal()
        out = _serialize_proposal(p)
        self.assertIsInstance(out["created_at"], str)
        self.assertIsInstance(out["updated_at"], str)

    def test_reviewed_at_none_when_unset(self):
        p = _make_proposal()
        out = _serialize_proposal(p)
        self.assertIsNone(out["reviewed_at"])


# ── ProposalListView ──────────────────────────────────────────


class ListViewPermissionTests(TestCase):
    def test_admin_required(self):
        view = ProposalListView()
        req = _make_request(get_params={})
        with _patch_admin(False):
            resp = view.get(req)
        self.assertEqual(resp.status_code, 403)


class ListViewFilterTests(TestCase):
    def test_filter_chain_includes_tenant(self):
        view = ProposalListView()
        proposal = _make_proposal()
        with _patch_admin(True):
            patch_objs, objects, qs = _patch_objects_filter([proposal])
            with patch_objs:
                req = _make_request(get_params={
                    "document_id": "100",
                    "status": "pending",
                    "engine": "yolo",
                    "analysis_version_key": "batch-1",
                })
                resp = view.get(req)
        self.assertEqual(resp.status_code, 200)
        # tenant filter 1번 + qs.filter 호출 4번 (document_id / status / engine / version)
        self.assertEqual(objects.filter.call_count, 1)
        # qs.filter는 chained — 4번 호출
        self.assertEqual(qs.filter.call_count, 4)

    def test_invalid_document_id_returns_400(self):
        view = ProposalListView()
        with _patch_admin(True):
            patch_objs, _, _ = _patch_objects_filter([])
            with patch_objs:
                req = _make_request(get_params={"document_id": "abc"})
                resp = view.get(req)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_status_returns_400(self):
        view = ProposalListView()
        with _patch_admin(True):
            patch_objs, _, _ = _patch_objects_filter([])
            with patch_objs:
                req = _make_request(get_params={"status": "INVALID_VALUE"})
                resp = view.get(req)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_engine_returns_400(self):
        view = ProposalListView()
        with _patch_admin(True):
            patch_objs, _, _ = _patch_objects_filter([])
            with patch_objs:
                req = _make_request(get_params={"engine": "INVALID_ENGINE"})
                resp = view.get(req)
        self.assertEqual(resp.status_code, 400)

    def test_pagination_limit_clamp(self):
        view = ProposalListView()
        with _patch_admin(True):
            patch_objs, _, _ = _patch_objects_filter([])
            with patch_objs:
                req = _make_request(get_params={"limit": "999", "offset": "-5"})
                resp = view.get(req)
        body = json.loads(resp.content)
        self.assertEqual(body["limit"], 200)  # max 200
        self.assertEqual(body["offset"], 0)   # min 0


# ── ProposalDetailView ──────────────────────────────────────────


class DetailViewTests(TestCase):
    def test_admin_required(self):
        view = ProposalDetailView()
        req = _make_request()
        with _patch_admin(False):
            resp = view.get(req, proposal_id=1)
        self.assertEqual(resp.status_code, 403)

    def test_returns_404_when_not_found(self):
        view = ProposalDetailView()
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(None)
            with patch_obj:
                req = _make_request()
                resp = view.get(req, proposal_id=999)
        self.assertEqual(resp.status_code, 404)

    def test_returns_serialized_proposal(self):
        view = ProposalDetailView()
        proposal = _make_proposal(pid=42)
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch_obj:
                req = _make_request()
                resp = view.get(req, proposal_id=42)
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(body["id"], 42)


# ── ProposalApproveView ──────────────────────────────────────────


class ApproveViewTests(TestCase):
    def test_admin_required(self):
        view = ProposalApproveView()
        req = _make_request(method="POST", body=b"{}")
        with _patch_admin(False):
            resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 403)

    def test_404_when_proposal_missing_or_other_tenant(self):
        view = ProposalApproveView()
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(None)
            with patch_obj:
                req = _make_request(method="POST", body=b"{}")
                resp = view.post(req, proposal_id=999)
        self.assertEqual(resp.status_code, 404)

    def test_invalid_json_returns_400(self):
        view = ProposalApproveView()
        proposal = _make_proposal()
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch_obj:
                req = _make_request(method="POST", body=b"NOT_JSON")
                resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 400)

    def test_adjustments_must_be_dict(self):
        view = ProposalApproveView()
        proposal = _make_proposal()
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch_obj:
                body = json.dumps({"adjustments": "not_a_dict"}).encode()
                req = _make_request(method="POST", body=body)
                resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 400)

    def test_calls_approve_helper(self):
        view = ProposalApproveView()
        proposal = _make_proposal()
        new_problem = MagicMock(id=88888)
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch(
                "apps.domains.matchup.views_proposal.approve_proposal",
                return_value=new_problem,
            ) as helper:
                with patch_obj:
                    body = json.dumps({"adjustments": {"text": "abc"}}).encode()
                    req = _make_request(method="POST", body=body)
                    resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 200)
        helper.assert_called_once_with(
            proposal.id, req.user, adjustments={"text": "abc"},
        )
        body_json = json.loads(resp.content)
        self.assertEqual(body_json["promoted_problem_id"], 88888)

    def test_helper_error_mapped_to_409(self):
        """ProposalApprovalError (rejected / manual_overlap / 등) → 409."""
        view = ProposalApproveView()
        proposal = _make_proposal()
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch(
                "apps.domains.matchup.views_proposal.approve_proposal",
                side_effect=ProposalApprovalError("manual_overlap"),
            ):
                with patch_obj:
                    req = _make_request(method="POST", body=b"{}")
                    resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 409)
        body_json = json.loads(resp.content)
        self.assertEqual(body_json["code"], "approval_blocked")
        self.assertIn("manual_overlap", body_json["detail"])


# ── ProposalRejectView ──────────────────────────────────────────


class RejectViewTests(TestCase):
    def test_admin_required(self):
        view = ProposalRejectView()
        req = _make_request(method="POST", body=b"{}")
        with _patch_admin(False):
            resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 403)

    def test_404_when_proposal_missing_or_other_tenant(self):
        view = ProposalRejectView()
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(None)
            with patch_obj:
                req = _make_request(method="POST", body=b"{}")
                resp = view.post(req, proposal_id=999)
        self.assertEqual(resp.status_code, 404)

    def test_invalid_json_returns_400(self):
        view = ProposalRejectView()
        proposal = _make_proposal()
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch_obj:
                req = _make_request(method="POST", body=b"NOT_JSON")
                resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 400)

    def test_calls_reject_helper(self):
        view = ProposalRejectView()
        proposal = _make_proposal()
        rejected = _make_proposal(status="rejected", validation_errors=[
            {"code": "incorrect_segmentation", "detail": "test"},
        ])
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch(
                "apps.domains.matchup.views_proposal.reject_proposal",
                return_value=rejected,
            ) as helper:
                with patch_obj:
                    body = json.dumps({
                        "reason": "분리 부정확",
                        "code": "incorrect_segmentation",
                    }).encode()
                    req = _make_request(method="POST", body=body)
                    resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 200)
        helper.assert_called_once_with(
            proposal.id, req.user,
            reason="분리 부정확",
            code="incorrect_segmentation",
        )

    def test_default_reason_and_code_when_body_empty(self):
        view = ProposalRejectView()
        proposal = _make_proposal()
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch(
                "apps.domains.matchup.views_proposal.reject_proposal",
                return_value=proposal,
            ) as helper:
                with patch_obj:
                    req = _make_request(method="POST", body=b"{}")
                    view.post(req, proposal_id=1)
        kwargs = helper.call_args.kwargs
        self.assertEqual(kwargs["reason"], "")
        self.assertEqual(kwargs["code"], "manual_reject")

    def test_helper_error_mapped_to_409(self):
        """approved → reject 시 ProposalApprovalError → 409."""
        view = ProposalRejectView()
        proposal = _make_proposal(status="approved")
        with _patch_admin(True):
            patch_obj, _ = _patch_objects_get(proposal)
            with patch(
                "apps.domains.matchup.views_proposal.reject_proposal",
                side_effect=ProposalApprovalError("approved cannot be rejected"),
            ):
                with patch_obj:
                    req = _make_request(method="POST", body=b"{}")
                    resp = view.post(req, proposal_id=1)
        self.assertEqual(resp.status_code, 409)
        body = json.loads(resp.content)
        self.assertEqual(body["code"], "rejection_blocked")
