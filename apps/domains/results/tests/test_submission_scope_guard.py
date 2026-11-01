from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import AnswerKey, Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.lectures.models import Lecture, Session
from apps.domains.results.models import ExamAttempt, ExamResult, Result, ResultFact, ResultItem
from apps.domains.results.services.exam_grading_service import ExamGradingService
from apps.domains.results.services.attempt_service import ExamAttemptService
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.results.services.sync_result_from_submission import (
    sync_result_from_exam_submission,
)
from apps.domains.results.services.question_stats_service import QuestionStatsService
from apps.domains.students.models import Student
from apps.domains.submissions.models import (
    OMRDetectedAnswer,
    OMRRecognitionRun,
    OMRStudentMatch,
    Submission,
    SubmissionAnswer,
)


User = get_user_model()


class SubmissionScopeGuardTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="scope-guard", name="Scope Guard", is_active=True)
        self.admin = User.objects.create_user(
            username="scope-guard-admin",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Scope Guard Lecture",
            name="Scope Guard Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1회")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Scope Guard Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=10,
        )
        self.exam.sessions.add(self.session)
        self.sheet = Sheet.objects.create(exam=self.exam, name="MAIN", total_questions=1)
        self.question = ExamQuestion.objects.create(sheet=self.sheet, number=1, score=10)
        AnswerKey.objects.create(exam=self.exam, answers={str(self.question.id): "1"})

        student_user = User.objects.create_user(
            username="scope-guard-student",
            password="pw1234",
            tenant=self.tenant,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            name="Scope Student",
            ps_number="SG-1",
            omr_code="SG000001",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            lecture=self.lecture,
            student=self.student,
            status="ACTIVE",
        )

    def _unassigned_submission(self):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.ANSWERS_READY,
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=self.question.id,
            answer="1",
        )
        return submission

    def _submission_for_exam(self, exam, question, answer="1"):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.ANSWERS_READY,
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            exam_question_id=question.id,
            answer=answer,
        )
        return submission

    def _create_mixed_exam(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Mixed Scope Guard Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
        )
        exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=exam,
            name="MIXED",
            total_questions=2,
            choice_count=1,
            essay_count=1,
        )
        choice = ExamQuestion.objects.create(sheet=sheet, number=1, score=80)
        essay = ExamQuestion.objects.create(sheet=sheet, number=2, score=20)
        AnswerKey.objects.create(
            exam=exam,
            answers={str(choice.id): "1", str(essay.id): "해설참조"},
        )
        return exam, choice, essay

    def test_numeric_essay_answer_is_auto_graded_as_short_answer(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Numeric Short Answer Exam",
            subject="수학",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=30,
        )
        exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=exam,
            name="NUMERIC-SHORT",
            total_questions=1,
            choice_count=0,
            essay_count=1,
        )
        question = ExamQuestion.objects.create(sheet=sheet, number=1, score=30)
        AnswerKey.objects.create(exam=exam, answers={str(question.id): "007"})
        submission = self._submission_for_exam(exam, question, answer="7")

        exam_result = ExamGradingService().auto_grade_objective(
            submission_id=submission.id,
        )
        result = sync_result_from_exam_submission(submission.id)

        self.assertEqual(float(exam_result.total_score), 30.0)
        self.assertEqual(float(exam_result.max_score), 30.0)
        self.assertEqual(float(result.objective_score), 30.0)
        item = ResultItem.objects.get(result=result, question=question)
        self.assertTrue(item.is_correct)
        self.assertEqual(float(item.score), 30.0)

    def test_auto_grade_rejects_unassigned_submission_without_side_effects(self):
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            ExamGradingService().auto_grade_objective(submission_id=submission.id)

        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())

    def test_auto_grade_rejects_submission_after_exam_is_unlinked_from_lecture(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        self.exam.sessions.remove(self.session)
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            ExamGradingService().auto_grade_objective(submission_id=submission.id)

        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())
        self.assertFalse(Result.objects.filter(target_id=self.exam.id, enrollment_id=self.enrollment.id).exists())
        self.assertFalse(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).exists())

    def test_sync_rejects_unassigned_submission_without_side_effects(self):
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(submission.id)

        self.assertFalse(Result.objects.filter(target_id=self.exam.id, enrollment_id=self.enrollment.id).exists())
        self.assertFalse(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).exists())

    def test_session_enrollment_alone_does_not_assign_exam_submission(self):
        SessionEnrollment.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=self.enrollment,
        )
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            ExamGradingService().auto_grade_objective(submission_id=submission.id)

        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())
        self.assertFalse(Result.objects.filter(target_id=self.exam.id, enrollment_id=self.enrollment.id).exists())

    def test_sync_rejects_duplicate_submission_when_retake_is_disabled(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        first = self._unassigned_submission()

        first_result = sync_result_from_exam_submission(first.id)
        self.assertIsNotNone(first_result)
        first.status = Submission.Status.DONE
        first.save(update_fields=["status", "updated_at"])
        second = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(second.id)

        attempts = ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment)
        self.assertEqual(attempts.count(), 1)
        self.assertEqual(attempts.get().submission_id, first.id)
        result = Result.objects.get(target_id=self.exam.id, enrollment_id=self.enrollment.id)
        self.assertEqual(result.attempt.submission_id, first.id)
        self.assertFalse(ExamResult.objects.filter(submission=second).exists())

    def test_done_omr_regrade_recomputes_final_and_persisted_result(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        submission = self._unassigned_submission()
        submission.source = Submission.Source.OMR_SCAN
        submission.meta = {"manual_review": {"required": False}}
        submission.save(update_fields=["source", "meta", "updated_at"])

        first = grade_submission(submission.id)
        self.assertEqual(float(first.total_score), 10.0)
        self.assertEqual(
            float(Result.objects.get(target_id=self.exam.id, enrollment=self.enrollment).total_score),
            10.0,
        )

        SubmissionAnswer.objects.filter(
            submission=submission,
            exam_question_id=self.question.id,
        ).update(answer="2")
        from academy.application.use_cases.omr.grading_readiness import (
            grade_omr_submission_if_ready,
        )

        decision = grade_omr_submission_if_ready(
            submission.id,
            actor="test.force_regrade",
            allow_done_regrade=True,
        )

        self.assertTrue(decision.graded)
        legacy = ExamResult.objects.get(submission=submission)
        canonical = Result.objects.get(target_id=self.exam.id, enrollment=self.enrollment)
        self.assertEqual(float(legacy.total_score), 0.0)
        self.assertEqual(float(canonical.total_score), 0.0)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.DONE)

    def test_sync_attaches_zero_manual_placeholder_to_real_submission(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        attempt = ExamAttempt.objects.create(
            exam=self.exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 0.0,
                    "max_score": 10.0,
                    "source": "admin_manual_total",
                }
            },
        )
        Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=0.0,
            max_score=10.0,
            objective_score=0.0,
        )
        submission = self._unassigned_submission()

        result = sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, submission.id)
        self.assertEqual(attempt.attempt_index, 1)
        self.assertTrue(attempt.is_representative)
        self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_replaced_manual_zero")
        self.assertEqual(float(result.total_score), 10.0)
        self.assertEqual(float(result.objective_score), 10.0)
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).count(), 1)

    def _absence_before_confirmed_scan(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        attempt = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment, submission_id=0,
            attempt_index=1, is_representative=True, status="done",
            meta={"status": "NOT_SUBMITTED", "synced_from_result": True},
        )
        result = Result.objects.create(
            target_type="exam", target_id=self.exam.id, enrollment=self.enrollment,
            attempt=attempt, total_score=0, objective_score=0, max_score=10,
        )
        fact = ResultFact.objects.create(
            target_type="exam", target_id=self.exam.id, enrollment=self.enrollment,
            attempt=attempt, submission_id=0, question_id=0, answer="",
            is_correct=False, score=0, max_score=10, source="manual_not_submitted",
            meta={"status": "NOT_SUBMITTED"},
        )
        submission = self._submission_for_exam(self.exam, self.question)
        submission.source = Submission.Source.OMR_SCAN
        submission.save(update_fields=["source", "updated_at"])
        OMRStudentMatch.objects.create(
            tenant=self.tenant, submission=submission, enrollment=self.enrollment,
            status=OMRStudentMatch.Status.CONFIRMED,
            method=OMRStudentMatch.Method.AUTO_IDENTIFIER,
            identifier_status="matched", actor="test.absence", is_current=True,
        )
        return attempt, result, fact, submission

    def test_first_scan_after_unused_absence_completes_without_retake(self):
        attempt, result, fact, submission = self._absence_before_confirmed_scan()
        self.assertFalse(self.exam.allow_retake)
        original_fact = ResultFact.objects.filter(pk=fact.pk).values().get()
        for _ in range(2):
            grade_submission(submission.id)
            submission.refresh_from_db()
            result.refresh_from_db()
            attempt.refresh_from_db()
            self.assertEqual(submission.status, Submission.Status.DONE)
            self.assertEqual(result.total_score, 10)
            self.assertEqual(result.attempt_id, attempt.id)
            self.assertEqual(attempt.submission_id, submission.id)
            self.assertEqual(attempt.attempt_index, 1)
            self.assertFalse(attempt.is_retake)
            self.assertNotIn("status", attempt.meta)
            self.assertEqual(attempt.meta["absence_placeholder"]["previous_meta"]["status"], "NOT_SUBMITTED")
            self.assertEqual(attempt.meta["initial_snapshot"]["total_score"], 10)
            self.assertEqual(ResultItem.objects.get(result=result).answer, "1")
            self.assertEqual(ResultFact.objects.filter(pk=fact.pk).values().get(), original_fact)
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).count(), 1)

    def test_absence_decision_after_scan_is_not_automatically_replaced(self):
        attempt, result, _fact, submission = self._absence_before_confirmed_scan()
        attempt.save(update_fields=["updated_at"])
        with self.assertRaisesMessage(DjangoValidationError, "Retake is not allowed"):
            grade_submission(submission.id)
        attempt.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(attempt.submission_id, 0)
        self.assertEqual(attempt.meta["status"], "NOT_SUBMITTED")
        self.assertEqual(result.total_score, 0)

    def test_manual_match_preserves_later_absence_then_recovers_after_explicit_answer_confirmation(self):
        from datetime import timedelta
        from django.urls import resolve
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership
        from apps.domains.results.views.admin_exam_manual_answers_view import AdminExamManualAnswersView

        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="teacher")
        attempt, result, fact, submission = self._absence_before_confirmed_scan()
        submission.created_at = attempt.updated_at - timedelta(seconds=1)
        submission.save(update_fields=["created_at"])
        self.assertGreater(attempt.updated_at, submission.created_at)
        submission.enrollment_id = None
        submission.status = Submission.Status.NEEDS_IDENTIFICATION
        submission.save(update_fields=["enrollment_id", "status", "updated_at"])
        original_fact = ResultFact.objects.filter(pk=fact.pk).values().get()
        original_answers = list(submission.answers.values("id", "answer"))

        def match(answers):
            request = APIRequestFactory().post(
                f"/api/v1/submissions/submissions/{submission.id}/manual-edit/",
                {"identifier": {"enrollment_id": self.enrollment.id}, "answers": answers},
                format="json",
            )
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            route = resolve(request.path)
            return route.func(request, **route.kwargs)

        response = match([{"question_id": self.question.id, "answer": "2"}])
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["code"], "OMR_ATTEMPT_CONFLICT")
        self.assertEqual(response.data["reason"], "retake_not_allowed")
        self.assertEqual(response.data["recovery"]["enrollment_id"], self.enrollment.id)
        submission.refresh_from_db()
        result.refresh_from_db()
        attempt.refresh_from_db()
        self.assertIsNone(submission.enrollment_id)
        self.assertEqual(submission.status, Submission.Status.NEEDS_IDENTIFICATION)
        self.assertEqual(attempt.submission_id, 0)
        self.assertEqual(attempt.meta["status"], "NOT_SUBMITTED")
        self.assertEqual(result.total_score, 0)
        self.assertEqual(list(submission.answers.values("id", "answer")), original_answers)
        self.assertEqual(ResultFact.objects.filter(pk=fact.pk).values().get(), original_fact)

        # A teacher's explicit, version-bound preview/confirm is the recovery.
        # Student identification alone must never cancel a later absence.
        payload = {
            "answers": {str(self.question.id): "1"},
            "expected_version": result.updated_at.isoformat(),
            "note": "교사가 종이 답안을 확인하고 미응시를 정정",
            "apply": False,
        }
        for apply in (False, True):
            payload["apply"] = apply
            request = APIRequestFactory().post(
                f"/results/admin/exams/{self.exam.id}/enrollments/{self.enrollment.id}/manual-answers/",
                payload, format="json",
            )
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            response = AdminExamManualAnswersView.as_view()(
                request, exam_id=self.exam.id, enrollment_id=self.enrollment.id,
            )
            self.assertEqual(response.status_code, 200, response.data)
            payload["preview_token"] = response.data["preview_token"]
        manual_facts = list(
            ResultFact.objects.filter(attempt=attempt, source="manual").values()
        )
        self.assertEqual(len(manual_facts), 1)
        for _ in range(2):
            response = match([])
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(response.data["graded"])
            submission.refresh_from_db()
            result.refresh_from_db()
            attempt.refresh_from_db()
            self.assertEqual(submission.status, Submission.Status.DONE)
            self.assertEqual(submission.enrollment_id, self.enrollment.id)
            self.assertEqual(result.total_score, 10)
            self.assertEqual(attempt.submission_id, submission.id)
            self.assertNotIn("status", attempt.meta)
            self.assertEqual(list(submission.answers.values("id", "answer")), original_answers)
            self.assertEqual(ResultFact.objects.filter(pk=fact.pk).values().get(), original_fact)
            self.assertEqual(list(ResultFact.objects.filter(attempt=attempt, source="manual").values()), manual_facts)
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).count(), 1)

    def test_absence_placeholder_with_prior_answer_history_is_preserved(self):
        attempt, result, _fact, submission = self._absence_before_confirmed_scan()
        ResultFact.objects.create(
            target_type="exam", target_id=self.exam.id, enrollment=self.enrollment,
            attempt=attempt, submission_id=0, question_id=self.question.id,
            answer="1", is_correct=True, score=10, max_score=10, source="manual",
        )
        with self.assertRaisesMessage(DjangoValidationError, "Retake is not allowed"):
            grade_submission(submission.id)
        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, 0)
        self.assertEqual(attempt.meta["status"], "NOT_SUBMITTED")

    def test_attempt_limit_has_a_typed_conflict_without_changing_existing_attempt(self):
        self.exam.allow_retake = True
        self.exam.max_attempts = 1
        self.exam.save(update_fields=["allow_retake", "max_attempts"])
        ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment, submission_id=77,
            attempt_index=1, is_representative=True, status="done",
        )
        with self.assertRaises(DjangoValidationError) as caught:
            ExamAttemptService.create_for_submission(
                exam_id=self.exam.id, enrollment_id=self.enrollment.id, submission_id=78,
            )
        self.assertEqual(caught.exception.code, "max_attempts_exceeded")
        self.assertEqual(ExamAttempt.objects.get(exam=self.exam).submission_id, 77)

    def test_mixed_omr_assignment_without_key_preserves_answers_then_key_save_grades(self):
        from django.urls import resolve
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership

        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="teacher")
        self.exam.max_score = 100
        self.exam.save(update_fields=["max_score"])
        self.sheet.total_questions = 20
        self.sheet.choice_count = 19
        self.sheet.essay_count = 1
        self.sheet.save(update_fields=["total_questions", "choice_count", "essay_count"])
        self.question.score = 5
        self.question.save(update_fields=["score"])
        choices = [self.question] + [
            ExamQuestion.objects.create(sheet=self.sheet, number=number, score=5)
            for number in range(2, 20)
        ]
        essay = ExamQuestion.objects.create(sheet=self.sheet, number=20, score=5)
        AnswerKey.objects.filter(exam=self.exam).delete()
        submission = self._submission_for_exam(self.exam, self.question)
        submission.source = Submission.Source.OMR_SCAN
        submission.enrollment_id = None
        submission.status = Submission.Status.NEEDS_IDENTIFICATION
        submission.file_key = f"tenants/{self.tenant.id}/qa/omr-existing.png"
        submission.save(update_fields=["source", "enrollment_id", "status", "file_key", "updated_at"])
        SubmissionAnswer.objects.bulk_create([
            SubmissionAnswer(tenant=self.tenant, submission=submission, exam_question_id=q.id, answer="1")
            for q in choices[1:]
        ])
        OMRRecognitionRun.objects.create(
            tenant=self.tenant, submission=submission, status="DONE", answer_count=19,
            answer_status_counts={"ok": 19},
            contract_snapshot={"choice_count": 19, "essay_count": 1, "auto_detect_count": 19},
        )
        original_answers = list(submission.answers.order_by("id").values("id", "answer"))
        original_file = submission.file_key
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

        def post(path, payload):
            request = APIRequestFactory().post(path, payload, format="json")
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            route = resolve(path)
            return route.func(request, **route.kwargs)

        complete_choice_key = {str(q.id): "1" for q in choices}
        for pending_key in (
            None, None, {},
            {str(q.id): "1" for q in choices[:-1]},
            {**complete_choice_key, str(choices[-1].id): " "},
            {**complete_choice_key, str(choices[-1].id): []},
            {**complete_choice_key, str(choices[-1].id): [None, ""]},
        ):
            AnswerKey.objects.filter(exam=self.exam).delete()
            if pending_key is not None:
                AnswerKey.objects.create(exam=self.exam, answers=pending_key)
            response = post(
                f"/api/v1/submissions/submissions/{submission.id}/manual-edit/",
                {"identifier": {"enrollment_id": self.enrollment.id}, "answers": []},
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertFalse(response.data["graded"])
            self.assertEqual(response.data["code"], "OMR_GRADING_CONFIGURATION_REQUIRED")
            self.assertIn("answer_key", response.data["readiness"]["missing"])
            self.assertEqual(response.data["readiness"]["answer_count"], 19)
            submission.refresh_from_db()
            self.assertEqual(submission.status, Submission.Status.ANSWERS_READY)
            self.assertEqual(submission.enrollment_id, self.enrollment.id)
            self.assertEqual(submission.file_key, original_file)
            self.assertEqual(list(submission.answers.order_by("id").values("id", "answer")), original_answers)
            self.assertFalse(Result.objects.filter(target_id=self.exam.id).exists())
            self.assertFalse(ExamAttempt.objects.filter(exam=self.exam).exists())
            self.assertFalse(ResultFact.objects.filter(target_id=self.exam.id).exists())

        # Direct key-save regrading must enforce the same complete choice key.
        AnswerKey.objects.filter(exam=self.exam).delete()
        for incomplete in ({}, {str(q.id): "1" for q in choices[:-1]}):
            response = post("/api/v1/exams/answer-keys/", {"exam": self.exam.id, "answers": incomplete})
            self.assertEqual(response.status_code, 400, response.data)
            self.assertFalse(AnswerKey.objects.filter(exam=self.exam).exists())
            self.assertFalse(ResultFact.objects.filter(target_id=self.exam.id).exists())
            self.assertFalse(ExamAttempt.objects.filter(exam=self.exam).exists())
            self.assertEqual(list(submission.answers.order_by("id").values("id", "answer")), original_answers)

        def inbox_row():
            path = "/api/v1/submissions/submissions/pending/"
            request = APIRequestFactory().get(path)
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            route = resolve(path)
            response = route.func(request, **route.kwargs)
            self.assertEqual(response.status_code, 200, response.data)
            return next(row for row in response.data if row["id"] == submission.id)

        for _ in range(2):
            row = inbox_row()
            self.assertEqual(row["omr_readiness"], {"can_grade": False, "missing": ["answer_key"]})
            self.assertEqual(row["recovery"], {"action": "configure_exam", "exam_id": self.exam.id})

        discarded = Submission.objects.create(
            tenant=self.tenant, user=self.admin, enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM, target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN, status=Submission.Status.FAILED,
            file_key=f"tenants/{self.tenant.id}/qa/discarded-original.png",
            error_message="discarded:operator_discarded",
            meta={"discarded": {"reason": "operator_discarded"}},
        )
        SubmissionAnswer.objects.create(
            tenant=self.tenant, submission=discarded, exam_question_id=self.question.id, answer="2",
        )
        discarded_before = list(Submission.objects.filter(pk=discarded.id).values())
        discarded_answers = list(discarded.answers.values())

        # Only the teacher's authoritative key completes the existing uploads.
        response = post("/api/v1/exams/answer-keys/", {
            "exam": self.exam.id, "answers": {str(q.id): "1" for q in choices},
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["regrade"][0]["graded"], 1)
        self.assertEqual(response.data["regrade"][0]["skipped"], 1)
        self.assertEqual(response.data["regrade"][0]["failed"], [])
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.DONE)
        self.assertEqual(submission.file_key, original_file)
        self.assertEqual(list(submission.answers.order_by("id").values("id", "answer")), original_answers)
        result = Result.objects.get(target_id=self.exam.id, enrollment=self.enrollment)
        self.assertEqual(result.objective_score, 95)
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam).count(), 1)
        self.assertEqual(ExamResult.objects.get(submission=submission).status, ExamResult.Status.DRAFT)
        self.assertFalse(ResultItem.objects.filter(result=result, question=essay, source="manual").exists())
        self.assertIsNone(inbox_row()["omr_readiness"])
        self.assertIsNone(inbox_row()["recovery"])

        # The teacher's item score completes both the mixed exam and its stats.
        from apps.domains.results.models import ScoreEditDraft

        ScoreEditDraft.objects.create(
            tenant=self.tenant, session=self.session, editor_user=self.admin,
            client_id="mixed-key-recovery", payload={"client_id": "mixed-key-recovery", "changes": []},
        )
        path = f"/api/v1/results/admin/exams/{self.exam.id}/enrollments/{self.enrollment.id}/items/{essay.id}/"
        request = APIRequestFactory().patch(
            path, {"score": 5}, format="json",
            HTTP_X_SCORE_EDITOR_CLIENT="mixed-key-recovery", HTTP_X_SCORE_SESSION_ID=str(self.session.id),
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        route = resolve(path)
        response = route.func(request, **route.kwargs)
        self.assertEqual(response.status_code, 200, response.data)
        result.refresh_from_db()
        canonical = ExamResult.objects.get(submission=submission)
        self.assertEqual((result.objective_score, result.total_score), (95, 100))
        self.assertEqual((canonical.status, canonical.total_score), (ExamResult.Status.FINAL, 100))
        stats = QuestionStatsService.per_question_stats(exam_id=self.exam.id)
        objective_stats = [row for row in stats if row["question_id"] in {q.id for q in choices}]
        self.assertEqual(len(objective_stats), 19)
        self.assertEqual({(row["attempts"], row["avg_score"]) for row in objective_stats}, {(1, 5)})
        self.assertEqual(len(stats), 20)
        essay_stats = next(row for row in stats if row["question_id"] == essay.id)
        self.assertEqual(
            (essay_stats["attempts"], essay_stats["avg_score"], essay_stats["correct"], essay_stats["accuracy"]),
            (1, 5, 1, 1.0),
        )
        self.assertEqual(list(Submission.objects.filter(pk=discarded.id).values()), discarded_before)
        self.assertEqual(list(discarded.answers.values()), discarded_answers)
        self.assertFalse(ExamResult.objects.filter(submission=discarded).exists())
        self.assertFalse(ExamAttempt.objects.filter(submission_id=discarded.id).exists())

    def test_existing_omr_results_reject_invalid_configuration_without_saving_changed_answers(self):
        from django.urls import resolve
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership

        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="teacher")
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        submission = self._submission_for_exam(self.exam, self.question)
        submission.source = Submission.Source.OMR_SCAN
        submission.save(update_fields=["source"])
        grade_submission(submission.id)

        def post(path, payload):
            request = APIRequestFactory().post(path, payload, format="json")
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            route = resolve(path)
            return route.func(request, **route.kwargs)

        def snapshot():
            submission.refresh_from_db()
            return {
                "answers": list(submission.answers.order_by("id").values()),
                "meta": submission.meta,
                "enrollment_id": submission.enrollment_id,
                "status": submission.status,
                "results": list(Result.objects.filter(target_id=self.exam.id).values()),
                "canonical": list(ExamResult.objects.filter(submission=submission).values()),
                "facts": list(ResultFact.objects.filter(target_id=self.exam.id).order_by("id").values()),
                "stats": QuestionStatsService.per_question_stats(exam_id=self.exam.id),
            }

        path = f"/api/v1/submissions/submissions/{submission.id}/manual-edit/"
        for invalid_key in (None, [], {str(self.question.id + 1000): "1"}, {}, {str(self.question.id): " "}):
            with self.subTest(key=invalid_key):
                AnswerKey.objects.filter(exam=self.exam).delete()
                if invalid_key is not None:
                    AnswerKey.objects.create(exam=self.exam, answers=invalid_key)
                    # A result must also protect a submission reopened by another flow.
                    Submission.objects.filter(pk=submission.pk).update(status=Submission.Status.ANSWERS_READY)
                before = snapshot()
                response = post(path, {"answers": [{"question_id": self.question.id, "answer": "2"}]})
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data["code"], "OMR_GRADING_CONFIGURATION_CONFLICT")
                self.assertEqual(response.data["missing"], ["answer_key"])
                self.assertEqual(snapshot(), before)

        key = AnswerKey.objects.get(exam=self.exam)
        key_path = f"/api/v1/exams/answer-keys/{key.id}/"
        request = APIRequestFactory().patch(
            key_path, {"answers": {str(self.question.id): "1"}}, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        route = resolve(key_path)
        response = route.func(request, **route.kwargs)
        self.assertEqual(response.status_code, 200, response.data)
        response = post(path, {"answers": [{"question_id": self.question.id, "answer": "2"}]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["graded"])
        self.assertEqual(submission.answers.get().answer, "2")
        self.assertEqual(Result.objects.get(target_id=self.exam.id).total_score, 0)
        self.assertEqual(ExamResult.objects.get(submission=submission).status, ExamResult.Status.FINAL)
        self.assertEqual(QuestionStatsService.per_question_stats(exam_id=self.exam.id)[0]["avg_score"], 0)
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam).count(), 1)

    def test_pending_configuration_is_fresh_cached_per_exam_and_has_no_foreign_recovery(self):
        from unittest.mock import patch
        from django.urls import resolve
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership
        from apps.support.submissions.grading_contract_dependencies import omr_grading_configuration_missing

        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="teacher")
        rows = []
        for _ in range(2):
            submission = self._submission_for_exam(self.exam, self.question)
            submission.source = Submission.Source.OMR_SCAN
            submission.meta = {"omr_readiness": {"can_grade": True, "missing": []}}
            submission.save(update_fields=["source", "meta"])
            rows.append(submission)

        def get():
            path = "/api/v1/submissions/submissions/pending/"
            request = APIRequestFactory().get(path)
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            route = resolve(path)
            response = route.func(request, **route.kwargs)
            self.assertEqual(response.status_code, 200, response.data)
            return response.data

        AnswerKey.objects.filter(exam=self.exam).update(answers=[])
        before = list(Submission.objects.order_by("id").values())
        with patch(
            "apps.domains.submissions.views.pending_submissions_view.omr_grading_configuration_missing",
            wraps=omr_grading_configuration_missing,
        ) as check:
            response = get()
        check.assert_called_once_with(tenant_id=self.tenant.id, exam_id=self.exam.id)
        self.assertEqual([r["omr_readiness"]["missing"] for r in response], [["answer_key"], ["answer_key"]])
        self.assertEqual(list(Submission.objects.order_by("id").values()), before)
        AnswerKey.objects.filter(exam=self.exam).update(answers={str(self.question.id): "1"})
        self.assertTrue(all(r["omr_readiness"] is None and r["recovery"] is None for r in get()))

        foreign = Tenant.objects.create(code="pending-foreign-key", name="Foreign")
        foreign_exam = Exam.objects.create(tenant=foreign, title="Foreign", exam_type=Exam.ExamType.REGULAR)
        Submission.objects.filter(pk=rows[0].id).update(target_id=foreign_exam.id)
        Submission.objects.filter(pk=rows[1].id).update(meta={"discarded": {"reason": "duplicate"}})
        AnswerKey.objects.filter(exam=self.exam).delete()
        self.assertTrue(all(r["omr_readiness"] is None and r["recovery"] is None for r in get()))

    def test_key_save_preserves_teacher_result_entered_after_pending_assignment(self):
        from django.urls import resolve
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership

        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="teacher")
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        AnswerKey.objects.filter(exam=self.exam).delete()
        submission = self._submission_for_exam(self.exam, self.question)
        submission.source = Submission.Source.OMR_SCAN
        submission.enrollment_id = None
        submission.save(update_fields=["source", "enrollment_id"])

        def post(path, payload):
            request = APIRequestFactory().post(path, payload, format="json")
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            route = resolve(path)
            return route.func(request, **route.kwargs)

        response = post(f"/api/v1/submissions/submissions/{submission.id}/manual-edit/", {
            "identifier": {"enrollment_id": self.enrollment.id}, "answers": [],
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["graded"])
        # A later independent teacher score is authoritative, even while the
        # older scan is waiting for its key. It must not become an OMR result.
        attempt = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment, submission_id=0,
            attempt_index=1, is_representative=True, status="done",
            meta={"initial_snapshot": {"source": "admin_manual_total", "total_score": 7, "max_score": 10}},
        )
        result = Result.objects.create(
            target_type="exam", target_id=self.exam.id, enrollment=self.enrollment,
            attempt=attempt, total_score=7, objective_score=7, max_score=10,
        )
        original = {
            "submission": list(Submission.objects.filter(pk=submission.id).values()),
            "answers": list(submission.answers.values()),
            "result": list(Result.objects.filter(pk=result.id).values()),
            "attempt": list(ExamAttempt.objects.filter(pk=attempt.id).values()),
        }
        response = post("/api/v1/exams/answer-keys/", {
            "exam": self.exam.id, "answers": {str(self.question.id): "1"},
        })
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(list(Submission.objects.filter(pk=submission.id).values()), original["submission"])
        self.assertEqual(list(submission.answers.values()), original["answers"])
        self.assertEqual(list(Result.objects.filter(pk=result.id).values()), original["result"])
        self.assertEqual(list(ExamAttempt.objects.filter(pk=attempt.id).values()), original["attempt"])
        self.assertFalse(AnswerKey.objects.filter(exam=self.exam).exists())
        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())

    def test_late_roster_assignment_retries_unknown_errors_and_persists_without_answer_changes(self):
        from unittest.mock import patch
        from django.urls import resolve
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership

        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="teacher")
        submission = self._submission_for_exam(self.exam, self.question)
        submission.source = Submission.Source.OMR_SCAN
        submission.enrollment_id = None
        submission.status = Submission.Status.NEEDS_IDENTIFICATION
        submission.save(update_fields=["source", "enrollment_id", "status", "updated_at"])
        # Register the roster after uploading the unassigned scan.
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

        def match(answers):
            request = APIRequestFactory().post(
                f"/api/v1/submissions/submissions/{submission.id}/manual-edit/",
                {"identifier": {"enrollment_id": self.enrollment.id}, "answers": answers},
                format="json",
            )
            request.tenant = self.tenant
            force_authenticate(request, user=self.admin)
            route = resolve(request.path)
            return route.func(request, **route.kwargs)

        for error in (RuntimeError("temporary grading failure"), DjangoValidationError({"score": "invalid"})):
            with patch(
                "academy.application.use_cases.omr.grading_readiness.grade_omr_submission_if_ready",
                side_effect=error,
            ):
                response = match([{"question_id": self.question.id, "answer": "2"}])
            self.assertEqual(response.status_code, 500, response.data)
            self.assertEqual(response.data["code"], "OMR_REGRADING_FAILED")
            submission.refresh_from_db()
            self.assertIsNone(submission.enrollment_id)
            self.assertEqual(submission.answers.get().answer, "1")
            self.assertFalse(ExamAttempt.objects.filter(exam=self.exam).exists())
        for _ in range(2):
            response = match([])
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(response.data["graded"])
            submission.refresh_from_db()
            self.assertEqual(submission.enrollment_id, self.enrollment.id)
            self.assertEqual(submission.status, Submission.Status.DONE)
            self.assertEqual(submission.answers.get().answer, "1")
            self.assertEqual(Result.objects.get(target_id=self.exam.id).total_score, 10)
            self.assertEqual(ExamResult.objects.get(submission=submission).status, ExamResult.Status.FINAL)
        self.assertEqual(ExamAttempt.objects.filter(exam=self.exam).count(), 1)

    def test_omr_configuration_validates_the_existing_key_contract_in_the_same_tenant(self):
        from apps.support.submissions.grading_contract_dependencies import omr_grading_configuration_missing

        def missing(exam_id):
            return omr_grading_configuration_missing(tenant_id=self.tenant.id, exam_id=exam_id)

        for answers in (None, [], {str(self.question.id + 1000): "1"}):
            AnswerKey.objects.filter(exam=self.exam).delete()
            if answers is not None:
                AnswerKey.objects.create(exam=self.exam, answers=answers)
            self.assertIn("answer_key", missing(self.exam.id))
        AnswerKey.objects.filter(exam=self.exam).update(answers={str(self.question.id): "1"})
        self.assertEqual(missing(self.exam.id), [])
        other_tenant = Tenant.objects.create(code="omr-other-config", name="Other Config")
        foreign_exam = Exam.objects.create(tenant=other_tenant, title="Other", exam_type=Exam.ExamType.REGULAR)
        self.assertEqual(missing(foreign_exam.id), ["exam"])

    def test_omr_key_completeness_preserves_multiple_answers_and_all_essay_empty_keys(self):
        from apps.support.submissions.grading_contract_dependencies import omr_grading_configuration_missing
        from apps.domains.results.guards.grading_contract import GradingContractGuard

        for answers in (["1", "3"], "1|3"):
            AnswerKey.objects.filter(exam=self.exam).update(answers={str(self.question.id): answers})
            self.assertEqual(omr_grading_configuration_missing(
                tenant_id=self.tenant.id, exam_id=self.exam.id,
            ), [])
            GradingContractGuard.validate_exam_for_grading(self.exam, require_omr_answers=True)

        self.sheet.choice_count = 0
        self.sheet.essay_count = 1
        self.sheet.save(update_fields=["choice_count", "essay_count"])
        AnswerKey.objects.filter(exam=self.exam).update(answers={})
        self.assertEqual(omr_grading_configuration_missing(
            tenant_id=self.tenant.id, exam_id=self.exam.id,
        ), [])
        # Use a fresh exam instance; related-object caches must not obscure the key.
        GradingContractGuard.validate_exam_for_grading(
            Exam.objects.get(pk=self.exam.pk), require_omr_answers=True,
        )
        self.assertFalse(ExamAttempt.objects.filter(exam=self.exam).exists())

    def test_sync_attaches_zero_manual_component_placeholders_to_real_submission(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)

        for source in ("admin_manual_objective", "admin_manual_subjective"):
            with self.subTest(source=source):
                ExamAttempt.objects.filter(exam=self.exam, enrollment=self.enrollment).delete()
                Result.objects.filter(target_id=self.exam.id, enrollment_id=self.enrollment.id).delete()
                Submission.objects.filter(
                    tenant=self.tenant,
                    user=self.admin,
                    target_type=Submission.TargetType.EXAM,
                    target_id=self.exam.id,
                ).delete()
                submission = self._unassigned_submission()
                attempt = ExamAttempt.objects.create(
                    exam=self.exam,
                    enrollment=self.enrollment,
                    submission_id=0,
                    attempt_index=1,
                    is_representative=True,
                    status="done",
                    meta={
                        "initial_snapshot": {
                            "total_score": 0.0,
                            "max_score": 10.0,
                            "source": source,
                        }
                    },
                )
                Result.objects.create(
                    target_type="exam",
                    target_id=self.exam.id,
                    enrollment=self.enrollment,
                    attempt=attempt,
                    total_score=0.0,
                    max_score=10.0,
                    objective_score=0.0,
                )

                result = sync_result_from_exam_submission(submission.id)

                attempt.refresh_from_db()
                self.assertEqual(attempt.submission_id, submission.id)
                self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_replaced_manual_zero")
                self.assertEqual(float(result.total_score), 10.0)
                self.assertEqual(float(result.objective_score), 10.0)

    def test_sync_does_not_overwrite_nonzero_manual_placeholder(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        attempt = ExamAttempt.objects.create(
            exam=self.exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 7.0,
                    "max_score": 10.0,
                    "source": "admin_manual_total",
                }
            },
        )
        Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=7.0,
            max_score=10.0,
            objective_score=7.0,
        )
        submission = self._unassigned_submission()

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, 0)
        result = Result.objects.get(target_id=self.exam.id, enrollment_id=self.enrollment.id)
        self.assertEqual(float(result.total_score), 7.0)
        self.assertFalse(ExamResult.objects.filter(submission=submission).exists())

    def test_sync_combines_omr_objective_with_existing_manual_subjective_score(self):
        exam, choice, essay = self._create_mixed_exam()
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 15.0,
                    "max_score": 100.0,
                    "source": "admin_manual_subjective",
                }
            },
        )
        Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=15.0,
            max_score=100.0,
            objective_score=0.0,
        )
        submission = self._submission_for_exam(exam, choice, answer="1")

        result = sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, submission.id)
        self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_attached_manual_subjective")
        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 95.0)
        self.assertEqual(float(result.max_score), 100.0)

    def test_sync_preserves_manual_subjective_score_and_pending_online_answer(self):
        exam, choice, essay = self._create_mixed_exam()
        submission = self._submission_for_exam(exam, choice, answer="1")

        result = sync_result_from_exam_submission(submission.id)
        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 80.0)
        self.assertEqual(float(result.max_score), 100.0)
        self.assertTrue(ResultItem.objects.filter(result=result, question=choice).exists())
        pending = ResultItem.objects.get(result=result, question=essay)
        self.assertEqual(pending.source, "online_pending")
        self.assertEqual(pending.answer, "")
        self.assertEqual(pending.score, 0)

        ResultItem.objects.update_or_create(
            result=result,
            question=essay,
            defaults={
                "answer": "manual", "is_correct": True, "score": 15.0,
                "max_score": 20.0, "source": "manual",
            },
        )
        result.total_score = 95.0
        result.objective_score = 80.0
        result.max_score = 100.0
        result.save(update_fields=["total_score", "objective_score", "max_score", "updated_at"])

        result = sync_result_from_exam_submission(submission.id)

        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 95.0)
        self.assertEqual(float(result.max_score), 100.0)
        self.assertFalse(
            ResultItem.objects.filter(
                result=result,
                question=essay,
                source__in=["online", "omr"],
            ).exists()
        )
        self.assertTrue(ResultItem.objects.filter(result=result, question=essay, source="manual").exists())

    def test_sync_drops_stale_subjective_difference_without_manual_evidence(self):
        exam, choice, _essay = self._create_mixed_exam()
        submission = self._submission_for_exam(exam, choice, answer="1")
        result = sync_result_from_exam_submission(submission.id)
        result.total_score = 85.0
        result.objective_score = 80.0
        result.save(update_fields=["total_score", "objective_score", "updated_at"])

        result = sync_result_from_exam_submission(submission.id)

        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 80.0)
        self.assertEqual(float(result.max_score), 100.0)

    def test_sync_preserves_manual_subjective_fact_on_regrade(self):
        exam, choice, _essay = self._create_mixed_exam()
        submission = self._submission_for_exam(exam, choice, answer="1")
        result = sync_result_from_exam_submission(submission.id)
        ResultFact.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            submission_id=submission.id,
            attempt=result.attempt,
            question_id=0,
            answer="",
            is_correct=True,
            score=12.0,
            max_score=20.0,
            source="manual_subjective",
            meta={"manual_subjective": True},
        )
        result.total_score = 92.0
        result.objective_score = 80.0
        result.save(update_fields=["total_score", "objective_score", "updated_at"])

        result = sync_result_from_exam_submission(submission.id)

        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 92.0)
        self.assertEqual(float(result.max_score), 100.0)

    def test_auto_grade_scores_only_objective_component_for_structured_mixed_sheet(self):
        exam, choice, _essay = self._create_mixed_exam()
        submission = self._submission_for_exam(exam, choice, answer="1")

        exam_result = ExamGradingService().auto_grade_objective(submission_id=submission.id)

        self.assertEqual(float(exam_result.total_score), 80.0)
        self.assertEqual(float(exam_result.objective_score), 80.0)
        self.assertEqual(float(exam_result.max_score), 80.0)
        self.assertEqual(list(exam_result.breakdown.keys()), ["1"])

    def test_sync_combines_omr_with_existing_manual_essay_item_score(self):
        exam, choice, essay = self._create_mixed_exam()
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"source": "manual_entry"},
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=15.0,
            max_score=100.0,
            objective_score=0.0,
        )
        ResultItem.objects.create(
            result=result,
            question=essay,
            answer="manual",
            is_correct=True,
            score=15.0,
            max_score=20.0,
            source="manual",
        )
        submission = self._submission_for_exam(exam, choice, answer="1")

        result = sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.submission_id, submission.id)
        self.assertEqual(attempt.meta["initial_snapshot"]["source"], "omr_attached_manual_essay_items")
        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 95.0)
        self.assertEqual(float(result.max_score), 100.0)
        self.assertEqual(ResultItem.objects.filter(result=result).count(), 2)
        self.assertTrue(ResultItem.objects.filter(result=result, question=essay, source="manual").exists())
        self.assertTrue(ResultItem.objects.filter(result=result, question=choice, source="online").exists())
        self.assertFalse(
            ResultItem.objects.filter(
                result=result,
                question=essay,
                source__in=["online", "omr"],
            ).exists()
        )

    def test_sync_attaches_exact_matching_manual_objective_result(self):
        ExamEnrollment.objects.create(exam=self.exam, enrollment=self.enrollment)
        attempt = ExamAttempt.objects.create(
            exam=self.exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"source": "manual_entry"},
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=self.exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=10.0,
            max_score=10.0,
            objective_score=10.0,
        )
        ResultItem.objects.create(
            result=result,
            question=self.question,
            answer="1",
            is_correct=True,
            score=10.0,
            max_score=10.0,
            source="manual",
        )
        submission = self._submission_for_exam(self.exam, self.question, answer="1")
        submission.source = Submission.Source.OMR_SCAN
        submission.save(update_fields=["source", "updated_at"])
        self.assertIsNone(
            ExamAttemptService.attach_matching_manual_objective_placeholder_for_submission(
                exam_id=self.exam.id,
                enrollment_id=self.enrollment.id,
                submission_id=submission.id,
                expected_items=[
                    {
                        "question_id": self.question.id,
                        "answer": "1",
                        "is_correct": True,
                        "score": 10.0,
                        "max_score": 10.0,
                    }
                ],
                expected_objective_score=10.0,
                expected_max_score=10.0,
            )
        )
        OMRStudentMatch.objects.create(
            tenant=self.tenant,
            submission=submission,
            enrollment=self.enrollment,
            status=OMRStudentMatch.Status.CONFIRMED,
            method=OMRStudentMatch.Method.MANUAL,
            identifier_status="matched",
            actor="test.manual_confirm",
            is_current=True,
        )

        synced = sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(synced.id, result.id)
        self.assertEqual(attempt.submission_id, submission.id)
        self.assertEqual(
            attempt.meta["initial_snapshot"]["source"],
            "omr_attached_matching_manual_result",
        )
        self.assertEqual(float(result.total_score), 10.0)
        self.assertEqual(float(result.objective_score), 10.0)
        self.assertEqual(ResultItem.objects.get(result=result).source, "online")
        self.assertTrue(
            ResultFact.objects.filter(
                target_id=self.exam.id,
                enrollment=self.enrollment,
                submission_id=submission.id,
                question_id=self.question.id,
                answer="1",
                score=10.0,
            ).exists()
        )

    def test_sync_does_not_attach_manual_entry_with_objective_item_score(self):
        exam, choice, _essay = self._create_mixed_exam()
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"source": "manual_entry"},
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=80.0,
            max_score=100.0,
            objective_score=80.0,
        )
        ResultItem.objects.create(
            result=result,
            question=choice,
            answer="1",
            is_correct=True,
            score=80.0,
            max_score=80.0,
            source="manual",
        )
        submission = self._submission_for_exam(exam, choice, answer="1")

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(attempt.submission_id, 0)
        self.assertEqual(float(result.total_score), 80.0)
        self.assertEqual(float(result.objective_score), 80.0)

    def test_sync_uses_omr_detected_answer_facts_when_submission_answers_missing(self):
        exam, choice, essay = self._create_mixed_exam()
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.ANSWERS_READY,
        )
        run = OMRRecognitionRun.objects.create(
            tenant=self.tenant,
            submission=submission,
            status="DONE",
            answer_count=1,
            answer_status_counts={"ok": 1},
            contract_snapshot={"choice_count": 1},
        )
        OMRDetectedAnswer.objects.create(
            tenant=self.tenant,
            submission=submission,
            recognition_run=run,
            question_number=1,
            exam_question_id=choice.id,
            answer="1",
            detected=["1"],
            status="ok",
            marking="single",
            confidence=0.99,
        )

        result = sync_result_from_exam_submission(submission.id)

        self.assertEqual(float(result.objective_score), 80.0)
        self.assertEqual(float(result.total_score), 80.0)
        self.assertEqual(float(result.max_score), 100.0)
        self.assertTrue(ResultItem.objects.filter(result=result, question=choice, source="online").exists())
        self.assertFalse(ResultItem.objects.filter(result=result, question=essay).exists())

    def test_sync_rejects_incomplete_omr_answers_without_mutating_existing_result(self):
        exam, choice, essay = self._create_mixed_exam()
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            submission_id=0,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"source": "manual_entry"},
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            attempt=attempt,
            total_score=15.0,
            max_score=100.0,
            objective_score=0.0,
        )
        ResultItem.objects.create(
            result=result,
            question=essay,
            answer="manual",
            is_correct=True,
            score=15.0,
            max_score=20.0,
            source="manual",
        )
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.ANSWERS_READY,
        )

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(submission.id)

        attempt.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(attempt.submission_id, 0)
        self.assertEqual(float(result.total_score), 15.0)
        self.assertEqual(float(result.objective_score), 0.0)
        self.assertFalse(ResultItem.objects.filter(result=result, question=choice).exists())

    def test_sync_treats_zero_score_mixed_sheet_essay_as_decorative_without_essay_evidence(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Zero Score Mixed Scope Guard Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
        )
        exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=exam,
            name="ZERO-MIXED",
            total_questions=2,
            choice_count=1,
            essay_count=1,
        )
        choice = ExamQuestion.objects.create(sheet=sheet, number=1, score=0)
        essay = ExamQuestion.objects.create(sheet=sheet, number=2, score=0)
        AnswerKey.objects.create(
            exam=exam,
            answers={str(choice.id): "1", str(essay.id): "해설참조"},
        )
        submission = self._submission_for_exam(exam, choice, answer="1")
        submission.source = Submission.Source.OMR_SCAN
        submission.save(update_fields=["source", "updated_at"])

        result = sync_result_from_exam_submission(submission.id)

        self.assertEqual(float(result.objective_score), 100.0)
        self.assertEqual(float(result.total_score), 100.0)
        self.assertEqual(float(result.max_score), 100.0)
        item = ResultItem.objects.get(result=result, question=choice)
        self.assertEqual(float(item.score), 100.0)
        self.assertEqual(float(item.max_score), 100.0)
        self.assertFalse(ResultItem.objects.filter(result=result, question=essay).exists())
        legacy = ExamResult.objects.get(submission=submission)
        self.assertEqual(float(legacy.total_score), 100.0)
        self.assertEqual(float(legacy.objective_score), 100.0)
        self.assertEqual(float(legacy.max_score), 100.0)
        self.assertEqual(list(legacy.breakdown.keys()), ["1"])

    def test_sync_repairs_stale_draft_exam_result_snapshot_from_result_payload(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Stale Legacy Snapshot Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
        )
        exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=exam,
            name="STALE-ZERO-MIXED",
            total_questions=2,
            choice_count=1,
            essay_count=1,
        )
        choice = ExamQuestion.objects.create(sheet=sheet, number=1, score=0)
        essay = ExamQuestion.objects.create(sheet=sheet, number=2, score=0)
        AnswerKey.objects.create(
            exam=exam,
            answers={str(choice.id): "1", str(essay.id): "해설참조"},
        )
        submission = self._submission_for_exam(exam, choice, answer="1")
        submission.source = Submission.Source.OMR_SCAN
        submission.save(update_fields=["source", "updated_at"])
        attempt = ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            submission_id=submission.id,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 80.0,
                    "max_score": 100.0,
                    "source": "submission_sync",
                    "submission_id": submission.id,
                }
            },
        )
        ExamResult.objects.create(
            submission=submission,
            exam=exam,
            total_score=80,
            max_score=100,
            objective_score=80,
            status=ExamResult.Status.DRAFT,
            breakdown={"1": {"question_id": choice.id, "earned": 80}},
        )

        result = sync_result_from_exam_submission(submission.id)

        legacy = ExamResult.objects.get(submission=submission)
        self.assertEqual(float(result.total_score), 100.0)
        self.assertEqual(float(result.objective_score), 100.0)
        self.assertEqual(float(legacy.total_score), 100.0)
        self.assertEqual(float(legacy.objective_score), 100.0)
        self.assertEqual(float(legacy.max_score), 100.0)
        self.assertEqual(list(legacy.breakdown.keys()), ["1"])
        self.assertNotIn(str(essay.number), legacy.breakdown)
        attempt.refresh_from_db()
        self.assertEqual(float(attempt.meta["initial_snapshot"]["total_score"]), 100.0)
        self.assertEqual(float(attempt.meta["initial_snapshot"]["max_score"]), 100.0)
        self.assertEqual(
            attempt.meta["initial_snapshot"]["repair_source"],
            "sync_result_from_exam_submission",
        )

    def test_auto_grade_treats_zero_score_mixed_sheet_essay_as_decorative_without_essay_evidence(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Zero Score Mixed Auto Grade Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
        )
        exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=exam,
            name="ZERO-MIXED-AUTO",
            total_questions=2,
            choice_count=1,
            essay_count=1,
        )
        choice = ExamQuestion.objects.create(sheet=sheet, number=1, score=0)
        essay = ExamQuestion.objects.create(sheet=sheet, number=2, score=0)
        AnswerKey.objects.create(
            exam=exam,
            answers={str(choice.id): "1", str(essay.id): "해설참조"},
        )
        submission = self._submission_for_exam(exam, choice, answer="1")
        submission.source = Submission.Source.OMR_SCAN
        submission.save(update_fields=["source", "updated_at"])

        exam_result = ExamGradingService().auto_grade_objective(submission_id=submission.id)

        self.assertEqual(float(exam_result.total_score), 100.0)
        self.assertEqual(float(exam_result.objective_score), 100.0)
        self.assertEqual(float(exam_result.max_score), 100.0)
        self.assertEqual(list(exam_result.breakdown.keys()), ["1"])
        self.assertNotIn(str(essay.number), exam_result.breakdown)

    def test_sync_honors_custom_choice_and_essay_question_scores(self):
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="Custom Weighted OMR Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=0,
            max_score=100,
        )
        exam.sessions.add(self.session)
        ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        sheet = Sheet.objects.create(
            exam=exam,
            name="CUSTOM-WEIGHTS",
            total_questions=23,
            choice_count=22,
            essay_count=1,
        )
        questions = []
        for number in range(1, 24):
            if number <= 18:
                score = 3
            elif number <= 22:
                score = 4
            else:
                score = 30
            questions.append(ExamQuestion.objects.create(sheet=sheet, number=number, score=score))
        AnswerKey.objects.create(
            exam=exam,
            answers={
                **{str(question.id): "1" for question in questions[:22]},
            },
        )
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.ANSWERS_READY,
        )
        for question in questions[:22]:
            SubmissionAnswer.objects.create(
                tenant=self.tenant,
                submission=submission,
                exam_question_id=question.id,
                answer="1",
            )

        result = sync_result_from_exam_submission(submission.id)

        self.assertEqual(float(result.objective_score), 70.0)
        self.assertEqual(float(result.total_score), 70.0)
        self.assertEqual(float(result.max_score), 100.0)
        self.assertEqual(ResultItem.objects.filter(result=result).count(), 22)
        self.assertEqual(
            sum(float(item.max_score) for item in ResultItem.objects.filter(result=result)),
            70.0,
        )
        self.assertFalse(ResultItem.objects.filter(result=result, question=questions[22]).exists())
        legacy = ExamResult.objects.get(submission=submission)
        self.assertEqual(float(legacy.total_score), 70.0)
        self.assertEqual(float(legacy.max_score), 70.0)
        self.assertEqual(list(legacy.breakdown.keys()), [str(n) for n in range(1, 23)])
        self.assertEqual(
            ResultFact.objects.filter(
                target_type="exam",
                target_id=exam.id,
                enrollment_id=self.enrollment.id,
                submission_id=submission.id,
            ).count(),
            22,
        )
        stats = QuestionStatsService.per_question_stats(exam_id=exam.id)
        self.assertEqual(len(stats), 22)
        self.assertEqual({row["attempts"] for row in stats}, {1})

    def test_sync_rejects_incomplete_omr_answers_before_creating_first_result(self):
        exam, choice, essay = self._create_mixed_exam()
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.admin,
            enrollment_id=self.enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.OMR_SCAN,
            status=Submission.Status.ANSWERS_READY,
        )

        with self.assertRaises(DjangoValidationError):
            sync_result_from_exam_submission(submission.id)

        self.assertFalse(
            Result.objects.filter(
                target_type="exam",
                target_id=exam.id,
                enrollment=self.enrollment,
            ).exists()
        )
        self.assertFalse(ExamAttempt.objects.filter(exam=exam, enrollment=self.enrollment).exists())
        self.assertFalse(ResultItem.objects.filter(question=choice).exists())
