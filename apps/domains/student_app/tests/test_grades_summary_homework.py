from datetime import date, timedelta
import hashlib
import json
import uuid

from django.contrib.auth import get_user_model
from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.services.student_grade_report_layout import (
    STUDENT_GRADE_REPORT_LAYOUT_KEY,
)
from apps.domains.enrollment.models import Enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.lectures.models import Lecture, Session
from apps.domains.student_app.results.views import MyExamResultView, MyGradesSummaryView
from apps.domains.students.models import Student
from apps.support.submissions.dependencies import homework_submission_revisions


User = get_user_model()
Submission = django_apps.get_model("submissions", "Submission")
SubmissionMedia = django_apps.get_model("submissions", "SubmissionMedia")


def _exam_correction_fingerprint(result) -> str:
    """Mirror the persisted result-content contract without a cross-domain import."""
    payload = {
        "result_id": int(result.id),
        "attempt_id": int(result.attempt_id) if result.attempt_id else None,
        "total_score": float(result.total_score) if result.total_score is not None else None,
        "max_score": float(result.max_score) if result.max_score is not None else None,
        "objective_score": float(result.objective_score) if result.objective_score is not None else None,
        "items": [
            {
                "question_id": int(item.question_id),
                "answer": str(item.answer or ""),
                "is_correct": bool(item.is_correct),
                "include_in_wrong_note": bool(item.include_in_wrong_note),
                "score": float(item.score) if item.score is not None else None,
                "max_score": float(item.max_score) if item.max_score is not None else None,
            }
            for item in sorted(
                result.items.all(),
                key=lambda candidate: (candidate.question_id, candidate.id),
            )
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class MyGradesSummaryHomeworkTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="student-grades-hw", name="Student Grades HW", is_active=True)
        self.user = User.objects.create_user(
            username="student-grades-hw-user",
            password="pw1234",
            tenant=self.tenant,
            name="학생",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.user,
            ps_number="SGH001",
            omr_code="11112222",
            name="학생",
            phone="01011112222",
            parent_phone="01033334444",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="수학",
            name="수학",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.Exam = django_apps.get_model("exams", "Exam")
        self.ExamEnrollment = django_apps.get_model("exams", "ExamEnrollment")
        self.ExamQuestion = django_apps.get_model("exams", "ExamQuestion")
        self.Sheet = django_apps.get_model("exams", "Sheet")
        self.ExamAttempt = django_apps.get_model("results", "ExamAttempt")
        self.Result = django_apps.get_model("results", "Result")
        self.ResultItem = django_apps.get_model("results", "ResultItem")
        self.AssessmentCorrection = django_apps.get_model("progress", "AssessmentCorrection")

    def _call(self):
        request = self.factory.get("/api/v1/student/grades/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return MyGradesSummaryView.as_view()(request)

    def _call_exam_result(self, exam_id: int):
        request = self.factory.get(f"/api/v1/student/results/me/exams/{exam_id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return MyExamResultView.as_view()(request, exam_id=exam_id)

    def _score_exam(
        self,
        *,
        title: str,
        order: int,
        score: float,
        max_score: float,
    ):
        session_order = order + 1
        session = Session.objects.create(
            lecture=self.lecture,
            order=session_order,
            regular_order=session_order,
            date=date(2026, 7, 1) + timedelta(days=order),
            title=f"{order}회",
        )
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title=title,
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=max_score,
            pass_score=max_score * 0.6,
        )
        exam.sessions.add(session)
        return self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=score,
            max_score=max_score,
        )

    def test_shared_exam_history_uses_enrollment_lecture_pass_score(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="공유 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
            pass_score=60,
            grading_mode=self.Exam.GradingMode.MIXED,
            choice_question_count=18,
            essay_numbering=self.Exam.EssayNumbering.SEPARATE,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        django_apps.get_model("exams", "ExamLecturePolicy").objects.create(
            exam=exam,
            lecture=self.lecture,
            pass_score=70,
        )
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=65,
            max_score=100,
        )

        detail = self._call_exam_result(exam.id)
        summary = self._call()

        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertFalse(detail.data["is_pass"])
        row = next(item for item in summary.data["exams"] if item["exam_id"] == exam.id)
        self.assertFalse(row["is_pass"])
        self.assertEqual(row["achievement"], "FAIL")
        self.assertEqual(row["essay_numbering"], "separate")
        self.assertEqual(row["choice_question_count"], 18)

    def test_student_grades_excludes_result_for_explicit_non_target_exam(self):
        target_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="지정 시험",
            is_active=True,
            max_score=100,
            pass_score=70,
        )
        non_target_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="순환",
            is_active=True,
            max_score=100,
            pass_score=70,
        )
        target_exam.sessions.add(self.session)
        non_target_exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(
            exam=target_exam,
            enrollment=self.enrollment,
        )

        other_user = User.objects.create_user(
            username="student-grades-other-target",
            password="pw1234",
            tenant=self.tenant,
        )
        other_student = Student.objects.create(
            tenant=self.tenant,
            user=other_user,
            ps_number="SGH002",
            omr_code="22223333",
            name="다른 학생",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=other_student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.ExamEnrollment.objects.create(
            exam=non_target_exam,
            enrollment=other_enrollment,
        )
        self.Result.objects.create(
            target_type="exam",
            target_id=target_exam.id,
            enrollment=self.enrollment,
            total_score=85,
            max_score=100,
        )
        # Preserve a historical bad row to prove read paths no longer consume it.
        self.Result.objects.create(
            target_type="exam",
            target_id=non_target_exam.id,
            enrollment=self.enrollment,
            total_score=0,
            max_score=100,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [(row["exam_id"], row["title"]) for row in response.data["exams"]],
            [(target_exam.id, "지정 시험")],
        )
        self.assertEqual(response.data["exam_summary"]["scored_count"], 1)

    def test_exam_trend_accumulates_rounds_and_normalizes_different_max_scores(self):
        self._score_exam(title="월간 테스트", order=1, score=16, max_score=20)
        self._score_exam(title="중간 테스트", order=2, score=45, max_score=50)
        self._score_exam(title="기말 테스트", order=3, score=0, max_score=100)

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [(row["round_index"], row["title"], row["score_pct"]) for row in response.data["exam_trend"]],
            [(1, "월간 테스트", 80.0), (2, "중간 테스트", 90.0), (3, "기말 테스트", 0.0)],
        )
        self.assertEqual(response.data["exam_summary"], {
            "scored_count": 3,
            "average_score_pct": 56.7,
            "latest_score_pct": 0.0,
            "change_pct_points": -90.0,
            "best_score_pct": 90.0,
        })
        self.assertTrue(all("recorded_at" in row for row in response.data["exam_trend"]))

    def test_retake_score_stays_supplemental_to_initial_student_report_score(self):
        result = self._score_exam(
            title="재시험 분리 시험",
            order=1,
            score=100,
            max_score=100,
        )
        exam = self.Exam.objects.get(id=result.target_id)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            attempt_index=1,
            is_representative=False,
            status="done",
            meta={
                "initial_snapshot": {
                    "total_score": 25.0,
                    "max_score": 100.0,
                    "source": "test",
                },
                "total_score": 25.0,
                "max_score": 100.0,
            },
        )
        retake = self.ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            attempt_index=2,
            is_retake=True,
            is_representative=True,
            status="done",
            meta={"total_score": 100.0, "max_score": 100.0},
        )
        result.attempt = retake
        result.save(update_fields=["attempt", "updated_at"])

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"][0]["total_score"], 25.0)
        self.assertEqual(response.data["exams"][0]["is_pass"], False)
        self.assertEqual(response.data["exams"][0]["retake_count"], 2)
        self.assertEqual(response.data["exam_trend"][0]["score"], 25.0)
        self.assertEqual(response.data["exam_summary"]["average_score_pct"], 25.0)

    def test_student_summary_lists_active_lectures_before_they_have_scores(self):
        self.lecture.color = "#2563eb"
        self.lecture.chip_label = "수"
        self.lecture.save(update_fields=["color", "chip_label", "updated_at"])
        second_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="심화 수학",
            name="심화 수학",
            subject="MATH",
            color="#7c3aed",
            chip_label="심",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=second_lecture,
            status="ACTIVE",
        )
        inactive_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="지난 강좌",
            name="지난 강좌",
            subject="MATH",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=inactive_lecture,
            status="INACTIVE",
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["lecture_options"], [
            {"id": self.lecture.id, "title": "수학", "color": "#2563eb", "chip_label": "수", "is_active": True},
            {"id": second_lecture.id, "title": "심화 수학", "color": "#7c3aed", "chip_label": "심", "is_active": True},
        ])

    def test_ended_lecture_keeps_readonly_exam_history_with_inactive_marker(self):
        result = self._score_exam(
            title="종료 강의 시험",
            order=1,
            score=80,
            max_score=100,
        )
        exam = self.Exam.objects.get(id=result.target_id)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.lecture.is_active = False
        self.lecture.save(update_fields=["is_active", "updated_at"])

        response = self._call()
        result_response = self._call_exam_result(exam.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["exams"]), 1)
        self.assertFalse(response.data["exams"][0]["lecture_active"])
        self.assertFalse(response.data["lecture_options"][0]["is_active"])
        self.assertEqual(result_response.status_code, 200, result_response.data)
        self.assertFalse(result_response.data["can_retake"])

    def test_current_learning_todos_respect_course_dates_without_erasing_history(self):
        today = timezone.localdate()
        self._score_exam(title="방학특강 미통과 시험", order=1, score=20, max_score=100)
        homework = Homework.objects.create(
            tenant=self.tenant, session=self.session, title="방학특강 미제출 과제",
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant, session=self.session,
            homework=homework, enrollment=self.enrollment,
        )
        for start, end, ongoing, actionable in [
            (None, today - timedelta(days=1), False, False),
            (None, today, True, True),
            (today + timedelta(days=1), None, True, False),
            (today, today, True, True),
            (None, None, True, True),
        ]:
            with self.subTest(start=start, end=end):
                self.lecture.start_date = start
                self.lecture.end_date = end
                self.lecture.save(update_fields=["start_date", "end_date", "updated_at"])
                response = self._call()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.data["exams"]), 1)
                self.assertEqual(len(response.data["homeworks"]), 1)
                for row in response.data["exams"] + response.data["homeworks"]:
                    self.assertEqual(row["lecture_active"], ongoing)
                    self.assertEqual(row["learning_todo_eligible"], actionable)
                self.assertEqual(response.data["lecture_options"][0]["is_active"], ongoing)

    def test_current_learning_todos_keep_passed_homework_reviewed(self):
        homework = Homework.objects.create(
            tenant=self.tenant, session=self.session, title="이미 통과한 과제",
        )
        HomeworkScore.objects.create(
            homework=homework, session=self.session, enrollment=self.enrollment,
            score=100, max_score=100, passed=True,
        )
        response = self._call()
        row = response.data["homeworks"][0]
        self.assertTrue(row["passed"])
        self.assertEqual(row["submission_state"], "reviewed")

    def test_current_learning_todos_exclude_absence_but_keep_scored_history(self):
        result = self._score_exam(title="결석 차시 시험", order=1, score=20, max_score=100)
        exam = self.Exam.objects.get(pk=result.target_id)
        session = exam.sessions.get()
        django_apps.get_model("attendance", "Attendance").objects.create(
            tenant=self.tenant, session=session, enrollment=self.enrollment, status="ABSENT",
        )
        response = self._call()
        self.assertEqual(len(response.data["exams"]), 1)
        self.assertFalse(response.data["exams"][0]["learning_todo_eligible"])

    def test_student_summary_returns_tenant_layout_and_current_teacher_correction_status(self):
        program = self.tenant.program
        program.ui_config = {
            STUDENT_GRADE_REPORT_LAYOUT_KEY: {
                "version": 1,
                "sections": [
                    {"id": "score_comparison", "visible": True},
                    {"id": "score_trend", "visible": False},
                ],
            },
        }
        program.save(update_fields=["ui_config"])
        result = self._score_exam(
            title="오답 확인 시험",
            order=1,
            score=70,
            max_score=100,
        )
        exam = self.Exam.objects.get(id=result.target_id)
        session = exam.sessions.get()
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.AssessmentCorrection.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=session,
            source_type="exam",
            source_id=exam.id,
            completed=True,
            source_fingerprint=_exam_correction_fingerprint(result),
        )

        completed = self._call()
        completed_detail = self._call_exam_result(exam.id)

        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(completed.data["exams"][0]["correction_status"], "COMPLETED")
        self.assertEqual(completed_detail.status_code, 200, completed_detail.data)
        self.assertEqual(completed_detail.data["correction_status"], "COMPLETED")
        self.assertEqual(completed.data["report_layout"]["sections"][0], {
            "id": "score_comparison",
            "visible": True,
        })
        result.total_score = 60
        result.save(update_fields=["total_score", "updated_at"])

        stale = self._call()
        stale_detail = self._call_exam_result(exam.id)

        self.assertEqual(stale.data["exams"][0]["correction_status"], "PENDING")
        self.assertEqual(stale_detail.data["correction_status"], "PENDING")

    def test_foreign_and_template_exam_metadata_is_fail_closed(self):
        other_tenant = Tenant.objects.create(code="student-grades-foreign", name="Foreign", is_active=True)
        foreign_exam = self.Exam.objects.create(
            tenant=other_tenant,
            title="다른 학원 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        template_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="시험지 원본",
            exam_type=self.Exam.ExamType.TEMPLATE,
            is_active=True,
            max_score=100,
        )
        for exam in (foreign_exam, template_exam):
            self.Result.objects.create(
                target_type="exam",
                target_id=exam.id,
                enrollment=self.enrollment,
                total_score=100,
                max_score=100,
            )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["exam_summary"]["scored_count"], 0)
        self.assertEqual(response.data["lecture_options"], [{
            "id": self.lecture.id,
            "title": "수학",
            "color": "#3b82f6",
            "chip_label": "",
            "is_active": True,
        }])

    def test_not_submitted_exam_stays_in_list_but_not_in_trend(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="미응시 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
            pass_score=60,
        )
        exam.sessions.add(self.session)
        attempt = self.ExamAttempt.objects.create(
            exam=exam,
            enrollment=self.enrollment,
            attempt_index=1,
            is_representative=True,
            status="done",
            meta={"status": "NOT_SUBMITTED"},
        )
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=0,
            max_score=100,
            attempt=attempt,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"][0]["achievement"], "NOT_SUBMITTED")
        self.assertIsNone(response.data["exams"][0]["total_score"])
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["exam_summary"]["scored_count"], 0)

    def test_reexam_submission_pending_is_separate_from_prior_failed_grade(self):
        result = self._score_exam(
            title="재제출 시험",
            order=1,
            score=40,
            max_score=100,
        )
        exam = self.Exam.objects.get(id=result.target_id)
        before = self._call().data["exams"][0]
        self.assertEqual(before["achievement"], "FAIL")
        self.assertFalse(before["submission_pending"])
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.SUBMITTED,
        )
        during = self._call().data["exams"][0]
        self.assertEqual(during["achievement"], "FAIL")
        self.assertTrue(during["submission_pending"])
        submission.status = Submission.Status.FAILED
        submission.save(update_fields=["status"])
        self.assertFalse(self._call().data["exams"][0]["submission_pending"])

    def test_duplicate_results_collapse_to_latest_exam_point(self):
        first = self._score_exam(
            title="중복 결과 시험",
            order=1,
            score=40,
            max_score=100,
        )
        other_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="중복 연결 강의",
            name="중복 연결 강의",
            subject="MATH",
        )
        other_session = Session.objects.create(
            lecture=other_lecture,
            order=1,
            title="중복 연결 1회",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=other_lecture,
            status="ACTIVE",
        )
        exam = self.Exam.objects.get(id=first.target_id)
        exam.sessions.add(other_session)
        self.Result.objects.create(
            target_type="exam",
            target_id=first.target_id,
            enrollment=other_enrollment,
            total_score=85,
            max_score=100,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["exams"]), 1)
        self.assertEqual(len(response.data["exam_trend"]), 1)
        self.assertEqual(response.data["exam_trend"][0]["score_pct"], 85.0)

    def test_rank_cohort_excludes_corrupt_foreign_tenant_result(self):
        local_result = self._score_exam(
            title="테넌트 석차 테스트",
            order=1,
            score=80,
            max_score=100,
        )
        other_tenant = Tenant.objects.create(code="student-rank-foreign", name="Foreign", is_active=True)
        other_user = User.objects.create_user(
            username="student-rank-foreign-user",
            password="pw1234",
            tenant=other_tenant,
        )
        TenantMembership.ensure_active(tenant=other_tenant, user=other_user, role="student")
        other_student = Student.objects.create(
            tenant=other_tenant,
            user=other_user,
            ps_number="SRF001",
            omr_code="99998888",
            name="다른 학원 학생",
            phone="01099998888",
            parent_phone="01077776666",
        )
        other_lecture = Lecture.objects.create(
            tenant=other_tenant,
            title="다른 학원 수학",
            name="다른 학원 수학",
            subject="MATH",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=other_tenant,
            student=other_student,
            lecture=other_lecture,
            status="ACTIVE",
        )
        self.Result.objects.create(
            target_type="exam",
            target_id=local_result.target_id,
            enrollment=other_enrollment,
            total_score=100,
            max_score=100,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"][0]["rank"], 1)
        self.assertEqual(response.data["exams"][0]["cohort_size"], 1)
        self.assertEqual(response.data["exams"][0]["cohort_avg"], 80.0)
        self.assertEqual(response.data["exam_trend"][0]["rank"], 1)
        self.assertEqual(response.data["exam_trend"][0]["percentile"], 100.0)
        self.assertEqual(response.data["exam_trend"][0]["cohort_size"], 1)

    def test_rank_cohort_excludes_nonfinite_peer_and_response_renders(self):
        local_result = self._score_exam(
            title="석차 수치 안전 테스트",
            order=1,
            score=80,
            max_score=100,
        )
        peer_user = User.objects.create_user(
            username="student-rank-nonfinite-peer",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=peer_user,
            role="student",
        )
        peer_student = Student.objects.create(
            tenant=self.tenant,
            user=peer_user,
            ps_number="SRN001",
            omr_code="77776666",
            name="같은 학원 학생",
            phone="01077776666",
            parent_phone="01055554444",
        )
        peer_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=peer_student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.Result.objects.create(
            target_type="exam",
            target_id=local_result.target_id,
            enrollment=peer_enrollment,
            total_score=float("inf"),
            max_score=100,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["exams"][0]["cohort_size"], 1)
        self.assertEqual(response.data["exams"][0]["cohort_avg"], 80.0)
        response.render()
        self.assertNotIn(b"Infinity", response.content)

    def test_assigned_unscored_homework_is_visible_as_not_submitted(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="미채점 과제",
            meta={"default_max_score": 20},
        )
        assignment = HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["homeworks"]), 1)
        row = response.data["homeworks"][0]
        self.assertEqual(row["homework_id"], homework.id)
        self.assertIsNone(row["score"])
        self.assertEqual(row["max_score"], 20.0)
        self.assertEqual(row["achievement"], "NOT_SUBMITTED")
        self.assertFalse(row["submission_media_locked"])
        self.assertEqual(row["lecture_title"], "수학")
        self.assertEqual(row["recorded_at"], assignment.created_at.isoformat())

    def test_teacher_completed_unscored_homework_preserves_submission_state_for_student_and_parent(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="현장 협의 완료 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        correction = self.AssessmentCorrection.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.session,
            source_type=self.AssessmentCorrection.SourceType.HOMEWORK,
            source_id=homework.id,
            completed=True,
            completed_at=timezone.now(),
            note="수업 중 풀이 확인",
            updated_by=self.user,
        )
        ClinicLink = django_apps.get_model("progress", "ClinicLink")
        ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.session,
            source_type="homework",
            source_id=homework.id,
            reason="AUTO_FAILED",
            is_auto=True,
            approved=True,
            cycle_no=1,
            resolved_at=timezone.now(),
            resolution_type="MANUAL_OVERRIDE",
            resolution_evidence={
                "assessment_correction_id": correction.id,
                "memo": correction.note,
            },
            memo=correction.note,
        )

        student_response = self._call()
        student_row = student_response.data["homeworks"][0]

        self.assertEqual(student_response.status_code, 200)
        self.assertIsNone(student_row["score"])
        self.assertFalse(student_row["passed"])
        self.assertEqual(student_row["achievement"], "REMEDIATED")
        self.assertTrue(student_row["teacher_resolved"])
        self.assertTrue(student_row["submission_media_locked"])
        self.assertNotIn("correction_note", student_row)

        parent_user = User.objects.create_user(
            username="student-grades-hw-parent",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=parent_user,
            role="parent",
        )
        Parent = django_apps.get_model("parents", "Parent")
        parent = Parent.objects.create(
            tenant=self.tenant,
            user=parent_user,
            name="학부모",
            phone="01099998888",
        )
        self.student.parent = parent
        self.student.save(update_fields=["parent", "updated_at"])
        parent_request = self.factory.get(
            "/api/v1/student/grades/",
            HTTP_X_STUDENT_ID=str(self.student.id),
        )
        parent_request.tenant = self.tenant
        force_authenticate(parent_request, user=parent_user)
        parent_response = MyGradesSummaryView.as_view()(parent_request)

        self.assertEqual(parent_response.status_code, 200)
        self.assertTrue(parent_response.data["homeworks"][0]["teacher_resolved"])
        self.assertTrue(parent_response.data["homeworks"][0]["submission_media_locked"])
        self.assertIsNone(parent_response.data["homeworks"][0]["score"])
        self.assertNotIn("correction_note", parent_response.data["homeworks"][0])

    def _homework_cycle_fixture(self):
        self.ClinicLink = django_apps.get_model("progress", "ClinicLink")
        homework = Homework.objects.create(
            tenant=self.tenant, session=self.session, title="기준 변경 과제",
            meta={"default_max_score": 100}, cutline_mode="PERCENT",
            cutline_value=90, round_unit_percent=1,
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant, homework=homework, session=self.session,
            enrollment=self.enrollment,
        )
        score = HomeworkScore.objects.create(
            enrollment=self.enrollment, session=self.session, homework=homework,
            attempt_index=1, score=92, max_score=100, passed=True,
        )
        history = self.ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.session,
            source_type="homework", source_id=homework.id, cycle_no=1,
            reason="AUTO_FAILED", resolved_at=timezone.now(),
            resolution_type="HOMEWORK_PASS", resolution_evidence={"score": 92},
        )
        self.cycle_parent_user = User.objects.create_user(
            username="homework-cycle-parent", password="pw1234", tenant=self.tenant,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant, user=self.cycle_parent_user, role="parent",
        )
        Parent = django_apps.get_model("parents", "Parent")
        self.student.parent = Parent.objects.create(
            tenant=self.tenant, user=self.cycle_parent_user,
            name="학부모", phone="01099998888",
        )
        self.student.save(update_fields=["parent", "updated_at"])
        return homework, score, history

    def _assert_homework_cycle_summary(self, *, passed, achievement, teacher_resolved=False, score=92):
        before = list(self.ClinicLink.objects.order_by("id").values())
        for user in (self.user, self.cycle_parent_user):
            with self.subTest(role="student" if user == self.user else "parent"):
                request = self.factory.get(
                    "/api/v1/student/grades/", HTTP_X_STUDENT_ID=str(self.student.id),
                )
                request.tenant = self.tenant
                force_authenticate(request, user=user)
                response = MyGradesSummaryView.as_view()(request)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(len(response.data["homeworks"]), 1)
                row = response.data["homeworks"][0]
                self.assertEqual(row["score"], score)
                self.assertEqual(row["passed"], passed)
                self.assertEqual(row["achievement"], achievement)
                self.assertEqual(row["teacher_resolved"], teacher_resolved)
        self.assertEqual(list(self.ClinicLink.objects.order_by("id").values()), before)

    def test_homework_current_cycle_reopens_after_cutline_increase_and_passes_after_decrease(self):
        from apps.support.homework.view_dependencies import recalc_scores_for_homework_change

        homework, score, history = self._homework_cycle_fixture()
        history_before = self.ClinicLink.objects.filter(pk=history.pk).values().get()
        for cutoff, passed, achievement in ((95, False, "FAIL"), (90, True, "PASS"), (95, False, "FAIL")):
            with self.subTest(cutoff=cutoff, achievement=achievement):
                homework.cutline_value = cutoff
                homework.save(update_fields=["cutline_value", "updated_at"])
                recalc_scores_for_homework_change(homework=homework)
                score.refresh_from_db()
                self.assertEqual(score.score, 92)
                self.assertEqual(score.passed, passed)
                self.assertEqual(score.clinic_required, not passed)
                current = self.ClinicLink.objects.order_by("-cycle_no", "-id").first()
                self.assertEqual(current.resolved_at is not None, passed)
                self._assert_homework_cycle_summary(passed=passed, achievement=achievement)
        self.assertEqual(self.ClinicLink.objects.filter(pk=history.pk).values().get(), history_before)
        self.assertEqual(self.ClinicLink.objects.count(), 3)

    def test_homework_current_cycle_manual_completion_wins_over_later_historical_timestamp(self):
        homework, score, history = self._homework_cycle_fixture()
        score.passed = False
        score.save(update_fields=["passed"])
        self.ClinicLink.objects.filter(pk=history.pk).update(resolved_at=timezone.now() + timedelta(days=1))
        correction = self.AssessmentCorrection.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.session,
            source_type="homework", source_id=homework.id, completed=True,
            completed_at=timezone.now(), updated_by=self.user,
        )
        self.ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.session,
            source_type="homework", source_id=homework.id, cycle_no=2,
            reason="AUTO_FAILED", resolved_at=timezone.now(), resolution_type="MANUAL_OVERRIDE",
            resolution_evidence={"assessment_correction_id": correction.id},
        )
        self._assert_homework_cycle_summary(passed=False, achievement="REMEDIATED", teacher_resolved=True)

    def test_homework_current_cycle_unresolved_does_not_restore_cancelled_unscored_completion(self):
        homework, score, history = self._homework_cycle_fixture()
        score.delete()
        correction = self.AssessmentCorrection.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.session,
            source_type="homework", source_id=homework.id, completed=False,
            updated_by=self.user,
        )
        self.ClinicLink.objects.filter(pk=history.pk).update(
            resolution_type="MANUAL_OVERRIDE",
            resolution_evidence={"assessment_correction_id": correction.id},
        )
        self.ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.session,
            source_type="homework", source_id=homework.id, cycle_no=2, reason="AUTO_FAILED",
        )
        self._assert_homework_cycle_summary(passed=False, achievement="NOT_SUBMITTED", score=None)

    def test_homework_current_cycle_nonpassing_resolution_does_not_restore_historical_pass(self):
        homework, score, _ = self._homework_cycle_fixture()
        score.passed = False
        score.save(update_fields=["passed"])
        current = self.ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.session,
            source_type="homework", source_id=homework.id, cycle_no=2,
            reason="AUTO_FAILED", resolved_at=timezone.now(), resolution_type="WAIVED",
        )
        for reason in ("WAIVED", "SOURCE_REMOVED", "GRADING_RETRACTED"):
            with self.subTest(reason=reason):
                current.resolution_type = reason
                current.save(update_fields=["resolution_type"])
                self._assert_homework_cycle_summary(passed=False, achievement="FAIL")

    def test_homework_current_cycle_retake_completion_preserves_initial_score(self):
        from apps.support.homework_results.score_dependencies import resolve_homework_clinic_pass

        homework, score, _ = self._homework_cycle_fixture()
        score.passed = False
        score.save(update_fields=["passed"])
        current = self.ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.session,
            source_type="homework", source_id=homework.id, cycle_no=2, reason="AUTO_FAILED",
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment, session=self.session, homework=homework,
            attempt_index=2, score=98, max_score=100, passed=True,
        )
        resolve_homework_clinic_pass(
            enrollment_id=self.enrollment.id, session_id=self.session.id,
            homework_id=homework.id, score=98, max_score=100,
        )
        current.refresh_from_db()
        self.assertEqual(current.resolution_type, "HOMEWORK_PASS")
        self.assertIsNotNone(current.resolved_at)
        self._assert_homework_cycle_summary(passed=False, achievement="REMEDIATED")

    def test_homework_current_cycle_ignores_foreign_tenant_and_other_sources(self):
        homework, score, _ = self._homework_cycle_fixture()
        score.passed = False
        score.save(update_fields=["passed"])
        self.ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.session,
            source_type="homework", source_id=homework.id, cycle_no=2, reason="AUTO_FAILED",
        )
        foreign_tenant = Tenant.objects.create(code="foreign-homework-cycle", name="Foreign")
        for overrides in (
            {"tenant": foreign_tenant}, {"source_type": "exam"}, {"source_id": homework.id + 1000},
        ):
            fields = dict(
                tenant=self.tenant, enrollment=self.enrollment, session=self.session,
                source_type="homework", source_id=homework.id, cycle_no=99,
                reason="AUTO_FAILED", resolved_at=timezone.now(), resolution_type="HOMEWORK_PASS",
            )
            fields.update(overrides)
            self.ClinicLink.objects.create(**fields)
        self._assert_homework_cycle_summary(passed=False, achievement="FAIL")

    def test_submitted_ungraded_homework_is_visible_as_waiting_for_review(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="제출 후 검사 대기 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        Submission = django_apps.get_model("submissions", "Submission")
        Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=homework.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.SUBMITTED,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        row = response.data["homeworks"][0]
        self.assertIsNone(row["score"])
        self.assertIsNone(row["passed"])
        self.assertIsNone(row["achievement"])

    def test_failed_homework_submission_waits_for_review_then_requires_retry(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="재제출 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        score = HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            attempt_index=1,
            score=5,
            max_score=20,
            passed=False,
            reviewed_submission_revision="",
        )
        self.assertEqual(self._call().data["homeworks"][0]["submission_state"], "needs_submission")
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=homework.id,
            source=Submission.Source.HOMEWORK_IMAGE,
            status=Submission.Status.SUBMITTED,
        )
        media = SubmissionMedia.objects.create(
            tenant=self.tenant,
            submission=submission,
            client_upload_id=uuid.uuid4(),
            upload_batch_id=uuid.uuid4(),
            fingerprint="a" * 64,
            object_key="tenant/test/homework.jpg",
            original_filename="풀이.jpg",
            media_kind=SubmissionMedia.Kind.IMAGE,
            mime_type="image/jpeg",
            size=100,
            position=0,
            status=SubmissionMedia.Status.FAILED,
        )
        self.assertEqual(self._call().data["homeworks"][0]["submission_state"], "needs_submission")
        media.status = SubmissionMedia.Status.UPLOADED
        media.uploaded_at = timezone.now()
        media.save(update_fields=["status", "uploaded_at"])
        waiting = self._call().data["homeworks"][0]
        self.assertEqual(waiting["achievement"], "FAIL")
        self.assertEqual(waiting["submission_state"], "awaiting_review")
        revision = homework_submission_revisions(
            tenant=self.tenant,
            enrollment_ids=[self.enrollment.id],
            homework_ids=[homework.id],
        )[(self.enrollment.id, homework.id)]
        score.reviewed_submission_revision = revision
        score.save(update_fields=["reviewed_submission_revision"])
        self.assertEqual(self._call().data["homeworks"][0]["submission_state"], "needs_submission")
        media.removed_at = timezone.now()
        media.status = SubmissionMedia.Status.REMOVED
        media.save(update_fields=["removed_at", "status"])
        self.assertEqual(self._call().data["homeworks"][0]["submission_state"], "needs_submission")

    def test_explicit_not_submitted_review_requires_new_evidence(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="미제출 재검토 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=homework.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.DONE,
        )
        revision = homework_submission_revisions(
            tenant=self.tenant,
            enrollment_ids=[self.enrollment.id],
            homework_ids=[homework.id],
        )[(self.enrollment.id, homework.id)]
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            attempt_index=1,
            score=None,
            max_score=None,
            passed=False,
            meta={"status": HomeworkScore.MetaStatus.NOT_SUBMITTED},
            reviewed_submission_revision=revision,
        )
        reviewed = self._call().data["homeworks"][0]
        self.assertEqual(reviewed["achievement"], "NOT_SUBMITTED")
        self.assertEqual(reviewed["submission_state"], "needs_submission")

        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=homework.id,
            source=Submission.Source.HOMEWORK_IMAGE,
            status=Submission.Status.SUBMITTED,
        )
        SubmissionMedia.objects.create(
            tenant=self.tenant,
            submission=submission,
            client_upload_id=uuid.uuid4(),
            upload_batch_id=uuid.uuid4(),
            fingerprint="b" * 64,
            object_key="tenant/test/retry.jpg",
            original_filename="보완.jpg",
            media_kind=SubmissionMedia.Kind.IMAGE,
            mime_type="image/jpeg",
            size=100,
            position=0,
            status=SubmissionMedia.Status.UPLOADED,
        )
        waiting = self._call().data["homeworks"][0]
        self.assertIsNone(waiting["achievement"])
        self.assertEqual(waiting["submission_state"], "awaiting_review")

    def test_submission_revision_rejects_foreign_homework_target(self):
        foreign_tenant = Tenant.objects.create(
            code="foreign-homework-target",
            name="Foreign Homework Target",
            is_active=True,
        )
        foreign_lecture = Lecture.objects.create(
            tenant=foreign_tenant,
            title="Foreign Lecture",
            name="Foreign Lecture",
            subject="MATH",
        )
        foreign_session = Session.objects.create(
            lecture=foreign_lecture,
            order=1,
            title="Foreign Session",
        )
        foreign_homework = Homework.objects.create(
            tenant=foreign_tenant,
            session=foreign_session,
            title="Foreign Homework",
        )
        Submission.objects.create(
            tenant=self.tenant,
            user=self.user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=foreign_homework.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.SUBMITTED,
        )
        self.assertEqual(homework_submission_revisions(
            tenant=self.tenant,
            enrollment_ids=[self.enrollment.id],
            homework_ids=[foreign_homework.id],
        ), {})

    def test_retake_pass_exposes_submission_media_lock_without_changing_initial_grade(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="재응시 통과 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            attempt_index=1,
            score=8,
            max_score=20,
            passed=False,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            attempt_index=2,
            score=18,
            max_score=20,
            passed=True,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["homeworks"][0]
        self.assertFalse(row["passed"])
        self.assertEqual(row["achievement"], "FAIL")
        self.assertEqual(row["retake_count"], 2)
        self.assertTrue(row["submission_media_locked"])
        self.assertEqual(row["submission_state"], "reviewed")

    def test_latest_retake_failure_keeps_media_unlocked_after_initial_pass(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="재응시 보완 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            attempt_index=1,
            score=18,
            max_score=20,
            passed=True,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            attempt_index=2,
            score=8,
            max_score=20,
            passed=False,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["homeworks"][0]
        self.assertTrue(row["passed"])
        self.assertEqual(row["retake_count"], 2)
        self.assertFalse(row["submission_media_locked"])
        self.assertEqual(row["submission_state"], "needs_submission")

    def test_homeworks_are_session_ordered_and_expose_regular_supplement_scope(self):
        supplement_session = Session.objects.create(
            lecture=self.lecture,
            order=2,
            session_type=Session.SessionType.SUPPLEMENT,
            title="주말 보강",
        )
        latest_regular_session = Session.objects.create(
            lecture=self.lecture,
            order=3,
            regular_order=2,
            session_type=Session.SessionType.REGULAR,
            title="2차시",
        )
        for session, title in (
            (self.session, "첫 과제"),
            (supplement_session, "보강 과제"),
            (latest_regular_session, "최근 과제"),
        ):
            homework = Homework.objects.create(
                tenant=self.tenant,
                session=session,
                title=title,
                meta={"default_max_score": 10},
            )
            HomeworkAssignment.objects.create(
                tenant=self.tenant,
                homework=homework,
                session=session,
                enrollment=self.enrollment,
            )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["title"] for row in response.data["homeworks"]],
            ["최근 과제", "보강 과제", "첫 과제"],
        )
        rows = {row["title"]: row for row in response.data["homeworks"]}
        self.assertEqual(rows["최근 과제"]["session_order"], 3)
        self.assertEqual(rows["최근 과제"]["session_regular_order"], 2)
        self.assertEqual(rows["최근 과제"]["session_type"], "REGULAR")
        self.assertEqual(rows["보강 과제"]["session_order"], 2)
        self.assertIsNone(rows["보강 과제"]["session_regular_order"])
        self.assertEqual(rows["보강 과제"]["session_type"], "SUPPLEMENT")

    def test_legacy_template_exam_items_are_included_in_summary_analysis(self):
        template_exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="레거시 시험지",
            exam_type=self.Exam.ExamType.TEMPLATE,
            is_active=True,
            max_score=100,
        )
        template_sheet = self.Sheet.objects.create(
            exam=template_exam,
            name="TEMPLATE",
            total_questions=1,
        )
        question = self.ExamQuestion.objects.create(
            sheet=template_sheet,
            number=7,
            score=100,
        )
        regular_exam = self.Exam.objects.create(
            tenant=self.tenant,
            template_exam=template_exam,
            title="레거시 운영 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        regular_exam.sessions.add(self.session)
        result = self.Result.objects.create(
            target_type="exam",
            target_id=regular_exam.id,
            enrollment=self.enrollment,
            total_score=0,
            max_score=100,
        )
        self.ResultItem.objects.create(
            result=result,
            question=question,
            answer="2",
            is_correct=False,
            score=0,
            max_score=100,
            source="manual",
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["exams"][0]
        self.assertEqual(row["total_questions"], 1)
        self.assertEqual(row["wrong_count"], 1)
        self.assertEqual(row["wrong_question_numbers"], [7])

    def test_cross_tenant_template_items_are_excluded_from_summary_analysis(self):
        other_tenant = Tenant.objects.create(
            code="student-summary-template-foreign",
            name="Student Summary Template Foreign",
            is_active=True,
        )
        foreign_template = self.Exam.objects.create(
            tenant=other_tenant,
            title="외부 시험지",
            exam_type=self.Exam.ExamType.TEMPLATE,
            is_active=True,
            max_score=100,
        )
        foreign_sheet = self.Sheet.objects.create(
            exam=foreign_template,
            name="FOREIGN_TEMPLATE",
            total_questions=1,
        )
        foreign_question = self.ExamQuestion.objects.create(
            sheet=foreign_sheet,
            number=91,
            score=100,
        )
        regular_exam = self.Exam.objects.create(
            tenant=self.tenant,
            template_exam=foreign_template,
            title="손상된 운영 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        regular_exam.sessions.add(self.session)
        result = self.Result.objects.create(
            target_type="exam",
            target_id=regular_exam.id,
            enrollment=self.enrollment,
            total_score=100,
            max_score=100,
        )
        self.ResultItem.objects.create(
            result=result,
            question=foreign_question,
            answer="1",
            is_correct=True,
            score=100,
            max_score=100,
            source="manual",
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        row = response.data["exams"][0]
        self.assertEqual(row["total_questions"], 0)
        self.assertEqual(row["correct_count"], 0)
        self.assertEqual(row["wrong_question_numbers"], [])

    def test_removed_homework_assignment_is_hidden_from_student_summary(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="제거된 과제",
            meta={"removed_from_session_at": "2026-05-24T00:00:00+09:00"},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["homeworks"], [])

    def test_removed_homework_score_is_hidden_from_student_summary(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="제거된 채점 과제",
            meta={
                "default_max_score": 30,
                "removed_from_session_at": "2026-05-24T00:00:00+09:00",
            },
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=27,
            max_score=30,
            passed=False,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["homeworks"], [])

    def test_removed_and_recreated_same_title_homework_shows_only_replacement(self):
        removed = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="동일 제목 재생성 과제",
            meta={
                "default_max_score": 30,
                "removed_from_session_at": "2026-08-08T22:52:04+09:00",
            },
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=removed,
            score=27,
            max_score=30,
            passed=False,
        )
        replacement = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title=removed.title,
            meta={"default_max_score": 30},
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=replacement,
            score=30,
            max_score=30,
            passed=True,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["homeworks"]), 1)
        row = response.data["homeworks"][0]
        self.assertEqual(row["homework_id"], replacement.id)
        self.assertEqual(row["title"], removed.title)
        self.assertEqual(row["score"], 30.0)

    def test_cross_lecture_homework_relations_are_fail_closed(self):
        other_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="다른 반",
            name="다른 반",
            subject="MATH",
        )
        other_session = Session.objects.create(
            lecture=other_lecture,
            order=1,
            title="다른 반 1회",
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=other_session,
            title="다른 반 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=other_session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=other_session,
            homework=homework,
            score=20,
            max_score=20,
            passed=True,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["homeworks"], [])

    def test_nonfinite_homework_values_do_not_break_json_response(self):
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="손상 수치 과제",
            meta={},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=float("inf"),
            max_score=float("inf"),
            passed=False,
        )

        response = self._call()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["homeworks"][0]["achievement"], "NOT_SUBMITTED")
        self.assertIsNone(response.data["homeworks"][0]["max_score"])
        response.render()
        self.assertNotIn(b"Infinity", response.content)

    def test_inactive_enrollment_exam_result_is_hidden_from_student_detail(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="비활성 수강 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=100,
            max_score=100,
        )
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status", "updated_at"])

        response = self._call_exam_result(exam.id)

        self.assertEqual(response.status_code, 404)

    def test_student_detail_filters_corrupt_foreign_tenant_result_item(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="문항 격리 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        result = self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=80,
            max_score=100,
        )
        other_tenant = Tenant.objects.create(
            code="student-detail-item-foreign",
            name="Student Detail Item Foreign",
            is_active=True,
        )
        foreign_exam = self.Exam.objects.create(
            tenant=other_tenant,
            title="외부 시험",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        foreign_sheet = self.Sheet.objects.create(
            exam=foreign_exam,
            name="FOREIGN",
            total_questions=1,
        )
        foreign_question = self.ExamQuestion.objects.create(
            sheet=foreign_sheet,
            number=99,
            score=100,
        )
        self.ResultItem.objects.create(
            result=result,
            question=foreign_question,
            answer="1",
            is_correct=True,
            score=100,
            max_score=100,
            source="manual",
        )

        response = self._call_exam_result(exam.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(response.data["analysis"]["total_questions"], 0)

    def test_inactive_enrollment_scores_are_hidden_from_student_summary(self):
        exam = self.Exam.objects.create(
            tenant=self.tenant,
            title="비활성 수강 성적",
            exam_type=self.Exam.ExamType.REGULAR,
            is_active=True,
            max_score=100,
        )
        exam.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            enrollment=self.enrollment,
            total_score=80,
            max_score=100,
        )
        homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="비활성 수강 과제",
            meta={"default_max_score": 20},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=homework,
            session=self.session,
            enrollment=self.enrollment,
        )
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=homework,
            score=18,
            max_score=20,
            passed=True,
        )
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status", "updated_at"])

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["exams"], [])
        self.assertEqual(response.data["homeworks"], [])
        self.assertEqual(response.data["exam_trend"], [])
        self.assertEqual(response.data["exam_summary"]["scored_count"], 0)
        self.assertEqual(response.data["lecture_options"], [])


    def test_shared_exam_totals_keep_each_enrollment_while_history_stays_deduplicated(self):
        SessionEnrollment = django_apps.get_model("enrollment", "SessionEnrollment")
        other_lecture = Lecture.objects.create(tenant=self.tenant, name="Shared second", title="Shared second", subject="MATH")
        other_session = Session.objects.create(lecture=other_lecture, order=1, title="Other session")
        other_enrollment = Enrollment.objects.create(tenant=self.tenant, student=self.student, lecture=other_lecture, status="ACTIVE")
        for session, enrollment in ((self.session, self.enrollment), (other_session, other_enrollment)):
            SessionEnrollment.objects.create(tenant=self.tenant, session=session, enrollment=enrollment)
        shared = self.Exam.objects.create(tenant=self.tenant, title="Shared real exam", exam_type="regular",
                                          max_score=100, pass_score=60, student_results_published=True)
        shared.sessions.add(self.session, other_session)
        for enrollment, score in ((self.enrollment, 80), (other_enrollment, 40)):
            self.ExamEnrollment.objects.create(exam=shared, enrollment=enrollment)
            self.Result.objects.create(target_type="exam", target_id=shared.id, enrollment=enrollment,
                                       total_score=score, max_score=100)
        response = self._call()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len([row for row in response.data["exams"] if row["exam_id"] == shared.id]), 1)
        totals = {row["session_id"]: row["exam_total"] for row in response.data["session_totals"]}
        self.assertEqual(totals[self.session.id], {"score": 80.0, "max_score": 100.0, "selected_count": 1, "scored_count": 1, "complete": True})
        self.assertEqual(totals[other_session.id]["score"], 40.0)
        self.assertTrue(totals[other_session.id]["complete"])
        hidden = self.Exam.objects.create(tenant=self.tenant, title="Unpublished result", exam_type="regular",
                                          max_score=100, pass_score=60, student_results_published=False)
        hidden.sessions.add(self.session)
        self.ExamEnrollment.objects.create(exam=hidden, enrollment=self.enrollment)
        self.Result.objects.create(target_type="exam", target_id=hidden.id, enrollment=self.enrollment,
                                   total_score=99, max_score=100)
        protected = self._call().data
        self.assertNotIn(hidden.id, [row["exam_id"] for row in protected["exams"]])
        self.assertEqual(next(row for row in protected["session_totals"] if row["session_id"] == self.session.id)["exam_total"]["score"], 80.0)

    def test_absent_exam_activity_matches_admin_pending_denominator(self):
        from django.urls import resolve
        SessionEnrollment = django_apps.get_model("enrollment", "SessionEnrollment")
        Attendance = django_apps.get_model("attendance", "Attendance")
        SessionEnrollment.objects.create(tenant=self.tenant, session=self.session, enrollment=self.enrollment)
        Attendance.objects.create(tenant=self.tenant, session=self.session, enrollment=self.enrollment, status="ABSENT")
        graded = self.Exam.objects.create(tenant=self.tenant, title="Known grade", exam_type="regular", max_score=100, pass_score=60, student_results_published=True)
        pending = self.Exam.objects.create(tenant=self.tenant, title="No grade yet", exam_type="regular", max_score=100, pass_score=60, student_results_published=True)
        for exam in (graded, pending):
            exam.sessions.add(self.session)
            self.ExamEnrollment.objects.create(exam=exam, enrollment=self.enrollment)
        self.Result.objects.create(target_type="exam", target_id=graded.id, enrollment=self.enrollment, total_score=80, max_score=100)
        staff = User.objects.create_user(username="absent-total-reader", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=staff, role="admin")
        def assert_matches(count):
            parent_total = self._call().data["session_totals"][0]["exam_total"]
            request = self.factory.get("/")
            request.tenant = self.tenant
            force_authenticate(request, user=staff)
            response = resolve(f"/api/v1/results/admin/sessions/{self.session.id}/scores/").func(request, session_id=self.session.id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(parent_total, response.data["rows"][0]["exam_total"])
            self.assertEqual(parent_total["selected_count"], count)
            self.assertEqual(parent_total["complete"], count == 1)
        assert_matches(1)
        submission = Submission.objects.create(tenant=self.tenant, user=self.user, enrollment=self.enrollment,
            target_type="exam", target_id=pending.id, source="online", status="submitted")
        assert_matches(2)
        submission.delete()
        assert_matches(1)
        attempt = self.ExamAttempt.objects.create(exam=pending, enrollment=self.enrollment, attempt_index=1, status="pending")
        assert_matches(2)
        attempt.delete()
        assert_matches(1)
        correction = self.AssessmentCorrection.objects.create(tenant=self.tenant, enrollment=self.enrollment,
            session=self.session, source_type="exam", source_id=pending.id, completed=False, note="Review pending")
        assert_matches(2)
        correction.delete()
        assert_matches(1)

    def test_unscored_exam_only_session_uses_authorized_roster_and_targets(self):
        SessionEnrollment = django_apps.get_model("enrollment", "SessionEnrollment")
        Attendance = django_apps.get_model("attendance", "Attendance")
        SessionEnrollment.objects.create(tenant=self.tenant, session=self.session, enrollment=self.enrollment)
        exam = self.Exam.objects.create(tenant=self.tenant, title="Awaiting first score", exam_type="regular",
            max_score=100, pass_score=60, student_results_published=True)
        exam.sessions.add(self.session)
        expected = {"score": None, "max_score": 100.0, "selected_count": 1, "scored_count": 0, "complete": False}
        data = self._call().data
        self.assertEqual(data["exams"], [])
        self.assertEqual(data["session_totals"][0]["exam_total"], expected)
        # Explicit assignment to someone else removes the default roster target.
        other_user = User.objects.create_user(username="unscored-other-child", tenant=self.tenant)
        other_student = Student.objects.create(tenant=self.tenant, user=other_user, name="Other child")
        other = Enrollment.objects.create(tenant=self.tenant, student=other_student, lecture=self.lecture, status="ACTIVE")
        target = self.ExamEnrollment.objects.create(exam=exam, enrollment=other)
        self.assertEqual(self._call().data["session_totals"], [])
        target.delete()
        Attendance.objects.create(tenant=self.tenant, session=self.session, enrollment=self.enrollment, status="ABSENT")
        self.assertEqual(self._call().data["session_totals"][0]["exam_total"]["selected_count"], 0)
        Submission.objects.create(tenant=self.tenant, user=self.user, enrollment=self.enrollment,
            target_type="exam", target_id=exam.id, source="online", status="submitted")
        self.assertEqual(self._call().data["session_totals"][0]["exam_total"], expected)
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status"])
        self.assertEqual(self._call().data.get("session_totals", []), [])

    def test_shared_exam_second_enrollment_absence_is_not_lost_to_display_dedup(self):
        SessionEnrollment = django_apps.get_model("enrollment", "SessionEnrollment")
        Attendance = django_apps.get_model("attendance", "Attendance")
        lecture = Lecture.objects.create(tenant=self.tenant, name="Second absent", title="Second absent", subject="MATH")
        session = Session.objects.create(lecture=lecture, order=1, title="Second absent session")
        enrollment = Enrollment.objects.create(tenant=self.tenant, student=self.student, lecture=lecture, status="ACTIVE")
        shared = self.Exam.objects.create(tenant=self.tenant, title="Shared scored", exam_type="regular",
            max_score=100, pass_score=60, student_results_published=True)
        shared.sessions.add(self.session, session)
        for current_session, current_enrollment in ((self.session, self.enrollment), (session, enrollment)):
            SessionEnrollment.objects.create(tenant=self.tenant, session=current_session, enrollment=current_enrollment)
            self.ExamEnrollment.objects.create(exam=shared, enrollment=current_enrollment)
            self.Result.objects.create(target_type="exam", target_id=shared.id, enrollment=current_enrollment, total_score=40, max_score=100)
        pending = self.Exam.objects.create(tenant=self.tenant, title="Absent no activity", exam_type="regular",
            max_score=100, pass_score=60, student_results_published=True)
        pending.sessions.add(session)
        Attendance.objects.create(tenant=self.tenant, session=session, enrollment=enrollment, status="ABSENT")
        data = self._call().data
        self.assertEqual(len(data["exams"]), 1)
        total = next(row["exam_total"] for row in data["session_totals"] if row["session_id"] == session.id)
        self.assertEqual(total, {"score": 40.0, "max_score": 100.0, "selected_count": 1, "scored_count": 1, "complete": True})
