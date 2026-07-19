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
from typing import Optional

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


def _proposal_kind_value(p: ProblemSegmentationProposal) -> str:
    value = getattr(p, "proposal_kind", None)
    return value if value in {"segmentation", "manual_index"} else "segmentation"


def _target_problem_id_value(p: ProblemSegmentationProposal) -> Optional[int]:
    value = getattr(p, "target_problem_id", None)
    return value if isinstance(value, int) else None


def _serialize_proposal(p: ProblemSegmentationProposal) -> dict:
    """[Phase 3.4 internal] raw 모든 필드 응답 — admin/debug 용. 외부 endpoint 응답에는
    `_serialize_proposal_user_v1` (Stage 6.3A) 사용. 본 함수는 backward compat 보존.
    """
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "document_id": p.document_id,
        "analysis_version_key": p.analysis_version_key,
        "proposal_kind": _proposal_kind_value(p),
        "target_problem_id": _target_problem_id_value(p),
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


# ── Stage 6.3A — Proposal Review API v1 (user-friendly, sanitized) ─────


# UI 표시용 status 한글 라벨 (PROPOSAL_REVIEW_UI_WIREFRAME 정의)
_UI_STATUS_LABELS = {
    "pending":      "🟡 검수 대기",
    "needs_review": "⚠️ 검수 필수",
    "rejected":     "🔴 거절",
    "approved":     "🟢 승인 완료",
    "auto_passed":  "🟢 자동 통과",
}

# raw confidence float → high/medium/low 추상화 (학원장 UI 정책)
_CONFIDENCE_HIGH = 0.85
_CONFIDENCE_MED = 0.50

# 영구 차단 / 사용자 결정 필요 conflict codes (validation_errors[*].code)
_CONFLICT_CODE_MANUAL_OVERLAP = "manual_overlap"
_CONFLICT_CODE_NUMBER_CONFLICT = "number_conflict"

# 학원장 UI user_message
_USER_MSG_MANUAL_OVERLAP = (
    "기존 수동 문항과 겹쳐 자동 승인할 수 없습니다"
)
_USER_MSG_NUMBER_CONFLICT = (
    "번호 충돌 — 번호 수정 또는 거절이 필요합니다"
)
_USER_MSG_ALREADY_APPROVED = "이미 승인된 문항입니다"
_USER_MSG_ALREADY_REJECTED = "거절된 문항입니다"
# approvable status 매핑 (proposal_helpers._APPROVABLE_STATUSES mirror)
_APPROVABLE_STATUSES = frozenset({"pending", "needs_review", "auto_passed"})


def _ui_status_label(status: str) -> str:
    return _UI_STATUS_LABELS.get(status, status)


def _confidence_label(value: Optional[float]) -> str:
    """raw confidence float → 'high' / 'medium' / 'low' / 'unknown'."""
    if value is None:
        return "unknown"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if v >= _CONFIDENCE_HIGH:
        return "high"
    if v >= _CONFIDENCE_MED:
        return "medium"
    return "low"


def _validation_errors_have_code(validation_errors, code: str) -> bool:
    for err in validation_errors or []:
        if isinstance(err, dict) and err.get("code") == code:
            return True
    return False


