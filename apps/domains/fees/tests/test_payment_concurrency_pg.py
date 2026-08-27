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
import time
import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.fees.models import (
    FeePayment,
    FeeTemplate,
    InvoiceItem,
    StudentFee,
    StudentInvoice,
)
from apps.domains.fees import services
from apps.domains.fees.services import generate_monthly_invoices, record_payment
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
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL row-level locking is required for this concurrency test."
            )
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

    def _setup_generation_fee(self):
        tenant, invoice = self._setup_invoice(total=100_000)
        student = invoice.student
        invoice.delete()
        template = FeeTemplate.objects.get(tenant=tenant)
        student_fee = StudentFee.objects.create(
            tenant=tenant,
            student=student,
            fee_template=template,
        )
        return tenant, student, template, student_fee

    def _generate_while_fee_rows_are_locked(
        self,
        tenant,
        student,
        mutate_locked_rows,
        *,
        billing_month: int = 5,
    ):
        snapshot_ready = threading.Event()
        mutation_locked = threading.Event()
        generation_released = threading.Event()
        original_groupby = services.groupby
        results = []
        errors = []

        def pause_after_snapshot(*args, **kwargs):
            snapshot_ready.set()
            if not mutation_locked.wait(timeout=5):
                raise AssertionError("fee mutation did not acquire its row locks")
            generation_released.set()
            return original_groupby(*args, **kwargs)

        def generate_worker():
            close_old_connections()
            try:
                results.append(generate_monthly_invoices(
                    tenant,
                    billing_year=2026,
                    billing_month=billing_month,
                    due_date=timezone.localdate() + timedelta(days=10),
                ))
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
            finally:
                close_old_connections()

        with patch(
            "apps.domains.fees.services.groupby",
            side_effect=pause_after_snapshot,
        ):
            generation_thread = threading.Thread(target=generate_worker)
            generation_thread.start()
            self.assertTrue(snapshot_ready.wait(timeout=5), "generation snapshot was not read")

            with transaction.atomic():
                locked_fees = list(
                    StudentFee.objects
                    .select_for_update()
                    .filter(tenant=tenant, student=student)
                    .order_by("id")
                )
                template_ids = [fee.fee_template_id for fee in locked_fees]
                locked_templates = {
                    template.id: template
                    for template in (
                        FeeTemplate.objects
                        .select_for_update()
                        .filter(tenant=tenant, id__in=template_ids)
                        .order_by("id")
                    )
                }
                mutate_locked_rows(locked_fees, locked_templates)
                mutation_locked.set()
                self.assertTrue(
                    generation_released.wait(timeout=5),
                    "generation did not enter the locked section",
                )
                # 생성 thread가 select_for_update에서 대기하는 동안 변경을 commit한다.
                time.sleep(0.2)

            generation_thread.join(timeout=10)

        self.assertFalse(generation_thread.is_alive(), "generation thread deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        return results[0]

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

    def test_one_time_fee_different_month_generation_creates_single_item(self):
        tenant, invoice = self._setup_invoice(total=100_000)
        invoice.delete()
        student = Student.objects.get(tenant=tenant)
        template = FeeTemplate.objects.get(tenant=tenant)
        template.billing_cycle = FeeTemplate.BillingCycle.ONE_TIME
        template.save(update_fields=["billing_cycle", "updated_at"])
        StudentFee.objects.create(
            tenant=tenant,
            student=student,
            fee_template=template,
        )

        start_barrier = threading.Barrier(2)
        invoice_number_barrier = threading.Barrier(2)
        original_next_invoice_number = services._next_invoice_number
        results = []
        errors = []

        def synchronized_next_invoice_number(*args, **kwargs):
            try:
                invoice_number_barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            return original_next_invoice_number(*args, **kwargs)

        def worker(month: int):
            start_barrier.wait()
            try:
                results.append(generate_monthly_invoices(
                    tenant,
                    billing_year=2026,
                    billing_month=month,
                    due_date=timezone.localdate() + timedelta(days=10),
                ))
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
            finally:
                close_old_connections()

        with patch(
            "apps.domains.fees.services._next_invoice_number",
            side_effect=synchronized_next_invoice_number,
        ):
            t1 = threading.Thread(target=worker, args=(5,))
            t2 = threading.Thread(target=worker, args=(6,))
            t1.start(); t2.start()
            t1.join(timeout=10); t2.join(timeout=10)

        self.assertFalse(t1.is_alive() or t2.is_alive(), "generation threads deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(
            InvoiceItem.objects.filter(
                tenant=tenant,
                invoice__student=student,
                fee_template=template,
                invoice__status__in=["PENDING", "PARTIAL", "PAID", "OVERDUE"],
            ).count(),
            1,
            results,
        )

    def test_fee_amounts_committed_during_generation_use_locked_fresh_values(self):
        tenant, student, template, student_fee = self._setup_generation_fee()
        second_template = FeeTemplate.objects.create(
            tenant=tenant,
            name="동시성교재비",
            fee_type=FeeTemplate.FeeType.TEXTBOOK,
            amount=50_000,
        )
        second_fee = StudentFee.objects.create(
            tenant=tenant,
            student=student,
            fee_template=second_template,
        )

        def mutate(locked_fees, locked_templates):
            fees_by_id = {fee.id: fee for fee in locked_fees}
            first_locked = fees_by_id[student_fee.id]
            first_template = locked_templates[template.id]
            first_template.amount = 140_000
            first_template.save(update_fields=["amount", "updated_at"])
            first_locked.discount_amount = 20_000
            first_locked.save(update_fields=["discount_amount", "updated_at"])

            second_locked = fees_by_id[second_fee.id]
            second_locked.adjusted_amount = 80_000
            second_locked.discount_amount = 5_000
            second_locked.save(
                update_fields=["adjusted_amount", "discount_amount", "updated_at"],
            )

        result = self._generate_while_fee_rows_are_locked(
            tenant,
            student,
            mutate,
        )

        self.assertEqual(result["created"], 1, result)
        invoice = StudentInvoice.objects.get(
            tenant=tenant,
            student=student,
            billing_year=2026,
            billing_month=5,
        )
        self.assertEqual(invoice.total_amount, 195_000)
        self.assertEqual(
            list(invoice.items.order_by("fee_template_id").values_list("amount", flat=True)),
            [120_000, 75_000],
        )

    def test_fee_state_committed_during_generation_is_rechecked_after_lock(self):
        tenant, student, template, _student_fee = self._setup_generation_fee()

        def mutate(locked_fees, locked_templates):
            locked_fee = locked_fees[0]
            locked_fee.is_active = False
            locked_fee.billing_end_month = "2026-04"
            locked_fee.save(
                update_fields=["is_active", "billing_end_month", "updated_at"],
            )
            locked_template = locked_templates[template.id]
            locked_template.is_active = False
            locked_template.save(update_fields=["is_active", "updated_at"])

        result = self._generate_while_fee_rows_are_locked(
            tenant,
            student,
            mutate,
        )

        self.assertEqual(result["created"], 0, result)
        self.assertFalse(
            StudentInvoice.objects.filter(
                tenant=tenant,
                student=student,
                billing_year=2026,
                billing_month=5,
            ).exists(),
        )

    def test_template_cycle_committed_during_generation_prevents_one_time_rebill(self):
        tenant, student, template, _student_fee = self._setup_generation_fee()
        prior_invoice = StudentInvoice.objects.create(
            tenant=tenant,
            student=student,
            invoice_number=f"FEE-PRIOR-{uuid.uuid4().hex[:8]}",
            billing_year=2026,
            billing_month=4,
            total_amount=template.amount,
            due_date=timezone.localdate() + timedelta(days=10),
        )
        InvoiceItem.objects.create(
            tenant=tenant,
            invoice=prior_invoice,
            fee_template=template,
            description=template.name,
            amount=template.amount,
        )

        def mutate(_locked_fees, locked_templates):
            locked_template = locked_templates[template.id]
            locked_template.billing_cycle = FeeTemplate.BillingCycle.ONE_TIME
            locked_template.save(update_fields=["billing_cycle", "updated_at"])

        result = self._generate_while_fee_rows_are_locked(
            tenant,
            student,
            mutate,
        )

        self.assertEqual(result["created"], 0, result)
        self.assertEqual(
            InvoiceItem.objects.filter(
                tenant=tenant,
                invoice__student=student,
                fee_template=template,
                invoice__status__in=["PENDING", "PARTIAL", "PAID", "OVERDUE"],
            ).count(),
            1,
        )
