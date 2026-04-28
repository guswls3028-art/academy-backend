"""fees 도메인 동시성 테스트 — PostgreSQL REQUIRED.

select_for_update가 SQLite에서는 noop이므로 진짜 race 검증은 PostgreSQL이 필수.
TransactionTestCase + threading으로 실제 DB row-level lock 동작 확인.

Run:
  DJANGO_SETTINGS_MODULE=apps.api.config.settings.test_pg \
  pytest apps/domains/fees/tests/test_payment_concurrency_pg.py -v

검증 시나리오:
- A. 부분납 동시 호출 (다른 idempotency_key) → 직렬화 후 합산
- B. 동일 idempotency_key 동시 호출 → 정확히 1개 payment만 생성
- C. 잔액 초과 동시 시도 → 정확히 1개만 성공, 나머지 ValueError
"""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.fees.models import (
    FeePayment,
    FeeTemplate,
    InvoiceItem,
    StudentInvoice,
)
from apps.domains.fees.services import record_payment
from apps.domains.students.models import Student

pytestmark = pytest.mark.django_db(transaction=True)
User = get_user_model()


class FeesConcurrencyPGTest(TransactionTestCase):
    """실제 PostgreSQL 환경에서 select_for_update 직렬화 검증."""

    # Django TransactionTestCase의 _fixture_teardown은 모든 테이블에 TRUNCATE를
    # allow_cascade=False로 호출. 이 코드베이스에는 FK constraint가 있어
    # cascade 없이 truncate 불가.
    # available_apps를 설정하면 Django가 자동으로 allow_cascade=True를 적용한다.
    # 모든 INSTALLED_APPS를 동적으로 로드해 동기화 부담 회피.
    @classmethod
    def setUpClass(cls):
        from django.conf import settings as dj_settings
        from django.apps import apps as dj_apps
        installed_names = {ac.name for ac in dj_apps.get_app_configs()}
        # third-party 앱은 제외(예: rest_framework)하면 truncate 대상에서 빠지면
        # 안 되므로, 전체 installed_apps 사용.
        cls.available_apps = list(installed_names)
        super().setUpClass()

    def _setup_invoice(self, total: int = 100_000):
        # 각 테스트가 격리된 데이터를 갖도록 uuid suffix 사용 — TransactionTestCase가
        # 테이블을 truncate하지만 일부 unique 컬럼(code)이 충돌할 수 있음.
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant.objects.create(
            name=f"FeesConc-{suffix}", code=f"fc_{suffix}", is_active=True,
        )
        user = User.objects.create(
            tenant=tenant, username=f"fc_stu_{suffix}", is_active=True,
        )
        student = Student.objects.create(
            tenant=tenant, user=user,
            ps_number=f"PS{suffix[:6]}",
            omr_code=f"O{suffix[:7]}",
            name="동시성테스트학생",
            parent_phone="010-0000-0001",
        )
        FeeTemplate.objects.create(
            tenant=tenant, name="동시성수강료",
            fee_type="TUITION", amount=total,
        )
        invoice = StudentInvoice.objects.create(
            tenant=tenant, student=student,
            invoice_number=f"FEE-CONC-{suffix}",
            billing_year=2026, billing_month=4,
            total_amount=total,
            due_date=timezone.localdate() + timedelta(days=10),
        )
        InvoiceItem.objects.create(
            tenant=tenant, invoice=invoice,
            description="수강료", amount=total,
        )
        return tenant, invoice

    def test_two_threads_partial_payments_serialize(self):
        """A. 두 thread가 다른 idempotency_key로 30,000 + 50,000 동시 납부.
        select_for_update가 직렬화하면 둘 다 성공, 최종 paid_amount = 80,000."""
        tenant, invoice = self._setup_invoice(total=100_000)

        results = {"success": 0, "errors": []}
        barrier = threading.Barrier(2)

        def worker(amount: int, key: str):
            barrier.wait()
            try:
                record_payment(
                    tenant, invoice.id, amount, "CASH",
                    idempotency_key=key,
                )
                results["success"] += 1
            except Exception as e:  # noqa: BLE001
                results["errors"].append((key, repr(e)))
            finally:
                close_old_connections()

        t1 = threading.Thread(target=worker, args=(30_000, "thread-1"))
        t2 = threading.Thread(target=worker, args=(50_000, "thread-2"))
        t1.start(); t2.start()
        t1.join(); t2.join()

        # 둘 다 성공해야 함 (직렬화로 race 없음)
        self.assertEqual(results["success"], 2, f"errors: {results['errors']}")
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 80_000)
        self.assertEqual(invoice.status, "PARTIAL")
        self.assertEqual(
            FeePayment.objects.filter(invoice=invoice, status="SUCCESS").count(),
            2,
        )

    def test_same_idempotency_key_concurrent_creates_single_payment(self):
        """B. 같은 idempotency_key로 두 thread 동시 호출 → 정확히 1개 payment만."""
        tenant, invoice = self._setup_invoice(total=100_000)

        results = {"success": 0, "errors": []}
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            try:
                record_payment(
                    tenant, invoice.id, 50_000, "CASH",
                    idempotency_key="same-key",
                )
                results["success"] += 1
            except Exception as e:  # noqa: BLE001
                results["errors"].append(repr(e))
            finally:
                close_old_connections()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()

        # 둘 다 성공 (한 쪽은 기존 payment 반환)
        # → DB에는 정확히 1개만 존재
        self.assertEqual(
            FeePayment.objects.filter(
                invoice=invoice, idempotency_key="same-key",
            ).count(),
            1,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 50_000)

    def test_overpayment_race_only_one_succeeds(self):
        """C. 잔액 50,000원에 두 thread가 각 40,000원 동시 납부 시도.
        합 80,000 > 50,000이므로 둘 중 하나는 ValueError."""
        tenant, invoice = self._setup_invoice(total=50_000)

        results = {"success": 0, "value_errors": 0, "other": []}
        barrier = threading.Barrier(2)

        def worker(key: str):
            barrier.wait()
            try:
                record_payment(
                    tenant, invoice.id, 40_000, "CASH",
                    idempotency_key=key,
                )
                results["success"] += 1
            except ValueError:
                results["value_errors"] += 1
            except Exception as e:  # noqa: BLE001
                results["other"].append(repr(e))
            finally:
                close_old_connections()

        t1 = threading.Thread(target=worker, args=("over-1",))
        t2 = threading.Thread(target=worker, args=("over-2",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        # 정확히 1개만 성공
        self.assertEqual(results["success"], 1, f"others: {results['other']}")
        self.assertEqual(results["value_errors"], 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, 40_000)
