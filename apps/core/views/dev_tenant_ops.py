# PATH: apps/core/views/dev_tenant_ops.py
"""
/dev 테넌트별 운영 API (사용량 / 활동 / 임퍼소네이션).
"""
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger(__name__)

from apps.core.models import OpsAuditLog, Program, Tenant, TenantMembership
from apps.core.permissions import IsPlatformAdmin
from apps.core.services.ops_audit import record_audit
from academy.adapters.db.django import repositories_core as core_repo


def _get_tenant_or_404(tenant_id):
    return core_repo.tenant_get_by_id_any(tenant_id)


class DevTenantUsageView(APIView):
    """
    GET /api/v1/core/dev/tenants/<tenant_id>/usage/
    테넌트별 사용량 KPI: 학생/교사/학부모/영상/메시지/결제 요약.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request, tenant_id: int):
        tenant = _get_tenant_or_404(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)

        now = timezone.now()
        d30 = now - timedelta(days=30)

        # 학생/교사/학부모
        try:
            from apps.domains.students.models import Student
            students_total = Student.all_objects.filter(tenant=tenant).count() if hasattr(Student, "all_objects") else Student.objects.filter(tenant=tenant).count()
        except Exception:
            students_total = 0
        try:
            from apps.domains.teachers.models import Teacher
            teachers_total = Teacher.objects.filter(tenant=tenant).count()
        except Exception:
            teachers_total = 0
        try:
            from apps.domains.parents.models import Parent
            parents_total = Parent.objects.filter(tenant=tenant).count()
        except Exception:
            parents_total = 0

        # 영상
        try:
            from apps.domains.video.models import Video
            videos_total = Video.all_with_deleted.filter(tenant=tenant).count()
            videos_active = Video.objects.filter(tenant=tenant).count()
            videos_processing = Video.objects.filter(tenant=tenant, status="PROCESSING").count()
            videos_failed = Video.objects.filter(tenant=tenant, status="FAILED").count()
        except Exception:
            videos_total = videos_active = videos_processing = videos_failed = 0

        # 메시지 (30d)
        try:
            from apps.domains.messaging.models import NotificationLog
            messages_30d = NotificationLog.objects.filter(tenant=tenant, sent_at__gte=d30).count()
            messages_failed_30d = NotificationLog.objects.filter(
                tenant=tenant, sent_at__gte=d30, success=False,
            ).count()
        except Exception:
            messages_30d = messages_failed_30d = 0

        # 결제
        program = core_repo.program_get_by_tenant(tenant)
        billing = None
        if program is not None:
            billing = {
                "plan": program.plan,
                "plan_display": program.get_plan_display(),
                "monthly_price": program.monthly_price,
                "subscription_status": program.subscription_status,
                "subscription_status_display": program.get_subscription_status_display(),
                "subscription_expires_at": str(program.subscription_expires_at) if program.subscription_expires_at else None,
                "next_billing_at": str(program.next_billing_at) if program.next_billing_at else None,
                "days_remaining": program.days_remaining,
                "cancel_at_period_end": program.cancel_at_period_end,
            }

        # 마지막 활동: User.last_login 최댓값
        User = get_user_model()
        last_login = (
            User.objects.filter(tenant=tenant, last_login__isnull=False)
            .order_by("-last_login")
            .values_list("last_login", flat=True)
            .first()
        )

        # 멤버십 카운트 (역할별)
        membership_role_counts = dict(
            TenantMembership.objects.filter(tenant=tenant, is_active=True)
            .values_list("role")
            .annotate(c=Count("id"))
            .values_list("role", "c")
        )

        return Response({
            "tenant": {
                "id": tenant.id,
                "code": tenant.code,
                "name": tenant.name,
                "is_active": tenant.is_active,
            },
            "users": {
                "students": students_total,
                "teachers": teachers_total,
                "parents": parents_total,
                "memberships_by_role": membership_role_counts,
                "last_login_at": last_login.isoformat() if last_login else None,
            },
            "videos": {
                "total": videos_total,
                "active": videos_active,
                "processing": videos_processing,
                "failed": videos_failed,
            },
            "messaging": {
                "sent_30d": messages_30d,
                "failed_30d": messages_failed_30d,
            },
            "billing": billing,
        })


class DevTenantActivityView(APIView):
    """
    GET /api/v1/core/dev/tenants/<tenant_id>/activity/
    해당 테넌트 관련 감사 로그 최근 50건.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request, tenant_id: int):
        tenant = _get_tenant_or_404(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)

        rows = list(
            OpsAuditLog.objects.filter(target_tenant=tenant)
            .order_by("-created_at")[:50]
            .values(
                "id", "created_at", "actor_username", "action", "summary",
                "result", "error", "payload",
            )
        )
        return Response({
            "results": [
                {
                    "id": r["id"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "actor": r["actor_username"] or "—",
                    "action": r["action"],
                    "summary": r["summary"],
                    "result": r["result"],
                    "error": r["error"],
                    "payload": r["payload"],
                }
                for r in rows
            ],
            "count": len(rows),
        })


class DevImpersonateView(APIView):
    """
    POST /api/v1/core/dev/tenants/<tenant_id>/impersonate/
    body: { user_id: int }
    해당 테넌트의 owner/admin/staff/teacher 멤버십 사용자에 대해
    JWT(access/refresh)를 발급한다 (테넌트 격리·감사 로그 필수).

    학생/학부모는 임퍼소네이션 불가 (개인정보 보호).
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    ALLOWED_ROLES = ("owner", "admin", "staff", "teacher")

    def post(self, request, tenant_id: int):
        tenant = _get_tenant_or_404(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)

        user_id = (request.data or {}).get("user_id")
        if not user_id:
            return Response({"detail": "user_id is required."}, status=400)

        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return Response({"detail": "user_id must be an integer."}, status=400)

        membership = (
            TenantMembership.objects.select_related("user")
            .filter(tenant=tenant, user_id=user_id, is_active=True, role__in=self.ALLOWED_ROLES)
            .first()
        )
        if not membership:
            record_audit(
                request,
                action="impersonation.start",
                target_tenant=tenant,
                summary=f"Impersonation denied: user_id={user_id} not staff in {tenant.code}",
                result="failed",
                payload={"user_id": user_id, "reason": "not_staff"},
            )
            return Response(
                {"detail": "지정한 사용자는 이 테넌트의 운영 멤버가 아니거나 비활성 상태입니다."},
                status=403,
            )

        target = membership.user
        if not target.is_active:
            return Response({"detail": "사용자 비활성 상태."}, status=403)

        refresh = RefreshToken.for_user(target)
        # 학원 격리: tenant_id 클레임 필수
        if target.tenant_id is not None:
            refresh["tenant_id"] = target.tenant_id
            refresh.access_token["tenant_id"] = target.tenant_id
        # token_version
        tv = getattr(target, "token_version", 0) or 0
        refresh["token_version"] = tv
        refresh.access_token["token_version"] = tv
        # 임퍼소네이션 흔적
        refresh["impersonated_by"] = getattr(request.user, "id", None)
        refresh.access_token["impersonated_by"] = getattr(request.user, "id", None)

        record_audit(
            request,
            action="impersonation.start",
            target_tenant=tenant,
            target_user=target,
            summary=f"Impersonation: {request.user} -> {target.username} ({tenant.code})",
            payload={"target_user_id": target.id, "target_role": membership.role},
        )

        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "target": {
                "user_id": target.id,
                "username": getattr(target, "username", "") or "",
                "role": membership.role,
                "tenant_id": tenant.id,
                "tenant_code": tenant.code,
            },
        })


class DevTenantStorageView(APIView):
    """
    GET /api/v1/core/dev/tenants/<tenant_id>/storage/?refresh=1
    R2 비디오 버킷에서 tenants/<id>/video/ prefix 아래 모든 객체의 총 바이트·개수.
    실시간 list_objects_v2 호출 비용을 줄이기 위해 10분 캐시 (refresh=1로 강제 재계산).
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    CACHE_TTL = 600  # 10분

    def get(self, request, tenant_id: int):
        tenant = _get_tenant_or_404(tenant_id)
        if not tenant:
            return Response({"detail": "Tenant not found."}, status=404)

        force_refresh = (request.query_params.get("refresh") or "").lower() in ("1", "true", "yes")
        cache_key = f"dev:tenant_storage:{tenant_id}"
        cached = None if force_refresh else cache.get(cache_key)
        if cached is not None:
            return Response({**cached, "cached": True})

        from apps.infrastructure.storage.r2 import sum_size_under_prefix_r2_video

        prefix = f"tenants/{tenant_id}/video/"
        try:
            total_bytes, total_objects = sum_size_under_prefix_r2_video(prefix=prefix)
        except Exception as e:
            logger.exception("sum_size_under_prefix_r2_video failed: tenant_id=%s", tenant_id)
            return Response({"detail": f"R2 listing failed: {e}"}, status=502)

        payload = {
            "tenant_id": tenant_id,
            "tenant_code": tenant.code,
            "prefix": prefix,
            "bytes": total_bytes,
            "objects": total_objects,
            "calculated_at": timezone.now().isoformat(),
            "cache_ttl": self.CACHE_TTL,
        }
        cache.set(cache_key, payload, self.CACHE_TTL)
        return Response({**payload, "cached": False})
