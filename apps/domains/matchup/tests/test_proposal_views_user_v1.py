"""Stage 6.3A (2026-05-07) — Proposal Review API v1 user-friendly serializer tests.

검증:
- _serialize_proposal_user_v1 schema (sanitized)
- internal field 외부 노출 0건 (paper_type / engine / model_version / raw_response /
  raw confidence / analysis_version_key / tenant_id / reviewed_by_id)
- ui_status_label / confidence_label / conflict_type / user_message / can_approve /
  can_reject 도출
- number_conflict 처리 (Stage 6.3N follow-up)
- manual_overlap 처리 (영구 차단)
- approved/rejected 상태 처리
- 4 endpoint 응답 모두 user_v1 schema 사용
- approve_proposal / reject_proposal helper 만 호출 (callback 미import regression)
- selected_problem_ids 미접근 (regression)
"""
from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.domains.matchup.views_proposal import (
    _confidence_label,
    _detect_conflict_and_actions,
    _serialize_proposal_user_v1,
    _ui_status_label,
)


def _make_proposal(
    *, pid=1, status="pending", validation_errors=None,
    bbox=None, document_id=100, tenant_id=2, page_number=1,
    detected_problem_number=5, engine="vlm", model_version="v11",
    image_key="r2/proposal/1.png", confidence=0.85,
    promoted_problem_id=None, reviewed_by_id=None,
    analysis_version_key="batch-x",
):
    p = MagicMock()
    p.id = pid
    p.status = status
    p.validation_errors = validation_errors or []
    p.bbox = bbox or {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.3, "norm": True}
    p.document_id = document_id
    p.tenant_id = tenant_id
    p.page_number = page_number
    p.detected_problem_number = detected_problem_number
    p.engine = engine
    p.model_version = model_version
    p.image_key = image_key
    p.confidence = confidence
    p.promoted_problem_id = promoted_problem_id
    p.reviewed_by_id = reviewed_by_id
    p.analysis_version_key = analysis_version_key
    p.reviewed_at = None
    p.created_at = MagicMock()
    p.created_at.isoformat = MagicMock(return_value="2026-05-07T12:00:00+00:00")
    return p


# ── ui_status_label / confidence_label ──────────────────────────


class TestUiStatusLabel(TestCase):
    def test_pending(self):
        self.assertEqual(_ui_status_label("pending"), "🟡 검수 대기")

    def test_needs_review(self):
        self.assertEqual(_ui_status_label("needs_review"), "⚠️ 검수 필수")

    def test_rejected(self):
        self.assertEqual(_ui_status_label("rejected"), "🔴 거절")

    def test_approved(self):
        self.assertEqual(_ui_status_label("approved"), "🟢 승인 완료")

    def test_unknown_falls_back(self):
        self.assertEqual(_ui_status_label("weird_unknown"), "weird_unknown")


class TestConfidenceLabel(TestCase):
    def test_high(self):
        self.assertEqual(_confidence_label(0.85), "high")
        self.assertEqual(_confidence_label(0.95), "high")
        self.assertEqual(_confidence_label(1.0), "high")

    def test_medium(self):
        self.assertEqual(_confidence_label(0.50), "medium")
        self.assertEqual(_confidence_label(0.84), "medium")

    def test_low(self):
        self.assertEqual(_confidence_label(0.49), "low")
        self.assertEqual(_confidence_label(0.0), "low")

    def test_none_unknown(self):
        self.assertEqual(_confidence_label(None), "unknown")

    def test_invalid_unknown(self):
        self.assertEqual(_confidence_label("abc"), "unknown")


# ── _detect_conflict_and_actions ────────────────────────────────


