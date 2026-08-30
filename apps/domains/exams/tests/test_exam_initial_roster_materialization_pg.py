from __future__ import annotations

from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from apps.core.models import PlatformPushOutbox, Tenant, TenantMembership
from apps.domains.exams.models import (
    AnswerKey,
    Exam,
    ExamEnrollment,
    ExamQuestion,
    Sheet,
)


User = get_user_model()


class InitialExamRosterMaterializationPostgresTests(TransactionTestCase):
    def setUp(self):
        self.Lecture = django_apps.get_model("lectures", "Lecture")
        self.Session = django_apps.get_model("lectures", "Session")
        self.Student = django_apps.get_model("students", "Student")
        self.Enrollment = django_apps.get_model("enrollment", "Enrollment")
        self.SessionEnrollment = django_apps.get_model(
            "enrollment",
            "SessionEnrollment",
        )
        self.Result = django_apps.get_model("results", "Result")
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
            name="Initial roster tenant",
            code="initial-roster-tenant",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="Initial roster other tenant",
            code="initial-roster-other",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="initial_roster_admin",
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
        _, self.other_session = self._lecture_session(
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
        self.source_exam = self._source_exam()

    def _lecture_session(self, *, tenant, suffix):
        lecture = self.Lecture.objects.create(
            tenant=tenant,
            title=f"Initial roster lecture {suffix}",
            name=f"Initial roster lecture {suffix}",
            subject="MATH",
        )
        session = self.Session.objects.create(
            lecture=lecture,
            order=1,
            title=f"Initial roster session {suffix}",
        )
        return lecture, session

    def _enrollment(self, tenant, lecture, session, suffix):
        user = User.objects.create_user(
            username=f"initial_roster_student_{suffix}",
            password="test1234",
            tenant=tenant,
        )
        student = self.Student.objects.create(
            tenant=tenant,
            user=user,
            ps_number=f"IR{suffix}",
            omr_code=f"IR{suffix}",
            name=f"Initial roster student {suffix}",
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

    def _source_exam(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Initial roster source exam",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=60,
            max_score=100,
        )
        sheet = Sheet.objects.create(
            exam=exam,
            name="MAIN",
            total_questions=1,
            choice_count=1,
            essay_count=0,
        )
        question = ExamQuestion.objects.create(
            sheet=sheet,
            number=1,
            score=100,
        )
        AnswerKey.objects.create(exam=exam, answers={str(question.id): "1"})
        return exam

    def _unrelated_counts(self):
        return {
            "results": self.Result.objects.count(),
            "clinic_links": self.ClinicLink.objects.count(),
            "submissions": self.Submission.objects.count(),
            "omr_matches": self.OMRStudentMatch.objects.count(),
            "notification_logs": self.NotificationLog.objects.count(),
            "scheduled_notifications": self.ScheduledNotification.objects.count(),
            "platform_push_outbox": PlatformPushOutbox.objects.count(),
        }

    def test_source_import_materializes_initial_and_added_session_rosters_once(self):
        unrelated_before = self._unrelated_counts()

        create = self.client.post(
            "/api/v1/exams/",
            {
                "title": "Imported shared exam",
                "exam_type": Exam.ExamType.REGULAR,
                "session_id": self.session_a.id,
                "source_exam_id": self.source_exam.id,
                "pass_score": 60,
                "max_score": 100,
            },
            format="json",
            **self.headers,
        )

        self.assertEqual(create.status_code, 201, create.data)
        exam = Exam.objects.get(id=create.data["id"])
        self.assertEqual(
            set(
                ExamEnrollment.objects.filter(exam=exam).values_list(
                    "enrollment_id",
                    flat=True,
                )
            ),
            {enrollment.id for enrollment in self.enrollments_a},
        )

        assignment_url = f"/api/v1/exams/{exam.id}/lecture-assignments/"
        with patch(
            "apps.domains.exams.views.exam_lecture_assignment_view."
            "dispatch_progress_for_exam"
        ) as dispatch:
            added = self.client.post(
                assignment_url,
                {"session_id": self.session_b.id, "pass_score": 70},
                format="json",
                **self.headers,
            )
            repeated = self.client.post(
                assignment_url,
                {"session_id": self.session_b.id, "pass_score": 70},
                format="json",
                **self.headers,
            )

        self.assertEqual(added.status_code, 201, added.data)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(added.data["total_roster_count"], 4)
        self.assertEqual(repeated.data["total_roster_count"], 4)
        dispatch.assert_called()

        expected = {
            (enrollment.id, enrollment.lecture_id, self.tenant.id)
            for enrollment in [*self.enrollments_a, *self.enrollments_b]
        }
        actual = set(
            ExamEnrollment.objects.filter(exam=exam).values_list(
                "enrollment_id",
                "enrollment__lecture_id",
                "enrollment__tenant_id",
            )
        )
        self.assertEqual(actual, expected)
        self.assertEqual(exam.exam_enrollments.count(), 4)
        self.assertEqual(
            set(exam.sessions.values_list("id", "lecture_id")),
            {
                (self.session_a.id, self.lecture_a.id),
                (self.session_b.id, self.lecture_b.id),
            },
        )

        before_denied = actual
        denied = self.client.post(
            assignment_url,
            {"session_id": self.other_session.id, "pass_score": 80},
            format="json",
            **self.headers,
        )
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertFalse(exam.sessions.filter(id=self.other_session.id).exists())
        self.assertEqual(
            set(
                ExamEnrollment.objects.filter(exam=exam).values_list(
                    "enrollment_id",
                    "enrollment__lecture_id",
                    "enrollment__tenant_id",
                )
            ),
            before_denied,
        )

        wrong_tenant = self.client.get(
            assignment_url,
            HTTP_HOST="localhost",
            HTTP_X_TENANT_CODE=self.other_tenant.code,
        )
        self.assertEqual(wrong_tenant.status_code, 403, wrong_tenant.data)
        self.assertEqual(self._unrelated_counts(), unrelated_before)
