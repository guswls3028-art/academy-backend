import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue

from django.db import connection, transaction
from django.test import TransactionTestCase
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import TenantMembership
from apps.domains.enrollment.test_support import (
    create_enrollment_fixture,
    create_session_enrollment_fixture,
)
from apps.domains.parents.test_support import create_parent_account_fixture
from apps.domains.student_app.tests.test_session_tenant_isolation import (
    _create_lecture,
    _create_session,
    _create_student,
    _create_tenant,
    _create_user,
)


class StudentScheduleWriteConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL is required for schedule row-lock contention")
        super().setUpClass()

    def setUp(self):
        self.tenant = _create_tenant("schedule-write-race")
        self.user = _create_user(self.tenant, "schedule-student")
        self.student = _create_student(self.tenant, self.user, "ScheduleStudent")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.parent = create_parent_account_fixture(
            tenant=self.tenant,
            parent_phone="01081003003",
            student_name=self.student.name,
            initial_password="Synthetic-parent-1234",
        ).parent
        self.student.parent = self.parent
        self.student.save(update_fields=["parent"])
        self.today = timezone.localdate()
        lecture = _create_lecture(self.tenant, "Schedule race lecture")
        self.first = _create_session(lecture, session_date=self.today - timedelta(days=3))
        self.second = _create_session(lecture, order=2, session_date=self.today - timedelta(days=2))
        self.future = _create_session(lecture, order=3, session_date=self.today + timedelta(days=3))
        enrollment = create_enrollment_fixture(
            tenant=self.tenant, student=self.student, lecture=lecture, status="ACTIVE",
        )
        for session in (self.first, self.second, self.future):
            create_session_enrollment_fixture(tenant=self.tenant, enrollment=enrollment, session=session)

    def _request(self, action, user, target=None, *, parent=False):
        path = f"/api/v1/student/sessions/{action}/"
        factory = APIRequestFactory()
        headers = {"HTTP_X_STUDENT_ID": str(self.student.pk)} if parent else {}
        if action == "me":
            request = factory.get(path, **headers)
        else:
            body = {} if target is None else {"id": target}
            request = factory.post(path, body, format="json", **headers)
        request.tenant = self.tenant
        force_authenticate(request, user=user)
        return resolve(path).func(request)

    def _write_together(self, student_action, parent_action):
        pids = Queue()
        parent_user = self.parent.user

        def write(action, user, parent=False):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.put(cursor.fetchone()[0])
                return self._request(action[0], user, action[1], parent=parent)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            with transaction.atomic():
                type(self.student).objects.select_for_update().get(tenant=self.tenant, pk=self.student.pk)
                student_write = pool.submit(write, student_action, self.user)
                parent_write = pool.submit(write, parent_action, parent_user, True)
                first_pid = pids.get(timeout=10)
                second_pid = pids.get(timeout=10)
                deadline = time.monotonic() + 10
                with connection.cursor() as cursor:
                    while time.monotonic() < deadline:
                        cursor.execute(
                            "SELECT cardinality(pg_blocking_pids(%s)), cardinality(pg_blocking_pids(%s))",
                            [first_pid, second_pid],
                        )
                        if all(cursor.fetchone()):
                            break
                        time.sleep(0.02)
                    else:
                        for future in (student_write, parent_write):
                            if future.done():
                                response = future.result()
                                self.assertEqual(response.status_code, 200, response.data)
                        self.fail("Both schedule writes must compete for the student row")
            for future in (student_write, parent_write):
                result = future.result(timeout=20)
                self.assertEqual(result.status_code, 200, result.data)

    def _assert_visibility(self, *, hidden, visible):
        self.student.refresh_from_db()
        self.assertCountEqual(self.student.schedule_hidden_ids, hidden)
        for user, is_parent in ((self.user, False), (self.parent.user, True)):
            result = self._request("me", user, parent=is_parent)
            self.assertEqual(result.status_code, 200, result.data)
            self.assertCountEqual((row["id"] for row in result.data), visible)

    def test_student_and_parent_can_hide_different_past_sessions_at_once(self):
        self._write_together(("hide", self.first.pk), ("hide", self.second.pk))
        self._assert_visibility(hidden=[self.first.pk, self.second.pk], visible=[self.future.pk])

    def test_parent_undo_does_not_erase_student_hide_of_other_session(self):
        self.student.schedule_hidden_ids = [self.first.pk]
        self.student.save(update_fields=["schedule_hidden_ids"])
        self._write_together(("hide", self.second.pk), ("unhide", self.first.pk))
        self._assert_visibility(hidden=[self.second.pk], visible=[self.first.pk, self.future.pk])

    def test_clearing_past_keeps_concurrent_future_hide(self):
        self.student.schedule_hidden_ids = [self.first.pk]
        self.student.save(update_fields=["schedule_hidden_ids"])
        self._write_together(("hide", self.future.pk), ("clear-past", None))
        self._assert_visibility(hidden=[self.future.pk], visible=[])
        self.assertEqual(self.student.schedule_hidden_before, self.today - timedelta(days=1))
