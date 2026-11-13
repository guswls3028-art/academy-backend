"""PostgreSQL regression for active playback renewal racing an attendance write."""

import threading
import time
from unittest import skipUnless

from django.apps import apps
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from academy.adapters.db.django.repositories_enrollment import (
    lock_enrollment_by_id_with_lecture,
)
from apps.core.models import Tenant, TenantMembership, User
from apps.domains.enrollment.test_support import (
    create_enrollment_fixture,
    create_session_enrollment_fixture,
)
from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
from apps.domains.students.test_support import create_student_fixture
from apps.domains.video.drm import verify_playback_token
from apps.domains.video.models import AccessMode, Video, VideoAccess, VideoPlaybackSession
from apps.support.student_app.video_media import issue_playback_access_grant

Attendance = apps.get_model("attendance", "Attendance")


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock contract")
@override_settings(VIDEO_PLAYBACK_TTL_SECONDS=600)
class PlaybackRenewalLockConcurrencyPGTests(TransactionTestCase):
    def test_attendance_commit_does_not_deadlock_waiting_renewal(self):
        tenant = Tenant.objects.create(
            code="qa-playback-renew-lock", name="Playback Renew Lock", is_active=True,
        )
        user = User.objects.create_user(username="qa-renew-lock-student", tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
        student = create_student_fixture(
            tenant=tenant, user=user, name="Playback Student", ps_number="QAR001",
            omr_code="12345678", parent_phone="01000000000",
        )
        lecture = create_lecture_fixture(
            tenant=tenant, title="Playback Lecture", name="Playback Lecture", subject="MATH",
        )
        lesson = create_session_fixture(lecture=lecture, title="Playback Lesson", order=1)
        enrollment = create_enrollment_fixture(
            tenant=tenant, student=student, lecture=lecture, status="ACTIVE",
        )
        create_session_enrollment_fixture(tenant=tenant, session=lesson, enrollment=enrollment)
        video = Video.objects.create(
            tenant=tenant, session=lesson, title="Playback Video",
            status=Video.Status.READY, duration=600,
        )
        VideoAccess.objects.create(
            video=video, enrollment=enrollment, access_mode=AccessMode.PROCTORED_CLASS,
            is_override=True,
        )
        with self.settings(VIDEO_PLAYBACK_TTL_SECONDS=120):
            grant = issue_playback_access_grant(
                video=video, enrollment=enrollment, user=user,
                device_id="qa-renew-device", event_protocol_version=2,
            )
        self.assertIsNone(grant.error)
        self.assertIsNotNone(grant.token)
        self.assertIsNotNone(grant.session_id)
        self.assertIsNotNone(grant.expires_at)

        writer_ready = threading.Event()
        renew_connected = threading.Event()
        allow_writer_commit = threading.Event()
        pids: dict[str, int] = {}
        errors: list[Exception] = []
        responses = []

        def record_connection(raw_connection, role):
            with raw_connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '10s'")
                cursor.execute("SELECT pg_backend_pid()")
                pids[role] = cursor.fetchone()[0]

        def write_attendance():
            try:
                connection.ensure_connection()
                record_connection(connection.connection, "writer")
                with transaction.atomic():
                    locked = lock_enrollment_by_id_with_lecture(enrollment.id, tenant)
                    Attendance.objects.create(
                        tenant=tenant, session=lesson, enrollment=locked, status="ONLINE",
                    )
                    writer_ready.set()
                    if not allow_writer_commit.wait(timeout=12):
                        raise AssertionError("test did not release attendance transaction")
                    # The Session foreign key is checked when this transaction commits.
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        def track_renew_connection(execute, sql, params, many, context):
            if not renew_connected.is_set():
                record_connection(context["connection"].connection, "renew")
                renew_connected.set()
            return execute(sql, params, many, context)

        def renew():
            try:
                client = APIClient()
                client.force_authenticate(user)
                with connection.execute_wrapper(track_renew_connection):
                    responses.append(client.post(
                        "/api/v1/media/playback/renew/", {"token": grant.token}, format="json",
                        HTTP_HOST="localhost", HTTP_X_TENANT_CODE=tenant.code,
                    ))
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        writer = threading.Thread(target=write_attendance, name="playback-test-attendance")
        renewal = threading.Thread(target=renew, name="playback-test-renew")
        writer.start()
        try:
            self.assertTrue(writer_ready.wait(timeout=5), errors)
            renewal.start()
            self.assertTrue(renew_connected.wait(timeout=5), errors)
            deadline = time.monotonic() + 5
            waiting_on_writer = False
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT %s = ANY(pg_blocking_pids(%s))", [pids["writer"], pids["renew"]],
                    )
                    waiting_on_writer = cursor.fetchone()[0]
                if waiting_on_writer:
                    break
                time.sleep(0.01)
            self.assertTrue(waiting_on_writer, errors)
        finally:
            allow_writer_commit.set()
            writer.join(timeout=15)
            if renewal.ident is not None:
                renewal.join(timeout=15)

        self.assertFalse(writer.is_alive() or renewal.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(responses), 1)
        response = responses[0]
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["playback_session_id"], grant.session_id)
        self.assertGreater(response.data["playback_expires_at"], grant.expires_at)
        renewed_session = VideoPlaybackSession.objects.get(session_id=grant.session_id)
        self.assertEqual(renewed_session.status, VideoPlaybackSession.Status.ACTIVE)
        self.assertEqual(
            int(renewed_session.expires_at.timestamp()), response.data["playback_expires_at"],
        )
        token_ok, token_payload, token_error = verify_playback_token(response.data["playback_token"])
        self.assertTrue(token_ok, token_error)
        self.assertEqual(token_payload["exp"], response.data["playback_expires_at"])
        self.assertTrue(Attendance.objects.filter(
            tenant=tenant, session=lesson, enrollment=enrollment, status="ONLINE",
        ).exists())
