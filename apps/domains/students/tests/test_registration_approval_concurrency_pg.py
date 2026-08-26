from __future__ import annotations

import threading
import unittest
from unittest import mock

import pytest
from django.contrib.auth.hashers import make_password
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.core.models import Tenant
from apps.domains.students.models import Student, StudentRegistrationRequest
from apps.domains.students.services import registration_approval
from apps.domains.students.services.creation import create_student_account
from apps.domains.students.services.lifecycle import soft_delete_student
from apps.support.students.lifecycle_dependencies import ensure_parent_account_for_student


pytestmark = pytest.mark.django_db(transaction=True)


class RegistrationApprovalConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL is required for registration approval concurrency verification."
            )
        super().setUpClass()

    def test_same_identity_approvals_create_exactly_one_account_graph(self):
        tenant = Tenant.objects.create(
            name="가입 승인 동시성 학원",
            code="registration-approval-race",
            is_active=True,
        )
        registrations = [
            StudentRegistrationRequest.objects.create(
                tenant=tenant,
                status=StudentRegistrationRequest.PENDING,
                initial_password=make_password("signup-password"),
                initial_password_plain="",
                name="동시승인학생",
                username=username,
                parent_phone="01075552222",
                phone="01075551111",
                school_type="HIGH",
                high_school="동시성고",
                origin_middle_school="동시성중",
                grade=1,
                gender="M",
                address="서울",
            )
            for username in ("RACE-REQUEST-A", "RACE-REQUEST-B")
        ]

        real_create_student_account = registration_approval.create_student_account
        both_creates_reached = threading.Event()
        first_create_reached = threading.Event()
        create_call_lock = threading.Lock()
        create_call_count = 0
        outcomes: list[tuple[str, int]] = []
        errors: list[Exception] = []

        def synchronized_create_student_account(**kwargs):
            nonlocal create_call_count
            with create_call_lock:
                create_call_count += 1
                if create_call_count == 1:
                    first_create_reached.set()
                else:
                    both_creates_reached.set()
            both_creates_reached.wait(timeout=3)
            return real_create_student_account(**kwargs)

        def approve(registration_id: int):
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                result = registration_approval.approve_registration_request(
                    tenant=thread_tenant,
                    registration_id=registration_id,
                )
                outcomes.append(("approved", result.student.id))
            except registration_approval.RegistrationApprovalError as exc:
                outcomes.append(("rejected", exc.status_code))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch.object(
            registration_approval,
            "create_student_account",
            side_effect=synchronized_create_student_account,
        ):
            threads = [
                threading.Thread(target=approve, args=(registration.id,))
                for registration in registrations
            ]
            for thread in threads:
                thread.start()
            self.assertTrue(first_create_reached.wait(timeout=10))
            for thread in threads:
                thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(kind for kind, _value in outcomes), ["approved", "rejected"])
        self.assertEqual([value for kind, value in outcomes if kind == "rejected"], [409])
        self.assertEqual(create_call_count, 1)
        self.assertEqual(Student.objects.filter(tenant=tenant).count(), 1)
        self.assertEqual(
            StudentRegistrationRequest.objects.filter(
                tenant=tenant,
                status=StudentRegistrationRequest.APPROVED,
            ).count(),
            1,
        )
        self.assertEqual(
            StudentRegistrationRequest.objects.filter(
                tenant=tenant,
                status=StudentRegistrationRequest.PENDING,
                student__isnull=True,
            ).count(),
            1,
        )

    def test_parent_ensure_and_existing_student_approval_do_not_deadlock(self):
        tenant = Tenant.objects.create(
            name="가입 승인 잠금 순서 학원",
            code="registration-approval-lock-order",
            is_active=True,
        )
        student = create_student_account(
            tenant=tenant,
            password="existing-password",
            student_data={
                "name": "잠금순서학생",
                "ps_number": "LOCK-ORDER-STUDENT",
                "phone": "01078881111",
                "parent_phone": "01078882222",
                "omr_code": "78881111",
                "uses_identifier": False,
                "school_type": "HIGH",
                "grade": 1,
            },
        ).student
        registration = StudentRegistrationRequest.objects.create(
            tenant=tenant,
            status=StudentRegistrationRequest.PENDING,
            initial_password=make_password("signup-password"),
            initial_password_plain="",
            name=student.name,
            username="LOCK-ORDER-REQUEST",
            parent_phone=student.parent_phone,
            phone=student.phone,
            school_type="HIGH",
            high_school="잠금순서고",
            origin_middle_school="잠금순서중",
            grade=1,
            gender="M",
            address="서울",
        )
        start = threading.Barrier(2, timeout=10)
        outcomes: list[tuple[str, int]] = []
        errors: list[Exception] = []

        def approve():
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                start.wait()
                result = registration_approval.approve_registration_request(
                    tenant=thread_tenant,
                    registration_id=registration.id,
                )
                outcomes.append(("approval", result.student.id))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def ensure_parent():
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                start.wait()
                result = ensure_parent_account_for_student(
                    tenant=thread_tenant,
                    parent_phone=student.parent_phone,
                    student_name=student.name,
                    initial_password="unused-existing-parent-password",
                )
                outcomes.append(("parent", result.parent.id))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=approve), threading.Thread(target=ensure_parent)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual({kind for kind, _value in outcomes}, {"approval", "parent"})
        self.assertEqual([value for kind, value in outcomes if kind == "approval"], [student.id])
        self.assertEqual(Student.objects.filter(tenant=tenant).count(), 1)
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.APPROVED)
        self.assertEqual(registration.student_id, student.id)

    def test_same_deleted_resolution_retry_adopts_password_once(self):
        tenant = Tenant.objects.create(
            name="가입 복구 재시도 학원",
            code="registration-recovery-retry",
            is_active=True,
        )
        student = create_student_account(
            tenant=tenant,
            password="existing-password",
            student_data={
                "name": "복구재시도학생",
                "ps_number": "RECOVERY-OLD",
                "phone": "01079991111",
                "parent_phone": "01079992222",
                "omr_code": "79991111",
                "uses_identifier": False,
                "school_type": "HIGH",
                "grade": 1,
            },
        ).student
        soft_delete_student(student, tenant=tenant)
        registration = StudentRegistrationRequest.objects.create(
            tenant=tenant,
            status=StudentRegistrationRequest.PENDING,
            initial_password=make_password("signup-password"),
            initial_password_plain="",
            name=student.name,
            username="RECOVERY-NEW",
            parent_phone=student.parent_phone,
            phone=student.phone,
            school_type="HIGH",
            grade=1,
        )
        outcomes: list[tuple[str, int]] = []
        errors: list[Exception] = []
        start = threading.Barrier(2, timeout=10)
        real_adopt = registration_approval.adopt_password_hash

        def resolve():
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                start.wait()
                result = registration_approval.resolve_deleted_registration_request(
                    tenant=thread_tenant,
                    registration_id=registration.id,
                    student_id=student.id,
                )
                outcomes.append(("approved", result.student.id))
            except registration_approval.RegistrationApprovalError as exc:
                outcomes.append(("rejected", exc.status_code))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with mock.patch.object(
            registration_approval,
            "adopt_password_hash",
            wraps=real_adopt,
        ) as adopt_mock:
            threads = [threading.Thread(target=resolve) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(kind for kind, _value in outcomes), ["approved", "rejected"])
        self.assertEqual([value for kind, value in outcomes if kind == "approved"], [student.id])
        self.assertEqual([value for kind, value in outcomes if kind == "rejected"], [409])
        self.assertEqual(adopt_mock.call_count, 1)
        registration.refresh_from_db()
        student.refresh_from_db()
        student.user.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.APPROVED)
        self.assertEqual(registration.student_id, student.id)
        self.assertIsNone(student.deleted_at)
        self.assertTrue(student.user.check_password("signup-password"))

    def test_parent_ensure_and_deleted_resolution_keep_lock_order(self):
        tenant = Tenant.objects.create(
            name="가입 복구 잠금 학원",
            code="registration-recovery-lock-order",
            is_active=True,
        )
        student = create_student_account(
            tenant=tenant,
            password="existing-password",
            student_data={
                "name": "복구잠금학생",
                "ps_number": "RECOVERY-LOCK-OLD",
                "phone": "01078883333",
                "parent_phone": "01078884444",
                "omr_code": "78883333",
                "uses_identifier": False,
                "school_type": "HIGH",
                "grade": 1,
            },
        ).student
        soft_delete_student(student, tenant=tenant)
        registration = StudentRegistrationRequest.objects.create(
            tenant=tenant,
            status=StudentRegistrationRequest.PENDING,
            initial_password=make_password("signup-password"),
            initial_password_plain="",
            name=student.name,
            username="RECOVERY-LOCK-NEW",
            parent_phone=student.parent_phone,
            phone=student.phone,
            school_type="HIGH",
            grade=1,
        )
        start = threading.Barrier(2, timeout=10)
        outcomes: list[str] = []
        errors: list[Exception] = []

        def resolve():
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                start.wait()
                registration_approval.resolve_deleted_registration_request(
                    tenant=thread_tenant,
                    registration_id=registration.id,
                    student_id=student.id,
                )
                outcomes.append("resolution")
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def ensure_parent():
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                start.wait()
                ensure_parent_account_for_student(
                    tenant=thread_tenant,
                    parent_phone=student.parent_phone,
                    student_name=student.name,
                    initial_password="unused-existing-parent-password",
                )
                outcomes.append("parent")
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=resolve), threading.Thread(target=ensure_parent)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(set(outcomes), {"resolution", "parent"})

    def test_ordinary_approval_and_deleted_resolution_serialize_fail_closed(self):
        tenant = Tenant.objects.create(
            name="가입 복구 승인 경합 학원",
            code="registration-recovery-approval-race",
            is_active=True,
        )
        student = create_student_account(
            tenant=tenant,
            password="existing-password",
            student_data={
                "name": "복구승인경합학생",
                "ps_number": "RECOVERY-RACE-OLD",
                "phone": "01076663333",
                "parent_phone": "01076664444",
                "omr_code": "76663333",
                "uses_identifier": False,
                "school_type": "HIGH",
                "grade": 1,
            },
        ).student
        soft_delete_student(student, tenant=tenant)
        registration = StudentRegistrationRequest.objects.create(
            tenant=tenant,
            status=StudentRegistrationRequest.PENDING,
            initial_password=make_password("signup-password"),
            initial_password_plain="",
            name=student.name,
            username="RECOVERY-RACE-NEW",
            parent_phone=student.parent_phone,
            phone=student.phone,
            school_type="HIGH",
            grade=1,
        )
        start = threading.Barrier(2, timeout=10)
        outcomes: list[tuple[str, int]] = []
        errors: list[Exception] = []

        def ordinary_approve():
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                start.wait()
                registration_approval.approve_registration_request(
                    tenant=thread_tenant,
                    registration_id=registration.id,
                )
                outcomes.append(("ordinary", 200))
            except registration_approval.RegistrationApprovalError as exc:
                outcomes.append(("ordinary", exc.status_code))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def resolve_deleted():
            close_old_connections()
            try:
                thread_tenant = Tenant.objects.get(pk=tenant.pk)
                start.wait()
                registration_approval.resolve_deleted_registration_request(
                    tenant=thread_tenant,
                    registration_id=registration.id,
                    student_id=student.id,
                )
                outcomes.append(("resolution", 200))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=ordinary_approve), threading.Thread(target=resolve_deleted)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual([status for kind, status in outcomes if kind == "resolution"], [200])
        self.assertEqual(len([status for kind, status in outcomes if kind == "ordinary"]), 1)
        self.assertIn(
            [status for kind, status in outcomes if kind == "ordinary"][0],
            (400, 409),
        )
        registration.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.APPROVED)
        self.assertEqual(registration.student_id, student.id)
        self.assertIsNone(student.deleted_at)
