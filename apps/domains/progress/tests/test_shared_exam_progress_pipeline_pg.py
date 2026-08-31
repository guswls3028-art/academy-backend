from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from apps.core.models import PlatformPushOutbox, Tenant, TenantMembership
from apps.domains.progress.services.progress_pipeline import ProgressPipelineService


User = get_user_model()


class SharedExamProgressPipelinePostgresTests(TransactionTestCase):
    def setUp(self):
        self.Lecture = django_apps.get_model("lectures", "Lecture")
        self.Session = django_apps.get_model("lectures", "Session")
        self.Student = django_apps.get_model("students", "Student")
        self.Enrollment = django_apps.get_model("enrollment", "Enrollment")
        self.SessionEnrollment = django_apps.get_model(
            "enrollment",
            "SessionEnrollment",
        )
        self.Exam = django_apps.get_model("exams", "Exam")
        self.ExamEnrollment = django_apps.get_model("exams", "ExamEnrollment")
        self.Result = django_apps.get_model("results", "Result")
        self.ProgressPolicy = django_apps.get_model("progress", "ProgressPolicy")
        self.SessionProgress = django_apps.get_model("progress", "SessionProgress")
        self.LectureProgress = django_apps.get_model("progress", "LectureProgress")
        self.ClinicLink = django_apps.get_model("progress", "ClinicLink")
        self.Submission = django_apps.get_model("submissions", "Submission")
        self.OMRStudentMatch = django_apps.get_model(
            "submissions",
            "OMRStudentMatch",
        )
        self.NotificationLog = django_apps.get_model("messaging", "NotificationLog")
        self.ScheduledNotification = django_apps.get_model(
            "messaging",
            "ScheduledNotification",
        )

        self.tenant = Tenant.objects.create(
            name="Shared progress tenant",
            code="shared-progress-tenant",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="Shared progress other tenant",
            code="shared-progress-other",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="shared_progress_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            is_superuser=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="admin",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.headers = {
            "HTTP_HOST": "localhost",
            "HTTP_X_TENANT_CODE": self.tenant.code,
        }

        self.lecture_a, self.session_a = self._lecture_session(
            tenant=self.tenant,
            suffix="A",
        )
        self.lecture_b, self.session_b = self._lecture_session(
            tenant=self.tenant,
            suffix="B",
        )
        self.other_lecture, self.other_session = self._lecture_session(
            tenant=self.other_tenant,
            suffix="OTHER",
        )
        self.enrollments_a = [
            self._enrollment(self.tenant, self.lecture_a, self.session_a, f"A{index}")
            for index in range(1, 3)
        ]
        self.enrollments_b = [
            self._enrollment(self.tenant, self.lecture_b, self.session_b, f"B{index}")
            for index in range(1, 3)
        ]
        self.other_enrollment = self._enrollment(
            self.other_tenant,
            self.other_lecture,
            self.other_session,
            "OTHER",
        )

        self.exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="Shared progress exam",
            subject="MATH",
            exam_type="regular",
            pass_score=60,
            max_score=100,
        )
        self.exam.sessions.add(self.session_a, self.session_b)
        self.ExamEnrollment.objects.bulk_create(
            [
                self.ExamEnrollment(exam=self.exam, enrollment=enrollment)
                for enrollment in [*self.enrollments_a, *self.enrollments_b]
            ]
        )
        for enrollment in [*self.enrollments_a, *self.enrollments_b]:
            self.Result.objects.create(
                target_type="exam",
                target_id=self.exam.id,
                enrollment=enrollment,
                total_score=100,
                max_score=100,
                objective_score=100,
            )
        self.Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.other_enrollment,
            total_score=100,
            max_score=100,
            objective_score=100,
        )
        for lecture in (self.lecture_a, self.lecture_b):
            self.ProgressPolicy.objects.create(
                lecture=lecture,
                video_required_rate=0,
                exam_start_session_order=1,
                exam_end_session_order=9999,
                exam_pass_score=60,
                exam_aggregate_strategy="MAX",
                exam_pass_source="EXAM",
                homework_start_session_order=9999,
                homework_end_session_order=9999,
                homework_pass_type="TEACHER_APPROVAL",
            )

    def _lecture_session(self, *, tenant, suffix):
        lecture = self.Lecture.objects.create(
            tenant=tenant,
            title=f"Shared progress lecture {suffix}",
            name=f"Shared progress lecture {suffix}",
            subject="MATH",
        )
        session = self.Session.objects.create(
            lecture=lecture,
            order=1,
            title=f"Shared progress session {suffix}",
        )
        return lecture, session

    def _enrollment(self, tenant, lecture, session, suffix):
        user = User.objects.create_user(
            username=f"shared_progress_student_{suffix}",
            password="test1234",
            tenant=tenant,
        )
        student = self.Student.objects.create(
            tenant=tenant,
            user=user,
            ps_number=f"SP{suffix}",
            omr_code=f"SP{suffix}",
            name=f"Shared progress student {suffix}",
            parent_phone="01000000000",
        )
        enrollment = self.Enrollment.objects.create(
            tenant=tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        self.SessionEnrollment.objects.create(
            tenant=tenant,
            session=session,
            enrollment=enrollment,
        )
        return enrollment

    def _protected_snapshot(self):
        return {
            "results": list(
                self.Result.objects.order_by("id").values_list(
                    "id",
                    "target_id",
                    "enrollment_id",
                    "total_score",
                    "max_score",
                    "objective_score",
                )
            ),
            "submissions": self.Submission.objects.count(),
            "omr_matches": self.OMRStudentMatch.objects.count(),
            "clinic_links": self.ClinicLink.objects.count(),
            "notification_logs": self.NotificationLog.objects.count(),
            "scheduled_notifications": self.ScheduledNotification.objects.count(),
            "platform_push_outbox": PlatformPushOutbox.objects.count(),
        }

    def _assert_progress_ownership(self):
        expected = {
            (enrollment.id, self.session_a.id, self.lecture_a.id)
            for enrollment in self.enrollments_a
        } | {
            (enrollment.id, self.session_b.id, self.lecture_b.id)
            for enrollment in self.enrollments_b
        }
        actual_sessions = set(
            self.SessionProgress.objects.values_list(
                "enrollment_id",
                "session_id",
                "session__lecture_id",
            )
        )
        self.assertEqual(actual_sessions, expected)
        self.assertEqual(
            set(
                self.LectureProgress.objects.values_list(
                    "enrollment_id",
                    "lecture_id",
                )
            ),
            {(enrollment_id, lecture_id) for enrollment_id, _, lecture_id in expected},
        )
        self.assertFalse(
            self.SessionProgress.objects.filter(
                enrollment__tenant=self.other_tenant,
            ).exists()
        )
        self.assertFalse(
            self.LectureProgress.objects.filter(
                enrollment__tenant=self.other_tenant,
            ).exists()
        )
        for progress in self.SessionProgress.objects.all():
            self.assertTrue(progress.exam_attempted)
            self.assertEqual(float(progress.exam_aggregate_score), 100.0)
            self.assertTrue(progress.exam_passed)
            self.assertTrue(progress.completed)

    def test_existing_assignment_recomputes_only_each_result_enrollments_own_lecture(self):
        protected_before = self._protected_snapshot()
        url = f"/api/v1/exams/{self.exam.id}/lecture-assignments/"

        first = self.client.post(
            url,
            {"session_id": self.session_b.id, "pass_score": 70},
            format="json",
            **self.headers,
        )

        first_body = getattr(first, "data", None) or first.json()
        self.assertEqual(first.status_code, 200, first_body)
        self.assertEqual(first_body["total_roster_count"], 4)
        self.assertEqual(self.ExamEnrollment.objects.filter(exam=self.exam).count(), 4)
        self._assert_progress_ownership()
        session_progress_ids = set(
            self.SessionProgress.objects.values_list("id", flat=True)
        )
        lecture_progress_ids = set(
            self.LectureProgress.objects.values_list("id", flat=True)
        )

        repeated = self.client.post(
            url,
            {"session_id": self.session_b.id, "pass_score": 70},
            format="json",
            **self.headers,
        )

        repeated_body = getattr(repeated, "data", None) or repeated.json()
        self.assertEqual(repeated.status_code, 200, repeated_body)
        self.assertEqual(repeated_body["total_roster_count"], 4)
        self._assert_progress_ownership()
        self.assertEqual(
            set(self.SessionProgress.objects.values_list("id", flat=True)),
            session_progress_ids,
        )
        self.assertEqual(
            set(self.LectureProgress.objects.values_list("id", flat=True)),
            lecture_progress_ids,
        )
        self.assertEqual(self._protected_snapshot(), protected_before)

    def test_submission_recompute_uses_only_the_enrollments_own_linked_lecture(self):
        enrollment = self.enrollments_a[0]
        submission = self.Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment=enrollment,
            target_type="exam",
            target_id=self.exam.id,
            source=self.Submission.Source.ONLINE,
            status=self.Submission.Status.ANSWERS_READY,
        )
        protected_before = self._protected_snapshot()

        ProgressPipelineService().apply(submission_id=submission.id)

        self.assertEqual(
            set(
                self.SessionProgress.objects.values_list(
                    "enrollment_id",
                    "session_id",
                    "session__lecture_id",
                )
            ),
            {(enrollment.id, self.session_a.id, self.lecture_a.id)},
        )
        self.assertEqual(
            set(
                self.LectureProgress.objects.values_list(
                    "enrollment_id",
                    "lecture_id",
                )
            ),
            {(enrollment.id, self.lecture_a.id)},
        )
        self.assertEqual(self._protected_snapshot(), protected_before)
