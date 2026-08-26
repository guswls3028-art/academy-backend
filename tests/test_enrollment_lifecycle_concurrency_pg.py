from __future__ import annotations

import threading
import unittest
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError

from apps.core.models import Tenant
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.enrollment.services.lifecycle import (
    bulk_create_enrollments,
    bulk_create_session_enrollments,
)
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student
from apps.support.enrollment.lifecycle_dependencies import (
    ensure_session_roster_membership,
)


User = get_user_model()


class EnrollmentLifecycleConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL row-level locking is required for lifecycle concurrency."
            )
        super().setUpClass()

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.tenant = Tenant.objects.create(
            name=f"Enrollment concurrency {suffix}",
            code=f"enrollment-concurrency-{suffix}",
            is_active=True,
        )
        self.students = [self._student(suffix, index) for index in range(2)]

    def _student(self, suffix: str, index: int) -> Student:
        user = User.objects.create_user(
            tenant=self.tenant,
            username=f"enrollment-concurrency-{suffix}-{index}",
            password="test1234",
        )
        return Student.objects.create(
            tenant=self.tenant,
            user=user,
            name=f"Concurrent student {index}",
            ps_number=f"EC-{suffix}-{index}",
            omr_code=f"{index + 1:08d}",
            parent_phone=f"0108111000{index}",
        )

    def _lecture(self, title: str) -> Lecture:
        return Lecture.objects.create(
            tenant=self.tenant,
            title=title,
            name=title,
            subject="MATH",
        )

    @staticmethod
    def _start_and_join(threads: list[threading.Thread]) -> None:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

    def test_reverse_order_bulk_enrollment_locks_students_without_deadlock(self):
        lectures = [self._lecture("Bulk A"), self._lecture("Bulk B")]
        student_ids = [student.id for student in self.students]
        barrier = threading.Barrier(2, timeout=10)
        errors: list[BaseException] = []

        def worker(lecture_id: int, requested_student_ids: list[int]) -> None:
            close_old_connections()
            try:
                tenant = Tenant.objects.get(pk=self.tenant.pk)
                barrier.wait()
                bulk_create_enrollments(
                    tenant=tenant,
                    lecture_id=lecture_id,
                    student_ids=requested_student_ids,
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(
                target=worker,
                args=(lectures[0].id, student_ids),
                name="bulk-enrollment-forward",
            ),
            threading.Thread(
                target=worker,
                args=(lectures[1].id, list(reversed(student_ids))),
                name="bulk-enrollment-reverse",
            ),
        ]
        with patch(
            "apps.domains.enrollment.services.lifecycle.schedule_pending_account_notice"
        ):
            self._start_and_join(threads)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture__in=lectures,
            ).count(),
            4,
        )

    def test_reverse_order_session_batch_locks_students_then_enrollments(self):
        lecture = self._lecture("Session batch")
        session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="Session batch",
        )
        enrollments = [
            Enrollment.objects.create(
                tenant=self.tenant,
                lecture=lecture,
                student=student,
                status="ACTIVE",
            )
            for student in self.students
        ]
        enrollment_ids = [enrollment.id for enrollment in enrollments]
        barrier = threading.Barrier(2, timeout=10)
        errors: list[BaseException] = []

        def worker(requested_enrollment_ids: list[int]) -> None:
            close_old_connections()
            try:
                tenant = Tenant.objects.get(pk=self.tenant.pk)
                barrier.wait()
                bulk_create_session_enrollments(
                    tenant=tenant,
                    session_id=session.id,
                    enrollment_ids=requested_enrollment_ids,
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(
                target=worker,
                args=(enrollment_ids,),
                name="session-enrollment-forward",
            ),
            threading.Thread(
                target=worker,
                args=(list(reversed(enrollment_ids)),),
                name="session-enrollment-reverse",
            ),
        ]
        self._start_and_join(threads)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                session=session,
            ).count(),
            2,
        )
        self.assertEqual(
            Attendance.objects.filter(
                tenant=self.tenant,
                session=session,
            ).count(),
            2,
        )

    def test_stale_active_enrollment_is_reloaded_after_concurrent_deactivation(self):
        lecture = self._lecture("Stale enrollment")
        session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="Stale enrollment",
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            lecture=lecture,
            student=self.students[0],
            status="ACTIVE",
        )
        stale_enrollment = Enrollment.objects.select_related("student").get(
            pk=enrollment.pk
        )
        barrier = threading.Barrier(2, timeout=10)
        deactivated = threading.Event()
        outcomes: list[str] = []
        errors: list[BaseException] = []

        def deactivate() -> None:
            close_old_connections()
            try:
                barrier.wait()
                Enrollment.objects.filter(pk=enrollment.pk).update(status="INACTIVE")
                outcomes.append("deactivated")
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                deactivated.set()
                close_old_connections()

        def add_to_roster() -> None:
            close_old_connections()
            try:
                tenant = Tenant.objects.get(pk=self.tenant.pk)
                current_session = Session.objects.select_related("lecture").get(
                    pk=session.pk
                )
                barrier.wait()
                if not deactivated.wait(timeout=10):
                    raise AssertionError("deactivation did not finish")
                with self.assertRaises(ValidationError):
                    ensure_session_roster_membership(
                        tenant=tenant,
                        session=current_session,
                        enrollment=stale_enrollment,
                    )
                outcomes.append("roster-blocked")
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=deactivate, name="deactivate-enrollment"),
            threading.Thread(target=add_to_roster, name="add-stale-enrollment"),
        ]
        self._start_and_join(threads)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertCountEqual(outcomes, ["deactivated", "roster-blocked"])
        self.assertFalse(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                session=session,
                enrollment=enrollment,
            ).exists()
        )
        self.assertFalse(
            Attendance.objects.filter(
                tenant=self.tenant,
                session=session,
                enrollment=enrollment,
            ).exists()
        )
