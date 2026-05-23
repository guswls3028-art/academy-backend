from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.homework_results.views.homework_view import HomeworkViewSet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


User = get_user_model()


def _rows(response):
    data = response.data
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    return data


class HomeworkDestroyPolicyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Homework Destroy",
            code="hwdestroy",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="hwdestroy-admin",
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
        self.student_user = User.objects.create_user(
            username="hwdestroy-student",
            password="test1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            name="Student",
            ps_number="HW-DST-1",
            omr_code="HWDST1",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=self.student,
            status="ACTIVE",
        )
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="삭제 정책 과제",
        )
        self.assignment = HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        self.score = HomeworkScore.objects.create(
            homework=self.homework,
            session=self.session,
            enrollment=self.enrollment,
            attempt_index=1,
            score=80,
            max_score=100,
        )
        self.submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=self.homework.id,
            source=Submission.Source.HOMEWORK_IMAGE,
            status=Submission.Status.SUBMITTED,
        )

    def _delete_homework(self):
        request = self.factory.delete(f"/homeworks/{self.homework.id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return HomeworkViewSet.as_view({"delete": "destroy"})(request, pk=self.homework.id)

    def test_destroy_removes_live_assignment_but_preserves_history(self):
        response = self._delete_homework()

        self.assertEqual(response.status_code, 204)
        self.assertTrue(Homework.objects.filter(id=self.homework.id).exists())
        self.assertFalse(HomeworkAssignment.objects.filter(id=self.assignment.id).exists())
        self.assertTrue(HomeworkScore.objects.filter(id=self.score.id).exists())
        self.assertTrue(Submission.objects.filter(id=self.submission.id).exists())

        self.homework.refresh_from_db()
        self.assertEqual(self.homework.status, Homework.Status.CLOSED)
        self.assertIsInstance(self.homework.meta, dict)
        self.assertIn("removed_from_session_at", self.homework.meta)

        request = self.factory.get(f"/homeworks/?session_id={self.session.id}")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = HomeworkViewSet.as_view({"get": "list"})(request)
        ids = [row["id"] for row in _rows(response)]
        self.assertNotIn(self.homework.id, ids)

    def test_destroyed_homework_can_be_queried_explicitly_for_audit(self):
        self._delete_homework()

        request = self.factory.get(
            f"/homeworks/?session_id={self.session.id}&include_removed=true"
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = HomeworkViewSet.as_view({"get": "list"})(request)

        ids = [row["id"] for row in _rows(response)]
        self.assertIn(self.homework.id, ids)

    def test_destroy_homework_without_assignments_still_soft_removes(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="대상자 없는 과제",
        )

        request = self.factory.delete(f"/homeworks/{homework.id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = HomeworkViewSet.as_view({"delete": "destroy"})(request, pk=homework.id)

        self.assertEqual(response.status_code, 204)
        homework.refresh_from_db()
        self.assertEqual(homework.status, Homework.Status.CLOSED)
        self.assertEqual(homework.meta["removed_assignment_count"], 0)