def _detect_conflict_and_actions(p: ProblemSegmentationProposal) -> dict:
    """validation_errors + status 분석 → conflict_type / user_message /
    can_approve / can_reject 도출.

    원칙:
    - manual_overlap → 영구 차단, can_approve=False (proposal_helpers approve_proposal
      가 동일 정책 적용)
    - number_conflict (Stage 6.3N) → can_approve=False, 학원장 결정 필요
    - approved/rejected → can_approve/can_reject False (이미 처리)
    - pending/needs_review/auto_passed + 충돌 없음 → can_approve=True
    """
    errs = p.validation_errors or []
    has_manual_overlap = _validation_errors_have_code(errs, _CONFLICT_CODE_MANUAL_OVERLAP)
    has_number_conflict = _validation_errors_have_code(errs, _CONFLICT_CODE_NUMBER_CONFLICT)

    # status 별 분기
    if p.status == "approved":
        return {
            "conflict_type": None,
            "user_message": _USER_MSG_ALREADY_APPROVED,
            "can_approve": False,
            "can_reject": False,
        }
    if p.status == "rejected":
        if has_manual_overlap:
            return {
                "conflict_type": _CONFLICT_CODE_MANUAL_OVERLAP,
                "user_message": _USER_MSG_MANUAL_OVERLAP,
                "can_approve": False,
                "can_reject": False,    # 이미 rejected — 재처리 불가
            }
        return {
            "conflict_type": None,
            "user_message": _USER_MSG_ALREADY_REJECTED,
            "can_approve": False,
            "can_reject": False,
        }

    # pending / needs_review / auto_passed
    if has_manual_overlap:
        return {
            "conflict_type": _CONFLICT_CODE_MANUAL_OVERLAP,
            "user_message": _USER_MSG_MANUAL_OVERLAP,
            "can_approve": False,
            "can_reject": True,
        }
    if has_number_conflict:
        return {
            "conflict_type": _CONFLICT_CODE_NUMBER_CONFLICT,
            "user_message": _USER_MSG_NUMBER_CONFLICT,
            "can_approve": False,
            "can_reject": True,
        }
    if p.status in _APPROVABLE_STATUSES:
        return {
            "conflict_type": None,
            "user_message": None,
            "can_approve": True,
            "can_reject": True,
        }
    # 알 수 없는 status — 보수적으로 차단
    return {
        "conflict_type": None,
        "user_message": None,
        "can_approve": False,
        "can_reject": False,
    }


def _serialize_proposal_user_v1(p: ProblemSegmentationProposal) -> dict:
    """Stage 6.3A — 학원장/운영자 노출용 sanitized 응답 (Proposal Review API v1).

    숨김 필드 (사용자 directive):
    - paper_type / engine (raw route) / model_version / raw_response
    - raw confidence float (→ confidence_label 추상화)
    - analysis_version_key (internal batch identifier)
    - validation_errors raw list (→ user_message + conflict_type 변환)
    - tenant_id / reviewed_by_id (cross-tenant info leak / admin info)

    노출 필드:
    - id / document_id / page_number / detected_problem_number
    - status (frontend 가 ui_status_label 매핑)
    - ui_status_label (한글)
    - bbox (학원장이 직접 보는 visual 영역)
    - image_key (preview 용 — frontend 가 signed URL 생성)
    - confidence_label (high/medium/low/unknown)
    - conflict_type (None / 'manual_overlap' / 'number_conflict')
    - user_message (한글 — 충돌/상태 설명)
    - can_approve / can_reject (UI 버튼 활성화 신호)
    - promoted_problem_id (승인 후 결과 추적)
    - reviewed_at / created_at
    """
    actions = _detect_conflict_and_actions(p)
    proposal_kind = _proposal_kind_value(p)
    raw_response = p.raw_response if isinstance(p.raw_response, dict) else {}
    return {
        "id": p.id,
        "proposal_kind": proposal_kind,
        "target_problem_id": _target_problem_id_value(p),
        "document_id": p.document_id,
        "page_number": p.page_number,
        "detected_problem_number": p.detected_problem_number,
        "status": p.status,
        "ui_status_label": _ui_status_label(p.status),
        "bbox": p.bbox,
        "image_key": p.image_key,
        "confidence_label": _confidence_label(p.confidence),
        "conflict_type": actions["conflict_type"],
        "user_message": actions["user_message"],
        "can_approve": actions["can_approve"],
        "can_reject": actions["can_reject"],
        "promoted_problem_id": p.promoted_problem_id,
        "proposed_text": str(raw_response.get("text") or "") if proposal_kind == "manual_index" else "",
        "proposed_format": str(raw_response.get("format") or "") if proposal_kind == "manual_index" else "",
        "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        "created_at": p.created_at.isoformat(),
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
            "proposals": [_serialize_proposal_user_v1(p) for p in proposals],
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
        return JsonResponse(_serialize_proposal_user_v1(p))


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
            "proposal": _serialize_proposal_user_v1(proposal),
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
        return JsonResponse({"proposal": _serialize_proposal_user_v1(proposal)})