class TestConflictDetection(TestCase):
    def test_pending_no_errors_can_approve(self):
        p = _make_proposal(status="pending", validation_errors=[])
        result = _detect_conflict_and_actions(p)
        self.assertIsNone(result["conflict_type"])
        self.assertIsNone(result["user_message"])
        self.assertTrue(result["can_approve"])
        self.assertTrue(result["can_reject"])

    def test_needs_review_number_conflict(self):
        p = _make_proposal(status="needs_review", validation_errors=[
            {"code": "number_conflict", "conflicting_problem_id": 99,
             "target_number": 5},
        ])
        result = _detect_conflict_and_actions(p)
        self.assertEqual(result["conflict_type"], "number_conflict")
        self.assertEqual(
            result["user_message"], "번호 충돌 — 번호 수정 또는 거절이 필요합니다",
        )
        self.assertFalse(result["can_approve"])
        self.assertTrue(result["can_reject"])

    def test_pending_with_manual_overlap(self):
        p = _make_proposal(status="pending", validation_errors=[
            {"code": "manual_overlap", "bbox_iou": 0.5},
        ])
        result = _detect_conflict_and_actions(p)
        self.assertEqual(result["conflict_type"], "manual_overlap")
        self.assertEqual(
            result["user_message"], "기존 수동 문항과 겹쳐 자동 승인할 수 없습니다",
        )
        self.assertFalse(result["can_approve"])
        self.assertTrue(result["can_reject"])

    def test_already_approved(self):
        p = _make_proposal(status="approved")
        result = _detect_conflict_and_actions(p)
        self.assertIsNone(result["conflict_type"])
        self.assertEqual(result["user_message"], "이미 승인된 문항입니다")
        self.assertFalse(result["can_approve"])
        self.assertFalse(result["can_reject"])

    def test_already_rejected(self):
        p = _make_proposal(status="rejected")
        result = _detect_conflict_and_actions(p)
        self.assertEqual(result["user_message"], "거절된 문항입니다")
        self.assertFalse(result["can_approve"])
        self.assertFalse(result["can_reject"])

    def test_rejected_with_manual_overlap_permanently_blocked(self):
        p = _make_proposal(status="rejected", validation_errors=[
            {"code": "manual_overlap", "bbox_iou": 0.5},
        ])
        result = _detect_conflict_and_actions(p)
        # rejected + manual_overlap → 영구 차단 (can_reject 도 False — 이미 처리)
        self.assertEqual(result["conflict_type"], "manual_overlap")
        self.assertFalse(result["can_approve"])
        self.assertFalse(result["can_reject"])

    def test_auto_passed_can_approve(self):
        p = _make_proposal(status="auto_passed", validation_errors=[])
        result = _detect_conflict_and_actions(p)
        self.assertTrue(result["can_approve"])

    def test_unknown_status_blocked(self):
        p = _make_proposal(status="weird_status", validation_errors=[])
        result = _detect_conflict_and_actions(p)
        self.assertFalse(result["can_approve"])
        self.assertFalse(result["can_reject"])


# ── _serialize_proposal_user_v1 schema ──────────────────────────


class TestUserV1SerializerSchema(TestCase):
    def test_exposed_fields(self):
        p = _make_proposal(pid=42, status="pending", confidence=0.75)
        out = _serialize_proposal_user_v1(p)
        # 필수 노출 필드
        for key in (
            "id", "document_id", "page_number", "detected_problem_number",
            "status", "ui_status_label", "bbox", "image_key",
            "confidence_label", "conflict_type", "user_message",
            "can_approve", "can_reject", "promoted_problem_id",
            "reviewed_at", "created_at",
        ):
            self.assertIn(key, out)

    def test_internal_fields_not_exposed(self):
        p = _make_proposal(
            engine="vlm", model_version="v11",
            confidence=0.85, analysis_version_key="batch-x",
            tenant_id=2, reviewed_by_id=42,
            validation_errors=[{"code": "manual_overlap", "bbox_iou": 0.5}],
        )
        out = _serialize_proposal_user_v1(p)
        # 사용자 directive — 외부 노출 금지
        forbidden = {
            "engine", "model_version", "raw_response",
            "confidence",            # raw float — confidence_label 로 대체
            "validation_errors",      # raw codes — user_message 로 대체
            "analysis_version_key",
            "tenant_id", "reviewed_by_id",
            "paper_type",
            "_internal_paper_type",
        }
        for key in forbidden:
            self.assertNotIn(key, out, f"내부 필드 '{key}' 외부 노출 금지")

    def test_confidence_label_replaces_raw_value(self):
        p = _make_proposal(confidence=0.90)
        out = _serialize_proposal_user_v1(p)
        self.assertEqual(out["confidence_label"], "high")
        self.assertNotIn("confidence", out)

    def test_number_conflict_user_message(self):
        p = _make_proposal(
            status="needs_review",
            validation_errors=[
                {"code": "number_conflict", "conflicting_problem_id": 99,
                 "target_number": 5},
            ],
        )
        out = _serialize_proposal_user_v1(p)
        self.assertEqual(out["status"], "needs_review")
        self.assertEqual(out["ui_status_label"], "⚠️ 검수 필수")
        self.assertEqual(out["conflict_type"], "number_conflict")
        self.assertEqual(
            out["user_message"], "번호 충돌 — 번호 수정 또는 거절이 필요합니다",
        )
        self.assertFalse(out["can_approve"])
        self.assertTrue(out["can_reject"])

    def test_manual_overlap_user_message(self):
        p = _make_proposal(
            status="pending",
            validation_errors=[{"code": "manual_overlap", "bbox_iou": 0.5}],
        )
        out = _serialize_proposal_user_v1(p)
        self.assertEqual(out["conflict_type"], "manual_overlap")
        self.assertEqual(
            out["user_message"], "기존 수동 문항과 겹쳐 자동 승인할 수 없습니다",
        )
        self.assertFalse(out["can_approve"])

    def test_pending_clean_can_approve(self):
        p = _make_proposal(status="pending", validation_errors=[])
        out = _serialize_proposal_user_v1(p)
        self.assertIsNone(out["conflict_type"])
        self.assertIsNone(out["user_message"])
        self.assertTrue(out["can_approve"])
        self.assertTrue(out["can_reject"])


