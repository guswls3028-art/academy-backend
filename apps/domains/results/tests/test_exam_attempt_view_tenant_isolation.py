from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.results.models import ExamAttempt
from apps.domains.results.views.exam_attempt_view import ExamAttemptViewSet
User = get_user_model()
Enrollment = django_apps.get_model("enrollment", "Enrollment")
Exam = django_apps.get_model("exams", "Exam")
ExamEnrollment = django_apps.get_model("exams", "ExamEnrollment")
Lecture = django_apps.get_model("lectures", "Lecture")
Session = django_apps.get_model("lectures", "Session")
ClinicLink = django_apps.get_model("progress", "ClinicLink")
Student = django_apps.get_model("students", "Student")
Submission = django_apps.get_model("submissions", "Submission")


class ExamAttemptViewTenantIsolationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.mine = self._make_tenant_data("attempt-mine")
        self.foreign = self._make_tenant_data("attempt-foreign")
        self.teacher = User.objects.create_user(
            username="attempt-teacher",
            password="test1234",
            tenant=self.mine["tenant"],
        )
        TenantMembership.objects.create(
            tenant=self.mine["tenant"],
            user=self.teacher,
            role="teacher",
        )
        self.submission = Submission.objects.create(
            tenant=self.mine["tenant"],
            user=self.teacher,
            target_type="exam",
            target_id=self.mine["exam"].id,
            enrollment_id=self.mine["enrollment"].id,
            source="online",
            status="done",
        )

    @staticmethod
    def _make_tenant_data(code):
        tenant = Tenant.objects.create(code=code, name=code, is_active=True)
        student_user = User.objects.create_user(
            username=f"{code}-student",
            password="test1234",
            tenant=tenant,
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=student_user,
            role="student",
        )
        student = Student.objects.create(
            tenant=tenant,
            user=student_user,
            ps_number=f"PS-{code}",
            omr_code=f"{tenant.id:08d}",
            name=code,
            parent_phone="01012345678",
        )
        lecture = Lecture.objects.create(
            tenant=tenant,
            title=code,
            name=code,
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="1차시")
        enrollment = Enrollment.objects.create(
            tenant=tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        exam = Exam.objects.create(
            tenant=tenant,
            title=code,
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
        )
        exam.sessions.add(session)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)
        clinic_link = ClinicLink.objects.create(
            tenant=tenant,
            enrollment=enrollment,
            session=session,
            reason=ClinicLink.Reason.TEACHER_RECOMMEND,
            source_type="exam",
            source_id=exam.id,
        )
        return {
            "tenant": tenant,
            "student_user": student_user,
            "enrollment": enrollment,
            "exam": exam,
            "clinic_link": clinic_link,
        }

    def _request(self, method, data, *, user=None):
        request = getattr(self.factory, method)(
            "/api/v1/results/exam-attempts/",
            data,
            format="json",
        )
        request.tenant = self.mine["tenant"]
        force_authenticate(request, user=user or self.teacher)
        return request

    def _valid_payload(self):
        return {
            "exam": self.mine["exam"].id,
            "enrollment": self.mine["enrollment"].id,
            "clinic_link": self.mine["clinic_link"].id,
            "submission_id": self.submission.id,
            "attempt_index": 99,
            "is_retake": True,
            "is_representative": False,
            "status": "done",
            "meta": {"client_owned": True},
        }

    def test_teacher_can_create_same_tenant_attempt(self):
        request = self._request("post", self._valid_payload())

        response = ExamAttemptViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 201, response.data)
        attempt = ExamAttempt.objects.get()
        self.assertEqual(attempt.exam_id, self.mine["exam"].id)
        self.assertEqual(attempt.enrollment_id, self.mine["enrollment"].id)
        self.assertEqual(attempt.submission_id, self.submission.id)
        self.assertEqual(attempt.clinic_link_id, self.mine["clinic_link"].id)
        self.assertEqual(attempt.attempt_index, 1)
        self.assertFalse(attempt.is_retake)
        self.assertTrue(attempt.is_representative)
        self.assertEqual(attempt.status, "pending")
        self.assertIsNone(attempt.meta)

    def test_cross_tenant_related_ids_are_rejected_on_create(self):
        foreign_fields = {
            "exam": self.foreign["exam"].id,
            "enrollment": self.foreign["enrollment"].id,
            "clinic_link": self.foreign["clinic_link"].id,
        }

        for field_name, foreign_id in foreign_fields.items():
            payload = self._valid_payload()
            payload[field_name] = foreign_id
            request = self._request("post", payload)
            with self.subTest(field=field_name):
                response = ExamAttemptViewSet.as_view({"post": "create"})(request)
                self.assertEqual(response.status_code, 400, response.data)

        self.assertFalse(ExamAttempt.objects.exists())

    def test_submission_must_match_tenant_exam_and_enrollment(self):
        foreign_submission = Submission.objects.create(
            tenant=self.foreign["tenant"],
            user=self.foreign["student_user"],
            target_type="exam",
            target_id=self.foreign["exam"].id,
            enrollment_id=self.foreign["enrollment"].id,
            source="online",
            status="done",
        )
        payload = self._valid_payload()
        payload["submission_id"] = foreign_submission.id
        request = self._request("post", payload)

        response = ExamAttemptViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ExamAttempt.objects.exists())

    def test_submission_id_is_required_and_must_be_positive(self):
        create_view = ExamAttemptViewSet.as_view({"post": "create"})
        for value in (None, 0, -1):
            payload = self._valid_payload()
            payload["submission_id"] = value
            with self.subTest(submission_id=value):
                response = create_view(self._request("post", payload))
                self.assertEqual(response.status_code, 400, response.data)

        payload = self._valid_payload()
        payload.pop("submission_id")
        response = create_view(self._request("post", payload))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ExamAttempt.objects.exists())

    def test_exam_must_be_linked_to_enrollment_lecture(self):
        other_lecture = Lecture.objects.create(
            tenant=self.mine["tenant"],
            title="same-tenant-other-lecture",
            name="same-tenant-other-lecture",
            subject="MATH",
        )
        other_user = User.objects.create_user(
            username="same-tenant-other-student",
            password="test1234",
            tenant=self.mine["tenant"],
        )
        other_student = Student.objects.create(
            tenant=self.mine["tenant"],
            user=other_user,
            ps_number="PS-SAME-TENANT-OTHER",
            omr_code="87654321",
            name="same-tenant-other-student",
            parent_phone="01012345678",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.mine["tenant"],
            student=other_student,
            lecture=other_lecture,
            status="ACTIVE",
        )
        ExamEnrollment.objects.create(
            exam=self.mine["exam"],
            enrollment=other_enrollment,
        )
        other_submission = Submission.objects.create(
            tenant=self.mine["tenant"],
            user=self.teacher,
            target_type="exam",
            target_id=self.mine["exam"].id,
            enrollment_id=other_enrollment.id,
            source="online",
            status="done",
        )
        payload = self._valid_payload()
        payload.update(
            enrollment=other_enrollment.id,
            submission_id=other_submission.id,
        )
        payload.pop("clinic_link")

        response = ExamAttemptViewSet.as_view({"post": "create"})(
            self._request("post", payload)
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ExamAttempt.objects.exists())

    def test_clinic_link_session_must_be_linked_to_exam(self):
        unlinked_session = Session.objects.create(
            lecture=self.mine["enrollment"].lecture,
            order=2,
            title="미연결 차시",
        )
        unlinked_clinic = ClinicLink.objects.create(
            tenant=self.mine["tenant"],
            enrollment=self.mine["enrollment"],
            session=unlinked_session,
            reason=ClinicLink.Reason.TEACHER_RECOMMEND,
            source_type="exam",
            source_id=self.mine["exam"].id,
        )
        payload = self._valid_payload()
        payload["clinic_link"] = unlinked_clinic.id

        response = ExamAttemptViewSet.as_view({"post": "create"})(
            self._request("post", payload)
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(ExamAttempt.objects.exists())

    def test_api_create_enforces_retake_policy(self):
        create_view = ExamAttemptViewSet.as_view({"post": "create"})
        first = create_view(self._request("post", self._valid_payload()))
        self.assertEqual(first.status_code, 201, first.data)
        second_submission = Submission.objects.create(
            tenant=self.mine["tenant"],
            user=self.teacher,
            target_type="exam",
            target_id=self.mine["exam"].id,
            enrollment_id=self.mine["enrollment"].id,
            source="online",
            status="done",
        )
        payload = self._valid_payload()
        payload["submission_id"] = second_submission.id

        second = create_view(self._request("post", payload))

        self.assertEqual(second.status_code, 400, second.data)
        self.assertEqual(ExamAttempt.objects.count(), 1)
        attempt = ExamAttempt.objects.get()
        self.assertEqual(attempt.attempt_index, 1)
        self.assertTrue(attempt.is_representative)

    def test_student_cannot_create_attempt(self):
        request = self._request(
            "post",
            self._valid_payload(),
            user=self.mine["student_user"],
        )

        response = ExamAttemptViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ExamAttempt.objects.exists())

    def test_list_hides_existing_attempts_with_cross_tenant_relations(self):
        visible = ExamAttempt.objects.create(
            exam=self.mine["exam"],
            enrollment=self.mine["enrollment"],
            submission_id=0,
            attempt_index=1,
            is_representative=True,
        )
        foreign_enrollment = ExamAttempt.objects.create(
            exam=self.mine["exam"],
            enrollment=self.foreign["enrollment"],
            submission_id=0,
            attempt_index=1,
            is_representative=True,
        )
        foreign_clinic_link = ExamAttempt.objects.create(
            exam=self.mine["exam"],
            enrollment=self.mine["enrollment"],
            clinic_link=self.foreign["clinic_link"],
            submission_id=0,
            attempt_index=2,
            is_representative=False,
        )
        unlinked_session = Session.objects.create(
            lecture=self.mine["enrollment"].lecture,
            order=2,
            title="미연결 차시",
        )
        unlinked_clinic = ClinicLink.objects.create(
            tenant=self.mine["tenant"],
            enrollment=self.mine["enrollment"],
            session=unlinked_session,
            reason=ClinicLink.Reason.TEACHER_RECOMMEND,
            source_type="exam",
            source_id=self.mine["exam"].id,
        )
        wrong_session = ExamAttempt.objects.create(
            exam=self.mine["exam"],
            enrollment=self.mine["enrollment"],
            clinic_link=unlinked_clinic,
            submission_id=0,
            attempt_index=3,
            is_representative=False,
        )
        other_user = User.objects.create_user(
            username="historical-other-student",
            password="test1234",
            tenant=self.mine["tenant"],
        )
        other_student = Student.objects.create(
            tenant=self.mine["tenant"],
            user=other_user,
            ps_number="PS-HISTORICAL-OTHER",
            omr_code="11223344",
            name="historical-other-student",
            parent_phone="01012345678",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.mine["tenant"],
            student=other_student,
            lecture=self.mine["enrollment"].lecture,
            status="ACTIVE",
        )
        other_clinic = ClinicLink.objects.create(
            tenant=self.mine["tenant"],
            enrollment=other_enrollment,
            session=self.mine["clinic_link"].session,
            reason=ClinicLink.Reason.TEACHER_RECOMMEND,
            source_type="exam",
            source_id=self.mine["exam"].id,
        )
        wrong_enrollment = ExamAttempt.objects.create(
            exam=self.mine["exam"],
            enrollment=self.mine["enrollment"],
            clinic_link=other_clinic,
            submission_id=0,
            attempt_index=4,
            is_representative=False,
        )
        foreign_submission = Submission.objects.create(
            tenant=self.foreign["tenant"],
            user=self.foreign["student_user"],
            target_type="exam",
            target_id=self.foreign["exam"].id,
            enrollment_id=self.foreign["enrollment"].id,
            source="online",
            status="done",
        )
        wrong_submission = ExamAttempt.objects.create(
            exam=self.mine["exam"],
            enrollment=self.mine["enrollment"],
            submission_id=foreign_submission.id,
            attempt_index=5,
            is_representative=False,
        )
        request = self._request("get", {})

        response = ExamAttemptViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data.get("results", response.data)
        returned_ids = {row["id"] for row in rows}
        self.assertEqual(returned_ids, {visible.id})
        self.assertNotIn(foreign_enrollment.id, returned_ids)
        self.assertNotIn(foreign_clinic_link.id, returned_ids)
        self.assertNotIn(wrong_session.id, returned_ids)
        self.assertNotIn(wrong_enrollment.id, returned_ids)
        self.assertNotIn(wrong_submission.id, returned_ids)

    def test_attempt_detail_is_append_only(self):
        attempt = ExamAttempt.objects.create(
            exam=self.mine["exam"],
            enrollment=self.mine["enrollment"],
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
        )
        view = ExamAttemptViewSet.as_view({"get": "retrieve"})

        for method in ("patch", "put", "delete"):
            request = self._request(method, {"status": "failed"})
            with self.subTest(method=method):
                response = view(request, pk=attempt.id)
                self.assertEqual(response.status_code, 405)

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "done")
