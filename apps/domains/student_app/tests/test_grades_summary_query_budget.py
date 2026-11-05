"""Full student grades reads stay bounded as published OMR/ONLINE history grows."""
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.domains.student_app.tests import test_grades_summary_homework as fixtures
from apps.support.omr.score_shape import get_exam_score_shape
from apps.support.omr.score_shape_batch import ExamScoringDetails, load_exam_scoring_details
from apps.support.results.admin_exam_result_detail_dependencies import get_answer_key_answers


AnswerKey = apps.get_model("exams", "AnswerKey")
ResultFact = apps.get_model("results", "ResultFact")


def _single_exam_details(exams):
    details = {}
    for exam in exams:
        shape = get_exam_score_shape(exam)
        details[exam.id] = ExamScoringDetails(
            shape=shape, answers=get_answer_key_answers(template_exam_id=shape.template_exam_id),
        )
    return details


class GradesSummaryQueryBudgetTests(TestCase):
    setUp = fixtures.MyGradesSummaryHomeworkTests.setUp
    _call = fixtures.MyGradesSummaryHomeworkTests._call
    _score_exam = fixtures.MyGradesSummaryHomeworkTests._score_exam

    def _add_exam(self, order, source):
        result = self._score_exam(title=f"History {order}", order=order, score=80, max_score=100)
        exam = self.Exam.objects.get(pk=result.target_id)
        exam.student_results_published = True
        exam.save(update_fields=["student_results_published"])
        sheet = self.Sheet.objects.create(exam=exam, total_questions=5, choice_count=4, essay_count=1)
        questions = [self.ExamQuestion.objects.create(
            sheet=sheet, number=i, score=20, question_kind="choice" if i <= 4 else "essay",
        ) for i in range(1, 6)]
        AnswerKey.objects.create(exam=exam, answers={
            str(q.id): "1" if q.number <= 4 else "explanation" for q in questions
        })
        if source != "manual":
            submission = fixtures.Submission.objects.create(
                tenant=self.tenant, user=self.user, enrollment=self.enrollment,
                target_type="exam", target_id=exam.id, source=source, status="done",
            )
            result.attempt = self.ExamAttempt.objects.create(
                exam=exam, enrollment=self.enrollment, submission_id=submission.id,
                attempt_index=1, meta={"subjective_score": 20},
            )
            result.save(update_fields=["attempt"])
        for question in questions:
            self.ResultItem.objects.create(
                result=result, question=question, answer="1", is_correct=True,
                score=20, max_score=20, source="manual",
            )
        return exam, sheet, questions, result

    def _assert_history_budget(self, source):
        counts = []
        for order in range(1, 25):
            self._add_exam(order, source)
            if order not in (1, 12, 24):
                continue
            with CaptureQueriesContext(connection) as queries:
                response = self._call()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.data["exams"]), order)
            self.assertTrue(all(row["total_score"] == 80 for row in response.data["exams"]))
            counts.append(len(queries))
            with patch(
                "apps.domains.results.services.omr_subjective_completion.load_exam_scoring_details",
                side_effect=_single_exam_details,
            ):
                original = self._call()
            self.assertEqual(response.data, original.data)
        self.assertLessEqual(max(counts), 90, counts)
        self.assertLessEqual(max(counts) - min(counts), 1, counts)

    def test_omr_history_query_budget_and_response_parity(self):
        self._assert_history_budget("omr_scan")

    def test_online_history_query_budget_and_response_parity(self):
        self._assert_history_budget("online")

    def test_manual_history_query_budget_and_response_parity(self):
        self._assert_history_budget("manual")

    def test_batch_matches_legacy_template_inference_adjustment_and_missing_sheet(self):
        exams = []
        for order in range(1, 6):
            exam, sheet, questions, result = self._add_exam(order, "manual")
            exams.append(exam)
            if order == 1:
                # A legacy template owns the sheet and key; an owned snapshot
                # below must still take precedence when both are present.
                template = self.Exam.objects.create(
                    tenant=self.tenant, title="Legacy", exam_type="template", max_score=100,
                )
                sheet.exam = template
                sheet.save(update_fields=["exam"])
                AnswerKey.objects.filter(exam=exam).update(exam=template)
                exam.template_exam = template
                exam.save(update_fields=["template_exam"])
            elif order == 2:
                exam.template_exam = exams[0].template_exam
                exam.save(update_fields=["template_exam"])
                AnswerKey.objects.filter(exam=exam).update(answers={
                    **{str(q.id): "1" if q.number <= 4 else "explanation" for q in questions},
                    "__score_adjustment__": {"objective": 2, "subjective": 3},
                })
            elif order == 3:
                sheet.choice_count = sheet.essay_count = 0
                sheet.save(update_fields=["choice_count", "essay_count"])
                self.ExamQuestion.objects.filter(sheet=sheet).update(question_kind=None)
            elif order == 4:
                sheet.delete()
            elif order == 5:
                AnswerKey.objects.filter(exam=exam).delete()
        self.assertEqual(load_exam_scoring_details(exams), _single_exam_details(exams))

    def test_zero_weight_evidence_is_batched_and_keeps_exam_question_pairs(self):
        exams = []
        counts = []
        for order in range(1, 25):
            exam, sheet, questions, result = self._add_exam(order, "manual")
            exams.append(exam)
            self.ExamQuestion.objects.filter(sheet=sheet).update(score=0)
            AnswerKey.objects.filter(exam=exam).update(answers={})
            self.ResultItem.objects.filter(result=result).update(score=0, max_score=0)
            result.total_score = result.objective_score = 0
            result.save(update_fields=["total_score", "objective_score"])
            if order % 5 == 1:
                self.ResultItem.objects.filter(result=result, question=questions[-1]).update(max_score=20)
            elif order % 5 in (2, 3):
                ResultFact.objects.create(
                    target_type="exam", target_id=exam.id, enrollment=self.enrollment,
                    submission_id=0, question_id=questions[-1].id if order % 5 == 2 else 0,
                    source="manual" if order % 5 == 2 else "manual_subjective", max_score=20,
                )
            elif order % 5 == 4:
                result.total_score = 20
                result.save(update_fields=["total_score"])
            if order in (1, 12, 24):
                with CaptureQueriesContext(connection) as queries:
                    actual = load_exam_scoring_details(exams)
                counts.append(len(queries))
                self.assertEqual(actual, _single_exam_details(exams))
                for index, details in enumerate(actual.values(), 1):
                    self.assertEqual(details.shape.subjective_max_score, 20 if index % 5 else 0)
        self.assertLessEqual(max(counts), 9, counts)
        self.assertLessEqual(max(counts) - min(counts), 1, counts)

    def test_foreign_question_evidence_does_not_complete_another_exam(self):
        exams = []
        for order in (1, 2):
            exam, sheet, questions, result = self._add_exam(order, "manual")
            exams.append(exam)
            self.ExamQuestion.objects.filter(sheet=sheet).update(score=0)
            AnswerKey.objects.filter(exam=exam).update(answers={})
            self.ResultItem.objects.filter(result=result).update(score=0, max_score=0)
            result.total_score = result.objective_score = 0
            result.save(update_fields=["total_score", "objective_score"])
        ResultFact.objects.create(
            target_type="exam", target_id=exams[0].id, enrollment=self.enrollment,
            submission_id=0, question_id=questions[-1].id, source="manual", max_score=20,
        )
        actual = load_exam_scoring_details(exams)
        self.assertEqual(actual, _single_exam_details(exams))
        self.assertTrue(all(details.shape.subjective_max_score == 0 for details in actual.values()))
