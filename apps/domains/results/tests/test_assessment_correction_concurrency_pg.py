"""PostgreSQL concurrency coverage for score-preserving teacher decisions."""

from __future__ import annotations

import threading
import time
import unittest
import uuid

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.views.session_scores_view import SessionScoreCorrectionView


pytestmark = pytest.mark.django_db(transaction=True)
User = get_user_model()
Attendance = apps.get_model("attendance", "Attendance")
Enrollment = apps.get_model("enrollment", "Enrollment")
SessionEnrollment = apps.get_model("enrollment", "SessionEnrollment")
HomeworkAssignment = apps.get_model("homework", "HomeworkAssignment")
Homework = apps.get_model("homework_results", "Homework")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
AssessmentCorrection = apps.get_model("progress", "AssessmentCorrection")
Student = apps.get_model("students", "Student")


class AssessmentCorrectionConcurrencyPGTests(TransactionTestCase):
    """An unscored homework decision still has a row lock for first-write CAS."""

    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL row-level locking is required for this regression test."
            )
        super().setUpClass()

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.tenant = Tenant.objects.create(
            name=f"Correction Lock {suffix}",
            code=f"correction_lock_{suffix}",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username=f"correction-lock-{suffix}",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Correction Lock Lecture",
            name="Correction Lock Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="Session 1",
        )
        student_user = User.objects.create_user(
            username=f"correction-student-{suffix}",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Correction Student",
            ps_number=f"P{suffix}",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
            status="PRESENT",
        )
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Unscored Homework",
        )
        self.assignment = HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=self.enrollment,
        )

    def test_first_unscored_homework_decision_honors_expected_updated_at(self):
        correction_created = threading.Event()
        competing_request_started = threading.Event()
        statuses: list[int] = []
        errors: list[str] = []

        def first_writer() -> None:
            close_old_connections()
            try:
                with transaction.atomic():
                    HomeworkAssignment.objects.select_for_update().get(
                        id=self.assignment.id
                    )
                    AssessmentCorrection.objects.create(
                        tenant_id=self.tenant.id,
                        enrollment_id=self.enrollment.id,
                        session_id=self.session.id,
                        source_type=AssessmentCorrection.SourceType.HOMEWORK,
                        source_id=self.homework.id,
                        completed=True,
                        note="현장 검사 완료",
                        updated_by_id=self.admin.id,
                    )
                    correction_created.set()
                    if not competing_request_started.wait(timeout=5):
                        raise AssertionError("competing request did not start")
                    time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"first: {exc!r}")
            finally:
                close_old_connections()

        def second_writer() -> None:
            close_old_connections()
            try:
                if not correction_created.wait(timeout=5):
                    raise AssertionError("first correction was not created")
                request = APIRequestFactory().patch(
                    f"/api/v1/results/admin/sessions/{self.session.id}/score-correction/",
                    {
                        "enrollment_id": self.enrollment.id,
                        "source_type": "homework",
                        "source_id": self.homework.id,
                        "completed": False,
                        "expected_updated_at": None,
                    },
                    format="json",
                )
                request.tenant = self.tenant
                force_authenticate(request, user=self.admin)
                competing_request_started.set()
                response = SessionScoreCorrectionView.as_view()(
                    request,
                    session_id=self.session.id,
                )
                statuses.append(response.status_code)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"second: {exc!r}")
            finally:
                close_old_connections()

        first = threading.Thread(target=first_writer)
        second = threading.Thread(target=second_writer)
        first.start()
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)

        self.assertFalse(first.is_alive(), "first writer did not finish")
        self.assertFalse(second.is_alive(), "second writer did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(statuses, [409])
        correction = AssessmentCorrection.objects.get(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.session,
            source_type=AssessmentCorrection.SourceType.HOMEWORK,
            source_id=self.homework.id,
        )
        self.assertTrue(correction.completed)
