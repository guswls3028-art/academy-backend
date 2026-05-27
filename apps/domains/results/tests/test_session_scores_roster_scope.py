from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, ExamQuestion, Sheet
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Lecture, Session
from apps.domains.progress.models import ClinicLink, SessionProgress
from apps.domains.progress.services.clinic_remediation_service import ClinicRemediationService
from apps.domains.results.services.clinic_target_service import ClinicTargetService
from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.views.session_scores_view import SessionScoresView
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission
from apps.domains.submissions.views.submission_view import SubmissionViewSet


User = get_user_model()


class SessionScoresRosterScopeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Tenant", code="scorecope", is_active=True)
        self.admin = User.objects.create_user(
            username="score_scope_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture",
            name="Lecture",
            subject="SCIENCE",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1주차")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="주간 테스트",
            pass_score=60,
            max_score=100,
        )
        self.exam.sessions.add(self.session)
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="주간 과제",
        )

        self.active_enrollment = self._create_enrollment("ACTIVE001", "현재 학생")
        self.stale_enrollment = self._create_enrollment("STALE001", "출결 제외 학생")

        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.active_enrollment,
        )
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.stale_enrollment,
        )
        Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.active_enrollment,
            status="PRESENT",
        )

        for enrollment in (self.active_enrollment, self.stale_enrollment):
            ExamEnrollment.objects.create(exam=self.exam, enrollment=enrollment)
            HomeworkAssignment.objects.create(
                tenant=self.tenant,
                homework=self.homework,
                session=self.session,
                enrollment=enrollment,
            )

    def _create_enrollment(self, ps_number: str, name: str) -> Enrollment:
        user = User.objects.create_user(
            username=f"score_scope_{ps_number}",
            password="test1234",
            tenant=self.tenant,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number=ps_number,
            omr_code=ps_number[-8:],
            name=name,
            parent_phone="01000000000",
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
            status="ACTIVE",
        )

    def test_session_scores_excludes_assignment_without_attendance_row(self):
        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data["rows"]
        self.assertEqual(response.data["meta"]["exams"][0]["exam_id"], self.exam.id)
        self.assertEqual(response.data["meta"]["homeworks"][0]["homework_id"], self.homework.id)
        self.assertEqual([row["enrollment_id"] for row in rows], [self.active_enrollment.id])
        self.assertEqual(rows[0]["student_name"], "현재 학생")
        self.assertEqual(len(rows[0]["exams"]), 1)
        self.assertEqual(len(rows[0]["homeworks"]), 1)

    def test_session_scores_treats_session_student_as_omr_exam_target(self):
        ExamEnrollment.objects.filter(exam=self.exam, enrollment=self.active_enrollment).delete()

        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data["rows"]
        self.assertEqual([row["enrollment_id"] for row in rows], [self.active_enrollment.id])
        self.assertEqual(len(rows[0]["exams"]), 1)
        self.assertEqual(rows[0]["exams"][0]["exam_id"], self.exam.id)
        self.assertIsNone(rows[0]["exams"][0]["block"]["score"])

    def test_session_scores_excludes_cross_tenant_exam_m2m_contamination(self):
        other_tenant = Tenant.objects.create(name="Other Tenant", code="scorecope-other", is_active=True)
        foreign_exam = Exam.objects.create(
            tenant=other_tenant,
            title="타 테넌트 시험",
            pass_score=40,
            max_score=100,
        )
        foreign_exam.sessions.add(self.session)

        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [row["exam_id"] for row in response.data["meta"]["exams"]],
            [self.exam.id],
        )
        self.assertEqual(
            [row["exam_id"] for row in response.data["rows"][0]["exams"]],
            [self.exam.id],
        )

    def test_omr_manual_match_registers_exam_target_and_score_appears(self):
        ExamEnrollment.objects.filter(exam=self.exam).delete()
        sheet = Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=2)
        q1 = ExamQuestion.objects.create(sheet=sheet, number=1, score=5)
        q2 = ExamQuestion.objects.create(sheet=sheet, number=2, score=5)
        AnswerKey.objects.create(exam=self.exam, answers={str(q1.id): "2", str(q2.id): "4"})

        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=None,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.NEEDS_IDENTIFICATION,
            meta={"manual_review": {"required": True, "reasons": ["NO_MATCH"]}},
        )

        request = self.factory.post(
            f"/api/v1/submissions/submissions/{submission.id}/manual-edit/",
            {
                "identifier": {"enrollment_id": self.active_enrollment.id},
                "answers": [
                    {"exam_question_id": q1.id, "answer": "2"},
                    {"exam_question_id": q2.id, "answer": "4"},
                ],
                "note": "test_manual_match",
            },
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SubmissionViewSet.as_view({"post": "manual_edit"})(
            request,
            pk=submission.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            ExamEnrollment.objects.filter(
                exam=self.exam,
                enrollment=self.active_enrollment,
            ).exists()
        )
        result = Result.objects.get(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.active_enrollment,
        )
        self.assertEqual(float(result.total_score), 10.0)

        score_request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        score_request.tenant = self.tenant
        force_authenticate(score_request, user=self.admin)
        score_response = SessionScoresView.as_view()(score_request, session_id=self.session.id)

        self.assertEqual(score_response.status_code, 200, score_response.data)
        rows = score_response.data["rows"]
        active_row = next(row for row in rows if row["enrollment_id"] == self.active_enrollment.id)
        self.assertEqual(len(active_row["exams"]), 1)
        self.assertEqual(active_row["exams"][0]["block"]["score"], 10.0)

    def test_session_scores_marks_omr_review_required_without_fail_score(self):
        Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.active_enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.DONE,
            meta={
                "manual_review": {
                    "required": True,
                    "reasons": ["ANSWER_STATUS_NOT_OK"],
                }
            },
        )

        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["meta"]["exams"][0]["exam_id"], self.exam.id)
        row = next(row for row in response.data["rows"] if row["enrollment_id"] == self.active_enrollment.id)
        block = row["exams"][0]["block"]
        self.assertIsNone(block["score"])
        self.assertEqual(block["meta"]["status"], "OMR_REVIEW_REQUIRED")
        self.assertTrue(block["meta"]["manual_review_required"])
        self.assertEqual(block["meta"]["manual_review_reasons"], ["ANSWER_STATUS_NOT_OK"])

    def test_completed_progress_overrides_unresolved_clinic_link(self):
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.active_enrollment,
            session=self.session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            approved=True,
            source_type="exam",
            source_id=self.exam.id,
        )
        SessionProgress.objects.create(
            enrollment=self.active_enrollment,
            session=self.session,
            exam_passed=True,
            homework_passed=True,
            video_completed=True,
            completed=True,
        )

        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        row = next(row for row in response.data["rows"] if row["enrollment_id"] == self.active_enrollment.id)
        self.assertTrue(row["progress_completed"])
        self.assertEqual(row["progress_status"], "completed")
        self.assertFalse(row["clinic_required"])
        self.assertFalse(row["exams"][0]["block"]["clinic_required"])
        self.assertIsNone(row["exams"][0]["clinic_link_id"])

        targets = ClinicTargetService.list_admin_targets(tenant=self.tenant)
        self.assertFalse(
            any(target.get("clinic_link_id") == link.id for target in targets),
            "완료 상태 학생은 현재 클리닉 대상자 API에서 제외되어야 한다.",
        )
        highlights = compute_clinic_highlight_map(
            tenant=self.tenant,
            enrollment_ids={self.active_enrollment.id},
            session=self.session,
        )
        self.assertFalse(highlights[self.active_enrollment.id])

    def test_session_scores_ignores_exam_clinic_link_when_source_not_in_session(self):
        other_exam = Exam.objects.create(
            tenant=self.tenant,
            title="다른 차시 시험",
            pass_score=60,
            max_score=100,
        )
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.active_enrollment,
            session=self.session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="exam",
            source_id=other_exam.id,
            meta={"kind": "EXAM_FAILED", "exam_id": other_exam.id},
        )

        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        row = next(row for row in response.data["rows"] if row["enrollment_id"] == self.active_enrollment.id)
        self.assertFalse(row["clinic_required"])
        self.assertFalse(row["name_highlight_clinic_target"])

        targets = ClinicTargetService.list_admin_targets(tenant=self.tenant)
        self.assertFalse(any(target.get("clinic_link_id") == link.id for target in targets))

    def test_session_scores_ignores_homework_clinic_link_when_assignment_removed(self):
        HomeworkAssignment.objects.filter(
            homework=self.homework,
            enrollment=self.active_enrollment,
        ).delete()
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.active_enrollment,
            session=self.session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            source_type="homework",
            source_id=self.homework.id,
            meta={"kind": "HOMEWORK_FAILED", "homework_id": self.homework.id},
        )

        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        row = next(row for row in response.data["rows"] if row["enrollment_id"] == self.active_enrollment.id)
        self.assertFalse(row["clinic_required"])
        self.assertFalse(row["name_highlight_clinic_target"])
        self.assertEqual(row["homeworks"], [])

        targets = ClinicTargetService.list_admin_targets(tenant=self.tenant)
        self.assertFalse(any(target.get("clinic_link_id") == link.id for target in targets))

    def test_session_scores_include_retake_history_and_final_pass(self):
        self.exam.pass_score = 70
        self.exam.max_score = 100
        self.exam.save(update_fields=["pass_score", "max_score"])

        attempt1 = ExamAttempt.objects.create(
            exam=self.exam,
            enrollment=self.active_enrollment,
            attempt_index=1,
            is_retake=False,
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 50.0,
                    "max_score": 100.0,
                    "source": "test",
                },
                "total_score": 50.0,
            },
        )
        Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.active_enrollment,
            attempt=attempt1,
            total_score=50,
            max_score=100,
        )
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.active_enrollment,
            session=self.session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            approved=True,
            source_type="exam",
            source_id=self.exam.id,
        )
        ClinicRemediationService.submit_exam_retake(
            clinic_link_id=link.id,
            score=70,
            max_score=100,
            pass_score=60,
            graded_by_user_id=self.admin.id,
        )

        request = self.factory.get(f"/api/v1/results/admin/sessions/{self.session.id}/scores/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SessionScoresView.as_view()(request, session_id=self.session.id)

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["rows"][0]
        exam_entry = row["exams"][0]
        self.assertEqual(exam_entry["block"]["passed"], False)
        self.assertEqual(exam_entry["block"]["final_pass"], True)
        self.assertEqual(exam_entry["block"]["achievement"], "REMEDIATED")
        self.assertEqual(len(exam_entry["attempts"]), 2)
        self.assertEqual(exam_entry["attempts"][0]["pass_score"], 70.0)
        self.assertEqual(exam_entry["attempts"][1]["pass_score"], 60.0)
        self.assertEqual(exam_entry["attempts"][1]["passed"], True)