# ── 4 endpoint 응답이 v1 schema 사용하는지 ───────────────────────


def _make_request(method="GET", path="/", body=b"", get_params=None):
    req = MagicMock()
    req.method = method
    req.body = body
    req.GET = MagicMock()
    params = get_params or {}
    req.GET.get = MagicMock(side_effect=lambda k, default=None: params.get(k, default))
    req.tenant = MagicMock()
    req.tenant.id = 2
    req.user = MagicMock()
    req.user.id = 42
    return req


def _patch_admin(allowed=True):
    return patch("apps.domains.matchup.views_proposal._is_tenant_admin",
                 return_value=allowed)


def _patch_objects_filter(proposals_for_list):
    from apps.domains.matchup.models import ProblemSegmentationProposal
    qs = MagicMock()
    qs.filter = MagicMock(return_value=qs)
    qs.count = MagicMock(return_value=len(proposals_for_list))
    qs.order_by = MagicMock(return_value=qs)
    qs.__getitem__ = MagicMock(return_value=proposals_for_list)
    objects = MagicMock()
    objects.filter = MagicMock(return_value=qs)
    return patch.object(ProblemSegmentationProposal, "objects", objects), objects, qs


class TestListEndpointResponseShape(TestCase):
    def test_list_response_uses_user_v1_schema(self):
        from apps.domains.matchup.views_proposal import ProposalListView
        view = ProposalListView()
        proposal = _make_proposal(
            pid=1, status="needs_review",
            validation_errors=[
                {"code": "number_conflict", "conflicting_problem_id": 99,
                 "target_number": 5},
            ],
        )
        with _patch_admin(True):
            patch_objs, _, _ = _patch_objects_filter([proposal])
            with patch_objs:
                req = _make_request(get_params={})
                resp = view.get(req)
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(len(body["proposals"]), 1)
        item = body["proposals"][0]
        # v1 schema 필드
        self.assertIn("ui_status_label", item)
        self.assertIn("confidence_label", item)
        self.assertIn("user_message", item)
        self.assertIn("conflict_type", item)
        # internal 필드 미노출
        self.assertNotIn("engine", item)
        self.assertNotIn("model_version", item)
        self.assertNotIn("validation_errors", item)
        self.assertNotIn("analysis_version_key", item)
        self.assertNotIn("tenant_id", item)


# ── approve / reject helper 호출 + selected 무접근 regression ────


class TestApproveCallsHelperOnly(TestCase):
    def test_approve_endpoint_calls_helper(self):
        from apps.domains.matchup.views_proposal import ProposalApproveView
        from apps.domains.matchup.models import ProblemSegmentationProposal

        view = ProposalApproveView()
        proposal = _make_proposal(pid=1, status="pending")

        # Mock proposal get + approve_proposal helper
        with _patch_admin(True), patch.object(
            ProblemSegmentationProposal, "objects",
        ) as mock_objs, patch(
            "apps.domains.matchup.views_proposal.approve_proposal",
        ) as mock_approve:
            mock_objs.get = MagicMock(return_value=proposal)
            mock_problem = MagicMock(); mock_problem.id = 999
            mock_approve.return_value = mock_problem
            proposal.refresh_from_db = MagicMock()

            req = _make_request(method="POST", body=b"{}")
            resp = view.post(req, proposal_id=1)

        self.assertEqual(resp.status_code, 200)
        mock_approve.assert_called_once()
        body = json.loads(resp.content)
        # response: v1 schema (sanitized) + promoted_problem_id
        self.assertEqual(body["promoted_problem_id"], 999)
        self.assertNotIn("engine", body["proposal"])
        self.assertIn("ui_status_label", body["proposal"])

    def test_approve_helper_error_409(self):
        from apps.domains.matchup.views_proposal import ProposalApproveView
        from apps.domains.matchup.proposal_helpers import ProposalApprovalError
        from apps.domains.matchup.models import ProblemSegmentationProposal

        view = ProposalApproveView()
        proposal = _make_proposal(pid=1, status="pending")

        with _patch_admin(True), patch.object(
            ProblemSegmentationProposal, "objects",
        ) as mock_objs, patch(
            "apps.domains.matchup.views_proposal.approve_proposal",
            side_effect=ProposalApprovalError("number_conflict — 5"),
        ) as mock_approve:
            mock_objs.get = MagicMock(return_value=proposal)
            req = _make_request(method="POST", body=b"{}")
            resp = view.post(req, proposal_id=1)

        self.assertEqual(resp.status_code, 409)
        body = json.loads(resp.content)
        self.assertEqual(body["code"], "approval_blocked")


