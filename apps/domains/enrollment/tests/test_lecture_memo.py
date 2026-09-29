from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.enrollment.serializers import EnrollmentSerializer
from apps.domains.enrollment.services.lifecycle import (
    assess_disposable_enrollment,
    deactivate_enrollments_for_student,
    delete_disposable_enrollment,
    restore_enrollments_after_student_restore,
)
from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
from apps.domains.parents.test_support import create_parent_account_fixture
from apps.domains.students.test_support import create_student_fixture


class LectureMemoTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Memo A", code="memo-a", is_active=True)
        self.foreign = Tenant.objects.create(name="Memo B", code="memo-b", is_active=True)
        self.admin = self.user("admin")
        self.staff = self.user("staff")
        self.student_user = self.user("student")
        parent = create_parent_account_fixture(
            tenant=self.tenant, parent_phone="01012345678", student_name="학생", initial_password="pw1234",
        ).parent
        self.parent_user = parent.user
        self.student = create_student_fixture(
            tenant=self.tenant, user=self.student_user, parent=parent,
            name="학생", ps_number="MEMO-1", omr_code="12345678",
            parent_phone=parent.phone, memo="기존 학생 공통 메모",
        )
        self.lecture = create_lecture_fixture(tenant=self.tenant, title="A", name="A", subject="MATH")
        self.other_lecture = create_lecture_fixture(tenant=self.tenant, title="B", name="B", subject="MATH")
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=self.student, lecture=self.lecture,
        )
        self.other_enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=self.student, lecture=self.other_lecture,
        )
        self.sessions = [create_session_fixture(lecture=self.lecture, order=i, title=f"S{i}") for i in (1, 2)]
        self.attendances = []
        self.client.force_authenticate(self.admin)
        for session in self.sessions:
            SessionEnrollment.objects.create(tenant=self.tenant, session=session, enrollment=self.enrollment)
            response = self.client.post(
                "/api/v1/lectures/attendance/bulk_create/", {"session": session.id, "students": [self.student.id]},
                format="json", **self.headers(),
            )
            self.assertEqual(response.status_code, 201, response.data)
            attendance_id = response.data[0]["id"]
            response = self.client.patch(
                f"/api/v1/lectures/attendance/{attendance_id}/", {"memo": "기존 차시 메모"},
                format="json", **self.headers(),
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.attendances.append(attendance_id)

    def user(self, role, tenant=None):
        tenant = tenant or self.tenant
        user = get_user_model().objects.create_user(
            username=f"{tenant.code}-{role}", password="pw1234", tenant=tenant,
            is_staff=role == "admin",
        )
        TenantMembership.ensure_active(tenant=tenant, user=user, role=role)
        return user

    def headers(self, **extra):
        return {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code, **extra}

    def memo_url(self):
        return f"/api/v1/enrollments/{self.enrollment.id}/lecture-memo/"

    def read_enrollment(self):
        response = self.client.get(f"/api/v1/enrollments/{self.enrollment.id}/", **self.headers())
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def write(self, memo, version=None):
        version = version if version is not None else self.read_enrollment()["lecture_memo_updated_at"]
        return self.client.patch(
            self.memo_url(), {"lecture_memo": memo}, format="json",
            **self.headers(HTTP_X_EXPECTED_UPDATED_AT=version),
        )

    @staticmethod
    def rows(data):
        return data if isinstance(data, list) else data["results"]

    def test_admin_staff_shared_sessions_and_other_lecture_isolation(self):
        for actor, text in ((self.admin, "이 강의는 전체 영상"), (self.staff, "  조교 전달\n영상 확인  ")):
            with self.subTest(role=actor.username):
                self.client.force_authenticate(actor)
                saved = self.write(text)
                self.assertEqual(saved.status_code, 200, saved.data)
                self.assertEqual(set(saved.data), {"id", "lecture_memo", "lecture_memo_updated_at"})
                self.assertEqual(self.read_enrollment()["lecture_memo"], text)
                for session in self.sessions:
                    for route in ("enrollments/session-enrollments", "lectures/attendance"):
                        response = self.client.get(f"/api/v1/{route}/?session={session.id}", **self.headers())
                        self.assertEqual(response.status_code, 200, response.data)
                        row = self.rows(response.data)[0]
                        self.assertEqual(row["lecture_memo"], text)
                        self.assertEqual(row["lecture_memo_updated_at"], saved.data["lecture_memo_updated_at"])
                        self.assertEqual(row["student_memo"], self.student.memo)
        self.other_enrollment.refresh_from_db()
        self.student.refresh_from_db()
        self.assertEqual(self.other_enrollment.lecture_memo, "")
        self.assertEqual(self.student.memo, "기존 학생 공통 메모")
        for attendance_id in self.attendances:
            response = self.client.get(f"/api/v1/lectures/attendance/{attendance_id}/", **self.headers())
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["memo"], "기존 차시 메모")
        self.assertEqual(self.write("").status_code, 200)
        self.assertEqual(self.read_enrollment()["lecture_memo"], "")

    def test_parent_student_denied_and_profile_does_not_disclose_lecture_memo(self):
        self.assertEqual(self.write("직원 전용 강의 전달").status_code, 200)
        for actor in (self.student_user, self.parent_user):
            with self.subTest(role=actor.username):
                self.client.force_authenticate(actor)
                for path in (self.memo_url(), "/api/v1/enrollments/", "/api/v1/enrollments/session-enrollments/", "/api/v1/lectures/attendance/"):
                    response = self.client.get(path, **self.headers())
                    self.assertEqual(response.status_code, 403, response.data)
                response = self.write("악의적 변경", "2026-01-01T00:00:00Z")
                self.assertEqual(response.status_code, 403, response.data)
                response = self.client.get(
                    "/api/v1/student/me/", **self.headers(HTTP_X_STUDENT_ID=str(self.student.id)),
                )
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["memo"], self.student.memo)
                self.assertNotIn("lecture_memo", response.data)
                self.assertNotIn("lecture_memo_updated_at", response.data)

    def test_foreign_tenant_and_inactive_membership_fail_closed(self):
        foreign_staff = self.user("staff", self.foreign)
        self.client.force_authenticate(foreign_staff)
        response = self.client.patch(
            self.memo_url(), {"lecture_memo": "foreign"}, format="json",
            HTTP_HOST="localhost", HTTP_X_TENANT_CODE=self.foreign.code,
            HTTP_X_EXPECTED_UPDATED_AT=self.enrollment.updated_at.isoformat(),
        )
        self.assertEqual(response.status_code, 404, response.data)
        response = self.write("foreign", self.enrollment.updated_at.isoformat())
        self.assertEqual(response.status_code, 403, response.data)
        TenantMembership.objects.filter(user=self.staff, tenant=self.tenant).update(is_active=False)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.write("inactive", self.enrollment.updated_at.isoformat()).status_code, 403)

    def test_required_version_validation_and_stale_save_preserves_winner(self):
        original = self.read_enrollment()["lecture_memo_updated_at"]
        for header in ({}, {"HTTP_X_EXPECTED_UPDATED_AT": "bad-date"}):
            response = self.client.patch(self.memo_url(), {"lecture_memo": "bad"}, format="json", **self.headers(**header))
            self.assertEqual(response.status_code, 400, response.data)
        for body in ({}, {"lecture_memo": None}, {"lecture_memo": 12}, {"lecture_memo": "x" * 2001}, {"lecture_memo": "ok", "status": "INACTIVE"}):
            response = self.client.patch(self.memo_url(), body, format="json", **self.headers(HTTP_X_EXPECTED_UPDATED_AT=original))
            self.assertEqual(response.status_code, 400, response.data)
        winner = self.write("먼저 저장한 메모", original)
        self.assertEqual(winner.status_code, 200)
        loser = self.write("오래된 편집창", original)
        self.assertEqual(loser.status_code, 409, loser.data)
        self.assertEqual(loser.data["code"], "stale_resource")
        self.assertEqual(loser.data["current_updated_at"], winner.data["lecture_memo_updated_at"])
        self.assertEqual(self.read_enrollment()["lecture_memo"], "먼저 저장한 메모")

    def test_stale_generic_status_save_cannot_overwrite_memo(self):
        stale_instance = Enrollment.objects.get(pk=self.enrollment.id)
        self.assertEqual(self.write("최신 전달사항").status_code, 200)
        serializer = EnrollmentSerializer(stale_instance, data={"status": "INACTIVE", "lecture_memo": "bypass"}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.status, "INACTIVE")
        self.assertEqual(self.enrollment.lecture_memo, "최신 전달사항")

    def test_invalid_memo_payload_preserves_existing_text_and_scope(self):
        self.assertEqual(self.write("보존할 메모").status_code, 200)
        version = self.read_enrollment()["lecture_memo_updated_at"]
        for payload in (
            {}, {"lecture_memo": None}, {"lecture_memo": 123},
            {"lecture_memo": "가" * 2001},
            {"lecture_memo": "이동 금지", "lecture": self.other_lecture.id},
        ):
            with self.subTest(payload_type=list(payload)):
                response = self.client.patch(
                    self.memo_url(), payload, format="json",
                    **self.headers(HTTP_X_EXPECTED_UPDATED_AT=version),
                )
                self.assertEqual(response.status_code, 400, response.data)
                current = self.read_enrollment()
                self.assertEqual(current["lecture_memo"], "보존할 메모")
                self.assertEqual(current["lecture_memo_updated_at"], version)
        self.assertEqual(self.write("가" * 2000).status_code, 200)

    def test_memo_save_has_no_enrollment_fee_or_notification_side_effects(self):
        with patch("apps.domains.enrollment.views.sync_enrollment_status_side_effects") as sync:
            self.assertEqual(self.write("전달").status_code, 200)
        sync.assert_not_called()

    def test_reactivation_and_student_restore_preserve_memo(self):
        self.assertEqual(self.write("다시 등록해도 보존").status_code, 200)
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status"])
        with patch("apps.domains.enrollment.services.lifecycle.auto_assign_fees_on_enrollment"), patch("apps.domains.enrollment.services.lifecycle.schedule_pending_account_notice"):
            response = self.client.post("/api/v1/enrollments/bulk_create/", {"lecture": self.lecture.id, "students": [self.student.id]}, format="json", **self.headers())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data[0]["id"], self.enrollment.id)
        self.assertEqual(response.data[0]["lecture_memo"], "다시 등록해도 보존")
        with patch("apps.domains.enrollment.services.lifecycle.deactivate_fees_for_enrollment"), patch("apps.domains.enrollment.services.lifecycle.sync_enrollment_status_side_effects"):
            deactivate_enrollments_for_student(tenant=self.tenant, student=self.student)
            restore_enrollments_after_student_restore(tenant=self.tenant, student=self.student)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.lecture_memo, "다시 등록해도 보존")

    def test_authored_memo_blocks_disposable_cleanup(self):
        self.other_enrollment.lecture_memo = "사용자 작성"
        self.other_enrollment.save(update_fields=["lecture_memo", "updated_at"])
        impact = assess_disposable_enrollment(tenant=self.tenant, enrollment=self.other_enrollment)
        self.assertFalse(impact.can_remove)
        self.assertEqual(impact.protected_dependencies["enrollment.lecture_memo"], 1)
        with self.assertRaises(ValidationError):
            delete_disposable_enrollment(tenant=self.tenant, enrollment_id=self.other_enrollment.id, student_id=self.student.id)
        self.assertTrue(Enrollment.objects.filter(pk=self.other_enrollment.id).exists())

    def test_database_default_supports_old_runtime_insert(self):
        # Omit the new column exactly as the previous application model does.
        self.other_enrollment.delete()
        table = connection.ops.quote_name(Enrollment._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} (tenant_id,student_id,lecture_id,status,enrolled_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                [self.tenant.id, self.student.id, self.other_lecture.id, "ACTIVE", *[self.enrollment.updated_at] * 3],
            )
        self.assertEqual(Enrollment.objects.get(lecture=self.other_lecture, student=self.student).lecture_memo, "")
