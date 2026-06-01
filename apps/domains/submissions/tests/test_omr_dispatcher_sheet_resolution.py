from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.ai.models import AIJobModel
from apps.domains.assets.omr.services.omr_document_service import OMRDocumentService
from apps.domains.exams.models import AnswerKey, Exam, ExamQuestion, Sheet
from apps.domains.submissions.models import Submission
from apps.domains.submissions.services import dispatcher
from apps.support.omr.sheet_shape import resolve_omr_sheet_shape


User = get_user_model()


class OMRDispatcherSheetResolutionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="omr-audit", name="OMR Audit")
        self.other_tenant = Tenant.objects.create(code="omr-other", name="OMR Other")
        self.user = User.objects.create_user(
            username="omr-audit-user",
            password="pass1234!",
            tenant=self.tenant,
        )

        self.template_exam = Exam.objects.create(
            tenant=self.tenant,
            title="Template Exam",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        self.regular_exam = Exam.objects.create(
            tenant=self.tenant,
            title="Regular Exam",
            exam_type=Exam.ExamType.REGULAR,
            template_exam=self.template_exam,
        )
        self.sheet = Sheet.objects.create(
            exam=self.template_exam,
            name="MAIN",
            total_questions=7,
        )
        ExamQuestion.objects.create(
            sheet=self.sheet,
            number=1,
            score=1,
            region_meta={"x": 1, "y": 2, "w": 3, "h": 4},
        )
        ExamQuestion.objects.create(
            sheet=self.sheet,
            number=2,
            score=1,
            region_meta={"x": 5, "y": 6, "w": 7, "h": 8},
        )

        self.other_exam = Exam.objects.create(
            tenant=self.other_tenant,
            title="Other Template",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        self.other_sheet = Sheet.objects.create(
            exam=self.other_exam,
            name="MAIN",
            total_questions=3,
        )

    def _reset_sheet_shape(
        self,
        *,
        total_questions: int,
        choice_count: int = 0,
        essay_count: int = 0,
        score: float = 1.0,
    ) -> list[ExamQuestion]:
        self.sheet.total_questions = total_questions
        self.sheet.choice_count = choice_count
        self.sheet.essay_count = essay_count
        self.sheet.save(update_fields=[
            "total_questions",
            "choice_count",
            "essay_count",
            "updated_at",
        ])
        ExamQuestion.objects.filter(sheet=self.sheet).delete()
        return [
            ExamQuestion.objects.create(sheet=self.sheet, number=number, score=score)
            for number in range(1, total_questions + 1)
        ]

    def _submission(self) -> Submission:
        return Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type=Submission.TargetType.EXAM,
            target_id=self.regular_exam.id,
            source=Submission.Source.OMR_SCAN,
            payload={},
        )

    def test_regular_exam_resolves_template_sheet(self):
        sheet = dispatcher.resolve_omr_sheet_for_exam(
            tenant=self.tenant,
            exam_id=self.regular_exam.id,
            requested_sheet_id=None,
        )

        self.assertEqual(sheet.id, self.sheet.id)

    def test_requested_sheet_must_belong_to_exam_and_tenant(self):
        with self.assertRaisesMessage(ValueError, "sheet_id does not belong"):
            dispatcher.resolve_omr_sheet_for_exam(
                tenant=self.tenant,
                exam_id=self.regular_exam.id,
                requested_sheet_id=self.other_sheet.id,
            )

    def test_ai_payload_uses_resolved_sheet_and_question_count(self):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type=Submission.TargetType.EXAM,
            target_id=self.regular_exam.id,
            source=Submission.Source.OMR_SCAN,
            payload={},
        )

        payload = dispatcher._build_ai_payload(submission)

        self.assertEqual(payload["omr"]["sheet_id"], self.sheet.id)
        self.assertEqual(payload["question_count"], 7)
        self.assertEqual([q["number"] for q in payload["questions"]], [1, 2])

    def test_omr_document_defaults_use_template_sheet_for_regular_exam(self):
        doc = OMRDocumentService.from_exam(
            exam=self.regular_exam,
            tenant=self.tenant,
        )

        self.assertEqual(doc.mc_count, 7)

    def test_ai_payload_uses_choice_count_not_total_questions(self):
        self._reset_sheet_shape(total_questions=25, choice_count=20, essay_count=5)
        submission = self._submission()

        payload = dispatcher._build_ai_payload(submission)

        self.assertEqual(payload["question_count"], 20)
        self.assertEqual(payload["mc_count"], 20)
        self.assertEqual(payload["essay_count"], 5)
        self.assertEqual(payload["total_question_count"], 25)
        self.assertEqual(payload["template_meta"]["mc_count"], 20)
        self.assertEqual(payload["template_meta"]["essay_count"], 5)
        self.assertEqual(payload["template_meta"]["layout"]["n_cols"], 1)

    def test_custom_sheet_type_matrix_resolves_payload_and_document_defaults(self):
        cases = [
            {
                "name": "objective_only",
                "total": 12,
                "choice": 12,
                "essay": 0,
                "expected_cols": 1,
            },
            {
                "name": "mixed_objective_essay",
                "total": 25,
                "choice": 20,
                "essay": 5,
                "expected_cols": 1,
            },
            {
                "name": "essay_only",
                "total": 5,
                "choice": 0,
                "essay": 5,
                "expected_cols": 0,
            },
            {
                "name": "large_objective_custom",
                "total": 45,
                "choice": 45,
                "essay": 0,
                "expected_cols": 3,
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                self._reset_sheet_shape(
                    total_questions=case["total"],
                    choice_count=case["choice"],
                    essay_count=case["essay"],
                )
                submission = self._submission()

                payload = dispatcher._build_ai_payload(submission)
                doc = OMRDocumentService.from_exam(
                    exam=self.regular_exam,
                    tenant=self.tenant,
                )

                self.assertEqual(payload["question_count"], case["choice"])
                self.assertEqual(payload["mc_count"], case["choice"])
                self.assertEqual(payload["essay_count"], case["essay"])
                self.assertEqual(payload["total_question_count"], case["total"])
                self.assertEqual(payload["omr"]["shape_source"], "sheet")
                self.assertEqual(payload["template_meta"]["mc_count"], case["choice"])
                self.assertEqual(payload["template_meta"]["essay_count"], case["essay"])
                self.assertEqual(
                    payload["template_meta"]["layout"]["n_cols"],
                    case["expected_cols"],
                )
                self.assertEqual(doc.mc_count, case["choice"])
                self.assertEqual(doc.essay_count, case["essay"])

    def test_legacy_sheet_shape_infers_choice_count_from_answer_key(self):
        questions = self._reset_sheet_shape(total_questions=25)
        AnswerKey.objects.create(
            exam=self.template_exam,
            answers={
                str(q.id): ("1" if q.number <= 20 else "서술형")
                for q in questions
            },
        )
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type=Submission.TargetType.EXAM,
            target_id=self.regular_exam.id,
            source=Submission.Source.OMR_SCAN,
            payload={},
        )

        payload = dispatcher._build_ai_payload(submission)
        doc = OMRDocumentService.from_exam(
            exam=self.regular_exam,
            tenant=self.tenant,
        )

        self.assertEqual(payload["question_count"], 20)
        self.assertEqual(payload["omr"]["shape_source"], "answer_key")
        self.assertEqual(doc.mc_count, 20)
        self.assertEqual(doc.essay_count, 5)

    def test_legacy_sheet_shape_infers_choice_count_with_choice_answer_variants(self):
        questions = self._reset_sheet_shape(total_questions=5)
        AnswerKey.objects.create(
            exam=self.template_exam,
            answers={
                str(questions[0].id): "①",
                str(questions[1].id): "2,3",
                str(questions[2].id): "2|4",
                str(questions[3].id): "서술형",
                str(questions[4].id): "풀이",
            },
        )
        submission = self._submission()

        shape = resolve_omr_sheet_shape(sheet=self.sheet, exam=self.regular_exam)
        payload = dispatcher._build_ai_payload(submission)
        doc = OMRDocumentService.from_exam(
            exam=self.regular_exam,
            tenant=self.tenant,
        )

        self.assertEqual(shape.source, "answer_key")
        self.assertEqual(shape.choice_count, 3)
        self.assertEqual(shape.essay_count, 2)
        self.assertEqual(payload["question_count"], 3)
        self.assertEqual(payload["essay_count"], 2)
        self.assertEqual(doc.mc_count, 3)
        self.assertEqual(doc.essay_count, 2)

    def test_legacy_sheet_shape_supports_essay_only_from_answer_key(self):
        questions = self._reset_sheet_shape(total_questions=3)
        AnswerKey.objects.create(
            exam=self.template_exam,
            answers={
                str(questions[0].id): "서술형 1",
                str(questions[1].id): "서술형 2",
                str(questions[2].id): "서술형 3",
            },
        )
        submission = self._submission()

        shape = resolve_omr_sheet_shape(sheet=self.sheet, exam=self.regular_exam)
        payload = dispatcher._build_ai_payload(submission)
        doc = OMRDocumentService.from_exam(
            exam=self.regular_exam,
            tenant=self.tenant,
        )

        self.assertEqual(shape.source, "answer_key")
        self.assertEqual(shape.choice_count, 0)
        self.assertEqual(shape.essay_count, 3)
        self.assertEqual(payload["question_count"], 0)
        self.assertEqual(payload["mc_count"], 0)
        self.assertEqual(payload["essay_count"], 3)
        self.assertEqual(payload["template_meta"]["mc_count"], 0)
        self.assertEqual(payload["template_meta"]["layout"]["n_cols"], 0)
        self.assertEqual(doc.mc_count, 0)
        self.assertEqual(doc.essay_count, 3)

    def test_omr_document_rejects_cross_tenant_template_sheet(self):
        self.regular_exam.template_exam = self.other_exam
        self.regular_exam.save(update_fields=["template_exam"])

        with self.assertRaisesMessage(ValueError, "template exam belongs to another tenant"):
            OMRDocumentService.from_exam(
                exam=self.regular_exam,
                tenant=self.tenant,
            )

    def test_dispatch_failure_marks_submission_failed(self):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type=Submission.TargetType.EXAM,
            target_id=self.regular_exam.id,
            source=Submission.Source.OMR_SCAN,
            file_key="submissions/omr/dispatch-rejected.png",
            payload={},
        )

        with (
            patch.object(
                dispatcher,
                "dispatch_job",
                return_value={
                    "ok": False,
                    "job_id": None,
                    "type": "omr_grading",
                    "error": "Validation failed",
                    "rejection_code": "basic_photo_not_allowed",
                },
            ) as dispatch_job,
            patch.object(dispatcher, "start_ai_worker_instance") as start_worker,
        ):
            dispatcher.dispatch_submission(submission)

        dispatch_job.assert_called_once()
        start_worker.assert_not_called()
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.FAILED)
        self.assertEqual(submission.error_message, "Validation failed")
        self.assertEqual(
            (submission.meta or {}).get("ai_dispatch", {}).get("rejection_code"),
            "basic_photo_not_allowed",
        )

    def test_publish_failure_marks_submission_failed_after_commit(self):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type=Submission.TargetType.EXAM,
            target_id=self.regular_exam.id,
            source=Submission.Source.OMR_SCAN,
            file_key="submissions/omr/publish-failed.png",
            payload={},
        )

        with (
            patch("apps.domains.ai.gateway.publish_job", side_effect=RuntimeError("SQS down")),
            patch.object(dispatcher, "start_ai_worker_instance"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            dispatcher.dispatch_submission(submission)

        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.FAILED)
        self.assertIn("SQS down", submission.error_message)
        self.assertEqual(
            (submission.meta or {}).get("ai_dispatch", {}).get("rejection_code"),
            "publish_failed",
        )
        self.assertEqual(
            AIJobModel.objects.get(source_domain="submissions", source_id=str(submission.id)).status,
            "FAILED",
        )

    def test_dispatch_fails_closed_for_foreign_sheet_id(self):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_type=Submission.TargetType.EXAM,
            target_id=self.regular_exam.id,
            source=Submission.Source.OMR_SCAN,
            file_key="submissions/omr/foreign-sheet.png",
            payload={"sheet_id": self.other_sheet.id},
        )

        with patch.object(dispatcher, "dispatch_job") as dispatch_job:
            dispatcher.dispatch_submission(submission)

        dispatch_job.assert_not_called()
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.FAILED)
        self.assertIn("sheet_id does not belong", submission.error_message)