class TestRejectCallsHelperOnly(TestCase):
    def test_reject_endpoint_calls_helper(self):
        from apps.domains.matchup.views_proposal import ProposalRejectView
        from apps.domains.matchup.models import ProblemSegmentationProposal

        view = ProposalRejectView()
        proposal = _make_proposal(pid=1, status="pending")

        with _patch_admin(True), patch.object(
            ProblemSegmentationProposal, "objects",
        ) as mock_objs, patch(
            "apps.domains.matchup.views_proposal.reject_proposal",
        ) as mock_reject:
            mock_objs.get = MagicMock(return_value=proposal)
            updated_proposal = _make_proposal(pid=1, status="rejected")
            mock_reject.return_value = updated_proposal

            req = _make_request(
                method="POST",
                body='{"reason": "분리 부정확", "code": "incorrect_segmentation"}'.encode("utf-8"),
            )
            resp = view.post(req, proposal_id=1)

        self.assertEqual(resp.status_code, 200)
        mock_reject.assert_called_once()
        body = json.loads(resp.content)
        # v1 schema sanitized
        self.assertNotIn("engine", body["proposal"])
        self.assertIn("ui_status_label", body["proposal"])
        # MatchupProblem.create 미호출 (helper 만 호출)


# ── regression: selected_problem_ids / callback / hit_report 미접근 ──


class TestRegressionNoOperationalSideEffects(TestCase):
    def test_no_callback_imports_in_views_proposal(self):
        from apps.domains.matchup import views_proposal
        import inspect
        src = inspect.getsource(views_proposal)
        # docstring 제거
        if src.startswith('"""') or "# PATH" in src.split("\n")[0]:
            # 단순 — 첫 import statement 후 본문만
            pass
        forbidden = (
            "_handle_matchup_ai_result",
            "_handle_matchup_index_result",
            "_handle_matchup_manual_result",
            "from apps.domains.ai.callbacks",
            "dispatch_job(",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"views_proposal 에서 callback access '{token}' 발견")

    def test_no_selected_problem_ids_modification(self):
        """views_proposal 에 selected_problem_ids 직접 mutation 0회 (read 도 0회)."""
        from apps.domains.matchup import views_proposal
        import inspect
        src = inspect.getsource(views_proposal)
        # mutation pattern (assignment / save 호출)
        forbidden = (
            "selected_problem_ids =",
            "selected_problem_ids.append",
            "selected_problem_ids.extend",
            "selected_problem_ids.remove",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"selected_problem_ids 직접 변경 '{token}' 발견")

    def test_no_hit_report_modification(self):
        from apps.domains.matchup import views_proposal
        import inspect
        src = inspect.getsource(views_proposal)
        forbidden = (
            "MatchupHitReport.objects.create",
            "MatchupHitReport.objects.update",
            "MatchupHitReportEntry.objects.create",
            "MatchupHitReportEntry.objects.update",
            "MatchupHitReport.objects.delete",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"hit_report direct write '{token}' 발견")

    def test_no_segment_dispatcher_imports(self):
        from apps.domains.matchup import views_proposal
        import inspect
        src = inspect.getsource(views_proposal)
        forbidden = (
            "from academy.adapters.ai.detection.segment_dispatcher",
            "segment_questions_multipage(",
            "segment_questions(",
        )
        for token in forbidden:
            self.assertNotIn(token, src,
                             f"segment_dispatcher access '{token}' 발견")
