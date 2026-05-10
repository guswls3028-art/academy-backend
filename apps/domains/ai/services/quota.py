"""AI 호출 quota — tracking + enforcement 2단 분리.

tracking (default ON): tenant×kind 호출수를 ai_usage에 누적. 한도 체크 없음.
enforcement (default OFF): 누적치가 DEFAULT_LIMITS 초과 시 AIQuotaExceeded raise.

운영 절차:
    1. tracking으로 실측 데이터 수집 (현재 단계)
    2. 가격정책 결정 → DEFAULT_LIMITS 또는 tenant별 override 설정
    3. AI_QUOTA_ENFORCEMENT_ENABLED=true 토글로 brake 활성화

호출처:
- generate_problem_from_ocr → kind="problem_generation"
- _embed_openai (외부 임베딩) → kind="embedding_openai"
- google_ocr / google_ocr_blocks → kind="ocr"
- infer_parent_phone_column → kind="schema_infer"
- 매치업 파이프라인 시작 → kind="matchup"

사용:
    from apps.domains.ai.services.quota import consume_ai_quota, AIQuotaExceeded
    try:
        consume_ai_quota(kind="problem_generation")
    except AIQuotaExceeded as e:
        logger.warning("...", e.tenant_id, e.kind)
        raise
"""
from __future__ import annotations

import logging
from typing import Literal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

QuotaKind = Literal[
    "matchup", "ocr", "embedding_openai",
    "problem_generation", "schema_infer",
    "matchup_vlm",  # Gemini VLM 호출 (B-2 paper_type + 운영 자동분리, 2026-05-04)
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
    # B-2 (2026-05-04): VLM 호출 — Gemini Flash $0.005/call → daily 500=$2.5, monthly 10000=$50.
    # in-memory _check_tenant_quota(MATCHUP_VLM_PER_TENANT_DAILY_LIMIT) fast-fail과 별도로
    # DB 영구 카운터로 모니터링 + enforcement 가능. 학원장 정책 변경 시 env 또는 dict.
    "matchup_vlm":        {"daily": 500,  "monthly": 10000},
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
    """현재 테넌트 컨텍스트의 AI 호출을 cost만큼 카운트.

    동작:
      - AI_USAGE_TRACKING_ENABLED=True (default) + tenant context 있음 → 누적 카운트
      - AI_QUOTA_ENFORCEMENT_ENABLED=True 추가로 → DEFAULT_LIMITS 초과 시 raise
      - tenant context 없으면 (admin/익명 워커) skip — 안전한 default

    enforcement OFF 상태에서도 카운트는 계속 쌓이므로,
    나중에 AI_QUOTA_ENFORCEMENT_ENABLED=true로 토글하는 즉시 brake가 활성화된다.
    """
    if not getattr(settings, "AI_USAGE_TRACKING_ENABLED", True):
        return

    from apps.core.tenant.context import get_current_tenant
    from apps.domains.ai.models import AIUsageModel

    tenant = get_current_tenant()
    if tenant is None:
        return

    limits = DEFAULT_LIMITS.get(kind)
    if not limits:
        # 알 수 없는 kind는 tracking/enforcement 모두 skip — 의도치 않은 row 누적 방지.
        logger.warning("Unknown AI quota kind: %s — skipping", kind)
        return

    enforcement_on = getattr(settings, "AI_QUOTA_ENFORCEMENT_ENABLED", False)

    today = timezone.localdate()
    try:
        with transaction.atomic():
            daily, _ = AIUsageModel.objects.select_for_update().get_or_create(
                tenant=tenant,
                kind=kind,
                year=today.year,
                month=today.month,
                day=today.day,
                defaults={"count": 0},
            )
            if enforcement_on and daily.count + cost > limits["daily"]:
                raise AIQuotaExceeded(
                    tenant_id=tenant.id, kind=kind,
                    period=f"daily-{today.isoformat()}",
                    used=daily.count, limit=limits["daily"],
                )
            monthly, _ = AIUsageModel.objects.select_for_update().get_or_create(
                tenant=tenant,
                kind=kind,
                year=today.year,
                month=today.month,
                day=0,
                defaults={"count": 0},
            )
            if enforcement_on and monthly.count + cost > limits["monthly"]:
                raise AIQuotaExceeded(
                    tenant_id=tenant.id, kind=kind,
                    period=f"monthly-{today.year}-{today.month:02d}",
                    used=monthly.count, limit=limits["monthly"],
                )
            daily.count += cost
            monthly.count += cost
            daily.save(update_fields=["count", "updated_at"])
            monthly.save(update_fields=["count", "updated_at"])
    except AIQuotaExceeded:
        raise
    except Exception:
        # tracking 실패가 본 작업을 죽이면 안 됨 — DB 일시 단절 등은 silent.
        logger.warning("AI usage tracking failed | kind=%s tenant=%s",
                       kind, tenant.id, exc_info=True)


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
