# PATH: apps/domains/matchup/views_proposal.py
# Stage 3 Phase 3.4 — ProblemSegmentationProposal admin API.
#
# 검수 큐 / 단건 / approve / reject endpoint. proposal_helpers Phase 3.3 helper 재사용.
# permission: _is_tenant_admin (학원 owner/admin 만 — 매치업 보고서 검수와 동일 권한).
#
# 원칙 (사용자 directive):
# - selected_problem_ids 미접근.
# - 기존 manual=true MatchupProblem 변경 X.
# - approve_proposal / reject_proposal helper 가 transaction.atomic + select_for_update
#   를 자체 보장 — view 는 입력 검증 + 권한 + 응답만.
# - tenant 격리: 자기 tenant proposal 외 access 차단 (DoesNotExist).
from __future__ import annotations

import json
import logging

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import ProblemSegmentationProposal
from .proposal_helpers import (
    ProposalApprovalError,
    approve_proposal,
    reject_proposal,
)
from .views import _is_tenant_admin, _jwt_required, _tenant_required

logger = logging.getLogger(__name__)


def _serialize_proposal(p: ProblemSegmentationProposal) -> dict:
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "document_id": p.document_id,
        "analysis_version_key": p.analysis_version_key,
        "page_number": p.page_number,
        "detected_problem_number": p.detected_problem_number,
        "engine": p.engine,
        "model_version": p.model_version,
        "confidence": p.confidence,
        "status": p.status,
        "image_key": p.image_key,
        "bbox": p.bbox,
        "validation_errors": p.validation_errors,
        "promoted_problem_id": p.promoted_problem_id,
        "reviewed_by_id": p.reviewed_by_id,
        "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _parse_int_arg(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProposalListView(View):
    """GET /api/v1/matchup/proposals/

    검수 큐. tenant 격리. admin/owner 만.

    Query params:
      document_id          (optional, int)
      status               (optional, pending|needs_review|rejected|approved|auto_passed)
      engine               (optional, yolo|vlm|ocr|native_pdf|manual_assist)
      analysis_version_key (optional, str — 같은 batch 그룹화)
      limit                (default 50, max 200)
      offset               (default 0)

    Response:
      {
        "proposals": [...],
        "total": int, "limit": int, "offset": int,
      }
    """

    def get(self, request):
        if not _is_tenant_admin(request):
            return JsonResponse({"detail": "Admin only"}, status=403)

        qs = ProblemSegmentationProposal.objects.filter(tenant=request.tenant)

        document_id = _parse_int_arg(request.GET.get("document_id"))
        if document_id is not None:
            qs = qs.filter(document_id=document_id)
        elif request.GET.get("document_id") not in (None, ""):
            return JsonResponse({"detail": "invalid document_id"}, status=400)

        status_f = (request.GET.get("status") or "").strip()
        if status_f:
            valid = dict(ProblemSegmentationProposal.STATUS_CHOICES)
            if status_f not in valid:
                return JsonResponse({"detail": f"invalid status (allowed: {list(valid)})"}, status=400)
            qs = qs.filter(status=status_f)

        engine_f = (request.GET.get("engine") or "").strip()
        if engine_f:
            valid = dict(ProblemSegmentationProposal.ENGINE_CHOICES)
            if engine_f not in valid:
                return JsonResponse({"detail": f"invalid engine (allowed: {list(valid)})"}, status=400)
            qs = qs.filter(engine=engine_f)

        version_f = (request.GET.get("analysis_version_key") or "").strip()
        if version_f:
            qs = qs.filter(analysis_version_key=version_f)

        limit = _parse_int_arg(request.GET.get("limit"), default=50) or 50
        limit = max(1, min(limit, 200))
        offset = _parse_int_arg(request.GET.get("offset"), default=0) or 0
        offset = max(0, offset)

        total = qs.count()
        proposals = list(qs.order_by("-created_at")[offset : offset + limit])

        return JsonResponse({
            "proposals": [_serialize_proposal(p) for p in proposals],
            "total": total,
            "limit": limit,
            "offset": offset,
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProposalDetailView(View):
    """GET /api/v1/matchup/proposals/{proposal_id}/"""

    def get(self, request, proposal_id: int):
        if not _is_tenant_admin(request):
            return JsonResponse({"detail": "Admin only"}, status=403)
        try:
            p = ProblemSegmentationProposal.objects.get(
                id=proposal_id, tenant=request.tenant,
            )
        except ProblemSegmentationProposal.DoesNotExist:
            return JsonResponse({"detail": "not found"}, status=404)
        return JsonResponse(_serialize_proposal(p))


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProposalApproveView(View):
    """POST /api/v1/matchup/proposals/{proposal_id}/approve/

    body (optional):
      {"adjustments": {"bbox": ..., "text": str, "image_key": str, "embedding": [..]}}

    response:
      200 → {"proposal": {...}, "promoted_problem_id": int}
      403 Admin only
      404 not found
      409 approval_blocked (rejected / already approved / manual_overlap / adjusted bbox overlap)
      400 invalid body
    """

    def post(self, request, proposal_id: int):
        if not _is_tenant_admin(request):
            return JsonResponse({"detail": "Admin only"}, status=403)

        # tenant 격리 사전 검증 — 자기 tenant proposal 만.
        try:
            proposal = ProblemSegmentationProposal.objects.get(
                id=proposal_id, tenant=request.tenant,
            )
        except ProblemSegmentationProposal.DoesNotExist:
            return JsonResponse({"detail": "not found"}, status=404)

        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"detail": "invalid json"}, status=400)

        adjustments = body.get("adjustments")
        if adjustments is not None and not isinstance(adjustments, dict):
            return JsonResponse({"detail": "adjustments must be object"}, status=400)

        try:
            new_problem = approve_proposal(
                proposal.id, request.user, adjustments=adjustments,
            )
        except ProposalApprovalError as e:
            return JsonResponse(
                {"detail": str(e), "code": "approval_blocked"}, status=409,
            )

        # proposal refetch — approve_proposal 안에서 status/reviewed_at 갱신됨.
        proposal.refresh_from_db()
        logger.info(
            "ProposalApproveView | proposal=%s → problem=%s | tenant=%s | by_user=%s",
            proposal.id, new_problem.id, request.tenant.id,
            getattr(request.user, "id", None),
        )
        return JsonResponse({
            "proposal": _serialize_proposal(proposal),
            "promoted_problem_id": new_problem.id,
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProposalRejectView(View):
    """POST /api/v1/matchup/proposals/{proposal_id}/reject/

    body:
      {"reason": "분리 부정확", "code": "incorrect_segmentation"}

    response:
      200 → {"proposal": {...}}
      403 Admin only
      404 not found
      409 rejection_blocked (이미 approved)
      400 invalid body
    """

    def post(self, request, proposal_id: int):
        if not _is_tenant_admin(request):
            return JsonResponse({"detail": "Admin only"}, status=403)

        try:
            ProblemSegmentationProposal.objects.get(
                id=proposal_id, tenant=request.tenant,
            )
        except ProblemSegmentationProposal.DoesNotExist:
            return JsonResponse({"detail": "not found"}, status=404)

        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"detail": "invalid json"}, status=400)

        reason = str(body.get("reason") or "").strip()
        code = str(body.get("code") or "manual_reject").strip()

        try:
            proposal = reject_proposal(
                proposal_id, request.user, reason=reason, code=code,
            )
        except ProposalApprovalError as e:
            return JsonResponse(
                {"detail": str(e), "code": "rejection_blocked"}, status=409,
            )

        logger.info(
            "ProposalRejectView | proposal=%s | tenant=%s | by_user=%s | code=%s",
            proposal_id, request.tenant.id,
            getattr(request.user, "id", None), code,
        )
        return JsonResponse({"proposal": _serialize_proposal(proposal)})
