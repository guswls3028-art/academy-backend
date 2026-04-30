"""AI 호출 quota enforcement 단위테스트.

검증:
- enforcement OFF: tenant 카운트 안 늘고 한도 초과해도 패스
- enforcement ON + 정상 사용: 카운트 증가
- enforcement ON + daily 한도 초과: AIQuotaExceeded
- enforcement ON + monthly 한도 초과: AIQuotaExceeded
- tenant 컨텍스트 없음: skip (카운트 0 유지)
- 알 수 없는 kind: skip
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import Tenant
from apps.core.tenant.context import (
    clear_current_tenant,
    set_current_tenant,
)
from apps.domains.ai.models import AIUsageModel
from apps.domains.ai.services.quota import (
    DEFAULT_LIMITS,
    AIQuotaExceeded,
    consume_ai_quota,
    get_current_usage,
)

User = get_user_model()


class QuotaDisabledTest(TestCase):
    """AI_QUOTA_ENFORCEMENT_ENABLED=False (default)에서는 no-op."""

    def setUp(self):
        self.tenant = Tenant.objects.create(code="t_qd", name="QuotaOff")
        set_current_tenant(self.tenant)

    def tearDown(self):
        clear_current_tenant()

    @override_settings(AI_QUOTA_ENFORCEMENT_ENABLED=False)
    def test_no_count_when_disabled(self):
        for _ in range(10):
            consume_ai_quota(kind="problem_generation")
        self.assertEqual(
            AIUsageModel.objects.filter(tenant=self.tenant).count(), 0
        )


class QuotaEnabledTest(TestCase):
    """AI_QUOTA_ENFORCEMENT_ENABLED=True 정상 동작."""

    def setUp(self):
        self.tenant = Tenant.objects.create(code="t_qe", name="QuotaOn")
        set_current_tenant(self.tenant)

    def tearDown(self):
        clear_current_tenant()

    @override_settings(AI_QUOTA_ENFORCEMENT_ENABLED=True)
    def test_count_increments_on_success(self):
        consume_ai_quota(kind="problem_generation")
        consume_ai_quota(kind="problem_generation")

        today = timezone.localdate()
        daily = AIUsageModel.objects.get(
            tenant=self.tenant, kind="problem_generation",
            year=today.year, month=today.month, day=today.day,
        )
        monthly = AIUsageModel.objects.get(
            tenant=self.tenant, kind="problem_generation",
            year=today.year, month=today.month, day=0,
        )
        self.assertEqual(daily.count, 2)
        self.assertEqual(monthly.count, 2)

    @override_settings(AI_QUOTA_ENFORCEMENT_ENABLED=True)
    def test_daily_limit_raises(self):
        limit = DEFAULT_LIMITS["schema_infer"]["daily"]
        for _ in range(limit):
            consume_ai_quota(kind="schema_infer")
        with self.assertRaises(AIQuotaExceeded) as ctx:
            consume_ai_quota(kind="schema_infer")
        self.assertEqual(ctx.exception.kind, "schema_infer")
        self.assertIn("daily", ctx.exception.period)

    @override_settings(AI_QUOTA_ENFORCEMENT_ENABLED=True)
    def test_get_current_usage_reports_correct_numbers(self):
        consume_ai_quota(kind="embedding_openai", cost=3)
        usage = get_current_usage(kind="embedding_openai")
        self.assertEqual(usage["daily_used"], 3)
        self.assertEqual(usage["monthly_used"], 3)
        self.assertEqual(
            usage["daily_limit"],
            DEFAULT_LIMITS["embedding_openai"]["daily"],
        )

    @override_settings(AI_QUOTA_ENFORCEMENT_ENABLED=True)
    def test_unknown_kind_is_skipped(self):
        # 잘못된 kind는 enforcement skip — silent log warning. 카운트도 안 증가.
        consume_ai_quota(kind="unknown_kind")  # type: ignore[arg-type]
        self.assertEqual(
            AIUsageModel.objects.filter(tenant=self.tenant).count(), 0
        )


class QuotaTenantContextTest(TestCase):
    """tenant 컨텍스트 없이 호출되면 skip (admin 작업, 테스트 등)."""

    @override_settings(AI_QUOTA_ENFORCEMENT_ENABLED=True)
    def test_no_tenant_skips_enforcement(self):
        clear_current_tenant()
        # 어떤 한도여도 raise 안 됨
        for _ in range(5):
            consume_ai_quota(kind="matchup")
        self.assertEqual(AIUsageModel.objects.count(), 0)
