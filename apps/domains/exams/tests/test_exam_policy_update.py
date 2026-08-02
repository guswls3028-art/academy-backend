from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import Exam, Sheet
from apps.domains.exams.serializers.exam import ExamSerializer
from apps.domains.exams.serializers.exam_update import ExamUpdateSerializer
from apps.domains.exams.views.exam_view import ExamViewSet


class ExamPolicyUpdateTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="시험 정책 동시 수정",
            code="exam-policy-update",
            is_active=True,
        )
        self.user = get_user_model().objects.create_user(
            username="exam-policy-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.user,
            role="admin",
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="중간 점검",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
            pass_score=80,
        )

    def patch(self, data, *, expected_updated_at: str | None = None):
        headers = {}
        if expected_updated_at is not None:
            headers["HTTP_X_EXPECTED_UPDATED_AT"] = expected_updated_at
        request = self.factory.patch(
            f"/api/v1/exams/{self.exam.id}/",
            data,
            format="json",
            **headers,
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        view = ExamViewSet.as_view({"patch": "partial_update"})
        return view(request, pk=self.exam.id)

    def test_patch_returns_complete_exam_and_accepts_current_version(self):
        expected_updated_at = ExamSerializer(self.exam).data["updated_at"]

        response = self.patch(
            {"pass_score": 75},
            expected_updated_at=expected_updated_at,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], self.exam.id)
        self.assertEqual(response.data["title"], "중간 점검")
        self.assertEqual(response.data["pass_score"], 75)
        self.assertTrue(response.data["updated_at"])

    def test_patch_rejects_stale_version_without_overwriting_newer_value(self):
        stale_updated_at = ExamSerializer(self.exam).data["updated_at"]
        newer_updated_at = timezone.now() + timedelta(seconds=1)
        Exam.objects.filter(pk=self.exam.id).update(
            pass_score=70,
            updated_at=newer_updated_at,
        )

        response = self.patch(
            {"pass_score": 60},
            expected_updated_at=stale_updated_at,
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["code"], "stale_resource")
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.pass_score, 70)

    def test_mixed_mode_can_repair_zero_boundary_from_existing_sheet(self):
        Sheet.objects.create(
            exam=self.exam,
            total_questions=2,
            choice_count=1,
            essay_count=1,
        )
        serializer = ExamUpdateSerializer(
            self.exam,
            data={
                "grading_mode": Exam.GradingMode.MIXED,
                "choice_question_count": 1,
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.grading_mode, Exam.GradingMode.MIXED)
        self.assertEqual(self.exam.choice_question_count, 1)
