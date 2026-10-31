import threading
import unittest

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from apps.domains.attendance.models import Attendance
from apps.domains.attendance.services.qr import QrConflict, record
from apps.domains.students.models import Student


class QrAttendanceConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("Real row-lock checks require PostgreSQL")
        super().setUpClass()

    def setUp(self):
        from tests.test_qr_attendance import AttendanceQrTests
        AttendanceQrTests.setUp(self)

    def simultaneous(self, *, clinic=False, action="check_in"):
        barrier = threading.Barrier(2, timeout=10)
        results, errors = [], []

        def worker():
            close_old_connections()
            try:
                barrier.wait()
                results.append(record(
                    tenant=self.tenant, student=self.student, actor=self.user,
                    token=(self.clinic_point if clinic else self.point).token,
                    target_id=self.participant.pk if clinic else self.session.pk, action=action,
                ))
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "QR lock deadlock")
        self.assertEqual(errors, [])
        self.assertEqual(sorted(row["already_recorded"] for row in results), [False, True])
        self.assertEqual(results[0]["recorded_at"], results[1]["recorded_at"])

    def test_duplicate_class_requests_create_one_attendance(self):
        self.simultaneous()
        self.assertEqual(Attendance.objects.filter(enrollment=self.enrollment, session=self.session).count(), 1)

    def test_duplicate_clinic_requests_do_not_toggle_or_complete(self):
        self.simultaneous(clinic=True)
        self.participant.refresh_from_db()
        self.assertIsNone(self.participant.checked_out_at)
        self.simultaneous(clinic=True, action="check_out")
        self.participant.refresh_from_db()
        self.assertIsNotNone(self.participant.checked_out_at)
        self.assertIsNone(self.participant.completed_at)

    def test_manual_correction_wins_before_waiting_qr_write(self):
        row = Attendance.objects.create(tenant=self.tenant, enrollment=self.enrollment, session=self.session)
        started = threading.Event()
        errors = []

        def worker():
            close_old_connections()
            try:
                started.set()
                record(tenant=self.tenant, student=self.student, actor=self.user,
                       token=self.point.token, target_id=self.session.pk, action="check_in")
            except BaseException as error:
                errors.append(error)
            finally:
                close_old_connections()

        with transaction.atomic():
            Student.objects.select_for_update().get(pk=self.student.pk)
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(started.wait(timeout=10))
            row.status = "ABSENT"
            row.save(update_fields=["status"])
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], QrConflict)
        row.refresh_from_db()
        self.assertEqual(row.status, "ABSENT")
        self.assertIsNone(row.qr_checked_in_at)
