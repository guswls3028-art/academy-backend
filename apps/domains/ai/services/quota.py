"""AI 호출 quota 가드 — 테넌트별 일일/월간 한도 enforcement.

가격정책 결정 전 임시 default 한도. 운영 데이터로 정책이 정해지면 plan별 한도로 확장.

호출처:
- generate_problem_from_ocr → kind="problem_generation"
- _embed_openai (외부 임베딩) → kind="embedding_openai"
- google_ocr → kind="ocr"
- infer_parent_phone_column → kind="schema_infer"
- 매치업 파이프라인 시작 → kind="matchup"

사용:
    from apps.domains.ai.services.quota import consume_ai_quota, AIQuotaExceeded
    try:
        consume_ai_quota(kind="problem_generation")
    except AIQuotaExceeded as e:
        logger.warning("...", e.tenant_id, e.kind)
        raise

활성화: settings.AI_QUOTA_ENFORCEMENT_ENABLED=True (default False).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Literal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

QuotaKind = Literal[
    "matchup", "ocr", "embedding_openai",
    "problem_generation", "schema_infer",
]

# 가격정책 결정 전 보수적 default. 운영 폭증 방지가 목표 — 정상 학원 사용량 초과하지 않게.
# 단위:
# - daily: 일일 호출 횟수
# - monthly: 월간 호출 횟수
# matchup pipeline은 1회 = 시험지 1건이라 daily=20도 충분.
# embedding은 batch당 1회 카운트라 daily=200 정도면 매치업 검색 충분.
DEFAULT_LIMITS: dict[str, dict[str, int]] = {
    "matchup":            {"daily": 30,   "monthly": 600},
    "ocr":                {"daily": 500,  "monthly": 8000},
    "embedding_openai":   {"daily": 300,  "monthly": 5000},
    "problem_generation": {"daily": 100,  "monthly": 2000},
    "schema_infer":       {"daily": 50,   "monthly": 500},
}


class AIQuotaExceeded(Exception):
    """테넌트 일일/월간 AI 호출 한도 초과."""

    def __init__(self, tenant_id: int, kind: str, period: str, used: int, limit: int):
        self.tenant_id = tenant_id
        self.kind = kind
        self.period = period
        self.used = used
        self.limit = limit
        super().__init__(
            f"AI quota exceeded: tenant={tenant_id} kind={kind} "
            f"period={period} used={used}/{limit}"
        )


def consume_ai_quota(kind: QuotaKind, cost: int = 1) -> None:
    """현재 테넌트 컨텍스트의 AI 호출 카운트를 cost만큼 증가.

    한도 초과 시 AIQuotaExceeded. settings.AI_QUOTA_ENFORCEMENT_ENABLED=False면 no-op.
    tenant context 없으면 (admin 작업 등) skip — 안전한 default.
    """
    if not getattr(settings, "AI_QUOTA_ENFORCEMENT_ENABLED", False):
        return

    from apps.core.tenant.context import get_current_tenant
    from apps.domains.ai.models import AIUsageModel

    tenant = get_current_tenant()
    if tenant is None:
        # tenant 없으면 enforcement skip (admin 작업, 테스트 등 — quota는 테넌트 단위 정책).
        return

    limits = DEFAULT_LIMITS.get(kind)
    if not limits:
        logger.warning("Unknown AI quota kind: %s — skipping enforcement", kind)
        return

    today = timezone.localdate()
    with transaction.atomic():
        # 일일 row
        daily, _ = AIUsageModel.objects.select_for_update().get_or_create(
            tenant=tenant,
            kind=kind,
            year=today.year,
            month=today.month,
            day=today.day,
            defaults={"count": 0},
        )
        if daily.count + cost > limits["daily"]:
            raise AIQuotaExceeded(
                tenant_id=tenant.id, kind=kind,
                period=f"daily-{today.isoformat()}",
                used=daily.count, limit=limits["daily"],
            )
        # 월간 row (day=0)
        monthly, _ = AIUsageModel.objects.select_for_update().get_or_create(
            tenant=tenant,
            kind=kind,
            year=today.year,
            month=today.month,
            day=0,
            defaults={"count": 0},
        )
        if monthly.count + cost > limits["monthly"]:
            raise AIQuotaExceeded(
                tenant_id=tenant.id, kind=kind,
                period=f"monthly-{today.year}-{today.month:02d}",
                used=monthly.count, limit=limits["monthly"],
            )
        # 둘 다 통과 → 카운트 증가
        daily.count += cost
        monthly.count += cost
        daily.save(update_fields=["count", "updated_at"])
        monthly.save(update_fields=["count", "updated_at"])


def get_current_usage(kind: QuotaKind) -> dict[str, int]:
    """현재 테넌트의 daily/monthly 사용량 + 한도 조회 (UI 노출용)."""
    from apps.core.tenant.context import get_current_tenant
    from apps.domains.ai.models import AIUsageModel

    tenant = get_current_tenant()
    if tenant is None:
        return {"daily_used": 0, "monthly_used": 0,
                "daily_limit": 0, "monthly_limit": 0}

    today = timezone.localdate()
    limits = DEFAULT_LIMITS.get(kind, {"daily": 0, "monthly": 0})

    daily = AIUsageModel.objects.filter(
        tenant=tenant, kind=kind,
        year=today.year, month=today.month, day=today.day,
    ).first()
    monthly = AIUsageModel.objects.filter(
        tenant=tenant, kind=kind,
        year=today.year, month=today.month, day=0,
    ).first()
    return {
        "daily_used": daily.count if daily else 0,
        "monthly_used": monthly.count if monthly else 0,
        "daily_limit": limits["daily"],
        "monthly_limit": limits["monthly"],
    }
