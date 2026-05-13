"""상담 요청 (landing contact form) view + helper.

분리 출처: apps/core/views_landing.py:593-760 (P1 audit 구조 리팩토링 step 2, 2026-05-14).

3 view + 검증/rate limit/honeypot:
- LandingConsultPublicView: POST 상담 폼 (비로그인 OK + tenant 격리 + DB rate limit)
- LandingConsultAdminListView: 학원장 inbox
- LandingConsultAdminDetailView: 학원장 처리 (mark_read/메모)
"""
from __future__ import annotations

import logging
import re as _re

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolved, TenantResolvedAndStaff

from ._helpers import check_landing_admin_role, client_ip

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────
# 검증 상수 + 헬퍼
# ─────────────────────────────────────────────────

PERSONAL_MOBILE_PREFIX = ("010", "011", "016", "017", "018", "019")
NAME_MAX = 50
INTEREST_MAX = 80
MESSAGE_MAX = 2000


def validate_consult(data: dict) -> list[str]:
    errs: list[str] = []
    name = str(data.get("name") or "").strip()
    phone = str(data.get("phone") or "").strip()
    interest = str(data.get("interest") or "").strip()
    message = str(data.get("message") or "").strip()
    if not name or len(name) > NAME_MAX:
        errs.append(f"이름은 1~{NAME_MAX}자여야 합니다.")
    digits = _re.sub(r"[^\d]", "", phone)
    if not digits or len(digits) < 9 or len(digits) > 15:
        errs.append("올바른 전화번호를 입력해주세요.")
    # P2 audit (2026-05-14): +82 외국인 학부모 prefix 통과.
    elif not (
        digits.startswith(PERSONAL_MOBILE_PREFIX)
        or digits.startswith("02")
        or digits[0] == "0"
        or digits.startswith("82")  # +82 외국인/해외 학부모
    ):
        errs.append("올바른 전화번호 형식이 아닙니다.")
    if interest and len(interest) > INTEREST_MAX:
        errs.append(f"관심 분야는 {INTEREST_MAX}자 이내여야 합니다.")
    if message and len(message) > MESSAGE_MAX:
        errs.append(f"메시지는 {MESSAGE_MAX}자 이내여야 합니다.")
    return errs


# Rate limit — DB-level dedup (LandingConsultRequest row 자체).
# P0 audit (2026-05-13 → 2026-05-14): settings.CACHES 미설정 → LocMemCache 결국 같은 문제.
# DB-level 이 ASG 다중 인스턴스 자연 공유 + Redis 인프라 추가 부담 X.


def is_rate_limited(tenant, phone: str, limit: int = 5, window_sec: int = 60) -> bool:
    """tenant + phone 별 window_sec 안 limit 초과면 True.

    ASG 3 인스턴스에서 공유되는 RDS row 자체를 카운터로 사용 → 자연 공유.
    인덱스: (tenant, -created_at) 기존 — 1분 cutoff 범위라 100건/일 학원 부담 0.
    """
    if not phone:
        return False
    from datetime import timedelta
    from django.utils import timezone as dj_tz
    from apps.core.models import LandingConsultRequest
    cutoff = dj_tz.now() - timedelta(seconds=window_sec)
    try:
        count = LandingConsultRequest.objects.filter(
            tenant=tenant, phone=phone, created_at__gte=cutoff,
        ).count()
        return count >= limit
    except Exception:
        logger.exception("CONSULT_RATE_LIMIT_DB_FAIL phone=%s", phone[-4:])
        return False  # fail-open


# ─────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────


class LandingConsultPublicView(APIView):
    """POST /api/v1/core/landing/consult/ — 공개 상담 폼 (비로그인 OK + tenant 격리)."""
    permission_classes = [TenantResolved]
    authentication_classes = []  # 인증 없이 작동

    def post(self, request):
        # honeypot — 사람은 안 채우는 hidden field. 채워졌으면 봇 → 201처럼 위장 응답.
        if (request.data or {}).get("website") or (request.data or {}).get("hp"):
            logger.info("CONSULT_HONEYPOT_TRAP ip=%s", client_ip(request))
            return Response({"id": 0, "ok": True}, status=201)

        # rate limit — tenant + phone 기준 (DB-level). phone 없으면 validate 에서 차단.
        phone_raw = str((request.data or {}).get("phone") or "").strip()
        if phone_raw and is_rate_limited(request.tenant, phone_raw):
            return Response({"detail": "너무 빠른 요청입니다. 잠시 후 다시 시도해주세요."}, status=429)

        errs = validate_consult(request.data or {})
        if errs:
            return Response({"detail": errs}, status=400)

        from apps.core.models import LandingConsultRequest
        obj = LandingConsultRequest.objects.create(
            tenant=request.tenant,
            name=str(request.data.get("name") or "").strip()[:NAME_MAX],
            phone=str(request.data.get("phone") or "").strip()[:20],
            interest=str(request.data.get("interest") or "").strip()[:INTEREST_MAX],
            message=str(request.data.get("message") or "").strip()[:MESSAGE_MAX],
            source=str(request.data.get("source") or "landing").strip()[:40],
        )
        logger.info("LandingConsultRequest created tenant=%s id=%s ip=%s", request.tenant.id, obj.id, client_ip(request))
        return Response({"id": obj.id, "ok": True}, status=201)


class LandingConsultAdminListView(APIView):
    """GET /api/v1/core/landing/admin/consult/ — 학원장 inbox."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("상담 요청 조회는 원장/관리자만 가능합니다.")

    def get(self, request):
        from apps.core.models import LandingConsultRequest
        qs = LandingConsultRequest.objects.filter(tenant=request.tenant)[:200]
        items = [{
            "id": r.id,
            "name": r.name,
            "phone": r.phone,
            "interest": r.interest,
            "message": r.message,
            "source": r.source,
            "read_at": r.read_at.isoformat() if r.read_at else None,
            "admin_memo": r.admin_memo,
            "created_at": r.created_at.isoformat(),
        } for r in qs]
        unread = sum(1 for r in qs if r.read_at is None)
        return Response({"items": items, "summary": {"total": len(items), "unread": unread}})


class LandingConsultAdminDetailView(APIView):
    """PATCH /api/v1/core/landing/admin/consult/<id>/ — 읽음/메모 update."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("상담 요청 처리는 원장/관리자만 가능합니다.")

    def patch(self, request, item_id):
        from apps.core.models import LandingConsultRequest
        from django.utils import timezone
        try:
            r = LandingConsultRequest.objects.get(id=item_id, tenant=request.tenant)
        except LandingConsultRequest.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)
        data = request.data or {}
        if "mark_read" in data and data["mark_read"]:
            r.read_at = r.read_at or timezone.now()
        if "admin_memo" in data:
            r.admin_memo = str(data["admin_memo"] or "")[:2000]
        r.save(update_fields=["read_at", "admin_memo", "updated_at"])
        return Response({"ok": True})
