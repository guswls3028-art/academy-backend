"""학부모 후기 제출 + 승인 큐 + 공개 list (testimonials 섹션).

분리 출처: apps/core/views_landing.py:611-739 (P1 audit step 3, 2026-05-14).

4 view + validate:
- LandingTestimonialPublicView: POST (학부모 공개 제출, honeypot + validate)
- LandingTestimonialPublicListView: GET 외부 공개 list (approved 만)
- LandingTestimonialAdminListView: 학원장 승인 큐
- LandingTestimonialAdminDetailView: 학원장 승인/거절
"""
from __future__ import annotations

import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolved, TenantResolvedAndStaff

from ._helpers import check_landing_admin_role, client_ip

logger = logging.getLogger(__name__)


TEST_NAME_MAX = 50
TEST_ROLE_MAX = 80
TEST_TEXT_MAX = 1000


def validate_testimonial(data: dict) -> list[str]:
    """학부모 후기 제출 검증."""
    errs: list[str] = []
    name = str(data.get("name") or "").strip()
    text = str(data.get("text") or "").strip()
    role = str(data.get("role") or "").strip()
    if not name or len(name) > TEST_NAME_MAX:
        errs.append(f"이름은 1~{TEST_NAME_MAX}자여야 합니다.")
    if not text or len(text) < 10:
        errs.append("후기는 10자 이상 입력해주세요.")
    if len(text) > TEST_TEXT_MAX:
        errs.append(f"후기는 {TEST_TEXT_MAX}자 이내여야 합니다.")
    if role and len(role) > TEST_ROLE_MAX:
        errs.append(f"학년/관계는 {TEST_ROLE_MAX}자 이내여야 합니다.")
    return errs


class LandingTestimonialPublicView(APIView):
    """POST /api/v1/core/landing/testimonial/ — 학부모 직접 후기 제출. honeypot + validate."""
    permission_classes = [TenantResolved]
    authentication_classes = []

    def post(self, request):
        if (request.data or {}).get("website") or (request.data or {}).get("hp"):
            logger.info("TESTIMONIAL_HONEYPOT_TRAP ip=%s", client_ip(request))
            return Response({"id": 0, "ok": True}, status=201)

        # testimonial 은 phone 필드 없음 — rate limit 적용 불가. 학원장이 검수 stage 에서 reject.
        # spam 보호 본질은 honeypot + validate 로 차단. 본격 dedup (name+text hash) 별 cycle.

        errs = validate_testimonial(request.data or {})
        if errs:
            return Response({"detail": errs}, status=400)

        from apps.core.models import LandingTestimonialSubmission
        obj = LandingTestimonialSubmission.objects.create(
            tenant=request.tenant,
            name=str(request.data.get("name") or "").strip()[:TEST_NAME_MAX],
            role=str(request.data.get("role") or "").strip()[:TEST_ROLE_MAX],
            text=str(request.data.get("text") or "").strip()[:TEST_TEXT_MAX],
            status=LandingTestimonialSubmission.Status.PENDING,
        )
        logger.info("LandingTestimonialSubmission created tenant=%s id=%s", request.tenant.id, obj.id)
        return Response({"id": obj.id, "ok": True}, status=201)


class LandingTestimonialAdminListView(APIView):
    """GET /api/v1/core/landing/admin/testimonial/ — 학원장 후기 승인 큐."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("후기 승인은 원장/관리자만 가능합니다.")

    def get(self, request):
        from apps.core.models import LandingTestimonialSubmission
        status_filter = (request.GET.get("status") or "").strip()
        qs = LandingTestimonialSubmission.objects.filter(tenant=request.tenant)
        if status_filter in ("pending", "approved", "rejected"):
            qs = qs.filter(status=status_filter)
        items = [{
            "id": r.id, "name": r.name, "role": r.role, "text": r.text,
            "status": r.status,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "created_at": r.created_at.isoformat(),
        } for r in qs[:200]]
        all_qs = LandingTestimonialSubmission.objects.filter(tenant=request.tenant)
        return Response({
            "items": items,
            "summary": {
                "total": all_qs.count(),
                "pending": all_qs.filter(status="pending").count(),
                "approved": all_qs.filter(status="approved").count(),
            },
        })


class LandingTestimonialAdminDetailView(APIView):
    """PATCH /api/v1/core/landing/admin/testimonial/<id>/ — 승인/거절."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("후기 승인은 원장/관리자만 가능합니다.")

    def patch(self, request, item_id):
        from apps.core.models import LandingTestimonialSubmission
        from django.utils import timezone
        try:
            r = LandingTestimonialSubmission.objects.get(id=item_id, tenant=request.tenant)
        except LandingTestimonialSubmission.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)
        new_status = (request.data or {}).get("status")
        if new_status not in ("approved", "rejected", "pending"):
            return Response({"detail": "유효하지 않은 status"}, status=400)
        r.status = new_status
        r.reviewed_at = timezone.now() if new_status in ("approved", "rejected") else None
        r.reviewed_by = request.user if new_status in ("approved", "rejected") else None
        r.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
        return Response({"ok": True})


class LandingTestimonialPublicListView(APIView):
    """GET /api/v1/core/landing/testimonial/public/ — 외부에 노출되는 승인된 후기."""
    permission_classes = [TenantResolved]
    authentication_classes = []

    def get(self, request):
        from apps.core.models import LandingTestimonialSubmission
        qs = LandingTestimonialSubmission.objects.filter(
            tenant=request.tenant, status=LandingTestimonialSubmission.Status.APPROVED,
        ).order_by("-created_at")[:30]
        items = [{
            "id": r.id, "name": r.name, "role": r.role, "text": r.text,
        } for r in qs]
        return Response({"items": items})
