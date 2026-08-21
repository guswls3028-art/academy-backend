"""PostgreSQL regression coverage for session-level homework roster writes."""

from __future__ import annotations

import threading
import time
import unittest
import uuid
from unittest.mock import patch

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.homework.models import HomeworkEnrollment
from apps.domains.homework.views.homework_enrollment_view import (
    HomeworkEnrollmentManageView,
)
from apps.support.homework.view_dependencies import (
    get_session_for_homework_enrollment,
)


pytestmark = pytest.mark.django_db(transaction=True)
User = get_user_model()
Enrollment = apps.get_model("enrollment", "Enrollment")
SessionEnrollment = apps.get_model("enrollment", "SessionEnrollment")
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
Student = apps.get_model("students", "Student")


class HomeworkEnrollmentConcurrencyPGTests(TransactionTestCase):
    """The session owner lock makes each complete replacement indivisible."""

    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL row-level locking is required for this regression test."
            )
        from django.apps import apps as django_apps

        cls.available_apps = [
            app_config.name for app_config in django_apps.get_app_configs()
        ]
        super().setUpClass()

    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.tenant = Tenant.objects.create(
            name=f"Homework roster {suffix}",
            code=f"homework_roster_{suffix}",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            tenant=self.tenant,
            username=f"homework-roster-{suffix}",
            password="test1234",
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Homework roster",
            name="Homework roster",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="Homework roster session",
        )
        self.enrollments = [self._create_enrollment(index) for index in range(3)]
        HomeworkEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollments[0],
        )
        self.factory = APIRequestFactory()

    def _create_enrollment(self, index: int):
        student_user = User.objects.create_user(
            tenant=self.tenant,
            username=f"homework-roster-student-{index}-{uuid.uuid4().hex[:6]}",
            password="test1234",
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name=f"Roster student {index}",
            ps_number=f"HR{uuid.uuid4().hex[:8]}",
            omr_code=f"{index + 1:08d}",
            parent_phone=f"0100000000{index}",
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=enrollment,
        )
        return enrollment

    def test_concurrent_replacements_finish_as_one_complete_request(self):
        first_locked = threading.Event()
        second_attempting = threading.Event()
        errors: list[str] = []
        responses = {}

        def instrumented_session_lookup(**kwargs):
            thread_name = threading.current_thread().name
            if kwargs.get("for_update") and thread_name == "first-roster-writer":
                session = get_session_for_homework_enrollment(**kwargs)
                first_locked.set()
                if not second_attempting.wait(timeout=5):
                    raise AssertionError("second writer did not attempt the lock")
                time.sleep(0.2)
                return session
            if kwargs.get("for_update") and thread_name == "second-roster-writer":
                if not first_locked.wait(timeout=5):
                    raise AssertionError("first writer did not acquire the lock")
                second_attempting.set()
            return get_session_for_homework_enrollment(**kwargs)

        def replace_roster(name: str, enrollment_ids: list[int]) -> None:
            close_old_connections()
            try:
                request = self.factory.put(
                    f"/homework/enrollments/?session_id={self.session.id}",
                    {
                        "session_id": self.session.id,
                        "enrollment_ids": enrollment_ids,
                    },
                    format="json",
                )
                request.tenant = Tenant.objects.get(id=self.tenant.id)
                force_authenticate(
                    request,
                    user=User.objects.get(id=self.admin.id),
                )
                responses[name] = HomeworkEnrollmentManageView.as_view()(request)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc!r}")
            finally:
                close_old_connections()

        first_ids = [self.enrollments[0].id, self.enrollments[1].id]
        second_ids = [self.enrollments[2].id]
        with patch(
            "apps.domains.homework.views.homework_enrollment_view."
            "get_session_for_homework_enrollment",
            side_effect=instrumented_session_lookup,
        ):
            first = threading.Thread(
                target=replace_roster,
                name="first-roster-writer",
                args=("first", first_ids),
            )
            second = threading.Thread(
                target=replace_roster,
                name="second-roster-writer",
                args=("second", second_ids),
            )
            first.start()
            second.start()
            first.join(timeout=10)
            second.join(timeout=10)

        self.assertFalse(first.is_alive(), "first writer thread did not finish")
        self.assertFalse(second.is_alive(), "second writer thread did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(responses["first"].status_code, 200)
        self.assertEqual(responses["second"].status_code, 200)
        self.assertEqual(
            set(
                HomeworkEnrollment.objects.filter(
                    tenant=self.tenant,
                    session=self.session,
                ).values_list("enrollment_id", flat=True)
            ),
            set(second_ids),
        )
