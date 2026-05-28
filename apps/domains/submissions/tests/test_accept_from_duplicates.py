"""
같은 (시험, 학생) 중복 OMR 후보를 한 번에 채택+나머지 폐기하는 endpoint 테스트.

기대 흐름:
- 검토 화면이 본 sub의 manual_edit GET을 요청하면 응답에 duplicate_siblings 가 포함된다.
- accept-from-duplicates POST 한 번으로 본 sub는 DONE+grade까지 진행되고,
  같은 (exam, enrollment) 다른 active sub는 'discarded:duplicate'로 폐기된다.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import TenantMembership
from apps.core.models.tenant import Tenant
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import (
    AnswerKey,
    Exam,
    ExamEnrollment,
    ExamQuestion,
    Sheet,
)
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import Result
from apps.domains.students.services.creation import create_student_account
from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.views.submission_view import SubmissionViewSet


User = get_user_model()


class AcceptFromDuplicatesTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Dup", code="dup_tenant", is_active=True
        )
        self.staff = User.objects.create_user(
            username="dup_staff",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant, user=self.staff, role="teacher"
        )

        sr = create_student_account(
            tenant=self.tenant,
            student_data={
                "ps_number": "DUP001",
                "name": "중복 후보 학생",
                "phone": "01012345678",
                "parent_phone": "",
                "omr_code": "12345678",
                "school_type": "HIGH",
            },
            password="test1234",
        )
        self.student = sr.student

        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="L", name="L", subject="MATH"
        )
        self.session = Session.objects.create(
            lecture=self.lecture, order=1, title="S"
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )

        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="중복 시험",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
            max_attempts=1,
        )
        self.exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

        self.sheet = Sheet.objects.create(
            exam=self.exam, name="MAIN", total_questions=2
        )
        self.q1 = ExamQuestion.objects.create(sheet=self.sheet, number=1, score=50)
        self.q2 = ExamQuestion.objects.create(sheet=self.sheet, number=2, score=50)
        AnswerKey.objects.create(
            exam=self.exam,
            answers={str(self.q1.id): "1", str(self.q2.id): "2"},
        )

        # 두 sub: 둘 다 같은 학생/시험. kept는 검토 필요, other는 DONE.
        self.kept = Submission.objects.create(
            tenant=self.tenant,
            user=self.staff,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.NEEDS_IDENTIFICATION,
            file_key=f"tenants/{self.tenant.id}/omr/kept.jpg",
            meta={
                "manual_review": {"required": True, "reasons": ["DUPLICATE_ENROLLMENT"]},
                "identifier_status": "matched_duplicate",
            },
        )
        SubmissionAnswer.objects.create(
            submission=self.kept,
            tenant=self.tenant,
            exam_question_id=self.q1.id,
            answer="1",
        )
        SubmissionAnswer.objects.create(
            submission=self.kept,
            tenant=self.tenant,
            exam_question_id=self.q2.id,
            answer="3",  # 오답
        )

        self.other = Submission.objects.create(
            tenant=self.tenant,
            user=self.staff,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.DONE,
            file_key=f"tenants/{self.tenant.id}/omr/other.jpg",
        )

    def _call(self, *, method: str, action: str, sub_id: int, user=None):
        path = f"/submissions/submissions/{sub_id}/{action.replace('_', '-')}/"
        request_factory_method = getattr(self.factory, method)
        request = request_factory_method(path)
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.staff)
        action_map = {"get": "manual_edit"} if action == "manual_edit" else {method: action}
        if action == "manual_edit":
            view = SubmissionViewSet.as_view({"get": "manual_edit"})
        else:
            view = SubmissionViewSet.as_view({method: action})
        return view(request, pk=sub_id)

    def test_manual_edit_get_exposes_duplicate_siblings(self):
        response = self._call(method="get", action="manual_edit", sub_id=self.kept.id)
        self.assertEqual(response.status_code, 200, response.data)
        siblings = response.data.get("duplicate_siblings") or []
        self.assertEqual(len(siblings), 1)
        self.assertEqual(siblings[0]["submission_id"], self.other.id)
        self.assertEqual(siblings[0]["status"], Submission.Status.DONE)

    def test_accept_from_duplicates_promotes_kept_and_discards_other(self):
        response = self._call(
            method="post",
            action="accept_from_duplicates",
            sub_id=self.kept.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.kept.refresh_from_db()
        self.other.refresh_from_db()

        self.assertEqual(self.kept.status, Submission.Status.DONE)
        self.assertFalse((self.kept.meta or {}).get("manual_review", {}).get("required"))
        self.assertEqual(
            (self.kept.meta or {}).get("identifier_status"),
            "matched",
        )
        self.assertEqual(
            (self.kept.meta or {}).get("accepted_from_duplicates", {}).get("superseded_sibling_count"),
            1,
        )

        # 형제가 DONE이었으므로 SUPERSEDED로 전환 (재시험과 같은 도메인 의미)
        self.assertEqual(self.other.status, Submission.Status.SUPERSEDED)
        self.assertEqual(
            (self.other.meta or {}).get("discarded", {}).get("kept_sibling_id"),
            self.kept.id,
        )
        self.assertEqual(
            (self.other.meta or {}).get("discarded", {}).get("reason"),
            "superseded_by_duplicate_selection",
        )

        self.assertEqual(response.data["superseded_count"], 1)
        self.assertEqual(response.data["discarded_count"], 0)
        self.assertEqual(response.data["status"], Submission.Status.DONE)
        self.assertEqual(response.data["score"], 50.0)
        self.assertEqual(response.data["max_score"], 100.0)

        result = Result.objects.filter(
            target_type="exam",
            target_id=self.exam.id,
            enrollment_id=self.enrollment.id,
        ).order_by("-id").first()
        self.assertIsNotNone(result)
        self.assertEqual(float(result.total_score or 0), 50.0)

    def test_rejects_when_enrollment_missing(self):
        unmatched = Submission.objects.create(
            tenant=self.tenant,
            user=self.staff,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            enrollment_id=None,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.NEEDS_IDENTIFICATION,
            file_key="x.jpg",
        )
        response = self._call(
            method="post",
            action="accept_from_duplicates",
            sub_id=unmatched.id,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("학생 식별", response.data.get("detail", ""))

    def test_tenant_isolation(self):
        other_tenant = Tenant.objects.create(
            name="Other", code="other_dup", is_active=True
        )
        other_staff = User.objects.create_user(
            username="other_dup_staff",
            password="test1234",
            tenant=other_tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(
            tenant=other_tenant, user=other_staff, role="teacher"
        )
        path = f"/submissions/submissions/{self.kept.id}/accept-from-duplicates/"
        request = self.factory.post(path)
        request.tenant = other_tenant
        force_authenticate(request, user=other_staff)
        view = SubmissionViewSet.as_view({"post": "accept_from_duplicates"})
        response = view(request, pk=self.kept.id)
        self.assertIn(response.status_code, (403, 404))
