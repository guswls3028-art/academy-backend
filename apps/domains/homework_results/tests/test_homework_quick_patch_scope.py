from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.homework_results.views.homework_score_viewset import HomeworkScoreViewSet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student


User = get_user_model()


class HomeworkQuickPatchScopeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Homework Scope",
            code="hwscope",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="hwscope-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture",
            name="Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1회차")
        self.other_session = Session.objects.create(lecture=self.lecture, order=2, title="2회차")
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Daily homework",
        )
        self.assigned_enrollment = self._create_enrollment("assigned")
        self.unassigned_enrollment = self._create_enrollment("unassigned")
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
        )

    def _create_enrollment(self, suffix: str) -> Enrollment:
        user = User.objects.create_user(
            username=f"hwscope-{suffix}",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            name=f"Student {suffix}",
            ps_number=f"HW-{suffix}",
            omr_code=f"HW{suffix.upper()}"[:8],
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=student,
            status="ACTIVE",
        )

    def _quick_patch(self, data, *, expected_status=200):
        request = self.factory.patch("/homework/scores/quick/", data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = HomeworkScoreViewSet.as_view({"patch": "quick_patch"})(request)
        self.assertEqual(response.status_code, expected_status, response.data)
        return response

    def test_rejects_unassigned_enrollment_without_side_effects(self):
        self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.unassigned_enrollment.id,
                "score": 80,
            },
            expected_status=400,
        )

        self.assertFalse(
            HomeworkScore.objects.filter(
                homework=self.homework,
                enrollment=self.unassigned_enrollment,
            ).exists()
        )

    def test_rejects_session_mismatch_without_side_effects(self):
        self._quick_patch(
            {
                "session_id": self.other_session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 80,
            },
            expected_status=400,
        )

        self.assertFalse(
            HomeworkScore.objects.filter(
                homework=self.homework,
                enrollment=self.assigned_enrollment,
            ).exists()
        )

    def test_accepts_assigned_enrollment_for_homework_session(self):
        response = self._quick_patch(
            {
                "session_id": self.session.id,
                "homework_id": self.homework.id,
                "enrollment_id": self.assigned_enrollment.id,
                "score": 80,
            }
        )

        score = HomeworkScore.objects.get(
            homework=self.homework,
            session=self.session,
            enrollment=self.assigned_enrollment,
            attempt_index=1,
        )
        self.assertEqual(response.data["id"], score.id)
        self.assertEqual(score.score, 80)

