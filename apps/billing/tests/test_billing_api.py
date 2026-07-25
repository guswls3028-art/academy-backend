"""
Billing API 통합 테스트 — 12개 엔드포인트 권한/응답/테넌트 격리 검증.

범위:
A. 플랫폼 관리자 API (6개) — Superuser 전용
   1. GET  /api/v1/billing/admin/tenants/
   2. POST /api/v1/billing/admin/tenants/{id}/extend/
   3. GET  /api/v1/billing/admin/invoices/
   4. GET  /api/v1/billing/admin/invoices/{id}/
   5. POST /api/v1/billing/admin/invoices/{id}/mark-paid/
   6. GET  /api/v1/billing/admin/dashboard/

B. 원장 API (6개) — TenantResolvedAndOwner/Staff
   7. GET  /api/v1/billing/invoices/
   8. GET  /api/v1/billing/invoices/{id}/
   9. GET /api/v1/billing/cards/
   10. GET/PATCH /api/v1/billing/profile/
   11. POST /api/v1/billing/cancel/
   12. POST /api/v1/billing/cancel/revoke/
"""

from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.billing.models import Invoice, PaymentTransaction
from apps.core.models import Tenant, TenantMembership
from apps.core.models.program import Program

User = get_user_model()


class BillingApiTestBase(APITestCase):
    """공통 fixture: 2개 테넌트 + superuser + owner + staff + anonymous"""

    def setUp(self):
        # ── Tenant A (주 테스트 대상) ──
        self.tenant_a = Tenant.objects.create(name="Academy A", code="api_test_a", is_active=True)
        self.program_a = Program.objects.get(tenant=self.tenant_a)
        self.program_a.subscription_status = "active"
        self.program_a.subscription_started_at = date(2026, 3, 13)
        self.program_a.subscription_expires_at = date(2026, 4, 12)
        self.program_a.billing_mode = "AUTO_CARD"
        self.program_a.save()

        # ── Tenant B (격리 테스트용) ──
        self.tenant_b = Tenant.objects.create(name="Academy B", code="api_test_b", is_active=True)
        self.program_b = Program.objects.get(tenant=self.tenant_b)
        self.program_b.subscription_status = "active"
        self.program_b.subscription_expires_at = date(2026, 5, 12)
        self.program_b.save()

        # ── Superuser (플랫폼 관리자) ──
        self.superuser = User.objects.create_superuser(
            username="billing_super", password="test1234!",
            tenant=self.tenant_a, name="Super",
        )

        # ── Owner A ──
        self.owner_a = User.objects.create(
            username=f"t{self.tenant_a.id}_owner", tenant=self.tenant_a,
            is_active=True, is_staff=True, name="OwnerA",
        )
        self.owner_a.set_password("test1234!")
        self.owner_a.save(update_fields=["password"])
        TenantMembership.objects.create(
            user=self.owner_a, tenant=self.tenant_a, role="owner", is_active=True,
        )

        # ── Staff A (owner 아님) ──
        self.staff_a = User.objects.create(
            username=f"t{self.tenant_a.id}_staff", tenant=self.tenant_a,
            is_active=True, is_staff=True, name="StaffA",
        )
        self.staff_a.set_password("test1234!")
        self.staff_a.save(update_fields=["password"])
        TenantMembership.objects.create(
            user=self.staff_a, tenant=self.tenant_a, role="staff", is_active=True,
        )

        # ── Owner B (다른 테넌트) ──
        self.owner_b = User.objects.create(
            username=f"t{self.tenant_b.id}_owner", tenant=self.tenant_b,
            is_active=True, is_staff=True, name="OwnerB",
        )
        self.owner_b.set_password("test1234!")
        self.owner_b.save(update_fields=["password"])
        TenantMembership.objects.create(
            user=self.owner_b, tenant=self.tenant_b, role="owner", is_active=True,
        )

        # ── 테스트 인보이스 ──
        self.invoice_a = Invoice.objects.create(
            tenant=self.tenant_a,
            invoice_number="INV-API-A-001",
            plan="pro",
            billing_mode="AUTO_CARD",
            supply_amount=198_000,
            tax_amount=19_800,
            total_amount=217_800,
            period_start=date(2026, 4, 13),
            period_end=date(2026, 5, 12),
            due_date=date(2026, 4, 13),
            status="PENDING",
        )

        self.invoice_b = Invoice.objects.create(
            tenant=self.tenant_b,
            invoice_number="INV-API-B-001",
            plan="max",
            billing_mode="INVOICE_REQUEST",
            supply_amount=330_000,
            tax_amount=33_000,
            total_amount=363_000,
            period_start=date(2026, 5, 13),
            period_end=date(2026, 6, 12),
            due_date=date(2026, 5, 28),
            status="SCHEDULED",
        )

        # Tenant A 기본 헤더
        self.headers_a = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant_a.code}
        self.headers_b = {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant_b.code}


