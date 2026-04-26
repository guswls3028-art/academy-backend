# PATH: apps/core/views/dev_dashboard.py
"""
/dev 운영 콘솔 대시보드 종합 summary API.

단일 호출로 KPI/활동/감사로그를 한 번에 반환 — 대시보드 첫화면 N+1 차단.
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Invoice
from apps.core.models import OpsAuditLog, Program, Tenant
from apps.core.permissions import IsPlatformAdmin


class DevDashboardSummaryView(APIView):
    """
    GET /api/v1/core/dev/dashboard/
    플랫폼 운영 대시보드 종합 KPI + 최근 감사 로그 + 30일 신규 가입 시리즈.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        now = timezone.now()
        today = timezone.localdate()
        d7 = now - timedelta(days=7)
        d24 = now - timedelta(hours=24)
        d30 = now - timedelta(days=30)

        exempt_ids = list(getattr(settings, "BILLING_EXEMPT_TENANT_IDS", []) or [])
        owner_id = getattr(settings, "OWNER_TENANT_ID", None)
        if owner_id is not None and owner_id not in exempt_ids:
            exempt_ids.append(owner_id)

        # ── Tenants ──
        # Tenant 모델은 created_at 필드 미보유 → Program(자동 생성됨)의 created_at을 대용.
        tenant_qs = Tenant.objects.exclude(id__in=exempt_ids)
        tenants_total = tenant_qs.count()
        tenants_active = tenant_qs.filter(is_active=True).count()
        tenants_new_7d = (
            Program.objects.exclude(tenant_id__in=exempt_ids)
            .filter(created_at__gte=d7)
            .count()
        )

        # ── Billing ──
        program_qs = Program.objects.exclude(tenant_id__in=exempt_ids)
        mrr = program_qs.filter(subscription_status="active").aggregate(
            total=Sum("monthly_price"),
        )["total"] or 0
        expiring_7d = program_qs.filter(
            subscription_status="active",
            subscription_expires_at__lte=today + timedelta(days=7),
            subscription_expires_at__gte=today,
        ).count()
        overdue_invoices = Invoice.objects.filter(
            status__in=["OVERDUE", "FAILED"],
        ).exclude(tenant_id__in=exempt_ids).count()
        paid_30d = Invoice.objects.filter(
            status="PAID",
            paid_at__gte=d30,
        ).exclude(tenant_id__in=exempt_ids).aggregate(total=Sum("total_amount"))["total"] or 0

        # ── Inbox ──
        try:
            from apps.domains.community.models.post import PostEntity
            inbox_qs = PostEntity.objects.filter(
                post_type="board",
            ).filter(Q(title__startswith="[BUG]") | Q(title__startswith="[FB]"))
            inbox_total = inbox_qs.count()
            inbox_unanswered = inbox_qs.annotate(
                _rc=Count("replies"),
            ).filter(_rc=0).count()
        except Exception:
            inbox_total = 0
            inbox_unanswered = 0

        # ── Users ──
        users_total = User.objects.filter(is_active=True).count()
        users_signups_7d = User.objects.filter(date_joined__gte=d7).count()

        # ── Audit ──
        audit_failed_24h = OpsAuditLog.objects.filter(
            created_at__gte=d24, result="failed",
        ).count()
        recent_audit = list(
            OpsAuditLog.objects.select_related("target_tenant", "target_user")
            .order_by("-created_at")[:10]
            .values(
                "id", "created_at", "actor_username", "action", "summary", "result",
                "target_tenant__code", "target_tenant__name",
            )
        )
        recent_audit_data = [
            {
                "id": r["id"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "actor": r["actor_username"] or "—",
                "action": r["action"],
                "summary": r["summary"],
                "result": r["result"],
                "tenant_code": r["target_tenant__code"],
                "tenant_name": r["target_tenant__name"],
            }
            for r in recent_audit
        ]

        # ── Maintenance ──
        maint_qs = Program.objects.exclude(tenant_id__in=exempt_ids)
        maint_total = maint_qs.count()
        maint_enabled = maint_qs.filter(feature_flags__maintenance_mode=True).count()

        # ── 30d signups series (Program created_at 기준) ──
        from django.db.models.functions import TruncDate
        signup_series = (
            Program.objects.exclude(tenant_id__in=exempt_ids)
            .filter(created_at__gte=d30)
            .annotate(d=TruncDate("created_at"))
            .values("d")
            .annotate(c=Count("id"))
            .order_by("d")
        )
        series = [
            {"date": str(row["d"]), "count": row["c"]}
            for row in signup_series
        ]

        return Response({
            "tenants": {
                "total": tenants_total,
                "active": tenants_active,
                "inactive": tenants_total - tenants_active,
                "new_7d": tenants_new_7d,
                "signup_series_30d": series,
            },
            "billing": {
                "mrr": mrr,
                "expiring_7d": expiring_7d,
                "overdue_invoices": overdue_invoices,
                "paid_30d": paid_30d,
            },
            "inbox": {
                "total": inbox_total,
                "unanswered": inbox_unanswered,
            },
            "users": {
                "total": users_total,
                "signups_7d": users_signups_7d,
            },
            "audit": {
                "failed_24h": audit_failed_24h,
                "recent": recent_audit_data,
            },
            "maintenance": {
                "enabled_for_all": bool(maint_total and maint_enabled == maint_total),
                "enabled_count": maint_enabled,
                "total": maint_total,
            },
        })