# ══════════════════════════════════════════════
# A. 플랫폼 관리자 API 테스트
# ══════════════════════════════════════════════

class TestAdminTenantSubscriptionList(BillingApiTestBase):
    """GET /api/v1/billing/admin/tenants/"""

    def test_superuser_can_list(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get("/api/v1/billing/admin/tenants/", **self.headers_a)
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        codes = [t["tenant_code"] for t in resp.data]
        self.assertIn("api_test_a", codes)
        self.assertIn("api_test_b", codes)

    def test_owner_cannot_access_admin(self):
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.get("/api/v1/billing/admin/tenants/", **self.headers_a)
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_cannot_access(self):
        resp = self.client.get("/api/v1/billing/admin/tenants/", **self.headers_a)
        self.assertIn(resp.status_code, [401, 403])

    def test_response_fields(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get("/api/v1/billing/admin/tenants/", **self.headers_a)
        entry = next(t for t in resp.data if t["tenant_code"] == "api_test_a")
        required_fields = [
            "program_id", "tenant_id", "tenant_code", "tenant_name", "plan", "plan_display",
            "monthly_price", "monthly_supply_amount", "monthly_tax_amount",
            "monthly_total_amount", "monthly_price_includes_tax", "vat_rate_percent",
            "billing_price_policy", "is_contract_price", "billing_price_integrity",
            "has_lifetime_price_guarantee", "price_guarantee_code",
            "price_guarantee_label",
            "is_billing_price_ready", "subscription_status",
            "subscription_expires_at", "service_access_expires_at",
            "grace_period_days", "grace_expires_at",
            "days_remaining", "billing_mode", "cancel_at_period_end",
            "next_billing_at", "is_subscription_active",
        ]
        for f in required_fields:
            self.assertIn(f, entry, f"Missing field: {f}")
        self.assertEqual(entry["program_id"], self.program_a.pk)
        self.assertEqual(entry["plan"], "all")
        self.assertEqual(entry["monthly_supply_amount"], 180_000)
        self.assertEqual(entry["monthly_tax_amount"], 18_000)
        self.assertEqual(entry["monthly_total_amount"], 198_000)
        self.assertEqual(entry["vat_rate_percent"], 10)
        self.assertEqual(entry["billing_price_policy"], "single")
        self.assertFalse(entry["is_contract_price"])
        self.assertFalse(entry["monthly_price_includes_tax"])


class TestAdminExtendSubscription(BillingApiTestBase):
    """POST /api/v1/billing/admin/tenants/{id}/extend/"""

    def test_superuser_can_extend(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(
            f"/api/v1/billing/admin/tenants/{self.program_a.pk}/extend/",
            {"days": 30}, format="json", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 200)
        self.program_a.refresh_from_db()
        self.assertEqual(resp.data["subscription_status"], "active")
        self.assertIsNotNone(resp.data["subscription_expires_at"])
        self.assertEqual(self.program_a.next_billing_at, self.program_a.subscription_expires_at)

    def test_owner_cannot_extend(self):
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.post(
            f"/api/v1/billing/admin/tenants/{self.program_a.pk}/extend/",
            {"days": 30}, format="json", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 403)

    def test_invalid_days_rejected(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(
            f"/api/v1/billing/admin/tenants/{self.program_a.pk}/extend/",
            {"days": 0}, format="json", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 400)

    def test_nonexistent_program_404(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(
            "/api/v1/billing/admin/tenants/99999/extend/",
            {"days": 30}, format="json", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 404)


class TestAdminInvoiceList(BillingApiTestBase):
    """GET /api/v1/billing/admin/invoices/"""

    def test_superuser_sees_all_invoices(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get("/api/v1/billing/admin/invoices/", **self.headers_a)
        self.assertEqual(resp.status_code, 200)
        numbers = [i["invoice_number"] for i in resp.data["results"]]
        self.assertIn("INV-API-A-001", numbers)
        self.assertIn("INV-API-B-001", numbers)

    def test_filter_by_status(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get(
            "/api/v1/billing/admin/invoices/?status=PENDING", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 200)
        for inv in resp.data["results"]:
            self.assertEqual(inv["status"], "PENDING")

    def test_filter_by_tenant(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get(
            f"/api/v1/billing/admin/invoices/?tenant=api_test_a", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 200)
        for inv in resp.data["results"]:
            self.assertEqual(inv["tenant_code"], "api_test_a")

    def test_owner_cannot_access(self):
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.get("/api/v1/billing/admin/invoices/", **self.headers_a)
        self.assertEqual(resp.status_code, 403)


class TestAdminInvoiceDetail(BillingApiTestBase):
    """GET /api/v1/billing/admin/invoices/{pk}/"""

    def test_superuser_can_view_detail(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get(
            f"/api/v1/billing/admin/invoices/{self.invoice_a.pk}/", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["invoice_number"], "INV-API-A-001")
        self.assertIn("provider_order_id", resp.data)

    def test_nonexistent_invoice_404(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get(
            "/api/v1/billing/admin/invoices/99999/", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 404)


class TestAdminMarkPaid(BillingApiTestBase):
    """POST /api/v1/billing/admin/invoices/{pk}/mark-paid/"""

    def test_superuser_can_mark_paid(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(
            f"/api/v1/billing/admin/invoices/{self.invoice_a.pk}/mark-paid/",
            **self.headers_a,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "PAID")
        transaction = PaymentTransaction.objects.get(invoice=self.invoice_a)
        self.assertEqual(transaction.provider, "manual")
        self.assertEqual(transaction.amount, self.invoice_a.total_amount)
        # 과거 인보이스의 수동 정산은 현재 구독 기간을 되감거나 연장하지 않는다.
        self.program_a.refresh_from_db()
        self.assertEqual(self.program_a.subscription_expires_at, date(2026, 4, 12))

    def test_already_paid_rejected(self):
        self.invoice_a.status = "PAID"
        self.invoice_a.save(update_fields=["status"])
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(
            f"/api/v1/billing/admin/invoices/{self.invoice_a.pk}/mark-paid/",
            **self.headers_a,
        )
        self.assertEqual(resp.status_code, 400)

    def test_scheduled_rejected(self):
        """SCHEDULED 상태에서는 직접 mark-paid 불가"""
        self.invoice_a.status = "SCHEDULED"
        self.invoice_a.save(update_fields=["status"])
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.post(
            f"/api/v1/billing/admin/invoices/{self.invoice_a.pk}/mark-paid/",
            **self.headers_a,
        )
        self.assertEqual(resp.status_code, 400)


class TestAdminDashboard(BillingApiTestBase):
    """GET /api/v1/billing/admin/dashboard/"""

    def test_superuser_gets_dashboard(self):
        self.client.force_authenticate(user=self.superuser)
        resp = self.client.get("/api/v1/billing/admin/dashboard/", **self.headers_a)
        self.assertEqual(resp.status_code, 200)
        required_fields = [
            "mrr", "mrr_supply_amount", "mrr_tax_amount", "mrr_total_amount",
            "mrr_includes_tax", "vat_rate_percent", "status_counts", "expiring_soon",
            "overdue_invoices", "plan_distribution", "total_tenants",
        ]
        for f in required_fields:
            self.assertIn(f, resp.data, f"Missing dashboard field: {f}")

    def test_owner_cannot_access_dashboard(self):
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.get("/api/v1/billing/admin/dashboard/", **self.headers_a)
        self.assertEqual(resp.status_code, 403)

    @override_settings(BILLING_EXEMPT_TENANT_IDS=set())
    def test_dashboard_sums_standard_and_august_guaranteed_prices(self):
        Program.objects.filter(pk=self.program_a.pk).update(
            created_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc),
            monthly_price=180_000,
        )
        Program.objects.filter(pk=self.program_b.pk).update(
            created_at=datetime(2026, 8, 15, tzinfo=dt_timezone.utc),
            monthly_price=145_000,
        )
        self.client.force_authenticate(user=self.superuser)

        with patch.dict(
            Program.PLAN_PRICES,
            {Program.Plan.ALL: 180_000},
            clear=True,
        ):
            resp = self.client.get(
                "/api/v1/billing/admin/dashboard/",
                **self.headers_a,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["mrr_supply_amount"], 325_000)
        self.assertEqual(resp.data["mrr_tax_amount"], 32_000)
        self.assertEqual(resp.data["mrr_total_amount"], 357_000)
        self.assertIsNone(resp.data["vat_rate_percent"])


# ══════════════════════════════════════════════
# B. 원장 API 테스트
# ══════════════════════════════════════════════

class TestMyInvoiceList(BillingApiTestBase):
    """GET /api/v1/billing/invoices/"""

    def test_staff_can_list_own_invoices(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.get("/api/v1/billing/invoices/", **self.headers_a)
        self.assertEqual(resp.status_code, 200)
        numbers = [i["invoice_number"] for i in resp.data["results"]]
        self.assertIn("INV-API-A-001", numbers)
        # 다른 테넌트 인보이스는 보이지 않아야 함
        self.assertNotIn("INV-API-B-001", numbers)
        invoice = next(
            item for item in resp.data["results"]
            if item["invoice_number"] == "INV-API-A-001"
        )
        self.assertEqual(invoice["status_display"], "결제 대기")
        self.assertTrue(invoice["can_mark_paid"])
        self.assertFalse(invoice["is_terminal"])
        self.assertEqual(invoice["payment_blocked_reason"], "")

    def test_tenant_isolation(self):
        """Owner B가 Tenant A 헤더로 요청 → 403 (멤버십 없음)"""
        self.client.force_authenticate(user=self.owner_b)
        resp = self.client.get("/api/v1/billing/invoices/", **self.headers_a)
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_rejected(self):
        resp = self.client.get("/api/v1/billing/invoices/", **self.headers_a)
        self.assertIn(resp.status_code, [401, 403])


class TestMyInvoiceDetail(BillingApiTestBase):
    """GET /api/v1/billing/invoices/{pk}/"""

    def test_staff_can_view_own_invoice(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.get(
            f"/api/v1/billing/invoices/{self.invoice_a.pk}/", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["invoice_number"], "INV-API-A-001")

    def test_cannot_view_other_tenant_invoice(self):
        """Tenant A staff가 Tenant B 인보이스 조회 → 404"""
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.get(
            f"/api/v1/billing/invoices/{self.invoice_b.pk}/", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 404)


class TestMyBillingCards(BillingApiTestBase):
    """GET /api/v1/billing/cards/"""

    def test_owner_can_list_cards(self):
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.get("/api/v1/billing/cards/", **self.headers_a)
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)

    def test_staff_cannot_list_cards(self):
        """카드 관리는 owner만"""
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.get("/api/v1/billing/cards/", **self.headers_a)
        self.assertEqual(resp.status_code, 403)


class TestMyBillingProfile(BillingApiTestBase):
    """GET/PATCH /api/v1/billing/profile/"""

    def test_owner_get_empty_profile(self):
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.get("/api/v1/billing/profile/", **self.headers_a)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("payer_name", resp.data)

    def test_owner_patch_profile(self):
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.patch(
            "/api/v1/billing/profile/",
            {"payer_name": "홍길동", "payer_email": "hong@test.com"},
            format="json", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["payer_name"], "홍길동")

    def test_staff_cannot_modify_profile(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.patch(
            "/api/v1/billing/profile/",
            {"payer_name": "테스트"},
            format="json", **self.headers_a,
        )
        self.assertEqual(resp.status_code, 403)


class TestCancelSubscription(BillingApiTestBase):
    """POST /api/v1/billing/cancel/"""

    def test_owner_can_schedule_cancel(self):
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.post("/api/v1/billing/cancel/", **self.headers_a)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["cancel_at_period_end"])
        # 상태는 여전히 active
        self.program_a.refresh_from_db()
        self.assertEqual(self.program_a.subscription_status, "active")
        self.assertTrue(self.program_a.cancel_at_period_end)

    def test_expired_cannot_cancel(self):
        self.program_a.subscription_status = "expired"
        self.program_a.save(update_fields=["subscription_status"])
        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.post("/api/v1/billing/cancel/", **self.headers_a)
        self.assertEqual(resp.status_code, 400)

    def test_staff_cannot_cancel(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.post("/api/v1/billing/cancel/", **self.headers_a)
        self.assertEqual(resp.status_code, 403)


class TestRevokeCancelSubscription(BillingApiTestBase):
    """POST /api/v1/billing/cancel/revoke/"""

    def test_owner_can_revoke_cancel(self):
        # 먼저 해지 예약
        self.program_a.cancel_at_period_end = True
        self.program_a.save(update_fields=["cancel_at_period_end"])

        self.client.force_authenticate(user=self.owner_a)
        resp = self.client.post("/api/v1/billing/cancel/revoke/", **self.headers_a)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["cancel_at_period_end"])

    def test_staff_cannot_revoke(self):
        self.client.force_authenticate(user=self.staff_a)
        resp = self.client.post("/api/v1/billing/cancel/revoke/", **self.headers_a)
        self.assertEqual(resp.status_code, 403)

    def test_grace_subscription_revoke_returns_validation_error(self):
        self.program_a.subscription_status = "grace"
        self.program_a.cancel_at_period_end = True
        self.program_a.save(
            update_fields=["subscription_status", "cancel_at_period_end"]
        )
        self.client.force_authenticate(user=self.owner_a)

        resp = self.client.post("/api/v1/billing/cancel/revoke/", **self.headers_a)

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("active", resp.data["detail"])

    def test_reconciliation_required_revoke_returns_conflict(self):
        self.program_a.cancel_at_period_end = True
        self.program_a.save(update_fields=["cancel_at_period_end"])
        self.invoice_a.status = "VOID"
        self.invoice_a.memo = "cancel_at_period_end"
        self.invoice_a.save(update_fields=["status", "memo"])
        PaymentTransaction.objects.create(
            tenant=self.tenant_a,
            invoice=self.invoice_a,
            provider="tosspayments",
            provider_order_id=self.invoice_a.provider_order_id,
            idempotency_key=self.invoice_a.provider_order_id,
            amount=self.invoice_a.total_amount,
            status="PROCESSING",
        )
        self.client.force_authenticate(user=self.owner_a)

        resp = self.client.post("/api/v1/billing/cancel/revoke/", **self.headers_a)

        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertIn("reconciliation", resp.data["detail"])
